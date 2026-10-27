# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Drawing a figure again, properly, from what was measured.

A hand-drawn figure is not copied: its wobble is not the author's intention. It is drawn again from the measured
pieces, made exact where the drawing so clearly means it that no reader would doubt it:
  - ends that nearly meet, meet (at a shared point, or on the line they run into: a T);
  - lines that are nearly horizontal or vertical become so, and lines that are nearly parallel become parallel;
  - points that nearly line up, line up; rows of points nearly evenly spaced become evenly spaced; lines in the
    same direction of nearly the same length get the same length; circles of nearly the same size, the same size;
  - curves are drawn as smooth curves through the measured line, without its tremor;
  - the handwritten labels are set in LaTeX at the place they were written.
"Nearly" is a tolerance of a few pen widths, measured on the page: a difference the author drew on purpose is larger
and stays. Each rule moves points as little as possible: all of them together are one least-squares problem (the
smallest total movement that satisfies every rule), and a rule that would move a point further than the tolerance
is dropped. The result is written as TikZ by code, in the same layout as the drawing, so that it can be laid over
the drawing and compared with it (figcheck.py)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .figmeasure import DEG, Scene, _unit, bezier_points, fit_bezier

CM_PER_LETTER = 0.24             # a handwritten letter becomes a printed capital letter: about 2.4 mm
MAX_WIDTH_CM = 12.0
MIN_WIDTH_CM = 3.5


@dataclass
class V:
    id: int
    p: np.ndarray                 # position, region pixels
    p0: np.ndarray                # as measured
    kind: str = "pt"              # pt | dot
    r: float = 0.0                # dot radius
    color: tuple = (0, 0, 0)
    deg: int = 0


@dataclass
class El:
    id: int
    kind: str                     # line | curve | arc | circle | ellipse | closed
    v: list                       # vertex ids: line/curve/arc [a, b]; circle/ellipse [centre]
    color: tuple = (0, 0, 0)
    width: float = 6.0
    bez: list = field(default_factory=list)       # curve / closed: Bezier segments (absolute, measured)
    r: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    angle: float = 0.0
    sweep: float = 0.0            # arc: signed sweep (radians, image orientation)
    heads: list = field(default_factory=lambda: ["", ""])     # arrow head at v[0], v[1]: "" | open | triangle
    bar: list = field(default_factory=lambda: [False, False])  # a bar across the end (maps to)
    dashed: bool = False
    reach: float = 0.0            # how far its ends may be from what they meet (a dashed line: about one dash and gap)
    on: list = field(default_factory=list)        # vertices that lie on this line (T-junctions)
    src: list = field(default_factory=list)       # measured primitive ids
    direction: np.ndarray | None = None


@dataclass
class Lb:
    id: int
    text: str
    box: tuple                    # region pixels
    color: tuple
    at: np.ndarray                # position of the anchor point, region pixels
    anchor: str = "center"
    follow: int = -1              # vertex it moves with
    font: str = ""                # a smaller font, for a label much larger typeset than handwritten


@dataclass
class Model:
    scene: Scene
    verts: list
    els: list
    labels: list
    pen: float
    h: float
    log: list = field(default_factory=list)
    k: float = 0.0                # centimetres per pixel, once fitted to the typeset labels (fit_labels)

    def v(self, i: int) -> V:
        return self.verts[i]


# ===================================================================== from the measurement to a model

def build(sc: Scene, texts: dict | None = None) -> Model:
    """The model of a figure: every primitive the drawing keeps, with its end points as vertices. `texts` gives the
    printed text of each label (label id -> LaTeX); a label without text is left out."""
    ink = sc.ink
    pen = max(ink.pen, 2.0)
    h = max(ink.text_h, 3 * pen)
    verts: list[V] = []
    els: list[El] = []

    def nv(p, kind="pt", r=0.0, color=(0, 0, 0)):
        verts.append(V(len(verts), np.array(p, float), np.array(p, float), kind, r, color))
        return verts[-1].id
    arrows = {(a.prim, a.end): a.head for a in sc.arrows}
    bars = set(sc.bars)
    for p in sc.prims:
        if p.role not in ("draw",):
            continue
        if p.kind == "dot":
            nv(p.center, "dot", max(p.r, pen / 2), p.color)
            continue
        if p.kind == "fill":                       # a filled area: its corners are points like any other
            els.append(El(len(els), "fill", [nv(q) for q in p.pts], p.color, p.width, src=[p.id]))
            continue
        e = El(len(els), p.kind, [], p.color, p.width, src=[p.id])
        if p.kind in ("line", "arc", "curve"):
            e.v = [nv(p.p0), nv(p.p1)]
            if p.kind == "curve":
                e.bez = [tuple(np.array(c, float) for c in seg) for seg in p.bez]
            if p.kind == "arc":
                e.r = p.r
                e.sweep = _arc_sweep(p.pts, p.center)
            e.heads = [arrows.get((p.id, 0), ""), arrows.get((p.id, 1), "")]
            e.bar = [(p.id, 0) in bars, (p.id, 1) in bars]
        elif p.kind == "circle":
            e.v = [nv(p.center, "center")]
            e.r = p.r
        elif p.kind == "ellipse":
            e.v = [nv(p.center, "center")]
            e.rx, e.ry, e.angle = p.rx, p.ry, p.angle
        elif p.kind == "closed":
            e.bez = [tuple(np.array(c, float) for c in seg) for seg in p.bez]
        els.append(e)
    for row in sc.rows:
        if row["kind"] == "dashes":
            e = El(len(els), "line", [nv(row["p0"]), nv(row["p1"])], sc.prims[row["prims"][0]].color,
                   sc.prims[row["prims"][0]].width, dashed=True, src=list(row["prims"]))
            e.reach = float(row.get("period") or 0.0)
            els.append(e)
        else:
            for i in row["prims"]:
                p = sc.prims[i]
                nv(p.center, "dot", max(p.r, pen / 2), p.color)
    labels = []
    for lb in sc.labels:
        t = (texts or {}).get(lb.id)
        if lb.kind == "drawing" or not t:
            continue
        x0, y0, x1, y1 = lb.box
        labels.append(Lb(lb.id, t, lb.box, lb.color, np.array([(x0 + x1) / 2, (y0 + y1) / 2])))
    m = Model(sc, verts, els, labels, pen, h)
    for e in els:
        for i in e.v:
            if verts[i].kind != "center":
                verts[i].color = e.color
    return m


