# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The whole pipeline with simulated models: every step, the five stops for the author, every output."""
import io, json, re, sys, time, zipfile
from helpers import ROOT, answers, isolate, new_project, wait
isolate()
from scriptavera import final, journals, paper as paperlib, pipeline, revision, store, writing
from scriptavera.llm import router

journal = journals.search("analysis")[0][0]
assert journal["family"] == "springer", journal
p = new_project({"journal": journals.clean_journal(journal)})
assert [pg.get("skip") for pg in p.state["pages"]] == [None] * 6 or not any(pg.get("skip") for pg in p.state["pages"])

# ---------------------------------------------------------------- the first search, and the author's topic
pipeline.start(p); wait(p, "topiccheck")
t0 = p.load("topic")
assert t0["keywords"][:2] == ["partial sums", "reciprocal powers"], "the author's keywords come first"
assert p.load("key_papers") and p.state["stages"]["topiccheck"]["status"] == "waiting" and not p.load("survey")
try:
    pipeline.submit_topic(p, {"action": "refine"}); raise SystemExit("a search again needs a comment or a correction")
except ValueError:
    pass
n0 = len(p.load("library"))
pipeline.submit_topic(p, {"action": "refine", "comment": "It is about explicit inequalities for every exponent, via the integral test."})
pipeline.start(p); wait(p, "topiccheck")
t1 = p.load("topic")
assert t1["refined"] == 1 and t1["subfield"].startswith("Explicit inequalities") and t1["author_notes"] and "pending_note" not in t1, t1
assert any(c["label"].startswith("Refine the topic") for c in p.calls(500)) and len(p.load("library")) >= 1
pipeline.submit_topic(p, {"action": "confirm"})
print("0. topic: the author's description first; searched again with the author's comment; confirmed")

# ---------------------------------------------------------------- reading, up to the first stop
pipeline.start(p); wait(p, "confirm")
g = p.load("glossary")
status = {e["symbol"]: e["status"] for e in g["entries"]}
assert status == {"\\log": "ambiguous", "\\zeta(s)": "undefined", "tail": "undefined", "H_n^{(s)}": "defined",
                  "H_m^{(1)}": "misread"}, status
assert p.load("topic")["confidence"] >= 0.7 and len(p.load("library")) == 5, len(p.load("library"))
try:
    pipeline.submit_confirm(p, {"glossary": []}); raise SystemExit("the gate must refuse unanswered items")
except ValueError as e:
    assert "still need your confirmation" in str(e)
pipeline.submit_confirm(p, answers(p))
conf = p.load("confirmed")
assert len(conf["glossary"]) == 4 and conf["ignored"] and "H_m" in conf["ignored"][0], conf
print("1. confirmed:", [x["symbol"] for x in conf["glossary"]], "| ignored:", conf["ignored"])

# ---------------------------------------------------------------- reading, up to the second stop
pipeline.start(p); wait(p, "review")
tr = p.load("transcript")["pages"]
unc = {n: [u["snippet"] for u in v["uncertain"]] for n, v in tr.items()}
assert unc["2"] == ["\\frac{1}{n}"] and unc["4"] == ["s > 1"] and unc["5"] == ["= \\log n"] and unc["6"] == [], unc       # the doubt about a plural never reaches the author
flags = p.load("flags")
assert [f["page"] for f in flags] == [5, 0], flags                                      # nor the one about spelling
shown_tr, shown_flags, _ = pipeline.visible_doubts(p)
assert [(f["page"], f["snippet"], f["alt"]) for f in shown_flags] == [(5, "\\int_{1}^{n+1} \\frac{dx}{x} = \\log n", "\\int_{1}^{n+1} \\frac{dx}{x} = \\log(n+1)")], shown_flags   # cleaned, on its page
assert "suggested reading" not in shown_flags[0]["issue"] and shown_flags[0]["issue"].startswith("The argument")
figs = p.load("figures")["items"]
assert len(figs) == 1 and figs[0]["page"] == 1 and figs[0]["box"] == [240, 90, 520, 900], figs
assert pipeline.figure_file(p, "f1", "orig").exists()
drawn = bool(figs[0]["tikz_ok"])
assert drawn == bool(paperlib.find_engine()) and pipeline.figure_file(p, "f1", "tikz").exists() == drawn, figs[0]
try:
    pipeline.submit_review(p, {}); raise SystemExit("the gate must ask about the figure")
