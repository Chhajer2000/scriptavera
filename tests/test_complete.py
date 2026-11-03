# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What counts as a page left out of a text written from the notes, and the first draft holding every statement and
proof the author approved, exactly as approved: the program puts the paper together, so nothing approved is dropped
or changed on the way, and a model writes only the text between them."""
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


p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm")
pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
pipeline.submit_review(p, {"figures": {"f1": "orig"}})
pipeline.start(p); wait(p, "scope")
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "credit"}})
pipeline.start(p); wait(p, "decide")
from scriptavera import drafting, paper as paperlib
a = p.load("audit")
assert a["total"] and a["preserved"] == a["total"] and not a["missing"] and not a["altered"], a
body = paperlib.blocks_to_latex(p.load("body")["blocks"])
for x in drafting.kept_items(p):
    assert drafting.label_of(x) in body, x["id"]
proofs = p.load("proofs")["items"]
for iid, rec in proofs.items():
    assert rec["text"].strip()[:60] in body, iid
assert "In this section we treat" in body, "the text between the results is written"
print("2. the first draft holds every statement and proof as approved, with the text between them")
print("complete ok")
