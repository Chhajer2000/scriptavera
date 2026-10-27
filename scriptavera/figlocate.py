# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Where exactly a drawing is on its page.

The model that reads the page points at each drawing with a box. Such a box is close but not exact: it may cut off
the end of a line or a label, or take in a line of the text above. The box is therefore only a starting point; the
ink decides:
  - a piece of drawing (a long line, a curve, a large shape) that lies mostly inside the box is taken whole, even
    where it runs out of the box;
  - the text around a drawing is read as lines of handwriting: a line of text mostly inside the box (a label) is
    taken whole, one mostly outside it (the sentence before or after the drawing) is left out entirely;
  - ink attached to what was taken (a dot at the end of a line, a label right next to it) is taken too.
The drawing's box is then the box around what was taken, with a small margin. Two drawings that end up with the same
box are the same drawing, described twice."""

from __future__ import annotations

import numpy as np

from .figink import read
from .figsource import PageSource


def _inside_frac(b, B) -> float:
    ix = max(0.0, min(b[2], B[2]) - max(b[0], B[0]))
    iy = max(0.0, min(b[3], B[3]) - max(b[1], B[1]))
    a = max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))
    if a <= 1e-6:
        return 1.0 if (B[0] <= b[0] <= B[2] and B[1] <= b[1] <= B[3]) else 0.0
    return ix * iy / a


def _gap(a, b) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return float(np.hypot(dx, dy))


def _union(boxes):
    a = np.array(boxes, float)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max()))


def snap(src: PageSource, box, text_h: float, others: list | None = None) -> tuple[tuple, dict]:
    """The exact box (page units) of the drawing the model pointed at with `box` (page units). Returns (box, info);
    info["exclude"] lists the boxes of writing that lies inside the final box but is not part of the drawing.
    `others` are the boxes of the other drawings on the page: ink that lies more in one of them is theirs."""
    from .figmeasure import _polyline, _seg_dist, measure
    h = max(text_h, 1e-6)
    x0, y0, x1, y1 = src.clip(box)
    bw, bh = x1 - x0, y1 - y0
    m = max(3.0 * h, 0.25 * max(bw, bh))
    area = src.clip((x0 - m, y0 - m, x1 + m, y1 + m))
    side = max(area[2] - area[0], area[3] - area[1])
    scale = min(2400.0 / side, 30.0 / h)                      # a letter about 30 pixels high is plenty here
    if src.native_scale(area) and not src.vector_ink(area):
        scale = min(scale, src.native_scale(area))
    ink = read(src, area, scale=scale, text_h_page=h)
    s = ink.scale
    sc = measure(ink)

    def page_box(b):
        return (area[0] + b[0] / s, area[1] + b[1] / s, area[0] + b[2] / s, area[1] + b[3] / s)
    B = (x0, y0, x1, y1)
    strokes = {st.id: st for st in ink.strokes}
    sbox = {sid: page_box(st.bbox) for sid, st in strokes.items()}
    polys: dict[int, list] = {}
    for p in sc.prims:
        polys.setdefault(p.stroke, []).append((_polyline(p), p.r if p.kind == "dot" else 0.0))
    theirs = set()
    for sid, b in sbox.items():
        mine = _inside_frac(b, B)
        if any(_inside_frac(b, o) > max(mine, 0.3) for o in (others or [])):
            theirs.add(sid)
    dashes = {p.stroke for p in sc.prims if p.role == "dash"}          # a dashed line is drawing, not a row of words
    draw = {sid for sid in strokes if (sc.kind.get(sid) == "draw" or sid in dashes) and sid not in theirs}
    in_label = {sid for lb in sc.labels for sid in lb.strokes}
    take: set[int] = {sid for sid in draw if _inside_frac(sbox[sid], B) >= 0.35}
    touch = max(1.6 * ink.pen, 0.15 * ink.text_h)

    def touching(a, b) -> bool:
        A, Bb = sbox[a], sbox[b]
        g = touch / s
        if A[0] - g > Bb[2] or Bb[0] - g > A[2] or A[1] - g > Bb[3] or Bb[1] - g > A[3]:
            return False
        gaps = [_seg_dist(P, Q) - rp - rq for P, rp in polys.get(a, []) for Q, rq in polys.get(b, [])]
        return bool(gaps) and min(gaps) <= touch
    # a drawing is taken whole: what touches it is part of it (a tree whose top the box cut off)
    frontier = list(take)
    others = [sid for sid in strokes if sid not in take and sid not in in_label and sid not in theirs]
    while frontier:
        a = frontier.pop()
        for b in list(others):
            if b in take:
                continue
            if touching(a, b):
                take.add(b)
                frontier.append(b)
        others = [o for o in others if o not in take]
    taken_draw = [sbox[sid] for sid in take]
    ends = []
    for p in sc.prims:
        if p.stroke in take and p.kind in ("line", "arc", "curve") and not p.closed:
            ends += [page_box((e[0], e[1], e[0], e[1]))[:2] for e in (p.p0, p.p1)]
    # labels: a word well inside the box, or partly inside and right next to the drawing, or at the end of a line
    # drawn to it; a sentence of the notes that the box only cuts through is not
    words = [(lb, page_box(lb.box)) for lb in sc.labels if not set(lb.strokes) & theirs]
    # words in a row make a line of writing; a line of three or more words that the box does not hold whole is a
    # sentence of the notes, not a label
    rows = _text_lines([{"id": lb.id, "box": b} for lb, b in words], h)
    sentence = set()
    for row in rows:
        if len(row) >= 3 and _inside_frac(_union([w["box"] for w in row]), B) < 0.8:
            sentence.update(w["id"] for w in row)
    for lb, b in words:
        if lb.id in sentence:
            continue
        fr = _inside_frac(b, B)
        near = any(_gap(b, d) <= 1.2 * h for d in taken_draw)      # a label sits within about a letter of what it names
        pointer = any(_gap(b, (e[0], e[1], e[0], e[1])) <= 0.9 * h for e in ends)
        if fr >= 0.8 or (fr >= 0.45 and near) or (pointer and fr >= 0.1):
            take.update(lb.strokes)
    # loose dots and small marks: well inside the box they are the drawing's (the points of a row, the dots of a
    # dotted line, the head of a thin arrow), unless they lie in a sentence of the notes; partly inside, only when
    # they are next to what was taken
    lines_out = [_union([w["box"] for w in row]) for row in rows if all(w["id"] in sentence for w in row)]
    for sid in strokes:
        if sid in take or sid in in_label or sid in theirs:
            continue
        b = sbox[sid]
        fr = _inside_frac(b, B)
        if any(_inside_frac(b, (L[0] - 0.3 * h, L[1] - 0.3 * h, L[2] + 0.3 * h, L[3] + 0.3 * h)) > 0.5 for L in lines_out):
            continue
        if fr >= 0.8 or (fr >= 0.5 and any(_gap(b, d) <= 0.5 * h for d in taken_draw)):
            take.add(sid)
    if not take:
        return B, {"pieces": 0, "changed": False, "exclude": []}
    U = _union([sbox[i] for i in take])
    pad = 0.35 * h
    out = src.clip((U[0] - pad, U[1] - pad, U[2] + pad, U[3] + pad))
    exclude = []
    for sid, b in sbox.items():
        if sid in take or _inside_frac(b, out) < 0.3:
            continue
        exclude.append([round(b[0] - 0.08 * h, 2), round(b[1] - 0.08 * h, 2), round(b[2] + 0.08 * h, 2), round(b[3] + 0.08 * h, 2)])
    return out, {"pieces": len(take), "changed": _gap(out, B) > 0 or _inside_frac(B, out) < 0.95, "exclude": exclude}


def _text_lines(small: list, h: float) -> list[list]:
    """Small pieces grouped into lines of handwriting: pieces that overlap vertically and follow each other closely."""
    order = sorted(small, key=lambda p: (p["box"][0]))
    parent = {p["id"]: p["id"] for p in order}

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(order):
        ax0, ay0, ax1, ay1 = a["box"]
        for b in order[i + 1:]:
            bx0, by0, bx1, by1 = b["box"]
            if bx0 - ax1 > 1.4 * h:
                break
            ov = min(ay1, by1) - max(ay0, by0)
            if ov >= 0.3 * min(ay1 - ay0, by1 - by0, h):
                ra, rb = f(a["id"]), f(b["id"])
                if ra != rb:
                    parent[rb] = ra
    groups: dict[int, list] = {}
    for p in order:
        groups.setdefault(f(p["id"]), []).append(p)
    return list(groups.values())


def same_drawing(a, b) -> bool:
    """Whether two boxes (page units) are around the same drawing."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return ua > 0 and inter / ua >= 0.6
