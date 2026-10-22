# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Every reference checked against the databases: does it exist, is it the paper the entry says, is it retracted.

Each BibTeX entry is looked up by its DOI (OpenAlex, then Crossref), its arXiv number (arXiv), or else its title
(OpenAlex). It is "verified" when the title found matches the entry's (and the first author's family name and the
year agree); a DOI that leads to another paper, an entry no database knows, and a retracted paper (OpenAlex's
is_retracted, from the Retraction Watch data) are reported. Only identifiers and titles are sent, never the paper."""
from __future__ import annotations

import difflib
import re

import requests

from . import config, literature as L


def parse_bib(text: str) -> list[dict]:
    """BibTeX entries as {key, type, fields} (fields lower-cased, braces and quotes removed)."""
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text or ""):
        kind = m.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += text[i] == "{"
            depth -= text[i] == "}"
            i += 1
        body, fields = text[m.end():i - 1], {}
        for f in re.finditer(r"(\w+)\s*=\s*", body):
            j = f.end()
            if j < len(body) and body[j] == "{":
                d, k = 0, j
                while k < len(body):
                    d += body[k] == "{"
                    d -= body[k] == "}"
                    k += 1
                    if d == 0:
                        break
                val = body[j + 1:k - 1]
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k if k > 0 else len(body)]
            else:
                val = re.match(r"[^,\n]*", body[j:]).group(0)
            fields.setdefault(f.group(1).lower(), re.sub(r"\s+", " ", re.sub(r"[{}]", "", val)).strip())
        out.append({"key": m.group(2), "type": kind, "fields": fields})
    return out


def _sim(a: str, b: str) -> float:
    a, b = L.norm_title(a or ""), L.norm_title(b or "")
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _family(authors: str) -> str:
    first = re.split(r"\s+and\s+", authors or "")[0].strip()
    if "," in first:
        return first.split(",")[0].strip().lower()
    return first.split()[-1].lower() if first.split() else ""


def _oa(url: str, params: dict | None = None) -> dict | None:
    r = L._session().get(url, params=L._oa_params({**(params or {}),
                                                    "select": "id,doi,title,publication_year,authorships,is_retracted"}), timeout=25)
    return r.json() if r.status_code == 200 else None


def _found(item: dict) -> dict:
    au = [((a.get("author") or {}).get("display_name") or "") for a in item.get("authorships") or []]
    return {"title": item.get("title") or "", "year": item.get("publication_year"), "doi": L.clean_doi(item.get("doi") or ""),
            "first_author": au[0] if au else "", "retracted": bool(item.get("is_retracted"))}


def check_entry(e: dict) -> dict:
    f = e["fields"]
    title, year, fam = f.get("title", ""), str(f.get("year", ""))[:4], _family(f.get("author", ""))
    doi = L.clean_doi(f.get("doi", "") or (f.get("url", "") if "doi.org" in f.get("url", "") else ""))
    arx = re.search(r"(\d{4}\.\d{4,5})", " ".join([f.get("eprint", ""), f.get("arxiv", ""), f.get("url", ""), f.get("journal", ""), f.get("note", "")]))
    rec = {"key": e["key"], "title": title, "status": "unchecked", "detail": "", "found": None}
    if config.mock_mode():
        rec["detail"] = "not checked (offline test)"
        return rec
    try:
        found, how = None, ""
        if doi:
            item = _oa(f"https://api.openalex.org/works/doi:{doi}")
            if item:
                found, how = _found(item), "its DOI"
            else:
                cr = L.crossref_by_doi(doi)
                if cr:
                    found = {"title": cr["title"], "year": cr.get("year"), "doi": doi,
                             "first_author": (cr.get("authors") or [{}])[0].get("family", ""), "retracted": False}
                    how = "its DOI"
                else:
                    rec.update(status="not_found", detail=f"The DOI {doi} is not registered (OpenAlex and Crossref know nothing of it).")
                    return rec
        elif arx:
            res = L.arxiv_by_ids([arx.group(1)])
            if res:
                found = {"title": res[0]["title"], "year": res[0].get("year"), "doi": res[0].get("doi", ""),
                         "first_author": (res[0].get("authors") or [{}])[0].get("family", ""), "retracted": False}
                how = "its arXiv number"
        if found is None and title:
            data = _oa("https://api.openalex.org/works", {"search": title[:250], "per-page": 5}) or {}
            best = max((_found(x) for x in data.get("results") or []), key=lambda x: _sim(x["title"], title), default=None)
            if best and _sim(best["title"], title) >= 0.9:
                found, how = best, "its title"
        if found is None:
            rec.update(status="not_found", detail="No database knows a paper with this title" + (" or identifier." if doi or arx else ". Check the entry."))
            return rec
        rec["found"] = found
        s = _sim(found["title"], title) if title else 1.0
        problems = []
        if title and s < 0.85:
            rec.update(status="mismatch", detail=f"Found by {how}, but it is another paper: “{found['title']}” ({found.get('year') or 'no year'}).")
            return rec
        if fam and found.get("first_author") and fam not in L.ascii_fold(found["first_author"]).lower():
            problems.append(f"first author is {found['first_author']}")
        if year.isdigit() and found.get("year") and abs(int(year) - int(found["year"])) > 1:
            problems.append(f"year is {found['year']}")
        if found.get("retracted"):
            rec.update(status="retracted", detail="This paper has been RETRACTED (Retraction Watch data, via OpenAlex). Do not rely on it.")
            return rec
        if problems:
            rec.update(status="check", detail=f"Found by {how}; but the " + " and the ".join(problems) + ".")
        else:
            rec.update(status="verified", detail=f"Found by {how}" + (f"; DOI {found['doi']}" if found.get("doi") and not doi else "") + ".")
    except (requests.RequestException, ValueError, KeyError) as ex:
        rec.update(status="unchecked", detail=f"Could not be checked now ({type(ex).__name__}); try again later.")
    return rec


def check_bib(text: str, limit: int = 150) -> dict:
    entries = parse_bib(text)[:limit]
    rows = [check_entry(e) for e in entries]
    count = {k: sum(1 for r in rows if r["status"] == k) for k in ("verified", "check", "mismatch", "not_found", "retracted", "unchecked")}
    return {"entries": rows, "count": count}
