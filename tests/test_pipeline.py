# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The whole pipeline with simulated models: every step, the five stops for the author, every output."""
import io, json, re, sys, time, zipfile
from helpers import ROOT, answers, isolate, new_project, wait
isolate()
from scriptavera import drafting, final, journals, paper as paperlib, pipeline, prompts, revision, store, writing
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
b = figs[0]["box"]                       # found on the page, then fitted to the ink: it holds the whole drawing
assert len(figs) == 1 and figs[0]["page"] == 1 and b[0] <= 280 and b[1] <= 138 and b[2] >= 495 and b[3] >= 862, figs
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
# the assessment is advice: 'not yet' is never chosen for the author, and a sketched main result is one the paper proves
r = lambda proof, known="new": {"proof": proof, "known": known, "where": "", "key": "", "evidence": ""}
for rec, res, mains, want in [
        ("not_ready", {"R1": r("sketch"), "R2": r("none"), "R3": r("sketch")}, {"R1", "R3"}, "article"),     # seminar slides
        ("partial", {"R1": r("gap"), "R2": r("breaks off")}, None, "article"),
        ("not_ready", {"R1": r("none"), "R2": r("sketch")}, {"R1"}, "partial"),                              # a main result with no argument
        ("not_ready", {"R1": r("complete", "known"), "R2": r("sketch", "special")}, None, "expository"),
        ("article", {"R1": r("complete")}, None, "article"), ("expository", {"R1": r("complete")}, None, "expository")]:
    a_ = {"recommend": rec, "main": "not established", "results": res}
    assert writing.default_kind(a_, mains) == want and writing.default_scope(a_)["kind"] in ("article", "partial", "expository"), (rec, res, want)
assert writing.default_scope({"recommend": "not_ready", "results": {"R1": r("sketch"), "R2": r("none")}})["results"] == {"R1": "keep", "R2": "conjecture"}
assert "Never only because proofs are sketched" in prompts.ASSESS
S = p.load("statements")
st = {x["id"]: x for x in S["items"]}
assert [x["id"] for x in S["items"]] == ["D1", "R1", "R2", "R3", "R4", "R5"] and st["R2"]["placement"] == "preliminaries"
assert st["R4"]["question"] and not st["R4"]["settled"] and drafting.open_questions(p) == ["R4"], "an unclear statement is put to the author"
try:
    drafting.submit_statements(p, {"kind": "article"}); raise SystemExit("an open question must be answered first")
except ValueError as e:
    assert "Answer the question about R4" in str(e), e
x = drafting.answer_statement(p, "R4", "The bound is $\\log(n+1)$; the last display of page 5 is a slip.")
assert x["settled"] and x["asked"][0]["answer"].startswith("The bound") and "\\log(n+1)" in x["statement"]
writing.submit_scope(p, {"kind": "not_ready"})
assert p.state["stages"]["scope"]["status"] == "waiting" and p.state["stages"]["scope"]["stopped"], "not ready: the pipeline stops here"
try:
    writing.submit_scope(p, {"kind": "article", "results": {"R2": "omit"}}); raise SystemExit("a result others use cannot be left out alone")
except ValueError as e:
    assert "R5 uses R2" in str(e), e
drafting.edit_statement(p, "R1", {"kind": "proposition"})
drafting.edit_statement(p, "R1", {"kind": "lemma"})                      # the author may switch a kind, and back
writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "credit"}})
dd = writing.planned_dossier(p)
tr = {x["id"]: x.get("treatment", "") for x in dd["results"]}
assert tr == {"R1": "", "R2": "credit", "R3": "", "R4": "", "R5": ""} and dd["plan_kind"] == "article", tr
assert "CREDITED TO [Example2015Simulated]" in writing.dossier_text(dd) and "THE AUTHOR'S PLAN" in writing.dossier_text(dd)
print("2b. assessed: R4 has a gap, R2 is known; the question about R4 answered; a research article, R2 credited")

# ---------------------------------------------------------------- the structure, the proofs, the first draft
pipeline.start(p); wait(p, "outline")
O = p.load("outline")
assert [(s["title"], s["items"]) for s in O["sections"]] == [("Preliminaries", ["D1", "R2"]), ("Upper bounds", ["R1", "R3"]),
                                                             ("The harmonic sums", ["R4", "R5"])], O["sections"]   # R4 before R5, which uses it
