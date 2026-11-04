# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Where each line of the transcript stands on the handwritten page. No model is asked: the page image is measured.

1. The lines of handwriting are found in the image: the ink (pen of any colour, not the paper and not highlighter)
   is smeared sideways so that letters join into words and words into lines, and each line gets its box, its
   writing size, and whether it is highlighted or underlined.
2. Lines that stand together (a block of lines one under the other) are grouped, and the blocks put in reading
   order: top to bottom, and left before right where blocks stand side by side, the order a reader writes them down.
3. The transcript is cut into its lines (a displayed formula and a drawing are one piece each), and each piece is
   matched to the handwritten lines in order, by its length against the length of the handwriting. A drawing is
   placed by the box the figure step found for it.

The result says, for each piece of the transcript, where it stands on the page (fractions of the page's width and
height) and whether it looks like a heading. Pieces that could not be matched take the place after the one before.
Handwriting is not straight and the matching is by length, so the places are approximate; a page that matches badly
is marked as such and shown as plain text."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image

VERSION = 11                    # of the measured lines (kept beside each page)
WIDTH = 700                     # the page is measured at this width


# ------------------------------------------------------------------ 1. the lines of handwriting

def _masks(img: Image.Image):
    im = img.convert("RGB")
    s = WIDTH / im.width
    im = im.resize((WIDTH, max(1, int(im.height * s))), Image.BILINEAR)
    a = np.asarray(im).astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    chroma = mx - mn
    ink = (mx < 175) | ((chroma > 110) & (mn < 130))     # a pen of any colour; light highlighter is not ink
    hl = (mx > 170) & (chroma > 30) & ~ink                # highlighter, coloured but light
    return ink, hl


def _components(mask: np.ndarray, min_px: int = 25) -> list[list[int]]:
    """Boxes [x1, y1, x2, y2, pixels] of the 8-connected parts of a mask, found row by row (union-find on runs)."""
    h, w = mask.shape
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs_prev: list[tuple[int, int, int]] = []
    runs_all: list[tuple[int, int, int, int]] = []
    for y in range(h):
        row = mask[y]
        if not row.any():
            runs_prev = []
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1
        cur = []
        for a, b in zip(starts, ends):
            k = len(parent)
            parent.append(k)
            for pa, pb, pk in runs_prev:
                if pa <= b + 1 and a <= pb + 1:
                    ra, rb = find(k), find(pk)
                    if ra != rb:
                        parent[ra] = rb
            cur.append((int(a), int(b), k))
            runs_all.append((y, int(a), int(b), k))
        runs_prev = cur
    boxes: dict[int, list[int]] = {}
    for y, a, b, k in runs_all:
        r = find(k)
        bx = boxes.get(r)
        if bx is None:
            boxes[r] = [a, y, b, y, b - a + 1]
        else:
            bx[0], bx[1], bx[2], bx[3], bx[4] = min(bx[0], a), min(bx[1], y), max(bx[2], b), max(bx[3], y), bx[4] + b - a + 1
    return [b for b in boxes.values() if b[4] >= min_px]


