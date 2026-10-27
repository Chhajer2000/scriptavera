# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A page of the notes as the original file has it, read for its drawings.

The page images the models read are made once, at 1800 pixels on the long side. A drawing is read again from the
original file, at the resolution it needs:
  - a PDF page is rendered again, only the region of the drawing, at a high resolution. Note-taking apps (GoodNotes,
    Notability, OneNote, Samsung Notes, Xournal++ ...) store every pen stroke in the PDF as a vector path, so the
    strokes themselves are read from the file: their exact outline, colour and width, and the order they were drawn
    in. Highlighter strokes (translucent), the paper's own lines (ruled or squared paper drawn as a template) and a
    page-sized background are not ink and are left out;
  - a scan or a photo (an image file, or a PDF page that is only an image) is cut from the original image at its own
    resolution.
Everything here is measured in page units: points (1/72 inch) for a PDF page, pixels of the original for an image,
with the origin at the top-left corner and y pointing down, as on the page image."""

from __future__ import annotations

import ctypes
import math
import io
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

_PDFIUM = threading.RLock()        # pdfium is not thread-safe: one call at a time


@dataclass
class Stroke:
    """One pen stroke read from a PDF, in page units."""
    rings: list            # closed outlines of the inked area, each an (n, 2) array (filled strokes)
    line: np.ndarray | None  # the centre line, for a stroke stored as a stroked path, with `width`
    width: float           # the pen's width (estimated from the outline for a filled stroke)
    color: tuple           # (r, g, b), 0..255
    alpha: float
    evenodd: bool
    bbox: tuple            # (x0, y0, x1, y1)
    order: int             # drawing order on the page
    role: str = "ink"      # ink | highlight | eraser | template
    obj: int = -1          # which object of the page holds it (its place in _walk's order)


@dataclass
class PageSource:
    kind: str                      # "pdf" (a PDF page) | "image" (an image file) | "pageimage" (the 1800-pixel copy)
    W: float
    H: float
    data: bytes = b""
    index: int = 0
    path: Path | None = None
    _strokes: list | None = field(default=None, repr=False)
    _images: list | None = field(default=None, repr=False)
    _img: Image.Image | None = field(default=None, repr=False)

    # ------------------------------------------------------------ coordinates

    def from_norm(self, box) -> tuple[float, float, float, float]:
        """A box given as [ymin, xmin, ymax, xmax] from 0 to 1000 on the page image -> (x0, y0, x1, y1) in page units."""
        y0, x0, y1, x1 = (float(v) for v in box)
        return (x0 / 1000 * self.W, y0 / 1000 * self.H, x1 / 1000 * self.W, y1 / 1000 * self.H)

    def to_norm(self, box) -> list[int]:
        x0, y0, x1, y1 = box
        return [round(y0 / self.H * 1000), round(x0 / self.W * 1000), round(y1 / self.H * 1000), round(x1 / self.W * 1000)]

    def clip(self, box) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = box
        return (max(0.0, min(x0, x1)), max(0.0, min(y0, y1)), min(self.W, max(x0, x1)), min(self.H, max(y0, y1)))

    # --------------------------------------------------------------- the ink

    @property
    def is_pdf(self) -> bool:
        return self.kind == "pdf"

    def strokes(self) -> list[Stroke]:
        """Every vector path of a PDF page that is ink, in drawing order (empty for an image)."""
        if self._strokes is None:
            self._strokes = _read_strokes(self) if self.is_pdf else []
        return self._strokes

    def ink_strokes(self, box=None) -> list[Stroke]:
        out = [s for s in self.strokes() if s.role == "ink"]
        if box is not None:
            x0, y0, x1, y1 = box
            out = [s for s in out if s.bbox[2] >= x0 and s.bbox[0] <= x1 and s.bbox[3] >= y0 and s.bbox[1] <= y1]
        return out

    def images(self) -> list[dict]:
        """Raster images placed on a PDF page: [{bbox, scale (pixels per page unit)}]."""
        if self._images is None:
            self._images = _read_images(self) if self.is_pdf else []
        return self._images

    def native_scale(self, box=None) -> float | None:
        """Pixels per page unit at which the source holds all its detail in `box`: the resolution of a scan, None for
        a region drawn only with vector strokes (which can be rendered at any resolution)."""
        if self.kind in ("image", "pageimage"):
            return 1.0
        sc = []
        for im in self.images():
            if box is None or _overlap(im["bbox"], box) > 0.2 * _area(box):
                sc.append(im["scale"])
        return max(sc) if sc else None

    def vector_ink(self, box) -> bool:
        """Whether the ink in `box` is (mostly) pen strokes stored as vector paths."""
        if not self.is_pdf:
            return False
        ss = self.ink_strokes(box)
        if not ss:
            return False
        imgs = [im for im in self.images() if _overlap(im["bbox"], box) > 0.5 * _area(box)]
        if not imgs:
            return True
        return len(ss) >= 3                     # a page-sized image under many strokes is the paper

    # ------------------------------------------------------------ rendering

    def render(self, box, scale: float, clean: bool = True) -> Image.Image:
        """The region `box` (page units) as an RGB image at `scale` pixels per page unit. `clean` leaves out what is
        not ink: highlighter strokes and the paper's template (PDF pages only)."""
        x0, y0, x1, y1 = self.clip(box)
        w, h = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
        if self.is_pdf:
            return _render_pdf(self, (x0, y0, x1, y1), scale, clean, (w, h))
        img = self._image()
        sx, sy = img.width / self.W, img.height / self.H
        crop = img.crop((round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy)))
        if crop.size != (w, h):
            crop = crop.resize((w, h), Image.LANCZOS if scale >= 1 else Image.BOX)
        return crop

    def _image(self) -> Image.Image:
        if self._img is None:
            img = Image.open(io.BytesIO(self.data) if self.data else self.path)
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                bg = Image.new("RGBA", img.size, "white")
                img = Image.alpha_composite(bg, img)
            self._img = img.convert("RGB")
        return self._img


