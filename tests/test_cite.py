# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Citing Scriptavera: a new paper declares what Scriptavera did for it and cites it, just before the references,
under the heading the journal asks for; the author can remove both; the reference checker does not look it up."""
import json
from helpers import isolate, new_project
isolate("scriptavera_cite_")
from scriptavera import __version__, citecheck, final, paper as P

paper = {"title": "On sums", "authors": [{"name": "A. Author"}, {"name": "B. Author"}], "abstract": "We bound sums.",
         "abstract_source": "generated", "intro": "We study $x$.", "intro_source": "generated", "title_source": "author",
         "blocks": [{"id": "b1", "type": "para", "text": "A paragraph."}], "statements": {"ack": "We thank C."}, "cite_software": True}
ref = P.software_reference()
assert ref["key"] == "scriptavera" and ref["url"] == "https://scriptavera.com" and __version__ in ref["venue"]
tex = P.build_tex(paper, [ref], "amsart")
i_ack, i_decl, i_refs = tex.index("Acknowledgements"), tex.index("\\section*{Use of AI-assisted tools}"), tex.index("\\begin{thebibliography}")
assert i_ack < i_decl < i_refs, "the declaration comes last, just before the references"
decl = P.software_statement(paper)
assert "the authors used Scriptavera~\\cite{scriptavera}" in decl and "draft the abstract" in decl and "propose the title" not in decl
assert "take full responsibility" in decl
assert "\\bibitem{scriptavera}" in tex and "\\href{https://scriptavera.com}{scriptavera.com}" in tex
one = P.software_statement({**paper, "authors": [{"name": "A"}], "abstract_source": "author", "intro_source": "author"})
assert "the author used" in one and "takes full responsibility" in one and "abstract" not in one and "introduction" not in one
assert "Declaration of generative AI and AI-assisted technologies in the writing process" in P.build_tex(paper, [ref], "elsarticle")
html = P.render_html({**paper, "profile": {}}, [ref])
assert "Use of AI-assisted tools" in html and 'id="ref-scriptavera"' in html and "scriptavera.com" in html
off = P.build_tex({**paper, "cite_software": False}, [], "amsart")
assert "Scriptavera" not in off, "removed by the author: no trace of it"
bib = P.reference_bibtex(ref)
assert bib.startswith("@misc{scriptavera,") and "howpublished = {\\url{https://scriptavera.com}}" in bib
assert citecheck.check_entry(citecheck.parse_bib(bib)[0])["status"] == "verified", "nothing to look up for the software"
print("1. the declaration and the reference, in LaTeX, HTML and BibTeX, under the journal's heading")

# through the paper's own build: on for a new paper, off when the author unticks it
p = new_project({"cite_software": True})
p.save("library", [])
built = final.build_outputs(p, {**paper, "statements": {}}, compile_now=False)
assert [r["key"] for r in built["refs"]] == ["scriptavera"] and built["refs"][0]["kind"] == "software"
out = (p.dir / "out" / "main.tex").read_text(encoding="utf-8")
assert "\\cite{scriptavera}" in out and "@misc{scriptavera" in (p.dir / "out" / "references.bib").read_text(encoding="utf-8")
p.save("paper", built)
final.set_journal(p, None, None, False)
assert "Scriptavera" not in (p.dir / "out" / "main.tex").read_text(encoding="utf-8") and p.state["meta"]["cite_software"] is False
old = new_project()                                   # a paper made before this existed is left as it was
assert not old.state["meta"].get("cite_software")
print("2. a new paper declares and cites Scriptavera; unticked, both are gone; older papers are unchanged")
print("cite ok")
