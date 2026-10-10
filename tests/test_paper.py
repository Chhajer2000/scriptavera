# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import paper as P

BODY = r"""
%%% page 2
\section{Preliminaries}\label{sec:prelim}
Let $f$ be a continuous function on $[0,1]$ and let $N(f)$ denote the number of its zeros. We write $V(f)$ for the total
variation of $f$; see \cite{Calinescu2015Finite}.

\begin{definition}\label{def:s}
Set
\[ w = V(f) + N(f). \]
\end{definition}
%%% page 3
\section{A bound for many zeros}
\begin{lemma}[Many zeros]\label{lem:odd}
Let $q \ge 2$ be an integer with $q \le N(f)$. Then
\begin{align*}
V(f) &\ge 2(q-1)\left(\frac{q+1}{q+2}\right) \\
&\ge q - 1.
\end{align*}
\end{lemma}
\begin{proof}
First paragraph of the proof with 100\% care, using \emph{Rolle's theorem} and Lemma~\ref{lem:odd}.

%%% page 4
Second paragraph:
\begin{itemize}
\item one, with $a<b$;
\item two.
\end{itemize}
Hence the claim. \end{proof} Trailing sentence after proof.
\begin{figurenote}Three rows of circled vertices.\end{figurenote}
\begin{theorem}
Main statement $V(f) \le 100$.
"""
blocks = P.parse_body(BODY)
kinds = [(b["type"], b.get("env", ""), b.get("page")) for b in blocks]
print(kinds)
assert kinds[0][0] == "section" and blocks[0]["label"] == "sec:prelim"
assert [k[0] for k in kinds] == ["section", "para", "env", "section", "env", "proof", "para", "figure", "env"], kinds
assert blocks[4]["title"] == "Many zeros" and blocks[4]["label"] == "lem:odd"
assert blocks[5]["page"] == 3 and "itemize" in blocks[5]["text"]
assert blocks[-1]["env"] == "theorem"          # auto-closed
assert P.open_environments(r"\begin{proof} abc") == ["proof"]

refs = [{"key": "Calinescu2015Finite", "title": "Zeros of continuous functions of bounded variation",
         "authors": [{"given": "Ana", "family": "Călinescu"}], "year": 2015, "venue": "Test Math. Monthly",
         "volume": "122", "issue": "3", "pages": "275--276", "doi": "10.9999/test.monthly.122.03.275", "arxiv": "", "zbl": "", "url": ""},
        {"key": "Rossi2018Number", "title": "On the number of zeros of a function",
         "authors": [{"given": "Marta", "family": "Rossi"}, {"given": "Igor", "family": "Lima"}], "year": 2018,
         "venue": "", "volume": "", "issue": "", "pages": "", "doi": "", "arxiv": "0000.12345", "zbl": "", "url": ""}]
paper = {"title": "Bounding $V(f)$ by the number of zeros", "authors": [{"name": "A. Author", "affiliation": "Dept. of Mathematics", "email": "a@b.org"}],
         "abstract": "We study continuous functions $f$ with $N(f) \\le V(f) + 1$.", "keywords": ["zeros", "bounded variation"],
         "msc": ["26A45", "26A15"], "intro": r"Counting zeros was initiated in \cite{Calinescu2015Finite}; see also \cite[Thm.~1]{Rossi2018Number}.",
         "blocks": blocks}
h = P.render_html(paper, refs)
assert 'href="#ref-Calinescu2015Finite"' in h and "Lemma 3.1" in h and "Definition 2.1" in h and "<ul>" in h and "3.1</a>" in h, h[:3000]
assert "doi.org/10.9999" in h and "arxiv.org/abs/0000.12345" in h
for cls in ("amsart", "article"):
    tex = P.build_tex(paper, refs, cls, markers=True)
    res = P.compile_pdf(tex)
    print(cls, "engine", res["engine"], "ok", res["ok"], res["errors"][:3], len(res["pdf"] or b""))
    assert res["ok"], res["errors"]
    pathlib.Path(tempfile.gettempdir(), f"scriptavera_test_{cls}.pdf").write_bytes(res["pdf"])
open(pathlib.Path(tempfile.gettempdir(), "scriptavera_test.html"), "w").write(P.standalone_html(paper, refs))
print(P.lint(paper))
print(P.reference_latex(refs[0]))
print(P.reference_bibtex(refs[1]))
print("paper ok")
