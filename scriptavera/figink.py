# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The ink of a drawing, separated from the paper.

For a PDF made by a note-taking app the ink is known exactly: each pen stroke is read from the file, and it is
drawn again in the region at a resolution where the pen is about six pixels wide. For a scan or a photo, the paper
is taken away step by step:
  1. the colour of the paper is estimated everywhere (it varies across a photo) and divided out;
  2. ruled or squared paper is found as rows and columns whose typical (median) pixel is a little darker than
     paper: a line of the paper runs across the whole region, while no line of handwriting fills half of a row.
     There, only pixels as faint as the paper's lines are removed, so ink that crosses them is kept;
  3. the ink is what is clearly darker than paper, together with the fainter pixels connected to it (so that a
     thin pencil line is kept whole, and specks are not).
Either way the result is the same: a mask of the ink, the colour of each piece of ink, the typical width of the pen
and the typical height of the handwriting on the page, all in pixels of the region."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageFilter

from . import figimg as fi
from .figsource import PageSource, Stroke

PEN_PX = 6.0                   # the pen's width in pixels at which drawings are measured
MAX_SIDE = 3600                # longest side of a region, in pixels


@dataclass
class InkStroke:
    """One piece of ink: a pen stroke of a PDF, or (for a scan) one connected part of the ink."""
    id: int
    mask: np.ndarray           # boolean mask over its bounding box
    x0: int
    y0: int
    color: tuple               # (r, g, b)
    width: float               # pen width in pixels
    order: int                 # drawing order (PDF) or reading order (scan)
    vector: bool
    line: np.ndarray | None = None   # the centre line in region pixels, when the PDF stores it

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        h, w = self.mask.shape
        return (self.x0, self.y0, self.x0 + w, self.y0 + h)


@dataclass
class Ink:
    box: tuple                 # the region, page units (x0, y0, x1, y1)
    scale: float               # pixels per page unit
    rgb: np.ndarray            # the region without highlighter and paper template, (h, w, 3) uint8
    mask: np.ndarray           # the ink, (h, w) bool
    strokes: list = field(default_factory=list)
    pen: float = PEN_PX        # typical pen width, pixels
    text_h: float = 30.0       # typical height of a handwritten letter, pixels
    vector: bool = False
    wb: np.ndarray | None = None   # a scan with the paper's colour divided out (white paper): the colours of the inks

    @property
    def shape(self) -> tuple[int, int]:
        return self.mask.shape

    def to_page(self, x: float, y: float) -> tuple[float, float]:
        return (self.box[0] + x / self.scale, self.box[1] + y / self.scale)

    def from_page(self, X: float, Y: float) -> tuple[float, float]:
        return ((X - self.box[0]) * self.scale, (Y - self.box[1]) * self.scale)


# ===================================================================== the scale

def pen_width(src: PageSource, box) -> float | None:
    """The typical pen width of the ink in `box`, in page units (vector strokes only)."""
    ws = [s.width for s in src.ink_strokes(box)]
    return float(np.median(ws)) if ws else None


def measure_scale(src: PageSource, box) -> float:
    """Pixels per page unit at which the drawing in `box` is measured: the pen about PEN_PX pixels wide for vector
    strokes; the source's own resolution for a scan (at least 1.5 pixels per point)."""
    x0, y0, x1, y1 = box
    side = max(x1 - x0, y1 - y0, 1e-6)
    if src.vector_ink(box):
        w = pen_width(src, box) or 1.0
        s = PEN_PX / max(w, 1e-3)
    else:
        s = src.native_scale(box) or 2.0
        if src.kind == "pdf":
            s = max(s, 1.5)
    return float(min(s, MAX_SIDE / side))


# ===================================================================== reading the region

MAX_ENLARGE = 3.0             # a scan is enlarged at most this much for measuring


def read(src: PageSource, box, scale: float | None = None, text_h_page: float | None = None,
         exclude: list | None = None) -> Ink:
    """The ink in `box` (page units). Ink lying in one of the boxes `exclude` (page units: writing around the
    drawing that the region could not avoid) is left out, and shown faintly in the picture.

    A scan whose pen is only a few pixels wide is read again enlarged (smoothly interpolated), so that the pen is
    about PEN_PX pixels wide, as for vector strokes. Enlarging adds no detail, but thinning a line to its centre
    needs a few pixels across the pen: at two or three pixels the short strokes of an arrowhead, a tick or a dash
    merge into blots. A scale given by the caller is kept as it is."""
    box = src.clip(box)
    s = scale or measure_scale(src, box)
    img = src.render(box, s, clean=True)
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    if src.vector_ink(box):
        ink = _vector_ink(src, box, s, rgb)
    else:
        ink = _raster_ink(box, s, rgb)
        side = max(box[2] - box[0], box[3] - box[1], 1e-6)
        f = min(MAX_ENLARGE, PEN_PX / max(ink.pen, 1e-3), MAX_SIDE / (side * s))
        if scale is None and f >= 1.25:
            s = s * f
            rgb = np.asarray(src.render(box, s, clean=True).convert("RGB"), dtype=np.uint8)
            ink = _raster_ink(box, s, rgb)
    if exclude:
        _exclude(ink, exclude)
    if text_h_page:
        ink.text_h = float(text_h_page * s)
    else:
        ink.text_h = _text_height_px(ink)
    return ink