# ===================================================================== opening a page

def _safe(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._\-]+", "_", Path(name or "file").name).strip("._") or "file"
    return base[:80]


def open_page(pdir: Path, page: dict) -> PageSource:
    """The original of page `page` (a page record of the project): the PDF page or the image it was made from, kept
    in originals/ when the notes were recorded. Without the original, the page image itself."""
    src = str(page.get("source") or "")
    m = re.match(r"^(.*) p\.(\d+)$", src)
    name, idx = (m.group(1), int(m.group(2)) - 1) if m else (src, 0)
    folder = pdir / "originals"
    cand = None
    if name and folder.is_dir():
        safe = _safe(name)
        for f in sorted(folder.iterdir()):
            if f.is_file() and (f.name == safe or re.sub(r"^\d+-", "", f.name) == safe):
                cand = f
                break
    if cand is not None:
        try:
            data = cand.read_bytes()
            if cand.suffix.lower() == ".pdf" or data[:5] == b"%PDF-":
                return _open_pdf(data, idx)
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            return PageSource("image", float(img.width), float(img.height), data=data, _img=None)
        except Exception:  # noqa: BLE001 - an original that cannot be read: the page image is used
            pass
    f = pdir / "pages" / page.get("file", f"p{int(page['n']):03d}.jpg")
    img = Image.open(f)
    return PageSource("pageimage", float(img.width), float(img.height), path=f)


def _open_pdf(data: bytes, index: int) -> PageSource:
    import pypdfium2 as pdfium
    with _PDFIUM:
        pdf = pdfium.PdfDocument(data)
        try:
            page = pdf[index]
            w, h = page.get_size()
            page.close()
        finally:
            pdf.close()
    return PageSource("pdf", float(w), float(h), data=data, index=index)


# ===================================================================== PDF internals

