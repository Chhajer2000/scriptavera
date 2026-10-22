# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Typed notes and papers from elsewhere: an Overleaf project taken as the notes (inputs expanded, the author's
macros and packages kept, pages cut between paragraphs only), read with no model looking at an image; the text layer
of a computer-made PDF; comments written on a PDF; a paper imported for review."""
import io, json, zipfile
from helpers import answers, isolate, wait
isolate("scriptavera_typed_")
import tests.simulator as sim  # noqa: F401  (the simulated models)
from scriptavera import config, pipeline, store, typed, workspace as W

MAIN = r"""\documentclass{article}
\usepackage{amsmath,amssymb,geometry}
\usepackage{tikz-cd}
\newcommand{\R}{\mathbb{R}}
\newcommand{\norm}[1]{\left\| #1 \right\|}
\DeclareMathOperator{\Aut}{Aut}
\title{Partial sums of reciprocal powers}
\begin{document}
\maketitle
\input{sections/one}
\section{Bounds}
% a comment that is not notes
For every $n \ge 1$ we have the following.
\begin{align*}
H_n &\le 1 + \log n \\
&\le 2n.
\end{align*}
""" + "\n\n".join(f"Paragraph {k}: the terms decrease, so each is at most the integral before it, and $\\norm{{x}} \\in \\R$." for k in range(40)) + r"""
\end{document}
"""
ONE = r"""\section{Setting}
Let $s > 0$ be real. We write $H_n^{(s)} = \sum_{k=1}^{n} k^{-s}$.
"""
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    for name, data in (("paper/main.tex", MAIN), ("paper/sections/one.tex", ONE), ("paper/fig.png", "x"), ("paper/refs.bib", "@book{a,}")):
        z.writestr(name, data)
files = [("overleaf.zip", buf.getvalue())]
assert typed.is_typed("overleaf.zip", buf.getvalue()) and typed.is_typed("notes.md") and not typed.is_typed("scan.pdf")
tree = typed.unpack(files)
assert typed.main_tex(tree) == "main.tex" and "sections/one.tex" in tree                  # the wrapping folder is dropped
pre, body, title = typed.split_document(typed.expand_inputs(typed.text_of(tree["main.tex"]), tree))
assert title == "Partial sums of reciprocal powers" and "\\section{Setting}" in body and "a comment" not in body
lines, katex = typed.preamble_parts(pre)
assert "\\usepackage{tikz-cd}" in lines and not any("geometry" in l or "amsmath" in l for l in lines), lines
assert any(l.startswith("\\providecommand{\\norm}{}\\renewcommand{\\norm}[1]") for l in lines) and katex["\\R"] == "\\mathbb{R}"
assert katex["\\Aut"] == "\\operatorname{Aut}"
parts = typed.chunks(body)
assert len(parts) >= 2 and all(p.count("\\begin{align*}") == p.count("\\end{align*}") for p in parts), [len(p) for p in parts]

# typed notes as a project: no image is read; the source is the transcript
pid = store.new_id()
folder = config.PROJECTS_DIR / pid
pages, info = typed.ingest_typed(files, folder / "pages")
(folder / "typed.json").write_text(json.dumps(info), encoding="utf-8")
typed.save_assets(files, folder)
assert (folder / "source_files" / "fig.png").exists() and all(pg["typed"] for pg in pages) and (folder / "pages" / "p001.jpg").exists()
meta = {"name": "Typed notes", "domains": ["math.CA"], "subarea": "", "hints": "", "title": info["title"],
        "problem": "Explicit bounds for the partial sums of the series of reciprocal powers.", "keywords": ["partial sums", "bounds"],
        "key_papers": [], "authors": [{"name": "Test Author"}], "known_refs": [], "journal": None, "statements": {}}
p = store.create(meta, pages, pid)
pipeline.start(p); wait(p, "topiccheck")
pipeline.submit_topic(p, {"action": "confirm"})
pipeline.start(p); wait(p, "confirm")
if p.state["stages"]["confirm"]["status"] == "waiting":
    pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
tr = p.load("transcript")["pages"]
assert all(tr[n]["text"] == info["pages"][n] and tr[n]["model"] == "typed source" for n in info["pages"]), "the source is the transcript"
labels = [c.get("label") or "" for c in p.calls(10000)]
assert not any(l.startswith(("Scout page", "Transcribe page")) for l in labels), labels
assert any(l.endswith("(typed)") for l in labels), "the notation is still catalogued, from the text"
print("1. typed notes: an Overleaf zip read as the notes, no image read, the source kept as the transcript")

# a second paper by the same author: the meanings confirmed in the first are proposed again, already accepted
pid2 = store.new_id()
pages2, info2 = typed.ingest_typed(files, config.PROJECTS_DIR / pid2 / "pages")
(config.PROJECTS_DIR / pid2 / "typed.json").write_text(json.dumps(info2), encoding="utf-8")
p2 = store.create({**meta, "name": "Second typed notes"}, pages2, pid2)
pipeline.start(p2); wait(p2, "topiccheck")
pipeline.submit_topic(p2, {"action": "confirm"})
pipeline.start(p2); wait(p2, "confirm")
rem = [e for e in (p2.load("glossary") or {}).get("entries", []) if e.get("remembered")]
confirmed1 = {g["symbol"] for g in (p.load("confirmed") or {}).get("glossary", [])}
assert rem and all(e["symbol"] in confirmed1 or e["remembered"]["meaning"] for e in rem), rem
print(f"1b. a second paper: {len(rem)} meaning(s) remembered from the first, proposed and already accepted")


# a computer-made PDF: its text layer, and the comments written on it
def pdf_with_comments() -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> /Annots [6 0 R 7 0 R] >>",
            None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Annot /Subtype /Text /Rect [400 700 420 720] /Contents (Please justify the second inequality.) >>",
            b"<< /Type /Annot /Subtype /Highlight /Rect [70 690 330 720] /QuadPoints [72 718 330 718 72 694 330 694] /Contents (Typo: bound) >>"]
    text = b"BT /F1 20 Tf 72 700 Td (The bund is sharp for all n.) Tj ET\n" + b"BT /F1 12 Tf 72 600 Td (" + b"Words of the page " * 6 + b") Tj ET"
    objs[3] = b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream"
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


pdf = pdf_with_comments()
assert "bund is sharp" in typed.text_layers(pdf)[1]
notes = typed.pdf_comments(pdf)
kinds = {x["kind"]: x for x in notes}
assert kinds["note"]["comment"] == "Please justify the second inequality." and kinds["note"]["page"] == 1, notes
assert kinds["highlight"]["comment"] == "Typo: bound" and "bund" in kinds["highlight"]["marked"], notes
from scriptavera import reviews
txt = reviews.report_text("report.pdf", pdf)
assert "COMMENTS WRITTEN ON THE PDF" in txt and 'on "' in txt and "Please justify" in txt
print("2. a computer-made PDF: its text layer read, and the comments written on it, with the words they mark")

# a paper written elsewhere, opened in the editor for review; no pipeline runs on it
q = store.create({"name": "Imported", "imported": True, "domains": [], "problem": "", "keywords": [], "authors": [], "files": [],
                  "journal": None, "statements": {}}, [], store.new_id())
W.import_tree(q, typed.unpack(files), "main.tex")
assert W.exists(q) and W.main_file(q) == "main.tex" and "sections/one.tex" in W.files(q) and "fig.png" in W.files(q)
assert pipeline.start(q) is False, "an imported paper has no notes to read"
print("3. a paper from elsewhere: its files exactly as they are, in the editor; nothing runs on it")
print("typed ok")
