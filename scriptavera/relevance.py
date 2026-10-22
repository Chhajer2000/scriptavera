# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Relevance and novelty: how close each paper is to each result of the notes.

Unpublished notes are cited by nobody, so the papers they rest on (the author's key papers and the closest papers
read) stand in for their reference list. Signals, combined into one score:
- bibliographic coupling: a paper that cites many of the same papers as those references treats a related subject;
- co-citation: a paper that those references cite together is a shared foundation;
- vector similarity between each result's statement and the paper's title and abstract: embeddings from the AI
  provider whose key is set (Gemini or OpenAI), otherwise TF-IDF vectors computed here;
- the word match the literature search already computes.
Candidates come from the usual search, from a search by each main result's statement, from the citation network,
and from OEIS (integer sequences in the notes) and MathOverflow / Math StackExchange. Only the best go to the
assessment, which decides for each result whether a paper has it, a special case of it, or something else."""
from __future__ import annotations

import math
import re
from collections import Counter

import requests

from . import config, literature
from .util import fit

_W = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = set("the and for with that this from are was were which has have been its their there these those such then than "
            "into onto over under also only each every some any all not but can may let suppose show prove proof lemma theorem "
            "paper result results where when what whose our one two three let given".split())


def _terms(text: str) -> list[str]:
    text = re.sub(r"\\[A-Za-z]+", " ", (text or "").lower())
    return [w for w in _W.findall(text) if w not in _STOP]


def tfidf(texts: list[str]) -> list[dict[str, float]]:
    docs = [Counter(_terms(t)) for t in texts]
    df = Counter(w for d in docs for w in d)
    n = len(docs)
    out = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((1 + n) / (1 + df[w])) for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cos_sparse(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def embed(texts: list[str]) -> tuple[list[list[float]] | None, str]:
    """Dense vectors from the provider whose key is set; (None, '') when none is, or the call fails."""
    if config.mock_mode() or not texts:
        return None, ""
    texts = [fit(t, 2000, "head") or " " for t in texts]
    key = config.get_env("GEMINI_API_KEY")
    try:
        if key:
            vecs: list[list[float]] = []
            for i in range(0, len(texts), 90):
                r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents",
                                  params={"key": key}, timeout=60,
                                  json={"requests": [{"model": "models/gemini-embedding-001", "content": {"parts": [{"text": t}]}}
                                                     for t in texts[i:i + 90]]})
                r.raise_for_status()
                vecs += [e["values"] for e in r.json()["embeddings"]]
            return vecs, "Gemini embeddings"
        key = config.get_env("OPENAI_API_KEY")
        if key and not config.load_settings().get("private"):
            r = requests.post("https://api.openai.com/v1/embeddings", headers={"Authorization": f"Bearer {key}"}, timeout=60,
                              json={"model": "text-embedding-3-small", "input": texts})
            r.raise_for_status()
            return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])], "OpenAI embeddings"
    except (requests.RequestException, KeyError, ValueError, TypeError):
        pass
    return None, ""


def similarities(queries: list[str], papers: list[dict]) -> tuple[list[list[float]], str]:
    """sims[i][j]: how close result i is to paper j (0-1)."""
    docs = [f"{p.get('title', '')}. {p.get('abstract', '')}" for p in papers]
    vecs, how = embed(queries + docs)
    if vecs:
        q, d = vecs[:len(queries)], vecs[len(queries):]
        return [[max(0.0, _cos(a, b)) for b in d] for a in q], how
    v = tfidf(queries + docs)
    q, d = v[:len(queries)], v[len(queries):]
    return [[_cos_sparse(a, b) for b in d] for a in q], "word vectors (TF-IDF) computed on this computer"


# ------------------------------------------------------------------ the citation network

def _oa_refs(ids: list[str]) -> dict[str, dict]:
    """OpenAlex ids -> {refs: set, related: set} for up to 50 ids per request."""
    out: dict[str, dict] = {}
    s = literature._session()
    for i in range(0, len(ids), 50):
        chunk = [x for x in ids[i:i + 50] if x]
        if not chunk:
            continue
        try:
            r = s.get("https://api.openalex.org/works", timeout=40, params=literature._oa_params(
                {"filter": "openalex:" + "|".join(chunk), "per-page": 50, "select": "id,referenced_works,related_works"}))
            if r.status_code != 200:
                continue
            for it in r.json().get("results") or []:
                wid = (it.get("id") or "").rsplit("/", 1)[-1]
                out[wid] = {"refs": {x.rsplit("/", 1)[-1] for x in it.get("referenced_works") or []},
                            "related": {x.rsplit("/", 1)[-1] for x in it.get("related_works") or []}}
        except (requests.RequestException, ValueError):
            continue
    return out


def graph(library: list[dict], seeds: list[dict], refs: dict[str, dict] | None = None) -> dict:
    """Coupling and co-citation of every paper with the seeds, and the edges of a graph like Connected Papers'."""
    ids = [p.get("openalex") for p in library if p.get("openalex")]
    if refs is None:
        refs = {} if config.mock_mode() else _oa_refs(ids)
    seed_ids = [p.get("openalex") for p in seeds if p.get("openalex") in refs]
    seed_refs = Counter(r for sid in seed_ids for r in refs[sid]["refs"])
    related = Counter(r for sid in seed_ids for r in refs[sid]["related"])
    out = {}
    for p in library:
        wid = p.get("openalex")
        if not wid:
            continue
        mine = refs.get(wid, {}).get("refs", set())
        coupling = len(mine & set(seed_refs)) / math.sqrt(max(1, len(mine)) * max(1, len(seed_refs))) if mine else 0.0
        out[wid] = {"coupling": round(coupling, 3), "cocited": seed_refs.get(wid, 0), "related": related.get(wid, 0),
                    "cites_seeds": len(mine & set(seed_ids))}
    edges = []
    known = [w for w in ids if w in refs]
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            ra, rb = refs[a]["refs"], refs[b]["refs"]
            shared = len(ra & rb)
            direct = (b in ra) or (a in rb)
            w = shared / math.sqrt(max(1, len(ra)) * max(1, len(rb))) + (0.3 if direct else 0)
            if w >= 0.08:
                edges.append([a, b, round(w, 3)])
    return {"papers": out, "edges": sorted(edges, key=lambda e: -e[2])[:160], "seeds": seed_ids}


