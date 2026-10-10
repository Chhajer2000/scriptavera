# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Journal templates and reference styles: every option of the standard classes is compiled; the Elsevier
front matter, whose class is not installed here, is checked as text."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import paper as P

refs = [{"key": "Smith2015Sums", "title": "On sums of reciprocal powers", "authors": [{"given": "Ann", "family": "Smith"}], "year": 2015,
         "venue": "J. Example Anal.", "volume": "12", "issue": "3", "pages": "275--276", "doi": "10.1000/x.1", "arxiv": "", "zbl": "", "url": ""},
        {"key": "Ortiz2018Integral", "title": "The integral test revisited", "authors": [{"given": "María", "family": "Ortíz"}, {"given": "Li", "family": "Wei"}],
         "year": 2018, "venue": "", "volume": "", "issue": "", "pages": "", "doi": "", "arxiv": "1705.00001", "zbl": "", "url": ""},
        {"key": "Smith2015Bounds", "title": "Bounds for harmonic numbers", "authors": [{"given": "Ann", "family": "Smith"}], "year": 2015,
         "venue": "Example Bull.", "volume": "3", "issue": "", "pages": "1--9", "doi": "", "arxiv": "", "zbl": "", "url": "https://example.org/b"}]
blocks = P.parse_body("\\section{Results}\nSee \\cite{Smith2015Sums} and \\cite[Thm.~2]{Ortiz2018Integral,Smith2015Bounds}.\n"
                      "\\begin{lemma}\\label{res:R1}Let $f$ be decreasing. Then \\[ f(k+1) \\le \\int_k^{k+1} f. \\]\\end{lemma}\n"
                      "\\begin{proof}Clear.\\end{proof}\n\\insertfigure{f1}")
paper = {"title": "Sums and integrals of monotone functions",
         "authors": [{"name": "A. Author", "affiliation": "Dept. of Mathematics, Example University", "email": "a@example.org"},
                     {"name": "B. Writer", "affiliation": "Dept. of Mathematics, Example University", "email": ""}],
         "abstract": "We compare sums with integrals.", "keywords": ["integral test", "harmonic numbers"], "msc": ["26D15", "40A05"],
         "intro": "Intro text citing \\cite{Smith2015Sums}.", "blocks": blocks,
         "figures": {"f1": {"mode": "tikz", "tikz": "\\begin{tikzpicture}\\draw (0,0) rectangle (1,1);\\end{tikzpicture}", "caption": "A square."}},
         "statements": {"ack": "We thank the referee.", "funding": "No funding was received.",
                        "competing": "The authors declare no competing interests.", "data": ""}}
assert P.reference_labels(refs, "alpha") == ["Smi15a", "OW18", "Smi15b"] and P.reference_labels(refs, "numeric") == ["1", "2", "3"]
for prof in ({"template": "amsart"}, {"template": "article", "ref_style": "alpha", "line_numbers": True, "double_spacing": True},
             {"template": "amsart", "anonymous": True, "ref_style": "alpha"}, {"template": "article", "anonymous": True}):
    tex = P.build_tex(paper, refs, prof, markers=True)
    r = P.compile_pdf(tex)
    assert r["ok"], (prof, r["errors"][:3])
    if prof.get("anonymous"):
        assert "A. Author" not in tex and "thank the referee" not in tex and "No funding" not in tex and "competing interests" in tex
    else:
        assert "A. Author" in tex and "thank the referee" in tex
    assert ("\\bibitem[Smi15a]" in tex) == (prof.get("ref_style") == "alpha")
tex = P.build_tex(paper, refs, {"template": "elsarticle", "journal": "Journal of Examples & Tests", "ref_style": "alpha"})
front = tex[tex.index("\\journal"):tex.index("\\section{Introduction}")]
for piece in ("\\journal{Journal of Examples \\& Tests}", "\\begin{frontmatter}", "\\author[1]{A. Author}", "\\ead{a@example.org}",
              "\\author[1]{B. Writer}", "\\address[1]{Dept. of Mathematics, Example University}", "\\begin{keyword}",
              "integral test \\sep harmonic numbers", "\\MSC[2020] 26D15 \\sep 40A05", "\\end{frontmatter}"):
    assert piece in front, (piece, front)
assert "\\documentclass[preprint,11pt]{elsarticle}" in tex and "\\maketitle" not in tex
assert "\\bibitem{Smith2015Sums}" in tex and "\\bibitem[" not in tex          # numbered labels only with that class
paper["profile"] = {"ref_style": "alpha"}
h = P.render_html(paper, refs, lambda f, m: "/fig")
assert ">Smi15a</a>" in h and "[OW18]" in h and "Acknowledgements" in h and "Competing interests" in h
paper["profile"] = {"anonymous": True}
h = P.render_html(paper, refs, lambda f, m: "/fig")
assert "A. Author" not in h and "Acknowledgements" not in h
print("templates ok")
