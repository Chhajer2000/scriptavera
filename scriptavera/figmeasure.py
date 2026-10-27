# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Measuring a drawing: its ink turned into lines, curves, circles, dots and arrows, and its handwritten labels set
apart.

Nothing here knows what a drawing shows. Every rule is about shapes and sizes, measured against two lengths taken
from the page itself, the width of the pen and the height of the handwriting:
  1. each piece of ink is thinned to its centre line. A pen stroke of a PDF is thinned on its own, so two strokes
     that cross stay two strokes; for a scan, where strokes that touch are one piece of ink, a crossing is undone by
     continuing each line in the direction it was going (a line passing straight through a crossing is one line);
  2. a centre line is cut at its corners, and every part is fitted: a straight line if it stays within a fraction of
     the pen width of its chord, an arc or a circle if a circle fits it as closely, and otherwise a smooth curve;
  3. a dot is a piece of ink hardly longer than the pen is wide; an arrowhead is a pair of short straight strokes (or
     one) meeting at the end of a longer line and pointing back along it; a dashed or dotted line is a row of short
     dashes or dots, evenly spaced along a line;
  4. what is left in small, curly pieces of the size of the handwriting is grouped into words: these are the labels.
     The model that reads them is asked, for each group, what it says, or whether it is part of the drawing after all;
  5. a drawing made of separate pictures (two trees and an arrow between them, three diagrams in a row) is divided
     where white space separates them: these are its panels."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import figimg as fi
from .figink import Ink, InkStroke

DEG = math.pi / 180


@dataclass
class Prim:
    """A fitted piece of the drawing, in pixels of the region (x right, y down)."""
    id: int
    kind: str                     # line | arc | curve | circle | ellipse | dot | closed
    pts: np.ndarray               # the measured centre line it was fitted to
    stroke: int                   # the InkStroke it came from
    color: tuple
    width: float
    p0: np.ndarray | None = None  # line, arc, curve: end points
    p1: np.ndarray | None = None
    center: np.ndarray | None = None   # arc, circle, ellipse, dot
    r: float = 0.0                # radius (circle, arc, dot); ellipse: semi-axes in rx, ry
    rx: float = 0.0
    ry: float = 0.0
    angle: float = 0.0            # ellipse rotation (radians)
    bez: list = field(default_factory=list)   # curve: cubic Bezier segments [(p0, c1, c2, p3)]
    role: str = "draw"            # draw | label | barb | bar | dash | dotrow | ignore
    group: int = -1               # label group, arrow or dash row it belongs to
    closed: bool = False

    @property
    def length(self) -> float:
        return float(_arclen(self.pts)) if len(self.pts) > 1 else 0.0

    def ends(self) -> list[np.ndarray]:
        if self.kind in ("line", "arc", "curve") and not self.closed:
            return [self.p0, self.p1]
        return []

    def direction_at(self, which: int) -> np.ndarray:
        """Unit vector pointing out of the primitive at end `which` (0 = p0, 1 = p1)."""
        if self.kind == "line":
            d = (self.p0 - self.p1) if which == 0 else (self.p1 - self.p0)
        else:
            pts = self.pts if which == 1 else self.pts[::-1]
            L = _arclen(pts)
            k = _index_at_length(pts[::-1], min(L * 0.3, 4 * self.width))
            d = pts[-1] - pts[::-1][k]
        n = np.hypot(*d)
        return d / n if n > 0 else np.array([1.0, 0.0])


@dataclass
class Arrow:
    prim: int
    end: int                      # 0 or 1: which end of the shaft carries the head
    barbs: list
    head: str = "open"            # open | triangle | filled


@dataclass
class Label:
    id: int
    box: tuple                    # x0, y0, x1, y1 in region pixels
    strokes: list                 # InkStroke ids
    prims: list                   # Prim ids
    text: str = ""                # what it says, in LaTeX (from the model)
    color: tuple = (0, 0, 0)
    kind: str = "candidate"       # candidate | label | drawing | ignore


@dataclass
class Panel:
    id: int
    box: tuple                    # x0, y0, x1, y1 in region pixels
    prims: list
    labels: list


@dataclass
class Scene:
    ink: Ink
    prims: list
    arrows: list
    labels: list
    panels: list
    rows: list = field(default_factory=list)      # dash and dot rows: {"kind", "prims", "p0", "p1"}
    bars: list = field(default_factory=list)      # (shaft prim id, end) with a bar across (maps-to arrows)
    notes: list = field(default_factory=list)

    texts: dict = field(default_factory=dict)      # label id -> LaTeX, once read
    kind: dict = field(default_factory=dict)       # stroke id -> draw | small | dot

    def prim(self, i: int) -> Prim:
        return self.prims[i]

    def drawing(self) -> list:
        return [p for p in self.prims if p.role == "draw"]


# ===================================================================== small geometry

def _arclen(pts: np.ndarray) -> float:
    if len(pts) < 2:
        return 0.0
    return float(np.hypot(*np.diff(pts, axis=0).T).sum())


def _cum(pts: np.ndarray) -> np.ndarray:
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))] if len(pts) > 1 else np.zeros(len(pts))


def _index_at_length(pts: np.ndarray, L: float) -> int:
    c = _cum(pts)
    return int(min(len(pts) - 1, np.searchsorted(c, L)))


def resample(pts: np.ndarray, step: float, closed: bool = False) -> np.ndarray:
    pts = np.asarray(pts, float)
    if closed and len(pts) > 2 and np.hypot(*(pts[0] - pts[-1])) > 1e-9:
        pts = np.vstack([pts, pts[:1]])
    if len(pts) < 2:
        return pts.copy()
    c = _cum(pts)
    L = c[-1]
    if L <= 0:
        return pts[:1].copy()
    n = max(2, int(round(L / step)) + 1)
    t = np.linspace(0, L, n)
    return np.column_stack([np.interp(t, c, pts[:, 0]), np.interp(t, c, pts[:, 1])])