def _arc_sweep(pts, c) -> float:
    a = np.unwrap(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    return float(a[-1] - a[0])


# ===================================================================== making it exact

def beautify(m: Model) -> Model:
    tol = max(2.0 * m.pen, 0.26 * m.h)            # "nearly": ends this close meet
    _merge_colors(m)
    _merge_ends(m, tol)
    _aim_ends(m, tol)
    _junctions(m, tol)
    _directions(m)
    _solve(m, tol)
    _equal_radii(m)
    _smooth_curves(m)
    _place_labels(m)
    return m


def _incident(m: Model) -> dict:
    inc: dict[int, list] = {v.id: [] for v in m.verts}
    for e in m.els:
        for k, i in enumerate(e.v):
            inc[i].append((e.id, k))
    return inc


def _merge_ends(m: Model, tol: float) -> None:
    """End points (and dots) closer than `tol` become one vertex: a dot keeps its place, others meet in the middle."""
    pts = [v for v in m.verts if v.kind in ("pt", "dot")]
    parent = {v.id: v.id for v in pts}

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict[int, int] = {}
    reach: dict[int, float] = {}
    for e in m.els:
        for i in e.v:
            owner[i] = e.id
            reach[i] = max(reach.get(i, 0.0), 0.9 * e.reach)
    def limit(va, vb) -> float:
        lim = max(tol, reach.get(va.id, 0.0), reach.get(vb.id, 0.0))
        if va.kind == "dot" or vb.kind == "dot":
            lim = lim + max(va.r, vb.r)
        return lim
    pairs = []
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            va, vb = pts[a], pts[b]
            d = float(np.hypot(*(va.p - vb.p)))
            lim = limit(va, vb)
            if d <= lim:
                if owner.get(va.id) is not None and owner.get(va.id) == owner.get(vb.id):
                    continue                          # the two ends of one line never merge
                pairs.append((d, va.id, vb.id))
    # the elements each group of ends belongs to: a group never takes in both ends of one element (a short tick at
    # the end of a line keeps its length, however close its far end is to the corner it marks)
    owns: dict[int, set] = {v.id: ({owner[v.id]} if v.id in owner else set()) for v in pts}
    # and every two ends of a group are close: ends do not chain into one point across a figure (two arrows that start
    # near the two ends of one dash are two arrows)
    members: dict[int, list] = {v.id: [v] for v in pts}
    for d, a, b in sorted(pairs):
        ra, rb = f(a), f(b)
        if ra == rb or (owns[ra] & owns[rb]):
            continue
        if any(float(np.hypot(*(x.p - y.p))) > limit(x, y) for x in members[ra] for y in members[rb]):
            continue
        parent[rb] = ra
        owns[ra] |= owns[rb]
        members[ra] += members.pop(rb)
    groups: dict[int, list] = {}
    for v in pts:
        groups.setdefault(f(v.id), []).append(v.id)
    remap = {}
    for root, ids in groups.items():
        if len(ids) == 1:
            continue
        vs = [m.verts[i] for i in ids]
        dots = [v for v in vs if v.kind == "dot"]
        if dots:
            keep = max(dots, key=lambda v: v.r)
            pos = keep.p0.copy()
        else:
            keep = vs[0]
            pos = np.mean([v.p0 for v in vs], axis=0)
        keep.p = pos.copy()
        keep.p0 = pos.copy()
        for v in vs:
            if v.id != keep.id:
                remap[v.id] = keep.id
    if remap:
        for e in m.els:
            e.v = [remap.get(i, i) for i in e.v]
        # an element whose two ends merged (a tiny loop) is dropped
        m.els = [e for e in m.els if not (e.kind == "line" and len(e.v) == 2 and e.v[0] == e.v[1])]
        m.log.append(f"{len(remap)} end points joined")
    _reindex(m)


def _aim_ends(m: Model, tol: float) -> None:
    """A straight line whose free end stops a little short of a corner it points at (a point where other lines
    meet, or a dot) runs on to that corner: hand-drawn lines often stop or overshoot by more than a pen's width.
    It must point at the corner closely, within about a pen of it sideways, and the corner must be near, within
    half a letter's height. An end with an arrowhead keeps its place, and so does an end that points at nothing
    (the end of an axis, of a tick)."""
    inc = _incident(m)
    reach = max(tol, 0.6 * m.h)
    side = max(1.2 * m.pen, 0.12 * m.h)
    corners = [w for w in m.verts if w.kind == "dot" or len(inc[w.id]) >= 2]
    n = 0
    for v in m.verts:
        if v.kind != "pt" or len(inc[v.id]) != 1:
            continue
        eid, k = inc[v.id][0]
        e = m.els[eid]
        if e.kind != "line" or e.heads[k] or e.bar[k]:
            continue
        other = m.verts[e.v[1 - k]].p
        u = v.p - other
        L = float(np.hypot(*u))
        if L < 1e-6:
            continue
        u = u / L
        best = None
        for w in corners:
            if w.id in e.v:
                continue
            d = w.p - v.p
            ahead = float(d @ u)
            lat = abs(float(u[0] * d[1] - u[1] * d[0]))
            rw = w.r if w.kind == "dot" else 0.0                 # (a line points at a dot's edge)
            if 0 < ahead <= reach + rw and lat <= side + rw and (best is None or ahead < best[0]):
                best = (ahead, w)
        if best is None:
            continue
        e.v[k] = best[1].id
        n += 1
    if n:
        m.log.append(f"{n} lines run on to the corner they point at")
        _reindex(m)


def _reindex(m: Model) -> None:
    used = {i for e in m.els for i in e.v} | {i for e in m.els for i in e.on} | {v.id for v in m.verts if v.kind == "dot"}
    keep = [v for v in m.verts if v.id in used]
    new = {v.id: k for k, v in enumerate(keep)}
    for k, v in enumerate(keep):
        v.id = k
    m.verts = keep
    for k, e in enumerate(m.els):
        e.id = k
        e.v = [new[i] for i in e.v]
        e.on = [new[i] for i in e.on if i in new]
    for v in m.verts:
        v.deg = 0
    for e in m.els:
        for i in e.v:
            m.verts[i].deg += 1


def _closest_on(e: El, m: Model, p: np.ndarray) -> tuple[float, np.ndarray, float]:
    """(distance, closest point, parameter 0..1) from p to element e."""
    if e.kind == "line":
        a, b = m.verts[e.v[0]].p, m.verts[e.v[1]].p
        ab = b - a
        L2 = float(ab @ ab) or 1e-9
        t = float(np.clip((p - a) @ ab / L2, 0, 1))
        q = a + t * ab
        return float(np.hypot(*(p - q))), q, t
    if e.kind == "circle":
        c = m.verts[e.v[0]].p
        d = p - c
        n = float(np.hypot(*d)) or 1e-9
        q = c + d / n * e.r
        return abs(n - e.r), q, 0.5
    P = _element_points(m, e)
    if len(P) < 2:
        return float("inf"), p, 0.0
    A, B = P[:-1], P[1:]
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(axis=1), 1e-12)
    t = np.clip(((p - A) * AB).sum(axis=1) / L2, 0, 1)
    Q = A + AB * t[:, None]
    d = np.hypot(*(Q - p).T)
    k = int(np.argmin(d))
    return float(d[k]), Q[k], (k + float(t[k])) / max(1, len(A))


