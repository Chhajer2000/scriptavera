# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Counting the words of a LaTeX paper the way Overleaf's word count (TeXcount) reports them: the words of the text,
the words of the headings and of the captions apart, and how many formulas there are in the text and displayed.

Only what LaTeX typesets is counted: the files are read in the order LaTeX reads them, from \\begin{document} to
\\end{document}; comments, the preamble, labels, references, citations, file names and the bibliography are left out,
and a command's name is not a word (the words of \\emph{...} are)."""

from __future__ import annotations

import re

_DISPLAY_ENV = r"(?:equation|align|gather|multline|flalign|alignat|eqnarray|displaymath|dmath)\*?"
_DISPLAY = re.compile(r"\\\[.*?\\\]|\$\$.*?\$\$|\\begin\{(" + _DISPLAY_ENV + r")\}.*?\\end\{\1\}", re.S)
_INLINE = re.compile(r"(?<!\\)\$(?:\\\$|[^$])+?(?<!\\)\$|\\\(.*?\\\)", re.S)
_HEAD = re.compile(r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph|title)\*?\s*(?:\[[^\]]*\])?\s*\{")
_CAPTION = re.compile(r"\\caption\*?\s*(?:\[[^\]]*\])?\s*\{")
_DROP_ARG = re.compile(r"\\(?:label|ref|eqref|cref|Cref|autoref|pageref|nameref|vref|cite[tp]?|citeauthor|citeyear|parencite|textcite|"
                       r"autocite|nocite|input|include|includegraphics|url|href|begin|end|usepackage|bibliographystyle|bibliography|"
                       r"addbibresource|hypersetup|setcounter|addtocounter|vspace|hspace|setlength|newcommand|renewcommand|"
                       r"thispagestyle|pagestyle|newtheorem|theoremstyle|color|textcolor|graphicspath)\*?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}")
_WORD = re.compile(r"[A-Za-z\u00C0-\u024F0-9]+(?:['’\-][A-Za-z\u00C0-\u024F0-9]+)*")


def _group(text: str, i: int) -> tuple[str, int]:
    """The content of the {...} whose { is just before i, and the index after its }."""
    d = 1
    for k in range(i, len(text)):
        c = text[k]
        if c == "\\":
            continue
        if c == "{" and text[k - 1] != "\\":
            d += 1
        elif c == "}" and text[k - 1] != "\\":
            d -= 1
            if d == 0:
                return text[i:k], k + 1
    return text[i:], len(text)


def _words(text: str) -> int:
    t = _DROP_ARG.sub(" ", text)
    t = re.sub(r"\\[A-Za-z@]+\*?", " ", t)
    t = re.sub(r"\\.", " ", t)
    t = re.sub(r"[{}\[\]~&]", " ", t)
    return len(_WORD.findall(t))


def count(body: str) -> dict:
    """Count the words of a document body (the text between \\begin{document} and \\end{document})."""
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\\begin\{(thebibliography|comment|verbatim\*?|lstlisting|minted)\}.*?\\end\{\1\}", " ", body, flags=re.S)
    body = re.sub(r"\\(?:bibliography|printbibliography)\b(?:\{[^{}]*\})?", " ", body)
    display = len(_DISPLAY.findall(body))
    body = _DISPLAY.sub(" ", body)
    inline = len(_INLINE.findall(body))
    body = _INLINE.sub(" ", body)
    heads = caps = 0
    out = []
    i = 0
    while True:
        mh, mc = _HEAD.search(body, i), _CAPTION.search(body, i)
        m = min((x for x in (mh, mc) if x), key=lambda x: x.start(), default=None)
        if not m:
            out.append(body[i:])
            break
        out.append(body[i:m.start()])
        inner, end = _group(body, m.end())
        if m is mh:
            heads += _words(inner)
        else:
            caps += _words(inner)
        i = end
    return {"words": _words(" ".join(out)), "headers": heads, "captions": caps, "inline_math": inline, "display_math": display}


def document_body(lines: list[str]) -> str:
    text = "\n".join(lines)
    a = text.find("\\begin{document}")
    b = text.rfind("\\end{document}")
    if a >= 0:
        text = text[a + len("\\begin{document}"):b if b > a else len(text)]
    return text