def find_lines(img: Image.Image) -> list[dict]:
    """The lines of handwriting on a page, top to bottom: box (fractions of the page), highlighter share, underline."""
    ink, hl = _masks(img)
    h, w = ink.shape
    if not ink.any():
        return []
    reach = max(6, int(0.012 * w))                        # letters and words of one line join; separate lines do not
    sm = ink.copy()
    for d in range(1, reach + 1):
        sm[:, d:] |= ink[:, :-d]
        sm[:, :-d] |= ink[:, d:]
    words = _components(sm, 25)
    # a stroke much taller than writing (a bracket, an arrow down, a line drawn across) is kept apart: joined to the
    # words beside it, it would make one line of several
    wh = float(np.median([b[3] - b[1] + 1 for b in words])) if words else 10.0
    tall = [b for b in words if b[3] - b[1] + 1 > 3.2 * wh]
    words = [b for b in words if b[3] - b[1] + 1 <= 3.2 * wh]
    # words into lines: overlapping in height, close side by side (a little further for words on the same line)
    words.sort(key=lambda b: b[0])
    lines: list[list[int]] = []
    for b in words:
        for ln in lines:
            ov = min(ln[3], b[3]) - max(ln[1], b[1])
            hb = b[3] - b[1] + 1
            # handwriting climbs and falls: the next word may sit a little higher or lower than the last
            near = abs((b[1] + b[3]) / 2 - _end_mid(ln, b)) < 0.7 * hb
            if (ov > 0.5 * min(ln[3] - ln[1] + 1, hb) or (near and ov > 0)) and b[0] - ln[2] < 0.1 * w:
                ln[0], ln[1], ln[2], ln[3] = min(ln[0], b[0]), min(ln[1], b[1]), max(ln[2], b[2]), max(ln[3], b[3])
                ln[4] += b[4]
                break
        else:
            lines.append(list(b) + [b[1], b[3]])         # the last two: the height of the line's last word
            continue
        ln[5], ln[6] = b[1], b[3]
    lines = [l[:5] for l in _attach_small(lines, wh)]
    out = []
    for kind, group in (("text", lines), ("tall", tall)):
        for x1, y1, x2, y2, px in group:
            if (x2 - x1) < 4 and (y2 - y1) < 4:
                continue
            box_hl = hl[y1:y2 + 1, x1:x2 + 1]
            # where along the line the highlighter is: a highlighted word at the start ("Examples") is a heading,
            # the words after it on the same line are not
            cols = box_hl.mean(0) if box_hl.size else np.zeros(1)
            prof = [round(float(c.mean()), 2) for c in np.array_split(cols, min(16, len(cols)))]
            out.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1 + 1) / w, "h": (y2 - y1 + 1) / h,
                        "hl": round(float(box_hl.mean()), 3) if box_hl.size else 0.0, "hlp": prof,
                        "ul": round(_underline(ink, x1, y1, x2, y2), 3), "ulc": round(_foot(ink, x1, y1, x2, y2), 3),
                        "ink": int(px), "tall": kind == "tall",
                        # an underline or a rule: a stroke much wider than tall, lower than writing
                        "thin": (y2 - y1 + 1) <= max(4, 0.0075 * h) and (x2 - x1) > 3 * max(1, y2 - y1)})
    return sorted(out, key=lambda l: (l["y"], l["x"]))


def _end_mid(ln: list[int], b: list[int]) -> float:
    """The middle height of the end of a line, where the next word would continue it."""
    return (ln[5] + ln[6]) / 2 if len(ln) > 6 else (ln[1] + ln[3]) / 2


def _attach_small(lines: list[list[int]], wh: float) -> list[list[int]]:
    """A small piece of writing beside a line (an index, a label on an arrow, "inv." over a loop) is part of that line,
    not a line of its own: it joins the nearest line of ordinary size it touches or almost touches."""
    def small(b):
        return (b[3] - b[1] + 1) < 0.75 * wh and (b[2] - b[0] + 1) < 2.5 * wh
    big = [b for b in lines if not small(b)]
    for b in [b for b in lines if small(b)]:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        best, dist = None, 1.2 * wh
        for g in big:
            dx = max(g[0] - cx, 0, cx - g[2])
            dy = max(g[1] - cy, 0, cy - g[3])
            d = (dx * dx + dy * dy) ** 0.5
            if d < dist:
                best, dist = g, d
        if best is None:
            big.append(b)                        # alone: a page number, a mark
        else:
            best[0], best[1], best[2], best[3] = min(best[0], b[0]), min(best[1], b[1]), max(best[2], b[2]), max(best[3], b[3])
            best[4] += b[4]
    return big