try:
    drafting.edit_outline(p, {"move": "R4", "to": "down"}); raise SystemExit("a statement cannot come after one that uses it")
except ValueError as e:
    assert "(R5) uses" in str(e) and "(R4)" in str(e), e
drafting.edit_outline(p, {"rename": "The harmonic sums", "section": 2})
drafting.submit_outline(p)
pipeline.start(p); wait(p, "proofcheck")
P = p.load("proofs")["items"]
assert sorted(P) == ["R1", "R3", "R4", "R5"], "a credited result gets no proof of ours"
assert P["R1"]["status"] == "accept" and P["R5"]["status"] == "accept" and "Lemma~\\ref{res:R2}" in P["R5"]["text"]
assert P["R3"]["status"] == "accept" and len(P["R3"]["tries"]) == 2 and "FIRSTTRY" not in P["R3"]["text"], "a wrong proof is written again"
assert "s-1" in P["R3"]["tries"][0]["feedback"] or "integral" in P["R3"]["tries"][0]["feedback"]
assert P["R4"]["status"] == "review" and P["R4"]["added"], "a repaired argument is marked for the author"
assert not drafting.open_proof_problems(p)
drafting.submit_proofs(p)
pipeline.start(p); wait(p, "draftcheck")
intro = p.load("intro")
assert "Nobody1999" not in intro["text"] and any("Removed a sentence" in n for n in intro["notes"]), intro["notes"]
plan = p.load("plan")["sections"]
assert [(s["title"], s["items"]) for s in plan] == [("Preliminaries", ["D1", "R2"]), ("Upper bounds", ["R1", "R3"]),
                                                    ("The harmonic sums", ["R4", "R5"])], plan
body = p.load("body")
labels = [b.get("label") for b in body["blocks"] if b["type"] == "env"]
assert labels == ["def:D1", "res:R2", "res:R1", "res:R3", "res:R4", "res:R5"], labels
assert "f1" in body["figures"] and any("\\insertfigure{f1}" in (b.get("text") or "") for b in body["blocks"]), "the kept figure is placed"
r2 = next(b for b in body["blocks"] if b.get("label") == "res:R2")
assert "Example2015Simulated" in (r2.get("title") or "") + (r2.get("text") or ""), r2
assert sum(1 for b in body["blocks"] if b["type"] == "proof") == 4 and p.load("abstract_draft")["abstract"]
assert any("In this section we treat" in (b.get("text") or "") for b in body["blocks"]), "the text between results is written"
drafting.submit_draft(p, {"abstract": p.load("abstract_draft")["abstract"] + " "})
pipeline.start(p); wait(p, "decide")
d = p.load("dossier")
assert [r["id"] for r in d["results"]] == ["R1", "R2", "R3", "R4", "R5"] and d["source"] == "Example2015Simulated" and d["concerns"]
assert any("R6 is a step in the proof of R3" in x for x in d["checked"]) and d["results"][2]["contains"][0]["id"] == "R6"   # a step is not a lemma
a = p.load("audit")
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
assert order == ["def:D1", "res:R2", "res:S1", "res:R1", "res:R3", "res:R4", "res:R5"], order       # the new lemma stands before its first use
proofs = {u["label"]: u["proof"] for u in revision.result_units(P["blocks"])}
assert all("\\ref{res:S1}" in proofs[l] for l in ("res:R1", "res:R3")) and not proofs.get("res:R2"), proofs["res:R1"]   # R2 is credited
assert "\\log(n+1)" in proofs["res:R4"] and "= \\log n" not in proofs["res:R4"]                       # the slip is corrected
assert "denotes the limit" in by["res:R3"]["text"]                                                   # the author's instruction was carried out
r = P["revision"]
assert len(r["simplified"]) == 1 and r["simplified"][0]["new"] == "res:S1" and len(r["corrected"]) == 1 and len(r["remarks"]) == 1 and not r["unapplied"], r
results = [x["result"] for x in r["polish"]]
assert results.count("edited") >= 2 and any(x.startswith("kept as written") for x in results), r["polish"]
assert "yields the claim" in proofs["res:R1"] and "H_n^{(2)} < 2" not in paperlib.blocks_to_latex(P["blocks"])   # wording changed, formula not
assert r["typography"]["stops"] >= 1 and r["typography"]["ties"] >= 2, r["typography"]
tex = (p.dir / "out" / "main.tex").read_text()
assert "\\int_{1}^{n+1} \\frac{dx}{x} = \\log(n+1)" in tex and "\\le H_n^{(1)} \\le 1 + \\log n$." in tex and "\\frac{1}{k^s} \\le \\int_{k-1}^{k}" not in tex
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