except ValueError as e:
    assert "each figure" in str(e)
rec = pipeline.redraw_figure(p, "f1", "The rectangles should start at 1.")
assert rec["redrawn"] == 1 and rec["choice"] == ""
pipeline.set_figure(p, "f1", caption="Rectangles under the graph of a decreasing function.")
pipeline.submit_review(p, {"figures": {"f1": "tikz" if drawn else "orig"}})
print("2. transcript approved; figure:", "redrawn" if drawn else "the author's own drawing")

# ---------------------------------------------------------------- the assessment, and the author's plan
pipeline.start(p); wait(p, "scope")
A0 = p.load("assessment")
assert A0["results"]["R4"]["proof"] == "gap" and A0["results"]["R2"]["known"] == "known" and A0["results"]["R2"]["key"] == "Example2015Simulated"
assert A0["results"]["R5"]["known"] == "new", "a paper the model invented is not believed"
assert not writing.assessment_is_clean(A0) and p.state["stages"]["scope"]["status"] == "waiting"
assert writing.default_scope(A0)["results"] == {"R1": "keep", "R2": "credit", "R3": "keep", "R4": "keep", "R5": "keep"}
writing.submit_scope(p, {"kind": "not_ready"})
assert p.state["stages"]["scope"]["status"] == "waiting" and p.state["stages"]["scope"]["stopped"], "not ready: the pipeline stops here"
try:
    writing.submit_scope(p, {"kind": "article", "results": {"R2": "omit"}}); raise SystemExit("a result others use cannot be left out alone")
except ValueError as e:
    assert "R5 uses R2" in str(e), e
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "credit"}})
dd = writing.planned_dossier(p)
tr = {x["id"]: x.get("treatment", "") for x in dd["results"]}
assert tr == {"R1": "", "R2": "credit", "R3": "", "R4": "gap", "R5": ""} and dd["plan_kind"] == "article", tr
assert "CREDITED TO [Example2015Simulated]" in writing.dossier_text(dd) and "THE AUTHOR'S PLAN" in writing.dossier_text(dd)
print("2b. assessed: R4 has a gap, R2 is known; the author's plan: a research article, R2 credited")

# ---------------------------------------------------------------- first draft and the three readings
pipeline.start(p); wait(p, "decide")
d = p.load("dossier")
assert [r["id"] for r in d["results"]] == ["R1", "R2", "R3", "R4", "R5"] and d["source"] == "Example2015Simulated" and d["concerns"]
assert any("R6 is a step in the proof of R3" in x for x in d["checked"]) and d["results"][2]["contains"][0]["id"] == "R6"   # a step is not a lemma
intro = p.load("intro")
assert "Nobody1999" not in intro["text"] and any("Removed a sentence" in n for n in intro["notes"]), intro["notes"]
plan = p.load("plan")["sections"]
assert [(s["title"], s["pages"], s["items"]) for s in plan] == [("Preliminaries", [1], ["D1"]), ("Upper bounds", [2, 3, 4], ["R1", "R2", "R3"]),
                                                               ("The harmonic sums", [5, 6], ["R4", "R5"])], plan
body = p.load("body")
a = p.load("audit")
assert a["preserved"] == a["total"] and not a["altered"] and not a["missing"], a                # the altered formula was restored
assert any(u.get("repaired") for u in p.load("structure_partial")["layout"]) and "f1" in body["figures"]
labels = [b.get("label") for b in body["blocks"] if b["type"] == "env"]
assert labels == ["def:D1", "res:R1", "res:R2", "res:R3", "res:R4", "res:R5"], labels
sim = p.load("simplify")
assert [x["id"] for x in sim["proposals"]] == ["S1"] and sim["proposals"][0]["new"]["label"] == "res:S1" and sim["proposals"][0]["saved"] > 0
assert [x["id"] for x in sim["dropped"]] == ["S2"] and "independent check" in sim["dropped"][0]["reason"], sim["dropped"]
ver = p.load("verify")["results"]
assert ver["res:R4"]["verdict"] == "problems" and ver["res:R4"]["issues"][0]["status"] == "upheld"
assert ver["res:R3"]["verdict"] == "correct" and ver["res:R3"]["issues"][0]["status"] == "dismissed"
assert ver["res:R1"]["verdict"] == "correct" and not ver["res:R1"]["issues"]
ref = p.load("referee")
assert ref["recommendation"] == "minor revision" and [x["route"] for x in ref["points"]] == ["author", "auto", "auto", "author"]
items = revision.decision_items(p)
assert [x["id"] for x in items["simplify"]] == ["S1"] and len(items["issues"]) == 1 and [r["id"] for r in items["remarks"]] == ["P1", "P4", "N1"], items["remarks"]
issue = items["issues"][0]
try:
    revision.submit_decisions(p, {"simplify": {"S1": "accept"}}); raise SystemExit("the gate must want every decision")