def _foot(ink: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    """How much of the line's width has ink along its foot. A line underlined word by word (each word its own
    stroke) covers much of it; letters that dip below the line (g, y, p) cover little."""
    hh = y2 - y1 + 1
    band = ink[max(y1, y2 - max(2, int(0.22 * hh))):y2 + 1, x1:x2 + 1]
    if not band.size:
        return 0.0
    cols = band.any(0)
    d = np.diff(np.concatenate(([0], cols.view(np.int8), [0])))
    s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    long_runs = (e - s)[(e - s) >= max(10, 0.9 * hh)]     # strokes along the foot, not a letter's tail
    return float(long_runs.sum()) / max(1, band.shape[1])


def _underline(ink: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    """How much of the line's width one unbroken stroke at its foot covers: an underline, not letters dipping below."""
    best = 0
    for y in range(max(y1, y2 - 4), y2 + 1):
        row = ink[y, x1:x2 + 1]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        if len(s):
            best = max(best, int((e - s).max()))
    return best / max(1, x2 - x1 + 1)


def rows_in_order(lines: list[dict]) -> list[dict]:
    """The lines read row by row: top to bottom, and left to right within a row."""
    med = float(np.median([l["h"] for l in lines if not l["thin"]] or [0.02]))
    rows: list[list[dict]] = []
    for l in sorted(lines, key=lambda l: l["y"] + l["h"] / 2):
        cy = l["y"] + l["h"] / 2
        if rows and abs(cy - np.mean([r["y"] + r["h"] / 2 for r in rows[-1]])) < 0.5 * med:
            rows[-1].append(l)
        else:
            rows.append([l])
    return [l for r in rows for l in sorted(r, key=lambda l: l["x"])]


def blocks_in_order(lines: list[dict]) -> list[dict]:
    """The lines in reading order. Lines one under the other, starting near the same place, form a block; blocks are
    read top to bottom, and side by side from left to right."""
    if not lines:
        return []
    med = float(np.median([ln["h"] for ln in lines if not ln["thin"]] or [0.02]))
    n = len(lines)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        a = lines[i]
        for j in range(i + 1, n):
            b = lines[j]
            gap = b["y"] - (a["y"] + a["h"])
            if gap > 2.2 * med:
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            if ox > 0 or abs(a["x"] - b["x"]) < 0.08:
                if gap > -0.6 * min(a["h"], b["h"]) or ox > 0.5 * min(a["w"], b["w"]):
                    parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    blocks = []
    for idx in groups.values():
        bl = [lines[i] for i in sorted(idx, key=lambda i: (lines[i]["y"], lines[i]["x"]))]
        blocks.append({"x": min(l["x"] for l in bl), "y": min(l["y"] for l in bl),
                       "y2": max(l["y"] + l["h"] for l in bl), "lines": bl})
    blocks.sort(key=lambda b: b["y"])
    ordered, band = [], []
    for b in blocks:                                       # side by side: a band of blocks that overlap in height
        if band and b["y"] < max(x["y2"] for x in band) - 0.3 * med:
            band.append(b)
        else:
            ordered += sorted(band, key=lambda x: x["x"])
            band = [b]
    ordered += sorted(band, key=lambda x: x["x"])
    return [ln for b in ordered for ln in b["lines"]]


def page_lines(image_path: Path, cache: Path | None = None) -> list[dict]:
    """The lines of a page in reading order, measured once and kept beside the page image."""
    if cache and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == VERSION:
                return data["lines"]
        except Exception:  # noqa: BLE001
            pass
    with Image.open(image_path) as img:
        lines = find_lines(img)
    if cache:
        cache.write_text(json.dumps({"version": VERSION, "lines": lines}), encoding="utf-8")
    return lines


# ------------------------------------------------------------------ 2. the transcript, in pieces

_MARKER = re.compile(r"\[(FIGURE(?:\s+f\d+)?|MARGIN|EXPLANATORY DRAWING)\s*:")


def pieces(text: str) -> list[dict]:
    """The transcript of a page cut into the pieces that stand on their own line: a line of text, a displayed
    formula (however many lines it takes), a drawing, a margin note. Blank lines only separate them."""
    out: list[dict] = []
    s, i, n = text or "", 0, len(text or "")
    buf = ""

    def flush():
        nonlocal buf
        for ln in buf.split("\n"):
            if ln.strip():
                out.append({"t": ln.strip(), "kind": "line"})
        buf = ""
    while i < n:
        if s.startswith("$$", i):
            j = s.find("$$", i + 2)
            if j > 0:
                head = buf.rsplit("\n", 1)
                if len(head) == 2 or not buf.strip():          # a display on its own line(s)
                    flush()
                    out.append({"t": s[i:j + 2].strip(), "kind": "math"})
                else:                                          # a display inside a line of text: one piece with it
                    buf += s[i:j + 2]
                i = j + 2
                continue
        m = _MARKER.match(s, i)
        if m:
            depth, j = 1, m.end()
            while j < n and depth:
                if s[j] == "[":
                    depth += 1
                elif s[j] == "]":
                    depth -= 1
                j += 1
            if not depth:
                kind = "figure" if m.group(1).startswith(("FIGURE", "EXPLANATORY")) else "margin"
                if kind == "figure" or not buf.strip() or buf.endswith("\n"):
                    flush()
                    out.append({"t": s[i:j].strip(), "kind": kind})
                else:
                    buf += s[i:j]
                i = j
                continue
        buf += s[i]
        i += 1
    flush()
    for p in out:
        p["chars"], p["rows"] = _size(p)
    return out


def _size(p: dict) -> tuple[float, int]:
    """How long the piece would be written by hand, in characters, and on how many lines."""
    t = p["t"]
    if p["kind"] == "figure":
        return 0.0, 1
    if p["kind"] == "margin":
        t = t.split(":", 1)[1].rstrip("]") if ":" in t else t
    rows = max(1, t.count("\\\\") + 1) if p["kind"] == "math" else 1
    plain = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", t)
    plain = re.sub(r"\\(?:text|mathrm|mathbf|mathcal|mathbb|operatorname|underline|overline|textbf|emph)\b", "", plain)
    plain = re.sub(r"\\(?:left|right|quad|qquad|,|;|!)", " ", plain)
    plain = re.sub(r"\\[A-Za-z]+", "x", plain)                  # a command is one symbol
    plain = re.sub(r"[{}$&]", "", plain)
    plain = re.sub(r"[_^]", "", plain)
    if rows > 1:
        longest = max((len(r.strip()) for r in plain.split("\\\\")), default=len(plain))
        return max(2.0, longest * 0.8), rows
    return max(1.0, len(plain.strip()) * (0.8 if p["kind"] == "math" else 1.0)), rows


# ------------------------------------------------------------------ 3. pieces matched to lines

SKIP_LINE = 0.9                 # a line of handwriting left without text (a page number, a crossed-out word, a mark)
SKIP_SMALL = 0.12               # ... a tiny mark or an underline
SKIP_PIECE = 1.8                # a piece of text without its line


def _merged(ls: list[dict]) -> dict:
    x1, y1 = min(l["x"] for l in ls), min(l["y"] for l in ls)
    x2, y2 = max(l["x"] + l["w"] for l in ls), max(l["y"] + l["h"] for l in ls)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1, "hl": max(l["hl"] for l in ls), "ul": max(l["ul"] for l in ls),
            "ulc": max(l.get("ulc", 0.0) for l in ls), "lead": _lead(ls[0])}


def _lead(line: dict) -> float:
    """How much of the start of a line is highlighted ("Def", "Example", "Theorem" marked at the start of the line)."""
    prof = line.get("hlp") or []
    k = 0
    while k < len(prof) and prof[k] >= 0.3:
        k += 1
    return k / len(prof) if prof and k < len(prof) else 0.0


def _stacked(group: list[dict]) -> bool:
    """Lines that can be one piece written over several lines: each below the one before, overlapping sideways."""
    for a, b in zip(group, group[1:]):
        if b["y"] < a["y"] + 0.3 * a["h"] or b["y"] - (a["y"] + a["h"]) > 2.5 * max(a["h"], b["h"]):
            return False
        if min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) < -0.05:
            return False
    return True


