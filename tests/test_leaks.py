# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A model's own reasoning never reaches the paper: it is recognised, asked for again, removed, and a 'repair'
that rewrites the text around the formulas is refused."""
from helpers import isolate, new_project
isolate("scriptavera_leaks_")
import tests.simulator as sim
from scriptavera import pipeline, store, writing
from scriptavera.texutil import drop_leaks, leaks

# ---------------------------------------------------------------- what counts as reasoning
bad = [
    "Wait! In ORIGINAL TRANSCRIPT, page 3 has: because if $m > \\frac{i(G)}{2}$.",
    "Wait, does the author say elements or cyclic subgroups?",
    "So wherever the diff found it, it should be restored. Let's check: on page 3 there is only one.",
    "The instruction says: [currently typeset as: m = \\frac{i(G)}{2}]",
    "Keep every formula character for character!",
    "Wait, let's check D1 statement in DOSSIER.",
]
good = [p for p in sim.PAGES] + [
    "Suppose, for a contradiction, that $m > \\frac{i(G)}{2}$. Then we would need more elements than there are.",
    "We wait until the end of the proof to choose $\\varepsilon$; let us check the case $n = 1$ first.",
    "\\begin{lemma}\\label{res:R1} For every prime $p \\ge 5$ we have $p \\le i(G) - 2m + 3$. \\end{lemma}",
]
assert all(leaks(x) for x in bad), [x for x in bad if not leaks(x)]
assert not any(leaks(x) for x in good), [x for x in good if leaks(x)]
text, gone = drop_leaks("First paragraph of the proof.\n\nWait! Let's check page 4.\n\nSecond paragraph.")
assert gone == 1 and text == "First paragraph of the proof.\n\nSecond paragraph."
print("1. reasoning and quoted instructions are recognised; ordinary mathematical writing is not")

# ---------------------------------------------------------------- a repair may only put formulas back
old = ("Hence we need more elements than there are, a contradiction. In the case where $m = \\frac{i(G)}{2}$ "
       "every remaining class has order $2$, and hence $G$ is a $\\{2,3\\}$-group.\n\n") * 3
fixed = old.replace("$m = \\frac{i(G)}{2}$", "$m > \\frac{i(G)}{2}$", 1)
assert writing.repair_is_faithful(old, fixed)
assert not writing.repair_is_faithful(old, old + "\n\nWait, on page 3 the author writes something else.")
assert not writing.repair_is_faithful(old, "Consider a different argument entirely. " * 30)
print("2. a repair that only restores formulas is used; one that rewrites the text or adds reasoning is refused")

# ---------------------------------------------------------------- the writer is asked again, then cleaned
orig = sim.respond
state = {"n": 0}


def respond(label, system, user, vision, model=None):
    if label.startswith("Write section 9"):
        state["n"] += 1
        if state["n"] == 1:
            return "\\section{Bounds}\n\nWait! Let's check the DOSSIER again.\n\nThe bound holds for $p \\ge 5$."
        assert "your previous answer contained your own thinking" in user
        return "\\section{Bounds}\n\nThe bound holds for $p \\ge 5$."
    if label.startswith("Write section 8"):
        return "\\section{More}\n\nWait, let's check.\n\nThe second bound follows."
    return orig(label, system, user, vision, model)


sim.respond = respond
p = new_project()
run = pipeline.Run(p)
run.stage = "structure"
res = run.ask("writer", "system", "user", label="Write section 9 (pages 1-1)", max_out=500)
assert state["n"] == 2 and not leaks(res.text) and "p \\ge 5" in res.text
res = run.ask("writer", "system", "user", label="Write section 8 (pages 1-1)", max_out=500)
assert not leaks(res.text) and "second bound follows" in res.text and "Wait" not in res.text
sim.respond = orig
print("3. a writer's answer with reasoning is asked for again; what is still reasoning is removed")

# ---------------------------------------------------------------- a draft written before the guard is written again
p.save("body", {"blocks": [{"type": "para", "text": "Wait! In ORIGINAL TRANSCRIPT, page 3 has it."}]})
for sid in ("structure", "simplify"):
    p.set_stage(sid, status="done")
assert pipeline.reset_leaked_draft(p) and p.load("body") is None and p.state["stages"]["structure"]["status"] == "pending"
p.save("body", {"blocks": [{"type": "para", "text": "Wait! again"}]})
assert not pipeline.reset_leaked_draft(p), "only once"
print("4. a draft that contains reasoning is written again, once")
print("leaks ok")