def _darkness(rgb: np.ndarray, bg: np.ndarray | None = None) -> np.ndarray:
    """How much darker than the paper each pixel is, in its most absorbed colour channel: 0 (paper) .. 1 (black).
    The darkest channel is used so that coloured ink (a yellow pen has little blue) counts as ink."""
    f = rgb.astype(np.float32)
    if bg is None:
        bg = np.full(3, 255.0, np.float32)
    d = 1.0 - f / np.maximum(bg, 1.0)
    return np.clip(d.max(axis=2), 0.0, 1.0)


def _vector_ink(src: PageSource, box, s: float, rgb: np.ndarray) -> Ink:
    h, w = rgb.shape[:2]
    d = _darkness(rgb)
    rendered = d > 0.18
    x0, y0 = box[0], box[1]
    strokes: list[InkStroke] = []
    union = np.zeros((h, w), bool)
    vis = fi.dilate(rendered, 1)
    for st in src.ink_strokes(box):
        bx0, by0, bx1, by1 = st.bbox
        pad = st.width * s + 2
        ix0 = max(0, int(np.floor((bx0 - x0) * s - pad)))
        iy0 = max(0, int(np.floor((by0 - y0) * s - pad)))
        ix1 = min(w, int(np.ceil((bx1 - x0) * s + pad)))
        iy1 = min(h, int(np.ceil((by1 - y0) * s + pad)))
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        if st.line is not None:
            pts = (st.line - [x0, y0]) * s - [ix0, iy0]
            m = fi.draw_polyline((iy1 - iy0, ix1 - ix0), pts, st.width * s)
            line = (st.line - [x0, y0]) * s
        else:
            rings = [(r - [x0, y0]) * s - [ix0, iy0] for r in st.rings]
            m = fi.fill(rings, (iy1 - iy0, ix1 - ix0), st.evenodd)
            line = None
        m &= vis[iy0:iy1, ix0:ix1]                     # what an eraser or a later white stroke covered is gone
        if m.sum() < 2:
            continue
        rows, cols = np.nonzero(m.any(axis=1))[0], np.nonzero(m.any(axis=0))[0]
        m = m[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        ix0, iy0 = ix0 + int(cols[0]), iy0 + int(rows[0])
        strokes.append(InkStroke(id=len(strokes), mask=m, x0=ix0, y0=iy0, color=tuple(int(c) for c in st.color),
                                 width=st.width * s, order=st.order, vector=True, line=line))
        union[iy0:iy0 + m.shape[0], ix0:ix0 + m.shape[1]] |= m
    # ink that no vector stroke accounts for (a pencil stroke stored as an image, typed text) is read as a scan
    rest = rendered & ~fi.dilate(union, 2)
    if rest.sum() > 0:
        extra = _components(rest, rgb, start=len(strokes), min_area=max(6, int((PEN_PX / 2) ** 2)))
        strokes.extend(extra)
        for e in extra:
            union[e.y0:e.y0 + e.mask.shape[0], e.x0:e.x0 + e.mask.shape[1]] |= e.mask
    pen = float(np.median([x.width for x in strokes if x.vector])) if any(x.vector for x in strokes) else PEN_PX
    return Ink(box=box, scale=s, rgb=rgb, mask=union, strokes=strokes, pen=pen, vector=True)


def _background(rgb: np.ndarray) -> np.ndarray:
    """The colour of the paper at every pixel: a high percentile of each neighbourhood, smoothed."""
    h, w = rgb.shape[:2]
    f = max(2, int(round(min(h, w) / 120)))
    img = Image.fromarray(rgb)
    small = img.reduce(f) if f > 1 else img
    k = 7
    small = small.filter(ImageFilter.RankFilter(k, int(k * k * 0.8)))
    small = small.filter(ImageFilter.GaussianBlur(2))
    bg = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)
    # never darker than the region's own paper level, and never brighter than white
    lvl = np.percentile(rgb.reshape(-1, 3), 75, axis=0).astype(np.float32)
    return np.clip(np.maximum(bg, lvl * 0.85), 1.0, 255.0)