def _within(group: list[dict], rows: int) -> bool:
    """Lines that can be one displayed formula: together no taller than its rows of writing allow."""
    med = float(np.median([l["h"] for l in group]))
    top, bottom = min(l["y"] for l in group), max(l["y"] + l["h"] for l in group)
    return bottom - top <= (rows + 1) * 2.6 * max(med, 0.012)


def _align(ps: list[dict], ls: list[dict], scale: float):
    """Pieces against lines, in order: each piece takes one line or several (a formula on several lines), lines may
    be left without text and, at a higher price, a piece without its line. Returns (cost, [(piece, [lines])])."""
    n, m, K = len(ps), len(ls), 5
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    small = [l["thin"] or (l["w"] < 0.06 and l["h"] < 0.04 and l["hl"] < 0.3) for l in ls]
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == INF:
                continue
            if j < m:                                     # a line without text
                v = c + (SKIP_SMALL if small[j] else SKIP_LINE)
                if v < cost[i][j + 1]:
                    cost[i][j + 1], back[i][j + 1] = v, (i, j, 0)
            if i < n:
                p = ps[i]
                if p["kind"] == "figure" or p.get("box"):   # a drawing is placed by its own box
                    v = c
                    if v < cost[i + 1][j]:
                        cost[i + 1][j], back[i + 1][j] = v, (i, j, -1)
                    continue
                v = c + SKIP_PIECE
                if v < cost[i + 1][j]:
                    cost[i + 1][j], back[i + 1][j] = v, (i, j, -1)
                kmax = min(K if p["kind"] == "math" else 2, m - j)
                for k in range(1, kmax + 1):
                    group = ls[j:j + k]
                    if k > 1 and (small[j + k - 1] or not (_stacked(group) if p["kind"] != "math" else _within(group, p["rows"]))):
                        break
                    if ls[j].get("tall") and p["kind"] != "math":
                        break
                    g = _merged(group)
                    want = max(0.01, scale * p["chars"])
                    have = g["w"] if p["kind"] == "math" else sum(l["w"] for l in group)   # wrapped text: both lines
                    width = min(2.5, abs(math.log(max(0.005, have) / want)))
                    rows = abs(k - p["rows"]) * 0.45 if p["kind"] == "math" else (k - 1) * 0.3
                    # a short label ("Def.", "Ex", "Examples") is what the author highlights: it prefers such a line
                    label = -0.35 if k == 1 and p["kind"] == "line" and p["chars"] < 12 and (group[0]["hl"] >= 0.35 or _lead(group[0]) > 0.3) else 0.0
                    v = c + width + rows + label
                    if v < cost[i + 1][j + k]:
                        cost[i + 1][j + k], back[i + 1][j + k] = v, (i, j, k, 1)
                # several pieces written on one line ("Étale groupoid :" and the diagram after it)
                if j < m and not small[j] and not ls[j].get("tall"):
                    chars = p["chars"]
                    for r in (2, 3):
                        if i + r > n or ps[i + r - 1]["kind"] == "figure" or ps[i + r - 1].get("box"):
                            break
                        chars += ps[i + r - 1]["chars"]
                        want = max(0.01, scale * chars)
                        v = c + min(2.5, abs(math.log(max(0.005, ls[j]["w"]) / want))) + 0.35 * (r - 1)
                        if v < cost[i + r][j + 1]:
                            cost[i + r][j + 1], back[i + r][j + 1] = v, (i, j, 1, r)
    out, i, j = [], n, m
    while i or j:
        pi, pj, k, *rest = back[i][j]
        r = rest[0] if rest else 1
        if k > 0 and r > 1:                              # one line shared by r pieces, in proportion to their length
            line, total = ls[pj], sum(ps[pi + q]["chars"] for q in range(r)) or 1
            at = 0.0
            for q in range(r):
                share = ps[pi + q]["chars"] / total
                prof = line.get("hlp") or [line["hl"]]
                a, b = int(at * len(prof)), max(int(at * len(prof)) + 1, int(round((at + share) * len(prof))))
                part = prof[a:b] or prof
                out.append((pi + q, [{**line, "x": line["x"] + line["w"] * at, "w": line["w"] * share, "hlp": part, "shared": True,
                                      "src": id(line), "sq": q, "sr": r,
                                      "hl": sum(part) / len(part), "ul": line["ul"] if q == 0 else 0.0, "ulc": 0.0}]))
                at += share
        elif k > 0:
            out.append((pi, ls[pj:pj + k]))
        elif k == -1:
            out.append((pi, []))
        i, j = pi, pj
    out.sort(key=lambda x: x[0])
    return cost[n][m], out