def _device_map(page) -> np.ndarray:
    """The affine map from PDF user space to page units (top-left origin, y down), as a 2x3 matrix."""
    import pypdfium2.raw as raw
    w, h = page.get_size()
    k = 64.0                                         # device pixels per point, so that rounding is negligible
    sx, sy = int(round(w * k)), int(round(h * k))

    def dev(x, y):
        dx, dy = ctypes.c_int(), ctypes.c_int()
        raw.FPDF_PageToDevice(page.raw, 0, 0, sx, sy, 0, ctypes.c_double(x), ctypes.c_double(y), ctypes.byref(dx), ctypes.byref(dy))
        return dx.value / k, dy.value / k

    l, b, r, t = page.get_cropbox()
    o = np.array(dev(l, b))
    ex = (np.array(dev(l + 100, b)) - o) / 100
    ey = (np.array(dev(l, b + 100)) - o) / 100
    # page = o + ex * (x - l) + ey * (y - b)
    return np.array([[ex[0], ey[0], o[0] - ex[0] * l - ey[0] * b],
                     [ex[1], ey[1], o[1] - ex[1] * l - ey[1] * b]])


def _matrix(obj) -> np.ndarray:
    import pypdfium2.raw as raw
    m = raw.FS_MATRIX()
    if not raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)):
        return np.array([[1.0, 0, 0], [0, 1.0, 0]])
    return np.array([[m.a, m.c, m.e], [m.b, m.d, m.f]])


def _compose(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """outer o inner for 2x3 affine matrices."""
    A = np.vstack([outer, [0, 0, 1]]) @ np.vstack([inner, [0, 0, 1]])
    return A[:2]


def _walk(page):
    """(object handle, type, matrix to page units) for every object of the page, forms opened, in drawing order. The
    place of an object in this list identifies it again in another opening of the same file."""
    import pypdfium2.raw as raw
    base = _device_map(page)
    out = []

    def rec(objs, count, get, mat):
        for i in range(count):
            o = get(objs, i)
            if not o:
                continue
            t = raw.FPDFPageObj_GetType(o)
            m = _compose(mat, _matrix(o))
            if t == raw.FPDF_PAGEOBJ_FORM:
                rec(o, raw.FPDFFormObj_CountObjects(o), raw.FPDFFormObj_GetObject, m)
            else:
                out.append((o, t, m))
    rec(page.raw, raw.FPDFPage_CountObjects(page.raw), raw.FPDFPage_GetObject, base)
    return out


def _rgba(obj, stroke: bool) -> tuple[int, int, int, int]:
    import pypdfium2.raw as raw
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    fn = raw.FPDFPageObj_GetStrokeColor if stroke else raw.FPDFPageObj_GetFillColor
    if not fn(obj, *(ctypes.byref(x) for x in (r, g, b, a))):
        return (0, 0, 0, 255)
    return (r.value, g.value, b.value, a.value)


def _bezier(p0, p1, p2, p3, step: float) -> np.ndarray:
    """Points of a cubic Bezier curve (without p0), about `step` apart."""
    L = np.hypot(*(p1 - p0)) + np.hypot(*(p2 - p1)) + np.hypot(*(p3 - p2))
    n = int(min(64, max(2, np.ceil(L / step))))
    t = np.linspace(0, 1, n + 1)[1:, None]
    u = 1 - t
    return u ** 3 * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t ** 3 * p3


def _subpaths(obj, mat: np.ndarray, step: float = 0.25) -> list[tuple[np.ndarray, bool]]:
    """The subpaths of a path object as (points in page units, closed)."""
    import pypdfium2.raw as raw
    n = raw.FPDFPath_CountSegments(obj)
    pts: list = []
    subs: list[tuple[np.ndarray, bool]] = []
    bez: list = []
    closed = False
    x, y = ctypes.c_float(), ctypes.c_float()
    A, t = mat[:, :2], mat[:, 2]

    def flush():
        nonlocal pts, closed
        if len(pts) >= 1:
            subs.append((np.array(pts, dtype=float), closed))
        pts, closed = [], False
    for i in range(n):
        seg = raw.FPDFPath_GetPathSegment(obj, i)
        kind = raw.FPDFPathSegment_GetType(seg)
        raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        p = A @ np.array([x.value, y.value]) + t
        if kind == raw.FPDF_SEGMENT_MOVETO:
            flush()
            pts = [p]
            bez = []
        elif kind == raw.FPDF_SEGMENT_LINETO:
            if not pts:
                pts = [p]
            else:
                pts.append(p)
        elif kind == raw.FPDF_SEGMENT_BEZIERTO:
            bez.append(p)
            if len(bez) == 3:
                if pts:
                    pts.extend(_bezier(np.asarray(pts[-1]), bez[0], bez[1], bez[2], step))
                else:
                    pts = [bez[2]]
                bez = []
        if raw.FPDFPathSegment_GetClose(seg):
            closed = True
    flush()
    return subs


def _ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _ring_perimeter(r: np.ndarray) -> float:
    return float(np.hypot(*np.diff(np.vstack([r, r[:1]]), axis=0).T).sum())


def point_in_ring(p, r: np.ndarray) -> bool:
    x, y = p
    xs, ys = r[:, 0], r[:, 1]
    xs2, ys2 = np.roll(xs, -1), np.roll(ys, -1)
    cond = (ys > y) != (ys2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = xs + (y - ys) * (xs2 - xs) / (ys2 - ys)
    return bool(np.count_nonzero(cond & (x < xc)) % 2)


def _bbox(arrs) -> tuple[float, float, float, float]:
    a = np.vstack(arrs)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))


