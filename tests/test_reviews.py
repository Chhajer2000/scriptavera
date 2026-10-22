# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Review and proofs: a referee's report split into points; a language point carried out by itself and only as
wording; a mathematical point put to the author with its exact change; nothing else touched; the letter written
from what was done. The same for the journal's proof corrections and queries."""
import time
from helpers import isolate, new_project
isolate("scriptavera_reviews_")
import tests.simulator as sim  # noqa: F401  (the simulated models)
import ast, pathlib
_src = (pathlib.Path(__file__).parent / "test_editor.py").read_text(encoding="utf-8")
TEX = ast.literal_eval(_src[_src.index('TEX = r"""') + 6:_src.index('"""', _src.index('TEX = r"""') + 10) + 3])   # the editor test's paper
from scriptavera import assist, reviews, workspace as W

p = new_project()
(p.dir / "out").mkdir()
(p.dir / "out" / "main.tex").write_text(TEX, encoding="utf-8")
(p.dir / "out" / "references.bib").write_text("@article{Euler1735,\n  title = {De summis},\n}\n", encoding="utf-8")
W.create(p)
before = {rel: W.read_text(p, rel)["text"] for rel in W.files(p) if rel.endswith(".tex")}


def settle(rid):
    for _ in range(400):
        if not reviews.is_busy(p):
            return reviews.get_round(p, rid)
        time.sleep(0.05)
    raise SystemExit("the report was not worked through")


r = settle(reviews.start(p, "referee", "Report on the paper. 1. The first sentence of Section 1 should be clarified. "
                                       "2. In the main lemma, the bound should read x+ instead. 3. Is the constant optimal?")["id"])
by = {x["id"]: x for x in r["points"]}
assert r["state"] == "ready" and [x["type"] for x in r["points"]] == ["language", "mathematics", "query"], r
assert by["P1"]["status"] == "applied" and by["P1"]["decision"]["choice"] == "auto" and by["P1"]["hunks"], by["P1"]
assert by["P2"]["status"] == "needs_author" and by["P2"]["hunks"], by["P2"]        # mathematics waits for the author
after = {rel: W.read_text(p, rel)["text"] for rel in W.files(p) if rel.endswith(".tex")}
changed = [rel for rel in after if after[rel] != before.get(rel)]
assert len(changed) == 1 and all(assist.math_bag(after[rel]) == assist.math_bag(before[rel]) for rel in after), changed
try:
    reviews.letter(p, r["id"]); raise SystemExit("the letter waits for every decision")
except assist.AssistError:
    pass
reviews.decide(p, r["id"], "P2", "decline", "The bound as stated is the one proved; the suggested form does not hold for small n.")
reviews.decide(p, r["id"], "P3", "answer", "We do not know; we added this as a question.")
r = reviews.letter(p, r["id"])
assert r["letter"].startswith("Response to the referee") and "P2" in r["letter"]
assert W.read_text(p, changed[0])["text"] == after[changed[0]], "declining changes nothing"
assert any("Before the referee" in v["label"] for v in W.versions(p))

q = settle(reviews.start(p, "proofs", "AQ1: Please confirm the affiliation of the author. Page 2: clarify the wording of the first sentence.")["id"])
qb = {x["id"]: x for x in q["points"]}
assert qb["P1"]["status"] == "needs_author" and not qb["P1"].get("proposal"), qb["P1"]      # a query is answered, not edited
assert qb["P2"]["status"] == "applied"
print("reviews ok")