def layout(text: str, lines: list[dict], figure_boxes: dict[int, list[float]] | None = None,
           assign: dict[int, list[int]] | None = None) -> dict:
    """Where each piece of a page's transcript stands. figure_boxes: k -> [y0, x0, y1, x1] (thousandths), the boxes
    the figure step found for the k-th drawing of the page. assign: piece -> the lines it stands on (indices into
    lines), when a model has matched them (placing.py); otherwise the pieces are matched by their lengths."""
    ps = pieces(text)
    if not ps:
        return {"ok": False, "items": []}
    k = 0
    for p in ps:
        if p["kind"] == "figure":
            b = (figure_boxes or {}).get(k)
            if b:
                p["box"] = {"x": b[1] / 1000, "y": b[0] / 1000, "w": (b[3] - b[1]) / 1000, "h": (b[2] - b[0]) / 1000}
            k += 1
    reserved = _page_numbers(ps, lines)                 # a page number: in its corner, not shared with a heading
    inside = [p["box"] for p in ps if p.get("box")]

    def in_figure(l):
        cx, cy = l["x"] + l["w"] / 2, l["y"] + l["h"] / 2
        return any(b["x"] <= cx <= b["x"] + b["w"] and b["y"] <= cy <= b["y"] + b["h"] for b in inside)
    free = [l for l in lines if not in_figure(l) and id(l) not in reserved]
    texty = [p for p in ps if p["kind"] != "figure"]
    if not free or not texty:
        return {"ok": False, "items": [_item(p, None, 0.02) for p in ps]}
    best = None
    for order in (rows_in_order, blocks_in_order):      # how the author's lines are read: the order that fits best
        ls = order(free)
        scale = sum(l["w"] for l in ls if not l["thin"]) / max(1.0, sum(p["chars"] for p in texty))
        for _ in range(3):                              # the size of the handwriting, learnt from the lines matched
            total, pairs = _align(ps, ls, scale)
            ratios = [_merged(g)["w"] / ps[i]["chars"] for i, g in pairs if len(g) == 1 and ps[i]["chars"] >= 6]
            if len(ratios) < 3:
                break
            new = float(np.median(ratios))
            if abs(new - scale) < 0.02 * scale:
                break
            scale = new
        if best is None or total < best[0]:
            best = (total, pairs)
    total, pairs = best
    med = float(np.median([l["h"] for l in ls if not l["thin"]] or [0.02]))
    if assign is not None:                              # the lines a model matched to the pieces, seeing the page
        # a piece the model left out keeps the line the matching by length gave it, if the model gave that line to
        # no other piece
        used = {id(lines[j]) for js in assign.values() for j in js if 0 <= j < len(lines)}
        bylen = dict(_unshare(pairs, free))
        pairs = []
        for i, g in _assigned(ps, lines, assign):
            if not g and not ps[i].get("box") and ps[i]["kind"] != "figure":
                alt = bylen.get(i) or []
                if alt and not any((l["src"] if l.get("shared") else id(l)) in used for l in alt):
                    g = alt
            pairs.append((i, g))
        total = 0.0
    else:
        pairs = _unshare(pairs, free)
    placed = {i: g for i, g in pairs}
    items, last = [], None
    matched = sum(1 for p_i, g in pairs if g)
    for i, p in enumerate(ps):
        g = placed.get(i)
        if p.get("box"):
            box = {**p["box"], "hl": 0.0, "ul": 0.0}
        elif g:
            box = _merged(g)
            under = [l for l in lines if l["thin"] and 0 <= l["y"] - (box["y"] + box["h"]) < 0.8 * med
                     and min(l["x"] + l["w"], box["x"] + box["w"]) - max(l["x"], box["x"]) > 0.4 * box["w"]]
            if under:
                box["ul"] = 1.0                          # an underline drawn as its own stroke below the words
        else:
            box = None
        if g and len(g) > 1 and p["kind"] in ("line", "margin") and not p.get("box"):
            parts = _wrap(p["t"], [l["w"] for l in g])  # a sentence the author wrote over several lines: each on its own
            if parts:
                for q, (part, l) in enumerate(zip(parts, g)):
                    sub = _item({**p, "t": part, "chars": len(part)}, {**_merged([l]), "ul": box.get("ul", 0) if q == 0 else 0}, med)
                    sub["cont"] = q > 0
                    items.append(sub)
                last = items[-1]
                continue
        it = _item(p, box, med)
        if box is None and last is not None:            # not matched: right after the piece before
            it.update(x=last["x"], y=last["y"] + last["h"] + 0.002, w=last["w"], h=med, placed=False)
        items.append(it)
        last = it
    texty_n = len(texty)
    shared = sum(1 for _, g in pairs if len(g) == 1 and g[0].get("shared"))
    written = sum(1 for l in free if not l["thin"])
    if assign is not None:
        return {"ok": matched >= 0.6 * texty_n, "items": items, "cost": 0.0, "med": med, "doubt": False, "ai": True}
    # how sure the matching by length is: a page where every piece found a line of about its length is sure; any
    # piece without its line, or lines of writing left over, or a poor fit, makes it doubtful
    spare = written - len({id(l) for _, g in pairs for l in g if not l.get("shared")} | {l["src"] for _, g in pairs for l in g if l.get("shared")})
    unplaced = sum(1 for it in items if not it.get("placed") and it["kind"] != "figure")
    doubt = bool(unplaced or spare >= 2 or total / max(1, texty_n) > 0.2)
    # a label and its formula often share a line ("Thm (Brown '92)  H(V) = 0"), but far more pieces than lines of
    # handwriting means that the text and the page do not belong together
    ok = (texty_n and matched >= 0.6 * texty_n and texty_n <= 1.8 * written + 2 and shared <= 0.6 * texty_n
          and total / max(1, texty_n) < 1.2)
    return {"ok": bool(ok), "items": items, "cost": round(total / max(1, texty_n), 2), "med": med, "doubt": doubt}