def _junctions(m: Model, tol: float) -> None:
    """A free end close to another line runs to it: to where its own line meets it (it is extended or cut back), or
    else to the nearest point of it. It then stays on that line (a T)."""
    inc = _incident(m)
    n = 0
    for v in m.verts:
        if v.kind == "pt" and len(inc[v.id]) == 1:
            eid, k = inc[v.id][0]
            own = m.els[eid]
            lim = max(tol, 0.9 * own.reach)
        elif v.kind == "dot" and len(inc[v.id]) <= 1:
            own, k = (m.els[inc[v.id][0][0]], inc[v.id][0][1]) if inc[v.id] else (None, 0)
            lim = tol + v.r
        else:
            continue
        best = None
        for e in m.els:
            if (own is not None and e.id == own.id) or v.id in e.v or e.kind in ("closed", "ellipse"):
                continue
            d, q, t = _closest_on(e, m, v.p)
            if d > lim:
                continue
            target = q
            if own is not None and own.kind == "line" and e.kind == "line":
                a = m.verts[own.v[1 - k]].p
                x = _intersect(a, v.p, m.verts[e.v[0]].p, m.verts[e.v[1]].p)
                if x is not None and np.hypot(*(x - v.p)) <= lim * 1.3:
                    target = x
            if best is None or d < best[0]:
                best = (d, e, target)
        if best is None:
            continue
        d, e, target = best
        # if the target is (nearly) an end of that line, join the vertices instead
        joined = False
        for j in e.v:
            if m.verts[j].kind in ("pt", "dot") and np.hypot(*(m.verts[j].p - target)) <= tol * 0.6:
                if own is not None:
                    own.v[k] = j
                    if v.kind == "dot":
                        m.verts[j].kind, m.verts[j].r = "dot", max(m.verts[j].r, v.r)
                        v.kind = "pt"
                else:
                    m.verts[j].kind, m.verts[j].r = "dot", max(m.verts[j].r, v.r)
                    v.kind = "pt"
                joined = True
                break
        if not joined:
            v.p = target.copy()
            v.p0 = target.copy()
            if e.kind == "line":
                e.on.append(v.id)
        n += 1
    if n:
        m.log.append(f"{n} ends run to the line they meet")
    _reindex(m)


def _intersect(a0, a1, b0, b1):
    d1, d2 = a1 - a0, b1 - b0
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    w = b0 - a0
    t = (w[0] * d2[1] - w[1] * d2[0]) / den
    u = (w[0] * d1[1] - w[1] * d1[0]) / den
    if -0.05 <= u <= 1.05:
        return a0 + t * d1
    return None


def _directions(m: Model) -> None:
    """Nearly horizontal or vertical lines become exactly so; lines of nearly the same direction become parallel."""
    lines = [e for e in m.els if e.kind == "line"]
    if not lines:
        return
    ang = []
    for e in lines:
        d = m.verts[e.v[1]].p - m.verts[e.v[0]].p
        ang.append(math.atan2(d[1], d[0]) % math.pi)
    L = [float(np.hypot(*(m.verts[e.v[1]].p - m.verts[e.v[0]].p))) for e in lines]
    inc = _incident(m)
    on = {v for e in m.els for v in e.on}
    fixed = [None] * len(lines)
    for i, a in enumerate(ang):
        e = lines[i]
        held = all(len(inc[v]) >= 2 or v in on for v in e.v)     # both ends on other lines: a divider, an edge
        for target in (0.0, math.pi / 2, math.pi):
            lim = (5 * DEG if L[i] >= 2 * m.h else 7 * DEG) + (2 * DEG if held else 0.0)
            if abs(a - target) <= lim:
                fixed[i] = target % math.pi
    # cluster the others
    rest = sorted([i for i in range(len(lines)) if fixed[i] is None], key=lambda i: ang[i])
    clusters: list[list[int]] = []
    for i in rest:
        if clusters and _adiff(ang[i], ang[clusters[-1][-1]]) <= 4 * DEG and _adiff(ang[i], ang[clusters[-1][0]]) <= 6 * DEG:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    if len(clusters) > 1 and _adiff(ang[clusters[0][0]], ang[clusters[-1][-1]]) <= 4 * DEG:
        clusters[0] = clusters[-1] + clusters[0]
        clusters.pop()
    for c in clusters:
        if len(c) == 1:
            fixed[c[0]] = ang[c[0]]
            continue
        vx = sum(L[i] * math.cos(2 * ang[i]) for i in c)
        vy = sum(L[i] * math.sin(2 * ang[i]) for i in c)
        mean = (math.atan2(vy, vx) / 2) % math.pi
        for i in c:
            fixed[i] = mean
    for e, a in zip(lines, fixed):
        e.direction = np.array([math.cos(a), math.sin(a)])
    snapped = sum(1 for i in range(len(lines)) if abs(_adiff(fixed[i], ang[i])) > 0.2 * DEG)
    if snapped:
        m.log.append(f"{snapped} lines straightened to a common direction")


