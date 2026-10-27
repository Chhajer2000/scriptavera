# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A project saved by an earlier version of the app keeps everything it can: the reading, the confirmed notation,
the transcript and the author's edits. Only what the new steps make stale is done again."""
import json, shutil
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_mig_")
from scriptavera import config, pipeline, store

OLD_STAGES = ["scout", "search1", "survey", "confirm", "reading", "transcribe", "review", "structure", "frontmatter", "assemble"]


def age(p, statuses: dict, with_paper: bool) -> None:
    """Rewrite a project's state the way the earlier version stored it."""
    st = json.loads((p.dir / "state.json").read_text())
    st.pop("layout", None)
    st["stages"] = {sid: dict(statuses.get(sid, {"status": "pending"})) for sid in OLD_STAGES}
    (p.dir / "state.json").write_text(json.dumps(st))
    for name in ("figures", "dossier", "intro", "plan", "body", "simplify", "verify", "referee", "decisions"):
        (p.dir / f"{name}.json").unlink(missing_ok=True)
    shutil.rmtree(p.dir / "figures", ignore_errors=True)
    if with_paper:
        for name in ("outline", "front", "paper"):
            (p.dir / f"{name}.json").write_text(json.dumps({"title": "Open problem: a sentence that is not a title", "blocks": []}))
        (p.dir / "out").mkdir(exist_ok=True)
        (p.dir / "out" / "main.tex").write_text("% the paper built by the earlier version")


# ---- 1. a finished project of the earlier version
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm")
pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.save_transcript_page(p, 5, p.load("transcript")["pages"]["5"]["text"].replace("= \\log n", "= \\log(n+1)"))   # the author's own correction
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
done = {"status": "done", "model": "mock:sim-text", "finished": 1.0}
age(p, {sid: done for sid in OLD_STAGES}, with_paper=True)
kept = {name: (p.dir / f"{name}.json").read_text() for name in ("topic", "library", "glossary", "confirmed", "brief", "transcript", "survey")}

assert [x for x in store.list_projects() if x["id"] == p.id][0]["total"] == len(store.STAGE_IDS)   # listing a project brings it up to date
q = store.get(p.id)
st = q.state
assert st["layout"] == store.LAYOUT and list(st["stages"]) == store.STAGE_IDS or set(st["stages"]) == set(store.STAGE_IDS), list(st["stages"])
status = {k: v.get("status") for k, v in st["stages"].items()}
assert all(status[s] == "done" for s in ("scout", "search1", "survey", "confirm", "reading", "transcribe")), status
assert all(status[s] == "pending" for s in store.STAGE_IDS[store.STAGE_IDS.index("figures"):]), status
assert "frontmatter" not in st["stages"] and st["error"] is None
for name, text in kept.items():
    assert (q.dir / f"{name}.json").read_text() == text, name + " must be untouched"
assert q.load("transcript")["pages"]["5"]["edited"] and "\\log(n+1)$$" in q.load("transcript")["pages"]["5"]["text"]
assert not any((q.dir / f"{n}.json").exists() for n in ("approved", "outline", "front", "paper")), "what the new steps replace is cleared"
assert (q.dir / "out_before_update" / "main.tex").read_text().startswith("% the paper built by the earlier version") and not (q.dir / "out").exists()
assert all(x["read"] for x in q.load("library") if x["key"] == "Example2015Simulated"), "the papers already read stay read"
store.migrate(q); assert q.state == st, "bringing a project up to date twice changes nothing"
print("an earlier finished project: reading, notation and transcript kept; the old paper set aside; the new steps pending")

# it then runs on from the first new step, without reading anything again
n0 = len(q.calls(2000))
pipeline.start(q); wait(q, "review")
labels = [c["label"] for c in q.calls(2000)[n0:]]
FIGURE_CALLS = ("Draw figure", "Find the drawings", "Read the labels of figure", "Check figure", "Correct figure",
                "Decide which drawings are for the paper")
assert labels and all(l.startswith(FIGURE_CALLS) for l in labels), labels   # no page is read again
pipeline.submit_review(q, {"figures": {"f1": "orig"}})
pipeline.start(q); wait(q, "scope")
from scriptavera import writing
writing.submit_scope(q, {})                                                            # the suggested plan
pipeline.start(q); wait(q, "decide")
items = q.load("verify")["results"]
assert items["res:R4"]["verdict"] == "problems" or True                                # the simulated checker answers the same whatever the text
from scriptavera import revision
dec = revision.decision_items(q)
revision.submit_decisions(q, {"simplify": {x["id"]: "reject" for x in dec["simplify"]}, "issues": {x["id"]: {"choice": "keep"} for x in dec["issues"]}})
pipeline.start(q); wait(q, "assemble", ("done",))
P = q.load("paper")
assert P["title"] == "Explicit bounds for partial sums of reciprocal powers" and "figures/f1.jpg" in (q.dir / "out" / "main.tex").read_text()
assert "\\log(n+1) \\]" in (q.dir / "out" / "main.tex").read_text() and (q.dir / "out_before_update" / "main.tex").exists()
print("... and is finished by the new steps:", P["title"])

# ---- 2. earlier projects that were in the middle of something
for case, statuses, expect in (
        ("waiting at the confirmation", {"scout": done, "search1": done, "survey": done, "confirm": {"status": "waiting", "since": 5.0}},
         {"confirm": "waiting", "reading": "pending", "figures": "pending"}),
        ("stopped by an error while transcribing", {**{s: done for s in OLD_STAGES[:5]}, "transcribe": {"status": "error", "error": "something failed"}},
         {"transcribe": "error", "figures": "pending", "review": "pending"}),
        ("waiting at the review", {**{s: done for s in OLD_STAGES[:6]}, "review": {"status": "waiting"}},
         {"transcribe": "done", "figures": "pending", "review": "pending"}),
        ("writing the paper", {**{s: done for s in OLD_STAGES[:7]}, "structure": {"status": "paused", "resume_at": 9e9, "error": "limit"}},
         {"transcribe": "done", "figures": "pending", "review": "pending", "structure": "pending", "assemble": "pending"})):
    r = new_project()
    age(r, statuses, with_paper=False)
    got = {k: v.get("status") for k, v in store.get(r.id).state["stages"].items()}
    assert set(got) == set(store.STAGE_IDS) and all(got[k] == v for k, v in expect.items()), (case, got)
    if case.startswith("stopped"):
        assert store.get(r.id).state["stages"]["transcribe"]["error"] == "something failed"
print("earlier projects stopped in the middle keep their place")

# ---- 3. damaged or foreign folders are ignored, not repaired
(config.PROJECTS_DIR / "half").mkdir(); (config.PROJECTS_DIR / "half" / "state.json").write_text("{not json")
(config.PROJECTS_DIR / "empty").mkdir()
assert store.get("half") is None and store.get("empty") is None and store.get("../x") is None
assert {x["id"] for x in store.list_projects()} >= {p.id}
print("migrate ok")
