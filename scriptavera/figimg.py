# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Small image operations on numpy arrays, so that the drawings can be measured with numpy and Pillow alone:
connected parts, thinning to one-pixel lines, growing and shrinking a mask, filling an outline, and the threshold
that separates ink from paper."""

from __future__ import annotations

import numpy as np


# ------------------------------------------------------------------ connected parts

def label(mask: np.ndarray, conn: int = 8) -> tuple[np.ndarray, int]:
    """Connected parts of a boolean mask: (labels with 0 for background and 1..n for the parts, n)."""
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    if not m.any():
        return np.zeros((h, w), np.int32), 0
    pad = np.zeros((h, w + 2), np.int8)
    pad[:, 1:-1] = m
    d = np.diff(pad, axis=1)
    rows, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)            # row-major order: the k-th start and the k-th end are one run [start, end)
    n = len(starts)
    parent = list(range(n))

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root
    row_first = np.searchsorted(rows, np.arange(h + 1)).tolist()
    st, en = starts.tolist(), ends.tolist()
    ext = 1 if conn == 8 else 0
    for r in range(1, h):
        a0, a1 = row_first[r - 1], row_first[r]
        b0, b1 = row_first[r], row_first[r + 1]
        if a0 == a1 or b0 == b1:
            continue
        k = a0
        for j in range(b0, b1):
            s_, e_ = st[j], en[j]
            while k < a1 and en[k] + ext <= s_:      # previous-row runs entirely to the left
                k += 1
            q = k
            while q < a1 and st[q] < e_ + ext:
                ri, rj = find(q), find(j)
                if ri != rj:
                    if ri < rj:
                        parent[rj] = ri
                    else:
                        parent[ri] = rj
                q += 1
    roots = np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)
    uniq, lab = np.unique(roots, return_inverse=True)
    lens = ends - starts
    rr = np.repeat(rows, lens)
    offs = np.arange(lens.sum()) - np.repeat(np.cumsum(lens) - lens, lens)
    cc = np.repeat(starts, lens) + offs
    out = np.zeros((h, w), np.int32)
    out[rr, cc] = np.repeat(lab + 1, lens)
    return out, len(uniq)


def boxes(labels: np.ndarray, n: int) -> np.ndarray:
    """Bounding boxes (x0, y0, x1, y1, area) of labelled parts 1..n (row k-1 for part k); x1, y1 exclusive."""
    out = np.zeros((n, 5), np.int64)
    if n == 0:
        return out
    ys, xs = np.nonzero(labels)
    ls = labels[ys, xs] - 1
    out[:, 0] = np.iinfo(np.int64).max
    out[:, 1] = np.iinfo(np.int64).max
    np.minimum.at(out[:, 0], ls, xs)
    np.minimum.at(out[:, 1], ls, ys)
    np.maximum.at(out[:, 2], ls, xs + 1)
    np.maximum.at(out[:, 3], ls, ys + 1)
    np.add.at(out[:, 4], ls, 1)
    return out


# ------------------------------------------------------------------ morphology

def dilate(mask: np.ndarray, r: int = 1) -> np.ndarray:
    """Grow a mask by r pixels (a square neighbourhood, applied r times as 3x3, which makes an octagon-like disc)."""
    m = np.asarray(mask, dtype=bool).copy()
    for i in range(int(r)):
        p = np.pad(m, 1)
        if i % 2 == 0:                                # alternate square and cross: closer to a disc
            m = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
                 | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
        else:
            m = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return m


def erode(mask: np.ndarray, r: int = 1) -> np.ndarray:
    return ~dilate(~np.asarray(mask, dtype=bool), r)


def close(mask: np.ndarray, r: int = 1) -> np.ndarray:
    return erode(dilate(mask, r), r)


def dist_within(mask: np.ndarray, r: int) -> np.ndarray:
    """Pixels at most about r pixels from the mask."""
    return dilate(mask, r)


def chamfer(mask: np.ndarray, cap: int = 60) -> np.ndarray:
    """Approximate Euclidean distance (in pixels) from every pixel to the nearest pixel of `mask`, capped at `cap`.
    Two-pass 3-4 chamfer distance, vectorised by rows."""
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    INF = 10 ** 6
    d = np.where(m, 0, INF).astype(np.int64)
    # forward pass
    for y in range(h):
        row = d[y]
        if y > 0:
            up = d[y - 1]
            cand = np.minimum(up + 3, np.minimum(np.r_[INF, up[:-1]] + 4, np.r_[up[1:], INF] + 4))
            row = np.minimum(row, cand)
        # left to right along the row: running minimum of row[x'] + 3 (x - x')
        idx = np.arange(w)
        row = np.minimum.accumulate(row - 3 * idx) + 3 * idx
        d[y] = row
    for y in range(h - 1, -1, -1):
        row = d[y]
        if y < h - 1:
            dn = d[y + 1]
            cand = np.minimum(dn + 3, np.minimum(np.r_[INF, dn[:-1]] + 4, np.r_[dn[1:], INF] + 4))
            row = np.minimum(row, cand)
        idx = np.arange(w)
        rev = row[::-1] - 3 * idx
        row = (np.minimum.accumulate(rev) + 3 * idx)[::-1]
        d[y] = row
    return np.minimum(d / 3.0, cap)


# ------------------------------------------------------------------ thinning

def _nbrs(img: np.ndarray):
    p = np.pad(img, 1)
    # P2..P9 clockwise from north
    return (p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:], p[2:, 2:], p[2:, 1:-1], p[2:, :-2], p[1:-1, :-2], p[:-2, :-2])


def thin(mask: np.ndarray, max_iter: int = 200) -> np.ndarray:
    """Zhang-Suen thinning to lines one pixel wide, then the removal of the pixels that only make staircases, so that
    every pixel of a plain line has exactly two neighbours."""
    img = np.asarray(mask, dtype=np.uint8).copy()
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            P2, P3, P4, P5, P6, P7, P8, P9 = _nbrs(img)
            B = P2.astype(np.int8) + P3 + P4 + P5 + P6 + P7 + P8 + P9
            seq = (P2, P3, P4, P5, P6, P7, P8, P9, P2)
            A = np.zeros_like(B)
            for i in range(8):
                A += (seq[i] == 0) & (seq[i + 1] == 1)
            if step == 0:
                m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((P2 & P4 & P6) == 0) & ((P4 & P6 & P8) == 0)
            else:
                m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((P2 & P4 & P8) == 0) & ((P2 & P6 & P8) == 0)
            if m.any():
                img[m] = 0
                changed = True
        if not changed:
            break
    return _unstair(img.astype(bool))


def _ring_components() -> np.ndarray:
    """For each of the 256 sets of 8-neighbours (bit i = ring position i, from north clockwise), the number of
    8-connected groups they form without the centre pixel."""
    pos = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    lut = np.zeros(256, np.int8)
    for code in range(256):
        on = [i for i in range(8) if code >> i & 1]
        par = {i: i for i in on}

        def f(i):
            while par[i] != i:
                i = par[i]
            return i
        for a in on:
            for b in on:
                if a < b and max(abs(pos[a][0] - pos[b][0]), abs(pos[a][1] - pos[b][1])) == 1:
                    par[f(b)] = f(a)
        lut[code] = len({f(i) for i in on})
    return lut


_RING = _ring_components()


def _code(sk: np.ndarray) -> np.ndarray:
    nb = _nbrs(sk.astype(np.uint8))
    c = np.zeros(sk.shape, np.int32)
    for i, x in enumerate(nb):
        c |= x.astype(np.int32) << i
    return c


def _unstair(sk: np.ndarray) -> np.ndarray:
    """Remove skeleton pixels that are not needed for 8-connectivity (the corner pixel of an L-shaped step): a pixel
    whose neighbours stay one connected group without it and that has two 4-neighbours at a right angle."""
    sk = sk.copy()
    for _ in range(4):
        code = _code(sk)
        n4 = [(code >> i) & 1 for i in (0, 2, 4, 6)]            # N, E, S, W
        corner = ((n4[0] & n4[1]) | (n4[1] & n4[2]) | (n4[2] & n4[3]) | (n4[3] & n4[0])).astype(bool)
        cnt = np.zeros(sk.shape, np.int32)
        for i in range(8):
            cnt += (code >> i) & 1
        cand = sk & corner & (cnt >= 2) & (_RING[code] == 1)
        if not cand.any():
            break
        changed = False
        h, w = sk.shape
        for y, x in zip(*np.nonzero(cand)):
            c = 0
            for i, (dy, dx) in enumerate(((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))):
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and sk[yy, xx]:
                    c |= 1 << i
            if bin(c).count("1") >= 2 and _RING[c] == 1 and ((c & 1 and c & 4) or (c & 4 and c & 16) or (c & 16 and c & 64) or (c & 64 and c & 1)):
                sk[y, x] = False
                changed = True
        if not changed:
            break
    return sk


def neighbours(sk: np.ndarray) -> np.ndarray:
    """Number of 8-neighbours of every pixel that are in the mask."""
    s = sk.astype(np.uint8)
    return sum(x.astype(np.uint8) for x in _nbrs(s)) * sk


# ------------------------------------------------------------------ outlines

def fill(rings: list, shape: tuple[int, int], evenodd: bool = False) -> np.ndarray:
    """Pixels whose centres lie inside closed outlines (each an (n, 2) array of x, y in pixel units, x to the right
    and y down), with the nonzero winding rule or the even-odd rule."""
    h, w = shape
    out = np.zeros((h, w), bool)
    segs = []
    for r in rings:
        r = np.asarray(r, float)
        if len(r) < 3:
            continue
        a = r
        b = np.roll(r, -1, axis=0)
        segs.append(np.hstack([a, b]))
    if not segs:
        return out
    S = np.vstack(segs)
    x0, y0, x1, y1 = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
    up = y1 > y0
    ylo, yhi = np.minimum(y0, y1), np.maximum(y0, y1)
    keep = yhi > ylo
    x0, y0, x1, y1, up, ylo, yhi = (v[keep] for v in (x0, y0, x1, y1, up, ylo, yhi))
    if not len(x0):
        return out
    rmin = max(0, int(np.floor(ylo.min() - 0.5)))
    rmax = min(h - 1, int(np.ceil(yhi.max() - 0.5)))
    xs_all = np.arange(w) + 0.5
    for i in range(rmin, rmax + 1):
        yc = i + 0.5
        act = (ylo <= yc) & (yhi > yc)
        if not act.any():
            continue
        xa, ya, xb, yb, dirs = x0[act], y0[act], x1[act], y1[act], np.where(up[act], 1, -1)
        xi = xa + (yc - ya) * (xb - xa) / (yb - ya)
        order = np.argsort(xi)
        xi, dirs = xi[order], dirs[order]
        if evenodd:
            wind = np.arange(1, len(xi) + 1) % 2
        else:
            wind = np.cumsum(dirs)
        # pixel centres between xi[k] and xi[k+1] are inside when wind[k] != 0
        inside = wind[:-1] != 0
        for k in np.nonzero(inside)[0]:
            a_, b_ = xi[k], xi[k + 1]
            j0 = max(0, int(np.ceil(a_ - 0.5)))
            j1 = min(w - 1, int(np.floor(b_ - 0.5)))
            if j1 >= j0:
                out[i, j0:j1 + 1] = True
    return out


def draw_polyline(shape, pts: np.ndarray, width: float) -> np.ndarray:
    """A polyline drawn with round caps and joins, `width` pixels wide, as a mask (pixel units, x right, y down)."""
    from PIL import Image, ImageDraw
    h, w = shape
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    pts = np.asarray(pts, float)
    r = max(0.5, width / 2)
    if len(pts) == 1:
        x, y = pts[0]
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    else:
        d.line([tuple(p) for p in pts], fill=255, width=max(1, int(round(width))), joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    return np.asarray(im) > 127


# ------------------------------------------------------------------ thresholds

def otsu(values: np.ndarray, bins: int = 128) -> float:
    v = np.asarray(values, float).ravel()
    if v.size == 0:
        return 0.5
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return lo
    hist, edges = np.histogram(v, bins=bins, range=(lo, hi))
    p = hist / hist.sum()
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(p)
    m = np.cumsum(p * mids)
    mt = m[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sb = (mt * w0 - m) ** 2 / (w0 * (1 - w0))
    sb = np.nan_to_num(sb)
    return float(mids[int(np.argmax(sb))])


def fill_holes(mask: np.ndarray, max_area: float) -> np.ndarray:
    """The mask with its holes of at most `max_area` pixels filled (a hole: paper enclosed by the mask)."""
    m = np.asarray(mask, dtype=bool)
    lab, n = label(~m, conn=4)
    if n <= 1:
        return m.copy()
    bx = boxes(lab, n)
    h, w = m.shape
    small = np.zeros(n + 1, bool)
    for k in range(n):
        x0, y0, x1, y1, area = bx[k]
        if area <= max_area and x0 > 0 and y0 > 0 and x1 < w and y1 < h:
            small[k + 1] = True
    return m | small[lab]


def hysteresis(strong: np.ndarray, weak: np.ndarray) -> np.ndarray:
    """The parts of `weak` that contain a pixel of `strong`."""
    lab, n = label(weak)
    if n == 0:
        return np.zeros_like(weak, bool)
    keep = np.zeros(n + 1, bool)
    keep[np.unique(lab[strong & weak])] = True
    keep[0] = False
    return keep[lab]