def _adiff(a: float, b: float) -> float:
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def _solve(m: Model, tol: float) -> None:
    """Move the vertices as little as possible so that the rules hold: each line keeps its exact direction, each T
    stays on its line, and, where that costs little, points line up, rows are evenly spaced and parallel lines of
    nearly equal length get equal length. All rules are weighed together (least squares with a heavy weight on the
    rules), so rules that cannot all hold at once (directions measured by hand around a closed figure) never squash
    the figure: the rule that would move a point furthest is dropped, the softer rules first, and the rest solved
    again."""
    nV = len(m.verts)
    if nV == 0:
        return
    X0 = np.concatenate([v.p for v in m.verts])
    hard: list = []
    for e in m.els:
        if e.kind == "line" and e.direction is not None:
            a, b = e.v
            nrm = np.array([-e.direction[1], e.direction[0]])
            hard.append((({2 * a: -nrm[0], 2 * a + 1: -nrm[1], 2 * b: nrm[0], 2 * b + 1: nrm[1]}, 0.0), "direction", e.id))
            for t in e.on:
                if t in (a, b):
                    continue
                hard.append((({2 * a: -nrm[0], 2 * a + 1: -nrm[1], 2 * t: nrm[0], 2 * t + 1: nrm[1]}, 0.0), "junction", e.id))
    soft = [(r, why, -1) for r, why in _soft_rules(m, tol)]
    rules = hard + soft
    limit = 1.2 * tol
    X = X0
    for _ in range(40):
        X = _weighted(X0, [r for r, _, _ in rules], len(X0))
        D = np.hypot(*(X - X0).reshape(-1, 2).T)
        if not len(D) or D.max() <= limit:
            break
        worst = int(np.argmax(D))
        # the rules on the point that moved furthest; drop the one it is least able to satisfy, softest first
        mine = [k for k, (r, why, _) in enumerate(rules) if any(c // 2 == worst for c in r[0])]
        if not mine:
            break
        def cost(k):
            (coef, rhs), why, _ = rules[k]
            res = abs(sum(v * X0[c] for c, v in coef.items()) - rhs)
            return (0 if why not in ("direction", "junction") else 1, -res)
        mine.sort(key=cost)
        rules.pop(mine[0])
    for v in m.verts:
        v.p = X[2 * v.id: 2 * v.id + 2].copy()
    # a line whose direction rule was dropped keeps the direction it now has
    kept = {eid for _, why, eid in rules if why == "direction"}
    for e in m.els:
        if e.kind == "line" and e.direction is not None and e.id not in kept:
            d = m.verts[e.v[1]].p - m.verts[e.v[0]].p
            n = float(np.hypot(*d))
            e.direction = d / n if n > 0 else e.direction
    kinds: dict[str, int] = {}
    for _, why, _ in rules:
        if why not in ("direction", "junction"):
            kinds[why] = kinds.get(why, 0) + 1
    for why, n in kinds.items():
        m.log.append(f"{n} × {why}")


def _weighted(X0: np.ndarray, rows: list, n: int, w: float = 1e4) -> np.ndarray:
    """argmin |X - X0|^2 + w * sum (C_i X - c_i)^2: the rules hold where they can, the figure never collapses."""
    if not rows:
        return X0.copy()
    C = np.zeros((len(rows), n))
    c = np.zeros(len(rows))
    for i, (coef, rhs) in enumerate(rows):
        for k, val in coef.items():
            C[i, k] += val
        c[i] = rhs
    A = np.eye(n) + w * (C.T @ C)
    return np.linalg.solve(A, X0 + w * (C.T @ c))


def _soft_rules(m: Model, tol: float) -> list:
    """Alignments, even spacing and equal lengths that the drawing nearly has."""
    out = []
    pts = [v for v in m.verts if v.kind in ("pt", "dot", "center")]
    linked: set = set()
    for e in m.els:
        if e.kind == "line" and len(e.v) == 2:
            linked.add((min(e.v), max(e.v)))
    lim = 0.45 * tol
    for axis, name in ((0, "points lined up vertically"), (1, "points lined up horizontally")):
        order = sorted(pts, key=lambda v: v.p[axis])
        groups: list[list[V]] = []
        for v in order:
            if groups and v.p[axis] - groups[-1][0].p[axis] <= lim:
                groups[-1].append(v)
            else:
                groups.append([v])
        for g in groups:
            if len(g) < 2:
                continue
            base = g[0]
            for v in g[1:]:
                pair = (min(base.id, v.id), max(base.id, v.id))
                if pair in linked:
                    e = next(e for e in m.els if e.kind == "line" and set(e.v) == set(pair))
                    if e.direction is not None and abs(e.direction[1 - axis]) < 0.999:
                        continue          # the two ends of a slanted line do not line up
                out.append((({2 * base.id + axis: 1.0, 2 * v.id + axis: -1.0}, 0.0), name))
    # even spacing in rows of three or more aligned points
    for axis, other in ((0, 1), (1, 0)):
        order = sorted(pts, key=lambda v: v.p[other])
        rows: list[list[V]] = []
        for v in order:
            if rows and abs(v.p[other] - rows[-1][0].p[other]) <= lim:
                rows[-1].append(v)
            else:
                rows.append([v])
        for r in rows:
            if len(r) < 3:
                continue
            r = sorted(r, key=lambda v: v.p[axis])
            gaps = np.diff([v.p[axis] for v in r])
            if gaps.min() <= 0.5 * m.h:
                continue
            # the longest run of consecutive gaps within 12 % of their mean
            best = None
            for i in range(len(gaps)):
                for j in range(i + 2, len(gaps) + 1):
                    g = gaps[i:j]
                    if g.max() - g.min() <= 0.12 * g.mean() and (best is None or j - i > best[1] - best[0]):
                        best = (i, j)
            if best and best[1] - best[0] >= 2:
                i, j = best
                for k in range(i, j - 1):
                    a, b, c = r[k], r[k + 1], r[k + 2]
                    out.append((({2 * a.id + axis: 1.0, 2 * b.id + axis: -2.0, 2 * c.id + axis: 1.0}, 0.0), "rows evenly spaced"))
    # equal lengths of parallel lines
    lines = [e for e in m.els if e.kind == "line" and e.direction is not None]
    lens = {e.id: float(np.hypot(*(m.verts[e.v[1]].p - m.verts[e.v[0]].p))) for e in lines}
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            if abs(float(a.direction @ b.direction)) < math.cos(1 * DEG):
                continue
            la, lb = lens[a.id], lens[b.id]
            if min(la, lb) < 1.5 * m.h or abs(la - lb) > 0.06 * max(la, lb):
                continue
            da = a.direction
            sa = 1.0 if (m.verts[a.v[1]].p - m.verts[a.v[0]].p) @ da > 0 else -1.0
            sb = 1.0 if (m.verts[b.v[1]].p - m.verts[b.v[0]].p) @ da > 0 else -1.0
            coef: dict = {}
            for k, val in ((2 * a.v[1], sa * da[0]), (2 * a.v[1] + 1, sa * da[1]), (2 * a.v[0], -sa * da[0]), (2 * a.v[0] + 1, -sa * da[1]),
                           (2 * b.v[1], -sb * da[0]), (2 * b.v[1] + 1, -sb * da[1]), (2 * b.v[0], sb * da[0]), (2 * b.v[0] + 1, sb * da[1])):
                coef[k] = coef.get(k, 0.0) + val
            out.append(((coef, 0.0), "parallel lines given equal length"))
    return out


def _equal_radii(m: Model) -> None:
    circ = [e for e in m.els if e.kind == "circle"]
    circ.sort(key=lambda e: e.r)
    groups: list[list[El]] = []
    for e in circ:
        if groups and e.r <= groups[-1][0].r * 1.12:
            groups[-1].append(e)
        else:
            groups.append([e])
    for g in groups:
        if len(g) > 1:
            r = float(np.mean([e.r for e in g]))
            for e in g:
                e.r = r
            m.log.append(f"{len(g)} circles given the same size")
    dots = [v for v in m.verts if v.kind == "dot"]
    if dots:
        r = float(np.median([v.r for v in dots]))
        for v in dots:
            if v.r <= 1.6 * r:
                v.r = r


def _smooth_curves(m: Model) -> None:
    """A curve follows its moved end points and is fitted again with a looser tolerance: smooth, not trembling."""
    for e in m.els:
        if e.kind == "curve" and e.bez:
            P = np.vstack([bezier_points(seg, 24)[:-1] for seg in e.bez] + [e.bez[-1][3][None, :]])
            a, b = m.verts[e.v[0]], m.verts[e.v[1]]
            d0, d1 = a.p - P[0], b.p - P[-1]
            t = np.linspace(0, 1, len(P))[:, None]
            P = P + (1 - t) * d0 + t * d1
            L = float(np.hypot(*np.diff(P, axis=0).T).sum())
            e.bez = fit_bezier(P, max(1.4 * m.pen, 0.03 * L))
        elif e.kind == "closed" and e.bez:
            P = np.vstack([bezier_points(seg, 24)[:-1] for seg in e.bez])
            L = float(np.hypot(*np.diff(P, axis=0).T).sum())
            e.bez = fit_bezier(np.vstack([P, P[:1]]), max(1.4 * m.pen, 0.02 * L))


def _place_labels(m: Model) -> None:
    """Each label stays where it was written. If a line ends right at one of its sides (a pointer to the label), the
    label is anchored at that side, so that the line still touches it whatever the width of the printed text."""
    ends = []
    inc = _incident(m)
    on = {v for e in m.els for v in e.on}
    for e in m.els:
        if e.kind in ("line", "curve", "arc"):
            for i in e.v:
                if len(inc[i]) == 1 and i not in on and m.verts[i].kind != "dot":
                    ends.append(m.verts[i].p)            # only a free end points at a label
    for lb in m.labels:
        x0, y0, x1, y1 = lb.box
        cy = (y0 + y1) / 2
        best = None
        for p in ends:
            for side, (sx, sy) in (("west", (x0, cy)), ("east", (x1, cy)), ("north", ((x0 + x1) / 2, y0)), ("south", ((x0 + x1) / 2, y1))):
                if side in ("west", "east") and not (y0 - 0.6 * m.h <= p[1] <= y1 + 0.6 * m.h):
                    continue
                if side in ("north", "south") and not (x0 - 0.3 * m.h <= p[0] <= x1 + 0.3 * m.h):
                    continue
                d = float(np.hypot(p[0] - sx, p[1] - sy)) if side in ("north", "south") else abs(p[0] - sx)
                if d <= 1.1 * m.h and (best is None or d < best[0]):
                    best = (d, side, (sx, sy))
        if best:
            lb.anchor = best[1]
            lb.at = np.array(best[2], float)


# ===================================================================== TikZ

PALETTE = [  # (name in TikZ, hue in degrees) for ink that is not the main colour
    ("blue!80!black", 225), ("red!75!black", 0), ("green!50!black", 120), ("orange!85!black", 32),
    ("violet!80!black", 285), ("cyan!60!black", 188), ("magenta!70!black", 320), ("brown", 22),
]


def _hue(c) -> tuple[float, float, float]:
    import colorsys
    r, g, b = (x / 255 for x in c)
    return colorsys.rgb_to_hsv(r, g, b)


def _chroma(c) -> float:
    """How coloured an ink is, 0 (black, grey) .. 1: the spread of its channels. Unlike the saturation it does not
    grow without bound as the ink gets darker, where a few units of tint left by the paper or the light would make
    a black ink look coloured."""
    return (max(c) - min(c)) / 255.0


GREY_CHROMA = 0.13


def color_names(colors: list[tuple]) -> dict:
    """A TikZ colour for each ink colour: the commonest dark ink is black; grey stays grey; other inks get the
    nearest of a few print-safe colours, each used once."""
    names: dict = {}
    if not colors:
        return names
    taken = set()
    # the main ink is the most used dark ink (a dark blue pen is the author's black), else the most used one
    dark = [c for c in colors if _hue(c)[2] <= (0.45 if _chroma(c) < GREY_CHROMA else 0.6)]
    main = dark[0] if dark else colors[0]
    for c in colors:
        hh, s, v = _hue(c)
        if c == main or (_chroma(c) < GREY_CHROMA and v < 0.4) or (_cdist(c, main) < 45):
            names[c] = "black"
            continue
        if _chroma(c) < GREY_CHROMA:
            names[c] = "black!55"
            continue
        best = None
        for name, hue in PALETTE:
            if name in taken:
                continue
            h2 = hue / 360
            dh = min(abs(hh - h2), 1 - abs(hh - h2))
            if best is None or dh < best[0]:
                best = (dh, name)
        name = best[1] if best else "black"
        taken.add(name)
        names[c] = name
    return names


def _cdist(a, b) -> float:
    return float(sum(abs(int(x) - int(y)) for x, y in zip(a, b)))


def _fmt(x: float) -> str:
    s = f"{x:.2f}"
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return "0" if s in ("-0", "") else s


class Frame:
    """From region pixels (y down) to TikZ centimetres (y up), with the origin at the bottom left of the drawing."""

    def __init__(self, m: Model):
        P = [v.p for v in m.verts]
        for e in m.els:
            if e.kind in ("curve", "closed"):
                for seg in e.bez:
                    P.extend(seg)
            if e.kind == "circle":
                c = m.verts[e.v[0]].p
                P.extend([c - e.r, c + e.r])
            if e.kind == "ellipse":
                c = m.verts[e.v[0]].p
                R = max(e.rx, e.ry)
                P.extend([c - R, c + R])
        for lb in m.labels:
            P.extend([np.array(lb.box[:2], float), np.array(lb.box[2:], float)])
        A = np.array(P) if P else np.zeros((1, 2))
        self.x0, self.y0 = A.min(axis=0)
        self.x1, self.y1 = A.max(axis=0)
        if m.k > 0:
            self.k = m.k
            return
        # a handwritten letter becomes a printed one: the labels of the figure, where it has some, set the scale (a
        # figure drawn small, with large letters in it, is printed larger, so that its labels still fit in it)
        hs = [lb.box[3] - lb.box[1] for lb in m.labels if lb.box[3] > lb.box[1]]
        ref = float(np.median(hs)) if hs else 0.0
        k = CM_PER_LETTER / max(ref if ref >= 0.6 * m.h else m.h, 1.0)
        w = (self.x1 - self.x0) * k
        if w > MAX_WIDTH_CM:
            k *= MAX_WIDTH_CM / w
        elif w < MIN_WIDTH_CM and w > 0:
            k *= min(MIN_WIDTH_CM / w, 2.5)
        self.k = k

    def xy(self, p) -> str:
        return f"({_fmt((p[0] - self.x0) * self.k)},{_fmt((self.y1 - p[1]) * self.k)})"

    def length(self, d: float) -> str:
        return _fmt(d * self.k)

    def size(self) -> tuple[float, float]:
        return ((self.x1 - self.x0) * self.k, (self.y1 - self.y0) * self.k)


FONTS = [("", 1.0), ("\\small", 10 / 11), ("\\footnotesize", 9 / 11), ("\\scriptsize", 8 / 11)]   # in an 11pt document


def fit_labels(m: Model, sizes: list) -> None:
    """Labels set at the size they were written. `sizes` gives each label's typeset size, (width, height) in points
    (paper.measure_tex), None where unknown. The scale of the drawing is chosen so that a typical label takes the
    room its handwriting took: crowded small writing makes the drawing larger, within the width a figure can have.
    A label still much larger typeset than handwritten (a long subscript written small) gets a smaller font."""
    F0 = Frame(m)
    k0 = F0.k
    ratios = []
    for lb, sz in zip(m.labels, sizes or []):
        if not sz or sz[0] <= 0 or sz[1] <= 0:
            continue
        x0, y0, x1, y1 = lb.box
        w_cm, h_cm = max(1.0, x1 - x0) * k0, max(1.0, y1 - y0) * k0
        tw, th = sz[0] / 72.27 * 2.54, sz[1] / 72.27 * 2.54
        ratios.append((lb, math.sqrt(tw / w_cm * th / h_cm)))
    if not ratios:
        return
    r = float(np.median([x for _, x in ratios]))
    width_px = max(F0.x1 - F0.x0, 1.0)
    k = k0 * min(max(r, 0.7), 2.5)
    k = min(k, max(k0, MAX_WIDTH_CM / width_px))         # never wider than a page allows (unless it already was)
    if abs(k / k0 - 1) > 0.08:
        m.k = k
        m.log.append(f"drawn {k / k0:.2g} times as large, so that the labels fit where they were written")
    else:
        k = k0
    small = 0
    for lb, ri in ratios:
        rest = ri * k0 / k                                 # how much larger than its handwriting the label still is
        if rest <= 1.2:
            continue
        for name, f in FONTS[1:]:
            lb.font = name
            if rest * f <= 1.15:
                break
        small += 1
    if small:
        m.log.append(f"{small} long labels set in a smaller font")


def tikz(m: Model) -> str:
    F = Frame(m)
    cols = color_names(_colors_by_use(m))
    pen_pt = m.pen * F.k / 2.54 * 72.27
    widths = sorted({round(e.width / m.pen, 1) for e in m.els}) or [1.0]
    base_w = "semithick"
    lines = []
    lines.append(f"\\begin{{tikzpicture}}[x=1cm,y=1cm,line cap=round,line join=round,{base_w},"
                 f">={{To[length=1.6mm,width=2.2mm]}}]")
    inc = _incident(m)
    names: dict[int, str] = {}
    for v in m.verts:
        if len(inc[v.id]) + sum(1 for e in m.els if v.id in e.on) >= 2:
            names[v.id] = f"v{len(names) + 1}"
    if names:
        lines.append("% points shared by several lines")
        for vid, nm in names.items():
            lines.append(f"\\coordinate ({nm}) at {F.xy(m.verts[vid].p)};")

    def P(vid):
        return f"({names[vid]})" if vid in names else F.xy(m.verts[vid].p)

    def opts(e: El) -> str:
        o = []
        c = cols.get(e.color, "black")
        if c != "black":
            o.append(c)
        ratio = e.width / m.pen
        if ratio >= 1.35:                          # a heavier line keeps its weight against the others
            o.append(f"line width={_fmt(min(3.0, round(0.6 * ratio, 1)))}pt")
        elif ratio <= 0.6:
            o.append("thin")
        if e.dashed:
            o.append("dashed")
        tip = {"open": "{To[length=1.6mm,width=2.2mm]}", "triangle": "{Triangle[open,length=2.4mm,width=2.4mm]}",
               "filled": "{Stealth[length=2.2mm,width=1.8mm]}"}
        h0, h1 = e.heads if len(e.heads) == 2 else ("", "")
        if h0 or h1 or any(e.bar):
            left = tip.get(h0, "") or ("{Bar[width=2.2mm]}" if e.bar[0] else "")
            right = tip.get(h1, "") or ("{Bar[width=2.2mm]}" if e.bar[1] else "")
            o.append(f"{left}-{right}")
        return f"[{','.join(o)}]" if o else ""
    body = []
    for e in m.els:                                 # filled areas first, under the lines
        if e.kind == "fill" and len(e.v) >= 3:
            body.append(f"\\fill[{_fill_color(e.color, cols)}] " + " -- ".join(P(i) for i in e.v) + " -- cycle;")
    for e in m.els:
        o = opts(e)
        if e.kind == "fill":
            continue
        if e.kind == "line":
            body.append(f"\\draw{o} {P(e.v[0])} -- {P(e.v[1])};")
        elif e.kind == "curve":
            segs = " ".join(f".. controls {F.xy(s[1])} and {F.xy(s[2])} .. " + (P(e.v[1]) if k == len(e.bez) - 1 else F.xy(s[3]))
                            for k, s in enumerate(e.bez))
            body.append(f"\\draw{o} {P(e.v[0])} {segs};")
        elif e.kind == "arc":
            a, b = m.verts[e.v[0]].p, m.verts[e.v[1]].p
            c, r, st, en = _arc_through(a, b, e.sweep)
            body.append(f"\\draw{o} {P(e.v[0])} arc[start angle={_fmt(st)},end angle={_fmt(en)},radius={F.length(r)}];")
        elif e.kind == "circle":
            body.append(f"\\draw{o} {P(e.v[0])} circle[radius={F.length(e.r)}];")
        elif e.kind == "ellipse":
            ang = -math.degrees(e.angle)
            body.append(f"\\draw{o} {P(e.v[0])} circle[x radius={F.length(e.rx)},y radius={F.length(e.ry)},rotate={_fmt(ang)}];")
        elif e.kind == "closed":
            segs = " ".join(f".. controls {F.xy(s[1])} and {F.xy(s[2])} .. {F.xy(s[3])}" for s in e.bez[:-1])
            s = e.bez[-1]
            body.append(f"\\draw{o} {F.xy(e.bez[0][0])} {segs} .. controls {F.xy(s[1])} and {F.xy(s[2])} .. cycle;")
    for v in m.verts:
        if v.kind == "dot":
            c = cols.get(v.color, "black")
            r_pt = max(0.7 if v.deg == 0 else 1.1, min(2.4, v.r * F.k / 2.54 * 72.27))
            body.append(f"\\fill{('[' + c + ']') if c != 'black' else ''} {P(v.id)} circle[radius={_fmt(r_pt)}pt];")
    if body:
        lines.append("% the drawing")
        lines.extend(body)
    if m.labels:
        lines.append("% labels")
        for lb in m.labels:
            c = cols.get(lb.color, "black")
            o = [f"anchor={lb.anchor}", "inner sep=1.5pt"]
            if lb.font:
                o.append(f"font={lb.font}")
            if c != "black":
                o.append(f"text={c}")
            lines.append(f"\\node[{','.join(o)}] at {F.xy(lb.at)} {{{lb.text}}};")
    lines.append("\\end{tikzpicture}")
    return "\n".join(lines)


def _fill_color(c, cols: dict) -> str:
    """A filled area keeps its tone: a grey of the same darkness, or the print colour of its ink."""
    hh, s, v = _hue(c)
    if _chroma(c) < GREY_CHROMA:
        k = int(round(min(100, max(15, 100 * (1 - v))) / 5) * 5)
        return "black" if k >= 95 else f"black!{k}"
    return cols.get(c, "black")


def _colors_by_use(m: Model) -> list[tuple]:
    use: dict = {}
    for e in m.els:
        use[e.color] = use.get(e.color, 0.0) + 1.0
    for v in m.verts:
        if v.kind == "dot":
            use[v.color] = use.get(v.color, 0.0) + 0.3
    for lb in m.labels:
        use[lb.color] = use.get(lb.color, 0.0) + 0.5
    cols = sorted(use, key=lambda c: -use[c])
    return cols


def _merge_colors(m: Model) -> None:
    """Inks of nearly the same colour are one ink (a scan blurs and tints every colour a little): each takes the
    colour of the most used ink close to it."""
    use: dict = {}
    for e in m.els:
        use[e.color] = use.get(e.color, 0.0) + 1.0
    for v in m.verts:
        if v.kind == "dot":
            use[v.color] = use.get(v.color, 0.0) + 0.3
    for lb in m.labels:
        use[lb.color] = use.get(lb.color, 0.0) + 0.5
    order = sorted(use, key=lambda c: -use[c])
    rep: dict = {}
    reps: list = []
    for c in order:
        for r in reps:
            if _same_ink(c, r):
                rep[c] = r
                break
        else:
            reps.append(c)
            rep[c] = c
    for e in m.els:
        e.color = rep.get(e.color, e.color)
    for v in m.verts:
        v.color = rep.get(v.color, v.color)
    for lb in m.labels:
        lb.color = rep.get(lb.color, lb.color)


def _same_ink(a, b) -> bool:
    ha, sa, va = _hue(a)
    hb, sb, vb = _hue(b)
    ga, gb = _chroma(a) < GREY_CHROMA, _chroma(b) < GREY_CHROMA
    if ga and gb:                                     # two greys: the same if about as dark
        return abs(va - vb) < 0.22
    if ga or gb:
        return False
    dh = min(abs(ha - hb), 1 - abs(ha - hb))
    return dh < 0.07 and abs(va - vb) < 0.35


def _arc_through(a, b, sweep):
    """The circle arc from a to b turning by `sweep` (image orientation): (centre, radius, start, end) with the
    angles in TikZ's orientation (y up), in degrees."""
    chord = b - a
    L = float(np.hypot(*chord)) or 1e-9
    s = max(1e-6, abs(sweep))
    r = L / (2 * math.sin(min(s, 2 * math.pi - 1e-6) / 2)) if s < 2 * math.pi - 1e-3 else L / 2
    mid = (a + b) / 2
    nrm = np.array([-chord[1], chord[0]]) / L
    dist = math.sqrt(max(r * r - (L / 2) ** 2, 0.0))
    # the centre lies on the side that gives the right orientation (image coordinates, y down)
    sgn = 1.0 if sweep > 0 else -1.0
    if s > math.pi:
        dist = -dist
    c = mid + sgn * nrm * dist
    st = math.degrees(math.atan2(-(a[1] - c[1]), a[0] - c[0]))
    en = st - math.degrees(sweep)          # y flips: a positive sweep in the image turns clockwise on paper
    return c, r, st, en


# ===================================================================== drawing the model as pixels (for checking)

def raster(m: Model, shape: tuple[int, int], scale: float = 1.0, labels: bool = False) -> np.ndarray:
    """The model drawn in the pixels of the region (the frame the ink was measured in), as a mask."""
    from PIL import Image, ImageDraw
    H, W = shape
    im = Image.new("L", (int(W * scale), int(H * scale)), 0)
    d = ImageDraw.Draw(im)
    S = lambda p: (float(p[0]) * scale, float(p[1]) * scale)    # noqa: E731
    for e in m.els:
        if e.kind == "fill":
            if len(e.v) >= 3:
                d.polygon([S(m.verts[i].p) for i in e.v], fill=255)
            continue
        w = max(1, int(round(e.width * scale)))
        pts = _element_points(m, e)
        if len(pts) >= 2:
            if e.dashed:
                for k in range(0, len(pts) - 1, 2):
                    d.line([S(pts[k]), S(pts[k + 1])], fill=255, width=w)
            else:
                d.line([S(p) for p in pts], fill=255, width=w, joint="curve")
        for k, hd in enumerate(e.heads):
            if hd and len(pts) >= 2:
                tip = pts[0] if k == 0 else pts[-1]
                prev = pts[min(3, len(pts) - 1)] if k == 0 else pts[max(-4, -len(pts))]
                u = _unit(tip - prev)
                n = np.array([-u[1], u[0]])
                L = 0.5 * m.h
                a1 = tip - u * L + n * L * 0.55
                a2 = tip - u * L - n * L * 0.55
                d.line([S(a1), S(tip), S(a2)], fill=255, width=w)
                if hd == "triangle":
                    d.line([S(a1), S(a2)], fill=255, width=w)
    for v in m.verts:
        if v.kind == "dot":
            x, y = S(v.p)
            r = max(1.0, v.r * scale)
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    if labels:
        for lb in m.labels:
            x0, y0, x1, y1 = lb.box
            d.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale], fill=255)
    return np.asarray(im) > 127