_HANG = re.compile(r"\\(?:overbrace|underbrace|overset|underset|stackrel|xrightarrow|xleftarrow)")


def _assigned(ps: list[dict], lines: list[dict], assign: dict[int, list[int]]) -> list:
    """(piece, [lines]) from the lines a model gave each piece. A line that several pieces stand on (a label and the
    formula after it; a label and the first row of a formula written over several lines) is shared side by side, in
    the order of the transcript, each in proportion to its length; a piece on several lines takes them in reading
    order."""
    users: dict[int, list[int]] = {}
    for i, idx in sorted(assign.items()):
        if i < len(ps) and not ps[i].get("box"):
            for j in idx:
                if 0 <= j < len(lines):
                    users.setdefault(j, []).append(i)

    def part(i: int, j: int) -> dict:
        line, sharers = lines[j], users[j]
        if len(sharers) < 2:
            return line
        total = sum(ps[q]["chars"] for q in sharers) or 1
        at = sum(ps[q]["chars"] for q in sharers[:sharers.index(i)]) / total
        share = ps[i]["chars"] / total
        prof = line.get("hlp") or [line["hl"]]
        a = int(at * len(prof))
        cut = prof[a:max(a + 1, int(round((at + share) * len(prof))))] or prof
        return {**line, "x": line["x"] + line["w"] * at, "w": line["w"] * share, "shared": True, "src": id(line),
                "hlp": cut, "hl": sum(cut) / len(cut), "ul": line["ul"] if at == 0 else 0.0, "ulc": 0.0}
    out = []
    for i, p in enumerate(ps):
        idx = [j for j in assign.get(i, []) if 0 <= j < len(lines)]
        if p.get("box") or not idx:
            out.append((i, []))
            continue
        g = [part(i, j) for j in sorted(set(idx), key=lambda j: (lines[j]["y"], lines[j]["x"]))]
        # lines side by side on one row are one line of the author's (the gap between words split it): one box;
        # rows one under the other stay apart, so that a sentence that goes on below is cut where it goes on
        rows: list[list[dict]] = []
        for l in g:
            row = next((r for r in rows if any(min(q["y"] + q["h"], l["y"] + l["h"]) - max(q["y"], l["y"])
                                               > 0.4 * min(q["h"], l["h"]) for q in r)), None)
            if row is None:
                rows.append([l])
            else:
                row.append(l)
        if len(g) > 1:
            rows = [sorted(r, key=lambda q: q["x"]) for r in sorted(rows, key=lambda r: min(q["y"] for q in r))]
            # words written over a formula (over a brace, over an arrow) are part of the piece but not a line of
            # it: the piece starts on its main line, never on the smaller label above it
            if p["kind"] in ("line", "margin") and _HANG.search(p["t"]):
                tall = lambda r: max(q["h"] for q in r)
                while len(rows) > 1 and tall(rows[0]) < 0.75 * tall(rows[1]):
                    rows.pop(0)
            g = [{**{k: v for k, v in r[0].items() if k not in ("shared", "src")}, **(_merged(r) if len(r) > 1 else {})} for r in rows]
        out.append((i, g))
    return out


