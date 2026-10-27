# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The checks of a redrawn figure, with a scripted model:
  1. a difference the model finds is corrected once, and the correction is checked again;
  2. a correction that moves the figure away from the drawing is refused, and the difference stays flagged;
  3. a redrawing that matches the drawing only in part is kept and says how far (no model draws it instead);
  4. a failure of the measurement is shown on the figure, with the author's drawing;
  5. a drawing the model does not find on the page is reported, not guessed.
    python tests/test_figflow.py"""
import io, json, re
from helpers import isolate
work = isolate("scriptavera_figflow_")
from PIL import Image, ImageDraw
from scriptavera import config, figflow, figmeasure, ingest, paper, store


def drawing() -> bytes:
    """A scan-like page with one drawing: a triangle, a circle and an arrow, in a pen about 5 pixels wide."""
    im = Image.new("RGB", (1200, 900), (250, 248, 242))
    d = ImageDraw.Draw(im)
    ink = (30, 30, 40)
    d.line([(200, 650), (700, 650), (350, 250), (200, 650)], fill=ink, width=5, joint="curve")
    d.ellipse([890, 190, 1010, 310], outline=ink, width=5)
    d.line([(800, 470), (1100, 470)], fill=ink, width=5)
    d.line([(1060, 440), (1100, 470), (1060, 500)], fill=ink, width=5, joint="curve")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def project():
    pid = store.new_id()
    d = config.PROJECTS_DIR / pid
    (d / "originals").mkdir(parents=True)
    data = drawing()
    (d / "originals" / "01-figure.png").write_bytes(data)
    pages = ingest.ingest([("figure.png", data)], d / "pages")
    (d / "state.json").write_text(json.dumps({"id": pid, "pages": pages, "stages": {}, "meta": {}}))
    return store.Project(pid), pages[0]


class R:
    def __init__(self, text):
        self.text, self.model, self.provider = text, "test-model", "test"


DRAWN = "\\begin{tikzpicture}\n\\draw (0,0) -- (4,0) -- (1.2,3.2) -- cycle;\n\\draw (6,2.4) circle[radius=0.5];\n\\draw[->] (5,1) -- (7.5,1);\n\\end{tikzpicture}"


def scripted(checks, fix=None):
    """The model's answers: the checks in turn, a correction made by `fix` (from the code), a drawing of its own."""
    calls = []
    checks = list(checks)

    def ask(role, system, user, images=None, **kw):
        label = kw.get("label", "")
        calls.append(label)
        if label.startswith("Find the drawings"):
            return R(json.dumps({"drawings": [{"n": 1, "found": True, "box": [190, 130, 770, 950]}]}))
        if label.startswith("Read the labels"):
            return R(json.dumps({"labels": [], "drawing": [], "ignore": [], "missed": [], "caption": "A triangle, a circle and an arrow."}))
        if label.startswith("Check figure"):
            return R(json.dumps(checks.pop(0) if checks else {"same": True, "problems": []}))
        if label.startswith("Correct figure"):
            code = re.search(r"\\begin\{tikzpicture\}.*\\end\{tikzpicture\}", user, re.S).group(0)
            return R("<tikz>\n" + fix(code) + "\n</tikz>")
        if label.startswith("Draw figure"):
            return R("<caption>A triangle, a circle and an arrow.</caption>\n<tikz>\n" + DRAWN + "\n</tikz>")
        return R("")
    return ask, calls


def run(ask):
    p, page = project()
    rec = {"id": "f1", "page": 1, "k": 0, "of": 1, "desc": "A triangle, a circle and an arrow."}
    figflow.draw(p, rec, ask, "(none)", page, (p.dir / "pages" / page["file"]).read_bytes())
    return rec


PROBLEM = {"same": False, "problems": [{"what": "the arrow has no head", "where": "on the right", "fix": "give it a head"}]}
latex = paper.find_engine() in ("pdflatex", "xelatex", "lualatex")

