# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The guards around the mathematics: what counts as an edit of wording only, what may be asked of the author,
how an accepted change is carried out, and the small parsers the writing steps depend on."""
from helpers import isolate
isolate("scriptavera_rev_")
from scriptavera import final, paper as P, pipeline, revision, writing

# ---- doubts about language never reach the author; doubts about mathematics always do
L = pipeline.language_only
assert L("the sums are bounded", "the sum is bounded", "singular or plural is unclear")
assert L("Adding the term", "Adding the terms", "")                                   # same words but for a plural ending
assert L("recieve the bound", "", "spelling of the word")                             # described purely as language
assert L("We get that the bound holds", "we get the bound holds", "a small word may be missing")
assert not L("$a_n \\le b$", "$a_m \\le b$", "the index may be n or m")
assert not L("x < 1", "x \\le 1", "typo?")                                            # a relation sign is mathematics, whatever it is called
assert not L("for all n", "for some n", "the quantifier is unclear")
assert not L("2 cases", "3 cases", "")
assert not L("compact set", "connected set", "which technical term")
assert not L("the limit exists", "", "") and not L("f is continuous", "none", "hard to read")
print("language filter ok")

# ---- an edit of wording only
old = r"""\begin{lemma}\label{res:R1}
For all $n \ge 1$ we have $a_n \le 2$; see \cite{Key2000Paper}.
\end{lemma}
\begin{proof}
By Lemma~\ref{res:R0} we get
\[ a_n \le b_n + 1 \]
and so we are done, as $b_n \le 1$.
\end{proof}"""
good = old.replace("we get", "we obtain").replace("and so we are done, as", "and the claim follows, since").replace(r"b_n + 1 \]", r"b_n + 1, \]")
assert writing.wording_only(old, good) and final._formulas_intact(old, good)
for what, bad in (("a label", old.replace("res:R1", "res:R9")), ("a reference", old.replace(r"~\ref{res:R0}", "")),
                  ("a citation", old.replace(r"; see \cite{Key2000Paper}", "")), ("an environment", old.replace("lemma", "theorem")),
                  ("half the text", old[:len(old) // 2] + r"\end{proof}"), ("unbalanced LaTeX", old.replace(r"\]", ""))):
    assert not writing.wording_only(old, bad), what
for what, bad in (("a relation", old.replace(r"a_n \le b_n", r"a_n < b_n")), ("an index", old.replace("$b_n", "$b_m")),
                  ("a dropped inline formula", old.replace(r", as $b_n \le 1$", "")), ("an added display", old.replace("and so", r"\[ x = y \] and so")),
                  ("a constant", old.replace(r"a_n \le 2$", r"a_n \le 3$")), ("a short symbol", old.replace(r"$n \ge 1$", r"$m \ge 1$"))):
    assert writing.wording_only(old, bad) and not final._formulas_intact(old, bad), what
assert final._formulas_intact(r"\[ a \leq b \] holds. $x\in A$", r"\begin{equation*} a \le b. \end{equation*} holds. $x \in A$")
assert final._formulas_intact(r"\begin{align*} a &= b \\ &= c \end{align*}", r"\begin{align*} a &= b \\ &= c. \end{align*}")
assert final._same_but_syntax(r"Let $x = \frac{1}{2$ be given.", r"Let $x = \frac{1}{2}$ be given.")
assert not final._same_but_syntax(r"Let $x = \frac{1}{2$ be given.", r"Let $x = 1$ be given and proceed otherwise.")
print("wording guard ok")

# ---- the copy-edit keeps statements and proofs in place; paragraphs may be joined or split
blocks = P.parse_body("\\section{One}\n\nA first paragraph.\n\nA second paragraph.\n\n" + old + "\n\nA closing remark.\n\n\\section{Two}\n\nMore text.")
assert [b["type"] for b in blocks] == ["section", "para", "para", "env", "proof", "para", "section", "para"]
groups = final._chunks(blocks, 10_000)
assert groups == [[0, 1, 2, 3, 4, 5], [6, 7]], groups
small = final._chunks(blocks, 60)
assert [3, 4] in small and all(not (g[-1] == 3) for g in small), small                 # a statement is never cut from its proof
part = [blocks[i] for i in groups[0]]
joined = P.parse_body(P.blocks_to_latex(part).replace("A first paragraph.\n\nA second paragraph.", "A first paragraph. A second one."))
merged = final._merge_shape(part, joined)
assert merged and [b["type"] for b in merged] == ["section", "para", "env", "proof", "para"] and merged[2]["label"] == "res:R1"
assert final._merge_shape(part, [b for b in joined if b["type"] != "proof"]) is None   # a proof went missing
swapped = [joined[0], joined[1], joined[3], joined[2], joined[4]]
assert final._merge_shape(part, swapped) is None                                       # statement and proof changed places
print("arrangement guard ok")

# ---- titles
for t in ("Explicit bounds for sums of reciprocal powers", "On the chromatic number of sparse random graphs", "A characterisation of $L^p$ multipliers"):
    assert final.good_title(t), t
for t in ("Open problem: classify all such objects", "Classify the finite objects with property P", "Is every such space compact?", "Find all solutions.",
          "Let $G$ be a graph", "Bounds", "", "A title that just keeps going on and on and on and on with far too many words to be a title at all",
          "Sums with $x^{2$ unbalanced", "Problem 3"):
    assert not final.good_title(t), t
print("title rule ok")

# ---- the dossier and the plan
dossier = writing.parse_dossier(r"""<problem>Bound $a_n$.</problem>
<source>[Key2000Paper]</source>
<setting>$n \ge 1$.</setting>
<result>
id: R1
kind: Lemma
statement: For all $n$,
\[ a_n \le 2. \]
Then: more.
pages: 2-3
depends: D1
role: auxiliary
</result>
<result>
id: R2
kind: main theorem
statement: $a_n \to 1$.
pages: 5, 7
depends: R1, D1
role: main
<definition>
id: D1
name: $a_n$
text: the $n$-th term
page: 1
</definition>
<storyline>From D1 to R2.</storyline>
<contribution>Known [Key2000Paper].</contribution>
<notation_choice>None.</notation_choice>
<concerns>
- Page 3: the step uses $n \ge 2$, which is not assumed.
none
</concerns>""")
assert dossier["source"] == "Key2000Paper" and dossier["notation_choice"] == "" and len(dossier["concerns"]) == 1 and "$n \\ge 2$, which" in dossier["concerns"][0]
r1, r2 = dossier["results"]
assert r1["kind"] == "lemma" and r1["pages"] == [2, 3] and r1["depends"] == ["D1"] and r1["statement"].endswith("Then: more.") and "\\[ a_n \\le 2. \\]" in r1["statement"]
assert r2["kind"] == "theorem" and r2["role"] == "main" and r2["pages"] == [5, 7] and r2["depends"] == ["R1", "D1"]       # a missing closing tag is tolerated
assert dossier["definitions"] == [{"id": "D1", "name": "$a_n$", "text": "the $n$-th term", "page": 1}]
pages = [{"n": n, "text": "x"} for n in (1, 2, 3, 5, 6, 7)]
plan = writing.normalise_plan([{"title": "2. Introduction", "items": "", "from_page": "1"}, {"title": "Section 2: Preliminaries.", "items": "D1", "from_page": "1"},
                               {"title": "The limit", "items": "R2", "from_page": "page 4"}, {"title": "A bound", "items": "R1, R7", "from_page": "2"},
                               {"title": "Duplicate", "items": "", "from_page": "2"}, {"title": "", "items": "R1", "from_page": "3"}], pages, dossier)
assert [(s["title"], s["pages"], s["items"]) for s in plan] == [("Preliminaries", [1], ["D1"]), ("The limit", [5, 6, 7], ["R2"]), ("A bound", [2, 3], ["R1"])], plan
assert writing.normalise_plan([], pages, dossier)[0]["pages"] == [1, 2, 3, 5, 6, 7]     # no usable plan: one section with everything
assert writing.organisation("See Section~9.", plan).startswith("The paper is organised as follows: Section~2 (Preliminaries)")
assert writing.organisation("Section~2 fixes notation; Section~4 has the bound.", plan) == "Section~2 fixes notation; Section~4 has the bound."
print("dossier and plan ok")

# ---- citations: only papers from the databases survive, and an unsourced claim goes with its citation
valid = {"Key2000Paper": {}, "Other2010Result": {}}
log = []
t = writing.drop_unsourced("It is known that $a \\le b$ \\cite{Key2000Paper}. A bold claim holds \\cite{Ghost1999Nothing}. We use \\cite{Key2000Papr}.", valid, log)
assert "bold claim" not in t and "Key2000Papr" in t and len(log) == 1
t = writing.fix_keys(t, valid, log)
assert t == "It is known that $a \\le b$ \\cite{Key2000Paper}. We use \\cite{Key2000Paper}." and "corrected" in log[1], (t, log)
assert writing.cited_keys(["\\cite{A, B}", "\\citep[Thm 2]{C}", "\\cite{A}"]) == ["A", "B", "C"]
print("citation rules ok")

# ---- carrying out the author's decisions
proj = isolate and __import__("helpers").new_project(pages=2)
body = P.parse_body(r"""\section{Results}
\begin{lemma}\label{res:R1}
We have $a \le b$.
\end{lemma}
\begin{proof}
First $a \le c$ by definition. Then $c \le b$ since $c = b - 1$. Hence $a \le b$ and the proof is complete.
\end{proof}
\begin{lemma}\label{res:R2}
We have $u \le v$.
\end{lemma}
\begin{proof}
First $u \le w$ by definition. Then $w \le v$ since $w = v - 1$. Hence $u \le v$ and the proof is complete.
\end{proof}
\begin{theorem}\label{res:R3}
We have $a + u \le b + v$.
\end{theorem}
\begin{proof}
Add the two lemmas, noting that $a + u \le b + w$.
\end{proof}""")
units = revision.result_units(body)
assert [(u["label"], u["name"], u["proof_at"] - u["at"]) for u in units] == [("res:R1", "Lemma 2.1", 1), ("res:R2", "Lemma 2.2", 1), ("res:R3", "Theorem 2.3", 1)]
proj.save("body", {"blocks": body, "organisation": "", "figures": {}})
proj.save("intro", {"text": "An introduction that says $a \\le b$ is known \\cite{Key2000Paper}.", "notes": []})
proj.save("simplify", {"proposals": [{
    "id": "S1", "kind": "general-lemma", "affects": ["res:R1", "res:R2"], "idea": "one argument", "benefit": "", "names": {"res:R1": "Lemma 2.1", "res:R2": "Lemma 2.2"},
    "before": {}, "after": {"res:R1": "Apply Lemma~\\ref{res:S1} to $a$ and $b$.", "res:R2": "Apply Lemma~\\ref{res:S1} to $u$ and $v$."},
    "new": {"before": "res:R1", "kind": "lemma", "label": "res:S1", "statement": "If $x \\le z$ and $z = y - 1$ then $x \\le y$.", "proof": "Indeed $x \\le z \\le y$."}}]})
proj.save("verify", {"results": {
    "res:R3": {"label": "res:R3", "name": "Theorem 2.3", "env": "theorem", "verdict": "problems", "issues": [
        {"id": "V3.1", "severity": "slip", "quote": "$a + u \\le b + w$", "problem": "w should be v", "correction": "$a + u \\le b + v$", "status": "upheld"},
        {"id": "V3.2", "severity": "gap", "quote": "nowhere", "problem": "dismissed one", "correction": "", "status": "dismissed"}]},
    "res:R1": {"label": "res:R1", "name": "Lemma 2.1", "env": "lemma", "verdict": "problems", "issues": [
        {"id": "V1.1", "severity": "slip", "quote": "$c = b - 1$", "problem": "p", "correction": "$c = b - 2$", "status": "upheld"}]},
    "res:R9": {"label": "res:R9", "name": "Corollary 9", "env": "corollary", "verdict": "unproved", "issues": []},
    "res:R8": {"label": "res:R8", "name": "Proposition 8", "env": "proposition", "verdict": "unproved", "issues": []}}})
proj.save("referee", {"points": [{"id": "P1", "type": "citation", "where": "Introduction", "comment": "c", "action": "a", "route": "author"},
                                 {"id": "P2", "type": "typography", "where": "whole paper", "comment": "c", "action": "a", "route": "auto"}]})
items = revision.decision_items(proj)
assert [x["id"] for x in items["issues"]] == ["V3.1", "V1.1"] and [x["id"] for x in items["remarks"]] == ["UresR8", "P1"], items["remarks"]   # a corollary may stand unproved
assert not revision.settle_if_nothing_to_decide(proj)
for bad in ({}, {"simplify": {"S1": "maybe"}, "issues": {}}, {"simplify": {"S1": "accept"}, "issues": {"V3.1": {"choice": "fix"}, "V1.1": {"choice": "instruct", "note": " "}}}):
    try: revision.submit_decisions(proj, bad); raise SystemExit("incomplete decisions must be refused")
    except ValueError: pass
revision.submit_decisions(proj, {"simplify": {"S1": "accept"}, "issues": {"V3.1": {"choice": "fix"}, "V1.1": {"choice": "fix"}}, "remarks": {"P2": "ignored: not a remark for the author"}})
assert proj.load("decisions")["remarks"] == {}
run = pipeline.Run(proj); run.stage = "assemble"
new, intro, waived, approved, report = revision.apply_decisions(run, body, proj.load("intro")["text"])
assert [b.get("label") for b in new if b["type"] == "env"] == ["res:S1", "res:R1", "res:R2", "res:R3"] and [b["id"] for b in new] == [f"b{i}" for i in range(1, len(new) + 1)]
proofs = {u["label"]: u["proof"] for u in revision.result_units(new)}
assert proofs["res:S1"] == "Indeed $x \\le z \\le y$." and proofs["res:R1"].startswith("Apply Lemma~\\ref{res:S1}") and proofs["res:R3"].endswith("$a + u \\le b + v$.")
assert [x["id"] for x in report["simplified"]] == ["S1"] and report["simplified"][0]["new"] == "res:S1" and [x["id"] for x in report["corrected"]] == ["V3.1"]
assert [x["id"] for x in report["unapplied"]] == ["V1.1"] and "replaced by a shorter one" in report["unapplied"][0]["why"], report["unapplied"]
assert "$c \\le b$ since $c = b - 1$" in waived and "Apply Lemma" in approved and intro == proj.load("intro")["text"]
assert body[2]["text"].startswith("First $a \\le c$"), "the draft itself is not modified"
from scriptavera import audit
notes = [{"n": 1, "text": "First $a \\le c$. Then $c \\le b$ since $c = b - 1$. Hence $a + u \\le b + w$. Also $$u \\le v$$"}]
rep = audit.audit(notes, P.blocks_to_latex(new), waived=waived, approved=approved)
assert not rep["missing"] and not rep["altered"] and len(rep["agreed"]) == 1 and rep["preserved"] == 1 and not rep["added"], rep     # agreed changes are not reported as damage
rep = audit.audit(notes, P.blocks_to_latex(new))
assert rep["missing"] or rep["altered"], "without the author's agreement the same changes are reported"
assert revision._replace_once("a  b\n c a b c", "a b c", "X") is None and revision._replace_once("x a  b\n c y", "a b c", "X") == "x X y"
print("decisions ok")
print("revision ok")

# doubts about language in the shapes models really write them, including a second reading of only the changed part
assert L("term , therefore,", "terms", 'singular "term" written instead of plural')
assert L("Hence,", "Thus,", 'handwritten word could be read as "Hence," or "Thus,"')
assert L("2 interval", "2 intervals", "singular noun used where plural might be expected")
assert L("then there at least k disjoint", "then there are at least k disjoint", 'missing "are" after "there"')
assert L("we have $a+b$ terms … only $a-b$ many term , therefore, we have,",
         "suggested reading: “… $a-b$ many **terms**, therefore …”.", "The word should be plural; not fatal mathematically.")
assert not L("f(4)", "f(n)", "a typo for f(n)") and not L("4(f(4)-1) \\le A", "4(f(n)-1) \\le A", "a typo")
assert not L("x is not prime", "x is prime", "a small word may be missing")
assert not L("x is not prime", "prime", "plural")                                       # a dropped 'not' is never language
assert not L("at least 2 terms", "at most 2 terms", "typo") and not L("a and b", "a or b", "grammar")
assert not L("then $f$ is continuous", "then $g$ is continuous", "which word")
assert not L("the doubt", "", "replace S with s so that $x\\le s$")
print("language filter ok")

# doubts are shown in a clean shape whatever columns the model put things in
T = pipeline.tidy_doubt
pg = {2: "Then $f(4)$ and $f(4)+1$ and $f(4)$.", 5: "at least 4 terms from $A$.\n$$\\therefore 4(f(n)-1) \\le A$$", 6: "Let $a$ be the rest."}
d = T({"snippet": "The letter used for the rest is $a$ throughout the notes, but here it appears as capital “A”. This likely results from a misreading.",
       "issue": "suggested reading: replace “A” with “a” (so the inequality reads $4(f(n)-1)\\le a$).", "suggestion": ""}, pg, 6)
assert d["page"] == 5 and d["snippet"] == "4(f(n)-1) \\le A" and d["alt"] == "4(f(n)-1)\\le a" and d["count"] == 1, d     # wrong page corrected
assert d["issue"].startswith("The letter used") and "suggested reading" not in d["issue"], d
d = T({"snippet": "A long description that appears to say the symbol f(4) is misread everywhere in the notes.", "issue": "suggested reading: replace every “f(4)” with “f(n)”.", "suggestion": ""}, pg, 0)
assert d["page"] == 2 and d["snippet"] == "f(4)" and d["alt"] == "f(n)" and d["every"] and d["count"] == 3, d
assert T({"snippet": "4(f(4)-1) \\le A", "issue": "typo", "alt": "4(f(n)-1) \\le A"}, pg, 5) is None                    # already corrected by the author
d = T({"snippet": "“at least 4 terms … \\le A”", "issue": "the bound should use $a$", "suggestion": "suggested reading: replace “A” by “a”."}, pg, 5)
assert d["snippet"] == "A" and d["alt"] == "a" and d["page"] == 5 and d["count"] == 2 and not d["every"], d                     # twice on the page: no button
print("doubt shapes ok")

# a passage quoted slightly wrongly is found on the page; one that is not there any more asks nothing
pg15 = {7: "Again the estimate is as before with $\\psi(5)33-$ here."}
d = T({"snippet": "\\psi(5)3-3", "issue": "unclear", "alt": "\\psi(5) = 3"}, pg15, 7)
assert d and d["snippet"] in pg15[7] and d["count"] == 1, d
assert T({"snippet": "\\psi(5)33-", "issue": "unclear", "alt": "\\psi(5) = 3"}, {7: "as before with $\\psi(5)=3$-"}, 7) is None
assert T({"snippet": "zzzz qqq", "issue": "unclear", "alt": "y"}, pg15, 7) is None
print("quotes ok")


# a section never starts in the middle of an argument, and a step of a proof is never a result of its own
from scriptavera import writing
pgs = [{"n": n, "text": f"page {n} $x_{n}$"} for n in range(1, 9)]
dd = {"results": [{"id": "R1", "pages": [1, 2]}, {"id": "R2", "pages": [3, 4, 5]}, {"id": "R3", "pages": [6, 8]}], "definitions": []}
raw = [{"title": "A", "from_page": "1", "items": "R1"}, {"title": "B", "from_page": "4", "items": "R2"}, {"title": "C", "from_page": "7", "items": "R3"}]
secs = writing.normalise_plan(raw, pgs, dd)
assert [s["pages"] for s in secs] == [[1, 2, 3, 4, 5], [6, 7, 8]], [s["pages"] for s in secs]
assert sorted(i for s in secs for i in s["items"]) == ["R1", "R2", "R3"]
d2 = {"results": [{"id": "R1", "pages": [2], "statement": "A", "depends": []}, {"id": "R2", "pages": [3], "statement": "B", "depends": ["R1"]},
                  {"id": "R3", "pages": [4], "statement": "C", "depends": ["R2"]}], "storyline": "R1 gives R2, and R2 gives R3."}
out = writing.apply_result_check(d2, [["R1", "result", "none"], ["R2", "part of R3", "a case"], ["R3", "incomplete", "If $y$ then C"]],
                                 [{"n": 3, "text": "If $y$ then"}])
assert [x["id"] for x in d2["results"]] == ["R1", "R3"] and d2["results"][1]["pages"] == [3, 4] and d2["results"][1]["depends"] == ["R1"]
assert d2["results"][1]["statement"] == "If $y$ then C" and "R2" not in d2["storyline"]
d3 = {"results": [{"id": "R1", "pages": [2], "statement": "A", "depends": []}, {"id": "R2", "pages": [3], "statement": "B", "depends": []}]}
writing.apply_result_check(d3, [["R2", "incomplete", "If $z^2$ then B"]], [{"n": 3, "text": "no such formula"}])
assert d3["results"][1]["statement"] == "B" and d3["concerns"], "a completed statement must use only the author's formulas"
print("structure ok")

# ---- found by the independent review: an unclear assessment is never "clean"
from scriptavera import writing as W
dres = {"results": [{"id": "R1", "depends": []}, {"id": "R2", "depends": ["R1"]}, {"id": "R3", "depends": ["R2"]}]}
a0 = W.parse_assessment("", dres, set())
assert not W.assessment_is_clean(a0) and a0["main"] == "unknown" and all(r["proof"] == "unknown" for r in a0["results"].values())
a1 = W.parse_assessment("<result>\nid: R1\nproof: incomplete, the odd case is not complete\nknown: new\n</result>\n"
                        "<result>\nid: R2\nproof: complete\nknown: new\n</result>\n<main>established</main>\n<recommend>partial article</recommend>", dres, set())
assert a1["results"]["R1"]["proof"] == "gap" and a1["results"]["R3"]["proof"] == "unknown" and a1["recommend"] == "partial"
assert W._proof_state("breaks off at page 3") == "breaks off" and W._proof_state("no proof given") == "none" and W._proof_state("mostly") == "unknown"
# a statement completed by the second reading keeps every formula and every quantifier, whatever the delimiters
notes = [{"n": 1, "text": "For all $n \\ge 2$ and $f \\in L^2$ we have $\\|f\\| \\le n$."}]
assert W._formulas_in_notes("If $f \\in L^2$, then for all $n \\ge 2$ we have $\\|f\\| \\le n$.", notes, "For all $n \\ge 2$ we have $\\|f\\| \\le n$.")
assert not W._formulas_in_notes("For some \\(n < 2\\) there is no bound.", notes, "For all $n \\ge 2$ we have $\\|f\\| \\le n$.")
assert not W._formulas_in_notes("For some $n \\ge 2$ we have $\\|f\\| \\le n$.", notes, "For all $n \\ge 2$ we have $\\|f\\| \\le n$.")
assert W.check_rows("R3 || incomplete || If $\\|f\\| \\le 1$ and ||f|| < 2 then C\nnoise line") == [["R3", "incomplete", "If $\\|f\\| \\le 1$ and ||f|| < 2 then C"]]
# the first section never moves, and no page is lost
pgs2 = [{"n": n, "text": f"p{n}"} for n in range(2, 8)]
secs2 = W.normalise_plan([{"title": "A", "from_page": "2", "items": "R1"}, {"title": "B", "from_page": "5", "items": "R2"}], pgs2,
                         {"results": [{"id": "R1", "pages": [1, 2, 3]}, {"id": "R2", "pages": [5, 6]}], "definitions": []})
assert [s["pages"] for s in secs2] == [[2, 3, 4], [5, 6, 7]], [s["pages"] for s in secs2]
# exactly the chosen papers
log2 = []
assert W.fix_keys(r"see \cite{Smith2019Groups,Jones2020}", {"Smith2020Groups": {}, "Jones2020": {}}, log2, fuzzy=False) == r"see \cite{Jones2020}"
print("review fixes ok")