def _element_points(m: Model, e: El) -> np.ndarray:
    if e.kind == "line":
        a, b = m.verts[e.v[0]].p, m.verts[e.v[1]].p
        if e.dashed:
            L = float(np.hypot(*(b - a)))
            n = max(2, int(L / max(1.0, 0.35 * m.h)))
            t = np.linspace(0, 1, n * 2)[:, None]
            return a + (b - a) * t
        return np.vstack([a, b])
    if e.kind in ("curve", "closed"):
        if not e.bez:
            return np.zeros((0, 2))
        return np.vstack([bezier_points(s, 24)[:-1] for s in e.bez] + [np.asarray(e.bez[-1][3])[None, :]])
    if e.kind == "arc":
        a, b = m.verts[e.v[0]].p, m.verts[e.v[1]].p
        chord = b - a
        L = float(np.hypot(*chord)) or 1e-9
        s = abs(e.sweep)
        r = L / (2 * math.sin(min(s, 2 * math.pi - 1e-6) / 2))
        nrm = np.array([-chord[1], chord[0]]) / L
        dist = math.sqrt(max(r * r - (L / 2) ** 2, 0.0))
        if s > math.pi:
            dist = -dist
        c = (a + b) / 2 + (1.0 if e.sweep > 0 else -1.0) * nrm * dist
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        t = a0 + np.linspace(0, e.sweep, 40)
        return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])
    if e.kind == "circle":
        c = m.verts[e.v[0]].p
        t = np.linspace(0, 2 * math.pi, 72)
        return np.column_stack([c[0] + e.r * np.cos(t), c[1] + e.r * np.sin(t)])
    if e.kind == "ellipse":
        c = m.verts[e.v[0]].p
        t = np.linspace(0, 2 * math.pi, 72)
        ca, sa = math.cos(e.angle), math.sin(e.angle)
        x = e.rx * np.cos(t)
        y = e.ry * np.sin(t)
        return np.column_stack([c[0] + x * ca - y * sa, c[1] + x * sa + y * ca])
    if e.kind == "fill" and e.v:
        return np.array([m.verts[i].p for i in e.v + e.v[:1]])
    return np.zeros((0, 2))
