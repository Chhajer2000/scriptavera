# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Readings that came back broken, and papers an update of the app reopened. A reading keeps only the page: no part
of the model's answer, no web markup, no endless repetition (such a reading is asked for again). The review opens only
when the steps before it are done; a reopened paper carries on by itself; a name taken from a file is plain text."""
import os
os.environ["SCRIPTAVERA_MOCK_BROKEN"] = "2,3"           # page 2 without its opening tag and with <u>, page 3 looping once
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_repairs_")
import tests.simulator as simulator
from scriptavera import pipeline, store
from scriptavera.server import _tidy_name
from scriptavera.texutil import answer_transcript, clean_transcript, collapse_loops, looping

# ---- the reading in an answer, however its tags came out
assert answer_transcript("<transcript>\nA\n</transcript>\n<uncertain>\nnone\n</uncertain>") == "A"
assert answer_transcript("A\n</transcript>\n<uncertain>\nnone\n</uncertain>\n<changes>\nx\n</changes>") == "A"   # no opening tag
assert answer_transcript("<transcript>\nA \\quad \\quad\n<transcript>\nB\n</transcript>") == "B"               # started twice
assert answer_transcript("<kind>content</kind>\n<transcript>\nA\n</transcript>\n<symbols>\nx\n</symbols>") == "A"
assert answer_transcript("A\n<uncertain>\nnone\n</uncertain>") == "A" and answer_transcript("plain") == "plain"
assert clean_transcript("<u>Def</u> A group $G$ is <b>acyclic</b> if $a<b$<br>and $c > d$.") == "Def A group $G$ is acyclic if $a<b$\nand $c > d$."
assert looping("x " + "\\quad " * 30) and not looping("$x_1 + x_2 + \\dots + x_n$ " * 3) and not looping("_" * 60)
assert collapse_loops("x " + "\\quad " * 40 + "y") == "x \\quad y"
assert _tidy_name("Martin_Palmer_Goettingen_Talk_Notes") == "Martin Palmer Goettingen Talk Notes" and _tidy_name("my_notes v2") == "my_notes v2"
print("1. the reading in an answer: no answer tags, no markup, no endless repetition; file names get their spaces")

# ---- broken answers in a run: page 2 repaired as it comes, page 3 read again
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
tr = p.load("transcript")["pages"]
assert tr["2"]["text"] == simulator._page_text(2), tr["2"]["text"][:200]
assert tr["3"]["text"] == simulator._page_text(3) and not any("broke down" in u["issue"] for u in tr["3"]["uncertain"])
labels = [c["label"] for c in p.calls(5000)]
assert labels.count("Transcribe page 3") == 1 and labels.count("Transcribe page 3 (again)") == 1, labels
assert pipeline.adding_now(p) is None, "at the review with nothing being added"
os.environ["SCRIPTAVERA_MOCK_BROKEN"] = "3!"            # the second reading breaks down too: cut, and the author told
r = new_project()
past_topic(r)
pipeline.start(r); wait(r, "confirm"); pipeline.submit_confirm(r, answers(r))
pipeline.start(r); wait(r, "review")
page3 = r.load("transcript")["pages"]["3"]
assert not looping(page3["text"]) and any("broke down twice" in u["issue"] for u in page3["uncertain"]), page3
print("2. a page answered without its opening tag is repaired; a page that looped is read again, and if it loops again the author is told")

# ---- a paper saved by an earlier version: repaired, reopened only where needed, carrying on by itself
os.environ["SCRIPTAVERA_MOCK_BROKEN"] = ""
q = new_project()
past_topic(q)
pipeline.start(q); wait(q, "confirm"); pipeline.submit_confirm(q, answers(q))
pipeline.start(q); wait(q, "review")
tr = q.load("transcript")
tr["pages"]["2"]["text"] = "<u>Claim</u>" + tr["pages"]["2"]["text"][5:] + "\n</transcript>\n<uncertain>\nnone\n</uncertain>"
tr["pages"]["4"]["text"] = tr["pages"]["4"]["text"][:50] + " " + "\\quad " * 200
q.save("transcript", tr)
q.update_state(lambda s: (s.update(layout=6), s["meta"].update(name="Some_Talk_Notes")))
q2 = store.get(q.id)
st = q2.state
assert st["layout"] == store.LAYOUT and st["meta"]["name"] == "Some Talk Notes", st["meta"]["name"]
pages = q2.load("transcript")["pages"]
assert pages["2"]["text"] == simulator._page_text(2) and "4" not in pages, "repaired; the looping page is read again"
assert {k: st["stages"][k]["status"] for k in ("transcribe", "figures", "review")} == {"transcribe": "pending", "figures": "pending", "review": "pending"}
assert st.get("restart") and not st["stages"]["review"].get("since") and st["stages"]["figures"].get("reset")
assert pipeline.adding_now(q2) is None, "the review is not shown before the steps before it are done"
n0 = len(q2.calls(5000))
pipeline.start(q2)
assert not q2.state.get("restart"), "starting clears the request to carry on"
wait(q2, "review")
labels = [c["label"] for c in q2.calls(5000)[n0:]]
assert [l for l in labels if l.startswith("Transcribe")] == ["Transcribe page 4"], labels      # only that page read again
assert q2.load("transcript")["pages"]["4"]["text"] == simulator._page_text(4)
p.update_state(lambda s: s.update(layout=6))
done = store.get(p.id).state
assert done["layout"] == store.LAYOUT and not done.get("restart") and done["stages"]["review"]["status"] == "waiting", "a paper at the review without broken readings is left as it is"
print("3. a paper from an earlier version: readings repaired, the looping page read again, the name plain; it carries on by itself")

# ---- a step an earlier version paused for the strongest model: tried again at once (a model almost as good may take
# over now), without the old offer of a weaker model, and showing only the calls of the run under way
import json, time
w = new_project()
past_topic(w)
pipeline.start(w); wait(w, "confirm"); pipeline.submit_confirm(w, answers(w))
pipeline.start(w); wait(w, "review")
t0 = time.time()
msg = "The strongest models for the 'Handwriting transcriber' job are not free right now: gemini-3.8-flash (daily limit reached). A weaker model is not used for this job unless you allow it."
with open(w.dir / "calls.jsonl", "a", encoding="utf-8") as fh:
    for dt, label in ((-7200, "Draw figure 1 (page 1)"), (-600, "Decide which drawings are for the paper"), (-300, "Draw figure 1 (page 1)")):
        fh.write(json.dumps({"role": "vision_best", "label": label, "provider": "gemini", "model": "m", "ok": True, "stage": "figures", "t": t0 + dt}) + "\n")
def paused(s):
    s.update(layout=6, error={"stage": "figures", "message": msg, "resume_at": t0 + 9 * 3600, "kind": "limits"})
    s["stages"]["figures"] = {"status": "paused", "error": msg, "resume_at": t0 + 9 * 3600, "reason": "limits", "auto_tries": 2, "weaker": "gemini-3.7-flash"}
    s["stages"]["review"] = {"status": "pending"}
w.update_state(paused)
fs = store.get(w.id).state["stages"]["figures"]
assert fs["status"] == "paused" and fs["resume_at"] <= time.time() and not fs["weaker"] and "allow it" not in fs["error"], fs
assert fs["reset"] == t0 - 601, (fs["reset"], t0)                 # the drawings of the earlier run are not this run's
assert "allow it" not in store.get(w.id).state["error"]["message"] and not store.get(w.id).state.get("restart")
print("4. a step paused for the strongest model by an earlier version tries again at once; the offer of a weaker model is gone")
print("repairs ok")