if latex:
    # 1. corrected and checked again
    ask, calls = scripted([PROBLEM, {"same": True, "problems": []}], fix=lambda code: code.replace("% the drawing", "% the drawing\n% corrected"))
    rec = run(ask)
    assert rec["method"] == "measured" and rec["tikz_ok"], rec
    assert rec["local"]["covered"] >= 0.95 and rec["local"]["precise"] >= 0.95, rec["local"]
    assert "% corrected" in rec["tikz"] and rec["check"]["same"] is True, rec["check"]
    assert rec["check"]["corrected"] == ["the arrow has no head — on the right — give it a head"], rec["check"]
    assert sum(c.startswith("Check figure") for c in calls) == 2 and sum(c.startswith("Correct figure") for c in calls) == 1, calls
    assert "To[" in rec["tikz"] or "->" in rec["tikz"], "the arrow keeps its head"
    print("1. a difference the model finds is corrected once and checked again")

    # 2. a correction that moves the figure away is refused
    ask, calls = scripted([PROBLEM], fix=lambda code: "\\begin{tikzpicture}\n\\draw (0,0) -- (0.3,0);\n\\end{tikzpicture}")
    rec = run(ask)
    assert rec["method"] == "measured" and "(0.3,0)" not in rec["tikz"], rec["tikz"]
    assert any("refused" in n for n in rec["notes"]), rec["notes"]
    assert rec["check"]["problems"] and not rec["check"].get("same"), rec["check"]
    print("2. a correction that moves the figure away from the drawing is refused; the difference stays for the author")
else:
    print("1-2. skipped: no LaTeX engine on this computer, so no redrawing can be compiled and compared")

# 3. a redrawing that matches the drawing only in part is kept, and says how far; no model draws it instead
real = figflow._local_check


def half(sc, m):
    out = real(sc, m)
    out["covered"] = 0.5
    return out


figflow._local_check = half
ask, calls = scripted([])
rec = run(ask)
figflow._local_check = real
assert rec["method"] == "measured" and rec["tikz"].strip() != DRAWN, rec
assert any("only 50%" in n for n in rec["notes"]), rec["notes"]
assert not any(c.startswith("Draw figure") for c in calls), calls
print("3. a redrawing that matches the drawing only in part is kept and says how far, for the author to see")

# 4. a failure is not covered up: the figure keeps the error, the author's drawing is shown, no model draws it
from scriptavera import pipeline
real_measure = figmeasure.measure


def broken(ink):
    raise ValueError("simulated")


figmeasure.measure = broken
ask, calls = scripted([])
p, page = project()
rec = {"id": "f1", "page": 1, "k": 0, "of": 1, "desc": "A triangle, a circle and an arrow."}
pipeline._draw_figure(p, rec, ask, {})
figmeasure.measure = real_measure
assert rec["tikz_ok"] is False and not rec["tikz"] and "simulated" in rec["error"], rec
assert not any(c.startswith("Draw figure") for c in calls), calls
assert (p.dir / "figures" / "f1_orig.jpg").exists()
print("4. a failure of the measurement is shown on the figure, with the author's own drawing; nothing is drawn instead")

# 5. a drawing the model does not find on the page is said to be missing, not guessed
p, page = project()
rec = {"id": "f1", "page": 1, "k": 0, "of": 1, "desc": "A triangle, a circle and an arrow."}


def nothing(role, system, user, images=None, **kw):
    if kw.get("label", "").startswith("Find the drawings"):
        return R(json.dumps({"drawings": [{"n": 1, "found": False, "box": None}]}))
    raise AssertionError("no other call: " + kw.get("label", ""))


pipeline._draw_figure(p, rec, nothing, {})
assert rec["tikz_ok"] is False and "did not find" in rec["error"], rec
print("5. a drawing the model does not find on the page is reported, not guessed")
print("figflow ok")
