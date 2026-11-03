# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Commands a model invents never reach the page as raw LaTeX: a made-up name of a standard symbol is replaced by the
standard command, a list or a rule of a LaTeX document becomes plain text, and any other command that does not exist
becomes a doubt for the author. Found with no model."""
from helpers import isolate, new_project
isolate("scriptavera_commands_")
from scriptavera import paper, pipeline
from scriptavera.texutil import clean_transcript, known_commands, unknown_commands

page2 = r"Idea : (1) $G \longhookrightarrow \underbrace{G^{\mathbb{Q}} \rtimes \mathrm{Aut}_c(\mathbb{Q})}_{\text{acyclic}}$"
assert r"G \hookrightarrow \underbrace" in clean_transcript(page2)
assert clean_transcript(r"$a \\\longtwoheadrightarrow b$") == r"$a \\\twoheadrightarrow b$", "a new row before it"
assert paper.MACROS[r"\longhookrightarrow"] == r"\hookrightarrow", "kept in the paper and the preview too"
print("1. an invented name of a standard arrow becomes the standard arrow")

out = clean_transcript("acts by\n\\begin{itemize}\n    \\item cutting $x$\n    \\item rescaling\n\\end{itemize}\n\n\\rule{\\textwidth}{0.4pt}\n\nNext")
assert "\\" not in out and "• cutting $x$" in out and "• rescaling" in out and "Next" in out, out
print("2. a LaTeX list becomes bullets, a rule across the page is left out")

assert len(known_commands()) > 500
assert unknown_commands(r"$\widetilde{H}_*(G) = 0$, $\mathrm{Homeo}_c(\mathbb{R}^n)$, $\xrightarrow{f}$ \\ $\curvearrowright$") == []
assert unknown_commands(r"$x \curvearrowtop y$ and $\rotatebox{90}{z}$ and $\R$", extra=set(paper.MACROS)) == ["curvearrowtop", "rotatebox"]
p = new_project(pages=2)
ds = pipeline.command_doubts(p, r"$G^{(0)} \curvearrowtop$")
assert len(ds) == 1 and ds[0]["snippet"] == r"\curvearrowtop" and "not a LaTeX command" in ds[0]["issue"], ds
print("3. a command that does not exist is a doubt for the author")
print("commands ok")