def _read_strokes(src: PageSource) -> list[Stroke]:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    with _PDFIUM:
        pdf = pdfium.PdfDocument(src.data)
        try:
            page = pdf[src.index]
            objs = _walk(page)
            out: list[Stroke] = []
            order = 0
            for key, (o, t, mat) in enumerate(objs):
                if t != raw.FPDF_PAGEOBJ_PATH:
                    continue
                fm, st = ctypes.c_int(), ctypes.c_int()
                raw.FPDFPath_GetDrawMode(o, ctypes.byref(fm), ctypes.byref(st))
                filled, stroked = fm.value != 0, bool(st.value)
                if not filled and not stroked:
                    continue
                det = abs(np.linalg.det(mat[:, :2])) ** 0.5 or 1.0
                w = ctypes.c_float()
                raw.FPDFPageObj_GetStrokeWidth(o, ctypes.byref(w))
                subs = _subpaths(o, mat)
                if not subs:
                    continue
                if filled:
                    col = _rgba(o, False)
                    rings = [s for s, _ in subs if len(s) >= 3]
                    if not rings:
                        continue
                    for grp, width in _merge_touching(_group_rings(rings)):
                        order += 1
                        out.append(Stroke(rings=grp, line=None, width=width, color=col[:3],
                                          alpha=col[3] / 255, evenodd=fm.value == 1, bbox=_bbox(grp), order=order, obj=key))
                if stroked:
                    col = _rgba(o, True)
                    width = max(0.05, w.value * det)
                    for s, closed in subs:
                        if len(s) < 1:
                            continue
                        line = np.vstack([s, s[:1]]) if closed and len(s) > 2 else s
                        order += 1
                        x0, y0, x1, y1 = _bbox([line])
                        out.append(Stroke(rings=[], line=line, width=width, color=col[:3], alpha=col[3] / 255,
                                          evenodd=False, bbox=(x0 - width / 2, y0 - width / 2, x1 + width / 2, y1 + width / 2),
                                          order=order, obj=key))
            page.close()
        finally:
            pdf.close()
    _classify(out, src.W, src.H)
    return out


