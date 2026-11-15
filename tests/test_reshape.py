# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The revision is planned from the first draft, never before it. After the author approved the first draft, a plan is
made: results that are parts of one statement, an argument stated once, a repeated proof, a proof to shorten, the
sections. Each change is planned (statements in full, proofs in outline) and checked; the author decides on the plan;
only then the proofs of the accepted changes are written, one on top of the other, and checked; the author reviews
them; the second draft is the first draft with what was accepted. The statements and proofs the author confirmed are
never overwritten, and the first draft is kept."""
import copy
import os
os.environ["SCRIPTAVERA_MOCK_SHAPE"] = "merge,argument,similar,shorten"
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_reshape_")
from scriptavera import drafting, paper as paperlib, pipeline, reshape, writing


def labels_since(p, n0: int) -> list[str]:
    return [c.get("label") or "" for c in p.calls(100000)[n0:]]


# ---------------------------------------------------------------- the changes themselves, on a paper written by hand
def item(i, kind, st, deps=(), pages=(1,)):
    return {"id": i, "kind": kind, "statement": st, "depends": list(deps), "pages": list(pages), "treatment": "keep", "role": ""}


seq = [item("D1", "definition", "A set is nice if it is finite."),
       item("R1", "lemma", "Every nice set $A$ satisfies $f(A) \\le 2$.", ["D1"]),
       item("R2", "lemma", "Every nice set $A$ satisfies $f(A) \\ge 1$.", ["D1"], (2,)),
       item("R3", "proposition", "Every nice set $A$ satisfies $g(A) \\le 5$.", ["D1"], (3,)),
       item("R4", "theorem", "For every nice set $A$ we have $1 \\le f(A) \\le 2$ and $g(A) \\le 5$.", ["R1", "R2", "R3"], (4,))]
secs = [{"title": "Bounds", "items": ["D1", "R1", "R2", "R3"]}, {"title": "The main result", "items": ["R4"]}]
proofs = {"R1": {"text": "Count the elements of $A$ twice; this gives $f(A) \\le 2$."},
          "R2": {"text": "Count the elements of $A$ once; this gives $f(A) \\ge 1$."},
          "R3": {"text": "Count the elements of $A$ five times; this gives $g(A) \\le 5$."},
          "R4": {"text": "Combine Lemma~\\ref{res:R1}, Lemma~\\ref{res:R2} and Proposition~\\ref{res:R3}."}}
fresh = lambda: reshape._empty(*copy.deepcopy((seq, secs, proofs)))                               # noqa: E731
merge = {"id": "H1", "kind": "merge", "affects": ["R1", "R2"], "new": {
    "into": "R1", "kind": "theorem", "name": "", "parts": {"R1": "i", "R2": "ii"},
    "statement": "Every nice set $A$ satisfies\n\\begin{enumerate}\n\\item[\\textup{(i)}] $f(A) \\le 2$;\n\\item[\\textup{(ii)}] $f(A) \\ge 1$.\n\\end{enumerate}",
    "proof": "\\emph{Proof of (i).} Count twice.\n\n\\emph{Proof of (ii).} Count once."}}
m = reshape.apply(fresh(), [merge])
assert m["applied"] == ["H1"] and [x["id"] for x in m["seq"]] == ["D1", "R1", "R3", "R4"] and m["remap"] == {"R2": "R1"}, m["unapplied"]
one = next(x for x in m["seq"] if x["id"] == "R1")
assert one["kind"] == "theorem" and one["pages"] == [1, 2] and "R2" not in m["proofs"] and m["proofs"]["R1"]["text"].startswith("\\emph{Proof of (i)")
assert m["proofs"]["R4"]["text"] == ("Combine Theorem~\\ref{res:R1}\\,\\textup{(i)}, Theorem~\\ref{res:R1}\\,\\textup{(ii)} and "
                                     "Proposition~\\ref{res:R3}."), m["proofs"]["R4"]["text"]
assert next(x for x in m["seq"] if x["id"] == "R4")["depends"] == ["R1", "R3"]
assert seq[1]["kind"] == "lemma" and proofs["R4"]["text"].startswith("Combine Lemma"), "what the author confirmed is not touched"
# results that cannot be one statement: something between them uses one and is used by the other
chain = copy.deepcopy((seq, secs, proofs))
chain[0][3]["depends"] = ["R1"]; chain[2]["R3"]["text"] = "By Lemma~\\ref{res:R1}, count five times."
bad = {"id": "H2", "kind": "merge", "affects": ["R1", "R4"], "new": {**merge["new"], "parts": {"R1": "i", "R4": "ii"}}}
m = reshape.apply(reshape._empty(*chain), [bad])
assert m["applied"] == [] and "cannot be one statement" in m["unapplied"][0]["why"], m["unapplied"]
# an argument stated once: the general statement stands before the first proof that applies it
method = {"id": "H3", "kind": "method", "affects": ["R1", "R3"], "new": {
    "id": "X3", "kind": "lemma", "name": "Counting", "statement": "If the elements of a nice set $A$ are counted $c$ times, the count is at most $c$.",
    "text": "Each element is counted $c$ times.",
    "proofs": {"R1": "Apply Lemma~\\ref{res:X3} with $c = 2$.", "R3": "Apply Lemma~\\ref{res:X3} with $c = 5$."}}}
m = reshape.apply(fresh(), [method])
assert [x["id"] for x in m["seq"]] == ["D1", "X3", "R1", "R2", "R3", "R4"] and m["proofs"]["R3"]["text"].endswith("$c = 5$."), m["unapplied"]
assert "X3" in next(x for x in m["seq"] if x["id"] == "R3")["depends"] and len(m["waived"]) == 2
# new sections, together with a merge: the merged result is taken out of the plan, the order is checked
plan = {"id": "H4", "kind": "sections", "affects": [], "new": {"ids": ["D1", "R1", "R2", "R3", "R4"], "sections": [
    {"title": "The function $f$", "items": ["D1", "R1", "R2"]}, {"title": "The function $g$ and the main result", "items": ["R3", "R4"]}]}}
m = reshape.apply(fresh(), [plan, merge])
assert m["applied"] == ["H1", "H4"] and [(s["title"], s["items"]) for s in m["secs"]] == [
    ("The function $f$", ["D1", "R1"]), ("The function $g$ and the main result", ["R3", "R4"])], m["secs"]
wrong = {"id": "H5", "kind": "sections", "affects": [], "new": {"ids": ["D1", "R1", "R2", "R3", "R4"], "sections": [
    {"title": "The main result", "items": ["R4"]}, {"title": "Bounds", "items": ["D1", "R1", "R2", "R3"]}]}}
m = reshape.apply(fresh(), [wrong])
assert m["applied"] == [] and "before one it uses" in m["unapplied"][0]["why"]
renamed = reshape.apply(fresh(), [{"id": "H6", "kind": "kind", "affects": ["R3"], "new": {"kinds": {"R3": "lemma"}}}])
assert next(x for x in renamed["seq"] if x["id"] == "R3")["kind"] == "lemma"
print("1. merged into one statement with parts and every reference pointed at its part; refused when the parts depend on "
      "something between them; an argument stated once before its first use; new sections checked for their order")

# ---------------------------------------------------------------- in the pipeline
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
pipeline.start(p); wait(p, "scope")
for iid in drafting.open_questions(p):
    drafting.answer_statement(p, iid, "The bound is $\\log(n+1)$.")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "keep"}})
pipeline.start(p); wait(p, "draftcheck")
st = p.state["stages"]
labels = [c["label"] for c in p.calls(100000)]
assert st["draftcheck"]["status"] == "waiting" and st["reshape"]["status"] == "pending", st["reshape"]
assert not any(l.startswith(("Outline the argument of", "Plan the revision", "Plan H")) for l in labels), "nothing of the revision before the first draft"
first = p.load("body")
tex1 = paperlib.blocks_to_latex(first["blocks"])
assert not first["shape"]["applied"] and "\\label{res:R4}" in tex1 and "telescopes" not in tex1, "the first draft is the author's paper"
drafting.submit_draft(p, {})
assert p.load("draft_first")["blocks"] == first["blocks"]
pipeline.start(p); wait(p, "reshapecheck")
st = p.state["stages"]
assert st["reshape"]["status"] == "done" and st["reshapecheck"]["status"] == "waiting" and st["reproofs"]["status"] == "pending", st["reshapecheck"]
labels = [c["label"] for c in p.calls(100000)]
assert sum(1 for l in labels if l.startswith("Outline the argument of")) >= 4 and "Plan the revision" in labels
assert not any(l.startswith("Write out H") for l in labels), "no proof is written before the author decided on the plan"
V = reshape.view(p)
kinds = {pr["kind"]: pr for pr in V["proposals"]}
assert set(kinds) == {"merge", "argument", "similar", "shorten"} and V["written"] == [] and V["dropped"] == [], [pr["kind"] for pr in V["proposals"]]
mg, ag, sm, sh = kinds["merge"], kinds["argument"], kinds["similar"], kinds["shorten"]
assert all(c["outline"] for c in V["proposals"]) and "set up once" in mg["after"][0]["proof"] and "\\begin{enumerate}" in mg["after"][0]["statement"]
assert set(mg["parts"].values()) == {"(i)", "(ii)"} and ag["ai"] and not mg["ai"] and "telescopes" in ag["after"][0]["proof"]
sk = V["skeleton"]
marks = {x["name"]: x["mark"] for sec in sk["new"] for x in sec["items"] if x["mark"]}
assert sum(len(sec["items"]) for sec in sk["new"]) == sum(len(sec["items"]) for sec in sk["now"]) - 1, "one result fewer: two became one"
assert sorted(marks.values()) == ["another argument", "one statement", "proof by reference to another", "proof shortened"], marks
try:
    reshape.submit(p, {mg["id"]: "accept"}); raise SystemExit("every change of the plan needs an answer")
except ValueError as e:
    assert "need your decision" in str(e)
proofs_before = p.load("proofs")
reshape.submit(p, {mg["id"]: "accept", ag["id"]: "accept", sm["id"]: "accept", sh["id"]: "accept"})
pipeline.start(p); wait(p, "reproofcheck")
labels = [c["label"] for c in p.calls(100000)]
wrote = [l for l in labels if l.startswith("Write out H")]
assert [l.split(": ")[1] for l in wrote] == ["merge", "argument", "similar", "shorten"], wrote
assert any(l.startswith("Check the proposed") for l in labels), "an argument of the app's own is also read line by line"
V = reshape.view(p)
got = {c["kind"]: c for c in V["written"]}
assert set(got) == {"merge", "argument", "shorten"} and not any(c["outline"] for c in V["written"]), list(got)
assert [x["title"] for x in V["failed"]] == ["A repeated proof not written again"] and "independent check" in V["failed"][0]["reason"]
assert "\\emph{Proof of (i).}" in got["merge"]["after"][0]["proof"] and got["merge"]["after"][0]["statement"] == mg["after"][0]["statement"]
assert got["shorten"]["on"] == [mg["id"]] and "Theorem~\\ref{res:R2}\\,\\textup{(i)}" in got["shorten"]["after"][0]["proof"], got["shorten"]
assert p.load("proofs") == proofs_before and p.load("body") == first, "nothing of the first draft is overwritten"
try:
    reshape.submit_proofs(p, {mg["id"]: "accept"}); raise SystemExit("every new proof needs an answer")
except ValueError as e:
    assert "need your decision" in str(e)
reshape.submit_proofs(p, {mg["id"]: "accept", ag["id"]: "accept", sh["id"]: "accept"})
pipeline.start(p); wait(p, "decide")
assert p.state["stages"]["redraft"]["status"] == "done"
body = p.load("body")
tex = paperlib.blocks_to_latex(body["blocks"])
assert tex.count("\\begin{theorem}\\label{res:R2}") == 1 and "\\label{res:R4}" not in tex and "\\begin{enumerate}" in tex, tex[:3000]
assert "Combine Theorem~\\ref{res:R2}\\,\\textup{(i)} and Theorem~\\ref{res:R2}\\,\\textup{(ii)}." in tex, "the corollary quotes the parts"
assert "telescopes" in tex and body["shape"]["ai"] == ["res:R1"] and len(body["shape"]["applied"]) == 3
assert p.load("draft_first")["blocks"] == first["blocks"], "the first draft is kept"
assert not (p.load("audit") or {}).get("missing") or all("log" not in str(x) for x in p.load("audit")["missing"]), p.load("audit")["missing"]
plan = p.load("plan")["sections"]
assert "R4" not in [i for s in plan for i in s["items"]] and "R2" in [i for s in plan for i in s["items"]]
print("2. in the pipeline: the first draft is the author's paper; then the plan (statements in full, proofs in outline), "
      "the proofs written only after the author's decision, one dropped by its check, and the second draft with what was accepted")

def decide_all(p):
    """The decisions of the revision, the simplest way: nothing changed."""
    from scriptavera import revision
    it = revision.decision_items(p)
    revision.submit_decisions(p, {"simplify": {x["id"]: "reject" for x in it["simplify"]}, "issues": {x["id"]: {"choice": "keep"} for x in it["issues"]},
                                  "complete": {x["id"]: {"choice": "keep"} for x in it["complete"]},
                                  "additions": {x["id"]: "reject" for x in it["additions"]}})


if p.state["stages"]["decide"]["status"] == "waiting":
    decide_all(p)
pipeline.start(p); wait(p, "finish")
P = p.load("paper")
assert P["ai_use"]["shaped"] and P["ai_use"]["ai_arguments"] == ["res:R1"], P["ai_use"]
out = paperlib.software_statement(P)
assert "The argument of the proof of Lemma~\\ref{res:R1} was proposed by the tool" in out and "to propose how the results and arguments are arranged" in out, out
assert "to write out the proofs in full" in out and "every other argument, are the author's own" in out, out
plain = paperlib.software_statement({**P, "ai_use": {}})
assert "The results and their arguments are the author's own" in plain and "proposed by the tool" not in plain, plain
from scriptavera import final, store
assert store.STAGE_IDS[-1] == "build" and p.state["stages"]["build"]["status"] == "pending"
final.finish(p, {"title": "Bounds for harmonic sums"})
pipeline.start(p); wait(p, "build", ("done",))
built = p.load("built")
assert built["title"] == "Bounds for harmonic sums" and built["problems"] == [], built
assert p.load("paper")["title"] == "Bounds for harmonic sums" and "Bounds for harmonic sums" in (p.dir / "out" / "main.tex").read_text()
print("3. the paper's declaration names the argument the app proposed; the final version is built last, after the title, "
      "and every reference and citation in it resolves")

# ---------------------------------------------------------------- decided again later: only what follows
n0 = len(p.calls(100000))
p.set_stage("reproofcheck", status="waiting", reopened=True)
reshape.submit_proofs(p, {mg["id"]: "reject", ag["id"]: "accept", sh["id"]: "accept"})
p.update_from("redraft", "your review of the new proofs"); pipeline.start(p); wait(p, "decide")
made = labels_since(p, n0)
assert not any(l.startswith(("Outline the argument", "Plan the revision", "Plan H", "Write out", "Write the proof")) for l in made), made
tex = paperlib.blocks_to_latex(p.load("body")["blocks"])
assert "\\label{res:R4}" in tex and "\\begin{enumerate}" not in tex and "telescopes" in tex, "the two results stand as in the first draft"
assert "Combine Theorem" not in tex and p.load("body")["shape"]["applied"] == [ag["id"]], "a change written on top of one not accepted is not used"
assert reshape.view(p)["decisions2"][sh["id"]] == "reject"
n0 = len(p.calls(100000))
p.update_from("reshape", "nothing"); pipeline.start(p); wait(p, "decide")
made = labels_since(p, n0)
assert not any(l.startswith(("Outline the argument", "Plan the revision", "Plan H", "Write out", "Check the plan", "Check H")) for l in made), made
assert p.state["stages"]["reshapecheck"].get("passed_again") and p.state["stages"]["reproofcheck"].get("passed_again")
print("4. decided again: the second draft follows without any proof read or written again; a change written on top of "
      "one that is not accepted goes with it; brought up to date with nothing changed: the plan and the new proofs stand")

# ---------------------------------------------------------------- a plan that does not hold is not shown; nothing proposed: no stop
os.environ["SCRIPTAVERA_MOCK_SHAPE"] = "similar,unsound"
u = new_project()
past_topic(u)
pipeline.start(u); wait(u, "confirm"); pipeline.submit_confirm(u, answers(u))
pipeline.start(u); wait(u, "review")
pipeline.submit_review(u, {"figures": {"f1": "orig"}})
pipeline.start(u); wait(u, "decide")
Vu = reshape.view(u)
assert Vu["proposals"] == [] and [d["title"] for d in Vu["dropped"]] == ["A repeated proof not written again"], Vu["dropped"]
assert "independent reading of the plan" in Vu["dropped"][0]["reason"] and u.state["stages"]["reshapecheck"].get("skipped")
os.environ["SCRIPTAVERA_MOCK_SHAPE"] = ""
q = new_project()
past_topic(q)
pipeline.start(q); wait(q, "confirm"); pipeline.submit_confirm(q, answers(q))
pipeline.start(q); wait(q, "review")
pipeline.submit_review(q, {"figures": {"f1": "orig"}})
pipeline.start(q); wait(q, "decide")
sq = q.state["stages"]
assert sq["reshapecheck"]["status"] == "done" and sq["reshapecheck"].get("skipped") and reshape.view(q)["proposals"] == [], sq["reshapecheck"]
assert sq["reproofcheck"].get("skipped") and sq["redraft"]["status"] == "done" and not q.load("body")["shape"]["applied"]
assert not any((c.get("label") or "").startswith(("Write out H", "Join the results")) for c in q.calls(100000)[-12:])
print("5. a plan an independent reading does not confirm is not put to the author; nothing proposed: both stops are passed "
      "by themselves and the second draft is the first draft")

# ---------------------------------------------------------------- a paper saved by an earlier version


def as_saved_before(proj, layout=9, **stages):
    """The project as an earlier version saved it: without the steps of the revision, the given steps in the given state."""
    def f(st):
        st["layout"] = layout
        for sid in ("reshape", "reshapecheck", "reproofs", "reproofcheck", "redraft", "build"):
            st["stages"].pop(sid, None)
        if layout == 10:
            st["stages"]["reshape"] = st["stages"]["reshapecheck"] = {"status": "pending"}
        for sid, v in stages.items():
            st["stages"][sid] = v
        st.pop("catchup", None)
    proj.update_state(f)
    proj.remove("reshape")
    return store.get(proj.id)


body_before = q.load("body")
# (a) the author is at the review of the proofs, with a draft made earlier waiting to be brought up to date: nothing is reset
r = as_saved_before(q, proofcheck={"status": "waiting"}, draft={"status": "stale", "was": "done"}, draftcheck={"status": "stale", "was": "waiting"},
                    verify={"status": "pending"}, referee={"status": "pending"}, decide={"status": "pending"})
sr = r.state["stages"]
assert r.state["layout"] == store.LAYOUT and all(sr[x]["status"] == "pending" for x in ("reshape", "reshapecheck", "reproofs", "reproofcheck", "redraft", "build")), sr
assert sr["draft"]["status"] == "stale" and r.load("body") == body_before and "simplify" not in sr, "the draft made before is kept"
# the same paper as version 10 left it (the two steps it had, not yet run): the same
r = as_saved_before(r, layout=10, proofcheck={"status": "waiting"}, draft={"status": "stale", "was": "done"}, draftcheck={"status": "stale", "was": "waiting"})
sr = r.state["stages"]
assert sr["reshape"]["status"] == "pending" and sr["draft"]["status"] == "stale" and r.load("body") == body_before
# (b) the author is past the first draft: the plan of the revision is made next, from the first draft
r = as_saved_before(r, proofcheck={"status": "done"}, draft={"status": "done"}, draftcheck={"status": "done"}, verify={"status": "done"},
                    referee={"status": "done"}, decide={"status": "waiting"})
sr = r.state["stages"]
assert sr["reshape"]["status"] == "pending" and sr["draft"]["status"] == "done" and sr["verify"]["status"] == "stale" \
    and r.state["catchup"]["from"] == "reshape" and r.load("body") == body_before, sr
n0 = len(r.calls(100000))
pipeline.start(r); wait(r, "decide")
made = labels_since(r, n0)
assert made and all(l.startswith(("Outline the argument of", "Plan the revision")) for l in made), made
assert r.load("body")["blocks"] == body_before["blocks"]
# (c) a finished paper is not touched
def fin(st):
    for sid in store.STAGE_IDS:
        st["stages"][sid] = {"status": "done"}
r.update_state(fin)
r = as_saved_before(r)
assert all(r.state["stages"][sid]["status"] == "done" for sid in store.STAGE_IDS) and r.state["stages"]["reshape"].get("skipped") \
    and r.state["stages"]["build"].get("skipped")
print("6. a paper saved by an earlier version: nothing is reset; the revision is planned from the first draft when the "
      "paper gets there; a finished paper is not touched")
print("reshape ok")
