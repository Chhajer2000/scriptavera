# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A table or figure the author did not make: suggested in the revision with the reason and the place, compiled,
printed only if the author accepts it. And the figures for print: decided by the app, the author asked only about a
correction."""
import os
os.environ["SCRIPTAVERA_MOCK_ADDITION"] = "1"
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_additions_")
from scriptavera import figpro, paper as paperlib, pipeline, revision

p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig", "f2": "orig"}})
pipeline.start(p); wait(p, "proofcheck")
pro = figpro.entries(p)["f1"]
assert pro["choice"] == ("pro" if paperlib.find_engine() else "copy") and pro["auto"] and figpro.unanswered(p) == [], pro
rec = figpro.decide({"ok": True, "corrections": [{"what": "a fourth rectangle"}]})
assert rec["choice"] == "pro" and rec["auto"], "with a correction the notes bear out it is selected too, and the author is told"
assert figpro.decide({"ok": False})["choice"] == "copy" and figpro.decide({"ok": True, "choice": "copy"})["choice"] == "copy"
assert figpro.decide({"ok": True, "choice": "none", "auto": False})["choice"] == "none", "the author's own answer stands"
assert figpro.to_confirm(p) == [], "no figure of this paper corrects the drawing"
data = p.load("figures_pro")                                  # as an earlier version left it: drawn, and waiting for an answer
for rec in data.values():
    rec.pop("choice", None); rec.pop("auto", None)
p.save("figures_pro", data)
from scriptavera import drafting
assert drafting.view(p)["open_figures"] == [] and all(r["choice"] == ("pro" if r.get("ok") else "copy") and r["auto"] for r in figpro.entries(p).values()), \
    "a figure drawn before the app selected answers gets one when the author next looks"
print("1. the app's answer is selected for every figure for print; the author can change it, and a correction is named again at the approval")

pipeline.start(p); wait(p, "decide")
items = revision.decision_items(p)
if not paperlib.find_engine():
    assert items["additions"] == []
    print("no LaTeX here: nothing is suggested that could not be shown")
    raise SystemExit(0)
a = items["additions"][0]
assert a["kind"] == "table" and a["after"] == "res:R5" and a["why"] and pipeline.figure_file(p, a["id"], "tikz").exists(), a
assert p.state["stages"]["decide"]["status"] == "waiting", "a suggestion waits for the author"
base = {"simplify": {x["id"]: "reject" for x in items["simplify"]}, "issues": {x["id"]: {"choice": "keep"} for x in items["issues"]},
        "complete": {x["id"]: {"choice": "keep"} for x in items["complete"]}}
try:
    revision.submit_decisions(p, base); raise SystemExit("the suggestion needs an answer")
except ValueError as e:
    assert "need your decision" in str(e)
revision.submit_decisions(p, {**base, "additions": {a["id"]: "accept"}})
pipeline.start(p); wait(p, "finish")
P = p.load("paper")
assert P["figures"][a["id"]]["kind"] == "table"
tex = (p.dir / "out" / "main.tex").read_text()
assert "\\begin{table}" in tex and "The bounds proved in this paper." in tex and f"Table~\\ref{{fig:{a['id']}}}" in tex, tex[-3000:]
assert tex.index("\\begin{table}") > tex.index("\\label{res:R5}")
print("2. a table suggested with its reason and place, shown, accepted, and printed after the result it stands by")

revision.submit_decisions(p, {**base, "additions": {a["id"]: "reject"}})
p.update_from("assemble", "your decisions"); pipeline.start(p); wait(p, "finish")
assert "\\begin{table}" not in (p.dir / "out" / "main.tex").read_text() and a["id"] not in p.load("paper")["figures"]
print("3. rejected: the paper is as it was")
print("additions ok")