def _group_rings(rings: list[np.ndarray]) -> list[list[np.ndarray]]:
    """Subpaths of one path object that form one stroke: an outline with the holes inside it."""
    boxes = [_bbox([r]) for r in rings]
    parent = list(range(len(rings)))
    order = sorted(range(len(rings)), key=lambda i: -(boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
    for pos, i in enumerate(order):
        bi = boxes[i]
        for j in order[:pos]:
            bj = boxes[j]
            if parent[j] != j:
                continue
            if bi[0] >= bj[0] and bi[1] >= bj[1] and bi[2] <= bj[2] and bi[3] <= bj[3] and point_in_ring(rings[i][0], rings[j]):
                parent[i] = j
                break
    groups: dict[int, list] = {}
    for i in range(len(rings)):
        groups.setdefault(parent[i], []).append(rings[i])
    return list(groups.values())


def _tube_width(grp: list) -> float:
    """The width of the pen that drew a filled outline. With holes (a letter's bowl) the ink is a closed tube: its
    area over half its perimeter. Without, an open tube with round ends, of the same area and perimeter: for a long
    stroke that is again its area over half its perimeter, for a dot its diameter, and in between neither too thin
    nor too thick."""
    areas = sorted((_ring_area(r) for r in grp), reverse=True)
    P = sum(_ring_perimeter(r) for r in grp) or 1.0
    if len(grp) > 1:
        A = max(areas[0] - sum(areas[1:]), 0.05 * areas[0])
        return max(0.05, 2 * A / P)
    A = areas[0]
    return max(0.05, (P - math.sqrt(max(0.0, P * P - 4 * math.pi * A))) / math.pi)


def _points_in_ring(P: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Which of the points P lie inside the closed outline r (even-odd rule)."""
    x, y = P[:, 0:1], P[:, 1:2]
    xs, ys = r[None, :, 0], r[None, :, 1]
    xs2, ys2 = np.roll(xs, -1, axis=1), np.roll(ys, -1, axis=1)
    cond = (ys > y) != (ys2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = xs + (y - ys) * (xs2 - xs) / (ys2 - ys)
    return (np.count_nonzero(cond & (x < xc), axis=1) % 2).astype(bool)


def _merge_touching(groups: list) -> list:
    """Outlines of one path object that overlap are one pen stroke: some apps store a stroke as a run of short pieces
    that overlap, filled together. Returns [(rings, pen width)]; the width of a stroke made of pieces is the typical
    width of its pieces (the width of their union would count the overlaps)."""
    widths = [_tube_width(g) for g in groups]
    if len(groups) < 2:
        return [(g, w) for g, w in zip(groups, widths)]
    boxes = [_bbox(g) for g in groups]
    outer = [max(g, key=_ring_area) for g in groups]
    parent = list(range(len(groups)))

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(groups)):
        a = boxes[i]
        for j in range(i + 1, len(groups)):
            b = boxes[j]
            if a[0] > b[2] or b[0] > a[2] or a[1] > b[3] or b[1] > a[3]:
                continue
            if _points_in_ring(outer[i], outer[j]).any() or _points_in_ring(outer[j], outer[i]).any():
                ri, rj = f(i), f(j)
                if ri != rj:
                    parent[rj] = ri
    merged: dict[int, list] = {}
    for i in range(len(groups)):
        merged.setdefault(f(i), []).append(i)
    out = []
    for ids in merged.values():
        rings = [r for i in ids for r in groups[i]]
        if len(ids) == 1:
            out.append((rings, widths[ids[0]]))
            continue
        # the typical width of the pieces, each counted by its length (half its perimeter)
        ws = np.array([widths[i] for i in ids])
        wt = np.array([sum(_ring_perimeter(r) for r in groups[i]) for i in ids])
        k = np.argsort(ws)
        c = np.cumsum(wt[k])
        out.append((rings, float(ws[k][np.searchsorted(c, c[-1] / 2)])))
    return out


def _straight(pts: np.ndarray, tol: float) -> bool:
    if len(pts) < 2:
        return False
    a, b = pts[0], pts[np.argmax(np.hypot(*(pts - pts[0]).T))]
    d = b - a
    L = np.hypot(*d)
    if L == 0:
        return False
    dist = np.abs((pts[:, 0] - a[0]) * d[1] - (pts[:, 1] - a[1]) * d[0]) / L
    return bool(dist.max() <= tol)


def _classify(strokes: list[Stroke], W: float, H: float) -> None:
    """Mark what is not ink: highlighter (translucent), white strokes that cover ink (an eraser), and the paper's own
    template: a page-sized fill, or a set of long, straight, parallel lines across the page (ruled or squared paper)."""
    rules = []
    for s in strokes:
        r, g, b = s.color
        x0, y0, x1, y1 = s.bbox
        bw, bh = x1 - x0, y1 - y0
        if s.alpha < 0.7:
            s.role = "highlight"
        elif min(r, g, b) >= 238:
            s.role = "eraser"
        elif bw * bh > 0.3 * W * H and s.line is None and len(s.rings) <= 2 and all(len(q) <= 12 for q in s.rings):
            s.role = "template"                 # a background: a few straight sides around most of the page
        else:
            thin = min(bw, bh) <= max(1.5, 2.5 * s.width)
            long_ = (bw >= 0.45 * W and bh <= 0.02 * H) or (bh >= 0.45 * H and bw <= 0.02 * W)
            if thin and long_:
                pts = s.line if s.line is not None else np.vstack(s.rings)
                if _straight(pts, max(1.0, 1.5 * s.width)):
                    rules.append(s)
    if len(rules) >= 4:                         # one long line is the author's; a set of them is the paper
        for s in rules:
            s.role = "template"


def _read_images(src: PageSource) -> list[dict]:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    out = []
    with _PDFIUM:
        pdf = pdfium.PdfDocument(src.data)
        try:
            page = pdf[src.index]
            for o, t, mat in _walk(page):
                if t != raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                pw, ph = ctypes.c_uint(), ctypes.c_uint()
                if not raw.FPDFImageObj_GetImagePixelSize(o, ctypes.byref(pw), ctypes.byref(ph)):
                    continue
                corners = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], float)
                pts = corners @ mat[:, :2].T + mat[:, 2]
                x0, y0 = pts.min(axis=0)
                x1, y1 = pts.max(axis=0)
                if x1 - x0 < 1 or y1 - y0 < 1:
                    continue
                out.append({"bbox": (float(x0), float(y0), float(x1), float(y1)),
                            "scale": max(pw.value / (x1 - x0), ph.value / (y1 - y0))})
            page.close()
        finally:
            pdf.close()
    return out


def _render_pdf(src: PageSource, box, scale: float, clean: bool, size) -> Image.Image:
    import pypdfium2 as pdfium
    x0, y0, x1, y1 = box
    hide: set[int] = set()
    if clean:
        roles: dict[int, set] = {}
        for s in src.strokes():
            roles.setdefault(s.obj, set()).add(s.role)
        hide = {k for k, r in roles.items() if r <= {"highlight", "template"}}
    with _PDFIUM:
        pdf = pdfium.PdfDocument(src.data)
        try:
            page = pdf[src.index]
            if hide:
                _hide_objects(page, hide)
            W, H = page.get_size()
            crop = (x0, H - y1, W - x1, y0)
            img = page.render(scale=scale, crop=crop, may_draw_forms=True).to_pil().convert("RGB")
            page.close()
        finally:
            pdf.close()
    if img.size != tuple(size):
        img = img.resize(size, Image.LANCZOS)
    return img


def _hide_objects(page, keys: set[int]) -> None:
    """Make the objects `keys` (places in _walk's order) transparent, so that the render shows only the ink. This
    works for objects inside forms too, which cannot be removed one by one."""
    import pypdfium2.raw as raw
    for key, (o, t, mat) in enumerate(_walk(page)):
        if key in keys:
            for fn, get in ((raw.FPDFPageObj_SetFillColor, False), (raw.FPDFPageObj_SetStrokeColor, True)):
                r, g, b, _ = _rgba(o, get)
                fn(o, r, g, b, 0)


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a, b) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
