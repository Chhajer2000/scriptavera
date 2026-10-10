# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Literature search limited to the author's domain, plus full-text retrieval.

Every reference that can end up in the bibliography comes from one of these
scholarly databases, never from a language model's memory:

  arXiv            exact category filter (math.GR, math.NT, ...)
  zbMATH Open      exact MSC filter; the reference database for mathematics
  OpenAlex         broad coverage, open-access links, citation graph
  Crossref         authoritative journal metadata and DOIs
  Semantic Scholar extra coverage and abstracts
"""

from __future__ import annotations

import io
import math
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

from . import __version__, config, domains
from .util import ascii_fold, norm_title, strip_markup

_STOP = set("""a an and are as at be by for from has have in into is it its of on or over that the their this to
under via we with without which when where whose some any all new note notes paper study result results using
use based towards toward about between among""".split())

_arxiv_lock = threading.Lock()
_arxiv_last = [0.0]


def _session() -> requests.Session:
    s = requests.Session()
    email = config.get_env("CONTACT_EMAIL")
    s.headers["User-Agent"] = f"Scriptavera/{__version__} (research writing tool" + (f"; mailto:{email}" if email else "") + ")"
    return s


def _words(q: str, limit: int = 7) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z\-']{1,}", ascii_fold(q)):
        if w.lower() not in _STOP and w.lower() not in [o.lower() for o in out]:
            out.append(w)
    return out[:limit]


def _split_name(name: str) -> dict:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return {"given": "", "family": ""}
    if "," in name:
        fam, giv = name.split(",", 1)
        return {"given": giv.strip(), "family": fam.strip()}
    parts = name.split(" ")
    if len(parts) == 1:
        return {"given": "", "family": parts[0]}
    # keep lowercase particles (van, de, von ...) with the family name
    i = len(parts) - 1
    while i > 1 and parts[i - 1].islower() and len(parts[i - 1]) <= 3:
        i -= 1
    return {"given": " ".join(parts[:i]), "family": " ".join(parts[i:])}


def _paper(**kw) -> dict:
    p = {"title": "", "authors": [], "year": None, "venue": "", "volume": "", "issue": "", "pages": "",
         "journal_ref": "", "doi": "", "arxiv": "", "zbl": "", "openalex": "", "url": "", "pdf_url": "", "abstract": "",
         "cited_by": 0, "msc": [], "cats": [], "sources": [], "origin": "search",
         "lex": 0.0, "score": None, "why": "", "state": "candidate", "pinned": False,
         "read": False, "fulltext": "", "notes": None, "verdict": ""}
    p.update(kw)
    p["title"] = strip_markup(p["title"])
    p["abstract"] = strip_markup(p["abstract"])
    p["doi"] = clean_doi(p["doi"])
    return p


def clean_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.I)
    return doi.lower().rstrip(".")


def clean_arxiv(aid: str) -> str:
    aid = (aid or "").strip()
    aid = re.sub(r"^(https?://)?(export\.)?arxiv\.org/(abs|pdf)/", "", aid, flags=re.I)
    aid = re.sub(r"^arxiv:", "", aid, flags=re.I)
    aid = re.sub(r"\.pdf$", "", aid)
    return re.sub(r"v\d+$", "", aid)


# ---------------------------------------------------------------- arXiv

_ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def _arxiv_get(params: dict) -> ET.Element | None:
    with _arxiv_lock:                       # arXiv asks for one request every three seconds
        wait = _arxiv_last[0] + 3.1 - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = _session().get("https://export.arxiv.org/api/query", params=params, timeout=40)
        finally:
            _arxiv_last[0] = time.time()
    if r.status_code != 200:
        raise RuntimeError(f"arXiv HTTP {r.status_code}")
    return ET.fromstring(r.content)


def _arxiv_parse(root: ET.Element) -> list[dict]:
    out = []
    for e in root.findall("a:entry", _ATOM):
        raw_id = (e.findtext("a:id", "", _ATOM) or "")
        if "arxiv.org/abs/" not in raw_id:
            continue
        aid = clean_arxiv(raw_id)
        title = re.sub(r"\s+", " ", e.findtext("a:title", "", _ATOM) or "").strip()
        if not title or title.lower() == "error":
            continue
        cats = [c.get("term") for c in e.findall("a:category", _ATOM) if c.get("term")]
        prim = e.find("x:primary_category", _ATOM)
        if prim is not None and prim.get("term") in cats:
            cats.remove(prim.get("term"))
            cats.insert(0, prim.get("term"))
        published = e.findtext("a:published", "", _ATOM) or ""
        out.append(_paper(
            title=title,
            authors=[_split_name(a.findtext("a:name", "", _ATOM)) for a in e.findall("a:author", _ATOM)],
            year=int(published[:4]) if published[:4].isdigit() else None,
            journal_ref=re.sub(r"\s+", " ", e.findtext("x:journal_ref", "", _ATOM) or "").strip(),
            doi=e.findtext("x:doi", "", _ATOM) or "",
            arxiv=aid, url=f"https://arxiv.org/abs/{aid}", pdf_url=f"https://arxiv.org/pdf/{aid}",
            abstract=re.sub(r"\s+", " ", e.findtext("a:summary", "", _ATOM) or "").strip(),
            cats=cats, sources=["arXiv"]))
    return out


def arxiv_search(query: str, cats: list[str], n: int = 12) -> list[dict]:
    words = _words(query, 6)
    if not words:
        return []
    cat_q = "(" + " OR ".join(f"cat:{c}" for c in cats) + ") AND " if cats else ""

    def term(w: str) -> str:
        return f'all:"{w}"' if re.search(r"[^A-Za-z0-9]", w) else f"all:{w}"

    def run(ws: list[str]) -> list[dict]:
        q = cat_q + "(" + " AND ".join(term(w) for w in ws) + ")"
        return _arxiv_parse(_arxiv_get({"search_query": q, "start": 0, "max_results": n, "sortBy": "relevance"}))

    res = run(words)
    if len(res) < 3 and len(words) > 3:      # too specific: keep the most distinctive words
        res += run(sorted(words, key=len, reverse=True)[:3])
    return res


def arxiv_by_ids(ids: list[str]) -> list[dict]:
    ids = [clean_arxiv(i) for i in ids if i]
    if not ids:
        return []
    return _arxiv_parse(_arxiv_get({"id_list": ",".join(ids), "max_results": len(ids)}))


def arxiv_find_title(title: str) -> dict | None:
    """Look for a preprint of a journal article so its full text can be read."""
    t = re.sub(r"[^A-Za-z0-9 ]+", " ", ascii_fold(title)).strip()
    if len(t) < 12:
        return None
    try:
        res = _arxiv_parse(_arxiv_get({"search_query": f'ti:"{t}"', "max_results": 3}))
    except Exception:
        return None
    for p in res:
        if norm_title(p["title"]) == norm_title(title):
            return p
    return None


# -------------------------------------------------------------- OpenAlex

_OA_SELECT = ("id,doi,title,publication_year,authorships,primary_location,best_oa_location,open_access,"
              "abstract_inverted_index,cited_by_count,biblio,type,ids")


def _oa_params(extra: dict) -> dict:
    p = dict(extra)
    if config.get_env("OPENALEX_API_KEY"):
        p["api_key"] = config.get_env("OPENALEX_API_KEY")
    if config.get_env("CONTACT_EMAIL"):
        p["mailto"] = config.get_env("CONTACT_EMAIL")
    return p


def _oa_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos))[:4000]


def _oa_parse(item: dict) -> dict | None:
    title = item.get("title") or item.get("display_name") or ""
    if not title:
        return None
    loc = item.get("primary_location") or {}
    src = loc.get("source") or {}
    best = item.get("best_oa_location") or {}
    bib = item.get("biblio") or {}
    pages = bib.get("first_page") or ""
    if pages and bib.get("last_page") and bib["last_page"] != pages:
        pages += "--" + bib["last_page"]
    doi = clean_doi(item.get("doi") or "")
    arxiv = ""
    for cand in (best.get("landing_page_url"), best.get("pdf_url"), loc.get("landing_page_url")):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", cand or "")
        if m:
            arxiv = m.group(1)
            break
    if not arxiv and doi.startswith("10.48550/arxiv."):
        arxiv = doi.split("arxiv.", 1)[1]
    return _paper(
        title=title,
        authors=[_split_name((a.get("author") or {}).get("display_name", "")) for a in item.get("authorships") or []][:12],
        year=item.get("publication_year"),
        venue=src.get("display_name") or "",
        volume=bib.get("volume") or "", issue=bib.get("issue") or "", pages=pages,
        doi="" if doi.startswith("10.48550/arxiv.") else doi,
        arxiv=arxiv, openalex=(item.get("id") or "").rsplit("/", 1)[-1],
        url=(loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else "")),
        pdf_url=best.get("pdf_url") or (item.get("open_access") or {}).get("oa_url") or "",
        abstract=_oa_abstract(item.get("abstract_inverted_index")),
        cited_by=item.get("cited_by_count") or 0, sources=["OpenAlex"])


def _oa_fields(domain_ids: list[str]) -> str:
    fields = ["26"]                                  # Mathematics
    if any(d in ("cs.DM", "math.IT") for d in domain_ids):
        fields.append("17")                          # Computer Science
    if "math.MP" in domain_ids:
        fields.append("31")                          # Physics and Astronomy
    if "math.ST" in domain_ids or "math.OC" in domain_ids:
        fields.append("18")                          # Decision Sciences
    return "|".join(fields)


def openalex_search(query: str, domain_ids: list[str], n: int = 12) -> list[dict]:
    s = _session()
    base = {"search": query, "per-page": n, "select": _OA_SELECT}
    items: list[dict] = []
    r = s.get("https://api.openalex.org/works",
              params=_oa_params({**base, "filter": f"primary_topic.field.id:{_oa_fields(domain_ids)}"}), timeout=30)
    if r.status_code == 200:
        items = r.json().get("results") or []
    elif r.status_code == 429:
        raise RuntimeError("OpenAlex daily allowance used up (add a free OpenAlex key in Settings)")
    if len(items) < 4:                               # field labels are imperfect: widen once
        r = s.get("https://api.openalex.org/works", params=_oa_params(base), timeout=30)
        if r.status_code == 200:
            seen = {i.get("id") for i in items}
            items += [i for i in r.json().get("results") or [] if i.get("id") not in seen]
        elif not items:
            raise RuntimeError(f"OpenAlex HTTP {r.status_code}")
    return [p for p in (_oa_parse(i) for i in items) if p]


def openalex_lookup(paper: dict) -> str:
    """Find the OpenAlex id of a paper we already know."""
    if paper.get("openalex"):
        return paper["openalex"]
    s = _session()
    tries = []
    if paper.get("doi"):
        tries.append(f"https://api.openalex.org/works/doi:{paper['doi']}")
    if paper.get("arxiv"):
        tries.append(f"https://api.openalex.org/works/doi:10.48550/arxiv.{paper['arxiv']}")
    for url in tries:
        try:
            r = s.get(url, params=_oa_params({"select": "id"}), timeout=20)
            if r.status_code == 200:
                return (r.json().get("id") or "").rsplit("/", 1)[-1]
        except requests.RequestException:
            pass
    return ""


def openalex_neighbors(paper: dict, n: int = 12) -> list[dict]:
    """Papers this one cites, and papers citing it: how a researcher follows a trail."""
    wid = openalex_lookup(paper)
    if not wid:
        return []
    s = _session()
    out: list[dict] = []
    for flt in (f"cited_by:{wid}", f"cites:{wid}"):
        try:
            r = s.get("https://api.openalex.org/works",
                      params=_oa_params({"filter": flt, "per-page": n, "sort": "cited_by_count:desc",
                                         "select": _OA_SELECT}), timeout=30)
            if r.status_code == 200:
                for it in r.json().get("results") or []:
                    p = _oa_parse(it)
                    if p:
                        p["origin"] = "graph"
                        out.append(p)
        except requests.RequestException:
            continue
    return out


# -------------------------------------------------------------- Crossref

def _cr_parse(it: dict) -> dict | None:
    title = (it.get("title") or [""])[0]
    if not title:
        return None
    issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
    return _paper(
        title=title,
        authors=[{"given": a.get("given", ""), "family": a.get("family", "") or a.get("name", "")}
                 for a in it.get("author") or [] if a.get("family") or a.get("name")][:12],
        year=issued[0] if issued and isinstance(issued[0], int) else None,
        venue=(it.get("container-title") or [""])[0],
        volume=it.get("volume") or "", issue=it.get("issue") or "",
        pages=(it.get("page") or "").replace("-", "--", 1) if "--" not in (it.get("page") or "") else it.get("page"),
        doi=it.get("DOI") or "", url=it.get("URL") or "",
        abstract=it.get("abstract") or "", cited_by=it.get("is-referenced-by-count") or 0,
        sources=["Crossref"])


def crossref_search(query: str, domain_ids: list[str], n: int = 10) -> list[dict]:
    params = {"query.bibliographic": query, "rows": n,
              "select": "DOI,title,author,issued,container-title,volume,issue,page,type,abstract,URL,is-referenced-by-count"}
    if config.get_env("CONTACT_EMAIL"):
        params["mailto"] = config.get_env("CONTACT_EMAIL")
    r = _session().get("https://api.crossref.org/works", params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Crossref HTTP {r.status_code}")
    keep = {"journal-article", "proceedings-article", "book-chapter", "posted-content", "book", "monograph"}
    return [p for p in (_cr_parse(i) for i in r.json().get("message", {}).get("items", [])
                        if i.get("type") in keep) if p]


def crossref_by_doi(doi: str) -> dict | None:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        r = _session().get(f"https://api.crossref.org/works/{requests.utils.quote(doi, safe='/')}", timeout=25)
        if r.status_code == 200:
            return _cr_parse(r.json().get("message") or {})
    except requests.RequestException:
        pass
    return None


# ------------------------------------------------------ Semantic Scholar

def s2_search(query: str, domain_ids: list[str], n: int = 10) -> list[dict]:
    fos = ["Mathematics"]
    if any(d in ("cs.DM", "math.IT") for d in domain_ids):
        fos.append("Computer Science")
    if "math.MP" in domain_ids:
        fos.append("Physics")
    headers = {"x-api-key": config.get_env("S2_API_KEY")} if config.get_env("S2_API_KEY") else {}
    params = {"query": query, "limit": n, "fieldsOfStudy": ",".join(fos),
              "fields": "title,abstract,year,authors,venue,externalIds,openAccessPdf,citationCount,journal"}
    r = None
    for attempt in range(2):
        r = _session().get("https://api.semanticscholar.org/graph/v1/paper/search", params=params,
                           headers=headers, timeout=30)
        if r.status_code != 429:
            break
        time.sleep(3.5)
    if r is None or r.status_code != 200:
        raise RuntimeError("Semantic Scholar is rate-limited right now" if r is not None and r.status_code == 429
                           else f"Semantic Scholar HTTP {getattr(r, 'status_code', '?')}")
    out = []
    for it in r.json().get("data") or []:
        ext = it.get("externalIds") or {}
        j = it.get("journal") or {}
        out.append(_paper(
            title=it.get("title") or "",
            authors=[_split_name(a.get("name", "")) for a in it.get("authors") or []][:12],
            year=it.get("year"), venue=j.get("name") or it.get("venue") or "",
            volume=str(j.get("volume") or ""), pages=str(j.get("pages") or "").strip().replace("-", "--", 1),
            doi=ext.get("DOI") or "", arxiv=ext.get("ArXiv") or "",
            url=f"https://www.semanticscholar.org/paper/{it.get('paperId')}",
            pdf_url=(it.get("openAccessPdf") or {}).get("url") or "",
            abstract=it.get("abstract") or "", cited_by=it.get("citationCount") or 0,
            sources=["Semantic Scholar"]))
    return [p for p in out if p["title"]]


# ----------------------------------------------------------- zbMATH Open

def zbmath_search(query: str, msc: list[str], n: int = 12) -> list[dict]:
    s = _session()
    words = " ".join(_words(query, 6))
    if not words:
        return []
    two = sorted({m[:2] for m in msc})
    attempts = []
    if two:
        attempts.append(f"{words} & cc:({' | '.join(two)})")
    attempts.append(words)
    items: list[dict] = []
    for q in attempts:
        try:
            r = s.get("https://api.zbmath.org/v1/document/_search",
                      params={"search_string": q, "results_per_page": n, "page": 0}, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            items = r.json().get("result") or []
            if items:
                break
    out = []
    for it in items:
        codes = [m.get("code", "") for m in it.get("msc") or []]
        if msc and codes and not any(c.startswith(pref) for c in codes for pref in msc):
            continue                                   # outside the author's domain
        t = it.get("title") or {}
        title = t.get("title") or t.get("original") or ""
        if not title or title.lower().startswith("zbmath open web interface"):
            continue
        src = it.get("source") or {}
        ser = (src.get("series") or [{}])[0] or {}
        doi, arxiv = "", ""
        for l in it.get("links") or []:
            if l.get("type") == "doi":
                doi = l.get("identifier") or ""
            elif l.get("type") == "arxiv":
                arxiv = l.get("identifier") or ""
        review = ""
        for ec in it.get("editorial_contributions") or []:
            txt = ec.get("text") or ""
            if txt and "zbmath open web interface" not in txt.lower():
                review = txt
                break
        out.append(_paper(
            title=title,
            authors=[_split_name(a.get("name", "")) for a in (it.get("contributors") or {}).get("authors") or []][:12],
            year=int(it["year"]) if str(it.get("year") or "").isdigit() else None,
            venue=ser.get("short_title") or ser.get("title") or "",
            volume=str(ser.get("volume") or ""), issue=str(ser.get("issue") or ""),
            pages=str(ser.get("pages") or "").replace("-", "--", 1),
            doi=doi, arxiv=clean_arxiv(arxiv), zbl=str(it.get("identifier") or ""),
            url=it.get("zbmath_url") or "", abstract=review, msc=codes, sources=["zbMATH"]))
    return out


# -------------------------------------------------------- merge and rank

def identity(p: dict) -> list[str]:
    ids = []
    if p.get("doi"):
        ids.append("doi:" + p["doi"])
    if p.get("arxiv"):
        ids.append("arxiv:" + p["arxiv"])
    nt = norm_title(p.get("title", ""))
    if len(nt) >= 12:
        ids.append("t:" + nt)
    return ids


def merge_into(base: dict, new: dict) -> None:
    """Combine two records of the same paper, keeping the richest metadata."""
    journal_new = bool(new.get("venue")) and "arxiv" not in new.get("venue", "").lower()
    for k in ("doi", "arxiv", "zbl", "openalex", "pdf_url", "volume", "issue", "pages", "journal_ref"):
        if not base.get(k) and new.get(k):
            base[k] = new[k]
    if (not base.get("venue") or "arxiv" in base["venue"].lower()) and journal_new:
        base["venue"] = new["venue"]
        for k in ("volume", "issue", "pages"):
            if new.get(k):
                base[k] = new[k]
        if new.get("year"):
            base["year"] = new["year"]
    if len(new.get("abstract") or "") > len(base.get("abstract") or ""):
        base["abstract"] = new["abstract"]
    if len(new.get("authors") or []) > len(base.get("authors") or []):
        base["authors"] = new["authors"]
    if not base.get("year") and new.get("year"):
        base["year"] = new["year"]
    if not base.get("url") and new.get("url"):
        base["url"] = new["url"]
    base["cited_by"] = max(base.get("cited_by") or 0, new.get("cited_by") or 0)
    for k in ("msc", "cats", "sources"):
        for v in new.get(k) or []:
            if v not in base[k]:
                base[k].append(v)
    if new.get("origin") == "user":
        base["origin"], base["pinned"] = "user", True


def add_papers(library: list[dict], new: list[dict]) -> int:
    """Add papers to the library without duplicates. Returns how many were new."""
    index: dict[str, dict] = {}
    for p in library:
        for i in identity(p):
            index[i] = p
    added = 0
    for p in new:
        if not p.get("title"):
            continue
        hit = next((index[i] for i in identity(p) if i in index), None)
        if hit:
            merge_into(hit, p)
            for i in identity(hit):
                index[i] = hit
        else:
            library.append(p)
            added += 1
            for i in identity(p):
                index[i] = p
    assign_keys(library)
    return added


def assign_keys(library: list[dict]) -> None:
    used = {p["key"] for p in library if p.get("key")}
    for p in library:
        if p.get("key"):
            continue
        fam = (p["authors"][0]["family"] if p.get("authors") else "") or "Anon"
        fam = re.sub(r"[^A-Za-z]", "", ascii_fold(fam)) or "Anon"
        word = next((w for w in re.findall(r"[A-Za-z]{4,}", ascii_fold(p.get("title", "")))
                     if w.lower() not in _STOP), "")
        base = f"{fam}{p.get('year') or ''}{word.capitalize()}"
        key, i = base, 0
        while key in used:
            i += 1
            key = base + "abcdefghijklmnopqrstuvwxyz"[min(i - 1, 25)] + (str(i) if i > 26 else "")
        p["key"] = key
        used.add(key)


def lexical_score(p: dict, keywords: list[str], domain_ids: list[str]) -> float:
    title = " " + ascii_fold(p.get("title", "")).lower() + " "
    abstract = " " + ascii_fold(p.get("abstract", "")).lower() + " "
    s = 0.0
    for kw in keywords:
        k = ascii_fold(kw).lower().strip()
        if not k:
            continue
        if k in title:
            s += 3.0
        elif all(w in title for w in k.split()):
            s += 1.5
        if k in abstract:
            s += 1.0
    if set(p.get("cats") or []) & set(domains.arxiv_categories(domain_ids)):
        s += 2.0
    if any(c.startswith(pref) for c in p.get("msc") or [] for pref in domains.msc_prefixes(domain_ids)):
        s += 2.0
    s += min(1.5, math.log10(1 + (p.get("cited_by") or 0)) * 0.5)
    s += 0.5 * (len(p.get("sources") or []) - 1)
    if not p.get("abstract"):
        s -= 1.0
    return round(s, 2)


def search(queries: list[str], domain_ids: list[str], log=lambda m: None, cancelled=lambda: False) -> tuple[list[dict], dict]:
    """Run every query against every source. Returns (papers, per-source status)."""
    if config.mock_mode():
        devmock = config.simulator()
        return devmock.papers(), {"Simulated library": {"ok": True, "found": len(devmock.papers()), "message": ""}}
    cats = domains.arxiv_categories(domain_ids)
    msc = domains.msc_prefixes(domain_ids)
    sources = {
        "arXiv": lambda q: arxiv_search(q, cats),
        "zbMATH": lambda q: zbmath_search(q, msc),
        "OpenAlex": lambda q: openalex_search(q, domain_ids),
        "Crossref": lambda q: crossref_search(q, domain_ids),
        "Semantic Scholar": lambda q: s2_search(q, domain_ids),
    }
    status = {name: {"ok": False, "found": 0, "message": "", "fails": 0} for name in sources}
    found: list[dict] = []
    for q in queries:
        if cancelled():
            break
        log(f"Searching: {q}")
        for name, fn in sources.items():
            st = status[name]
            if st["fails"] >= 2:                       # this source is down or limited; stop asking
                continue
            try:
                res = fn(q)
                st["ok"] = True
                st["found"] += len(res)
                found += res
            except Exception as e:  # noqa: BLE001
                st["fails"] += 1
                st["message"] = str(e)[:140]
    for st in status.values():
        if st["ok"]:
            st["message"] = "" if not st["fails"] else st["message"]
    return found, status


def resolve_identifier(text: str) -> dict | None:
    """A DOI, an arXiv id/URL, or a title typed by the author -> one paper record."""
    t = (text or "").strip()
    if not t:
        return None
    p: dict | None = None
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", t)
    old = re.search(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})\b", t)
    if "arxiv" in t.lower() or (m and "10." not in t) or old:
        aid = m.group(1) if m else (old.group(1) if old else clean_arxiv(t))
        try:
            res = arxiv_by_ids([aid])
            p = res[0] if res else None
        except Exception:
            p = None
    if p is None:
        d = re.search(r"10\.\d{4,9}/\S+", t)
        if d:
            p = crossref_by_doi(d.group(0).rstrip(").,;"))
    if p is None and len(t) > 12:
        try:
            res = crossref_search(t, [], 3)
            p = next((r for r in res if norm_title(r["title"])[:30] == norm_title(t)[:30]), res[0] if res else None)
        except Exception:
            p = None
    if p:
        p["origin"], p["pinned"], p["state"] = "user", True, "selected"
    return p


# -------------------------------------------------------------- full text

def _pdf_text(data: bytes, max_pages: int = 60) -> str:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    parts = []
    try:
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            tp = page.get_textpage()
            parts.append(tp.get_text_bounded() or "")
            tp.close()
            page.close()
    finally:
        pdf.close()
    text = "\n".join(parts).replace("\r", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)            # undo line-break hyphenation
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    cut = max(text.rfind("\nReferences\n"), text.rfind("\nREFERENCES\n"), text.rfind("\nBibliography\n"))
    if cut > len(text) * 0.5:
        text = text[:cut]
    return text.strip()


def _download_pdf(url: str) -> bytes | None:
    try:
        r = _session().get(url, timeout=45, stream=True, allow_redirects=True)
        if r.status_code != 200:
            return None
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            buf.write(chunk)
            if buf.tell() > 30_000_000:
                return None
        data = buf.getvalue()
        return data if data[:5] == b"%PDF-" else None
    except requests.RequestException:
        return None


def fetch_fulltext(paper: dict) -> tuple[str, str]:
    """Return (text, how). `how` is 'full text' or 'abstract only'."""
    if config.mock_mode():
        return (paper.get("abstract") or "") + "\n\n(Simulated full text.)", "full text"
    urls = []
    if not paper.get("arxiv") and paper.get("title"):
        pre = arxiv_find_title(paper["title"])
        if pre:
            paper["arxiv"] = pre["arxiv"]
            if not paper.get("abstract"):
                paper["abstract"] = pre["abstract"]
    if paper.get("arxiv"):
        urls.append(f"https://arxiv.org/pdf/{paper['arxiv']}")
    if paper.get("pdf_url"):
        urls.append(paper["pdf_url"])
    email = config.get_env("CONTACT_EMAIL")
    if paper.get("doi") and email:
        try:
            r = _session().get(f"https://api.unpaywall.org/v2/{paper['doi']}", params={"email": email}, timeout=20)
            if r.status_code == 200:
                loc = r.json().get("best_oa_location") or {}
                if loc.get("url_for_pdf"):
                    urls.append(loc["url_for_pdf"])
        except requests.RequestException:
            pass
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        data = _download_pdf(u)
        if not data:
            continue
        try:
            text = _pdf_text(data)
        except Exception:
            continue
        if len(text) > 1500:
            return text, "full text"
    return paper.get("abstract") or "", "abstract only"