def _unshare(pairs: list, lines: list[dict]) -> list:
    """Two pieces put on one line, while a line of handwriting on the same row, beside it, got no text: the author
    wrote them apart (a formula on the left, a map on the right). The piece on that side takes that line, and the
    other the whole of its own."""
    used = {id(l) for _, g in pairs for l in g if not l.get("shared")} | {l["src"] for _, g in pairs for l in g if l.get("shared")}
    spare = [l for l in lines if id(l) not in used and not l["thin"] and not l.get("tall")]
    by_src: dict[int, list] = {}
    for k, (i, g) in enumerate(pairs):
        if len(g) == 1 and g[0].get("shared") and g[0].get("sr") == 2:
            by_src.setdefault(g[0]["src"], []).append(k)
    src_line = {id(l): l for l in lines}
    for sid, ks in by_src.items():
        line = src_line.get(sid)
        if not line or len(ks) != 2:
            continue
        cy, hh = line["y"] + line["h"] / 2, line["h"]
        row = [u for u in spare if abs(u["y"] + u["h"] / 2 - cy) < 0.6 * max(hh, u["h"])]
        right = [u for u in row if u["x"] >= line["x"] + line["w"]]
        left = [u for u in row if u["x"] + u["w"] <= line["x"]]
        first, second = sorted(ks, key=lambda k: pairs[k][1][0]["sq"])
        whole = {k2: v for k2, v in line.items()}
        if right:
            u = min(right, key=lambda u: u["x"])
            pairs[first] = (pairs[first][0], [whole]); pairs[second] = (pairs[second][0], [u])
        elif left:
            u = max(left, key=lambda u: u["x"])
            pairs[first] = (pairs[first][0], [u]); pairs[second] = (pairs[second][0], [whole])
        else:
            continue
        spare.remove(u)
    return pairs


