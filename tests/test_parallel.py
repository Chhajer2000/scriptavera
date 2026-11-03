# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Steps whose requests do not depend on each other send them side by side, spread over the keys; nothing else
changes. Spreading never puts a job on a weaker model: only the keys of one and the same model take turns. The
final transcription stays one page at a time, each page with the end of the previous page and, new, the start of
the next page's draft."""
import threading, time
from helpers import answers, isolate, new_project, wait
isolate("scriptavera_parallel_")
import tests.simulator as sim
from scriptavera import pipeline
from scriptavera.llm import Model, Router, router

# ---- the router: the least busy key of the same model first; the order of the models never changes
r = Router(keys={})
a0, a1 = Model(provider="x", id="A", key_index=0), Model(provider="x", id="A", key_index=1)
b0 = Model(provider="x", id="B", key_index=0)
cands = [a0, a1, b0]
got = [r._take(cands, set()) for _ in range(4)]
assert got[:2] == [a0, a1] or got[:2] == [a1, a0], "two requests at once go to two keys"
assert all(m.id == "A" for m in got), "never the weaker model while the best one has a key"
assert a0.in_flight + a1.in_flight == 4 and b0.in_flight == 0
a0.in_flight, a1.in_flight = 3, 0
assert r._take(cands, set()) is a1
assert r._take(cands, {id(a0), id(a1)}) is b0, "the next model only when every key of the best was tried"
print("1. the router spreads requests over the keys of the best model, and never moves a job to a weaker one")

# ---- three keys for each strong simulated model
base = sim.models


def models():
    out = base()
    for k in (1, 2):
        for m in base():
            if m.id in sim.TOP:
                m.key_index, m.key = k, f"key{k}"
                out.append(m)
    return out


sim.models = models
lock, now, peak, keys, used = threading.Lock(), [0], {}, {}, {}
real = sim.respond


def respond(label, system, user, vision, model=None):
    kind = label.split(" ")[0]
    with lock:
        now[0] += 1
        peak[kind] = max(peak.get(kind, 0), now[0])
        keys.setdefault(kind, set()).add(model.key_index if model else -1)
        used.setdefault(kind, set()).add(model.id if model else "")
    try:
        time.sleep(0.12)
        return real(label, system, user, vision, model)
    finally:
        with lock:
            now[0] -= 1


sim.respond = respond
router.discover(force=True)
assert router.lanes("vision_best", True) == 3 and router.lanes("reader") == 3
p = new_project(pages=10)
assert pipeline._workers(pipeline.Run(p), "vision_best", True, 10) == 6, "two at a time per key, three keys"
pipeline.start(p); wait(p, "topiccheck")
assert peak["Scout"] >= 3 and len(keys["Scout"]) == 3 and used["Scout"] == {"sim-vision"}, (peak, keys, used)
assert len(p.load("scout")) >= 5, "still five pages first"
print(f"2. scout: {peak['Scout']} pages at a time over 3 keys, always the best model")
pipeline.submit_topic(p, {"action": "confirm"})
pipeline.start(p); wait(p, "confirm")
if p.state["stages"]["confirm"]["status"] == "waiting":
    pipeline.submit_confirm(p, answers(p))
assert peak["Survey"] >= 3 and len(keys["Survey"]) == 3, (peak, keys)
pipeline.start(p); wait(p, "review", seconds=900)
assert peak.get("Read", 0) >= 2 and len(keys["Read"]) >= 2, (peak, keys)
print(f"3. notation survey {peak['Survey']} pages at a time, papers read {peak['Read']} at a time")

# ---- the final transcription: in order, one page at a time, with the start of the next page
calls = [c for c in p.calls(10000) if (c.get("label") or "").startswith("Transcribe page")]
order = [int(c["label"].split()[2]) for c in calls]
assert order == sorted(order), order
assert peak["Transcribe"] == 1, "never two transcription requests at once"
first, last = sim.SEEN["Transcribe page 1"], sim.SEEN[f"Transcribe page {order[-1]}"]
assert "START OF THE NEXT PAGE" in first and "(this is the last page)" not in first
assert "(this is the last page)" in last and "(this is the first page)" in first
nxt = first.split("START OF THE NEXT PAGE", 1)[1].split("\n\n", 2)[0]
assert len(nxt) > 40 and "[FIGURE" not in nxt, nxt
print("4. final transcription: one page after another, each with the end of the previous and the start of the next")
print("parallel ok")