except ValueError as e:
    assert "1 item(s) still need" in str(e)
revision.submit_decisions(p, {"simplify": {"S1": "accept"}, "issues": {issue["id"]: {"choice": "fix"}},
                              "remarks": {"P1": "Say in the theorem what $\\zeta(s)$ is."}})
print("3. draft:", a["preserved"], "of", a["total"], "formulas; proposals", len(sim["proposals"]), "kept,", len(sim["dropped"]),
      "set aside; proof issues upheld 1, dismissed 1; referee points", len(ref["points"]))

# ---------------------------------------------------------------- the final version
pipeline.start(p); wait(p, "assemble", ("done",))
assert p.state["stages"]["finish"]["status"] == "waiting", "the author chooses the title and approves the introduction"
P = p.load("paper")
by = {b.get("label"): b for b in P["blocks"] if b.get("label")}
order = [b.get("label") for b in P["blocks"] if b["type"] == "env"]
assert order == ["def:D1", "res:S1", "res:R1", "res:R2", "res:R3", "res:R4", "res:R5"], order       # the new lemma stands before its first use
proofs = {u["label"]: u["proof"] for u in revision.result_units(P["blocks"])}
assert all("\\ref{res:S1}" in proofs[l] for l in ("res:R1", "res:R2", "res:R3")), proofs["res:R1"]
assert "\\log(n+1)" in proofs["res:R4"] and "= \\log n" not in proofs["res:R4"]                       # the slip is corrected
assert "denotes the limit" in by["res:R3"]["text"]                                                   # the author's instruction was carried out
r = P["revision"]
assert len(r["simplified"]) == 1 and r["simplified"][0]["new"] == "res:S1" and len(r["corrected"]) == 1 and len(r["remarks"]) == 1 and not r["unapplied"], r
results = [x["result"] for x in r["polish"]]
assert results.count("edited") >= 3 and any(x.startswith("kept as written") for x in results), r["polish"]
assert "yields the claim" in proofs["res:R1"] and "H_n^{(2)} < 2" not in paperlib.blocks_to_latex(P["blocks"])   # wording changed, formula not
assert r["typography"]["stops"] >= 3 and r["typography"]["ties"] >= 2, r["typography"]
tex = (p.dir / "out" / "main.tex").read_text()
assert "= \\log(n+1) \\]" in tex and "\\le H_n^{(1)} \\le 1 + \\log n. \\]" in tex and "\\frac{1}{k^s} \\le \\int_{k-1}^{k}" not in tex
A = P["audit"]
assert not A["altered"] and not A["missing"] and not A["added"] and len(A["agreed"]) >= 3, A      # changes the author agreed to are not reported as errors
assert P["title"] == "Explicit bounds for partial sums of reciprocal powers" and P["title_candidates"][0] == P["title"] and len(P["title_candidates"]) == 3
assert P["title_source"] == "suggested"
n = journals.words(P["abstract"])
assert 150 <= n <= 250 and len(P["keywords"]) == 6 and len(P["keywords_full"]) == 7 and P["msc"] == ["26D15", "40A25", "11M06"], (n, P["keywords"])
assert [x["key"] for x in P["refs"]] == ["Example2015Simulated", "Sample2018Simulated"] and len(P["uncited"]) == 2
assert [c["verdict"] for c in P["cite_checks"]].count("partly") == 1
check = {x["label"]: x["ok"] for x in P["checklist"]}
assert check["Abstract of 150–250 words"] and check["Keywords: 4–6"] and check["Statements on funding and competing interests"] is False, check
assert P["profile"]["template"] == "article" and P["profile"]["house"].startswith("sn-jnl") and "\\documentclass[11pt]{article}" in tex
assert "\\begin{tikzpicture}" in tex or not drawn
assert 'class="p-fig"' in P["html"] and "Figure 1." in P["html"]
if paperlib.find_engine():
    assert P["compile"]["ok"] and (p.dir / "out" / "paper.pdf").exists() and P["pages"] >= 2, P["compile"]
