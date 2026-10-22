# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Completing a sketched proof by its own steps: the sketch is never rewritten, every addition is screened, and
nothing reaches the paper unless the author chooses it."""
import os
os.environ["SCRIPTAVERA_MOCK_SKETCH"] = "1"                  # the simulated assessment calls the proof of R2 a sketch
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_complete_")
from scriptavera import completion as C, pipeline, revision, writing

# ---- the steps are quotations found in the sketch, in order, never inside a formula
sketch = r"Let $p$ be a prime. Then $p \mid ab$ gives \[ p \mid a \text{ or } p \mid b \] by Euclid. Hence the claim."
steps, why = C.locate_steps(sketch, [{"quote": "Then $p \\mid ab$ gives", "claim": "c"}, {"quote": "Hence the claim.", "claim": "c"}])
assert not why and sketch[:steps[0]["end"]].endswith("by Euclid."), steps        # after the formula and the end of its sentence
_, why = C.locate_steps(sketch, [{"quote": "Then $p | ab$ implies", "claim": "c"}])
assert "not quoted exactly" in why, why
_, why = C.locate_steps(sketch, [{"quote": "Hence the claim.", "claim": "c"}, {"quote": "Let $p$ be a prime.", "claim": "c"}])
assert why, "steps out of order are refused"

# ---- the sketch survives word for word; additions are only inserted
steps, _ = C.locate_steps(sketch, [{"quote": "Let $p$ be a prime.", "claim": "c"}, {"quote": "Hence the claim.", "claim": "c"}])
text, parts = C.assemble(sketch, steps, {1: "Write $q = p$ for brevity.", 2: ""})
assert "".join(x["text"] for x in parts if not x["added"]) == sketch and "Write $q = p$" in text
assert text.index("Let $p$ be a prime.") < text.index("Write $q") < text.index("Then $p")

# ---- what the code refuses by itself, and the symbols it lists for the faithfulness check
ctx = {"labels": {"res:R1", "res:R2", "res:R3"}, "later": {"res:R3"}, "cited": {"Key2000"}, "sketch": sketch,
       "steps": {s["n"]: s for s in steps}, "known": C.symbols(sketch)}
probs, new = C.screen({1: r"\begin{lemma}x\end{lemma}", 2: r"By Lemma~\ref{res:R3} and \cite{Other2001}, $\frac{1}{2$."}, ctx)
assert any("new result" in x for x in probs) and any("comes after" in x for x in probs) and any("does not cite" in x for x in probs) \
    and any("unbalanced" in x for x in probs), probs
probs, new = C.screen({1: r"Choose $m$ with $m > p$ and $\lambda = 2$."}, ctx)
assert not probs and new == [], new                          # introduced with their meaning: harmless
probs, new = C.screen({1: r"Since $r < p$, the claim follows."}, ctx)
assert new == ["step 1: r"], new                             # appears from nowhere: the independent reading is asked about it
adds, fails = C._additions('<step n="1">NONE</step><step n="2">Indeed $x$.</step><fail n="3">It needs $p$ odd.</fail>', steps + [{"n": 3}])
assert adds == {1: "", 2: "Indeed $x$."} and fails == {3: "It needs $p$ odd."}, (adds, fails)
print("1. the rules: quotations only, the sketch kept word for word, new results, later results, citations and stray symbols caught")

# ---- the whole pipeline, with the proof of R2 sketched
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {f["id"]: "orig" for f in (p.load("figures") or {}).get("items", [])}})
pipeline.start(p); wait(p, "scope")
assert p.load("assessment")["results"]["R2"]["proof"] == "sketch"
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
pipeline.start(p); wait(p, "decide")
rec = p.load("completion")["items"]["res:R2"]
assert rec["status"] == "ready", rec.get("reason")
assert len(rec["steps"]) == 3 and set(rec["additions"]) == {"1", "2", "3"} and rec["additions"]["3"] == "", rec["additions"]
assert "Unknown2020" not in rec["proof"], "the citation the screen refused was dropped on the second try"
labels = [c["label"] for c in p.calls(5000)]
assert labels.count("Complete the sketch of Lemma 3.2") == 2 and "Check the completion of Lemma 3.2 against the sketch" in labels, \
    [l for l in labels if "sketch" in l or "completion" in l]
kept = "".join(x["text"] for x in rec["parts"] if not x["added"])
assert kept == rec["sketch"], "the sketch is kept word for word"
assert r"\log 1 = 0" in rec["proof"] and [x["step"] for x in rec["parts"] if x["added"]] == [1, 2]
assert rec["proof"].index(r"\log 1 = 0") < rec["proof"].index("Adding the term"), "each addition stands right after its own step"
assert rec["proof"].index("x \\le k") < rec["proof"].index("Hence"), rec["proof"]
items = revision.decision_items(p)
comp = items["complete"]
assert [c["label"] for c in comp] == ["res:R2"] and comp[0]["context"]["pages"], comp
cid = comp[0]["id"]
print("2. the sketch of Lemma 3.2 completed by its own steps; the refused citation was dropped on the second try; put to the author")

# ---- the author decides
issue = items["issues"][0]
base = {"simplify": {"S1": "reject"}, "issues": {issue["id"]: {"choice": "fix"}}}
for bad in ({"choice": "instruct", "note": ""}, {"choice": "edit", "text": r"\frac{1}{2"}, {}):
    try:
        revision.submit_decisions(p, {**base, "complete": {cid: bad}}); raise SystemExit(f"refused: {bad}")
    except ValueError as e:
        assert "still need your decision" in str(e)
revision.submit_decisions(p, {**base, "complete": {cid: {"choice": "accept"}}})
pipeline.start(p); wait(p, "assemble", ("done",))
P = p.load("paper")
proofs = {u["label"]: u["proof"] for u in revision.result_units(P["blocks"])}
assert r"\log 1 = 0" in proofs["res:R2"] and "x \\le k" in proofs["res:R2"], proofs["res:R2"]
rev = P["revision"]
assert [x["label"] for x in rev["completed"]] == ["res:R2"] and rev["completed"][0]["how"] == "accept", rev
print("3. accepted: the completed proof is in the paper, and the revision report says so")
print("completion ok")
