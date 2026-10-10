# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The rule-based typography pass: what it corrects, and above all what it must leave alone."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera.texutil import tidy, close_paragraphs
from scriptavera.audit import audit

T = [
 (r"We get \[ a = b \] Hence $c$.", r"We get \[ a = b. \] Hence $c$."),
 (r"We get \[ a = b \] where $c$ is a constant.", r"We get \[ a = b \] where $c$ is a constant."),
 ("so that\n\\begin{align*}\na &= b \\\\\n&= c\n\\end{align*}\n\nNext paragraph.", "so that\n\\begin{align*}\na &= b \\\\\n&= c.\n\\end{align*}\n\nNext paragraph."),
 (r"Then \[ \begin{aligned} a &= b \\ &= c \end{aligned} \]", r"Then \[ \begin{aligned} a &= b \\ &= c. \end{aligned} \]"),
 (r"Then \[ a = b, \] and so on.", r"Then \[ a = b, \] and so on."),
 (r"Then \[ a = b \] , where $x$.", r"Then \[ a = b, \] where $x$."),
 (r"Then \[ a = b \]. Next", r"Then \[ a = b. \] Next"),
 (r"Then \[ f(x) = \begin{cases} 1 & x > 0 \\ 0 & x \le 0 \end{cases} \]", r"Then \[ f(x) = \begin{cases} 1 & x > 0 \\ 0 & x \le 0 \end{cases}. \]"),
 (r"\begin{equation}\label{eq:a} a = b \end{equation} Therefore $x$.", r"\begin{equation}\label{eq:a} a = b. \end{equation} Therefore $x$."),
 (r"Then \[ a = b \qedhere \]", r"Then \[ a = b. \qedhere \]"),
 (r"by Lemma \ref{res:R1} and theorem~\ref{res:R2}, see \cite{Key2020} .", r"by Lemma~\ref{res:R1} and Theorem~\ref{res:R2}, see~\cite{Key2020}."),
 (r"that is, i.e. the the set $A$ , and  two   spaces e.g. here", r"that is, i.e.\ the set $A$, and two spaces e.g.\ here"),
 (r'the so called "good" pairs... and', r"the so called ``good'' pairs\ldots{} and"),
 (r"Let $x$ . Then $y$ ; so", r"Let $x$. Then $y$; so"),
 (r"K\"ahler and \"Uber stay.", r"K\"ahler and \"Uber stay."),
 (r"Lemmas \ref{a} and \ref{b}", r"Lemmas~\ref{a} and \ref{b}"),
 (r"\[ x_1, x_2, \dots \] Then", r"\[ x_1, x_2, \dots. \] Then"),
 (r"\[ \left. \frac{d}{dx} f \right|_{x=0} = 1 \]", r"\[ \left. \frac{d}{dx} f \right|_{x=0} = 1. \]"),
 (r"\[ \left\{ \begin{array}{l} a \\ b \end{array} \right. \] Now", r"\[ \left\{ \begin{array}{l} a \\ b \end{array} \right.. \] Now"),
 (r"\[ a = b \quad \text{for all } n. \] Now", r"\[ a = b \quad \text{for all } n. \] Now"),
 (r"\[ a = b \]" + "\n\\end{lemma}", r"\[ a = b. \]" + "\n\\end{lemma}"),
 (r"\begin{enumerate}\item[(a)] one \item[(b)] two\end{enumerate}", r"\begin{enumerate}\item[(a)] one \item[(b)] two\end{enumerate}"),
 (r"% a comment with  two spaces , kept" + "\nText.", r"% a comment with  two spaces , kept" + "\nText."),
 (r"a 3 . 5 ratio", r"a 3. 5 ratio"),
]
bad = 0
for src, want in T:
    got, counts = tidy(src)
    if got != want:
        bad += 1
        print("FAIL\n  in  :", repr(src), "\n  got :", repr(got), "\n  want:", repr(want))
    again, _ = tidy(got)
    assert again == got, ("not stable", src, got, again)        # applying it twice changes nothing more
    r = audit([{"n": 1, "text": src.replace("\\[", "$$").replace("\\]", "$$")}], got)
    assert not r["altered"] and not r["missing"], ("a formula changed", src, r)
assert not bad, f"{bad} failures"

C = [("A sentence without a stop", "A sentence without a stop."), ("Ends with math $x$", "Ends with math $x$."),
     ("Ends well.", "Ends well."), ("Leads to a list:", "Leads to a list:"), ("see \\cite{K}", "see \\cite{K}."),
     ("\\begin{itemize}\\item a\\end{itemize}", "\\begin{itemize}\\item a\\end{itemize}"), ("\\textbf{Case 1.}", "\\textbf{Case 1.}"),
     ("First para\n\nSecond para.", "First para.\n\nSecond para."), ("Ends in display \\[ a=b. \\]", "Ends in display \\[ a=b. \\]"),
     ("\\insertfigure{f1}", "\\insertfigure{f1}"), ("by \\emph{induction}", "by \\emph{induction}."), ("done \\qed", "done \\qed"),
     ("(as claimed)", "(as claimed)."), ("Is it so?", "Is it so?")]
for src, want in C:
    got, n = close_paragraphs(src)
    assert got == want, (src, got, want)
print("typography ok")
