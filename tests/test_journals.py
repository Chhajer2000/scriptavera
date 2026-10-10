# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Journal lookup and requirements: the two databases in their real response formats, the three layers of
requirements, the checklist, and reading a guide for authors."""
import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.pop("SCRIPTAVERA_MOCK", None)
import tempfile
import requests
from scriptavera import config, journals as J
tmp = pathlib.Path(tempfile.mkdtemp()); config.SETTINGS_PATH = tmp / "settings.json"; config.ENV_PATH = tmp / ".env"
for k in ("OPENALEX_API_KEY", "CONTACT_EMAIL"): os.environ.pop(k, None)

OPENALEX = {"meta": {"count": 3, "db_response_time_ms": 31, "page": 1, "per_page": 12, "groups_count": None}, "results": [
    {"id": "https://openalex.org/S1", "display_name": "Test Journal of Algebra", "issn_l": "0000-1111", "issn": ["0000-1111", "0000-111X"],
     "host_organization_name": "Elsevier BV", "homepage_url": "https://example.org/tja", "works_count": 21000,
     "summary_stats": {"2yr_mean_citedness": 0.8, "h_index": 97, "i10_index": 5000}, "country_code": "US"},
    {"id": "https://openalex.org/S2", "display_name": "Test Journal of Algebra", "issn_l": "0000-1111", "issn": ["0000-1111"],
     "host_organization_name": "Elsevier BV", "homepage_url": None, "works_count": 3, "summary_stats": {}, "country_code": None},
    {"id": "https://openalex.org/S3", "display_name": "Test  Algebra\nand Number Theory", "issn_l": None, "issn": None,
     "host_organization_name": None, "homepage_url": None, "works_count": None, "summary_stats": None, "country_code": None},
    {"id": "https://openalex.org/S4", "display_name": None}]}
CROSSREF = {"status": "ok", "message-type": "journal-list", "message-version": "1.0.0", "message": {"items-per-page": 12, "total-results": 2, "items": [
    {"title": "Test Communications in Algebra", "publisher": "Informa UK Limited", "ISSN": ["0000-2222", "0000-222X"],
     "counts": {"current-dois": 900, "backfile-dois": 9000, "total-dois": 9900}, "subjects": [], "issn-type": [{"value": "0000-2222", "type": "print"}]},
    {"title": "Test Reports on Information", "publisher": "Institute of Information Press", "ISSN": [], "counts": {}}]}}

mode = {"openalex": 200, "crossref": 200}
seen = []
class R:
    def __init__(self, payload, status=200):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)
    def json(self): return self._p
def fake_get(self, url, **kw):
    seen.append((url, dict(kw.get("params") or {})))
    if "openalex.org/sources" in url: return R(OPENALEX, mode["openalex"])
    if "crossref.org/journals" in url: return R(CROSSREF, mode["crossref"])
    return R({}, 404)
requests.Session.get = fake_get

found, msg = J.search("  test   journal of algebra ")
assert msg == "" and [j["name"] for j in found] == ["Test Journal of Algebra", "Test Algebra and Number Theory"], found     # duplicates by ISSN merged
a = found[0]
assert a == {"name": "Test Journal of Algebra", "publisher": "Elsevier BV", "issn": "0000-1111", "homepage": "https://example.org/tja",
             "family": "elsevier", "works": 21000, "h_index": 97, "country": "US", "source": "OpenAlex"}, a
assert found[1]["family"] == "generic" and found[1]["publisher"] == "" and found[1]["works"] == 0
url, params = seen[0]
assert url == "https://api.openalex.org/sources" and params["search"] == "test journal of algebra" and params["filter"] == "type:journal" \
    and "api_key" not in params and "mailto" not in params, params
os.environ["OPENALEX_API_KEY"] = "k-123"; os.environ["CONTACT_EMAIL"] = "someone@example.org"
J.search("algebra"); assert seen[-1][1]["api_key"] == "k-123" and seen[-1][1]["mailto"] == "someone@example.org"
for k in ("OPENALEX_API_KEY", "CONTACT_EMAIL"): os.environ.pop(k)
mode["openalex"] = 403                                # the first database refuses: the second one answers
found, msg = J.search("communications in algebra")
assert msg == "" and found[0] == {"name": "Test Communications in Algebra", "publisher": "Informa UK Limited", "issn": "0000-2222", "homepage": "",
                                  "family": "tandf", "works": 9900, "h_index": None, "country": "", "source": "Crossref"}, found[0]
assert found[1]["family"] == "generic", "a publisher whose name merely contains 'informa' is not Taylor & Francis"
assert seen[-1][0] == "https://api.crossref.org/journals" and seen[-1][1]["query"] == "communications in algebra"
mode["crossref"] = 503
found, msg = J.search("anything at all")
assert found == [] and "could not be reached" in msg and "type the journal's name" in msg, msg
assert J.search("ab") == ([], "")
for name, fam in (("Springer Science+Business Media", "springer"), ("Springer Nature", "springer"), ("Birkhäuser", "springer"),
                  ("American Mathematical Society", "ams"), ("Society for Industrial & Applied Mathematics (SIAM)", "siam"),
                  ("Siam University Press", "generic"), ("Wiley", "wiley"), ("Cambridge University Press (CUP)", "cup"),
                  ("Walter de Gruyter GmbH", "degruyter"), ("European Mathematical Society - EMS - Publishing House GmbH", "ems"),
                  ("Mathematical Sciences Publishers", "msp"), ("MDPI AG", "mdpi"), ("", "generic"), ("A press nobody knows", "generic")):
    assert J.family_for(name)["id"] == fam, (name, J.family_for(name)["id"])
print("lookup ok: OpenAlex, Crossref as second source, publisher families")

# ---- the three layers of requirements
assert J.profile(None)["template"] == "amsart" and J.profile(None)["origin"]["template"] == "default" and J.profile(None)["journal"] is None
config.save_settings({"document_class": "article", "ref_order": "appearance"})
assert J.profile(None)["template"] == "article" and J.profile(None)["ref_order"] == "appearance"
config.save_settings({"document_class": "amsart", "ref_order": "alphabetical"})
j = J.clean_journal({"name": "  Test   Journal ", "publisher": "Springer Nature", "issn": "0000-1111", "homepage": "javascript:alert(1)",
                     "guide_url": "https://example.org/guide", "junk": 1})
assert j == {"name": "Test Journal", "publisher": "Springer Nature", "issn": "0000-1111", "homepage": "", "family": "springer", "guide": None,
             "guide_url": "https://example.org/guide", "custom": {}}, j
assert J.clean_journal(None) is None and J.clean_journal({"name": "  "}) is None and J.clean_journal("x") is None
assert J.clean_journal(j) == j, "cleaning twice changes nothing"
p = J.profile(j)
assert (p["template"], p["abstract_min_words"], p["abstract_max_words"], p["keywords_max"], p["declarations"]) == ("article", 150, 250, 6, True)
assert p["origin"]["abstract_max_words"] == "typical" and p["origin"]["msc"] == "default" and p["house"].startswith("sn-jnl") and p["journal"] == "Test Journal"
j["guide"] = J._clean_guide({"abstract_max_words": 200, "abstract_min_words": 400, "keywords_max": 5, "msc": "required", "latex_class": "elsarticle.cls v3",
                             "anonymous": "yes", "page_limit": 9999, "ref_style": "author-year", "notes": ["One.", "  ", "Two."], "unknown": 3})
assert j["guide"] == {"abstract_max_words": 200, "keywords_max": 5, "msc": "required", "notes": ["One.", "Two."], "latex_class": "elsarticleclsv3"}, j["guide"]
p = J.profile(j)
assert p["abstract_max_words"] == 200 and p["origin"]["abstract_max_words"] == "guide" and p["abstract_min_words"] == 150 and p["origin"]["abstract_min_words"] == "typical"
assert p["template"] == "elsarticle" and p["origin"]["template"] == "guide" and p["msc"] == "required" and p["notes"] == ["One.", "Two."]
j["custom"] = J._clean({"template": "amsart", "abstract_min_words": None, "abstract_max_words": 120, "anonymous": True, "ref_style": "alpha", "keywords_min": 9})
p = J.profile(j)
assert p["template"] == "amsart" and p["origin"]["template"] == "author" and p["abstract_max_words"] == 120 and p["anonymous"] and p["ref_style"] == "alpha"
assert J._clean({"abstract_min_words": 300, "abstract_max_words": 200}) == {"abstract_max_words": 200}
assert J._clean({"msc": "Maybe", "ref_order": "random", "template": "revtex", "line_numbers": 1, "page_limit": "12", "keywords_min": "x"}) == {"page_limit": 12}
assert J.words(r"Let $x^2 + y^2 = 1$ and \emph{note} that $n$ is odd; a well-known fact.") == 11
assert J.length_phrase({"abstract_min_words": 150, "abstract_max_words": 250}) == "between 150 and 250 words"
assert J.length_phrase({"abstract_max_words": 200}).startswith("at most 200 words") and J.length_phrase({}) == "90 to 170 words"
assert J.keywords_phrase({"keywords_min": 4, "keywords_max": 6}) == "4 to 6" and J.keywords_phrase({"keywords_max": 5}) == "4 to 5" \
    and J.keywords_phrase({"keywords_max": 3}) == "up to 3" and J.keywords_phrase({}) == "4 to 6"
paper = {"abstract": "word " * 130, "keywords": ["a", "b"], "msc": []}
prof = {"abstract_min_words": 150, "abstract_max_words": 250, "keywords_min": 4, "keywords_max": 6, "msc": "required", "declarations": True,
        "anonymous": True, "page_limit": 20, "origin": {"abstract_max_words": "guide", "page_limit": "author"}}
c = {x["label"]: x for x in J.checklist(paper, prof, {"funding": "None."}, pages=23)}
assert [k for k, v in c.items() if not v["ok"]] == ["Abstract of 150–250 words", "Keywords: 4–6", "Mathematics Subject Classification",
                                                     "Statements on funding and competing interests", "At most 20 pages"], c
assert c["Abstract of 150–250 words"]["detail"] == "The abstract has 130 words." and c["Abstract of 150–250 words"]["origin"] == "guide"
assert "competing interests" in c["Statements on funding and competing interests"]["detail"] and "funding," not in c["Statements on funding and competing interests"]["detail"]
assert c["Anonymous manuscript"]["ok"] and J.checklist(paper, J.DEFAULT_PROFILE, {}) == []
print("requirements ok: typical for the publisher, the journal's guide, the author's settings; checklist")

# ---- reading a guide for authors
HOME = """<html><head><style>p{color:red}</style><script>var x = "<a href='/trap'>Guide for authors</a>";</script></head><body>
<nav><a href="/about">About</a></nav><h1>Test Journal</h1><p>Aims &amp; scope.</p>
<a href="mailto:ed@example.org">Instructions for authors</a><a href="/journal/guide-for-authors#top">Guide&nbsp;for <b>Authors</b></a>
<a href="https://other.example.net/submission-guidelines">Submit</a><a href="/news">News</a></body></html>"""
GUIDE = "<html><body><h1>Guide for authors</h1><ul><li>The abstract must not exceed 250 words.</li><li>Provide 3 to 6 keywords.</li></ul>" + \
        "<p>Manuscript submission in LaTeX is encouraged; references are numbered; figures as PDF.</p>" * 20 + "</body></html>"
assert J.html_to_text(HOME) == "Test Journal\nAims & scope.\n\nInstructions for authors Guide for Authors\nSubmit News", repr(J.html_to_text(HOME))
assert J.guide_links(HOME, "https://example.org/journal/") == ["https://example.org/journal/guide-for-authors", "https://other.example.net/submission-guidelines"]
assert J.looks_like_guide(J.html_to_text(GUIDE)) and not J.looks_like_guide(J.html_to_text(HOME))
pages = {"https://example.org/journal/": HOME, "https://example.org/journal/guide-for-authors": GUIDE}
def fake_fetch(url):
    if url not in pages: raise RuntimeError("The page answered with error 404.")
    return pages[url], J.html_to_text(pages[url])
J.fetch = fake_fetch
asked = []
def ask(system, user):
    asked.append(user)
    return '```json\n{"journal": "Test Journal", "abstract_max_words": 250, "keywords_min": 3, "keywords_max": 6, "msc": null, "ref_style": "Author-Year", ' \
           '"anonymous": false, "line_numbers": null, "latex_class": "\\\\documentclass{sn-jnl}", "page_limit": null, "notes": ["Figures as PDF."]}\n```'
res = J.read_guide("https://example.org/journal/", ask)                # a home page: the link to the instructions is followed
assert res["url"] == "https://example.org/journal/guide-for-authors" and "must not exceed 250 words" in asked[0]
v = res["values"]
assert v["abstract_max_words"] == 250 and v["keywords_min"] == 3 and v["anonymous"] is False and "msc" not in v and "ref_style" not in v, v
assert v["latex_class"] == "documentclasssn-jnl" and v["journal_named"] == "Test Journal" and len(v["notes"]) == 2 and "author–year" in v["notes"][1], v
res = J.read_guide(J.html_to_text(GUIDE), ask); assert res["url"] == "" and res["chars"] > 1500             # pasted text
for bad, why in (("https://example.org/missing", "error 404"), ("short text", "too little text")):
    try: J.read_guide(bad, ask); raise SystemExit("should refuse")
    except RuntimeError as e: assert why in str(e), e
try: J.read_guide(J.html_to_text(GUIDE), lambda s, u: "I cannot help with that."); raise SystemExit("should refuse")
except RuntimeError as e: assert "could not be understood" in str(e)
print("journals ok")
