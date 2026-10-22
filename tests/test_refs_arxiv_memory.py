# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""References checked (verified, another paper, not found, retracted); an arXiv package checked and compiled the way
arXiv does, with the .bbl and the form filled in; meanings and handwriting remembered from paper to paper."""
import ast, io, json, pathlib, zipfile
from helpers import isolate, new_project
isolate("scriptavera_refs_")
import tests.simulator as sim  # noqa: F401
from scriptavera import arxivpack, citecheck, literature as L, memory, paper as paperlib, workspace as W

BIB = r"""@article{Good, author = {Euler, Leonhard}, title = {De summis serierum reciprocarum}, year = {1740}, doi = {10.1000/good}}
@article{Wrong, author = {Smith, A.}, title = {Groups with few cyclic subgroups}, year = {2020}, doi = {10.1000/other}}
@article{Gone, author = {Doe, J.}, title = {A retracted result}, year = {2019}, doi = {10.1000/retracted}}
@misc{Ghost, author = {Nobody, N.}, title = {A paper that does not exist anywhere}, year = {2021}}
@comment{ignored}"""
entries = citecheck.parse_bib(BIB)
assert [e["key"] for e in entries] == ["Good", "Wrong", "Gone", "Ghost"] and entries[0]["fields"]["title"] == "De summis serierum reciprocarum"

WORKS = {"good": {"title": "De summis serierum reciprocarum", "publication_year": 1740, "doi": "https://doi.org/10.1000/good",
                  "authorships": [{"author": {"display_name": "Leonhard Euler"}}], "is_retracted": False},
         "other": {"title": "Heat kernels on manifolds", "publication_year": 2011, "doi": "10.1000/other", "authorships": [], "is_retracted": False},
         "retracted": {"title": "A retracted result", "publication_year": 2019, "doi": "10.1000/retracted",
                       "authorships": [{"author": {"display_name": "J. Doe"}}], "is_retracted": True}}
class Fake:
    headers = {}
    def get(self, url, params=None, timeout=None, **kw):
        class R:
            status_code, body = 404, {}
            def json(self):
                return self.body
        r = R()
        for k, v in WORKS.items():
            if url.endswith("doi:10.1000/" + k):
                r.status_code, r.body = 200, v
        if url.endswith("/works") and "openalex" in url:
            r.status_code, r.body = 200, {"results": []}
        return r
L._session = lambda: Fake()
citecheck.config.mock_mode = lambda: False
st = {r["key"]: r["status"] for r in citecheck.check_bib(BIB)["entries"]}
assert st == {"Good": "verified", "Wrong": "mismatch", "Gone": "retracted", "Ghost": "not_found"}, st
print("1. references: verified, a DOI that leads to another paper, retracted, and one no database knows")

# an arXiv package from the editor's files
src = (pathlib.Path(__file__).parent / "test_editor.py").read_text(encoding="utf-8")
TEX = ast.literal_eval(src[src.index('TEX = r"""') + 6:src.index('"""', src.index('TEX = r"""') + 10) + 3])
p = new_project({"domains": ["math.CA", "math.NT"]})
(p.dir / "out").mkdir()
import re
TEX = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", lambda m: "\\bibliographystyle{plain}\n\\bibliography{references}", TEX, flags=re.S)
(p.dir / "out" / "main.tex").write_text(TEX, encoding="utf-8")
(p.dir / "out" / "references.bib").write_text("@article{Euler1735,\n  author = {Euler, L.},\n  title = {De summis serierum reciprocarum},\n  journal = {Comm. Acad. Sci. Petrop.},\n  year = {1740},\n}\n", encoding="utf-8")
W.create(p)
r = arxivpack.prepare(p)
by = {c["item"]: c for c in r["checks"]}
z = zipfile.ZipFile(io.BytesIO((p.dir / "out" / "arxiv-submission.zip").read_bytes()))
names = z.namelist()
assert z.read("main.tex").decode().startswith("\\pdfoutput=1") and by["File names"]["level"] == "ok"
assert r["metadata"]["primary category"] == "math.CA" and r["metadata"]["cross-lists"] == "math.NT" and r["metadata"]["title"]
if paperlib.find_engine():
    assert "main.bbl" in names and by["Bibliography"]["level"] == "ok", (names, r["checks"])
    assert by["Compiles like arXiv"]["level"] == "ok", r["checks"]
print("2. arXiv: .bbl included, \\pdfoutput=1, checked and compiled from a clean folder without BibTeX, the form filled in")

# what is learned from paper to paper
meta = {"name": "First paper", "domains": ["math.GR"]}
assert memory.learn_notation(meta, [{"symbol": "$i(G)$", "meaning": "the number of cyclic subgroups of G", "kind": "symbol"}]) == 1
assert memory.remembered("i(G)", ["math.GR"])["meaning"] == "the number of cyclic subgroups of G"
assert memory.remembered("i(G)", ["math.CA"]) is None, "another area: only after two papers"
memory.learn_notation({"name": "Second", "domains": ["math.GR"]}, [{"symbol": "i(G)", "meaning": "the number of cyclic subgroups of G"}])
assert memory.remembered("i(G)", ["math.CA"])["times"] == 2
assert memory.learn_notation({"name": "T", "domains": ["math.GR"], "test": {"before": "2026-01-01"}}, [{"symbol": "x", "meaning": "y"}]) == 0
memory.learn_reading(meta, "2", "z"); memory.learn_reading(meta, "2", "z")
q = memory.quirks()
assert "meant “z” (2×)" in q and memory.stats() == {"symbols": 1, "readings": 1}
memory.clear()
assert memory.stats() == {"symbols": 0, "readings": 0}
print("3. memory: meanings remembered by area, test papers teach nothing, handwriting corrections told to later readers")
print("refs arxiv memory ok")
