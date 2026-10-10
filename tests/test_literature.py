# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Parsers for each scholarly database, run on canned responses in the real response formats."""
import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.pop("SCRIPTAVERA_MOCK", None)
import requests
from scriptavera import literature as L

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/1234.56789v2</id>
    <published>2016-04-18T10:00:00Z</published>
    <title>A test paper on the
  chromatic number of sparse graphs</title>
    <summary>  We study $\chi(G)$ for
 sparse graphs.  </summary>
    <author><name>Ana Maria Popescu</name></author><author><name>Jan van der Berg</name></author>
    <arxiv:doi xmlns:arxiv="http://arxiv.org/schemas/atom">10.1234/test.2016.01</arxiv:doi>
    <arxiv:journal_ref xmlns:arxiv="http://arxiv.org/schemas/atom">Test J. Comb. 5 (2016) 1-10</arxiv:journal_ref>
    <link href="http://arxiv.org/abs/1234.56789v2" rel="alternate" type="text/html"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="math.CO" scheme="http://arxiv.org/schemas/atom"/>
    <category term="math.CO" scheme="http://arxiv.org/schemas/atom"/><category term="math.PR" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""
OPENALEX = {"meta": {"count": 1}, "results": [{
    "id": "https://openalex.org/W123", "doi": "https://doi.org/10.1234/TEST.2016.01", "title": "A test paper on the chromatic number of sparse graphs",
    "publication_year": 2016, "cited_by_count": 12, "type": "article",
    "authorships": [{"author": {"display_name": "Ana Maria Popescu"}}],
    "primary_location": {"landing_page_url": "https://doi.org/10.1234/test.2016.01", "source": {"display_name": "Test Journal of Combinatorics"}},
    "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1234.56789", "landing_page_url": "https://arxiv.org/abs/1234.56789"},
    "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1234.56789"},
    "biblio": {"volume": "5", "issue": "2", "first_page": "1", "last_page": "10"},
    "abstract_inverted_index": {"We": [0], "study": [1], "sparse": [2], "graphs.": [3]}, "ids": {}}]}
CROSSREF = {"message": {"items": [{"DOI": "10.1234/test.2016.01", "type": "journal-article", "title": ["A test paper on the chromatic number of sparse <i>graphs</i>"],
    "author": [{"given": "Ana Maria", "family": "Popescu"}], "issued": {"date-parts": [[2016, 4]]}, "container-title": ["Test Journal of Combinatorics"],
    "volume": "5", "issue": "2", "page": "1-10", "URL": "https://doi.org/10.1234/test.2016.01",
    "abstract": "<jats:p>We study sparse graphs.</jats:p>", "is-referenced-by-count": 9},
    {"DOI": "10.1/x", "type": "component", "title": ["skip me"]}]}}
S2 = {"total": 1, "data": [{"paperId": "abc", "title": "Another paper about matchings", "abstract": None, "year": 2011, "venue": "Test Arch. Math.",
    "externalIds": {"DOI": "10.5555/am.2011", "ArXiv": "1101.00001"}, "openAccessPdf": {"url": "https://example.org/x.pdf"}, "citationCount": 3,
    "journal": {"name": "Test Archive of Mathematics", "volume": "96", "pages": "201-210"}, "authors": [{"authorId": "1", "name": "D. Demo"}]}]}
ZB = {"result": [{"id": 1, "identifier": "0000.00001", "title": {"title": "On finite graphs of bounded degree"}, "year": "1955",
    "contributors": {"authors": [{"name": "Tanaka, Ichiro"}]}, "source": {"series": [{"short_title": "Test Ann. Math.", "volume": "77", "pages": "657-691"}]},
    "links": [{"type": "doi", "identifier": "10.9999/tam.1955"}], "msc": [{"code": "05C15"}], "editorial_contributions": [{"text": "A review."}],
    "zbmath_url": "https://zbmath.org/0000001"},
   {"id": 2, "identifier": "1", "title": {"title": "A paper in another field"}, "year": "2001", "contributors": {"authors": []}, "source": {}, "links": [],
    "msc": [{"code": "35J60"}], "editorial_contributions": []}], "status": {"status_code": 200}}

class R:
    def __init__(self, payload, status=200):
        self.status_code = status; self._p = payload
        self.content = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode(); self.text = self.content.decode()
    def json(self): return self._p
seen = []
def fake_get(self, url, **kw):
    seen.append((url, kw.get("params")))
    if "arxiv.org/api" in url: return R(ARXIV)
    if "openalex.org/works" in url: return R(OPENALEX)
    if "crossref.org/works" in url: return R(CROSSREF)
    if "semanticscholar" in url: return R(S2)
    if "zbmath" in url: return R(ZB)
    return R({}, 404)
requests.Session.get = fake_get
L._arxiv_last[0] = 0
found, status = L.search(["chromatic number sparse k-regular graphs"], ["math.CO"])
print({k: (v["ok"], v["found"], v["message"]) for k, v in status.items()})
assert all(v["ok"] for v in status.values()), status
aq = next(p for u, p in seen if "arxiv" in u)["search_query"]
assert aq.startswith("(cat:math.CO) AND (") and 'all:"k-regular"' in aq, aq
lib = []
added = L.add_papers(lib, found)
titles = [p["title"] for p in lib]
print(added, titles)
assert added == 3, titles                     # arXiv + OpenAlex + Crossref merged into one; the off-domain zbMATH hit dropped
m = next(p for p in lib if p["doi"] == "10.1234/test.2016.01")
assert set(m["sources"]) == {"arXiv", "OpenAlex", "Crossref"} and m["arxiv"] == "1234.56789" and m["venue"] == "Test Journal of Combinatorics"
assert m["pages"] == "1--10" and m["volume"] == "5" and m["authors"][1] == {"given": "Jan", "family": "van der Berg"}, m
assert m["key"] == "Popescu2016Test" and "<i>" not in m["title"], m["key"]
z = next(p for p in lib if p["zbl"]); assert z["authors"][0] == {"given": "Ichiro", "family": "Tanaka"} and z["year"] == 1955 and z["key"] == "Tanaka1955Finite"
s2 = next(p for p in lib if "Semantic Scholar" in p["sources"]); assert s2["arxiv"] == "1101.00001" and s2["pages"] == "201--210"
print("lex", [(p["key"], L.lexical_score(p, ["chromatic number", "matchings"], ["math.CO"])) for p in lib])
from scriptavera import paper as P
for p in lib: print(" ", P.reference_latex(p))
got = L.resolve_identifier("https://arxiv.org/abs/1234.56789v3"); assert got and got["pinned"] and got["arxiv"] == "1234.56789"
got = L.resolve_identifier("10.1234/test.2016.01"); assert got is None or got["doi"]   # crossref_by_doi uses message directly
print("literature ok")