names = zipfile.ZipFile(io.BytesIO((p.dir / "out" / "paper.zip").read_bytes())).namelist()
assert {"main.tex", "references.bib", "README.txt"} <= set(names), names
calls = p.calls(1000)
roles = {}
for c in calls:
    roles[c["stage"]] = roles.get(c["stage"], 0) + 1
print("4. final:", P["title"], "|", A["preserved"], "of", A["total"], "identical,", len(A["agreed"]), "changed with approval |",
      n, "words in the abstract |", P["pages"], "pages |", len(calls), "calls:", roles)

# ---------------------------------------------------------------- the last step: title, introduction, citations
F = final.finish_info(p)
keys = [r["key"] for r in F["related"]]
assert "Example2015Simulated" in keys and all(r["score"] >= 5 or r["cited"] for r in F["related"]), F["related"]
assert not [o for o in F["open"] if o["level"] == "error"], F["open"]          # the gap in R4 was corrected by the author's decision
P = final.more_titles(p)
assert len(P["title_candidates"]) == 5 and P["title"] == P["title_candidates"][0]
before = set(writing.cited_keys([P["intro"]]))
P = final.cite_intro(p, ["Example2015Simulated"])
assert set(writing.cited_keys([P["intro"]])) == {"Example2015Simulated"} and P["intro_source"] == "rewritten for your citations", P["intro"][:400]
try:
    final.finish(p, {"title": "  "}); raise SystemExit("a title is required")
except ValueError:
    pass
P = final.finish(p, {"title": "A short note on harmonic sums"})
assert p.state["stages"]["finish"]["status"] == "done" and P["title_source"] == "chosen by you"
assert "A short note on harmonic sums" in (p.dir / "out" / "main.tex").read_text() and p.state["meta"]["title"] == "", "a chosen suggestion is chosen again after a redo"
P = final.finish(p, {"title": "My own title for $H_n^{(s)}$"})
assert P["title_source"] == "author" and p.state["meta"]["title"] == "My own title for $H_n^{(s)}$", "the author's own title is kept"
print("4b. finish: 5 titles offered, the introduction rewritten for the chosen citation, the author's title in every file")

# ---------------------------------------------------------------- after the paper exists
P = final.revise_block(p, "abstract", "shorter")
assert P["abstract"].endswith("(Revised as requested.)")
guide = final.read_guide(p, "Instructions for authors. " + "The abstract must not exceed 200 words. Submit keywords and references. "
                         "LaTeX manuscripts with figures are welcome. " * 12)
v = guide["values"]
assert v["abstract_max_words"] == 200 and v["keywords_max"] == 5 and v["msc"] == "required" and v["line_numbers"] is True \
    and v["latex_class"] == "amsart" and "double_spacing" not in v and len(v["notes"]) == 2, v
j2 = journals.clean_journal({**journals.search("proceedings")[0][0], "guide": v, "custom": {"anonymous": True}})
P = final.set_journal(p, j2, {"funding": "No funding was received.", "competing": "None.", "ack": "Thanks to a colleague."})
prof = P["profile"]
assert prof["template"] == "amsart" and prof["origin"]["template"] in ("typical", "guide") and prof["origin"]["abstract_max_words"] == "guide" \
    and prof["origin"]["anonymous"] == "author" and prof["line_numbers"] and prof["ref_order"] == "alphabetical", prof
tex = (p.dir / "out" / "main.tex").read_text()
assert "{amsart}" in tex and "\\linenumbers" in tex and "Test Author" not in tex and "\\section*{Competing interests}" in tex \
    and "Acknowledgements" not in tex and "\\subjclass[2020]" in tex, tex[:1500]
assert len(P["keywords"]) == 5 and all(x["ok"] for x in P["checklist"]), P["checklist"]
assert P["abstract"].endswith("(Revised as requested.)"), "an abstract inside the range is left alone"
P = final.set_journal(p, None)
assert P["profile"]["journal"] is None and len(P["keywords"]) == 7 and "Test Author" in (p.dir / "out" / "main.tex").read_text()
print("5. journal: guide read, requirements applied, anonymous version built, journal cleared again")
print("pipeline ok")
