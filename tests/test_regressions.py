# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Cases found by the independent review; each failed before its fix."""
import os, sys, pathlib
os.environ["SCRIPTAVERA_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import paper as P, pipeline, texutil, writing
from scriptavera.util import strip_markup

# titles in every dialect compile, and say what they should
refs = []
titles = ["On $α$-stable sheaves & rings: 100% of cases_x #3", "The $1 problem", r"K\"ahler manifolds and Erd\H{o}s--R\'enyi graphs in {B}anach spaces",
          "Graphs with $|E'| < n^3$ and $d(H) > 2$", "Brănescu–Tóth: on ∞-categories ≤ 2"]
for i, t in enumerate(titles):
    refs.append({"key": f"K{i}", "title": strip_markup(t), "authors": [{"given": "Jean-Pierre", "family": "Serre"}, {"given": "", "family": "Erdős"}],
                 "year": 2000 + i, "venue": "J. Test & Co_", "volume": "1", "issue": "", "pages": "1--2",
                 "doi": "10.1000/a_b#c%d<e>(f)", "arxiv": "", "zbl": "", "url": ""})
assert strip_markup("Graphs with $|E'| < n^3$ and $d(H) > 2$") == "Graphs with $|E'| < n^3$ and $d(H) > 2$"
assert strip_markup("A <i>nice</i> title<sub>2</sub>") == "A nice title2"
blocks = P.parse_body("\\section{Results}\nWe cite " + ", ".join(f"\\cite{{K{i}}}" for i in range(len(titles)))
                      + ". About 50% of the cases, as claimed.\n\\begin{theorem}\\label{thm:a}Let $x_1 \\le 2$.\\end{theorem}")
assert "50\\% of the cases" in blocks[1]["text"]
paper = {"title": "A 50% bound for a_2(n) & problem #3 with $x_1^2$", "authors": [{"name": "Ünal Çelik", "affiliation": "Dept. of Maths & Stats", "email": "a_b@c.org"}],
         "abstract": "We bound 50% of cases; see \\cite{K0}.", "intro": "", "blocks": blocks, "keywords": ["rings & fields"], "msc": ["26D15"]}
for cls in ("amsart", "article"):
    r = P.compile_pdf(P.build_tex(paper, refs, cls, markers=True))
    assert r["ok"], (cls, r["errors"])
tex = P.build_tex(paper, refs)
assert r"{\"a}hler" in tex and r"Erd{\H{o}}s" in tex and "Banach" in tex and r"\$1 problem" in tex and r"$\alpha$-stable" in tex, tex[tex.index("thebib"):][:900]
print("reference and title escaping ok")

# a fatal error without a line number is reported, and mapped errors in the reference list are named
bad = dict(paper, blocks=P.parse_body("\\section{S}\nText \\emph{unclosed"))
r = P.compile_pdf(P.build_tex(bad, [], markers=True))
assert not r["ok"] and r["errors"], r
assert P.lint(bad), "lint should see the unclosed brace"
assert P.block_at_line("a\n%@block b1\nx\n%@block refs\n\\bibitem", 5) == "refs"
print("fatal compile errors are reported:", r["errors"][0]["message"][:50])

# page kind: only the first word counts
for raw, want in (("content | blank | cover | figure", "content"), ("content (not blank)", "content"), ("content, includes a discovery", "content"),
                  ("blank", "blank"), (" Cover page", "cover"), ("", "content")):
    assert pipeline._kind(f"<kind>{raw}</kind>") == want, (raw, pipeline._kind(f"<kind>{raw}</kind>"))
assert pipeline._as_list(None) == [] and pipeline._as_list("a; b\nc") == ["a", "b", "c"] and pipeline._as_list(["x", None, " "]) == ["x"]

# citation cleanup leaves mathematics alone
log = []
t = writing.fix_keys(r"$f() = 1$ and $a\ ,\ b$ and \item[] as in \cite{Known} (see \cite{Ghost}) and~\cite{Ghost2}.", {"Known": {}}, log)
assert t == r"$f() = 1$ and $a\ ,\ b$ and \item[] as in \cite{Known} and.", t
assert len(log) == 2

# transcript cleaning keeps row spacers; links cannot inject attributes
c = texutil.clean_transcript(r"$$\begin{aligned} a &= b \\[2pt] c &= d \end{aligned}$$ then \[ x = y \]")
assert r"\\[2pt]" in c and c.endswith("$$x = y$$"), c
h = P.tex_to_html(r'See \url{https://a.b/x" onmouseover="alert(1)} and \href{javascript:alert(1)}{click} and \href{https://ok.org/a_b}{fine}.', {})
assert 'href="https://a.b/x&quot; onmouseover=&quot;alert(1)"' in h and "javascript:" not in h and 'href="https://ok.org/a_b"' in h, h
print("regressions ok")
