# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Drawings: up to the check of the transcript every drawing is copied as the author drew it, and nothing is decided
about the paper. At the structure, with the whole paper in view, each drawing is decided: printed as a figure, or one
that only explains an argument (the writers read it; it is never printed). Each printed figure is then drawn again
for print before the proofs are written, and the author confirms it. Anything the code cannot square is put to the
author, who can always print a drawing after all."""
import json
import os
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"                # the proof on page 3 carries the author's own picture
os.environ["SCRIPTAVERA_MOCK_SKETCH"] = "1"                  # and the assessment calls that proof a sketch
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_drawings_")
import tests.simulator as simulator
from scriptavera import drafting, drawings as D, figpro, paper as paperlib, pipeline, writing

# ---- where a drawing sits, found from the labels before it
for text, kind in (("Claim: $x$.\n\nProof: Let $k \\ge 2$. Hence\n$$a$$\n", "proof"), ("Sketch of proof: compare.\n", "sketch"),
                   ("Proof (sketch). We compare.\n", "sketch"), ("Lemma 2.1 (bound). For all $n$.\n", "statement"),
                   ("Definition 3. A set is nice if\n", "definition"), ("Problem: Find bounds.\n", "problem"),
                   ("**Proof.** Let x.\n", "proof"), ("Note that x is big\n", "none"), ("Proof: Let x. $\\square$\nNext", "after")):
    assert D.where(text)[0] == kind, (text, D.where(text))
pages = {1: "Problem: Find bounds.\n\n[FIGURE: rectangles under a curve, Fig. 1]\n\nIdea: compare.",
         3: "Claim: $H_n \\le 1 + \\log n$.\n\nProof: Let $k \\ge 2$. Hence\n$$\\frac1k \\le \\int_{k-1}^k \\frac{dx}{x}$$\n"
            "[FIGURE: the graph of $1/x$ with a rectangle under it]\nSumming gives the claim, as in the picture above."}
one = D.item({"id": "f1", "page": 1, "k": 0, "of": 1, "desc": "rectangles under a curve, Fig. 1"}, pages)
two = D.item({"id": "f2", "page": 3, "k": 0, "of": 1, "desc": "the graph of $1/x$ with a rectangle under it"}, pages)
assert one["kind"] == "problem" and one["numbered"] and two["kind"] == "proof" and not two["numbered"]
assert D.HERE in two["passage"] and "Summing gives the claim" in two["passage"] and "[another drawing: rectangles" in two["passage"]
assert D.mentions(pages) == ['page 3: "Summing gives the claim, as in the picture above."']
# the decision rests on the drawing, its passage and its statement: not on the topic's wording or a section's title
x = {"id": "R2", "statement": "$H_n \\le 1 + \\log n$"}
k = D.plan_key(two, x, {"title": "Upper bounds"}, "Bounds for sums")
assert k == D.plan_key(two, x, {"title": "Another title"}, "Bounds for partial sums, reworded")
assert k != D.plan_key(two, {**x, "statement": "$H_n \\le 2 + \\log n$"}, None, "") != D.plan_key(one, x, None, "")

# ---- a verdict is held to its own reasons: what the code cannot square is put to the author
inproof = {"kind": "proof", "where": "inside a proof", "numbered": False}
v = D.settle(inproof, {"use": "explanation", "sure": True, "why": "It pictures the step.", "needed": "none"})
assert v["use"] == "explanation" and v["sure"] and not v["doubt"]
for row, it, why in (({"use": "explanation", "sure": True, "needed": "the points A and B"}, inproof, "rely on it"),
                     ({"use": "explanation", "sure": True, "needed": "none"}, {**inproof, "numbered": True}, "numbered"),
                     ({"use": "paper", "sure": True, "needed": "none"}, inproof, "names nothing"),
                     ({"use": "annotation", "sure": True}, inproof, "labels on a formula"),
                     ({"use": "perhaps", "sure": True}, inproof, "no clear answer")):
    v = D.settle(it, row)
    assert not v["sure"] and why in v["doubt"], (row, v)
assert D.settle({**inproof, "kind": "definition"}, {"use": "paper", "sure": True, "needed": "none"})["sure"]
assert D.settle(inproof, None)["use"] == "explanation" and D.settle({**inproof, "kind": "none"}, None)["use"] == "paper"

# ---- the writers read an explanation, the paper never prints it
notes = [{"n": 3, "text": "Proof. [EXPLANATORY DRAWING: The graph of $1/x$ with a rectangle under it.] Done."}]
frag = ("\\begin{proof}\nHence the claim.\n[EXPLANATORY DRAWING: The graph of $1/x$ with a rectangle under it.]\n"
        "\\begin{figurenote}The graph of $1/x$, with a rectangle under it.\\end{figurenote}\nDone.\n\\end{proof}")
out = writing.shape_fragment(frag, "T", False, {}, notes)
assert "EXPLANATORY" not in out and "figurenote" not in out and "Hence the claim." in out and "Done." in out, out
assert "figurenote" in writing.shape_fragment("\\begin{figurenote}Three rows of vertices.\\end{figurenote}", "T", False, {}, notes)
assert "1/x" not in pipeline.audit_pages([{"n": 3, "text": "A [EXPLANATORY DRAWING: $1/x$] B [FIGURE f1: $y$] C"}])[0]["text"]
print("1. the rules: where a drawing sits, what a decision rests on, verdicts held to their reasons, explanations never printed")

# ---- up to step 9 the notes are copied as they are: every drawing, nothing decided about the paper
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
figs = {f["id"]: f for f in p.load("figures")["items"]}
f1, f2 = figs["f1"], figs["f2"]
assert f1["page"] == 1 and f2["page"] == 3 and f1.get("drawn") and f2.get("drawn") and f1["choice"] == f2["choice"] == "", figs
assert pipeline.figure_file(p, "f2", "orig").exists() and "judged" not in f2, "the drawing inside a proof is copied like any other"
labels = [c["label"] for c in p.calls(5000)]
assert not any(l.startswith("Decide which drawings") for l in labels), "nothing is decided before the structure"
assert "Read the labels of figure 1 (page 1)" in labels and any(l.startswith("Read the labels of figure 2") for l in labels), labels
try:
    pipeline.submit_review(p, {}); raise SystemExit("every drawing needs an answer about its copy")
except ValueError as e:
    assert "page 1, 3)" in str(e), e
try:
    pipeline.set_figure(p, "f2", choice="explain"); raise SystemExit("printing is not decided here")
except ValueError as e:
    assert "decided with the structure" in str(e), e
assert pipeline.assumed_choice({"tikz_ok": True}) == "tikz" and pipeline.assumed_choice({"tikz_ok": False}) == "orig"
assert pipeline.assumed_choice({"tikz_ok": True, "check": {"same": False}}) == "orig"
assert pipeline._assume_reading("so $x$ is [?compact|complete?] and [?open?], [?] stays") == "so $x$ is compact and open, [?] stays"
pipeline.submit_review(p, {"figures": {"f1": "orig", "f2": "orig"}})
wpages, wfigs = pipeline.writing_pages(p)
t3 = next(pg["text"] for pg in wpages if pg["n"] == 3)
assert "[FIGURE f2: The graph of $1/x$" in t3 and wfigs == {}, (t3, wfigs)
print("2. step 9: both drawings copied as drawn and checked by the author; nothing decided about printing")

# ---- at the structure: decided with the whole paper in view
pipeline.start(p); wait(p, "scope")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
pipeline.start(p); wait(p, "outline")
plan = D.plan_items(p)
assert plan["f1"]["use"] == "paper" and plan["f1"]["sure"] and plan["f2"]["use"] == "explanation" and plan["f2"]["sure"], plan
assert plan["f2"]["with"] == "R2" and "compared" in plan["f2"]["illustrates"] and plan["f1"]["caption"], plan
asked = simulator.SEEN["Decide which drawings the paper prints"]
assert "THE PROBLEM OF THE PAPER" in asked and "THE STRUCTURE OF THE PAPER" in asked and "HOW THE ARGUMENT GOES" in asked
assert "It belongs to: Lemma" in asked and "(R2)" in asked and "Where it sits in the notes: inside a proof" in asked, asked
view = drafting.view(p)
assert {f["id"]: f["use"] for f in view["figures"]} == {"f1": "paper", "f2": "explanation"}, view["figures"]
drafting.submit_outline(p)
assert all(e.get("confirmed") and not e.get("fresh") for e in D.plan_items(p).values())
pipeline.start(p); wait(p, "proofcheck")
labels = [c["label"] for c in p.calls(5000)]
assert "Draw figure 1 for print" in labels and not any(l.startswith("Draw figure 2 for print") for l in labels), labels
pro = figpro.entries(p)
assert set(pro) == {"f1"} and pro["f1"]["tikz"] and (pro["f1"]["ok"] == bool(paperlib.find_engine())), pro
wpages, wfigs = pipeline.writing_pages(p, final=True)
t3 = next(pg["text"] for pg in wpages if pg["n"] == 3)
assert "[EXPLANATORY DRAWING: The graph of $1/x$" in t3 and set(wfigs) == {"f1"}, wfigs
assert wfigs["f1"]["version"] == ("print" if paperlib.find_engine() else "copy")
seen = next(v for k, v in simulator.SEEN.items() if k.startswith("Write the proof of") and "(R2)" in k)
assert "rectangle of height $1/k$" in seen and "EXPLANATORY DRAWING" in seen, "the writer of that proof read the drawing"
assert "rectangle of height" not in p.load("proofs")["items"]["R2"]["text"], "and did not print it"
print("3. at the structure: the picture of the method printed, the one inside a proof an explanation; the printed one drawn for print")

drafting.submit_proofs(p)
assert figpro.entries(p)["f1"]["choice"] == ("pro" if paperlib.find_engine() else "copy"), "unanswered: the most likely answer"
pipeline.start(p); wait(p, "decide")
tex = paperlib.blocks_to_latex(p.load("body")["blocks"])
assert "EXPLANATORY" not in tex and "insertfigure{f2}" not in tex and "insertfigure{f1}" in tex and "rectangle of height" not in tex, tex
missing = json.dumps((p.load("audit") or {}).get("missing", []))
assert "1/x" not in missing and "1/k" not in missing, missing
assert "res:R2" not in ((p.load("completion") or {}).get("items") or {}), "a proof written in full is not completed again"
print("4. the draft prints the figure for print and never the explanation")

# ---- the author prints an explanation after all, at the structure
q = new_project()
past_topic(q)
pipeline.start(q); wait(q, "confirm"); pipeline.submit_confirm(q, answers(q))
pipeline.start(q); wait(q, "review")
pipeline.submit_review(q, {"figures": {"f1": "orig", "f2": "orig"}})
pipeline.start(q); wait(q, "outline")
e = D.set_plan(q, "f2", use="paper", caption="The comparison of $1/k$ with the integral.")
assert e["use"] == "paper" and not e["auto"] and e["sure"]
drafting.submit_outline(q)
pipeline.start(q); wait(q, "proofcheck")
assert set(figpro.entries(q)) == {"f1", "f2"} and set(pipeline.writing_pages(q, final=True)[1]) == {"f1", "f2"}, "printed after all"
# the author does not want a figure drawn for print: the copy of their drawing, or not printed at all
figpro.set_choice(q, "f1", "copy")
assert pipeline.writing_pages(q, final=True)[1]["f1"]["version"] == "copy"
figpro.set_choice(q, "f2", "none")
assert D.plan_items(q)["f2"]["use"] == "explanation" and set(pipeline.writing_pages(q, final=True)[1]) == {"f1"}

os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "relies"           # the reader says the proof takes something from the drawing
r = new_project()
past_topic(r)
pipeline.start(r); wait(r, "confirm"); pipeline.submit_confirm(r, answers(r))
pipeline.start(r); wait(r, "review")
pipeline.submit_review(r, {"figures": {"f1": "orig", "f2": "orig"}})
pipeline.start(r); wait(r, "outline")
h = D.plan_items(r)["f2"]
assert h["use"] == "explanation" and not h["sure"] and "rely on it" in h["doubt"], h
print("5. printed after all when the author says so; the figure for print declined; asked about when the text relies on the drawing")

# ---- notes read by an earlier version, which judged drawings in step 8: what it did not copy is copied now
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"
from scriptavera import store
o = new_project()
past_topic(o)
pipeline.start(o); wait(o, "confirm"); pipeline.submit_confirm(o, answers(o))
pipeline.start(o); wait(o, "review")
data = o.load("figures")
for f in data["items"]:                                     # as the earlier version left them: f2 judged an explanation, not copied
    if f["id"] == "f2":
        f.update(choice="explain", auto=True, drawn=False, tikz="", tikz_ok=False,
                 judged={"use": "explanation", "sure": True, "why": "It pictures the step.", "illustrates": "the comparison"})
        pipeline.figure_file(o, "f2", "orig").unlink(missing_ok=True)
o.save("figures", data)
pipeline.set_figure(o, "f1", choice="orig")
text_before, answers_before = o.load("transcript"), o.load("doubt_answers")
o.update_state(lambda st: st.update(layout=8))
o2 = store.get(o.id)
st = o2.state["stages"]
assert st["figures"]["status"] == "pending" and st["review"]["status"] == "pending" and o2.state["layout"] == store.LAYOUT, st
g = {f["id"]: f for f in o2.load("figures")["items"]}
assert g["f1"]["choice"] == "orig" and g["f2"]["choice"] == "" and not g["f2"].get("drawn"), g
assert o2.load("transcript") == text_before and o2.load("doubt_answers") == answers_before, "the transcript and your answers stay"
n0 = len(o2.calls(5000))
pipeline.start(o2); wait(o2, "review")
labels = [c["label"] for c in o2.calls(5000)[n0:]]
assert labels and all(("figure 2" in l or "page 3" in l) for l in labels), labels       # only the drawing not copied before
assert {f["id"]: f["choice"] for f in o2.load("figures")["items"]} == {"f1": "orig", "f2": ""}
print("6. drawings judged by an earlier version: the one it did not copy is copied now; the rest, the transcript and answers kept")
print("drawings ok")
