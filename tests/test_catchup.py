# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A change made at an earlier step while the paper is further on: nothing is started over. Every later step is
brought up to date, and only what the change touches is done again; a stop the author had passed is passed again by
itself unless something there needs them, and then it shows only what changed. The paper comes back to where the
author was."""
import os
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"                # two drawings: the picture of the method, and one in a proof
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_catchup_")
import tests.simulator as simulator
from scriptavera import drafting, drawings as D, figpro, paper as paperlib, pipeline, revision, store, writing


def labels_since(p, n0: int) -> list[str]:
    return [c.get("label") or "" for c in p.calls(100000)[n0:]]


def approve_proofs(p) -> None:
    for iid in drafting.open_proof_problems(p):
        drafting.decide_proof(p, iid, "keep")
    drafting.submit_proofs(p)


def again(p, gate: str, why: str) -> None:
    """What the server does when the author confirms a stop again after the paper went further."""
    nxt = store.STAGE_IDS[store.STAGE_IDS.index(gate) + 1]
    p.update_from(nxt, why)
    pipeline.start(p)


# ---------------------------------------------------------------- a paper written up to the author's decisions
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig", "f2": "orig"}})
pipeline.start(p); wait(p, "scope")
for iid in drafting.open_questions(p):
    drafting.answer_statement(p, iid, "The bound is $\\log(n+1)$.")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
pipeline.start(p); wait(p, "decide")
assert p.state["stages"]["decide"]["status"] == "waiting"
proofs0 = {k: v["text"] for k, v in p.load("proofs")["items"].items()}
intro0 = p.load("intro")["text"]
print("0. a paper written up to the author's decisions")

# ---------------------------------------------------------------- 1. confirmed again with nothing changed: nothing is done again
n0 = len(p.calls(100000))
pipeline.submit_review(p, {})
again(p, "review", "your corrections in step 9")
assert p.state["catchup"]["from"] == "understand" and p.state["stages"]["proofs"]["status"] == "stale"
wait(p, "decide")
made = labels_since(p, n0)
assert made == [], made
st = p.state["stages"]
assert st["decide"]["status"] == "waiting" and "catchup" not in p.state, st["decide"]
assert all(st[g].get("passed_again") for g in ("scope", "outline", "proofcheck", "draftcheck")), {g: st[g] for g in ("scope", "outline")}
print("1. step 9 confirmed again with nothing changed: no request at all; every stop passed again; back at the decisions")

# ---------------------------------------------------------------- 2. one page changed: only what it touches is done again
tr = p.load("transcript")["pages"]
page3 = tr["3"]["text"] + "\n\nnow: R2 states For all $n \\ge 1$ we have $H_n^{(1)} \\le 2 + \\log n$."
n0 = len(p.calls(100000))
pipeline.submit_review(p, {"pages": {"3": page3}})
again(p, "review", "your corrections in step 9")
wait(p, "scope")
made = labels_since(p, n0)
assert "Bring the understanding up to date" in made and "Understand the whole paper" not in made, made
assert not any(l.startswith("Transcribe") for l in made), "no page is read again"
d = p.load("dossier")
assert next(x for x in d["results"] if x["id"] == "R2")["statement"].endswith("2 + \\log n$.") and d["changed"] == ["R2"], d["changed"]
assert next(x for x in d["results"] if x["id"] == "R3")["statement"].startswith("Let $s > 1$"), "the rest stays word for word"
A = p.load("assessment")
assert A["changed"] == ["R2"] and A["keys"], A.get("changed")
asked = simulator.SEEN["Assess the notes"]
assert asked.startswith("ASSESS ONLY THESE RESULTS: R2."), asked[:200]
S = p.load("statements")
asked = simulator.SEEN["Write the statements"]
assert asked.startswith("WRITE ONLY THESE ITEMS: R2."), asked[:200]
assert next(x for x in S["items"] if x["id"] == "R2")["fresh"] is True and not next(x for x in S["items"] if x["id"] == "R3").get("fresh")
sc = p.state["stages"]["scope"]
assert sc["status"] == "waiting" and sc["changed"] == ["R2 changed"], sc
assert next(x for x in S["items"] if x["id"] == "R4")["answer"], "the answer given before is kept"
print("2a. one page changed: the understanding brought up to date from it, R2 assessed and stated again; the statements stop shows R2")

n0 = len(p.calls(100000))
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
again(p, "scope", "your changes to the statements")
wait(p, "outline")
made = labels_since(p, n0)
assert made == ["Decide which drawings the paper prints"], made          # the drawing in R2's proof, decided again
ol = p.state["stages"]["outline"]
assert ol["status"] == "waiting" and any("f2" in c for c in ol["changed"]), ol
assert D.plan_items(p)["f2"]["fresh"] and not D.plan_items(p)["f1"].get("fresh")
n0 = len(p.calls(100000))
drafting.submit_outline(p)
again(p, "outline", "your changes to the structure")
wait(p, "proofcheck")
made = labels_since(p, n0)
written = [l for l in made if l.startswith("Write the proof of")]
checked = [l for l in made if l.startswith("Check the proof of")]
assert written and all("(R2)" in l for l in written), made                 # its own statement changed: written again
assert any("(R5)" in l for l in checked) and not any("(R5)" in l for l in written), made   # uses R2: checked again
assert not any(x in l for l in made for x in ("(R1)", "(R3)", "(R4)")), made          # the others are kept
assert not any(l.startswith("Draw figure") for l in made), "the figure for print is kept: nothing it is drawn from changed"
P = p.load("proofs")["items"]
assert P["R1"]["text"] == proofs0["R1"] and P["R3"]["text"] == proofs0["R3"] and P["R5"]["text"] == proofs0["R5"]
pc = p.state["stages"]["proofcheck"]
assert pc["status"] == "waiting" and pc["changed"][0] == "the proof of R2 was written again", pc
print("2b. the structure kept, the drawing of R2 decided again; R2's proof written again, R5 (which uses it) checked again, the rest kept")

n0 = len(p.calls(100000))
approve_proofs(p)
again(p, "proofcheck", "your decisions on the proofs and figures")
wait(p, "draftcheck")
made = labels_since(p, n0)
assert "Bring the introduction up to date" in made and "Write introduction" not in made, made
joined = [l for l in made if l.startswith("Join the results of section")]
assert joined == ["Join the results of section 2"], made                        # only the section with R2
assert "Write abstract" not in made, "the abstract: the main results and the introduction did not change"
assert p.load("intro")["text"] == intro0
dc = p.state["stages"]["draftcheck"]
assert dc["status"] == "waiting" and dc["changed"] == ["the introduction was brought up to date"], dc
n0 = len(p.calls(100000))
drafting.submit_draft(p, {})
again(p, "draftcheck", "your changes to the first draft")
wait(p, "decide")
made = labels_since(p, n0)
assert "Referee report" in made and not any(l.startswith(("Check proof of Lemma 2.1", "Check proof of Theorem")) for l in made), made
v = p.load("verify")["results"]
assert sum(1 for r in v.values() if r.get("kept")) >= 3, v.keys()
print("2c. the introduction brought up to date, one section joined again, the abstract kept; only the changed proof checked again")

# ---------------------------------------------------------------- 3. a drawing printed after all, while further on
n0 = len(p.calls(100000))
D.set_plan(p, "f2", use="paper")
drafting.submit_outline(p)
again(p, "outline", "your changes to the structure")
wait(p, "proofcheck")
made = labels_since(p, n0)
assert "Draw figure 2 for print" in made and "Draw figure 1 for print" not in made, made
written = [l for l in made if l.startswith("Write the proof of")]
assert written and all("(R2)" in l for l in written), made                 # the proof the drawing is in
assert set(pipeline.writing_pages(p, final=True)[1]) == {"f1", "f2"}
pc = p.state["stages"]["proofcheck"]
assert "the figure f2 was drawn again for print" in pc["changed"], pc
print("3. a drawing printed after all: drawn for print; only the proof it is in written again")

# ---------------------------------------------------------------- 4. a step done again by the author's wish
approve_proofs(p)
again(p, "proofcheck", "your decisions on the proofs and figures")
wait(p, "decide")
n0 = len(p.calls(100000))
p.redo_from("proofs")
assert p.state["catchup"]["anew"] == "proofs" and p.state["stages"]["proofs"]["status"] == "pending"
assert p.state["stages"]["draft"]["status"] == "stale" and p.load("body"), "what comes after is kept until brought up to date"
pipeline.start(p); wait(p, "proofcheck")
made = labels_since(p, n0)
assert {l.split("(")[-1] for l in made if l.startswith("Write the proof of")} >= {"R1)", "R2)", "R3)", "R4)", "R5)"}, made
print("4. a step redone: all its proofs written anew; the steps after it are brought up to date from them")

# ---------------------------------------------------------------- 5. the notation confirmed again: only the pages it bears on are read again
q = new_project()
past_topic(q)
pipeline.start(q); wait(q, "confirm"); pipeline.submit_confirm(q, answers(q))
pipeline.start(q); wait(q, "review")
pipeline.submit_review(q, {"figures": {"f1": "orig", "f2": "orig"}})
pipeline.start(q); wait(q, "scope")
conf = q.load("confirmed")
sym = next(g for g in conf["glossary"] if any(pipeline._occurs(g["symbol"], v["text"]) for v in q.load("transcript")["pages"].values()))
on = sorted(int(n) for n, v in q.load("transcript")["pages"].items()
            if pipeline._occurs(sym["symbol"], (q.load("survey").get(n) or {}).get("text", "")))
data = answers(q)
for e in data["glossary"]:
    g = next(x for x in q.load("glossary")["entries"] if x["id"] == e["id"])
    if g["symbol"] == sym["symbol"]:
        e["meaning"] = "a meaning the author corrected"
n0 = len(q.calls(100000))
pipeline.submit_confirm(q, data)
q.update_from("reading", "your answers on the notation")
pipeline.start(q); wait(q, "review")
made = labels_since(q, n0)
read = sorted(int(l.split()[-1]) for l in made if l.startswith("Transcribe page") and "again" not in l)
assert read == on and on and len(on) < len(q.load("transcript")["pages"]), (read, on, sym["symbol"])
rv = q.state["stages"]["review"]
assert rv["status"] == "waiting" and rv["changed"] == [f"page {n} was read again" for n in on], rv
print(f"5. the meaning of {sym['symbol']} changed: only page{'s' if len(on) > 1 else ''} {', '.join(map(str, on))} read again, and shown")

# ---------------------------------------------------------------- 6. a drawing added to a page in step 9, while further on
approve_proofs(p)
again(p, "proofcheck", "your decisions on the proofs and figures")
wait(p, "decide")
n0 = len(p.calls(100000))
t4 = p.load("transcript")["pages"]["4"]["text"] + "\n\n[FIGURE: A second picture of the comparison of the sum with the integral.]"
pipeline.submit_review(p, {"pages": {"4": t4}})
assert [w["page"] for w in pipeline.figures_to_copy(p)] == [4]
p.update_from("figures", "your corrections in step 9"); pipeline.start(p); wait(p, "review")
rv = p.state["stages"]["review"]
assert rv["status"] == "waiting" and rv["changed"] == ["the drawing on page 4 was copied: is the copy what you drew?"], rv
made = labels_since(p, n0)
assert made and all("page 4" in l or "figure 3" in l for l in made), made                      # only the new drawing is copied
new = next(f for f in p.load("figures")["items"] if f["page"] == 4)
pipeline.submit_review(p, {"figures": {new["id"]: "orig"}})
assert not pipeline.figures_to_copy(p)
n0 = len(p.calls(100000))
again(p, "review", "your corrections in step 9")
wait(p, "outline")
made = labels_since(p, n0)
assert "Bring the understanding up to date" in made and "Decide which drawings the paper prints" in made, made
assert not any(l.startswith(("Write the statements", "Transcribe", "Write the proof")) for l in made), made
ol = p.state["stages"]["outline"]
assert ol["status"] == "waiting" and any(new["id"] in c for c in ol["changed"]), ol
print("6. a drawing added to a page in step 9: copied and put to the author there, then decided at the structure; nothing else done again")
print("catch-up ok")