# ---------------------------------------------------------------- from the finished paper: summary, slides, poster
from scriptavera import extras
sm = extras.make(p, "summary")
assert sm["text"].startswith("The question") and (p.dir / "out" / "summary.txt").exists()
sl = extras.make(p, "slides")
assert (p.dir / "out" / "slides.tex").exists() and "\\begin{frame}{The question}" in (p.dir / "out" / "slides.tex").read_text()
assert any("x^{17}" in f for f in sl["foreign"]), "a formula that is not in the paper is reported"
assert sl["compiled"] in (None, True) and (sl["compiled"] is None or (p.dir / "out" / "slides.pdf").exists()), sl
po = extras.make(p, "poster")
assert "\\section*{Results}" in (p.dir / "out" / "poster.tex").read_text() and po["compiled"] in (None, True), po
pack = extras.review_pack(p).decode("utf-8")
assert "var PACK=" in pack and "data:image/jpeg;base64," in pack and "Download my comments" in pack and 'data-block="' in pack
PK = json.loads(re.search(r"var PACK=(\{.*?\});</script>", pack, re.S).group(1))
bid = next(k for k, v in PK["blocks"].items() if v["page"])
rep = extras.comments_to_report(json.dumps({"paper": "T", "reviewer": "A. Colleague", "comments": [
    {"block": bid, "where": PK["blocks"][bid]["where"], "quote": "We have", "comment": "Say why the sum is finite."}]}).encode())
assert rep.startswith("Comments from A. Colleague") and "Say why the sum is finite." in rep and PK["blocks"][bid]["where"] in rep
print("5b. from the paper: a plain-language summary, slides (a formula not in the paper reported), a poster, and a review pack for a co-author")

# ---------------------------------------------------------------- pages added to notes that already have a paper
from helpers import page_images
from scriptavera import ingest
before = len(p.calls(100000))
new = ingest.ingest(page_images(1), p.dir / "pages", start=max(pg["n"] for pg in p.state["pages"]))
assert pipeline.add_pages(p, new)["pages"] == [7] and p.state["incremental"]["rounds"][-1]["pages"] == [7]
st = p.state["stages"]
assert st["review"]["status"] == "pending" and st["transcribe"]["status"] == "pending" and st["assemble"]["status"] == "pending"
assert p.load("proof_cache") and p.load("statements_memory") and p.load("verify_cache") and "1" in (p.load("transcript") or {}).get("pages", {})
pipeline.start(p); wait(p, "review")
read = [c.get("label") or "" for c in p.calls(100000)[before:] if c.get("stage") in ("survey", "transcribe")]
assert read and not any(re.search(r"\bpage ([1-6])\b", l) for l in read), read
pipeline.submit_review(p, {"figures": {f["id"]: f.get("choice") or "orig" for f in (p.load("figures") or {}).get("items", [])}})
pipeline.start(p); wait(p, "scope")
if p.state["stages"]["scope"]["status"] == "waiting":
    writing.submit_scope(p, {"kind": "article", "results": {"R4": "keep", "R2": "credit"}})
pipeline.start(p); wait(p, "decide")
kept = [c for c in p.calls(100000)[before:] if c.get("stage") == "proofs"]
assert not kept, "unchanged statements keep their proofs: no proof is written again"
assert {x["id"]: x["treatment"] for x in p.load("statements")["items"]}["R2"] == "credit", "the author's decisions are kept"
log = (p.dir / "calls.jsonl").read_text(encoding="utf-8")
vk = [r for r in (p.load("verify") or {}).get("results", {}).values() if r.get("kept")]
assert vk, "an unchanged proof keeps its check"
print(f"6. pages added: only page 7 read; {len(vk)} unchanged proof check(s) kept; no proof written again")
print("pipeline ok")