def _wrap(text: str, widths: list[float]) -> list[str] | None:
    """A line of the transcript cut where the author's handwriting goes on to the next line: at spaces outside the
    formulas, as near as can be to where each written line ends. None when it cannot be cut so."""
    cuts, depth, inmath = [], 0, False
    for i, ch in enumerate(text):
        if ch == "$" and (i == 0 or text[i - 1] != "\\"):
            inmath = not inmath
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth = max(0, depth - 1)
        elif ch == " " and not inmath and depth == 0:
            cuts.append(i)
    if len(cuts) < len(widths) - 1:
        return None
    total, at, parts, start = sum(widths) or 1, 0.0, [], 0
    for w in widths[:-1]:
        at += w / total
        want = at * len(text)
        c = min((x for x in cuts if x > start), key=lambda x: abs(x - want), default=None)
        if c is None:
            return None
        parts.append(text[start:c].strip())
        start = c + 1
    parts.append(text[start:].strip())
    return parts if all(parts) else None


_NUMBER = re.compile(r"^\(?\s*\d{1,3}\s*\)?\.?$")


def _page_numbers(ps: list[dict], lines: list[dict]) -> set[int]:
    """A page number in the transcript (a bare number at its start or end) and a small mark alone in a corner of the
    page: they belong together. Matched first, so that the number is never laid over the start of a heading."""
    texty = [i for i, p in enumerate(ps) if p["kind"] != "figure"]
    if not texty:
        return set()
    used: set[int] = set()
    for i, top in ((texty[0], True), (texty[-1], False)):
        p = ps[i]
        if p.get("box") or p["kind"] != "line" or not _NUMBER.match(p["t"].strip()):
            continue
        corner = [l for l in lines if id(l) not in used and not l["thin"] and l["w"] < 0.08
                  and ((l["y"] < 0.15) if top else (l["y"] + l["h"] > 0.85))
                  and (l["x"] > 0.6 or l["x"] + l["w"] < 0.4)]
        if not corner:
            continue
        l = min(corner, key=lambda c: c["y"]) if top else max(corner, key=lambda c: c["y"])
        p["box"] = {"x": l["x"], "y": l["y"], "w": l["w"], "h": l["h"]}
        used.add(id(l))
    return used


def _item(p: dict, box: dict | None, med: float) -> dict:
    it = {"t": p["t"], "kind": p["kind"], "placed": box is not None, "rows": p.get("rows", 1)}
    if box:
        it.update(x=round(box["x"], 4), y=round(box["y"], 4), w=round(box["w"], 4), h=round(box["h"], 4))
        short = p["kind"] in ("line", "margin") and p["chars"] < 45 and box["h"] < 2 * med
        underlined = box.get("ul", 0) >= 0.6 or box.get("ulc", 0) >= 0.26      # one stroke, or word by word
        it["head"] = bool(short and (box.get("hl", 0) >= 0.35 or underlined))
        it["ul"] = bool(short and underlined and box.get("hl", 0) < 0.35)
        # a title or heading written in the middle of the page is shown in the middle
        it["center"] = bool(p["kind"] == "line" and abs(box["x"] + box["w"] / 2 - 0.5) < 0.06
                            and 0.08 <= box["w"] < 0.6 and box["x"] > 0.2 and (it["head"] or box["y"] < 0.25))
        # the title: written large, near the top, in the middle of the page
        it["big"] = bool(p["kind"] == "line" and box["y"] < 0.2 and abs(box["x"] + box["w"] / 2 - 0.5) < 0.15
                         and box["h"] > 1.25 * med and p["chars"] < 80)
        it["head"] = it["head"] or it["big"]
        it["center"] = it["center"] and it["head"]
        # the start of the line highlighted, the rest not: its first words are shown as a label
        it["lead"] = round(box.get("lead", 0.0), 2) if not it["head"] and 0.08 <= box.get("lead", 0.0) < 0.6 else 0.0
    else:
        it.update(x=0.08, y=0.0, w=0.8, h=med, head=False, big=False)
    return it
