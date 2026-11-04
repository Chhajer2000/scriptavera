# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Where each line of the transcript stands on the handwritten page, measured from the image with no model: a page
drawn with a title in the middle, two blocks side by side, a highlighted heading, a line written over two lines, a
small mark and an underline; the transcript's pieces must land where they were drawn."""
import json
from helpers import isolate
isolate("scriptavera_layout_")
from PIL import Image, ImageDraw
from scriptavera import layout

W, H = 1000, 1400
im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
INK = (20, 30, 110)


def words(x, y, lengths, size=22):
    """A line of 'handwriting': words as strokes of ink, one per letter group, with gaps between words."""
    for n in lengths:
        for k in range(n):
            d.line((x + 9 * k, y + size, x + 9 * k + 6, y + (4 if k % 2 else 0)), fill=INK, width=3)
        x += 9 * n + 18
    return x


words(320, 100, [9, 6, 4, 7, 6], size=30)                         # the title, large, in the middle
d.rectangle((90, 230, 270, 262), fill=(255, 236, 140))               # highlighter behind the heading
words(100, 236, [8])                                                  # "Examples" (highlighted)
words(100, 300, [5, 4, 4])                                            # left block, three lines
words(100, 340, [7, 2, 9])
words(100, 380, [6, 10])
words(600, 300, [11, 8, 3])                                           # right block, beside it
words(640, 340, [9])
words(650, 380, [2, 5, 4])
words(100, 470, [5, 7, 4, 9, 6, 8, 5, 7])                             # one long sentence, written over two lines
words(130, 510, [6, 5, 8])
d.line((100, 600, 300, 600), fill=INK, width=3)                       # a rule across, no text
words(100, 640, [3, 6]); d.line((100, 668, 220, 668), fill=INK, width=3)    # an underlined word
words(880, 40, [1])                                                    # a page number in the corner
TEXT = """Embedding groups into acyclic groups

Examples

Joint work with
Xiaolei Wu (Shanghai)
arXiv: 2510.16879

Oberseminar topology and geometry
Göttingen
15 April 2026

The partial sums of the series are bounded by the integral of the function
over the interval from one to n.

See below"""

lines = layout.blocks_in_order(layout.find_lines(im))
res = layout.layout(TEXT, layout.find_lines(im), {})
items = {it["t"][:12]: it for it in res["items"]}
assert res["ok"], res
for key, (x, y) in {"Embedding gr": (0.32, 0.07), "Examples": (0.10, 0.17), "Joint work w": (0.10, 0.21),
                    "Xiaolei Wu (": (0.10, 0.24), "arXiv: 2510.": (0.10, 0.27), "Oberseminar ": (0.60, 0.21),
                    "Göttingen": (0.64, 0.24), "15 April 202": (0.65, 0.27), "The partial ": (0.10, 0.335),
                    "See below": (0.10, 0.457)}.items():
    it = items[key]
    assert abs(it["x"] - x) < 0.03 and abs(it["y"] - y) < 0.02, (key, it["x"], it["y"], x, y)
cont = [it for it in res["items"] if it.get("cont")]
assert cont and cont[0]["y"] > items["The partial "]["y"] + 0.02 and abs(cont[0]["x"] - 0.13) < 0.03, \
    "the sentence is cut where the handwriting goes on to the next line, each part on its own line"
assert (items["The partial "]["t"] + " " + cont[0]["t"]).startswith("The partial sums of the series are bounded")
assert items["Embedding gr"]["big"] and items["Embedding gr"]["head"], "the title, large in the middle at the top"
assert items["Examples"]["head"], "a highlighted word is a heading"
assert items["See below"]["head"], "an underlined word is a heading"
assert not items["Joint work w"]["head"] and not items["Oberseminar "]["head"]
print("1. a drawn page: title, side-by-side blocks, highlighted and underlined headings and a wrapped line all placed")

# a page number in the corner is matched to the mark in the corner, never to a slice of the heading beside it
lines = [{"x": 0.88, "y": 0.06, "w": 0.02, "h": 0.015, "hl": 0, "hlp": [0], "ul": 0, "ulc": 0, "ink": 40, "tall": False, "thin": False},
         {"x": 0.10, "y": 0.10, "w": 0.40, "h": 0.03, "hl": 0, "hlp": [0], "ul": 0, "ulc": 0, "ink": 900, "tall": False, "thin": False},
         {"x": 0.12, "y": 0.18, "w": 0.55, "h": 0.03, "hl": 0, "hlp": [0], "ul": 0, "ulc": 0, "ink": 900, "tall": False, "thin": False}]
