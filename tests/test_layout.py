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
assert items["The partial "]["h"] > 0.04, "the sentence takes both its lines"
assert items["Embedding gr"]["big"] and items["Embedding gr"]["head"], "the title, large in the middle at the top"
assert items["Examples"]["head"], "a highlighted word is a heading"
assert items["See below"]["head"], "an underlined word is a heading"
assert not items["Joint work w"]["head"] and not items["Oberseminar "]["head"]
print("1. a drawn page: title, side-by-side blocks, highlighted and underlined headings and a wrapped line all placed")

# the pieces of a transcript: a displayed formula over several lines and a drawing are one piece each
ps = layout.pieces("Def A group\n\n$$\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}$$\nnext\n[FIGURE: a square [with a corner]]\nlast")
assert [p["kind"] for p in ps] == ["line", "math", "line", "figure", "line"] and ps[1]["rows"] == 2, ps
print("2. a transcript cut into its pieces: lines, a formula on two lines, a drawing")

# a page whose text does not fit its handwriting at all is said to be so, and shown plain
res = layout.layout("\n".join(f"line number {k} of a page that is much longer than what was written" for k in range(30)), layout.find_lines(im), {})
assert not res["ok"]
print("3. a transcript that does not fit the page is shown as plain text")
print("layout ok")
