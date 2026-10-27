# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Checking a redrawn figure against the drawing, by laying one over the other.

The redrawing is made in the coordinates the drawing was measured in, so the two can be compared pixel by pixel.
Both are thinned to their centre lines, and a centre line counts as matched where the other figure has ink within a
tolerance (the few pen widths by which the redrawing may straighten or align things). The labels are left out of
this comparison: handwriting and type never look alike, and the labels are checked by reading them.
  - covered: how much of the drawing's ink has a counterpart in the redrawing. Below 1, something drawn is missing;
  - precise: how much of the redrawing has a counterpart in the drawing. Below 1, something was added;
  - the missing and the added pieces themselves, each with its place, if longer than about a letter.
A figure whose TikZ was edited (by the author's request or a repair) is compiled, and its picture is laid over the
drawing at the scale the coordinates imply, after finding the shift that fits best."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from . import figimg as fi

PNG_PX_PER_CM = 2.6 * 72.27 / 2.54          # paper.render_tikz renders at scale 2.6 (pixels per point)


KEEP_ROLES = ("draw", "barb", "bar", "dash", "dotrow")


def drawing_ink(sc) -> np.ndarray:
    """The ink of the drawing without its labels and without what is not part of the figure. A piece of ink that
    holds nothing of the drawing goes whole; in a scan, where the drawing and a label or a speck touching it are one
    piece of ink, only the ink along what is not drawing goes."""
    from .figmeasure import _polyline, raster_polylines
    ink = sc.ink
    m = ink.mask.copy()
    pen = max(ink.pen, 2.0)
    by_stroke: dict = {}
    for p in sc.prims:
        by_stroke.setdefault(p.stroke, []).append(p)
    in_label = set()
    for lb in sc.labels:
        if lb.kind in ("label", "candidate", "ignore"):
            in_label.update(lb.strokes)
    for st in ink.strokes:
        ps = by_stroke.get(st.id, [])
        keep = [p for p in ps if p.role in KEEP_ROLES]
        drop = [p for p in ps if p.role not in KEEP_ROLES]
        x0, y0 = st.x0, st.y0
        hh, ww = st.mask.shape
        if not keep and (drop or st.id in in_label):
            m[y0:y0 + hh, x0:x0 + ww] &= ~st.mask
            continue
        if not drop:
            continue

        def lines(group, extra):
            polys = [(_polyline(p) if p.kind in ("line", "circle", "dot") else p.pts) - [x0, y0] for p in group]
            widths = [max(pen, p.width) + extra + (2 * p.r if p.kind == "dot" else 0) for p in group]
            return raster_polylines((hh, ww), polys, widths)
        gone = lines(drop, pen) & ~lines(keep, 0.5 * pen) & st.mask
        m[y0:y0 + hh, x0:x0 + ww] &= ~gone
    return m


def compare(orig: np.ndarray, drawn: np.ndarray, tol: float, min_len: float) -> dict:
    """Centre lines of `orig` (the drawing) and `drawn` (the redrawing), both masks in the same pixels."""
    so = fi.thin(orig) if orig.any() else orig
    sd = fi.thin(drawn) if drawn.any() else drawn
    r = max(1, int(round(tol)))
    near_d = fi.dilate(drawn, r)
    near_o = fi.dilate(orig, r)
    no, nd = int(so.sum()), int(sd.sum())
    miss = so & ~near_d
    extra = sd & ~near_o
    covered = 1.0 - miss.sum() / no if no else 1.0
    precise = 1.0 - extra.sum() / nd if nd else (1.0 if not no else 0.0)
    return {"covered": round(float(covered), 3), "precise": round(float(precise), 3),
            "missing": _pieces(miss, min_len), "extra": _pieces(extra, min_len),
            "_miss": miss, "_extra": extra, "_so": so, "_sd": sd}


def _pieces(mask: np.ndarray, min_len: float) -> list[tuple]:
    if not mask.any():
        return []
    grown = fi.dilate(mask, 3)
    lab, n = fi.label(grown)
    out = []
    for k in range(1, n + 1):
        part = mask & (lab == k)
        cnt = int(part.sum())
        if cnt >= min_len:
            ys, xs = np.nonzero(part)
            out.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, cnt))
    out.sort(key=lambda b: -b[4])
    return out[:8]