r2 = layout.layout("1\n\n(I) Embeddings into acyclic groups\n\nDef A group G is acyclic if its homology vanishes", lines, {})
assert r2["items"][0]["t"] == "1" and r2["items"][0]["x"] > 0.85 and r2["items"][1]["x"] < 0.15, r2["items"][:2]
assert layout._wrap("$F(G)$ = group of homeo. of $G^{(0)}$ that locally look like elements of $G$", [0.6, 0.2]) == \
    ["$F(G)$ = group of homeo. of $G^{(0)}$ that locally look", "like elements of $G$"], "never inside a formula"
print("1b. a page number goes to its corner; a sentence is cut only outside its formulas")

# the pieces of a transcript: a displayed formula over several lines and a drawing are one piece each
ps = layout.pieces("Def A group\n\n$$\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}$$\nnext\n[FIGURE: a square [with a corner]]\nlast")
assert [p["kind"] for p in ps] == ["line", "math", "line", "figure", "line"] and ps[1]["rows"] == 2, ps
print("2. a transcript cut into its pieces: lines, a formula on two lines, a drawing")

# a page whose text does not fit its handwriting at all is said to be so, and shown plain
res = layout.layout("\n".join(f"line number {k} of a page that is much longer than what was written" for k in range(30)), layout.find_lines(im), {})
assert not res["ok"]
print("3. a transcript that does not fit the page is shown as plain text")

# 4. a page placed by a model that sees it: the pieces go to the lines it names, whatever their length says; two
# pieces it puts on one line share that line side by side; a piece it cannot find takes the place after the one before
from scriptavera import placing
found = layout.find_lines(im)
ps = layout.pieces(TEXT)
idx = {p["t"][:12]: i for i, p in enumerate(ps)}
def line_at(x, y):
    return min(range(len(found)), key=lambda j: abs(found[j]["x"] - x) + abs(found[j]["y"] - y) if not found[j]["thin"] else 9)
assign = {i: [line_at(0.10, 0.21 + 0.035 * (k % 3))] for k, i in enumerate(idx[t] for t in ("Joint work w", "Xiaolei Wu (", "arXiv: 2510."))}
assign[idx["Oberseminar "]] = [line_at(0.10, 0.21)]               # said to share the line of "Joint work with"
assign[idx["Embedding gr"]] = [line_at(0.32, 0.07)]
r4 = layout.layout(TEXT, found, {}, assign=assign)
it4 = {it["t"][:12]: it for it in r4["items"]}
assert r4.get("ai") and not r4.get("doubt")
a, b = it4["Joint work w"], it4["Oberseminar "]
assert abs(a["y"] - b["y"]) < 0.005 and b["x"] > a["x"] + 0.05, (a, b)
assert abs(it4["Embedding gr"]["y"] - 0.07) < 0.02 and it4["Embedding gr"]["big"]
assert it4["Göttingen"]["placed"] and abs(it4["Göttingen"]["x"] - 0.64) < 0.03, "not named by the model: where its length put it"
# the model gives a piece two lines side by side on one row (the gap between two words split the line): one box
right = line_at(0.60, 0.21)
r5 = layout.layout(TEXT, found, {}, assign={**assign, idx["Oberseminar "]: [right, line_at(0.10, 0.21)]})
o5 = [it for it in r5["items"] if it["t"].startswith("Oberseminar")]
assert len(o5) == 1, [it["t"] for it in r5["items"]]
assert placing.parse("<places>\nP1: L2\nP2: L3 L4\nP3: none\nP9: L1\nP2x\n</places>", 3, 4) == {1: [2], 2: [3, 4], 3: []}
img, order = placing.framed(im, found, set())
assert img[:2] == b"\xff\xd8" and len(order) == sum(1 for l in found if not l["thin"])
print("4. a page placed by a model: its lines as the model says, shared lines side by side, a piece it left out where its")
print("   length put it, two lines on one row one box, the framed page numbered")
print("layout ok")
