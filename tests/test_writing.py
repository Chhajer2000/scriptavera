# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The writing of the paper as a mathematician does it, with simulated models: statements first (with questions and
the ones the paper does not need left out), then the structure (a move that breaks a dependence is refused and can be
argued), then each proof on its own with its check, then the paper put together."""
import json
from helpers import answers, isolate, new_project, wait
isolate("scriptavera_writing_")
from scriptavera import drafting, pipeline, prompts, writing

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
calls = [json.loads(l)["label"] for l in (p.dir / "calls.jsonl").read_text().splitlines() if "Plan the structure" in l]
assert calls[:2] == ["Plan the structure", "Plan the structure (again: the plan was incomplete)"], calls   # put R5 first: asked again
# the plan is the model's: a plan with a statement missing, named twice, unknown or out of order is asked for again, never filled in
its = [dict(x, statement="...") for x in [{"id": "D1", "kind": "definition", "depends": []}, {"id": "R1", "kind": "lemma", "depends": ["D1"]},
       {"id": "R2", "kind": "theorem", "depends": ["R1", "O1"]}, {"id": "O1", "kind": "remark", "depends": []}]]
secs, probs = drafting.check_plan([{"title": "Background", "items": "D1, R1"}, {"title": "The main theorem", "items": "R2, O1"}], its)
assert not probs and [s["items"] for s in secs] == [["D1", "R1"], ["R2", "O1"]]
secs, probs = drafting.check_plan([{"title": "Open questions", "items": "O1, R2, X9, R2"}], its)
assert any("in no section: D1, R1" in q for q in probs) and any("X9" in q for q in probs) and any("twice" in q for q in probs), probs
assert drafting.check_plan([{"title": "Main", "items": "R1, D1, R2, O1"}], its)[1] == ["R1 comes before D1, which it uses."]
assert any("no <section>" in q for q in drafting.check_plan([], its)[1])
assert any("Introduction" in q for q in drafting.check_plan([{"title": "Introduction", "items": "D1, R1, R2, O1"}], its)[1])


class _Bad:
    field, asked = "analysis", []

    def ask(self, role, system, user, **kw):
        self.asked.append((kw["label"], user))
        return type("R", (), {"text": "<section>\ntitle: Open questions\nitems: O1\n</section>", "model": "m"})()

    def log(self, *a):
        pass


bad = _Bad()
try:
    drafting.plan_structure(bad, its); raise SystemExit("an incomplete plan is never used")
except drafting.LLMError as e:
    assert e.kind == "transient" and "could not be planned" in str(e), e
assert len(bad.asked) == drafting.PLAN_TRIES and "in no section: D1, R1, R2" in bad.asked[1][1] and "YOUR PREVIOUS ANSWER" in bad.asked[1][1]
assert "One to six" not in prompts.OUTLINE and "As many sections as this paper" in prompts.OUTLINE
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
print("3. the plan is the model's own: an incomplete one is asked for again, never filled in; a move against a dependence refused and explained; a reply that does not hold changes nothing; another argument that holds moves it")

# 4. the proofs: the author's argument goes to the writer; the paper is put together
drafting.submit_outline(p)
pipeline.start(p); wait(p, "proofcheck")
calls = [c for c in p.calls(100000) if c.get("stage") == "proofs"]
assert any("(R5)" in (c.get("label") or "") for c in calls)
P = p.load("proofs")["items"]
assert P["R5"]["comment"].startswith("Prove it without the lemma"), "the writer of R5 was given the author's argument"
assert set(P) == {"R1", "R3", "R4", "R5"} and P["R3"]["status"] == "accept" and len(P["R3"]["tries"]) == 2
labels = [c.get("label") or "" for c in calls]
assert any(l.startswith("Second opinion on the proof of") and "(R3)" in l for l in labels), labels   # a real slip, upheld
assert not P["R3"]["check"].get("dismissed")
# the works the author's notes cite become papers the proofs may cite; one that cannot be found is named in the proof
refs = p.load("notes_refs")["refs"]
lib = {x["key"]: x for x in p.load("library")}
found = [r for r in refs if r["key"]]
assert [r["names"] for r in found] == [["sample"]] and lib[found[0]["key"]].get("notes_cite") and not refs[1]["key"], refs
assert found[0]["key"] in {x["key"] for x in writing.citable(p)} and "the notes use it for: the integral comparison" in writing.sources_text(p)
assert any("Not found, so a proof names them" in (e.get("message") or "") for e in pipeline.events_after(p.id, 0)), "the author is told"
# a referee's objection is put to a second referee; one it dismisses is not acted on but shown to the author
ctx = drafting.proof_context(p, drafting.Run(p))
x5 = next(y for y in ctx["seq"] if y["id"] == "R5")
w5 = {"text": P["R5"]["text"], "added": ["by a theorem of Sample (2018)"], "cites": ["Sample || 2018 || comparison || the bound"], "doubt": ""}
c5 = {"follows": "yes", "departures": [], "added": [{"what": "by a theorem of Sample (2018)", "ok": False, "note": "not among the statements"}],
      "author": "complete", "proof": "wrong: it uses a result that is not among the statements it may use", "statement": "holds",
      "verdict": "rewrite", "fix": "", "model": "m1"}
r = drafting.Run(p); r.stage = "proofs"
c5 = drafting.second_opinion(r, ctx, x5, w5, c5)
assert c5["verdict"] == "review" and len(c5["dismissed"]) == 2 and c5["proof"] == "correct" and c5["added"][0]["ok"], c5
for want in ("never wrong merely because it is not one of the paper's own statements", "write doubtful, not wrong or false"):
    assert want in prompts.CHECK_PROOF, want
assert "<cites>" in prompts.WRITE_PROOF and "by a theorem of Smith and Jones (2020)" in prompts.WRITE_PROOF
drafting.submit_proofs(p)
pipeline.start(p); wait(p, "draftcheck")
body = p.load("body")
envs = [b.get("label") for b in body["blocks"] if b["type"] == "env"]
assert envs.index("res:R5") < envs.index("res:R4"), envs
assert p.load("abstract_draft")["abstract"] and len(p.load("intro")["text"]) > 200
src = drafting.approved_source(p)
assert len(src) == 6 and all(s["text"] for s in src)
print("4. the papers the notes cite are found and may be cited; an objection is put to a second referee; each proof written on its own and checked; the author's argument used; the paper put together in the agreed order")
print("writing ok")