# ------------------------------------------------------------------ other places a result may be known

_SEQ = re.compile(r"(?<![\d.])(\d{1,7}(?:\s*,\s*\d{1,7}){4,})(?![\d.])")


def oeis(text: str, limit: int = 3) -> list[dict]:
    """Integer sequences written in the notes, looked up in OEIS (not in test mode: OEIS cannot be limited by date)."""
    if config.mock_mode() or literature.limits():
        return []
    out, seen = [], set()
    for m in _SEQ.finditer(text or ""):
        seq = ",".join(x.strip() for x in m.group(1).split(","))
        if seq in seen or len(set(seq.split(","))) < 3:
            continue
        seen.add(seq)
        try:
            r = literature._session().get("https://oeis.org/search", params={"q": seq, "fmt": "json"}, timeout=25)
            data = r.json() if r.status_code == 200 else None
            rows = data.get("results") if isinstance(data, dict) else data
            for it in (rows or [])[:limit]:
                out.append({"sequence": seq, "id": f"A{int(it.get('number', 0)):06d}", "name": str(it.get("name", ""))[:200],
                            "url": f"https://oeis.org/A{int(it.get('number', 0)):06d}"})
        except (requests.RequestException, ValueError, TypeError):
            continue
        if len(seen) >= 4:
            break
    return out


def threads(queries: list[str], per: int = 3) -> list[dict]:
    """MathOverflow and Math StackExchange questions matching the statements (public Stack Exchange API)."""
    if config.mock_mode():
        return []
    out, seen = [], set()
    for q in queries[:6]:
        for site in ("mathoverflow", "math"):
            try:
                params = {"order": "desc", "sort": "relevance", "q": q, "site": site, "pagesize": per, "filter": "default"}
                if (literature.limits() or {}).get("before"):     # test mode: only questions asked before the cutoff
                    import datetime
                    params["todate"] = int(datetime.datetime.fromisoformat(literature.limits()["before"][:10]).timestamp())
                r = literature._session().get("https://api.stackexchange.com/2.3/search/advanced", timeout=25, params=params)
                for it in (r.json().get("items") or [])[:per] if r.status_code == 200 else []:
                    if it.get("link") in seen:
                        continue
                    seen.add(it.get("link"))
                    out.append({"site": "MathOverflow" if site == "mathoverflow" else "Math StackExchange",
                                "title": re.sub(r"&#?\w+;", "", str(it.get("title", "")))[:200], "url": it.get("link"),
                                "score": it.get("score", 0), "answered": bool(it.get("is_answered")), "query": q})
            except (requests.RequestException, ValueError):
                continue
    return out


def combine(sim: float, g: dict | None, lex: float) -> float:
    g = g or {}
    return round(0.5 * sim + 0.25 * min(1.0, g.get("coupling", 0) * 3) + 0.1 * min(1.0, g.get("cocited", 0) / 2)
                 + 0.05 * min(1.0, g.get("related", 0)) + 0.1 * min(1.0, max(0.0, lex) / 10), 4)


def closest(results: list[dict], library: list[dict], g: dict, top: int = 4) -> tuple[dict, str]:
    """For each result, the closest papers with their signals."""
    papers = [p for p in library if p.get("state") != "excluded"]
    if not results or not papers:
        return {}, ""
    sims, how = similarities([r["statement"] for r in results], papers)
    out = {}
    for i, r in enumerate(results):
        rows = []
        for j, p in enumerate(papers):
            gp = g.get("papers", {}).get(p.get("openalex") or "", {})
            rows.append({"key": p.get("key", ""), "title": p.get("title", ""), "year": p.get("year"), "sim": round(sims[i][j], 3),
                         **{k: gp.get(k, 0) for k in ("coupling", "cocited", "related")},
                         "score": combine(sims[i][j], gp, p.get("lex", 0))})
        out[r["id"]] = sorted(rows, key=lambda x: -x["score"])[:top]
    return out, how
