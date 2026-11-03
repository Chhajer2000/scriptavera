# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The writing of the paper as a mathematician does it, with simulated models: statements first (with questions and
the ones the paper does not need left out), then the structure (a move that breaks a dependence is refused and can be
argued), then each proof on its own with its check, then the paper put together."""
from helpers import answers, isolate, new_project, wait
isolate("scriptavera_writing_")
from scriptavera import drafting, pipeline, writing

p = new_project()
pipeline.start(p); wait(p, "topiccheck"); pipeline.submit_topic(p, {"action": "confirm"})
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {f["id"]: "orig" for f in p.load("figures")["items"]}})
pipeline.start(p); wait(p, "scope")

# 1. a statement the paper does not need is left out, with the reason; it needs no answer
cut = drafting._merge_statement({"id": "R9", "kind": "lemma", "original": "$x$", "role": "auxiliary"},
                                {"kind": "lemma", "needed": "no: the proof of the theorem does not use it", "statement": "$x$",
                                 "question": "Is $x$ real?"})
assert cut["treatment"] == "omit" and cut["cut"] == "the proof of the theorem does not use it" and cut["settled"] and not cut["question"]
main = drafting._merge_statement({"id": "R8", "kind": "theorem", "original": "$y$", "role": "main"}, {"needed": "no", "statement": "$y$"})
assert main["treatment"] == "keep", "a main result is never left out by a model's choice"
print("1. a statement the paper does not need is suggested to be left out, with the reason; a main result never is")

# 2. a question is answered; the decisions survive the statements being written again
x = drafting.answer_statement(p, "R4", "The bound is $\\log(n+1)$.")
assert x["settled"]
drafting.edit_statement(p, "R1", {"kind": "proposition"})
p.remove("statements")
run = pipeline.Run(p); run.stage = "scope"
drafting.prepare_statements(run)
st = {y["id"]: y for y in p.load("statements")["items"]}
assert st["R1"]["kind"] == "proposition" and st["R4"]["settled"] and st["R4"]["asked"], "the author's decisions are remembered"
drafting.edit_statement(p, "R1", {"kind": "lemma"})
writing.submit_scope(p, {"kind": "article"})
print("2. a question answered; the author's decisions kept when the statements are written again")

# 3. the structure: a move that breaks a dependence is refused, said in the conversation, and can be argued
pipeline.start(p); wait(p, "outline")
order = lambda: [i for s in p.load("outline")["sections"] for i in s["items"]]
assert order().index("R4") < order().index("R5")
try:
    drafting.edit_outline(p, {"move": "R5", "to": "up"}); raise SystemExit("R5 uses R4: it cannot come first")
except ValueError as e:
    assert "uses" in str(e) and "R4" in str(e), e
chat = p.load("outline")["chat"]
assert chat[-1]["who"] == "app" and chat[-1]["move"] == {"id": "R5", "to": "up"} and chat[-1]["a"] == "R5" and chat[-1]["b"] == "R4"
drafting.talk_outline(p, "It is independent.")
chat = p.load("outline")["chat"]
assert chat[-1]["who"] == "ai" and chat[-1]["verdict"] == "needed" and order().index("R4") < order().index("R5"), "not convinced: no change"
drafting.talk_outline(p, "Here is another argument: compare each term with the integral directly.")
O = p.load("outline")
r5 = next(y for y in p.load("statements")["items"] if y["id"] == "R5")
assert "R4" not in r5["depends"] and "without the lemma" in r5["instruction"], r5
assert order().index("R5") < order().index("R4") and O["chat"][-1]["text"].startswith("The order was changed"), O["chat"][-2:]
print("3. a move against a dependence refused and explained; a reply that does not hold changes nothing; another argument that holds moves it")

# 4. the proofs: the author's argument goes to the writer; the paper is put together
drafting.submit_outline(p)
pipeline.start(p); wait(p, "proofcheck")
calls = [c for c in p.calls(100000) if c.get("stage") == "proofs"]
assert any("(R5)" in (c.get("label") or "") for c in calls)
P = p.load("proofs")["items"]
assert P["R5"]["comment"].startswith("Prove it without the lemma"), "the writer of R5 was given the author's argument"
assert set(P) == {"R1", "R3", "R4", "R5"} and P["R3"]["status"] == "accept" and len(P["R3"]["tries"]) == 2
drafting.submit_proofs(p)
pipeline.start(p); wait(p, "draftcheck")
body = p.load("body")
envs = [b.get("label") for b in body["blocks"] if b["type"] == "env"]
assert envs.index("res:R5") < envs.index("res:R4"), envs
assert p.load("abstract_draft")["abstract"] and len(p.load("intro")["text"]) > 200
src = drafting.approved_source(p)
assert len(src) == 6 and all(s["text"] for s in src)
print("4. each proof written on its own and checked; the author's argument used; the paper put together in the agreed order")
print("writing ok")
