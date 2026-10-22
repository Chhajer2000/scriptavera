# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Drawings that only explain an argument are not printed: each drawing is judged from the text around it, a sure
explanation is settled without being redrawn, the writers still read it, and a sketched proof is completed with it.
Anything the code cannot square is put to the author, who can always print a drawing after all."""
import json
import os
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"                # the proof on page 3 carries the author's own picture
os.environ["SCRIPTAVERA_MOCK_SKETCH"] = "1"                  # and the assessment calls that proof a sketch
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_drawings_")
import tests.simulator as simulator
from scriptavera import drawings as D, paper as paperlib, pipeline, writing

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
assert not D.wanted_drawn({"judged": {"use": "explanation", "sure": True}})
assert D.wanted_drawn({"judged": {"use": "explanation", "sure": False}}) and D.wanted_drawn({"judged": {"use": "paper", "sure": True}})
assert D.wanted_drawn({"judged": {"use": "explanation", "sure": True}, "asked_print": True})
assert not D.wanted_drawn({"judged": {"use": "annotation", "sure": False}}) and not D.wanted_drawn({"judged": {"use": "paper"}, "choice": "explain"})

# ---- the writers read an explanation, the paper never prints it
notes = [{"n": 3, "text": "Proof. [EXPLANATORY DRAWING: The graph of $1/x$ with a rectangle under it.] Done."}]
frag = ("\\begin{proof}\nHence the claim.\n[EXPLANATORY DRAWING: The graph of $1/x$ with a rectangle under it.]\n"
        "\\begin{figurenote}The graph of $1/x$, with a rectangle under it.\\end{figurenote}\nDone.\n\\end{proof}")
out = writing.shape_fragment(frag, "T", False, {}, notes)
assert "EXPLANATORY" not in out and "figurenote" not in out and "Hence the claim." in out and "Done." in out, out
assert "figurenote" in writing.shape_fragment("\\begin{figurenote}Three rows of vertices.\\end{figurenote}", "T", False, {}, notes)
assert "1/x" not in pipeline.audit_pages([{"n": 3, "text": "A [EXPLANATORY DRAWING: $1/x$] B [FIGURE f1: $y$] C"}])[0]["text"]
print("1. the rules: where a drawing sits, verdicts held to their reasons, explanations never printed")

# ---- the whole pipeline: the picture of the method is for the paper, the picture inside the proof only explains it
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
figs = {f["id"]: f for f in p.load("figures")["items"]}
f1, f2 = figs["f1"], figs["f2"]
assert f1["page"] == 1 and f1["judged"]["use"] == "paper" and f1["judged"]["sure"] and f1.get("drawn") and f1["choice"] == "", f1
assert f2["page"] == 3 and f2["judged"]["use"] == "explanation" and f2["judged"]["sure"] and f2["choice"] == "explain" and f2["auto"], f2
assert "compared" in f2["judged"]["illustrates"] and not f2.get("drawn") and not pipeline.figure_file(p, "f2", "orig").exists()
labels = [c["label"] for c in p.calls(5000)]
assert labels.count("Decide which drawings are for the paper") == 1, labels
assert "Draw figure 1 (page 1)" in labels and not any(l.startswith("Draw figure 2") for l in labels), "an explanation is not redrawn"
asked = simulator.SEEN["Decide which drawings are for the paper"]
assert "Where it sits: inside a proof (it begins on page 3" in asked and "Where it sits: in the statement of the problem" in asked
try:
    pipeline.submit_review(p, {}); raise SystemExit("the figure for the paper still needs an answer")
except ValueError as e:
    assert "page 1)" in str(e), e                            # only page 1: the explanation is settled
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
wpages, wfigs = pipeline.writing_pages(p)
t3 = next(pg["text"] for pg in wpages if pg["n"] == 3)
assert "[EXPLANATORY DRAWING: The graph of $1/x$" in t3 and set(wfigs) == {"f1"}, wfigs
print("2. two drawings: the one for the paper redrawn and asked about, the one inside a proof settled as an explanation")

pipeline.start(p); wait(p, "scope")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
pipeline.start(p); wait(p, "decide")
tex = paperlib.blocks_to_latex(p.load("body")["blocks"])
assert "EXPLANATORY" not in tex and "insertfigure{f2}" not in tex and "insertfigure{f1}" in tex and "rectangle of height" not in tex, tex
assert any("[EXPLANATORY DRAWING:" in v for k, v in simulator.SEEN.items() if k.startswith("Write section")), "the writer read it"
missing = json.dumps((p.load("audit") or {}).get("missing", []))
assert "1/x" not in missing and "1/k" not in missing, missing
rec = p.load("completion")["items"]["res:R2"]
assert rec["status"] == "ready" and rec["drawings"] == 1, rec.get("reason")
for label in ("Complete the sketch of Lemma 3.2", "Check the completion of Lemma 3.2 against the sketch"):
    assert "THE AUTHOR'S DRAWINGS FOR THIS PROOF" in simulator.SEEN[label] and "rectangle of height $1/k$" in simulator.SEEN[label], label
from scriptavera import revision
assert set(D.for_results(p, revision.result_units(p.load("body")["blocks"]))) == {"res:R2"}, "only the proof it was drawn for"
print("3. the draft never prints the explanation; the writer read it, and the sketch of Lemma 3.2 was completed with it")

# ---- the author prints an explanation after all; one the text relies on is asked about
q = new_project()
past_topic(q)
pipeline.start(q); wait(q, "confirm"); pipeline.submit_confirm(q, answers(q))
pipeline.start(q); wait(q, "review")
try:
    pipeline.set_figure(q, "f2", choice="orig"); raise SystemExit("nothing was cut from the page yet")
except ValueError as e:
    assert "Choose to print it first" in str(e)
n0 = len(q.calls(5000))
g = pipeline.print_figure(q, "f2")
assert g["choice"] == "" and g["asked_print"] and not g["auto"] and g.get("drawn") and pipeline.figure_file(q, "f2", "orig").exists()
assert [c["label"] for c in q.calls(5000)[n0:]] == ["Draw figure 2 (page 3)"]
pipeline.set_figure(q, "f2", choice="orig")
pipeline.set_figure(q, "f1", choice="tikz" if q.load("figures")["items"][0].get("tikz_ok") else "orig")
pipeline.submit_review(q, {})
assert set(pipeline.writing_pages(q)[1]) == {"f1", "f2"}, "printed after all"

os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "relies"           # the reader says the proof takes something from the drawing
r = new_project()
past_topic(r)
pipeline.start(r); wait(r, "confirm"); pipeline.submit_confirm(r, answers(r))
pipeline.start(r); wait(r, "review")
h = {f["id"]: f for f in r.load("figures")["items"]}["f2"]
assert h["judged"]["use"] == "explanation" and not h["judged"]["sure"] and "rely on it" in h["judged"]["doubt"], h["judged"]
assert h["choice"] == "" and h.get("drawn"), "not sure: drawn, and put to the author"
pipeline.set_figure(r, "f2", choice="explain")
assert r.load("figures")["items"][1]["choice"] == "explain" and not r.load("figures")["items"][1]["auto"]
print("4. printed after all when the author says so; asked about when the text relies on the drawing")

# ---- notes read by an earlier version, transcript not yet approved: the Figures step is done again from the start
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"
from scriptavera import store
o = new_project()
past_topic(o)
pipeline.start(o); wait(o, "confirm"); pipeline.submit_confirm(o, answers(o))
pipeline.start(o); wait(o, "review")
data = o.load("figures")
for f in data["items"]:
    f.pop("judged", None); f.pop("auto", None); f["choice"] = ""
o.save("figures", data)
pipeline.set_figure(o, "f1", choice="orig")
text_before, answers_before = o.load("transcript"), o.load("doubt_answers")
o.update_state(lambda st: st.update(layout=5))                        # as saved by the earlier version
assert (o.dir / "figures").exists()
o2 = store.get(o.id)
st = o2.state["stages"]
assert st["figures"]["status"] == "pending" and st["review"]["status"] == "pending" and o2.state["layout"] == store.LAYOUT, st["figures"]
assert o2.load("figures") is None and not (o2.dir / "figures").exists(), "the old drawings are discarded"
assert o2.load("transcript") == text_before and o2.load("doubt_answers") == answers_before, "the transcript and your answers stay"
n0 = len(o2.calls(5000))
pipeline.start(o2); wait(o2, "review")
labels = [c["label"] for c in o2.calls(5000)[n0:]]
assert labels == ["Decide which drawings are for the paper", "Draw figure 1 (page 1)"], labels
assert {f["id"]: f["choice"] for f in o2.load("figures")["items"]} == {"f1": "", "f2": "explain"}
# a finished paper is not touched, nor one whose figures were already judged
done = new_project(); past_topic(done)
pipeline.start(done); wait(done, "confirm"); pipeline.submit_confirm(done, answers(done))
pipeline.start(done); wait(done, "review")
done.update_state(lambda st: st.update(layout=5))
assert store.get(done.id).state["stages"]["figures"]["status"] == "done", "already judged: kept"
print("5. drawings made by an earlier version: the Figures step done again by itself, the transcript and answers kept")
print("drawings ok")