def smooth(pts: np.ndarray, sigma: float, closed: bool = False) -> np.ndarray:
    """Gaussian smoothing of a polyline sampled at about one pixel; the ends stay where they are."""
    n = len(pts)
    if n < 5 or sigma <= 0.3:
        return pts.copy()
    k = int(min(n // 2, max(2, round(2.5 * sigma))))
    x = np.arange(-k, k + 1)
    g = np.exp(-x * x / (2 * sigma * sigma))
    g /= g.sum()
    if closed:
        P = np.vstack([pts[-k:], pts, pts[:k]])
        out = np.column_stack([np.convolve(P[:, 0], g, "valid"), np.convolve(P[:, 1], g, "valid")])
        return out
    # reflect about the end points, so that the ends do not shrink inwards
    head = 2 * pts[0] - pts[1:k + 1][::-1]
    tail = 2 * pts[-1] - pts[-k - 1:-1][::-1]
    P = np.vstack([head, pts, tail])
    return np.column_stack([np.convolve(P[:, 0], g, "valid"), np.convolve(P[:, 1], g, "valid")])


def line_fit(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Total least squares line: (point on it, unit direction, largest distance, rms distance)."""
    c = pts.mean(axis=0)
    u, s, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    nrm = np.array([-d[1], d[0]])
    dist = (pts - c) @ nrm
    return c, d, float(np.abs(dist).max()), float(np.sqrt((dist ** 2).mean()))


def circle_fit(pts: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Algebraic circle fit (Kasa) refined by a few Gauss-Newton steps: (centre, radius, largest radial error)."""
    x, y = pts[:, 0], pts[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return pts.mean(axis=0), 0.0, float("inf")
    cx, cy = sol[0] / 2, sol[1] / 2
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-9))
    c = np.array([cx, cy])
    for _ in range(5):
        dv = pts - c
        dd = np.hypot(dv[:, 0], dv[:, 1])
        dd = np.where(dd < 1e-9, 1e-9, dd)
        res = dd - r
        J = np.column_stack([-dv[:, 0] / dd, -dv[:, 1] / dd, -np.ones_like(dd)])
        try:
            step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        except np.linalg.LinAlgError:
            break
        c = c + step[:2]
        r = r + step[2]
    err = np.abs(np.hypot(*(pts - c).T) - r)
    return c, abs(float(r)), float(err.max())


def ellipse_fit(pts: np.ndarray):
    """Direct least-squares ellipse (Fitzgibbon): (centre, rx, ry, angle, largest error) or None."""
    x, y = pts[:, 0], pts[:, 1]
    mx, my = x.mean(), y.mean()
    sx = max(x.std(), 1e-6)
    sy = max(y.std(), 1e-6)
    xn, yn = (x - mx) / sx, (y - my) / sy
    D = np.column_stack([xn * xn, xn * yn, yn * yn, xn, yn, np.ones_like(xn)])
    S = D.T @ D
    C = np.zeros((6, 6))
    C[0, 2] = C[2, 0] = 2
    C[1, 1] = -1
    try:
        evals, evecs = np.linalg.eig(np.linalg.solve(S, C))
    except np.linalg.LinAlgError:
        return None
    evals = np.real(evals)
    evecs = np.real(evecs)
    idx = [i for i in range(6) if evals[i] > 1e-12 and np.isfinite(evals[i])]
    if not idx:
        return None
    a = evecs[:, idx[0]]
    # back to pixel coordinates
    A_, B_, C_, D_, E_, F_ = a
    A2 = A_ / sx ** 2
    B2 = B_ / (sx * sy)
    C2 = C_ / sy ** 2
    D2 = -2 * A_ * mx / sx ** 2 - B_ * my / (sx * sy) + D_ / sx
    E2 = -2 * C_ * my / sy ** 2 - B_ * mx / (sx * sy) + E_ / sy
    F2 = (A_ * mx ** 2 / sx ** 2 + B_ * mx * my / (sx * sy) + C_ * my ** 2 / sy ** 2 - D_ * mx / sx - E_ * my / sy + F_)
    M = np.array([[A2, B2 / 2], [B2 / 2, C2]])
    try:
        c = np.linalg.solve(2 * M, [-D2, -E2])
    except np.linalg.LinAlgError:
        return None
    Fc = F2 + 0.5 * (D2 * c[0] + E2 * c[1])
    w, v = np.linalg.eigh(M)
    if np.any(w * -Fc <= 0):
        return None
    axes = np.sqrt(-Fc / w)
    ang = math.atan2(v[1, 0], v[0, 0])
    rx, ry = float(axes[0]), float(axes[1])
    # error: distance along the radial direction, approximately
    rel = pts - c
    ca, sa = math.cos(-ang), math.sin(-ang)
    u = rel[:, 0] * ca - rel[:, 1] * sa
    vv = rel[:, 0] * sa + rel[:, 1] * ca
    rr = np.hypot(u, vv)
    th = np.arctan2(vv / ry, u / rx)
    re = np.hypot(rx * np.cos(th), ry * np.sin(th))
    err = np.abs(rr - re)
    return c, rx, ry, ang, float(err.max())


def bezier_points(seg, n: int = 24) -> np.ndarray:
    p0, c1, c2, p3 = (np.asarray(v, float) for v in seg)
    t = np.linspace(0, 1, n)[:, None]
    u = 1 - t
    return u ** 3 * p0 + 3 * u * u * t * c1 + 3 * u * t * t * c2 + t ** 3 * p3


def fit_bezier(pts: np.ndarray, tol: float, max_depth: int = 6) -> list:
    """Smooth cubic Bezier segments through a polyline, with a largest error of about `tol` (Schneider's method)."""
    pts = np.asarray(pts, float)
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        a, b = pts
        return [(a, a + (b - a) / 3, a + 2 * (b - a) / 3, b)]
    t0 = _unit(pts[min(3, len(pts) - 1)] - pts[0])
    t1 = _unit(pts[max(-4, -len(pts))] - pts[-1])
    return _fit_cubic(pts, t0, t1, tol, max_depth)


def _unit(v):
    n = np.hypot(*v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def _turn(u, v) -> float:
    """The angle (0..pi) between two directions."""
    nu, nv = float(np.hypot(*u)), float(np.hypot(*v))
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    return math.acos(max(-1.0, min(1.0, float(np.dot(u, v)) / (nu * nv))))


def _chord_params(pts):
    c = _cum(pts)
    return c / c[-1] if c[-1] > 0 else np.linspace(0, 1, len(pts))


def _gen_bezier(pts, u, t0, t1):
    p0, p3 = pts[0], pts[-1]
    A1 = (3 * (1 - u) ** 2 * u)[:, None] * t0
    A2 = (3 * (1 - u) * u ** 2)[:, None] * t1
    C = np.array([[np.sum(A1 * A1), np.sum(A1 * A2)], [np.sum(A1 * A2), np.sum(A2 * A2)]])
    B0 = (1 - u) ** 3
    B3 = u ** 3
    tmp = pts - (B0 + 3 * (1 - u) ** 2 * u)[:, None] * p0 - (3 * (1 - u) * u ** 2 + B3)[:, None] * p3
    X = np.array([np.sum(A1 * tmp), np.sum(A2 * tmp)])
    det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
    seg_len = np.hypot(*(p3 - p0))
    if abs(det) > 1e-12:
        a1 = (X[0] * C[1, 1] - X[1] * C[0, 1]) / det
        a2 = (C[0, 0] * X[1] - C[1, 0] * X[0]) / det
    else:
        a1 = a2 = seg_len / 3
    eps = 1e-6 * seg_len
    if a1 < eps or a2 < eps:
        a1 = a2 = seg_len / 3
    return (p0, p0 + t0 * a1, p3 + t1 * a2, p3)


def _bez_eval(seg, u):
    p0, c1, c2, p3 = seg
    u = u[:, None]
    return (1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * c1 + 3 * (1 - u) * u ** 2 * c2 + u ** 3 * p3


def _fit_cubic(pts, t0, t1, tol, depth):
    u = _chord_params(pts)
    seg = _gen_bezier(pts, u, t0, t1)
    for _ in range(4):                        # reparameterise (Newton) and refit
        q = _bez_eval(seg, u)
        err = np.hypot(*(q - pts).T)
        if err.max() <= tol:
            return [seg]
        p0, c1, c2, p3 = seg
        d1 = 3 * ((1 - u)[:, None] ** 2 * (c1 - p0) + 2 * ((1 - u) * u)[:, None] * (c2 - c1) + u[:, None] ** 2 * (p3 - c2))
        d2 = 6 * ((1 - u)[:, None] * (c2 - 2 * c1 + p0) + u[:, None] * (p3 - 2 * c2 + c1))
        num = np.sum((q - pts) * d1, axis=1)
        den = np.sum(d1 * d1, axis=1) + np.sum((q - pts) * d2, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            nu = np.where(np.abs(den) > 1e-12, u - num / den, u)
        nu = np.clip(nu, 0, 1)
        nu[0], nu[-1] = 0.0, 1.0
        if np.all(np.diff(nu) >= 0):
            u = nu
        seg = _gen_bezier(pts, u, t0, t1)
    q = _bez_eval(seg, u)
    err = np.hypot(*(q - pts).T)
    if err.max() <= tol or depth <= 0 or len(pts) < 6:
        return [seg]
    k = int(np.clip(np.argmax(err), 2, len(pts) - 3))
    tc = _unit(pts[k - 1] - pts[k + 1])
    return _fit_cubic(pts[:k + 1], t0, tc, tol, depth - 1) + _fit_cubic(pts[k:], -tc, t1, tol, depth - 1)


# ===================================================================== centre lines

@dataclass
class _CL:
    pts: np.ndarray               # pixel coordinates (x, y), dense
    closed: bool
    stroke: int
    t_ends: tuple = (False, False)   # whether each end stops at a junction with another line
    r: float = 0.0                # for a dot: the radius of the blob of ink


def _trace(sk: np.ndarray, pen: float) -> list[tuple[np.ndarray, bool, tuple]]:
    """The centre lines of a thinned mask as polylines (x, y): crossings are undone by continuing each line in the
    direction it was going. Returns [(points, closed, (end0 at a junction, end1 at a junction))]."""
    h, w = sk.shape
    deg = fi.neighbours(sk)
    if not sk.any():
        return []
    junc = sk & (deg >= 3)
    jl, nj = fi.label(junc)
    # edge pixels: the skeleton without junction pixels
    ep = sk & ~junc
    el, ne = fi.label(ep)
    out = []
    if ne == 0 and nj == 0:
        return out
    # node centres
    jcent = {}
    if nj:
        ys, xs = np.nonzero(jl)
        for k in range(1, nj + 1):
            pass
        lab = jl[ys, xs]
        cnt = np.bincount(lab, minlength=nj + 1)
        sx = np.bincount(lab, weights=xs, minlength=nj + 1)
        sy = np.bincount(lab, weights=ys, minlength=nj + 1)
        for k in range(1, nj + 1):
            if cnt[k]:
                jcent[k] = np.array([sx[k] / cnt[k], sy[k] / cnt[k]])
    # order the pixels of every edge component
    ys, xs = np.nonzero(el)
    labs = el[ys, xs]
    order = np.argsort(labs, kind="stable")
    ys, xs, labs = ys[order], xs[order], labs[order]
    bounds = np.searchsorted(labs, np.arange(1, ne + 2))
    OFF = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    edges = []                        # (pixels list [(y, x)], junction at start, junction at end, closed)
    for k in range(ne):
        a, b = bounds[k], bounds[k + 1]
        pix = list(zip(ys[a:b].tolist(), xs[a:b].tolist()))
        if not pix:
            continue
        S = set(pix)

        def nb(p):
            y, x = p
            return [(y + dy, x + dx) for dy, dx in OFF if (y + dy, x + dx) in S]

        def jn(p):
            y, x = p
            js = set()
            for dy, dx in OFF:
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and jl[yy, xx]:
                    js.add(int(jl[yy, xx]))
            return js
        ends = [p for p in pix if len(nb(p)) <= 1]
        closed = False
        if not ends:
            start = pix[0]
            closed = True
        else:
            start = ends[0]
        seq = [start]
        seen = {start}
        cur = start
        while True:
            nx = [q for q in nb(cur) if q not in seen]
            if not nx:
                break
            # prefer 4-neighbours, then the one that keeps the direction
            nx.sort(key=lambda q: abs(q[0] - cur[0]) + abs(q[1] - cur[1]))
            cur = nx[0]
            seq.append(cur)
            seen.add(cur)
        if len(seq) < len(pix):
            # a branching left in the edge (rare): keep what was traced; the rest becomes its own edge
            rest = [p for p in pix if p not in seen]
            for p in rest:
                edges.append(([p], jn(p), set(), False))
        j0 = jn(seq[0])
        j1 = jn(seq[-1]) if len(seq) > 1 else set()
        if closed and (j0 or j1):
            closed = False
        edges.append((seq, j0, j1, closed))
    # junction clusters that touch no edge, or only one pixel: a dot or a tiny blob
    used_j = set()
    for e in edges:
        used_j |= e[1] | e[2]
    for k in range(1, nj + 1):
        if k not in used_j and k in jcent:
            out.append((jcent[k][None, :].copy(), False, (False, False)))
    # build the graph: node ids for junctions; edges with their end nodes
    E = []
    for seq, j0, j1, closed in edges:
        P = np.array([[x, y] for y, x in seq], float)
        n0 = min(j0) if j0 else None
        n1 = min(j1) if j1 else None
        if len(seq) == 1 and j0 and not j1:
            n1 = max(j0) if len(j0) > 1 else None
        if n0 is not None:
            P = np.vstack([jcent[n0], P])
        if n1 is not None:
            P = np.vstack([P, jcent[n1]])
        E.append({"pts": P, "n": [n0, n1], "closed": closed, "alive": True})
    _prune_spurs(E, pen)
    _merge_close_junctions(E, jcent, pen)
    return out + _resolve(E, pen)


def _prune_spurs(E: list, pen: float) -> None:
    """Short branches from a junction to a free end are artefacts of thinning (at a blob or a corner)."""
    for _ in range(3):
        deg: dict = {}
        for e in E:
            if e["alive"]:
                for n in e["n"]:
                    if n is not None:
                        deg[n] = deg.get(n, 0) + 1
        changed = False
        for e in E:
            if not e["alive"]:
                continue
            n0, n1 = e["n"]
            free = (n0 is None) != (n1 is None)
            if free:
                j = n0 if n0 is not None else n1
                if deg.get(j, 0) >= 3 and _arclen(e["pts"]) < max(1.6 * pen, 4.0):
                    e["alive"] = False
                    deg[j] -= 1
                    changed = True
        if not changed:
            break


def _merge_close_junctions(E: list, jcent: dict, pen: float) -> None:
    """Two junctions joined by a very short edge are one crossing that thinning split in two."""
    for e in E:
        if not e["alive"]:
            continue
        n0, n1 = e["n"]
        if n0 is not None and n1 is not None and n0 != n1 and _arclen(e["pts"]) < max(1.8 * pen, 5.0):
            e["alive"] = False
            keep, gone = min(n0, n1), max(n0, n1)
            jcent[keep] = (jcent[keep] + jcent[gone]) / 2
            for f in E:
                if f["alive"]:
                    f["n"] = [keep if x == gone else x for x in f["n"]]
    # move edge end points to the (possibly moved) junction centres
    for f in E:
        if f["alive"]:
            for i, n in enumerate(f["n"]):
                if n is not None:
                    f["pts"][0 if i == 0 else -1] = jcent[n]


def _resolve(E: list, pen: float) -> list:
    """Join edges through junctions where a line goes straight on; the rest end at the junction."""
    alive = [e for e in E if e["alive"]]
    at: dict = {}
    for i, e in enumerate(alive):
        for side, n in enumerate(e["n"]):
            if n is not None:
                at.setdefault(n, []).append((i, side))
    look = max(3.0 * pen, 8.0)

    def outdir(e, side):
        P = e["pts"] if side == 0 else e["pts"][::-1]
        k = _index_at_length(P, look)
        if k == 0:
            k = min(1, len(P) - 1)
        return _unit(P[k] - P[0])
    pair: dict = {}
    for n, ends in at.items():
        if len(ends) < 2:
            continue
        dirs = {(i, s): outdir(alive[i], s) for i, s in ends}
        cands = []
        for a in range(len(ends)):
            for b in range(a + 1, len(ends)):
                ea, eb = ends[a], ends[b]
                if ea[0] == eb[0] and len(ends) > 2:
                    continue
                cosang = float(np.dot(dirs[ea], dirs[eb]))
                bend = math.pi - math.acos(max(-1.0, min(1.0, cosang)))
                cands.append((bend, ea, eb))
        cands.sort(key=lambda t: t[0])
        used = set()
        limit = 50 * DEG if len(ends) >= 3 else 80 * DEG
        for bend, ea, eb in cands:
            if bend > limit or ea in used or eb in used:
                continue
            used.add(ea)
            used.add(eb)
            pair[ea] = eb
            pair[eb] = ea
    # walk chains of paired edges
    out = []
    done = set()
    for i, e in enumerate(alive):
        if i in done:
            continue
        # find a chain start: an end that is not paired
        start = None
        for side in (0, 1):
            if (i, side) not in pair:
                start = (i, side)
                break
        if start is None:
            # a loop through junctions: start anywhere
            start = (i, 0)
        chain = []
        cur = start
        tj0 = alive[start[0]]["n"][start[1]] is not None
        tj1 = False
        looped = False
        while True:
            ei, side = cur
            if ei in done:
                break
            done.add(ei)
            P = alive[ei]["pts"] if side == 0 else alive[ei]["pts"][::-1]
            chain.append(P if not chain else P[1:])
            other = (ei, 1 - side)
            tj1 = alive[ei]["n"][1 - side] is not None
            if other in pair:
                cur = pair[other]
                if cur == start:
                    looped = True                       # back where it began: a closed line through junctions
                    break
                if cur[0] in done:
                    break
            else:
                break
        pts = np.vstack(chain)
        closed = bool(alive[start[0]]["closed"]) or looped
        if looped and len(pts) > 2 and np.hypot(*(pts[0] - pts[-1])) < 1.5:
            pts = pts[:-1]
        out.append((pts, closed, (tj0 and not closed, tj1 and not closed)))
    return out


def raster_polylines(shape, polys, width, values=None) -> np.ndarray:
    """Polylines (x, y in pixels) drawn into one picture with round joins: a mask, or (with `values`) an integer
    picture holding each polyline's value where it is drawn (the later ones on top). `width` is one width for all,
    or a list with each polyline's own."""
    from PIL import Image, ImageDraw
    h, w = shape
    im = Image.new("I" if values is not None else "L", (max(1, w), max(1, h)), 0)
    d = ImageDraw.Draw(im)
    for k, P in enumerate(polys):
        wk = float(width[k]) if isinstance(width, (list, tuple, np.ndarray)) else float(width)
        wd = max(1, int(round(wk)))
        r = max(0.5, wk / 2)
        val = int(values[k]) if values is not None else 255
        P = np.asarray(P, float)
        if len(P) == 0:
            continue
        if len(P) >= 2:
            d.line([tuple(q) for q in P], fill=val, width=wd, joint="curve")
        for x, y in (P[0], P[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=val)
    a = np.asarray(im)
    return a if values is not None else a > 127


def _blob_dots(st, own: list, pen: float) -> list:
    """Dots pressed on a piece of ink: thick spots, at least twice as wide as the pen and round, that the lines
    through them do not explain. Where lines meet at a small angle, or an arrowhead closes on its tip, the ink is
    thick too; there the thickness of the lines drawn with the pen's width is the same as the ink's, and no dot
    was drawn. Returns [(centre in region pixels, radius)]."""
    m = st.mask
    pen = max(pen, 2.0)
    if min(m.shape) < 1.8 * pen:
        return []
    cap = int(6 * pen) + 2
    d = fi.chamfer(~np.pad(m, 1), cap=cap)[1:-1, 1:-1]
    if not (d >= 1.0 * pen).any():                      # half-width of at least one pen: twice the pen's width
        return []
    # the measured centre lines (not the fitted shapes), each with its own width: they run into the junctions,
    # where the ink is thickest, and a heavy line is as thick as it was drawn
    polys = [(p.pts if len(p.pts) >= 2 else _polyline(p)) - [st.x0, st.y0] for p in own]
    widths = [max(pen, float(p.width)) for p in own]
    lines = raster_polylines(m.shape, polys, widths) if polys else np.zeros(m.shape, bool)
    dl = fi.chamfer(~np.pad(lines, 1), cap=cap)[1:-1, 1:-1]
    # thicker than the lines there explain, by a margin (a hand-drawn line is not of one width all along)
    core = (d >= 1.0 * pen) & (d - dl >= 0.45 * pen) & (d >= 1.6 * dl)
    lab, n = fi.label(core)
    cands = []
    for k in range(1, n + 1):
        part = lab == k
        ys, xs = np.nonzero(part)
        j = int(np.argmax(np.where(part, d, 0)))
        y, x = divmod(j, m.shape[1])
        r = float(d[y, x])
        if max(xs.max() - xs.min(), ys.max() - ys.min()) + 1 > 2.4 * r:
            continue                                    # long and thick: a heavy stretch of line, not a dot
        cands.append((np.array([x + st.x0, y + st.y0], float), r))
    out: list = []
    for c, r in sorted(cands, key=lambda t: -t[1]):     # one blot is one dot
        if any(np.hypot(*(c - c2)) <= r2 + 0.5 * pen for c2, r2 in out):
            continue
        # well wider than the heaviest line through it (its measured centre line may run a little off the ink's)
        hw = max([pen / 2] + [p.width / 2 for p in own if len(p.pts) and _seg_dist(c[None], p.pts) <= r
                              and float(np.hypot(*(p.pts - c).T).max()) >= r + pen])    # lines that leave the blot
        if r < max(1.6 * hw, hw + 0.45 * pen) or not _round_blot(m, c - [st.x0, st.y0], r, pen):
            continue
        # and the thick ink there is round, not a long stretch where a line is drawn twice (a circle's ends overlap)
        cx, cy = int(round(c[0] - st.x0)), int(round(c[1] - st.y0))
        lab2, _ = fi.label(d >= max(pen, 0.7 * r))
        k = lab2[min(max(cy, 0), m.shape[0] - 1), min(max(cx, 0), m.shape[1] - 1)]
        if k:
            ys, xs = np.nonzero(lab2 == k)
            if max(xs.max() - xs.min(), ys.max() - ys.min()) + 1 > 2.6 * r:
                continue
        out.append((c, r))
    return out


def _round_blot(m: np.ndarray, c, r: float, pen: float) -> bool:
    """Whether the ink around a thick spot is a dot's: paper all round it, a little outside its edge, but for the
    few lines that leave it. Where lines merge into a blot (the end of a heavy line inside a small circle, strokes
    crowded at a junction), the ring around the spot is mostly ink."""
    hh, ww = m.shape
    rr = r + 0.8 * pen
    t = np.linspace(0, 2 * math.pi, max(24, int(2 * math.pi * rr)), endpoint=False)
    xs = np.round(c[0] + rr * np.cos(t)).astype(int)
    ys = np.round(c[1] + rr * np.sin(t)).astype(int)
    ok = (xs >= 0) & (xs < ww) & (ys >= 0) & (ys < hh)
    on = np.zeros(len(t), bool)
    on[ok] = m[ys[ok], xs[ok]]
    return float(on.mean()) <= 0.45


def _discs(core: np.ndarray, d: np.ndarray) -> np.ndarray:
    """The union of the discs centred on the pixels of `core`, each as large as its distance to the paper `d`: the
    ink those centres stand for, without the thin lines that leave it."""
    out = np.zeros(core.shape, bool)
    rad = np.round(np.where(core, d, 0)).astype(int)
    for r in np.unique(rad[core]):
        out |= fi.dilate(core & (rad == r), int(r))
    return out


def _thick_parts(m: np.ndarray, pen: float, h: float) -> list[np.ndarray]:
    """Stretches of a scan's piece of ink drawn heavier than the pen: long, at least 1.8 pens wide, and about as
    wide all along (a heavy segment drawn over a thin axis, a line gone over twice). In a scan they are one piece of
    ink with the thin lines they touch, and thinning them together tangles the lines. A dot is round, an arrowhead
    widens towards its base and a junction is short: none of them is such a stretch."""
    if min(m.shape) < 2 * pen:
        return []
    d = fi.chamfer(~np.pad(m, 1), cap=int(8 * pen) + 2)[1:-1, 1:-1]
    core = d >= 0.9 * pen
    if not core.any():
        return []
    lab, n = fi.label(core)
    out = []
    for k in range(1, n + 1):
        part = lab == k
        ys, xs = np.nonzero(part)
        if max(xs.max() - xs.min(), ys.max() - ys.min()) < max(0.8 * h, 4 * pen):
            continue
        R = _discs(part, d) & m
        sk = fi.thin(R)
        ws = d[sk]
        if len(ws) < 5:
            continue
        med = float(np.median(ws))
        if sk.sum() < max(1.0 * h, 6 * med) or float(np.percentile(ws, 25)) < 0.75 * med:
            continue
        out.append(R)
    if out:
        # crumbs of ink left between a heavy part and the paper (the rim of a dot pressed on it) go with it
        allR = np.zeros_like(m)
        for R in out:
            allR |= R
        rest = m & ~allR
        lab2, n2 = fi.label(rest)
        if n2:
            bx = fi.boxes(lab2, n2)
            near = fi.dilate(allR, 1)
            for k in range(n2):
                x0, y0, x1, y1, area = bx[k]
                if max(x1 - x0, y1 - y0) <= 2.0 * pen:
                    part = lab2 == k + 1
                    if (part & near).any():
                        for R in out:
                            if (part & fi.dilate(R, 1)).any():
                                R |= part
                                break
    return out


def centrelines(ink: Ink) -> list[_CL]:
    """The centre lines of every piece of ink."""
    out: list[_CL] = []
    for st in ink.strokes:
        if st.line is not None and len(st.line) >= 2:
            pts = resample(st.line, 1.0)
            closed = bool(np.hypot(*(st.line[0] - st.line[-1])) < 0.5 and len(st.line) > 3)
            # the parts of the line still inked: an eraser, or a filled area taken out of the strokes, removes some
            hh, ww = st.mask.shape
            lx = np.clip(np.round(pts[:, 0] - st.x0).astype(int), 0, ww - 1)
            ly = np.clip(np.round(pts[:, 1] - st.y0).astype(int), 0, hh - 1)
            ok = fi.dilate(st.mask, 1)[ly, lx]
            if ok.all():
                out.append(_CL(pts, closed, st.id))
                continue
            runs, cur = [], []
            for q, good in zip(pts, ok):
                if good:
                    cur.append(q)
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            for run in runs:
                if len(run) >= 3:
                    out.append(_CL(np.array(run), False, st.id))
            continue
        m = st.mask
        ys, xs = np.nonzero(m)
        if not len(ys):
            continue
        hh, ww = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        dot = _CL(np.array([[xs.mean() + st.x0, ys.mean() + st.y0]]), False, st.id, r=float(math.sqrt(len(ys) / math.pi)))
        if max(hh, ww) <= 2.6 * max(st.width, 2.0):
            out.append(dot)
            continue
        layers = [(m, st.width)]
        if not st.vector:
            heavy = _thick_parts(m, max(st.width, 2.0), ink.text_h)
            if heavy:
                rest = m.copy()
                for R in heavy:
                    rest &= ~R
                d = fi.chamfer(~np.pad(m, 1), cap=int(8 * st.width) + 2)[1:-1, 1:-1]
                layers = [(rest, st.width)] + [(R, max(st.width, float(np.median(2 * d[fi.thin(R)] - 1)))) for R in heavy]
        got = []
        for mm, wd in layers:
            if not mm.any():
                continue
            sk = fi.thin(np.pad(mm, 1))[1:-1, 1:-1]
            got += _trace(sk, wd) if sk.any() else []
        if not got:
            out.append(dot)                    # a blob that thins away entirely is a dot
            continue
        for pts, closed, tj in got:
            pts = pts + [st.x0, st.y0]
            out.append(_CL(pts, closed, st.id, tj))
    return out


# ===================================================================== corners and fitting

def _angles(pts: np.ndarray, c: np.ndarray, win: float, closed: bool) -> np.ndarray:
    """At every point, the angle between the chords to the points `win` before and after it."""
    n = len(pts)
    ang = np.zeros(n)
    step = max(1, int(round(win)))
    for i in range(n):
        if closed:
            va = pts[i] - pts[(i - step) % n]
            vb = pts[(i + step) % n] - pts[i]
        else:
            a = np.searchsorted(c, c[i] - win)
            b = min(np.searchsorted(c, c[i] + win), n - 1)
            if a >= i or b <= i:
                continue
            va = pts[i] - pts[a]
            vb = pts[b] - pts[i]
        na, nb_ = np.hypot(*va), np.hypot(*vb)
        if na < 1e-9 or nb_ < 1e-9:
            continue
        ang[i] = math.acos(max(-1.0, min(1.0, float(np.dot(va, vb) / (na * nb_)))))
    return ang


def corners(pts: np.ndarray, pen: float, closed: bool = False) -> list[int]:
    """Indices where the line turns sharply. A corner turns within a short stretch: measured over twice the
    distance, its angle hardly grows. A smooth bend turns gradually: over twice the distance its angle about
    doubles. So a point is a corner when its angle is large and stays nearly the same at the larger scale. The
    pen and the smoothing round a corner a little, so its full angle shows at the larger scale."""
    n = len(pts)
    if n < 7:
        return []
    L = _arclen(pts)
    win = max(1.6 * pen, 5.0)
    if L < 3 * win:
        win = L / 3
    c = _cum(pts)
    ang = _angles(pts, c, win, closed)
    ang2 = _angles(pts, c, 2 * win, closed) if L >= 6 * win else ang
    ang3 = _angles(pts, c, 3 * win, closed) if L >= 9 * win else ang2
    out = []
    thr = 26 * DEG                 # well above the tremor of a hand-drawn line over a few pen widths
    lo = 0.6 * thr
    i = 0
    while i < n:
        if ang[i] > lo:
            j = i
            while j + 1 < n and ang[j + 1] > lo:
                j += 1
            k = i + int(np.argmax(ang[i:j + 1]))
            size = max(ang[k], ang2[k], ang3[k])
            # (on a smooth bend the angle doubles over twice the distance; the tremor of a hand or a scan's noise
            # moves that ratio by a tenth or so, a corner keeps it near one)
            sharp = ang[k] >= 0.72 * max(ang2[k], 1e-9) or ang[k] >= 100 * DEG
            # a corner needs room for a piece on both sides (except on a closed line)
            if size > thr and sharp and (closed or (c[k] > 0.6 * pen and c[-1] - c[k] > 0.6 * pen)):
                out.append(k)
            i = j + 1
        else:
            i += 1
    return out


def _fit(pts: np.ndarray, pen: float, closed: bool, part: bool = False) -> dict:
    """The best shape for one piece of a centre line. `part`: the piece meets other lines (it was cut at a corner,
    or it runs into a junction), so even a short piece has a direction: the short stroke of an arrowhead thinned out
    of a blot of ink. A mark standing on its own must be longer to be more than a dot."""
    L = _arclen(pts)
    span = np.hypot(*(pts.max(axis=0) - pts.min(axis=0)))
    if (L <= 1.0 * pen or span <= 1.25 * pen) if part else (L <= 1.6 * pen or span <= 2.0 * pen):
        c = pts.mean(axis=0)
        return {"kind": "dot", "center": c, "r": max(pen / 2, span / 2)}
    if closed:
        # a closed line is a circle if an ellipse fits it and its axes differ by less than a tenth (a hand-drawn
        # circle is never quite round), an ellipse if they differ more, else a closed curve of its own shape
        el = ellipse_fit(pts)
        c, r, err = circle_fit(pts)
        if el is not None:
            ce, rx, ry, ang, e2 = el
            R = max(rx, ry)
            if e2 <= max(0.9 * pen, 0.06 * R) and R < 4 * span:
                if min(rx, ry) >= 0.9 * R:
                    c2, r2, err2 = circle_fit(pts)
                    return {"kind": "circle", "center": c2, "r": r2}
                return {"kind": "ellipse", "center": ce, "rx": rx, "ry": ry, "angle": ang}
        if r > 0 and err <= max(0.9 * pen, 0.05 * r):
            return {"kind": "circle", "center": c, "r": r}
        return {"kind": "closed", "bez": fit_bezier(np.vstack([pts, pts[:1]]), max(0.6 * pen, 1.5))}
    c, d, dmax, rms = line_fit(pts)
    if dmax <= max(0.75 * pen, 0.045 * L):
        t = (pts - c) @ d
        p0, p1 = c + d * t.min(), c + d * t.max()
        if np.dot(p1 - p0, pts[-1] - pts[0]) < 0:
            p0, p1 = p1, p0
        return {"kind": "line", "p0": p0, "p1": p1}
    if np.hypot(*(pts[0] - pts[-1])) <= max(2 * pen, 0.05 * L) and L > 6 * pen:
        return _fit(pts, pen, True)            # a line that ends where it began is closed
    cc, r, err = circle_fit(pts)
    if r > 0 and err <= max(0.6 * pen, 0.04 * r) and r < 6 * L:
        a0 = math.atan2(*(pts[0] - cc)[::-1])
        a1 = math.atan2(*(pts[-1] - cc)[::-1])
        sweep = _sweep(pts, cc)
        if abs(sweep) >= 25 * DEG:
            return {"kind": "arc", "center": cc, "r": r, "p0": pts[0].copy(), "p1": pts[-1].copy(), "a0": a0, "sweep": sweep}
    return {"kind": "curve", "bez": fit_bezier(pts, max(0.5 * pen, 1.2)), "p0": pts[0].copy(), "p1": pts[-1].copy()}


def _line_off_junction(P: np.ndarray, f: dict, pen: float, j0: bool, j1: bool) -> dict:
    """A straight piece fitted again without its points within a pen's width of a junction at its start (j0) or end
    (j1), then drawn out to the junction along the direction found."""
    c = _cum(P)
    keep = np.ones(len(P), bool)
    if j0:
        keep &= c >= 1.0 * pen
    if j1:
        keep &= c <= c[-1] - 1.0 * pen
    if keep.sum() < max(3, 0.4 * len(P)):
        return f
    Q = P[keep]
    c0, d, dmax, _ = line_fit(Q)
    t = (P - c0) @ d
    p0, p1 = c0 + d * t.min(), c0 + d * t.max()
    if np.dot(p1 - p0, P[-1] - P[0]) < 0:
        p0, p1 = p1, p0
    return {"kind": "line", "p0": p0, "p1": p1}


def _straight_but_junction(P: np.ndarray, pen: float, j0: bool, j1: bool) -> dict | None:
    """A piece that is straight once the stretch where it bends into a junction (up to two and a half pens) is left
    out: the straight line, drawn to the point of it nearest the junction."""
    c = _cum(P)
    L = c[-1]
    keep = np.ones(len(P), bool)
    if j0:
        keep &= c >= 2.5 * pen
    if j1:
        keep &= c <= L - 2.5 * pen
    Q = P[keep]
    if len(Q) < 5 or _arclen(Q) < max(3 * pen, 0.6 * L):
        return None
    c0, d, dmax, _ = line_fit(Q)
    if dmax > max(0.75 * pen, 0.03 * _arclen(Q)):
        return None
    t = (P - c0) @ d
    p0, p1 = c0 + d * t[0], c0 + d * t[-1]
    if np.hypot(*(p1 - p0)) < 1e-6:
        return None
    return {"kind": "line", "p0": p0, "p1": p1}


def _sweep(pts, c) -> float:
    a = np.unwrap(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    return float(a[-1] - a[0])


def _turning(pts: np.ndarray, pen: float) -> float:
    """Total turning (radians) of a centre line, measured on a version smoothed over the pen width."""
    if len(pts) < 5:
        return 0.0
    P = resample(pts, max(pen * 0.75, 1.5))
    if len(P) < 3:
        return 0.0
    v = np.diff(P, axis=0)
    a = np.unwrap(np.arctan2(v[:, 1], v[:, 0]))
    return float(np.abs(np.diff(a)).sum())


# ===================================================================== filled areas

_OFF8 = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))     # (dy, dx) clockwise from north
_IDX8 = {o: i for i, o in enumerate(_OFF8)}


def outline(mask: np.ndarray) -> np.ndarray:
    """The outer boundary of the (first, topmost) connected part of a mask, as pixel centres (x, y) in order
    (Moore-neighbour tracing)."""
    m = np.pad(np.asarray(mask, bool), 1)
    ys, xs = np.nonzero(m)
    if not len(ys):
        return np.zeros((0, 2))
    k0 = int(np.lexsort((xs, ys))[0])
    s = (int(ys[k0]), int(xs[k0]))
    b0 = (s[0], s[1] - 1)
    p, b = s, b0
    pts = [s]
    for _ in range(8 * len(ys) + 8):
        kb = _IDX8[(b[0] - p[0], b[1] - p[1])]
        for j in range(1, 9):
            k = (kb + j) % 8
            q = (p[0] + _OFF8[k][0], p[1] + _OFF8[k][1])
            if m[q]:
                kp = (k - 1) % 8
                b = (p[0] + _OFF8[kp][0], p[1] + _OFF8[kp][1])
                p = q
                break
        else:
            break                                               # a single pixel
        if p == s and b == b0:
            break
        pts.append(p)
    return np.array([(x - 1.0, y - 1.0) for y, x in pts])


def _rdp(P: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker simplification of an open polyline."""
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i], P[j]
        d = b - a
        L = float(np.hypot(*d))
        seg = P[i + 1:j]
        dist = np.hypot(*(seg - a).T) if L < 1e-9 else np.abs((seg[:, 0] - a[0]) * d[1] - (seg[:, 1] - a[1]) * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > eps:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return P[keep]


def simplify_ring(R: np.ndarray, eps: float) -> np.ndarray:
    """A closed outline reduced to its corners (Douglas-Peucker from the two points farthest apart)."""
    if len(R) < 4:
        return R
    a = 0
    b = int(np.argmax(np.hypot(*(R - R[0]).T)))
    a = int(np.argmax(np.hypot(*(R - R[b]).T)))
    R = np.roll(R, -a, axis=0)
    b = (b - a) % len(R)
    first = _rdp(R[:b + 1], eps)
    second = _rdp(np.vstack([R[b:], R[:1]]), eps)
    return np.vstack([first, second[1:-1]])


def _find_fills(ink: Ink, pen: float, h: float) -> list[dict]:
    """Areas filled with ink (a shaded bar, a blackened region): ink much wider than the pen, over more than the
    size of a letter. Each is taken out of the strokes it is made of, so that their outline and their scribbles are
    not read as lines, and becomes one filled shape. A dot, however heavy, is smaller; a heavy line is narrower."""
    H, W = ink.shape
    if not ink.mask.any():
        return []
    cap = int(max(8 * pen, 0.8 * h)) + 2
    d = fi.chamfer(~np.pad(ink.mask, 1), cap=cap)[1:-1, 1:-1]
    core = d >= 1.6 * pen
    if not core.any():
        return []
    lab, n = fi.label(core)
    out = []
    for k in range(1, n + 1):
        part = lab == k
        if int(part.sum()) < (0.8 * h) ** 2 or float(d[part].max()) < max(2.5 * pen, 0.35 * h):
            continue
        region = fi.dilate(part, int(round(1.6 * pen)) + 1) & ink.mask
        ys, xs = np.nonzero(region)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        ring = outline(region[y0:y1, x0:x1]) + [x0, y0]
        poly = simplify_ring(ring, max(0.7 * pen, 1.5))
        if len(poly) < 3:
            continue
        img = ink.wb if ink.wb is not None else ink.rgb
        inner = region & (d >= 1.0 * pen)
        col = tuple(int(v) for v in np.median(img[inner].astype(np.float32), axis=0)) if inner.any() else (0, 0, 0)
        best, most = -1, 0
        for st in ink.strokes:
            hh, ww = st.mask.shape
            sub = region[st.y0:st.y0 + hh, st.x0:st.x0 + ww]
            if sub.shape != st.mask.shape:
                continue
            cnt = int((sub & st.mask).sum())
            if cnt:
                st.mask = st.mask & ~sub
                if cnt > most:
                    best, most = st.id, cnt
                    if ink.vector:
                        col = tuple(st.color)               # a pen stroke's own colour is exact
        out.append({"pts": poly, "color": col, "stroke": best})
    return out


# ===================================================================== the whole drawing

def measure(ink: Ink) -> Scene:
    pen = max(ink.pen, 2.0)
    h = max(ink.text_h, 3 * pen)
    fills = _find_fills(ink, pen, h)
    cls = centrelines(ink)
    strokes = {s.id: s for s in ink.strokes}
    prims: list[Prim] = []
    info: dict[int, dict] = {}            # per stroke: length, turning, size
    for cl in cls:
        st = strokes[cl.stroke]
        pts = cl.pts
        if len(pts) > 3:
            pts = smooth(resample(pts, 1.0, cl.closed), max(0.5, pen / 3), cl.closed)
        rec = info.setdefault(cl.stroke, {"len": 0.0, "turn": 0.0})
        rec["len"] += _arclen(pts)
        rec["turn"] += _turning(pts, pen)
        if len(pts) == 1:
            r = cl.r or (st.width / 2 if st.line is not None else pen / 2)
            pr = Prim(len(prims), "dot", pts, cl.stroke, st.color, st.width, center=pts[0].copy(), r=max(r, 0.5))
            if r < 0.4 * pen:
                pr.role = "ignore"                     # a speck smaller than the pen: not a mark of the author
            prims.append(pr)
            continue
        cuts = corners(pts, pen, cl.closed)
        if cl.closed and len(cuts) < 2:
            cuts = []                          # one "corner" on a closed line is where it was joined, not a corner
        if cuts and any(cl.t_ends):
            # a thinned line bends as it runs into a junction (towards the junction's centre): not a corner
            c = _cum(pts)
            cuts = [k for k in cuts if not ((cl.t_ends[0] and c[k] < 1.5 * pen) or (cl.t_ends[1] and c[-1] - c[k] < 1.5 * pen))]
        pieces = []
        if cl.closed and cuts:
            k0 = cuts[0]
            rolled = np.vstack([pts[k0:], pts[1:k0 + 1]])
            idx = [c - k0 for c in cuts[1:]] + [len(rolled) - 1]
            last = 0
            for k in idx:
                pieces.append((rolled[last:k + 1], False))
                last = k
            closed_poly = True
        else:
            last = 0
            for k in cuts + [len(pts) - 1]:
                if k > last:
                    pieces.append((pts[last:k + 1], cl.closed and not cuts))
                last = k
            closed_poly = False
        first = len(prims)
        part = len(pieces) > 1 or any(cl.t_ends)
        fits = []
        for k, (P, closed) in enumerate(pieces):
            f = _fit(P, pen, closed, part)
            # near a junction the thinned line bends towards the junction's centre: a straight piece takes its
            # direction from the rest of it, and still reaches the junction; a piece that is straight but for that
            # bend (the side of a box where an arrow runs into it) is a straight line
            j0 = k == 0 and not cl.closed and cl.t_ends[0]
            j1 = k == len(pieces) - 1 and not cl.closed and cl.t_ends[1]
            if f["kind"] == "line" and (j0 or j1):
                f = _line_off_junction(P, f, pen, j0, j1)
            elif f["kind"] in ("arc", "curve") and (j0 or j1):
                g = _straight_but_junction(P, pen, j0, j1)
                if g is not None:
                    f = g
            fits.append((P, closed, f))
        if part:
            # a piece no longer than the pen is wide, between two corners, at an end or at a junction, is where the
            # pen turned or landed, or a stub that thinning left: not a dot of its own
            kept = [x for x in fits if x[2]["kind"] != "dot"]
            if not kept and not any(cl.t_ends):
                span = float(np.hypot(*(pts.max(axis=0) - pts.min(axis=0))))
                kept = [(pts, False, {"kind": "dot", "center": pts.mean(axis=0), "r": max(pen / 2, span / 2)})]
            fits = kept
        for P, closed, f in fits:
            pr = Prim(len(prims), f["kind"], P, cl.stroke, st.color, st.width, closed=closed or f["kind"] in ("circle", "ellipse", "closed"))
            if f["kind"] == "line":
                pr.p0, pr.p1 = f["p0"], f["p1"]
            elif f["kind"] == "arc":
                pr.p0, pr.p1, pr.center, pr.r = f["p0"], f["p1"], f["center"], f["r"]
            elif f["kind"] == "curve":
                pr.p0, pr.p1, pr.bez = f["p0"], f["p1"], f["bez"]
            elif f["kind"] == "circle":
                pr.center, pr.r = f["center"], f["r"]
            elif f["kind"] == "dot":
                # (a tap of a pen stroked in a PDF is as wide as its stroke; in a scan the blot gives the size)
                pr.center, pr.r = f["center"], max(f["r"], st.width / 2 if st.line is not None else 0.0)
            elif f["kind"] == "ellipse":
                pr.center, pr.rx, pr.ry, pr.angle = f["center"], f["rx"], f["ry"], f["angle"]
            elif f["kind"] == "closed":
                pr.bez = f["bez"]
            prims.append(pr)
        _merge_collinear(prims, first, pen, closed_poly)
    prims = [p for p in prims if p.role != "gone"]
    for i, p in enumerate(prims):
        p.id = i
    per_stroke: dict[int, list] = {}
    for p in prims:
        per_stroke.setdefault(p.stroke, []).append(p)
    for sid, ps in per_stroke.items():
        if len(ps) == 1 and ps[0].kind == "dot":                  # a blot drawn on its own: as large as its ink
            area = float(strokes[sid].mask.sum())
            ps[0].r = max(ps[0].r, math.sqrt(area / math.pi))
    _filled_dots(prims, ink.mask, pen)
    for f in fills:
        prims.append(Prim(len(prims), "fill", f["pts"], max(f["stroke"], 0), f["color"], pen, closed=True))
    if not ink.vector:
        _sample_colors(prims, ink)
        _sample_widths(prims, ink, pen)
        _faint_fringes(prims, pen)
    # dots pressed on the lines: thick spots that the lines through them do not explain
    for st in ink.strokes:
        if st.line is not None or st.vector:
            continue                       # a dot drawn with a pen of a PDF is a stroke of its own
        own = [p for p in prims if p.stroke == st.id and p.kind not in ("dot", "fill")]
        if not own:
            continue
        for c, r in _blob_dots(st, own, pen):
            col = st.color
            if not ink.vector:
                from .figink import ink_color
                img = ink.wb if ink.wb is not None else ink.rgb
                y0, y1 = int(max(0, c[1] - 0.6 * r)), int(c[1] + 0.6 * r) + 1
                x0, x1 = int(max(0, c[0] - 0.6 * r)), int(c[0] + 0.6 * r) + 1
                col = ink_color(img[y0:y1, x0:x1].reshape(-1, 3))
            prims.append(Prim(len(prims), "dot", c[None, :], st.id, col, st.width, center=c, r=r))
    prims = _join_curves(prims, pen, h, ink.mask)
    prims = _recover_circles(prims, pen, h, ink.mask)
    prims = _join_lines(prims, pen, h, ink.mask)
    _pen_blobs(prims, pen)
    sc = Scene(ink=ink, prims=prims, arrows=[], labels=[], panels=[])
    _stroke_kinds(sc, info, pen, h)
    _find_arrows(sc, pen, h)
    _drop_hooks(sc, pen, h)
    _find_rows(sc, pen, h, "dashes")
    _find_labels(sc, pen, h)
    if _rejoin(sc, pen, h):
        for p in sc.prims:
            if p.role in ("barb", "bar"):
                p.role = "draw"
        sc.arrows, sc.bars = [], []
        _find_arrows(sc, pen, h)
    _find_rows(sc, pen, h, "dots")
    _find_panels(sc, pen, h)
    return sc


def add_heads(sc: Scene, tips: list) -> list[str]:
    """Arrowheads the model sees in the figure, [(tip in region pixels, kind)]. A model points at a tip only roughly,
    so each is first matched to a head the measurement found (the nearest, within about a letter and a half); one
    left over is put at the free end of a line that runs towards it, the nearest such end. The short strokes and
    blots at that end were its head. Returns what was added."""
    pen = max(sc.ink.pen, 2.0)
    h = max(sc.ink.text_h, 3 * pen)
    H, W = sc.ink.shape
    R = max(1.5 * h, 0.035 * max(H, W))

    def at(a: Arrow) -> np.ndarray:
        s = sc.prims[a.prim]
        return s.p0 if a.end == 0 else s.p1
    tips = [(np.asarray(T, float), kind) for T, kind in tips]
    pairs = sorted((float(np.hypot(*(at(a) - T))), i, j) for i, (T, _) in enumerate(tips) for j, a in enumerate(sc.arrows))
    matched_t, matched_a = set(), set()
    for d, i, j in pairs:
        if d <= R and i not in matched_t and j not in matched_a:
            matched_t.add(i)
            matched_a.add(j)
    owner = None if sc.ink.vector else _owner_image(sc, sc.ink.shape, 1.1 * h, pen + 2)
    added = []
    for i, (T, kind) in enumerate(tips):
        if i in matched_t:
            continue                                          # found by the measurement too
        best = None
        for p in sc.prims:
            if p.role != "draw" or p.kind not in ("line", "arc", "curve") or p.closed or p.length < 1.1 * h:
                continue
            for e in (0, 1):
                if any(a.prim == p.id and a.end == e for a in sc.arrows):
                    continue
                E = p.p0 if e == 0 else p.p1
                d = float(np.hypot(*(E - T)))
                if d > R:
                    continue
                u = p.direction_at(e)
                w = T - E
                if float(np.dot(w, u)) < -0.5 * h:
                    continue                                  # the tip lies behind this end: not its head
                score = d + abs(float(u[0] * w[1] - u[1] * w[0]))
                if best is None or score < best[0]:
                    best = (score, p, e)
        if best is None:
            continue
        _, s, e = best
        E = s.p0 if e == 0 else s.p1
        u = s.direction_at(e)
        if not sc.ink.vector:                                 # the line runs on to the tip of its ink
            Tn = _ink_tip(sc, s, E, u, 1.6 * h, owner)
            if 0 < float(np.dot(Tn - E, u)) <= 1.2 * h:
                if e == 0:
                    s.p0 = Tn.copy()
                else:
                    s.p1 = Tn.copy()
                E = Tn
        barbs = []
        for q in sc.prims:
            if q is s or q.role != "draw":
                continue
            if q.kind == "dot" and np.hypot(*(q.center - E)) <= 0.6 * h:
                q.role = "barb"
                barbs.append(q.id)
            elif q.kind in ("line", "arc", "curve") and not q.closed and q.length <= 0.8 * h:
                if _seg_dist(E[None], _polyline(q)) <= 0.6 * h and float(np.dot((q.p0 + q.p1) / 2 - E, u)) <= 0.5 * h:
                    q.role = "barb"                           # (a head is often one stroke, bent or curved)
                    barbs.append(q.id)
        sc.arrows.append(Arrow(prim=s.id, end=e, barbs=barbs, head=kind if kind in ("open", "triangle", "filled") else "open"))
        added.append(f"{kind} head at ({E[0]:.0f}, {E[1]:.0f})")
    return added


def redo_shapes(sc: Scene) -> None:
    """Find arrows, rows and panels again, after groups of ink were moved between the drawing and its labels."""
    pen = max(sc.ink.pen, 2.0)
    h = max(sc.ink.text_h, 3 * pen)
    for p in sc.prims:
        if p.role in ("barb", "bar", "dash", "dotrow"):
            p.role = "draw"
            p.group = -1
    sc.arrows, sc.bars, sc.rows = [], [], []
    _rejoin(sc, pen, h)
    _find_arrows(sc, pen, h)
    _find_rows(sc, pen, h)
    _find_panels(sc, pen, h)


def _sample_colors(prims: list, ink: Ink) -> None:
    """The colour of each piece of a scan, read along its own centre line in the white-balanced picture: two inks
    that cross are one piece of ink, but not one colour. On the centre line the pen covers the paper, so the
    median there is the ink's colour; the few places where another ink crosses do not move it."""
    from .figink import ink_color
    H, W = ink.shape
    img = ink.wb if ink.wb is not None else ink.rgb
    for p in prims:
        if p.kind == "fill":
            continue
        P = p.pts if len(p.pts) else (p.center[None, :] if p.center is not None else None)
        if P is None or not len(P):
            continue
        step = max(1, len(P) // 80)
        xs = np.clip(np.round(P[::step, 0]).astype(int), 0, W - 1)
        ys = np.clip(np.round(P[::step, 1]).astype(int), 0, H - 1)
        ok = ink.mask[ys, xs]
        if ok.sum() < 2:
            continue
        px = img[ys[ok], xs[ok]]
        p.color = ink_color(px) if len(px) < 8 else tuple(int(v) for v in np.median(px.astype(np.float32), axis=0))


def _sample_widths(prims: list, ink: Ink, pen: float) -> None:
    """The width of each line of a scan, measured along it away from its ends (where lines meet, the ink is
    wider): a heavy line drawn among thin ones keeps its weight."""
    from .figink import widths_along
    H, W = ink.shape
    wid = widths_along(ink.mask, cap=8 * pen)
    for p in prims:
        if p.kind in ("dot", "fill") or len(p.pts) < 3:
            continue
        c = _cum(p.pts)
        keep = (c >= 1.2 * pen) & (c <= c[-1] - 1.2 * pen)
        P = p.pts[keep] if keep.sum() >= 3 else p.pts
        xs = np.clip(np.round(P[:, 0]).astype(int), 0, W - 1)
        ys = np.clip(np.round(P[:, 1]).astype(int), 0, H - 1)
        ok = ink.mask[ys, xs]
        if ok.sum() >= 3:
            p.width = float(np.median(wid[ys[ok], xs[ok]]))


def _join_curves(prims: list, pen: float, h: float, mask: np.ndarray | None = None, keep_ids: bool = False) -> list:
    """Pieces of one smooth curve are one curve again: arcs, curves and the straight stretches between them, of the
    same ink, that meet end to end and run on in the same direction, and together fit one shape (a circle cut where
    the lines of a triangle touch it in a scan, a curve drawn in two strokes, a loop cut where a letter touches it).
    Pieces that meet at an angle stay apart, and so do pieces meeting where a third line or a dot meets them (a
    vertex). The ends must meet, or the gap between them must be ink (a junction the thinning cut out); the gaps of a
    dashed curve are paper. With `keep_ids` the pieces stay in the list, marked "gone", and the joined shapes are
    added at its end."""
    cand = [p for p in prims if p.kind in ("arc", "curve", "line") and not p.closed and p.role == "draw" and p.p0 is not None]
    if len(cand) < 2 or not any(p.kind != "line" for p in cand):
        return prims
    others = [p for p in prims if p.role == "draw" and (p.kind == "dot" or (p.kind in ("line", "arc", "curve") and not p.closed))]
    near = max(1.5 * pen, 0.15 * h)
    bridge = max(3.0 * pen, 0.6 * h)

    def end(p, e):
        return p.p0 if e == 0 else p.p1

    def inked(A, B) -> bool:
        """Whether ink joins A and B close by (a junction), not only far round (or not at all: a dash's gap)."""
        if mask is None:
            return False
        H, W = mask.shape
        m = max(1.5 * pen, 0.3 * float(np.hypot(*(B - A))))
        x0, y0 = int(max(0, min(A[0], B[0]) - m)), int(max(0, min(A[1], B[1]) - m))
        x1, y1 = int(min(W, max(A[0], B[0]) + m + 1)), int(min(H, max(A[1], B[1]) + m + 1))
        if x1 <= x0 or y1 <= y0:
            return False
        # a break of a pixel or two in a light line next to darker ink is the scan's, not the author's
        lab, _ = fi.label(fi.dilate(mask[y0:y1, x0:x1], max(1, int(round(0.3 * pen)))))
        la = lab[min(y1 - y0 - 1, max(0, int(round(A[1])) - y0)), min(x1 - x0 - 1, max(0, int(round(A[0])) - x0))]
        lb = lab[min(y1 - y0 - 1, max(0, int(round(B[1])) - y0)), min(x1 - x0 - 1, max(0, int(round(B[0])) - x0))]
        return bool(la) and la == lb

    def vertex(a, b, A, B) -> bool:
        """Whether something else of the drawing meets a and b where they meet."""
        M = (A + B) / 2
        for q in others:
            if q is a or q is b or q.role != "draw":
                continue
            if q.kind == "dot":
                if np.hypot(*(q.center - M)) <= near + q.r:
                    return True
            elif any(np.hypot(*(e - M)) <= near for e in (q.p0, q.p1)):
                return True
        return False

    def link(a, ea, b, eb) -> bool:
        A, B = end(a, ea), end(b, eb)
        d = float(np.hypot(*(A - B)))
        if d > bridge or not _color_close(a.color, b.color):
            return False
        if _turn(a.direction_at(ea), -b.direction_at(eb)) > 25 * DEG:
            return False
        if a.kind == "line" and b.kind == "line":
            return False                                      # two straight pieces are joined as lines, if at all
        return (d <= near or inked(A, B)) and not vertex(a, b, A, B)
    used: set[int] = set()
    new: list[Prim] = []
    for a in cand:
        if a.id in used:
            continue
        used.add(a.id)
        chain = [(a, False)]                                  # (piece, traversed backwards)
        for side in (1, 0):
            cur, ce = a, side
            while True:
                nxt = None
                for b in cand:
                    if b.id in used:
                        continue
                    for eb in (0, 1):
                        if link(cur, ce, b, eb):
                            nxt = (b, eb)
                            break
                    if nxt:
                        break
                if nxt is None:
                    break
                b, eb = nxt
                used.add(b.id)
                if side == 1:
                    chain.append((b, eb == 1))
                else:
                    chain.insert(0, (b, eb == 0))
                cur, ce = b, 1 - eb
        if len(chain) < 2 or all(p.kind == "line" for p, _ in chain):
            for p, _ in chain[1:]:
                used.discard(p.id)
            continue
        pts = np.vstack([(p.pts[::-1] if rev else p.pts) for p, rev in chain])
        pts = resample(pts, 1.0)
        first, firstrev = chain[0]
        last, lastrev = chain[-1]
        closed = link(first, 1 if firstrev else 0, last, 0 if lastrev else 1)
        if closed and len(pts) > 2 and np.hypot(*(pts[0] - pts[-1])) < near:
            pts = pts[:-1]
        f = _fit(pts, pen, closed)
        # the shape found must run along every piece (a long line takes a short arc within its tolerance, but the
        # arc is not straight)
        if any(_shape_dist(f, q.pts).max() > max(0.9 * pen, 0.06 * q.length) for q, _ in chain if len(q.pts) >= 2):
            for q, _ in chain[1:]:
                used.discard(q.id)
            continue
        if f["kind"] in ("circle", "ellipse"):
            closed = True                                     # its ends nearly meet, as a hand-drawn circle's do
        elif closed or f["kind"] not in ("arc", "curve", "line"):
            continue                                          # a closed curve of its own shape: keep the pieces
        pr = Prim(-1, f["kind"], pts, first.stroke, first.color, first.width, closed=closed)
        for k in ("p0", "p1", "center", "r", "rx", "ry", "angle", "bez"):
            if k in f:
                setattr(pr, k, f[k])
        new.append(pr)
        for p, _ in chain:
            p.role = "gone"
    if not new:
        return prims
    if keep_ids:
        for k, p in enumerate(new):
            p.id = len(prims) + k
        return prims + new
    out = [p for p in prims if p.role != "gone"] + new
    for i, p in enumerate(out):
        p.id = i
    return out


def _shape_dist(f: dict, P: np.ndarray) -> np.ndarray:
    """Distance of each point of P from a fitted shape (the dictionary _fit returns)."""
    P = np.asarray(P, float)
    k = f["kind"]
    if k == "line":
        d = _unit(f["p1"] - f["p0"])
        w = P - f["p0"]
        return np.abs(w[:, 0] * d[1] - w[:, 1] * d[0])
    if k in ("arc", "circle"):
        return np.abs(np.hypot(*(P - f["center"]).T) - f["r"])
    if k == "dot":
        return np.hypot(*(P - f["center"]).T)
    if k in ("curve", "closed") and f.get("bez"):
        Q = np.vstack([bezier_points(sg, 24) for sg in f["bez"]])
        return np.array([float(np.hypot(*(Q - q).T).min()) for q in P])
    if k == "ellipse":
        c, rx, ry, a = f["center"], f["rx"], f["ry"], f["angle"]
        t = np.linspace(0, 2 * math.pi, 120)
        ca, sa = math.cos(a), math.sin(a)
        Q = np.column_stack([c[0] + rx * np.cos(t) * ca - ry * np.sin(t) * sa, c[1] + rx * np.cos(t) * sa + ry * np.sin(t) * ca])
        return np.array([float(np.hypot(*(Q - q).T).min()) for q in P])
    return np.zeros(len(P))


def _compact(sc: Scene, prims: list) -> None:
    """The scene's pieces without those marked "gone", numbered again, with every reference to them renumbered."""
    keep = [p for p in prims if p.role != "gone"]
    new = {p.id: i for i, p in enumerate(keep)}
    for i, p in enumerate(keep):
        p.id = i
    sc.prims = keep
    for lb in sc.labels:
        lb.prims = [new[i] for i in lb.prims if i in new]
    sc.arrows = [Arrow(prim=new[a.prim], end=a.end, barbs=[new[b] for b in a.barbs if b in new], head=a.head)
                 for a in sc.arrows if a.prim in new]
    sc.bars = [(new[i], e) for i, e in sc.bars if i in new]
    for row in sc.rows:
        row["prims"] = [new[i] for i in row["prims"] if i in new]
    for pn in sc.panels:
        pn.prims = [new[i] for i in pn.prims if i in new]


def _rejoin(sc: Scene, pen: float, h: float) -> bool:
    """Once the writing is set apart, pieces of the drawing that it cut apart where it touched them (a letter written
    against a curve) are joined again. Returns whether anything changed."""
    n = len(sc.prims)
    prims = _join_curves(sc.prims, pen, h, sc.ink.mask, keep_ids=True)
    prims = _join_lines(prims, pen, h, sc.ink.mask, keep_ids=True)
    if len(prims) == n and all(p.role != "gone" for p in prims):
        return False
    _compact(sc, prims)
    return True


def _recover_circles(prims: list, pen: float, h: float, mask: np.ndarray | None) -> list:
    """Pieces of one circle are one circle again, however junctions cut it. In a scan every line that touches a
    circle (an edge at a node of a tree or a graph, an arrow into a state) is one piece of ink with it, and the
    centre line bends into each junction: the circle comes out as arcs, with short bent pieces between them. So
    every arc that turns far enough to show its circle (a quarter turn) proposes one; every piece whose measured
    centre line lies on that circle, away from its ends, belongs to it; and where no piece does, the circle must
    still be inked (a junction's blot, not the paper of an arc that is meant to be open). A circle found all round
    replaces its pieces; a short piece lying along it is the bend into a junction and goes too."""
    seeds = []
    for p in prims:
        if p.kind != "arc" or p.closed or p.role != "draw" or p.center is None or len(p.pts) < 8:
            continue
        if abs(_sweep(p.pts, p.center)) >= 80 * DEG and p.r >= 1.5 * pen:
            seeds.append(p)
    if not seeds:
        return prims
    H, W = mask.shape if mask is not None else (0, 0)
    inked = fi.dilate(mask, max(1, int(round(0.5 * pen)))) if mask is not None else None
    gone: set[int] = set()
    new: list[Prim] = []
    for a in sorted(seeds, key=lambda p: -p.length):
        if a.id in gone:
            continue
        c, r, _ = circle_fit(a.pts)
        if not (1.5 * pen <= r <= 0.75 * max(H, W, 1)):
            continue
        tol = max(0.9 * pen, 0.07 * r)
        members, pts = [], []
        for q in prims:
            if q.id in gone or q.role != "draw" or q.kind not in ("line", "arc", "curve") or q.closed or len(q.pts) < 2:
                continue
            if q is not a and not _color_close(q.color, a.color):
                continue
            P = q.pts
            dist = np.abs(np.hypot(*(P - c).T) - r)
            cl = _cum(P)
            inner = (cl >= 1.2 * pen) & (cl <= cl[-1] - 1.2 * pen)
            if inner.sum() >= 3:
                ok = float((dist[inner] <= tol).mean()) >= 0.85
            else:
                ok = bool((dist <= 1.3 * tol).all())
            if ok:
                members.append(q)
                pts.append(P[inner] if inner.sum() >= 3 else P)
        Q = np.vstack(pts)
        ang = np.mod(np.arctan2(Q[:, 1] - c[1], Q[:, 0] - c[0]), 2 * math.pi)
        nb = 72
        hit = np.zeros(nb, bool)
        hit[np.minimum(nb - 1, (ang / (2 * math.pi) * nb).astype(int))] = True
        if hit.mean() < 0.5:
            continue
        # every stretch of the circle no piece covers must be ink
        ok = True
        if not hit.all():
            if inked is None:
                continue
            k0 = int(np.argmax(hit))
            order = [(k0 + i) % nb for i in range(nb)]
            run: list[int] = []
            for k in order + [k0]:
                if not hit[k]:
                    run.append(k)
                    continue
                if run:
                    t = np.linspace(run[0] / nb, (run[-1] + 1) / nb, 4 * len(run) + 2) * 2 * math.pi
                    xs = np.clip(np.round(c[0] + r * np.cos(t)).astype(int), 0, W - 1)
                    ys = np.clip(np.round(c[1] + r * np.sin(t)).astype(int), 0, H - 1)
                    if float(inked[ys, xs].mean()) < 0.8:
                        ok = False
                        break
                    run = []
        if not ok:
            continue
        cc, rr, err = circle_fit(Q)
        res = np.abs(np.hypot(*(Q - cc).T) - rr)
        if float(np.percentile(res, 90)) > tol:
            continue
        el = ellipse_fit(Q)
        t = np.linspace(0, 2 * math.pi, max(24, int(2 * math.pi * rr / 2)))
        if el is not None and min(el[1], el[2]) < 0.9 * max(el[1], el[2]) and el[4] < err:
            ce, rx, ry, angle, _ = el
            ca, sa = math.cos(angle), math.sin(angle)
            ring = np.column_stack([ce[0] + rx * np.cos(t) * ca - ry * np.sin(t) * sa, ce[1] + rx * np.cos(t) * sa + ry * np.sin(t) * ca])
            pr = Prim(-1, "ellipse", ring, a.stroke, a.color, a.width, center=ce, rx=rx, ry=ry, angle=angle, closed=True)
        else:
            ring = np.column_stack([cc[0] + rr * np.cos(t), cc[1] + rr * np.sin(t)])
            pr = Prim(-1, "circle", ring, a.stroke, a.color, a.width, center=cc, r=rr, closed=True)
        new.append(pr)
        gone.update(q.id for q in members)
        # short pieces lying along the circle: the bends into its junctions
        for q in prims:
            if q.id in gone or q.role != "draw" or q.kind not in ("line", "arc", "curve") or q.closed:
                continue
            if q.length <= max(3.0 * pen, 0.5 * h) and len(q.pts) and \
                    float(np.abs(np.hypot(*(q.pts - cc).T) - rr).max()) <= 1.5 * tol:
                gone.add(q.id)
    if not new:
        return prims
    out = [p for p in prims if p.id not in gone] + new
    for i, p in enumerate(out):
        p.id = i
    return out


def _filled_dots(prims: list, mask: np.ndarray, pen: float) -> None:
    """A small closed shape (a circle, an ellipse, a closed curve no wider than a few pens) whose inside is ink is a
    dot: a blot thins to a small loop around it. An open circle, a small ring, has paper inside."""
    H, W = mask.shape
    for p in prims:
        if p.kind not in ("circle", "ellipse", "closed") or p.role == "gone" or len(p.pts) < 3:
            continue
        P = p.pts
        c = P.mean(axis=0)
        R = float(np.hypot(*(P - c).T).max())
        if R > 3.0 * pen:
            continue
        inner = c + (P - c) * 0.5                          # halfway in from the line towards the centre
        Q = np.vstack([inner, c[None, :]])
        xs = np.clip(np.round(Q[:, 0]).astype(int), 0, W - 1)
        ys = np.clip(np.round(Q[:, 1]).astype(int), 0, H - 1)
        if float(mask[ys, xs].mean()) >= 0.9:
            p.kind, p.center, p.r = "dot", c, R + 0.5 * p.width
            p.pts = c[None, :]
            p.closed = False


def _darkness(c) -> float:
    return 1.0 - (0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]) / 255.0


def _faint_fringes(prims: list, pen: float) -> None:
    """In a scan the faint edge of a line sometimes thins into a hair of its own beside it: a piece far thinner than
    the pen and far lighter than the ink. It is not a line the author drew."""
    body = [p for p in prims if p.kind not in ("dot", "fill") and len(p.pts) >= 2]
    if not body:
        return
    w = np.array([p.length for p in body])
    dk = np.array([_darkness(p.color) for p in body])
    main = float(np.average(dk, weights=np.maximum(w, 1e-6)))
    for p in body:
        if p.width <= 0.35 * pen and (_darkness(p.color) < 0.5 * main or p.length <= 3 * pen):
            p.role = "ignore"


_DEBUG_JOIN = False
_DEBUG_AT = None              # a point (region pixels): the arrow finder says what it decides at a line end there


def _join_lines(prims: list, pen: float, h: float, mask: np.ndarray | None, keep_ids: bool = False) -> list:
    """Pieces of one straight line are one line again: a line cut where another crosses it or where the thinning
    branched, or drawn in two strokes. They run in the same direction, one after the other on the same line, of the
    same colour and weight, and the gap between their ends (if any) is ink. Lines that only meet at an angle, or
    pieces of a dashed line (paper between them), stay apart."""
    lines = [p for p in prims if p.kind == "line" and p.role == "draw" and p.length >= max(1.5 * pen, 0.2 * h)]
    if len(lines) < 2:
        return prims
    H, W = mask.shape if mask is not None else (0, 0)
    gap_max = max(2.0 * pen, 0.3 * h)

    def inked(A, B) -> bool:
        L = float(np.hypot(*(B - A)))
        if L <= 1.0 * pen:
            return True
        if mask is None:
            return False
        t = np.linspace(0, 1, max(3, int(L)))[:, None]
        P = A + (B - A) * t
        xs = np.clip(np.round(P[:, 0]).astype(int), 0, W - 1)
        ys = np.clip(np.round(P[:, 1]).astype(int), 0, H - 1)
        return float(fi.dilate(mask[max(0, int(min(A[1], B[1]) - 2)):int(max(A[1], B[1]) + 3),
                                    max(0, int(min(A[0], B[0]) - 2)):int(max(A[0], B[0]) + 3)], 1)[
            ys - max(0, int(min(A[1], B[1]) - 2)), xs - max(0, int(min(A[0], B[0]) - 2))].mean()) >= 0.9

    def inner(p: Prim) -> np.ndarray:
        """The measured centre line without its ends, where it bends into a junction or a head."""
        cl = _cum(p.pts)
        cut = 2.0 * pen if cl[-1] >= 6 * pen else 1.2 * pen
        k = (cl >= cut) & (cl <= cl[-1] - cut)
        return p.pts[k] if k.sum() >= 3 else p.pts

    def direction(p: Prim) -> np.ndarray:
        Q = inner(p)
        if len(Q) < 3:
            return _unit(p.p1 - p.p0)
        d = line_fit(Q)[1]
        return d if np.dot(d, p.p1 - p.p0) >= 0 else -d

    def joinable(a: Prim, b: Prim) -> bool:
        if not _color_close(a.color, b.color) or max(a.width, b.width) > 1.6 * max(1e-6, min(a.width, b.width)):
            return False
        da, db = direction(a), direction(b)
        if abs(float(np.dot(da, db))) < math.cos(12 * DEG):
            return False
        # both on one straight line: the union of their centre lines is straight, along the direction of each
        P = np.vstack([inner(a), inner(b)])
        _, dd, dmax, _ = line_fit(P)
        if min(abs(float(np.dot(da, dd))), abs(float(np.dot(db, dd)))) < math.cos(10 * DEG):
            return False
        span = float(np.ptp(P @ dd))
        if dmax > max(0.8 * pen, 0.012 * span):
            return False
        da = dd
        # one after the other: along the common direction their spans touch, overlap a little, or leave an inked gap
        ta = sorted([float(np.dot(a.p0, da)), float(np.dot(a.p1, da))])
        tb = sorted([float(np.dot(b.p0, da)), float(np.dot(b.p1, da))])
        if ta[0] > tb[0]:
            ta, tb = tb, ta
            first, second = (b, a)
        else:
            first, second = (a, b)
        gap = tb[0] - ta[1]
        if gap > gap_max:
            return False
        if gap < -0.5 * min(a.length, b.length):
            return False                                      # one lies along the other: a line drawn twice
        ends_f = max((first.p0, first.p1), key=lambda q: float(np.dot(q, da)))
        ends_s = min((second.p0, second.p1), key=lambda q: float(np.dot(q, da)))
        return gap <= 0 or inked(ends_f, ends_s)
    changed = True
    alive = {p.id for p in lines}
    while changed:
        changed = False
        for a in lines:
            if a.id not in alive:
                continue
            for b in lines:
                if b.id == a.id or b.id not in alive or not joinable(a, b):
                    continue
                if _DEBUG_JOIN:
                    print("JOIN", a.id, a.p0.round(), a.p1.round(), round(a.length), "+", b.id, b.p0.round(), b.p1.round(), round(b.length))
                P = np.vstack([a.pts, b.pts])
                c, d, dmax, _ = line_fit(P)
                t = (P - c) @ d
                ref = a.p1 - a.p0
                p0, p1 = c + d * t.min(), c + d * t.max()
                if np.dot(p1 - p0, ref) < 0:
                    p0, p1 = p1, p0
                order = np.argsort((P - p0) @ _unit(p1 - p0))
                a.pts, a.p0, a.p1 = P[order], p0, p1
                a.width = (a.width * a.length + b.width * b.length) / max(1e-6, a.length + b.length)
                b.role = "gone"
                alive.discard(b.id)
                changed = True
    if keep_ids or all(p.role != "gone" for p in prims):
        return prims
    out = [p for p in prims if p.role != "gone"]
    for i, p in enumerate(out):
        p.id = i
    return out


def _pen_blobs(prims: list, pen: float) -> None:
    """A dot no wider than the pen, at the end of a line or curve of the same colour, is where the pen touched down
    or lifted: part of that line, not a mark of its own."""
    ends = [(p, e) for p in prims if p.kind in ("line", "arc", "curve") and not p.closed for e in (p.p0, p.p1)]
    bodies = [p for p in prims if p.kind in ("line", "arc", "curve", "circle")]
    for d in prims:
        if d.kind != "dot" or d.role != "draw" or d.r > 0.85 * pen:
            continue
        lim = max(1.5 * pen, 2 * d.r) + 0.5 * d.r              # (lines start at its edge, not its centre)
        if d.r <= 0.5 * pen and any(np.hypot(*(e - d.center)) <= lim for p, e in ends if p.stroke != d.stroke):
            d.role = "ignore"                                   # no wider than the lines that end there: a tap
            continue
        near = [p for p, e in ends if p.stroke != d.stroke and np.hypot(*(e - d.center)) <= lim]
        if len(near) != 1 or not _color_close(near[0].color, d.color):
            continue                                       # at a junction of lines it marks a vertex
        if any(q is not near[0] and _seg_dist(d.center[None], _polyline(q)) <= lim for q in bodies):
            continue
        d.role = "ignore"


def _color_close(a, b) -> bool:
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b)) <= 60


def _merge_collinear(prims: list, first: int, pen: float, closed_poly: bool) -> None:
    """Consecutive straight pieces of one line that turned out to be one straight line are joined again: they run
    on in nearly the same direction (a split at a tremor of the hand), not at an angle (a corner, or the stroke of
    an arrowhead drawn in one go with its line, however short it is next to the line)."""
    i = first
    while i + 1 < len(prims):
        a, b = prims[i], prims[i + 1]
        if a.role == "gone":
            i += 1
            continue
        if a.kind == b.kind == "line" and a.stroke == b.stroke and _turn(a.p1 - a.p0, b.p1 - b.p0) <= 12 * DEG:
            P = np.vstack([a.pts, b.pts[1:]])
            c, d, dmax, _ = line_fit(P)
            L = _arclen(P)
            if dmax <= max(0.75 * pen, 0.04 * L):
                t = (P - c) @ d
                p0, p1 = c + d * t.min(), c + d * t.max()
                if np.dot(p1 - p0, P[-1] - P[0]) < 0:
                    p0, p1 = p1, p0
                a.pts, a.p0, a.p1 = P, p0, p1
                b.role = "gone"
                prims[i + 1] = a
                prims.pop(i + 1)
                continue
        i += 1


# ===================================================================== what each stroke is

def _seg_dist(P: np.ndarray, Q: np.ndarray) -> float:
    """Smallest distance between the points of polyline P and the segments of polyline Q."""
    if len(Q) == 1:
        return float(np.hypot(*(P - Q[0]).T).min())
    A, B = Q[:-1], Q[1:]
    AB = B - A
    L2 = (AB ** 2).sum(axis=1)
    L2 = np.where(L2 < 1e-12, 1e-12, L2)
    best = np.inf
    for p in P:
        t = np.clip(((p - A) * AB).sum(axis=1) / L2, 0, 1)
        proj = A + AB * t[:, None]
        best = min(best, float(np.hypot(*(proj - p).T).min()))
    return best


def _polyline(p: Prim) -> np.ndarray:
    if p.kind == "line":
        return np.vstack([p.p0, p.p1])
    if p.kind == "circle":
        t = np.linspace(0, 2 * math.pi, 48)
        return np.column_stack([p.center[0] + p.r * np.cos(t), p.center[1] + p.r * np.sin(t)])
    if p.kind == "dot":
        return p.center[None, :]
    return p.pts if len(p.pts) <= 120 else p.pts[:: max(1, len(p.pts) // 120)]


def _segments_cross(a0, a1, Q: np.ndarray) -> bool:
    """Whether segment a0-a1 crosses polyline Q."""
    if len(Q) < 2:
        return False
    d = a1 - a0
    B0, B1 = Q[:-1], Q[1:]
    e = B1 - B0
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    ok = np.abs(den) > 1e-9
    w = B0 - a0
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
        u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
    return bool(np.any(ok & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)))


def _piece_dens(p: Prim, h: float, pen: float) -> float:
    """Turning per letter height of length: about h/r for an arc of radius r, 0 for a line, large for writing."""
    if p.kind == "line":
        return 0.0
    if p.kind == "arc":
        return h / max(p.r, 1e-6)
    return _turning(p.pts, pen) / max(p.length / h, 1e-6)


def _stroke_kinds(sc: Scene, info: dict, pen: float, h: float) -> None:
    """Mark each stroke as part of the drawing, a dot, or 'small' (perhaps a letter). Handwriting is made of short
    pieces between sharp turns; a drawing has long straight or gently curved pieces. A stroke is drawing when most of
    its ink is in such long pieces, when it holds a long straight line (an arrow drawn with its head in one go), or
    when it is a large closed shape."""
    by_stroke: dict[int, list] = {}
    for p in sc.prims:
        by_stroke.setdefault(p.stroke, []).append(p)
    sc.kind = {}
    for sid, ps in by_stroke.items():
        if all(p.kind == "dot" for p in ps):
            sc.kind[sid] = "dot"
            continue
        total = sum(p.length for p in ps if p.kind != "dot") or 1.0
        long_len = 0.0
        hard = False
        for p in ps:
            if p.kind == "line" and p.length >= 1.8 * h:
                hard = hard or p.length >= 0.4 * total      # (a long joining stroke inside cursive writing is not)
                long_len += p.length
            elif p.kind in ("circle", "ellipse", "closed", "fill") and _size(p) >= 1.3 * h:
                hard = True
                long_len += p.length
            elif p.kind in ("arc", "curve") and p.length >= 2.2 * h and _piece_dens(p, h, pen) < 1.3:
                long_len += p.length
            elif p.kind == "line" and p.length >= 1.4 * h:
                long_len += 0.6 * p.length
        sc.kind[sid] = "draw" if hard or long_len >= 0.55 * total else "small"
    # strokes made of one or two simple pieces (straight, gently curved, a dot) that touch one another end to end
    # make up a drawing, when together they are larger than a letter: handwriting is not built of touching
    # straight pieces (the letters of a word are apart, or are one stroke)
    _simple_clusters(sc, by_stroke, pen, h)
    # a short, hardly turning stroke across a line of the drawing, or ending on it, is part of the drawing (a tick, a
    # short branch): handwriting does not run into the lines of a drawing
    long_draw = [p for p in sc.prims if sc.kind.get(p.stroke) == "draw" and (p.length >= 1.1 * h or p.kind in ("circle", "ellipse"))]
    reach = max(1.2 * pen, 0.18 * h)
    for sid, ps in by_stroke.items():
        if sc.kind.get(sid) != "small" or len(ps) > 2 or any(p.kind not in ("line", "arc", "curve") for p in ps):
            continue
        if any(_piece_dens(p, h, pen) >= 1.6 for p in ps if p.kind != "line"):
            continue
        a0, a1 = ps[0].p0, ps[-1].p1
        if not (0.2 * h <= np.hypot(*(a1 - a0)) <= 2.2 * h):
            continue
        for q in long_draw:
            if q.stroke == sid:
                continue
            Q = _polyline(q)
            if _segments_cross(a0, a1, Q) or min(_seg_dist(a0[None], Q), _seg_dist(a1[None], Q)) <= reach:
                sc.kind[sid] = "draw"
                break


def _simple_clusters(sc: Scene, by_stroke: dict, pen: float, h: float) -> None:
    def simple(ps) -> bool:
        body = [p for p in ps if p.kind != "dot"]
        if len(body) > 2:
            return False
        for p in body:
            if p.kind == "arc" and p.r < 0.15 * h:
                return False
            if p.kind == "curve" and _piece_dens(p, h, pen) >= 2.5:
                return False
            if p.kind in ("closed", "ellipse") or (p.kind == "circle" and p.r < 0.2 * h):
                return False
        return True
    cand = [sid for sid, ps in by_stroke.items() if sc.kind.get(sid) == "draw" or simple(ps)]
    boxes = {s.id: s.bbox for s in sc.ink.strokes}
    polys = {sid: [(_polyline(p), p.r if p.kind == "dot" else 0.0) for p in by_stroke[sid]] for sid in cand}
    touch = max(1.6 * pen, 0.15 * h)
    parent = {sid: sid for sid in cand}

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(cand):
        ax0, ay0, ax1, ay1 = boxes[a]
        for b in cand[i + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            if bx0 - touch > ax1 or ax0 - touch > bx1 or by0 - touch > ay1 or ay0 - touch > by1:
                continue
            if min(_seg_dist(P, Q) - rp - rq for P, rp in polys[a] for Q, rq in polys[b]) <= touch:
                ra, rb = f(a), f(b)
                if ra != rb:
                    parent[rb] = ra
    groups: dict = {}
    for sid in cand:
        groups.setdefault(f(sid), []).append(sid)
    for g in groups.values():
        if len(g) < 2:
            continue
        if any(sc.kind.get(sid) == "draw" for sid in g):
            hit = True
        else:
            x0 = min(boxes[s][0] for s in g)
            y0 = min(boxes[s][1] for s in g)
            x1 = max(boxes[s][2] for s in g)
            y1 = max(boxes[s][3] for s in g)
            # larger than a letter, and taller than a line of handwriting (a word, even with its ascenders and
            # descenders, is not); a low row of touching pieces is left for the reading to decide
            hit = len(g) >= 3 and y1 - y0 >= 2.2 * h
        if hit:
            for sid in g:
                sc.kind[sid] = "draw"


def _size(p: Prim) -> float:
    if p.kind == "circle":
        return 2 * p.r
    if p.kind == "ellipse":
        return 2 * max(p.rx, p.ry)
    P = p.pts
    return float(np.hypot(*(P.max(axis=0) - P.min(axis=0)))) if len(P) else 0.0


# ===================================================================== arrows

def _find_arrows(sc: Scene, pen: float, h: float) -> None:
    """Arrowheads: short straight pieces that meet at a point (the tip) at the free end of a longer line or curve,
    or a little beyond it on its line, and run back from the tip on both sides (a V); a third side across them
    closes an open triangle (then the line stops at the triangle's base). The tip must be free (no other line of the
    drawing passes there) and the pieces short against the line, so that the corner of a box or the top of a tree
    is not taken for an arrow. A short piece across the start of a line makes a 'maps to' arrow."""
    shafts = [p for p in sc.prims if p.kind in ("line", "arc", "curve") and not p.closed
              and ((p.length >= 1.1 * h and sc.kind.get(p.stroke) == "draw") or (p.kind == "line" and p.length >= 1.5 * h))]
    shorts = [p for p in sc.prims if 0.12 * h <= p.length <= 1.6 * h and (p.kind == "line" or (
        p.kind in ("arc", "curve") and not p.closed and np.hypot(*(p.p1 - p.p0)) >= 0.9 * p.length))]
    others = [p for p in sc.prims if p.kind != "dot" and sc.kind.get(p.stroke) == "draw"]
    reach = max(1.6 * pen, 0.28 * h)
    polys = {p.id: _polyline(p) for p in sc.prims}
    owner = None if sc.ink.vector else _owner_image(sc, sc.ink.shape, 1.1 * h, pen + 2)

    def free(pt, skip: set) -> bool:
        for q in others:
            if q.id in skip:
                continue
            if _seg_dist(pt[None], polys[q.id]) <= reach * 0.8:
                return False
        return True
    used: set[int] = set()
    found = []
    for s in sorted(shafts, key=lambda p: -p.length):
        for end in (0, 1):
            if any(a.prim == s.id and a.end == end for a in found):
                continue
            E = s.p0 if end == 0 else s.p1
            dbg = _DEBUG_AT is not None and float(np.hypot(*(E - np.asarray(_DEBUG_AT)))) <= 12
            u = s.direction_at(end)                           # out of the shaft at this end
            back = -u
            cands = []                                        # (barb, near end = tip side, far end, more pieces)
            for b in shorts:
                if b.id == s.id or b.id in used or b.role != "draw" or b.length > 0.5 * s.length:
                    continue
                for (Bn, Bf) in ((b.p0, b.p1), (b.p1, b.p0)):
                    w = Bn - E
                    ahead = float(np.dot(w, u))
                    off = abs(float(u[0] * w[1] - u[1] * w[0]))
                    # the strokes of a head start at the tip, or (thinned out of a blot of ink) on the line a little
                    # behind it
                    if not (-max(reach, 0.6 * h) <= ahead <= 1.7 * h and off <= reach):
                        continue
                    more = []
                    v0 = Bf - Bn
                    own = math.acos(max(-1.0, min(1.0, float(np.dot(v0, back)) / max(1e-9, float(np.hypot(*v0))))))
                    if not 12 * DEG <= own <= 80 * DEG:
                        continue
                    for c2 in shorts:                         # a stroke of the head drawn with a bend: two pieces
                        if own < 18 * DEG:
                            break                             # (not a piece running on along the line: its tip)
                        if c2.id in (b.id, s.id) or c2.id in used or c2.role != "draw":
                            continue
                        for (Cn, Cf) in ((c2.p0, c2.p1), (c2.p1, c2.p0)):
                            if np.hypot(*(Cn - Bf)) <= max(0.6 * pen, 2.0) and _turn(Bf - Bn, Cf - Cn) <= 50 * DEG \
                                    and np.hypot(*(Cf - Bn)) <= 1.6 * h:
                                more, Bf = [c2.id], Cf
                                break
                        if more:
                            break
                    v = Bf - Bn
                    nv = np.hypot(*v)
                    if nv < 0.12 * h:
                        continue
                    ang = math.acos(max(-1.0, min(1.0, float(np.dot(v / nv, back)))))
                    if 12 * DEG <= ang <= 80 * DEG:
                        cands.append((b, Bn, Bf, more))
                        break
            if dbg:
                print("ARROW at", E.round(), "shaft", s.id, "cands", [(c[0].id, c[1].round(), c[2].round(), c[3]) for c in cands])
            if not cands:
                continue
            # barbs that share a tip: the most sides, then the tip nearest the end of the line (a letter written just
            # beyond an arrow is not its head), then the longest strokes
            best = None
            for b0, n0, f0, _ in cands:
                grp = [(b, n, f, mo) for b, n, f, mo in cands if np.hypot(*(n - n0)) <= reach]
                tip = np.mean([n for _, n, _, _ in grp], axis=0)
                sides: dict = {}
                for b, n, f, mo in grp:
                    v = f - tip
                    side = float(np.sign(back[0] * v[1] - back[1] * v[0]))
                    if side and (side not in sides or np.hypot(*(f - n)) > np.hypot(*(sides[side][2] - sides[side][1]))):
                        sides[side] = (b, n, f, mo)
                score = (len(sides), -round(float(np.hypot(*(tip - E))) / reach), sum(np.hypot(*(x[2] - x[1])) for x in sides.values()))
                if best is None or score > best[0]:
                    best = (score, tip, list(sides.values()))
            _, tip, chosen = best
            if dbg:
                print("  chosen", [(c[0].id, c[2].round()) for c in chosen], "tip", tip.round())
            ids = {s.id} | {c[0].id for c in chosen} | {i for c in chosen for i in c[3]}
            merged = False
            if len(chosen) < 2:
                # one short piece at an end is a tick, not a head; unless the other stroke of the head is there,
                # merged with the line (in a scan, a stroke at a narrow angle to its line prints as one with it)
                if sc.ink.vector or s.length < 2.0 * h or not _merged_barb(sc, s, E, u, chosen[0][2] - chosen[0][1], pen, h, owner):
                    continue
                merged = True
            beyond = float(np.dot(tip - E, u))
            head = "open"
            base = None
            if len(chosen) == 2:
                f1, f2 = chosen[0][2], chosen[1][2]
                for b in shorts:
                    if b.id in used or b.id in ids or b.role != "draw":
                        continue
                    if (np.hypot(*(b.p0 - f1)) < reach and np.hypot(*(b.p1 - f2)) < reach) or \
                       (np.hypot(*(b.p0 - f2)) < reach and np.hypot(*(b.p1 - f1)) < reach):
                        base = b
                        head = "triangle"
                        break
            if base is not None:
                ids.add(base.id)
            elif beyond > max(2.5 * reach, 0.6 * h):
                continue                                      # a V floating well beyond the end of a line is not its head
            if not all(free(c[2], ids) for c in chosen) and not _clear_v(chosen, back, h):
                if dbg:
                    print("  far ends not free")
                continue
            if not free(tip, ids) and not (merged or _clear_v(chosen, back, h)):
                if dbg:
                    print("  tip not free and not a clear V")
                continue                  # at a tip on another line only a clear V is a head (an arrow into a box)
            for b in [c[0] for c in chosen] + [sc.prims[i] for c in chosen for i in c[3]] + ([base] if base is not None else []):
                b.role = "barb"
                used.add(b.id)
            if beyond > reach:                                # the line stops at the base: it runs on to the tip
                if end == 0:
                    s.p0 = tip.copy()
                else:
                    s.p1 = tip.copy()
            elif not sc.ink.vector:                           # a scan's line stops where the head branches off
                T = _ink_tip(sc, s, E, u, h, owner)
                if 0 < float(np.dot(T - E, u)) <= 0.5 * h:
                    if end == 0:
                        s.p0 = T.copy()
                    else:
                        s.p1 = T.copy()
            found.append(Arrow(prim=s.id, end=end, barbs=[c[0].id for c in chosen] + [i for c in chosen for i in c[3]]
                               + ([base.id] if base is not None else []), head=head))
            # a small blot where the strokes of the head close on the tip is part of the head, not a dot
            at = s.p0 if end == 0 else s.p1
            for d in sc.prims:
                if d.kind == "dot" and d.role == "draw" and d.r <= 1.6 * pen and \
                        min(np.hypot(*(d.center - at)), np.hypot(*(d.center - tip))) <= max(1.5 * pen, 0.3 * h):
                    d.role = "barb"
    if not sc.ink.vector:
        _blurred_heads(sc, shafts, found, pen, h)
    sc.arrows = found
    # bars across the start of an arrow (maps to)
    for a in found:
        s = sc.prims[a.prim]
        start = 1 - a.end
        if any(x.prim == s.id and x.end == start for x in found):
            continue
        E = s.p0 if start == 0 else s.p1
        dirn = s.direction_at(start)
        for b in shorts:
            if b.role != "draw" or b.id == s.id or b.length > 1.4 * h:
                continue
            mid = (b.p0 + b.p1) / 2
            bd = _unit(b.p1 - b.p0)
            if abs(float(np.dot(bd, dirn))) < math.cos(65 * DEG) and np.hypot(*(mid - E)) < max(1.5 * pen, 0.3 * b.length + pen):
                b.role = "bar"
                sc.bars.append((s.id, start))
                break


def _clear_v(chosen: list, back, h: float) -> bool:
    """Two short strokes, one on each side of the line, running back from the tip at about the same angle and of
    about the same length: an arrowhead, even where its tip touches another line (an arrow drawn into a box)."""
    if len(chosen) != 2:
        return False
    angs, lens = [], []
    for b, n, f, *_ in chosen:
        v = f - n
        angs.append(_turn(v, back))
        lens.append(float(np.hypot(*v)))
    return (all(12 * DEG <= a <= 65 * DEG for a in angs) and abs(angs[0] - angs[1]) <= 20 * DEG
            and max(lens) <= 1.2 * h and max(lens) <= 2.0 * min(lens))


def _ink_tip(sc: Scene, s: Prim, E, u, h: float, owner) -> np.ndarray:
    """Where the ink ends beyond the end E of line s, going on in its direction u. In a scan the thinned line stops
    where the strokes of its arrowhead branch off, a little short of the tip."""
    ink = sc.ink
    H, W = ink.shape
    T = np.asarray(E, float)
    for k in range(1, int(0.6 * h)):
        q = E + u * (0.5 * k)
        x, y = int(round(q[0])), int(round(q[1]))
        if not (0 <= x < W and 0 <= y < H) or not ink.mask[y, x]:
            break
        if owner is not None and owner[y, x] not in (0, s.id + 1):
            break
        T = q
    return T


def _merged_barb(sc: Scene, s: Prim, E, u, v, pen: float, h: float, owner) -> bool:
    """Whether, opposite the one stroke of a head found at the end E of line s (u points out of the line, v along
    that stroke from the tip), the ink of the line is widened like a second stroke run into it: wider than the line
    over a stretch just behind the tip, and of the line's own width again further back."""
    ink = sc.ink
    H, W = ink.shape
    n = np.array([-u[1], u[0]])
    side = -1.0 if float(np.dot(n, v)) > 0 else 1.0         # the side away from the stroke that was found
    out = side * n
    E = _ink_tip(sc, s, E, u, h, owner)
    ks = np.arange(-int(np.ceil(0.8 * pen)), int(1.2 * h))

    def extent(t: float) -> float:
        """How far the ink reaches out on that side, from the line: the run of ink nearest the line (a hand-drawn
        line is not exactly where its fitted line is)."""
        P = (E - u * t) + np.outer(ks, out)
        xs, ys = np.round(P[:, 0]).astype(int), np.round(P[:, 1]).astype(int)
        ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
        on = np.zeros(len(ks), bool)
        on[ok] = ink.mask[ys[ok], xs[ok]]
        if owner is not None:
            on[ok] &= np.isin(owner[ys[ok], xs[ok]], (0, s.id + 1))
        near = [i for i in range(len(ks)) if on[i] and abs(ks[i]) <= 0.6 * pen]
        if not near:
            return 0.0
        j = min(near, key=lambda i: abs(ks[i]))
        while j + 1 < len(ks) and on[j + 1]:
            j += 1
        return float(ks[j] + 1)
    near = [extent(t) for t in np.arange(0.1 * h, 0.75 * h, 1.0)]
    far = [extent(t) for t in np.arange(0.9 * h, 1.3 * h, 1.0)] if s.length >= 1.4 * h else [pen / 2]
    base = float(np.median(far))                              # half the line's own width, measured further back
    run = best = 0
    for e in near:
        run = run + 1 if e >= base + max(2.0, 0.4 * pen) else 0
        best = max(best, run)
    return best >= max(0.12 * h, 1.0 * pen) and base <= 0.85 * pen


def _drop_hooks(sc: Scene, pen: float, h: float) -> None:
    """A very short piece at the end of a long line of the same stroke, turned sharply away from it, with nothing
    else there, is the hook the pen made as it landed or lifted: not part of the drawing. (Two short pieces at an
    end are an arrowhead or a bar across the line, and stay.)"""
    lim = max(2.2 * pen, 0.3 * h)
    near = max(1.2 * pen, 0.1 * h)
    by_stroke: dict[int, list] = {}
    for p in sc.prims:
        by_stroke.setdefault(p.stroke, []).append(p)
    for p in sc.prims:
        if p.role != "draw" or p.kind not in ("line", "arc", "curve") or p.closed or p.length > lim:
            continue
        for E, O in ((p.p0, p.p1), (p.p1, p.p0)):
            host = None
            for q in by_stroke[p.stroke]:
                if q is p or q.kind not in ("line", "arc", "curve") or q.closed or q.role not in ("draw", "dash"):
                    continue
                if q.length < max(3 * p.length, 1.2 * h):
                    continue
                for k, e in enumerate((q.p0, q.p1)):
                    if np.hypot(*(e - E)) <= near:
                        host = (q, k)
            if host is None:
                continue
            q, k = host
            if _turn(q.direction_at(k), O - E) < 45 * DEG:
                continue                                  # it runs on along the line
            busy = False
            for r in sc.prims:
                if r is p or r is q or r.role == "ignore":
                    continue
                if r.kind == "dot":
                    busy = np.hypot(*(r.center - E)) <= near + r.r or np.hypot(*(r.center - O)) <= near + r.r
                else:
                    busy = _seg_dist(np.vstack([E, O]), _polyline(r)) <= near
                if busy:
                    break
            if not busy:
                p.role = "ignore"
                break


def _owner_image(sc: Scene, shape, min_len: float, width: float) -> np.ndarray:
    """For every pixel, which long line of the drawing covers it (its prim id + 1), 0 for none."""
    ps = [p for p in sc.prims if p.kind in ("line", "arc", "curve", "circle", "ellipse", "closed")
          and p.role in ("draw", "dash") and (p.length >= min_len or p.kind in ("circle", "ellipse"))]
    if not ps:
        return np.zeros(shape, np.int32)
    ps.sort(key=lambda p: p.length)
    return raster_polylines(shape, [_polyline(p) for p in ps], width, values=[p.id + 1 for p in ps])


def _blurred_heads(sc: Scene, shafts: list, found: list, pen: float, h: float) -> None:
    """In a scan a small arrowhead often prints as one blot. Across the line, the ink is about a pen wide at the end,
    widens to several pens a little behind it over a stretch, evenly on both sides, and narrows again: that profile
    (and not the round one of a dot) is an arrowhead. The ink of the other lines of the drawing is left out of the
    profile: a line crossing near the end (an axis through a tick, a circle through a corner) is not a head."""
    ink = sc.ink
    H, W = ink.shape
    owner = _owner_image(sc, (H, W), 1.1 * h, pen + 2)
    rings = [p for p in sc.prims if p.kind in ("circle", "ellipse") and p.role == "draw"]
    for s in shafts:
        for end in (0, 1):
            if any(a.prim == s.id and a.end == end for a in found):
                continue
            E = s.p0 if end == 0 else s.p1
            if any(np.hypot(*(E - q.center)) < (q.r or min(q.rx, q.ry)) - 1.5 * pen for q in rings):
                continue
            u = s.direction_at(end)
            n = np.array([-u[1], u[0]])
            prof, sym = [], []
            ts = np.arange(-0.4 * h, 1.4 * h, max(1.0, pen / 2))
            for t in ts:
                c = E - u * t
                ws = np.arange(-0.9 * h, 0.9 * h, 1.0)
                pts = c + np.outer(ws, n)
                xs = np.round(pts[:, 0]).astype(int)
                ys = np.round(pts[:, 1]).astype(int)
                ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
                on = np.zeros(len(ws), bool)
                o = owner[ys[ok], xs[ok]]
                on[ok] = ink.mask[ys[ok], xs[ok]] & ((o == 0) | (o == s.id + 1))
                # the run of ink through the centre line
                mid = len(ws) // 2
                if not on[mid]:
                    prof.append(0.0)
                    sym.append(1.0)
                    continue
                a = mid
                while a > 0 and on[a - 1]:
                    a -= 1
                b = mid
                while b < len(ws) - 1 and on[b + 1]:
                    b += 1
                prof.append(float(b - a + 1))
                sym.append(min(mid - a, b - mid) / max(1.0, max(mid - a, b - mid)))
            prof = np.array(prof)
            k = int(np.argmax(prof))
            wmax = prof[k]
            if wmax < 2.6 * pen or ts[k] < 0.15 * h or sym[k] < 0.35:
                continue
            tip = prof[ts <= 0.05 * h]
            after = prof[ts >= ts[k] + 0.45 * h]
            if not len(after) or after.max() > 0.6 * wmax or (len(tip) and tip.max() > 0.75 * wmax):
                continue
            # not a dot: a dot's widest point is its middle, with ink on both sides; a head is widest behind its tip
            before = prof[(ts > 0.05 * h) & (ts < ts[k])]
            if len(before) and before.min() < 0.5 * pen:
                continue
            # a head widens over a stretch, not at one crossing of the pen
            i0 = k
            while i0 > 0 and prof[i0 - 1] >= 1.8 * pen:
                i0 -= 1
            if ts[k] - ts[i0] < max(1.5 * pen, 0.12 * h):
                continue
            found.append(Arrow(prim=s.id, end=end, barbs=[], head="filled"))
            # the blot is the head: a dot found there is not a dot
            for d in sc.prims:
                if d.kind == "dot" and d.role == "draw" and np.hypot(*(d.center - (E - u * ts[k]))) <= max(2.5 * pen, 0.5 * h):
                    d.role = "barb"


# ===================================================================== labels

def _find_labels(sc: Scene, pen: float, h: float) -> None:
    """Group the small strokes into words: the label candidates. Dots and short drawing strokes that lie inside a
    word belong to it (the dot of an i, the tail of a y)."""
    ink = sc.ink
    strokes = {s.id: s for s in ink.strokes}
    roles: dict[int, set] = {}
    for p in sc.prims:
        roles.setdefault(p.stroke, set()).add(p.role)
    small = [s for s in ink.strokes if sc.kind.get(s.id) == "small" and not roles.get(s.id, set()) <= {"barb", "bar", "dash"}]
    boxes = {s.id: s.bbox for s in small}
    gx, gy = 0.42 * h, 0.3 * h
    ids = list(boxes)
    parent = {i: i for i in ids}

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a in range(len(ids)):
        ax0, ay0, ax1, ay1 = boxes[ids[a]]
        for b in range(a + 1, len(ids)):
            bx0, by0, bx1, by1 = boxes[ids[b]]
            if ax0 - gx <= bx1 and bx0 - gx <= ax1 and ay0 - gy <= by1 and by0 - gy <= ay1:
                ra, rb = f(ids[a]), f(ids[b])
                if ra != rb:
                    parent[rb] = ra
    groups: dict[int, list] = {}
    for i in ids:
        groups.setdefault(f(i), []).append(i)
    labels = []
    for g in groups.values():
        labels.append([min(boxes[i][0] for i in g), min(boxes[i][1] for i in g),
                       max(boxes[i][2] for i in g), max(boxes[i][3] for i in g), list(g), None])
    taken = {i for lab in labels for i in lab[4]}
    labels += _letters_on_lines(sc, pen, h)
    for lab in labels:
        for s in ink.strokes:
            if s.id in taken:
                continue
            kind = sc.kind.get(s.id)
            x0, y0, x1, y1, g, _ = lab
            bx0, by0, bx1, by1 = s.bbox
            if kind == "dot":
                inside = bx0 >= x0 - 0.25 * h and bx1 <= x1 + 0.25 * h and by0 >= y0 - 0.5 * h and by1 <= y1 + 0.2 * h
            elif kind == "draw":
                ps = [p for p in sc.prims if p.stroke == s.id]
                if any(p.role in ("barb", "bar") for p in ps) or any(p.kind == "line" and p.length >= 1.8 * h for p in ps):
                    continue
                if sum(p.length for p in ps) > 6.0 * h:
                    continue
                ex = (x0 - 0.6 * h, y0 - 0.8 * h, x1 + 0.6 * h, y1 + 1.3 * h)
                inter = max(0, min(bx1, ex[2]) - max(bx0, ex[0])) * max(0, min(by1, ex[3]) - max(by0, ex[1]))
                inside = inter >= 0.7 * max(1, (bx1 - bx0) * (by1 - by0)) and (by0 >= y0 - 0.9 * h)
            else:
                continue
            if inside:
                g.append(s.id)
                taken.add(s.id)
                lab[0], lab[1], lab[2], lab[3] = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
    # groups whose boxes now overlap are one label
    merged = True
    while merged:
        merged = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                A, B = labels[i], labels[j]
                pad = 0.1 * h
                if A[0] - pad <= B[2] and B[0] - pad <= A[2] and A[1] - pad <= B[3] and B[1] - pad <= A[3]:
                    own = None if A[5] is None and B[5] is None else (
                        (A[5] if A[5] is not None else _label_prims(sc, A[4])) + (B[5] if B[5] is not None else _label_prims(sc, B[4])))
                    labels[i] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3]),
                                 sorted(set(A[4] + B[4])), own]
                    labels.pop(j)
                    merged = True
                    break
            if merged:
                break
    out = []
    for lab in sorted(labels, key=lambda r: (r[1] // max(h, 1), r[0])):
        x0, y0, x1, y1, g, own = lab
        prs = sorted(set(own)) if own is not None else _label_prims(sc, g)
        if not prs:
            continue
        cols = [strokes[i].color for i in g]
        col = tuple(int(v) for v in np.median(np.array(cols), axis=0))
        L = Label(id=len(out) + 1, box=(int(x0), int(y0), int(x1), int(y1)), strokes=sorted(g), prims=prs, color=col)
        out.append(L)
        for i in prs:
            sc.prims[i].role = "label"
            sc.prims[i].group = L.id
    sc.labels = out


def _label_prims(sc: Scene, strokes: list) -> list:
    return [p.id for p in sc.prims if p.stroke in strokes and p.role == "draw"]


def _letters_on_lines(sc: Scene, pen: float, h: float) -> list:
    """Writing that touches the drawing. In a scan a label written against a line (the letter on a curved arrow, a
    name touching its node) is one piece of ink with the drawing, so it is not found as a stroke of its own. Within
    such a piece, a cluster of short pieces, together about the size of a letter, curly or of three strokes or more,
    is set apart as a label candidate; the model that reads the labels says whether it is writing or a small shape
    of the drawing (a small loop, a cross), which then goes back to the drawing. Returns label records."""
    if sc.ink.vector:
        return []
    by_stroke: dict[int, list] = {}
    for p in sc.prims:
        by_stroke.setdefault(p.stroke, []).append(p)
    near = max(1.5 * pen, 0.15 * h)
    out = []
    for sid, ps in by_stroke.items():
        if sc.kind.get(sid) != "draw":
            continue
        small = [p for p in ps if p.role == "draw" and p.kind in ("line", "arc", "curve", "dot", "closed")
                 and p.length <= 1.6 * h and _size(p) <= 1.0 * h]
        if len(small) < 2:
            continue
        polys = {p.id: _polyline(p) for p in small}
        parent = {p.id: p.id for p in small}

        def f(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i, a in enumerate(small):
            for b in small[i + 1:]:
                ra = a.r if a.kind == "dot" else 0.0
                rb = b.r if b.kind == "dot" else 0.0
                if _seg_dist(polys[a.id], polys[b.id]) - ra - rb <= near:
                    x, y = f(a.id), f(b.id)
                    if x != y:
                        parent[y] = x
        groups: dict[int, list] = {}
        for p in small:
            groups.setdefault(f(p.id), []).append(p)
        for g in groups.values():
            if len(g) < 2:
                continue
            P = np.vstack([polys[p.id] if p.kind != "dot" else np.vstack([p.center - p.r, p.center + p.r]) for p in g])
            x0, y0 = P.min(axis=0)
            x1, y1 = P.max(axis=0)
            if x1 - x0 > 1.4 * h or y1 - y0 > 1.4 * h or sum(p.length for p in g) < 0.5 * h:
                continue
            curly = any(p.kind in ("arc", "curve", "closed") and _piece_dens(p, h, pen) >= 1.2 for p in g)
            if len(g) >= 3 or curly:
                out.append([int(x0 - pen / 2), int(y0 - pen / 2), int(x1 + pen / 2) + 1, int(y1 + pen / 2) + 1, [sid], [p.id for p in g]])
    return out


# ===================================================================== dashed and dotted lines

def _find_rows(sc: Scene, pen: float, h: float, which: str = "both") -> None:
    """Rows of three or more dots, or of short dashes, evenly spaced along a straight line. Dashed lines are found
    before the writing is grouped (a dash is as short as a letter stroke, but a row of them is not writing); rows of
    dots after it (three dots in a row can be an ellipsis written in a label)."""
    def rows_of(items, pos, maxgap, along=lambda q, u: True, seed=lambda q: True):
        """Chains of three or more items, evenly spaced on a line; `along` says whether an item lies along the
        chain's direction u (a dash of a dashed line does); a chain starts at an item `seed` allows. A chain takes
        every item on its way: one that steps over an item lying between two of its own is not a row (it would
        take every other dash of a line, and run on into a letter beyond the line's end)."""
        rows = []
        pts = {p.id: pos(p) for p in items}
        used = set()

        def whole(chain, u) -> list:
            """The chain cut where a gap is much wider than its usual spacing (the pair it started from may have
            spanned the gap between the last dash and a letter); [] if it steps over an item."""
            P = [pts[q.id] for q in chain]
            g = [float(np.hypot(*(P[i + 1] - P[i]))) for i in range(len(P) - 1)]
            for i, gi in enumerate(g):
                for c in items:
                    if c.id in used or c in chain or not along(c, u):
                        continue
                    v = pts[c.id] - P[i]
                    s = P[i + 1] - P[i]
                    t = float(np.dot(v, s)) / (gi * gi)
                    if 0.2 < t < 0.8 and abs(float(s[0] * v[1] - s[1] * v[0])) / gi <= max(pen, 0.2 * gi):
                        return []
            med = float(np.median(g))
            pieces, piece = [], [chain[0]]
            for i, gi in enumerate(g):
                if gi > 1.6 * med:
                    pieces.append(piece)
                    piece = []
                piece.append(chain[i + 1])
            pieces.append(piece)
            return max(pieces, key=len)
        for a in items:
            if a.id in used or not seed(a):
                continue
            best = None
            for b in items:
                if b.id == a.id or b.id in used:
                    continue
                d = np.hypot(*(pts[b.id] - pts[a.id]))
                if d < 0.15 * h or d > maxgap(a, b):
                    continue
                u = (pts[b.id] - pts[a.id]) / d
                if not (along(a, u) and along(b, u)):
                    continue
                chain = [a, b]
                for sign in (1.0, -1.0):                      # on from b, then back from a
                    cur, step, w = (b, d, u) if sign > 0 else (a, d, -u)
                    while True:
                        nxt = None
                        for c in items:
                            if c.id in used or c in chain:
                                continue
                            v = pts[c.id] - pts[cur.id]
                            dv = np.hypot(*v)
                            if 0.55 * step <= dv <= 1.6 * step and abs(float(w[0] * v[1] - w[1] * v[0])) <= max(pen, 0.2 * dv) \
                                    and np.dot(w, v) > 0 and along(c, u) and (nxt is None or abs(dv - step) < nxt[1]):
                                nxt = (c, abs(dv - step))
                        if nxt is None:
                            break
                        if sign > 0:
                            chain.append(nxt[0])
                        else:
                            chain.insert(0, nxt[0])
                        cur = nxt[0]
                if len(chain) >= 3:
                    chain = whole(chain, u)
                if len(chain) >= 3 and (best is None or len(chain) > len(best)):
                    best = chain
            if best:
                rows.append(best)
                used |= {p.id for p in best}
        return rows
    if which in ("both", "dots"):
        dots = [p for p in sc.prims if p.kind == "dot" and p.role == "draw"]
        for row in rows_of(dots, lambda p: p.center, lambda a, b: 2.2 * h):
            for p in row:
                p.role = "dotrow"
                p.group = len(sc.rows)
            sc.rows.append({"kind": "dots", "prims": [p.id for p in row]})
    if which not in ("both", "dashes"):
        return
    # (a dash drawn short, or blurred in a scan, measures as a dot: dots take part in a row of dashes)
    dashes = [p for p in sc.prims if p.role == "draw" and ((p.kind == "line" and 0.2 * h <= p.length <= 1.8 * h)
                                                          or (p.kind == "dot" and p.r <= 0.4 * h))]
    by_stroke: dict[int, list] = {}
    for q in sc.prims:
        if q.role not in ("ignore", "gone"):
            by_stroke.setdefault(q.stroke, []).append(q)

    def mid(p):
        return (p.p0 + p.p1) / 2 if p.kind == "line" else p.center
    found = []
    for row in rows_of(dashes, mid, lambda a, b: a.length + b.length + 1.5 * h,
                       lambda q, u: q.kind == "dot" or abs(float(np.dot(_unit(q.p1 - q.p0), u))) > math.cos(20 * DEG),
                       lambda q: q.kind == "line"):
        if sum(1 for p in row if p.kind == "line") < 3:
            continue                                           # a row of dots is a dotted line, found later
        u = _unit(mid(row[-1]) - mid(row[0]))
        ends = np.array([q for p in row for q in ((p.p0, p.p1) if p.kind == "line" else (p.center,))])
        t = (ends - ends[0]) @ u
        a, b = ends[np.argmin(t)], ends[np.argmax(t)]
        mids = sorted(float(np.dot(mid(p) - a, u)) for p in row)
        period = float(np.median(np.diff(mids))) if len(mids) > 1 else h
        dash = float(np.median([p.length for p in row if p.kind == "line"]))
        L = float(np.dot(b - a, u))
        # a dash is drawn on its own: most dashes of a row are pieces of ink of their own (a dash touching another
        # line is one piece with it), while the bars of letters in a word are parts of their letters
        alone = sum(1 for p in row if all(q.id == p.id or q.kind == "dot" or q.length < 0.2 * h
                                          for q in by_stroke.get(p.stroke, [])))
        if alone < max(3, 0.5 * len(row)):
            continue
        found.append({"row": row, "u": u, "a": a, "L": L, "period": period, "dash": dash, "lo": 0.0, "hi": L})
    # dots on the line between its ends are dashes too short to be lines; just beyond an end, a blot or a short piece
    # is the last dash, run into the lines it meets (three dashed edges of a cube meeting at a corner). A piece that
    # could finish several rows is the one whose line it lies on: the last dash of a diagonal edge, next to the end of
    # an upright one at their corner, is the diagonal's.
    core = {p.id for f in found for p in f["row"]}
    offers: dict[int, list] = {}
    for k, f in enumerate(found):
        u, a, L, period, dash, row = f["u"], f["a"], f["L"], f["period"], f["dash"], f["row"]
        near = []
        for d in sc.prims:
            if d.role != "draw" or d.id in core or not (d.kind == "dot" or (d.kind == "line" and d.length <= 1.2 * dash
                                                        and abs(float(np.dot(_unit(d.p1 - d.p0), u))) > math.cos(30 * DEG))):
                continue
            c = d.center if d.kind == "dot" else mid(d)
            w = c - a
            along = float(np.dot(w, u))
            side = abs(float(u[0] * w[1] - u[1] * w[0]))
            inside = -0.6 * h <= along <= L + 0.6 * h and side <= max(pen, 0.15 * h)
            beyond = (-1.2 * period <= along < 0 or L < along <= L + 1.2 * period) and side <= max(2 * pen, 0.35 * h)
            if inside or beyond:
                near.append((d, along, side))
        mine = {p.id for p in row} | {d.id for d, _, _ in near}
        for d, along, side in near:
            # (not a stroke of a letter written next to the line's end: its other pieces are not dashes)
            own = sc.kind.get(d.stroke) == "draw" or all(q.id in mine or q.kind == "dot" or q.length < 0.2 * h
                                                          for q in by_stroke.get(d.stroke, []))
            if own:
                offers.setdefault(d.id, []).append((side, k, along, d))
    for choices in offers.values():
        side, k, along, d = min(choices, key=lambda c: c[0])
        f = found[k]
        f["row"].append(d)
        f["lo"], f["hi"] = min(f["lo"], along), max(f["hi"], along)
    for f in found:
        row = f["row"]
        for p in row:
            p.role = "dash"
            p.group = len(sc.rows)
        sc.rows.append({"kind": "dashes", "prims": [p.id for p in row], "p0": f["a"] + f["u"] * f["lo"],
                        "p1": f["a"] + f["u"] * f["hi"], "period": f["period"]})


# ===================================================================== panels

def _pool(mask: np.ndarray, f: int) -> np.ndarray:
    """Block maximum: a pixel of the small mask is ink if any pixel of its f x f block is."""
    if f <= 1:
        return mask.copy()
    H, W = mask.shape
    Hp, Wp = -(-H // f) * f, -(-W // f) * f
    m = np.zeros((Hp, Wp), bool)
    m[:H, :W] = mask
    return m.reshape(Hp // f, f, Wp // f, f).any(axis=(1, 3))


def _find_panels(sc: Scene, pen: float, h: float) -> None:
    """Separate pictures of one drawing: groups of ink with white space between them. A group with little drawing in
    it (a label on its own, an equals sign) is not a picture: it goes with the nearest picture, or stays a small
    panel of its own between two pictures."""
    ink = sc.ink
    H, W = ink.shape
    gap = max(1.6 * h, 0.03 * max(H, W))
    f = max(1, int(gap // 8))
    small = _pool(ink.mask, f)
    grown = fi.dilate(small, max(1, int(round(gap / f / 2))))
    lab, n = fi.label(grown)
    if n <= 1:
        sc.panels = [Panel(1, (0, 0, W, H), [p.id for p in sc.prims], [lb.id for lb in sc.labels])]
        return
    bx = fi.boxes(lab, n)
    groups = []
    for k in range(n):
        x0, y0, x1, y1, _ = bx[k]
        groups.append({"box": [int(x0 * f), int(y0 * f), int(min(W, x1 * f)), int(min(H, y1 * f))], "prims": [], "labels": [], "draw": 0.0})

    def which(x, y):
        yy, xx = min(lab.shape[0] - 1, int(y // f)), min(lab.shape[1] - 1, int(x // f))
        k = lab[yy, xx]
        return k - 1 if k else None
    for p in sc.prims:
        c = p.pts[len(p.pts) // 2] if len(p.pts) else p.center
        k = which(*c)
        if k is None:
            continue
        groups[k]["prims"].append(p.id)
        if p.role in ("draw", "dash", "dotrow", "barb"):
            groups[k]["draw"] += p.length if p.kind != "circle" else 2 * math.pi * p.r
    for lb in sc.labels:
        k = which((lb.box[0] + lb.box[2]) / 2, (lb.box[1] + lb.box[3]) / 2)
        if k is not None:
            groups[k]["labels"].append(lb.id)
    big = [g for g in groups if g["draw"] >= 3 * h]
    if not big:
        big = [max(groups, key=lambda g: g["draw"] + len(g["prims"]))]
    for g in groups:
        if g in big or not (g["prims"] or g["labels"]):
            continue
        if g["draw"] >= 1.2 * h and not g["labels"]:
            big.append(g)                               # a small drawing of its own (an arrow between two pictures)
            continue
        gb = g["box"]
        best = min(big, key=lambda b: _box_gap(gb, b["box"]))
        best["prims"] += g["prims"]
        best["labels"] += g["labels"]
        best["box"] = [min(gb[0], best["box"][0]), min(gb[1], best["box"][1]), max(gb[2], best["box"][2]), max(gb[3], best["box"][3])]
    order = sorted(big, key=lambda g: (g["box"][1] // max(1, int(3 * h)), g["box"][0]))
    sc.panels = [Panel(i + 1, tuple(g["box"]), g["prims"], g["labels"]) for i, g in enumerate(order)]


def _box_gap(a, b) -> float:
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)