def report(res: dict, shape) -> dict:
    """What is kept of a comparison: the two numbers and the places (as 0..1000 boxes on the figure)."""
    H, W = shape

    def norm(b):
        return [round(b[1] / H * 1000), round(b[0] / W * 1000), round(b[3] / H * 1000), round(b[2] / W * 1000)]
    return {"covered": res["covered"], "precise": res["precise"],
            "missing": [norm(b) for b in res["missing"]], "extra": [norm(b) for b in res["extra"]]}


def overlay(rgb: np.ndarray, res: dict) -> Image.Image:
    """The drawing in light grey, with the redrawing's centre lines in green, what is missing from the redrawing in
    red and what was added in blue."""
    base = 255 - (255 - rgb.astype(np.int32)) * 0.35
    out = base.astype(np.uint8)
    sd = fi.dilate(res["_sd"], 1)
    out[sd] = (40, 160, 60)
    out[fi.dilate(res["_miss"], 2)] = (220, 30, 30)
    out[fi.dilate(res["_extra"], 2)] = (30, 80, 230)
    return Image.fromarray(out)


# ----------------------------------------------------------------- a compiled picture laid over the drawing

def png_mask(png: bytes) -> np.ndarray:
    im = Image.open(io.BytesIO(png)).convert("L")
    return np.asarray(im) < 140


def place(png: bytes, shape, k: float, guess=None) -> np.ndarray:
    """The compiled picture drawn into the drawing's pixels: scaled by the known ratio (centimetres per pixel `k`),
    then shifted to the place where it covers the drawing best."""
    H, W = shape
    im = Image.open(io.BytesIO(png)).convert("L")
    f = 1.0 / (k * PNG_PX_PER_CM)                 # drawing pixels per picture pixel
    w2, h2 = max(1, int(round(im.width * f))), max(1, int(round(im.height * f)))
    small = np.asarray(im.resize((w2, h2), Image.BILINEAR)) < 160
    return small


def best_shift(target: np.ndarray, pic: np.ndarray, guess: tuple[int, int] | None = None, search: int | None = None) -> tuple[int, int]:
    """The shift (dx, dy) of `pic` inside the frame of `target` that makes the two overlap most (FFT correlation of
    the slightly widened masks)."""
    H, W = target.shape
    h, w = pic.shape
    A = fi.dilate(target, 2).astype(np.float32)
    B = fi.dilate(pic, 2).astype(np.float32)
    PH, PW = H + h, W + w
    FA = np.fft.rfft2(A, s=(PH, PW))
    FB = np.fft.rfft2(B[::-1, ::-1], s=(PH, PW))
    corr = np.fft.irfft2(FA * FB, s=(PH, PW))
    # corr[y, x] = sum A[i, j] B[i - y + h - 1, j - x + w - 1]: the picture's top-left at (x - w + 1, y - h + 1)
    if guess is not None and search is not None:
        gx, gy = guess
        ys = slice(max(0, gy + h - 1 - search), min(PH, gy + h - 1 + search + 1))
        xs = slice(max(0, gx + w - 1 - search), min(PW, gx + w - 1 + search + 1))
        sub = corr[ys, xs]
        y, x = np.unravel_index(int(np.argmax(sub)), sub.shape)
        y += ys.start
        x += xs.start
    else:
        y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)
    return int(x - w + 1), int(y - h + 1)


def paste(shape, pic: np.ndarray, dx: int, dy: int) -> np.ndarray:
    H, W = shape
    out = np.zeros((H, W), bool)
    h, w = pic.shape
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = min(W, dx + w), min(H, dy + h)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = pic[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    return out
