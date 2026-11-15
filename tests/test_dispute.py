# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A statement the author leaves out although a kept one uses it. The confirmation is not refused with an error: a
conversation opens. The author says why the kept statement does not need the other, or gives another argument for
that step; the reply is judged; a reason that is not enough gets an answer and the author may reply again; one that
holds removes the dependence, and the proof is written with the author's argument. The author may also keep the
statement after all, or leave the other out as well. The same at the structure step, where it used to be dropped
without a word."""
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_dispute_")
from scriptavera import drafting, pipeline

p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
pipeline.start(p); wait(p, "scope")
for iid in drafting.open_questions(p):
    drafting.answer_statement(p, iid, "The bound is $\\log(n+1)$.")
item = lambda i: next(y for y in p.load("statements")["items"] if y["id"] == i)          # noqa: E731
assert "R2" in item("R5")["depends"], item("R5")
was = item("R2")["treatment"]


def confirm():
    try:
        drafting.submit_statements(p, {"kind": "article"})
    except drafting.Dispute as e:
        return str(e)
    return ""


# ---------------------------------------------------------------- 1. the statements: left out, and confirmed
drafting.edit_statement(p, "R2", {"treatment": "omit"})
why = confirm()
assert "(R5) uses" in why and "(R2)" in why and "another argument" in why, why
assert p.state["stages"]["scope"]["status"] == "waiting", "nothing is confirmed while the question is open"
D = drafting.view(p)["disputes"]
assert len(D) == 1 and D[0]["a"]["id"] == "R5" and D[0]["b"]["id"] == "R2" and D[0]["at"] == "scope" and D[0]["chat"][0]["who"] == "app", D
assert D[0]["can_omit_a"] and D[0]["a"]["statement"] and "Lemma" in D[0]["b"]["name"] + D[0]["a"]["name"] + "Lemma"
did = D[0]["id"]
out = drafting.talk_dispute(p, did, "It is independent.")
assert not out["settled"] and out["verdict"] == "needed" and "does not replace that step" in out["reply"], out
D = drafting.view(p)["disputes"]
assert [m["who"] for m in D[0]["chat"]] == ["app", "you", "ai"] and D[0]["chat"][-1]["verdict"] == "needed"
assert confirm() and len(drafting.view(p)["disputes"]) == 1 and drafting.view(p)["disputes"][0]["id"] == did, "the same conversation, not a second one"
assert any((c.get("label") or "").startswith("Judge your reason for leaving out R2") for c in p.calls(1000))
print("1. a statement left out that a kept one uses: confirming opens a conversation; a reason that is not enough is answered, and nothing changes")

# ---------------------------------------------------------------- 2. the author's own ways out
assert drafting.settle_dispute(p, did, "keep")["settled"]
assert item("R2")["treatment"] == was and drafting.view(p)["disputes"] == [], "kept after all, as it was before"
drafting.edit_statement(p, "R2", {"treatment": "omit"})
assert confirm()
did2 = drafting.view(p)["disputes"][0]["id"]
assert did2 != did
drafting.settle_dispute(p, did2, "omit")
assert item("R5")["treatment"] == "omit" and drafting.view(p)["disputes"] == [], "the one that uses it is left out as well"
drafting.edit_statement(p, "R5", {"treatment": "keep"})
print("2. keep it after all: it goes back to what it was; or leave out the statement that uses it as well")

# ---------------------------------------------------------------- 3. another argument that holds
assert confirm()
did3 = drafting.view(p)["disputes"][0]["id"]
out = drafting.talk_dispute(p, did3, "Here is another argument: compare each term with the integral directly.")
assert out["settled"] and out["verdict"] == "avoided", out
r5 = item("R5")
assert "R2" not in r5["depends"] and "without the lemma" in r5["instruction"] and item("R2")["treatment"] == "omit", r5
assert drafting.view(p)["disputes"] == []
drafting.edit_statement(p, "R2", {"treatment": was})      # (the simulated planner of the structure always places R2: it stays for the rest)
assert confirm() == "" and p.state["stages"]["scope"]["status"] == "done"
print("3. another argument that holds: the dependence goes, the argument is kept for the writer of that proof, and the statements are confirmed")

# ---------------------------------------------------------------- 4. the structure: leaving out a statement a kept one uses
pipeline.start(p); wait(p, "outline")
order = lambda: [i for s in p.load("outline")["sections"] for i in s["items"]]          # noqa: E731
assert "R4" in item("R5")["depends"] and "R4" in order()
try:
    drafting.edit_outline(p, {"drop": "R4"}); raise SystemExit("R5 uses R4: it is not dropped without a word")
except drafting.Dispute as e:
    assert "(R5)" in str(e) and "(R4)" in str(e), e
assert "R4" in order() and item("R4")["treatment"] != "omit", "nothing is dropped while the question is open"
D = drafting.view(p)["disputes"]
assert len(D) == 1 and D[0]["at"] == "outline" and D[0]["then"] == "drop", D
drafting.settle_dispute(p, D[0]["id"], "close")
assert drafting.view(p)["disputes"] == [] and "R4" in order(), "let be: it stays in the paper"
try:
    drafting.edit_outline(p, {"drop": "R4"}); raise SystemExit("asked again")
except drafting.Dispute:
    pass
D = drafting.view(p)["disputes"]
out = drafting.talk_dispute(p, D[0]["id"], "R4 is mentioned only in a remark after the proof; no step uses it.")
assert out["settled"] and out["verdict"] == "not_used", out
assert "R4" not in order() and item("R4")["treatment"] == "omit" and "R4" not in item("R5")["depends"], "then it is left out"
assert drafting.view(p)["disputes"] == []
drafting.submit_outline(p)
pipeline.start(p); wait(p, "proofcheck")
P = p.load("proofs")["items"]
assert "R4" not in P and P["R5"]["comment"].startswith("Prove it without the lemma"), P["R5"].get("comment")
print("4. at the structure step the same: nothing is dropped until the question is settled; then the statement is left out and "
      "the proof of the one that used it is written with the author's argument")

# ------------------------------------------------- 5. an argument the author won is not fought again after a change
r5 = item("R5")
assert sorted(r5["waived"]) == ["R2", "R4"] and "without the lemma" in r5["instruction"], r5
p.remove("statements")                                   # the notes changed: every statement is written again
run = pipeline.Run(p); run.stage = "scope"
drafting.prepare_statements(run)
r5 = item("R5")
assert "R2" not in r5["depends"] and "R4" not in r5["depends"], r5["depends"]      # the dossier says it uses them; the author argued it does not
assert "without the lemma" in r5["instruction"], "the argument the author gave in their place is still given to the writer"
assert confirm() == "", "the author is not asked to win the same argument twice"
mem = list(p.load("statements_memory").values())
assert any("R2" in (m.get("waived") or []) for m in mem), mem
# a statement whose own notes changed is written again, and what was decided about it goes with it
d = p.load("dossier")
for x in d["results"]:
    if x["id"] == "R5":
        x["statement"] += " Moreover the bound is attained only for $n = 1$."      # the author rewrote that page
p.save("dossier", d)
p.remove("statements")
drafting.prepare_statements(run)
assert "R4" in item("R5")["depends"] and not item("R5").get("waived"), "the argument was about the statement as it stood"
print("5. a dependence the author argued away does not come back when the statements are written again after a change, "
      "and neither is the author's own argument lost; a statement whose notes changed is asked about afresh")
print("dispute ok")
