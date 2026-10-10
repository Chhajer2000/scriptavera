# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Several papers at once: each runs on its own, the list of papers says which is working, waiting, left in
between or completed, and leaving one to work on another loses nothing."""
import os, time
os.environ["SCRIPTAVERA_MOCK_DELAY"] = "0.05"
from helpers import isolate, new_project, wait
isolate("scriptavera_many_")
import tests.simulator as sim
from scriptavera import pipeline, store

a, b = new_project({"name": "First notes"}), new_project({"name": "Second notes"})
pipeline.start(a); pipeline.start(b)
time.sleep(0.3)
assert pipeline.is_running(a.id) and pipeline.is_running(b.id), "two papers run at the same time"
wait(a, "topiccheck"); wait(b, "topiccheck")
rows = {x["id"]: x for x in store.list_projects()}
assert rows[a.id]["waiting"] == "topiccheck" and rows[b.id]["waiting"] == "topiccheck"
assert rows[a.id]["current_name"] == "Your topic" and not rows[a.id]["completed"]
print("1. two papers run side by side; each stops at its own question")

pipeline.submit_topic(a, {"action": "confirm"})
sim.DELAY = 0.6                                                  # slow enough to leave it in the middle of a step
pipeline.start(a)
time.sleep(0.4)
pipeline.stop(a.id); pipeline.wait_stopped(a.id, 10)            # left in between
rows = {x["id"]: x for x in store.list_projects()}
assert rows[a.id]["done"] >= 3 and not rows[a.id]["waiting"] and rows[b.id]["waiting"] == "topiccheck"
left_at = rows[a.id]["current"]
sim.DELAY = 0.05
pipeline.start(a); wait(a, "confirm")                            # and taken up again where it was left
assert a.state["stages"]["topiccheck"]["status"] == "done" and a.load("survey")
print("2. a paper left in between keeps everything; opened again, it continues from", left_at)
print("many ok")