def paper_lines(d: np.ndarray, axis: int) -> np.ndarray:
    """Rows (axis=1) or columns (axis=0) on which a printed line of the paper runs: their median pixel is darker than
    paper. Returns a boolean vector."""
    med = np.median(d, axis=axis)
    base = np.median(med)
    noise = np.median(np.abs(med - base)) + 1e-3
    lines = med > base + max(0.03, 6 * noise)
    # a line of ink running across most of the region is far darker than a paper line: not taken for paper
    return lines & (med < 0.6)


def white_balanced(rgb: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """The picture with the paper's colour divided out: paper becomes white and every ink keeps its own colour,
    whatever the tint of the paper or of the light."""
    return np.clip(rgb.astype(np.float32) / np.maximum(bg, 1.0) * 255.0, 0, 255).astype(np.uint8)


def _raster_ink(box, s: float, rgb: np.ndarray) -> Ink:
    h, w = rgb.shape[:2]
    bg = _background(rgb)
    wb = white_balanced(rgb, bg)
    d = _darkness(rgb, bg)
    # 2. the paper's own lines
    grid = np.zeros((h, w), bool)
    rows = paper_lines(d, 1)
    cols = paper_lines(d, 0)
    if rows.any() or cols.any():
        rr = fi.dilate(np.broadcast_to(rows[:, None], (h, w)), 1)
        cc = fi.dilate(np.broadcast_to(cols[None, :], (h, w)), 1)
        lvl = []
        if rows.any():
            lvl.append(np.median(d[rows]))
        if cols.any():
            lvl.append(np.median(d[:, cols]))
        ceiling = min(0.55, max(0.08, 2.4 * float(max(lvl))))
        grid = (rr | cc) & (d <= ceiling)
        d = np.where(grid, 0.0, d)
    # 3. ink: clearly darker than paper, with the fainter pixels attached to it
    vals = d[d > 0.04]
    thr = otsu_ink(vals)
    strong = d > thr
    weak = d > thr * 0.55
    mask = fi.hysteresis(strong, weak)
    if grid.any():                                       # ink crossing a paper line keeps its pixels there
        mask |= fi.dilate(mask, 1) & grid & (d > 0)
    # pinholes in a faint line (a pixel or two of it lighter than the threshold) would split it in two around them
    mask = fi.fill_holes(mask, max(4.0, 0.8 * _pen_from_mask(mask) ** 2))
    strokes = _components(mask, wb, start=0, min_area=max(4, int(0.25 * PEN_PX ** 2)))
    keep = np.zeros_like(mask)
    for c in strokes:
        keep[c.y0:c.y0 + c.mask.shape[0], c.x0:c.x0 + c.mask.shape[1]] |= c.mask
    pen = _pen_from_mask(keep)
    for c in strokes:
        c.width = pen
    return Ink(box=box, scale=s, rgb=rgb, mask=keep, strokes=strokes, pen=pen, vector=False, wb=wb)


def _exclude(ink: Ink, boxes: list) -> None:
    s = ink.scale
    X0, Y0 = ink.box[0], ink.box[1]
    px = [((b[0] - X0) * s, (b[1] - Y0) * s, (b[2] - X0) * s, (b[3] - Y0) * s) for b in boxes]
    keep = []
    rgb = ink.rgb.copy()
    for st in ink.strokes:
        x0, y0, x1, y1 = st.bbox
        a = max(1, (x1 - x0) * (y1 - y0))
        out = False
        for b in px:
            ix = max(0.0, min(x1, b[2]) - max(x0, b[0]))
            iy = max(0.0, min(y1, b[3]) - max(y0, b[1]))
            if ix * iy >= 0.7 * a:
                out = True
                break
        if out:
            hh, ww = st.mask.shape
            region = rgb[st.y0:st.y0 + hh, st.x0:st.x0 + ww]
            region[st.mask] = (255 - (255 - region[st.mask].astype(np.int32)) * 0.22).astype(np.uint8)
            ink.mask[st.y0:st.y0 + hh, st.x0:st.x0 + ww] &= ~st.mask
        else:
            keep.append(st)
    for i, st in enumerate(keep):
        st.id = i
    ink.strokes = keep
    ink.rgb = rgb


def otsu_ink(vals: np.ndarray) -> float:
    if vals.size < 20:
        return 0.25
    t = fi.otsu(vals)
    return float(min(0.45, max(0.12, t)))


def ink_color(px: np.ndarray) -> tuple:
    """The colour of a piece of ink from its pixels (white-balanced): the median of its darkest pixels, where the
    pen covered the paper fully. The edge of a line is a mixture with the paper, lighter and greyer."""
    px = np.asarray(px, np.float32).reshape(-1, 3)
    if not len(px):
        return (0, 0, 0)
    dark = px.min(axis=1)
    core = px[dark <= np.percentile(dark, 25)] if len(px) >= 8 else px
    return tuple(int(c) for c in np.median(core, axis=0))


def _components(mask: np.ndarray, rgb: np.ndarray, start: int, min_area: int) -> list[InkStroke]:
    lab, n = fi.label(mask)
    bx = fi.boxes(lab, n)
    out = []
    order = np.lexsort((bx[:, 0], bx[:, 1] // 20)) if n else []
    for k in order:
        x0, y0, x1, y1, area = (int(v) for v in bx[k])
        if area < min_area:
            continue
        m = lab[y0:y1, x0:x1] == k + 1
        col = ink_color(rgb[y0:y1, x0:x1][m])
        out.append(InkStroke(id=start + len(out), mask=m, x0=x0, y0=y0, color=col,
                             width=PEN_PX, order=start + len(out), vector=False))
    return out


def widths_along(mask: np.ndarray, cap: float = 40.0) -> np.ndarray:
    """For every pixel, the width of the ink there if the pixel is on a centre line: twice its distance to the
    paper, less the pixel itself."""
    d = fi.chamfer(~np.pad(mask, 1), cap=int(cap) + 2)[1:-1, 1:-1]
    return np.maximum(1.0, 2.0 * d - 1.0)


def _pen_from_mask(mask: np.ndarray) -> float:
    """Typical pen width: the area of the ink divided by the length of its centre lines, leaving out the places
    where the ink is far wider than along most of its lines (a filled area, a blot), which would make the pen
    seem heavier than it is."""
    if mask.sum() < 20:
        return PEN_PX
    sk = fi.thin(mask)
    if not sk.any():
        return PEN_PX
    d = fi.chamfer(~np.pad(mask, 1), cap=60)[1:-1, 1:-1]
    md = float(np.median(d[sk]))
    thick = d >= max(2.0, 2.0 * md)
    if thick.any():
        thick = fi.dilate(thick, int(round(2 * md)) + 1)
    n = int((sk & ~thick).sum())
    if n < 10:
        return float(max(1.0, mask.sum() / sk.sum()))
    return float(max(1.0, (mask & ~thick).sum() / n))


def _text_height_px(ink: Ink) -> float:
    """Typical height of a handwritten letter in the region: the median height of small pieces of ink."""
    hs = []
    for st in ink.strokes:
        hh, ww = st.mask.shape
        if 1.5 * ink.pen <= hh <= 40 * ink.pen and ww <= 40 * ink.pen:
            hs.append(hh)
    if len(hs) < 5:
        return 6.0 * ink.pen
    return float(np.clip(np.percentile(hs, 60), 3 * ink.pen, 25 * ink.pen))


def text_height_page(src: PageSource) -> float:
    """Typical height of a handwritten letter on the whole page, in page units, read from the page at a modest
    resolution (letters dominate a page of notes, so the median piece of ink is about a letter)."""
    box = (0.0, 0.0, src.W, src.H)
    if src.is_pdf and src.ink_strokes():
        hs = [s.bbox[3] - s.bbox[1] for s in src.ink_strokes()]
        ws = [s.width for s in src.ink_strokes()]
        pen = float(np.median(ws))
        hs = [x for x in hs if 1.5 * pen <= x <= 0.08 * src.H]
        if len(hs) >= 10:
            return float(np.percentile(hs, 60))
    s = min(2.0, 1800.0 / max(src.W, src.H)) if src.kind != "image" else 1800.0 / max(src.W, src.H)
    ink = read(src, box, scale=s, text_h_page=1.0)
    hs = [st.mask.shape[0] for st in ink.strokes if 1.5 * ink.pen <= st.mask.shape[0] <= 0.08 * ink.mask.shape[0]]
    if len(hs) < 10:
        return 0.025 * src.H
    return float(np.percentile(hs, 60)) / s


def colors_of(ink: Ink) -> list[tuple]:
    """The distinct ink colours of the region, darkest first, as (r, g, b)."""
    cols = [st.color for st in ink.strokes]
    out: list[list] = []
    for c in cols:
        for grp in out:
            if _color_dist(grp[0], c) < 60:
                grp.append(c)
                break
        else:
            out.append([c])
    reps = [tuple(int(v) for v in np.median(np.array(g), axis=0)) for g in sorted(out, key=len, reverse=True)]
    return sorted(reps, key=lambda c: sum(c))


def _color_dist(a, b) -> float:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    return float(np.sqrt(((a - b) ** 2 * [0.3, 0.59, 0.11]).sum() * 3))
