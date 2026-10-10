# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The first draft holds every page of the notes. A writer that ends its answer early is caught at once and the
rest is written again in smaller parts; a page that still does not come out stops the step with its number, and
no later step works on an incomplete draft. A draft written before this check is written again, once."""
import re
from helpers import answers, isolate, new_project, past_topic, wait
isolate("scriptavera_complete_")
import tests.simulator as sim
from scriptavera import audit as fidelity, pipeline, writing

# ---------------------------------------------------------------- what counts as a page left out
pages = [{"n": 1, "text": "We have $$a = b$$ and $$c = d$$ and $$e = f$$."}, {"n": 2, "text": "Hence $$x \\le y$$."}]
ap = pipeline.audit_pages(pages)
whole = "%%% page 1\n\\[a = b\\] \\[c = d\\] \\[e = f\\]\n%%% page 2\n\\[x \\le y\\]"
assert pipeline.page_gaps(ap, fidelity.audit(ap, whole), pipeline.page_marks(whole)) == []
changed = whole.replace("x \\le y", "x < y").replace("c = d", "c = e")
assert pipeline.page_gaps(ap, fidelity.audit(ap, changed), pipeline.page_marks(changed)) == [], "a changed formula goes to the author"
short = "%%% page 1\n\\[a = b\\] and then"
assert pipeline.page_gaps(ap, fidelity.audit(ap, short), pipeline.page_marks(short)) == [1, 2]
assert pipeline.page_gaps(ap, fidelity.audit(ap, short, "$$c = d$$ $$e = f$$"), pipeline.page_marks(short)) == [2], "left out on purpose"
print("1. a page is missing when most of its formulas are; a changed formula, or one left out on purpose, is not")

# ---------------------------------------------------------------- a writer that stops early
orig = sim.respond
calls = {"sec3": 0, "sec4": 0}
mode = {"drop5": False}


def respond(label, system, user, vision, model=None):
    out = orig(label, system, user, vision, model)
    if label.startswith("Write section 3"):
        calls["sec3"] += 1
        if calls["sec3"] == 1:                                    # ends after the first of its three pages
            assert "(pages 2-4)" in label, label
            return out.split("%%% page 3", 1)[0] + "%%% page 3\nLet $s$ be the number of elements left after the assignment. Then"
    if label.startswith("Write section 4"):
        calls["sec4"] += 1
        if mode["drop5"]:                                         # never writes page 5
            return re.sub(r"(?s)%%% page 5\n.*?(?=%%% page|\Z)", "", out)
    return out


sim.respond = respond
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm")
pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
pipeline.start(p); wait(p, "scope")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "credit"}})
pipeline.start(p); wait(p, "decide")
lay = p.load("structure_partial")["layout"]
assert [u["pages"] for u in lay if u["sec"] == 1] == [[2], [3, 4]], lay
labels = [c["label"] for c in p.calls(500)]
assert "Write section 3, part 2 (pages 3-4)" in labels, labels
a = p.load("audit")
assert not a["missing"] and a["preserved"] == a["total"], a
msgs = [x.get("message", "") for x in pipeline.events_after(p.id, 0) if x["type"] == "log"]
assert any("stopped short: the material of page 4 did not come out. Pages 3-4 are written again" in m for m in msgs), [m for m in msgs if "page" in m]
assert "elements left after the assignment" not in str(p.load("body")), "the unfinished sentence is not kept"
print("2. a part that ends early keeps what it wrote; the rest is written again as the next part, and the draft is whole")

# ---------------------------------------------------------------- a page that never comes out stops the step
mode["drop5"] = True
p.reset_from("structure")
calls["sec4"] = 0
pipeline.start(p); wait(p, "structure", statuses=("error",))
st = p.state["stages"]
assert "page 5 of your notes" in st["structure"]["error"], st["structure"]
assert st["simplify"]["status"] == "pending" and p.load("body") is None, "nothing after it works on an incomplete draft"
assert calls["sec4"] <= 8, calls
mode["drop5"] = False
before = calls["sec4"]
pipeline.start(p); wait(p, "decide")
assert calls["sec4"] - before <= 2, "only the pages that were missing are written again"
assert not p.load("audit")["missing"]
print("3. a page still missing after writing again stops the step with its number; Continue writes only that page")

# ---------------------------------------------------------------- a draft written before the check
assert not pipeline.reset_incomplete_draft(p), "a whole draft is kept"
rep = p.load("audit")
pg = pipeline.audit_pages(pipeline.writing_pages(p)[0])
rep["missing"] = [{"page": x["n"], "source": f["tex"], "display": f["display"]} for x in pg if x["n"] > 2 for f in fidelity.formulas(x["text"])]
p.save("audit", rep)
assert pipeline.reset_incomplete_draft(p) and p.state["stages"]["structure"]["status"] == "pending" and p.load("body") is None
assert p.state["stages"]["scope"]["status"] == "done" and p.load("intro"), "everything before the draft is kept"
p.save("audit", rep); p.save("body", {"blocks": []}); p.set_stage("structure", status="done")
assert not pipeline.reset_incomplete_draft(p), "only once"
sim.respond = orig
print("4. a draft that stopped part of the way is written again, once, keeping everything before it")
print("complete ok")
