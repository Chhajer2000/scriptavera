# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The first draft: understand the whole paper, write the introduction, decide the structure, write each section.

A model sees only what is in its request, so nothing here relies on a model remembering an earlier call. The
"dossier" produced by the first step (problem, definitions, every result with its dependencies and pages, the
storyline) travels with every later request, together with only what that request needs.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time

from . import audit as fidelity
from . import paper as paperlib, prompts
from .llm import LLMError, router
from .pipeline import Run, _PAGE_MARK, _room, audit_pages, emit, find_figures, notation_text, page_gaps, page_marks, passing, writing_pages
from .store import Project
from .texutil import check_balance, clean_fragment, find_group, leaks, split_math
from .util import parse_rows, est_tokens, extract_tag, fit, parse_lines

RESULT_KINDS = ("lemma", "proposition", "theorem", "corollary")

# ------------------------------------------------------------- citations

CITE_RE = re.compile(r"\\cite[tp]?\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")


def cited_keys(texts: list[str]) -> list[str]:
    keys: list[str] = []
    for t in texts:
        for m in CITE_RE.finditer(t or ""):
            for k in m.group(1).split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
    return keys


def fix_keys(text: str, valid: dict[str, dict], log: list[str], fuzzy: bool = True) -> str:
    """Only citations of papers that came from the databases survive. A near-miss key is corrected, unless `fuzzy`
    is off: then every key not in `valid` is removed (used to keep exactly the papers the author chose)."""
    def repl(m: re.Match) -> str:
        good = []
        for k in [x.strip() for x in m.group(1).split(",") if x.strip()]:
            if k in valid:
                good.append(k)
                continue
            near = difflib.get_close_matches(k, list(valid), n=1, cutoff=0.82) if fuzzy else []
            if near:
                good.append(near[0])
                log.append(f"Citation key '{k}' corrected to '{near[0]}'.")
            else:
                log.append(f"Citation '{k}' removed: it does not match any paper from the databases.")
        if not good:
            return "\x00"
        return m.group(0).replace(m.group(1), ",".join(dict.fromkeys(good)))
    out = CITE_RE.sub(repl, text or "")
    if "\x00" not in out:
        return out
    lead = r"(?:see also|see,? e\.g\.,?|see|cf\.|e\.g\.,?)?"                # "(see [x])" left empty by the removal
    out = re.sub(r"\s*\(\s*" + lead + r"[\s~]*\x00\s*\)|\s*\[\s*\x00\s*\]", "", out, flags=re.I)
    return re.sub(r"[ ~]*\x00", "", out)


def drop_unsourced(text: str, valid: dict[str, dict], log: list[str]) -> str:
    """In text written by a model, a sentence whose only citation matches no known paper is removed whole:
    without a source, the claim has no place in the paper."""
    paras = []
    for para in re.split(r"\n\s*\n", text or ""):
        kept = []
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\\$])", para.strip()):
            keys = cited_keys([sent])
            known = [k for k in keys if k in valid or difflib.get_close_matches(k, list(valid), n=1, cutoff=0.82)]
            if keys and not known and "\\[" not in sent and "\\begin" not in sent:
                log.append("Removed a sentence that cited a source not found in any database: \""
                           + re.sub(r"\s+", " ", sent)[:140] + "\"")
                continue
            kept.append(sent)
        if kept:
            paras.append(" ".join(kept))
    return "\n\n".join(paras)


def citable(p: Project) -> list[dict]:
    return [x for x in p.load("library", []) if x.get("state") != "excluded"
            and ((x.get("read") and x.get("verdict") in ("core", "related", "background")) or x.get("pinned"))]


def sources_text(p: Project, per: int = 5) -> str:
    out = []
    for x in citable(p):
        nt = x.get("notes") or {}
        authors = ", ".join(a.get("family", "") for a in x.get("authors", [])[:4])
        facts = (nt.get("citable") or nt.get("results") or [fit(x.get("abstract", ""), 300, "head")])[:per]
        out.append(f"KEY {x['key']} | {authors} ({x.get('year')}) | {x['title']}\n"
                   f"  relevance: {x.get('verdict') or 'given by the author'}; {nt.get('relation', '')}\n"
                   f"  supports: " + " | ".join(str(f) for f in facts if f))
    return "\n".join(out)


# ------------------------------------------------------ 1. understanding

def _view(pages: list[dict], room: int) -> str:
    full = "\n\n".join(f"%%% page {pg['n']}\n{pg['text']}" for pg in pages)
    if len(full) <= room:
        return full
    per = max(300, room // max(1, len(pages)) - 30)          # too long for one request: the opening of every page
    return "\n\n".join(f"%%% page {pg['n']}\n{fit(pg['text'], per, 'head')}" for pg in pages)


def fields(text: str, tag: str, keys: tuple[str, ...]) -> list[dict]:
    """<tag> blocks of 'key: value' lines. Only the expected keys start a field, so a value that happens to
    contain 'Then: ...' on a new line stays whole."""
    out = []
    # models sometimes decorate the keys: "- id: R1", "**kind:** lemma"
    pat = re.compile(r"^\s*(?:[-*•]\s+)?\**\s*(" + "|".join(keys) + r")\s*\**\s*:\s*\**\s?(.*)$", re.I)
    for body in re.findall(rf"<{tag}\b[^>]*>(.*?)(?:</{tag}\s*>|(?=<{tag}\b)|\Z)", text or "", flags=re.S | re.I):
        rec: dict[str, str] = {}
        last = None
        for line in body.splitlines():
            m = pat.match(line)
            if m and m.group(1).lower() not in rec:
                last = m.group(1).lower()
                rec[last] = m.group(2).strip()
            elif last and (line.strip() or rec[last]):
                rec[last] += "\n" + line.rstrip()
        if rec:
            out.append({k: v.strip() for k, v in rec.items()})
    return out


def page_list(raw: str) -> list[int]:
    out: list[int] = []
    for a, b in re.findall(r"(\d+)(?:\s*(?:-|–|to)\s*(\d+))?", raw or ""):
        lo, hi = int(a), int(b or a)
        out += list(range(lo, min(hi, lo + 40) + 1)) if hi >= lo else [lo]
    return list(dict.fromkeys(out))


def _none(s: str) -> str:
    return "" if (s or "").strip().lower().strip(".") in ("none", "(none)", "n/a", "") else s.strip()


def parse_dossier(text: str) -> dict:
    results, defs = [], []
    for r in fields(text, "result", ("id", "kind", "statement", "author_words", "pages", "depends", "role")):
        if not r.get("statement", "").strip():
            continue
        rid = re.sub(r"[^A-Za-z0-9]", "", r.get("id", "")) or f"R{len(results) + 1}"
        if any(x["id"] == rid for x in results):
            rid = f"R{len(results) + 1}x"
        kind = next((k for k in RESULT_KINDS if k in r.get("kind", "").lower()), "proposition")
        role = next((k for k in ("main", "intermediate", "auxiliary") if k in r.get("role", "").lower()), "intermediate")
        results.append({"id": rid, "kind": kind, "statement": r["statement"].strip(), "words": _none(r.get("author_words", "")),
                        "pages": page_list(r.get("pages", "")),
                        "depends": re.findall(r"\b[A-Za-z]\d+\b", r.get("depends", "")), "role": role})
    for r in fields(text, "definition", ("id", "name", "text", "page")):
        if not (r.get("text") or r.get("name")):
            continue
        did = re.sub(r"[^A-Za-z0-9]", "", r.get("id", "")) or f"D{len(defs) + 1}"
        defs.append({"id": did, "name": r.get("name", "").strip(), "text": r.get("text", "").strip(),
                     "page": (page_list(r.get("page", "")) or [0])[0]})
    src = re.sub(r"[\[\]\s]", "", extract_tag(text, "source"))
    return {
        "problem": extract_tag(text, "problem").strip(), "source": _none(src), "setting": extract_tag(text, "setting").strip(),
        "results": results, "definitions": defs, "storyline": extract_tag(text, "storyline").strip(),
        "contribution": extract_tag(text, "contribution").strip(),
        "notation_choice": _none(extract_tag(text, "notation_choice")),
        "concerns": [c for c in parse_lines(extract_tag(text, "concerns")) if _none(c)],
    }


KIND_TEXT = {
    "article": "A research article. The main result is the author's own; say what is new and credit what is known.",
    "partial": "A paper on what the notes prove. What is not proved is stated as an open question or a conjecture, never as a result.",
    "expository": "An expository note. The results are known: nothing is claimed as new, and each known result is credited.",
}


def dossier_text(d: dict, limit: int = 12000) -> str:
    L = [f"PROBLEM\n{d.get('problem', '')}"]
    if d.get("plan_kind") in KIND_TEXT:
        L.append(f"THE AUTHOR'S PLAN\n{KIND_TEXT[d['plan_kind']]}"
                 + (f"\nLeft out by the author (do not present): " + "; ".join(d["left_out"]) if d.get("left_out") else "")
                 + (f"\nThe author's note: {d['plan_note']}" if d.get("plan_note") else ""))
    if d.get("source"):
        L.append(f"THE PROBLEM COMES FROM: [{d['source']}]")
    if d.get("setting"):
        L.append(f"SETTING AND NOTATION\n{d['setting']}")
    if d.get("definitions"):
        L.append("DEFINITIONS\n" + "\n".join(f"- {x['id']} ({x['name']}, page {x['page']}): {x['text']}" for x in d["definitions"]))
    if d.get("results"):
        L.append("RESULTS, in the order of presentation\n" + "\n".join(
            f"- {x['id']} [{x['kind']}, {x['role']}, pages {', '.join(map(str, x['pages'])) or '?'}"
            + (f", uses {', '.join(x['depends'])}" if x["depends"] else "")
            + {"conjecture": ", TO BE STATED AS A CONJECTURE", "credit": f", CREDITED TO [{x.get('credit_key', '')}]",
               "gap": ", its proof in the notes is incomplete", "unproved": ", THE NOTES GIVE NO PROOF"}.get(x.get("treatment", ""), "")
            + (f", ITS PROOF USES CONJECTURE {', '.join(x['conditional'])}: STATE IT AS CONDITIONAL ON IT" if x.get("conditional") else "")
            + f"]: {x['statement']}"
            + "".join(f"\n    (a step inside its proof, not a result of its own: {c['statement']})" for c in x.get("contains", []))
            for x in d["results"]))
    if d.get("storyline"):
        L.append(f"HOW THE ARGUMENT GOES\n{d['storyline']}")
    if d.get("contribution"):
        L.append(f"WHAT IS KNOWN AND WHAT THESE NOTES ADD\n{d['contribution']}")
    if d.get("notation_choice"):
        L.append(f"NOTATION CHOICE (use these forms everywhere)\n{d['notation_choice']}")
    text = "\n\n".join(L)
    if len(text) <= limit:
        return text
    # too long: the story and the context give way first; the results, and how each is to be presented, stay
    order = [i for i, part in enumerate(L) if part.startswith(("WHAT IS KNOWN", "HOW THE ARGUMENT", "SETTING", "DEFINITIONS"))]
    for i in order:
        over = len("\n\n".join(L)) - limit
        if over <= 0:
            break
        L[i] = fit(L[i], max(200, len(L[i]) - over), "head")
    return fit("\n\n".join(L), limit, "head")


def stage_understand(run: Run) -> None:
    """Everything is read together once, before any writing: the notes, the confirmed notation, the context
    brief and the notes on each paper read. The result (the dossier) travels with every later request."""
    p = run.p
    pages, _ = writing_pages(p)
    if not pages:
        raise ValueError("The transcript is empty.")
    conf = p.load("confirmed", {})
    brief = (p.load("brief") or {}).get("text", "")
    system = prompts.UNDERSTAND.format(field=run.field)
    out = 7000
    room = _room("reason", system, out)
    notation = notation_text(conf, max(800, min(3000, room // 8)))
    sources = fit(sources_text(p), max(600, room // 5), "head")
    brief = fit(brief, max(600, room // 8), "head")
    view_room = max(1500, room - len(notation) - len(sources) - len(brief) - 600)
    view = _view(pages, view_room)
    if len(view) < sum(len(pg["text"]) for pg in pages):
        run.log("Your notes are longer than the strongest model can read in one request, so only the opening of each page "
                "was read together. The results may be incomplete: check the list of results below.", "warn")
    user = (f"CONFIRMED NOTATION\n{notation}\n\nRESEARCH CONTEXT BRIEF\n{brief or '(none)'}\n\n"
            f"NOTES ON THE PAPERS READ (with citation KEYs)\n{sources or '(none)'}\n\nTRANSCRIPT OF THE NOTES\n\n{view}")
    run.progress(0, 1, "reading the whole of the notes together")
    d: dict = {}
    for attempt in range(2):
        res = run.ask("reason", system, user, max_out=out, effort="high",
                      label="Understand the whole paper" + (" (retry)" if attempt else ""))
        d = parse_dossier(res.text)
        d["model"] = res.model
        if d["problem"] and (d["results"] or d["definitions"]):
            break
    if not d.get("problem") and not d.get("results"):
        raise LLMError("The model's reading of the notes came back in a form that could not be used.",
                       kind="transient", retry_at=__import__("time").time() + 60)
    check_results(run, d, pages)
    keys = {x["key"] for x in p.load("library", [])}
    if d["source"] and d["source"] not in keys:
        near = difflib.get_close_matches(d["source"], list(keys), n=1, cutoff=0.82)
        d["source"] = near[0] if near else ""
    p.save("dossier", d)
    main = [x for x in d["results"] if x["role"] == "main"]
    run.progress(1, 1, "understood")
    run.log(f"Understood: {len(d['definitions'])} definitions and {len(d['results'])} results, {len(main)} of them main."
            + (f" {len(d['concerns'])} point(s) noted for your attention." if d["concerns"] else ""))


def _formulas_in_notes(statement: str, pages: list[dict], old: str = "") -> bool:
    """A completed statement may only add the author's own hypotheses: every formula in it occurs in the notes, it
    keeps every formula of the statement it replaces, and it keeps every word that decides what is asserted (all,
    some, not, at least, ...). Any formula delimiter counts."""
    from .pipeline import _MEANING_WORDS
    from .texutil import split_math
    notes = "\n".join(pg["text"] for pg in pages)
    flat = re.sub(r"\s+|\$", "", notes)
    for kind, seg in split_math(statement or ""):
        if kind == "text":
            continue
        core = re.sub(r"\s+", "", seg)
        if core and core not in flat and fidelity.audit([{"n": 0, "text": f"$${seg}$$"}], notes)["preserved"] < 1:
            return False                           # a formula the notes do not contain
    if old:
        if fidelity.audit([{"n": 0, "text": old}], statement)["preserved"] != fidelity.audit([{"n": 0, "text": old}], old)["preserved"]:
            return False
        words = lambda s: set(re.findall(r"[a-z]+", re.sub(r"\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", " ", s.lower())))
        if (words(old) & _MEANING_WORDS) - words(statement):
            return False
    return True


def apply_result_check(d: dict, rows: list[list[str]], pages: list[dict]) -> list[str]:
    """Carry out the check of the results: a step or a case of another result's proof stops being a result (its pages
    go to that result, whose proof contains it), and a statement that left out the hypotheses in force is completed
    from the author's own formulas. Returns what was changed, in words."""
    ids = {x["id"] for x in d["results"]}
    done: list[str] = []
    merged: dict[str, str] = {}
    for row in rows:
        rid = re.sub(r"[^A-Za-z0-9]", "", row[0])
        verdict = row[1].strip().lower() if len(row) > 1 else ""
        rest = row[2].strip() if len(row) > 2 else ""
        if rid not in ids:
            continue
        m = re.match(r"part\s+of\s+(?:the\s+proof\s+of\s+)?([A-Za-z]\d+\w*)", row[1].strip() if len(row) > 1 else "", re.I)
        parent = next((i for i in ids if m and i.lower() == m.group(1).lower()), None)
        if parent and parent != rid and parent not in merged:
            merged[rid] = parent
        elif verdict.startswith("incomplete") and _none(rest) and \
                _formulas_in_notes(rest, pages, next(x for x in d["results"] if x["id"] == rid)["statement"]):
            r = next(x for x in d["results"] if x["id"] == rid)
            if rest != r["statement"]:
                r["statement_before_check"], r["statement"] = r["statement"], rest
                done.append(f"{rid}: the statement now includes the hypotheses in force in the notes")
        elif verdict.startswith("incomplete"):
            d.setdefault("concerns", []).append(f"The statement of {rid} may leave out hypotheses that are in force where the notes state it.")
    def home(rid: str) -> str:
        seen = set()
        while rid in merged and rid not in seen:
            seen.add(rid)
            rid = merged[rid]
        return rid
    if merged:
        by_id = {x["id"]: x for x in d["results"]}
        for rid in merged:
            parent = by_id[home(rid)]
            parent["pages"] = sorted(set(parent["pages"]) | set(by_id[rid]["pages"]))
            parent["depends"] = parent["depends"] + by_id[rid]["depends"]      # what the step used, the proof uses
            parent.setdefault("contains", []).append({"id": rid, "statement": by_id[rid]["statement"]})
            done.append(f"{rid} is a step in the proof of {parent['id']}, not a result of its own")
        d["results"] = [x for x in d["results"] if x["id"] not in merged]
        for x in d["results"]:
            x["depends"] = list(dict.fromkeys(home(i) for i in x["depends"] if home(i) != x["id"]))
        d["storyline"] = re.sub(r"\b(" + "|".join(map(re.escape, merged)) + r")\b", lambda mm: home(mm.group(1)), d.get("storyline", ""))
    d["checked"] = done
    return done


def check_rows(text: str) -> list[list[str]]:
    """'R3 || incomplete || statement': three fields at most, so that a norm written ||f|| stays in the statement."""
    return [[x.strip() for x in line.split("||", 2)] for line in (text or "").splitlines()
            if re.match(r"\s*[A-Za-z]\d+\w*\s*\|\|", line)]


def check_results(run: Run, d: dict, pages: list[dict]) -> None:
    """A second, independent reading of one question only: is each listed result really a result of the notes, or a
    step of an argument that the first reading cut out and made into a lemma? A step lifted out of its proof loses
    the assumptions it depends on and becomes false; this is where that is prevented."""
    if len(d.get("results") or []) < 2:
        return
    system = prompts.UNDERSTAND_CHECK
    listing = "\n\n".join(f"{x['id']} [{x['kind']}, pages {', '.join(map(str, x['pages'])) or '?'}]\nStatement: {x['statement']}"
                            + (f"\nAuthor's words: {x['words']}" if x.get("words") else "") for x in d["results"])
    room = _room("reason", system, 1500)
    user = f"RESULTS AS LISTED\n{listing}\n\nTRANSCRIPT OF THE NOTES\n\n{_view(pages, max(1500, room - len(listing) - 300))}"
    run.progress(0, 1, "checking that every result is one of yours, not a step of a proof")
    res = run.ask("reason", system, user, max_out=1500, effort="high", label="Check the results against the notes")
    done = apply_result_check(d, check_rows(res.text), pages)
    for line in done:
        run.log(line + ".")


# ------------------------------------------------------- 1b. assessment and the author's plan

PROOF_STATES = ("complete", "gap", "sketch", "breaks off", "none", "unknown")
KINDS = ("article", "partial", "expository", "not_ready")


def parse_assessment(text: str, d: dict, keys: set[str]) -> dict:
    """The assessment as data. A result said to be known must name a paper that was actually found; otherwise the
    claim is not kept, because a wrong "this is known" would make the author give up a result."""
    ids = {x["id"] for x in d.get("results", [])}
    results: dict[str, dict] = {}
    for r in fields(text, "result", ("id", "proof", "where", "known", "evidence")):
        rid = re.sub(r"[^A-Za-z0-9]", "", r.get("id", ""))
        if rid not in ids:
            continue
        proof = _proof_state(r.get("proof") or "")
        known_raw = (r.get("known") or "").strip()
        key = next((k for k in sorted(keys, key=len, reverse=True)
                    if k and re.search(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", known_raw)), "")
        kn = "new"
        if key and re.match(r"\s*special", known_raw, re.I):
            kn = "special"
        elif key and re.match(r"\s*known", known_raw, re.I):
            kn = "known"
        results[rid] = {"proof": proof, "where": _none(r.get("where", "")), "known": kn, "key": key if kn != "new" else "",
                        "evidence": _none(r.get("evidence", "")) if kn != "new" else ""}
    for rid in ids:                                # a result the assessment did not answer for is not assumed proved
        results.setdefault(rid, {"proof": "unknown", "where": "The assessment gave no answer for this result.", "known": "new",
                                 "key": "", "evidence": ""})
    m = re.match(r"\s*(not\s+established|partly|established)\b", extract_tag(text, "main").lower())
    main = re.sub(r"\s+", " ", m.group(1)) if m else "unknown"
    m = re.match(r"\s*(not[_\s-]?ready|expository|partial|article)\b", extract_tag(text, "recommend").lower())
    rec = re.sub(r"[_\s-]+", "_", m.group(1)) if m else ""
    return {"results": results, "main": main, "main_note": _none(extract_tag(text, "main_note")),
            "unfinished": [x for x in parse_lines(extract_tag(text, "unfinished")) if _none(x)],
            "recommend": rec, "recommend_note": _none(extract_tag(text, "recommend_note"))}


def _proof_state(raw: str) -> str:
    """The first words of the answer decide; anything else (a hedge, a cut-off reply) is 'unknown', never 'complete'."""
    t = raw.strip().lower()
    if re.match(r"(incomplete|not\s+complete|partial)", t):
        return "gap"
    m = re.match(r"(complete|gap|sketch|breaks?\s+off|none|no\s+proof)\b", t)
    if not m:
        return "unknown"
    v = re.sub(r"\s+", " ", m.group(1))
    return {"break off": "breaks off", "no proof": "none"}.get(v, v)


def assessment_is_clean(a: dict) -> bool:
    """Nothing for the author to decide: every result proved in the notes, none found in the literature, the main
    claim established, nothing left open, and an ordinary research article recommended."""
    return bool(a) and a.get("recommend") == "article" and a.get("main") == "established" and not a.get("unfinished") \
        and all(r["proof"] == "complete" and r["known"] == "new" for r in (a.get("results") or {}).values())


def stage_assess(run: Run) -> None:
    """Before anything is written: how much of a paper the notes actually support. A research article is the usual
    case, but notes can also repeat known results, leave proofs unfinished, or stop before the main claim. The
    author is told which, with the evidence, and decides what kind of paper to write."""
    p = run.p
    d = p.load("dossier") or {}
    pages, _ = writing_pages(p)
    system = prompts.ASSESS.format(field=run.field)
    out = min(8000, 900 + 260 * len(d.get("results", [])))
    room = _room("reason", system, out)
    dos = dossier_text(d, max(1500, room // 4))
    sources = fit(sources_text(p), max(800, room // 4), "head")
    user = (f"DOSSIER\n{dos}\n\nREADING NOTES ON THE RELATED PAPERS (with citation KEYs)\n{sources or '(none were read)'}\n\n"
            f"TRANSCRIPT OF THE NOTES\n\n{_view(pages, max(1500, room - len(dos) - len(sources) - 500))}")
    run.progress(0, 1, "assessing what the notes prove and what is already known")
    keys = {x["key"] for x in p.load("library", [])}
    for attempt in range(2):
        res = run.ask("reason", system, user, max_out=out, effort="high", label="Assess the notes" + (" (retry)" if attempt else ""))
        a = parse_assessment(res.text, d, keys)
        if a["recommend"] and a["main"] != "unknown" and not any(r["proof"] == "unknown" for r in a["results"].values()):
            break                                  # a complete answer; otherwise ask once more, then show what came back
    a["model"] = res.model
    p.save("assessment", a)
    run.progress(1, 1, "assessed")
    bad = [rid for rid, r in a["results"].items() if r["proof"] != "complete"]
    known = [rid for rid, r in a["results"].items() if r["known"] != "new"]
    run.log(f"Assessment: main result {a['main']}; {len(bad)} result(s) with an incomplete or missing proof; "
            f"{len(known)} found in the literature. Suggested: {a['recommend'].replace('_', ' ')}.",
            "info" if assessment_is_clean(a) else "warn")


def default_scope(a: dict) -> dict:
    """What the app proposes before the author has said anything: the recommended kind, a conjecture for a result
    the notes do not prove, credit for a known result, and every other result kept as it is."""
    res = {}
    for rid, r in (a.get("results") or {}).items():
        if r["known"] != "new":
            res[rid] = "credit"
        elif r["proof"] in ("none", "unknown") or (r["proof"] in ("sketch", "breaks off") and a.get("recommend") != "article"):
            res[rid] = "conjecture"
        else:
            res[rid] = "keep"
    kind = a.get("recommend") or "article"
    return {"kind": kind if kind in KINDS else "article", "results": res, "note": ""}


def submit_scope(p: Project, data: dict) -> dict:
    """Gate: the author's plan for the paper. 'not_ready' stops here on purpose: the notes are not enough yet."""
    a = p.load("assessment") or {}
    base = default_scope(a)
    kind = data.get("kind") if data.get("kind") in KINDS else base["kind"]
    allowed = {"keep", "conjecture", "omit", "credit", "new"}
    given = data.get("results") or {}
    results = {rid: (given.get(rid) if given.get(rid) in allowed else v) for rid, v in base["results"].items()}
    if kind != "not_ready":
        d = p.load("dossier") or {}
        gone = {rid for rid, v in results.items() if v == "omit"}
        for x in d.get("results", []):
            used = [i for i in x.get("depends", []) if i in gone]
            if x["id"] not in gone and used:
                raise ValueError(f"{x['id']} uses {', '.join(used)} in its proof. Leave {x['id']} out as well, "
                                 f"or keep {', '.join(used)}.")
    scope = {"kind": kind, "results": results, "note": str(data.get("note") or "").strip()[:1500], "at": time.time()}
    p.save("scope", scope)
    if kind == "not_ready":
        p.set_stage("scope", status="waiting", since=time.time(), stopped=True)
        emit(p.id, "log", stage="scope", level="warn",
             message="Stopped here, as you chose. Complete the notes and start a new paper with them; nothing more is written for this one "
                     "unless you choose another kind of paper.")
        return scope
    p.set_stage("scope", status="done", finished=time.time(), stopped=False)
    emit(p.id, "log", stage="scope", level="done", message=f"Your plan: {KIND_TEXT.get(kind, kind)}")
    return scope


def settle_scope_if_clean(p: Project) -> bool:
    """The plan step is passed without stopping when the assessment found nothing to decide."""
    a = p.load("assessment") or {}
    prev = p.load("scope") or {}
    if (prev and not prev.get("auto")) or not assessment_is_clean(a):
        return False                               # the author's own plan is never replaced by an automatic one
    p.save("scope", {**default_scope(a), "at": time.time(), "auto": True})
    return True


def planned_dossier(p: Project) -> dict:
    """The dossier as the author's plan makes it: results left out are gone, and the others say how they are to be
    presented. Every writing step works from this."""
    d = json.loads(json.dumps(p.load("dossier") or {}))
    scope = p.load("scope") or {}
    if not scope:
        return d
    a = p.load("assessment") or {}
    d["plan_kind"] = scope.get("kind", "article")
    d["plan_note"] = scope.get("note", "")
    treat = scope.get("results") or {}
    gone = {rid for rid, v in treat.items() if v == "omit"}
    d["left_out"] = [x["statement"] for x in d.get("results", []) if x["id"] in gone]
    keep = []
    for x in d.get("results", []):
        if x["id"] in gone:
            continue
        info = (a.get("results") or {}).get(x["id"], {})
        t = treat.get(x["id"], "keep")
        if t == "conjecture":
            x["treatment"] = "conjecture"
        elif t == "credit" and info.get("key"):
            x["treatment"], x["credit_key"] = "credit", info["key"]
        elif info.get("proof") in ("gap", "sketch", "breaks off") and t in ("keep", "new"):
            x["treatment"] = "gap"
        elif info.get("proof") in ("none", "unknown") and t in ("keep", "new"):
            x["treatment"] = "unproved"
        keep.append(x)
    conj = {x["id"] for x in keep if x.get("treatment") == "conjecture"}
    by_id = {x["id"]: x for x in keep}

    def rests_on(rid: str, seen: set) -> set:
        out = set()
        for i in by_id.get(rid, {}).get("depends", []):
            if i in seen:
                continue
            seen.add(i)
            out |= ({i} if i in conj else set()) | rests_on(i, seen)
        return out
    for x in keep:                                 # a proof that uses a conjecture proves only a conditional result
        if x["id"] not in conj:
            c = sorted(rests_on(x["id"], {x["id"]}))
            if c:
                x["conditional"] = c
    d["results"] = keep
    return d


def left_out_text(p: Project) -> str:
    """What the author left out, so that its formulas missing from the paper are counted as left out on purpose:
    the statements, and the pages that belong to no result the paper keeps."""
    d = p.load("dossier") or {}
    pd = planned_dossier(p)
    if not pd.get("left_out"):
        return ""
    kept = {n for x in pd.get("results", []) for n in x.get("pages", [])} | {x.get("page") for x in d.get("definitions", [])}
    gone_pages = {n for x in d.get("results", []) if x["statement"] in pd["left_out"] for n in x.get("pages", [])} - kept
    pages, _ = writing_pages(p)
    return "\n".join(pd["left_out"] + [pg["text"] for pg in pages if pg["n"] in gone_pages])


# ------------------------------------------------------- 2. introduction

def stage_intro(run: Run) -> None:
    p = run.p
    d = planned_dossier(p)
    conf = p.load("confirmed", {})
    brief = (p.load("brief") or {}).get("text", "")
    library = {x["key"]: x for x in p.load("library", [])}
    system = prompts.INTRO.format()
    room = _room("writer", system, 1900)
    sources = sources_text(p)
    user = (f"FIELD\n{run.field}\n\nDOSSIER\n{dossier_text(d, max(1500, int(room * 0.4)))}\n\n"
            f"RESEARCH CONTEXT BRIEF\n{fit(brief, max(500, int(room * 0.15)), 'head') or '(none)'}\n\n"
            f"PAPERS YOU MAY CITE\n{fit(sources, max(600, int(room * 0.3)), 'head') or '(none: write the introduction without citations)'}\n\n"
            f"CONFIRMED NOTATION\n{notation_text(conf, max(500, int(room * 0.1)))}")
    run.progress(0, 1, "writing the introduction")
    intro, notes, model = "", [], ""
    for attempt in range(2):
        res = run.ask("writer", system, user, max_out=1900, effort="high", temperature=0.3,
                      label="Write introduction" + (" (retry)" if attempt else ""))
        notes = []
        intro = clean_fragment(res.text)
        intro = re.sub(r"\\section\*?\{[^{}]*\}\s*", "", intro)
        intro = re.sub(r"\\begin\{(abstract)\}.*?\\end\{\1\}", "", intro, flags=re.S).strip()
        intro = fix_keys(drop_unsourced(intro, library, notes), library, notes)
        model = res.model
        if len(intro) >= 500:
            break
    if len(intro) < 200:
        raise LLMError("The introduction came back empty.", kind="transient", retry_at=__import__("time").time() + 60)
    for n in notes:
        run.log(n, "warn")
    p.save("intro", {"text": intro, "notes": notes, "model": model})
    run.progress(1, 1, "introduction written")
    run.log(f"Introduction written ({len(intro.split())} words, {len(cited_keys([intro]))} papers cited).")


# ------------------------------------------- 3. structure and first draft

def normalise_plan(raw: list[dict], pages: list[dict], d: dict) -> list[dict]:
    """Make the plan safe to write from: every page belongs to exactly one section, and every definition and
    result is presented in a section that owns pages it is derived on."""
    page_ns = [pg["n"] for pg in pages]
    secs: list[dict] = []
    for r in raw:
        title = re.sub(r"^\s*(?:section\s*)?\d+[.:)]?\s*", "", r.get("title", ""), flags=re.I).strip().rstrip(".")
        if not title or title.lower() in ("introduction", "conclusion", "references", "abstract"):
            continue
        nums = page_list(r.get("from_page", ""))
        fp = next((n for n in page_ns if nums and n >= nums[0]), None) if nums else None
        if fp is None or any(s["from_page"] == fp for s in secs):
            continue
        secs.append({"title": title, "from_page": fp, "purpose": re.sub(r"\s+", " ", r.get("purpose", "")).strip(),
                     "planned": re.findall(r"\b[A-Za-z]\d+\b", r.get("items", ""))})
    if not secs:
        secs = [{"title": "Main results", "from_page": page_ns[0], "purpose": "", "planned": []}]
    min(secs, key=lambda s: s["from_page"])["from_page"] = page_ns[0]
    # a section never starts in the middle of an argument: where a result is derived over several pages, those
    # pages stay in one section, so that one writer sees the whole of it
    ranges = []
    for x in d.get("results", []):
        ps = [n for n in x.get("pages") or [] if n in page_ns]
        if ps and len([n for n in page_ns if min(ps) <= n <= max(ps)]) <= max(4, len(page_ns) // 2):
            ranges.append((min(ps), max(ps)))         # a range over most of the notes says "uses everything", not one argument
    first_start = page_ns[0]
    moved = True
    while moved:
        moved = False
        by_page = sorted(secs, key=lambda s: s["from_page"])
        for i, s in enumerate(by_page):
            cut = None if s["from_page"] == first_start else next(((a, b) for a, b in ranges if a < s["from_page"] <= b), None)
            if cut:
                nxt = next((n for n in page_ns if n > cut[1]), None)
                if nxt is None or any(t["from_page"] == nxt for t in secs if t is not s):
                    if i:                              # nothing left to start on: the section joins the one before
                        by_page[i - 1]["planned"] += s["planned"]
                    secs.remove(s)
                else:
                    s["from_page"] = nxt
                moved = True
                break
    starts = sorted(s["from_page"] for s in secs)
    for s in secs:
        later = [x for x in starts if x > s["from_page"]]
        s["pages"] = [n for n in page_ns if n >= s["from_page"] and (not later or n < later[0])]
        s["items"] = []
    everything = [(x["id"], [x["page"]] if x.get("page") else []) for x in d.get("definitions", [])] + \
                 [(x["id"], x.get("pages") or []) for x in d.get("results", [])]
    for iid, where in everything:
        planned = next((s for s in secs if iid in s["planned"]), None)
        if planned and (not where or set(where) & set(planned["pages"])):
            home = planned                         # the plan's choice stands when the item's pages reach that section
        else:
            home = next((s for s in secs if where and where[0] in s["pages"]), None) or planned or secs[-1]
        home["items"].append(iid)
    for s in secs:                                # the planned order first, then anything the plan forgot
        s["items"] = [i for i in s["planned"] if i in s["items"]] + [i for i in s["items"] if i not in s["planned"]]
        s["items"] = list(dict.fromkeys(s["items"]))
        del s["planned"]
    return secs


def plan_text(secs: list[dict]) -> str:
    return "1. Introduction (already written)\n" + "\n".join(
        f"{i}. {s['title']}: presents {', '.join(s['items']) or 'no numbered items'} "
        f"(notes pages {s['pages'][0]}-{s['pages'][-1]})" + (f". {s['purpose']}" if s.get("purpose") else "")
        for i, s in enumerate(secs, 2))


def organisation(text: str, secs: list[dict]) -> str:
    text = clean_fragment(text or "")
    nums = [int(n) for n in re.findall(r"Section[~\s]+(\d+)", text)]
    if text and nums and max(nums) <= len(secs) + 1 and len(text) < 1500:
        return text
    parts = [f"Section~{i} ({s['title']})" for i, s in enumerate(secs, 2)]
    if len(parts) == 1:
        return f"The rest of the paper consists of {parts[0]}."
    return "The paper is organised as follows: " + ", ".join(parts[:-1]) + " and " + parts[-1] + "."


_SEC_LINE = re.compile(r"(?m)^[ \t]*\\section\*?\s*\{")


def _prose_words(tex: str) -> list[str]:
    return [w.lower() for kind, part in split_math(tex or "") if kind == "text" for w in re.findall(r"[A-Za-z]{2,}", part)]


def repair_is_faithful(old: str, new: str) -> bool:
    """A repair may put formulas back; it may not rewrite the text around them. The words outside the
    mathematics must stay almost the same, the length too, and no reasoning may have crept in."""
    if not new or leaks(new) or len(new) < 0.6 * len(old) or len(new) > 1.35 * len(old) + 400:
        return False
    a, b = _prose_words(old), _prose_words(new)
    if not a:
        return True
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio() >= 0.85


def shape_fragment(frag: str, title: str, first: bool, figs: dict, unit_pages: list[dict]) -> str:
    """Hold one written part to its place in the paper: one section heading, only known figures, each once."""
    frag = clean_fragment(frag)
    frag = re.sub(r"(?m)^[ \t]*\\section\*?\s*\{\s*Introduction\s*\}.*$", "", frag)
    m = _SEC_LINE.search(frag)
    if m:                                         # the heading is ours: the planned title, once
        grp = find_group(frag, m.end() - 1)
        rest = frag[grp[1]:] if grp else frag[m.end():]
        frag = frag[:m.start()] + re.sub(r"^\s*\\label\{[^{}]*\}", "", rest)
    frag = _SEC_LINE.sub(lambda _: "\\subsection{", frag).strip()
    if first:
        frag = f"\\section{{{title}}}\n\n" + frag
    # figures: unknown or dropped ids go; a kept figure of these pages that the writer forgot is put back
    seen: set[str] = set()

    def keep(mm: re.Match) -> str:
        fid = mm.group(1)
        if fid not in figs or fid in seen:
            return ""
        seen.add(fid)
        return mm.group(0)
    frag = paperlib._FIG_RE.sub(keep, frag)
    for pg in unit_pages:
        for _, _, _, fid in find_figures(pg["text"]):
            if not fid or fid not in figs or fid in seen:
                continue
            seen.add(fid)
            here = re.search(rf"(?m)^%+\s*page\s+{pg['n']}\s*$", frag)
            nxt = re.search(r"(?m)^%+\s*page\s+\d+\s*$", frag[here.end():]) if here else None
            if nxt and not paperlib.open_environments(frag[:here.end() + nxt.start()]):
                at = here.end() + nxt.start()
                frag = frag[:at] + f"\\insertfigure{{{fid}}}\n\n" + frag[at:]
            else:
                frag += f"\n\n\\insertfigure{{{fid}}}"
    return re.sub(r"\[FIGURE\s+f\d+\s*:[^\]]*\]", "", frag).strip()


def skeleton(text: str) -> dict:
    """Everything in a passage that an edit of the wording must leave alone."""
    def bag(pat: str) -> list[str]:
        return sorted(re.findall(pat, text or ""))
    return {"labels": bag(r"\\label\{[^{}]+\}"), "figures": bag(r"\\insertfigure\{[^{}]+\}"),
            "envs": bag(r"\\begin\{[a-zA-Z]+\*?\}"), "refs": bag(r"\\(?:eq)?ref\{[^{}]+\}"),
            "cites": sorted(cited_keys([text or ""])), "sections": bag(r"\\(?:sub)*section\*?\{")}


def wording_only(old: str, new: str) -> bool:
    """True when `new` differs from `old` in wording alone: same labels, references, citations, figures and
    environments, no new syntax problem, and a length that rules out a deleted or invented passage."""
    if not new or not (0.75 * len(old) <= len(new) <= 1.35 * len(old)):
        return False
    a, b = skeleton(old), skeleton(new)
    if any(a[k] != b[k] for k in ("labels", "figures", "envs", "sections")):
        return False
    if set(a["cites"]) != set(b["cites"]) or set(a["refs"]) != set(b["refs"]):
        return False
    return len(check_balance(new)) <= len(check_balance(old))


def ensure_labels(blocks: list[dict]) -> None:
    """Every stated result gets a label, so that later steps can point at it."""
    used = {b.get("label") for b in blocks if b.get("label")}
    n = 0
    for b in blocks:
        if b["type"] == "env" and b.get("env") in RESULT_KINDS + ("claim",) and not b.get("label"):
            while True:
                n += 1
                if f"res:U{n}" not in used:
                    break
            b["label"] = f"res:U{n}"
            used.add(b["label"])


UNIT_CHARS = 6000          # notes written in one answer at most: longer parts are often ended early by the model


def stops_short(apages: list[dict], frag: str, omitted: str) -> int | None:
    """The first page of a written part whose material did not come out: its formulas are missing, or, on pages
    with little mathematics, the text is far shorter than the notes and the page's line is absent."""
    rep = fidelity.audit(apages, frag, omitted)
    marked = page_marks(frag)
    gaps = page_gaps(apages, rep, marked or None)
    if gaps:
        return gaps[0]
    size = sum(len(pg["text"]) for pg in apages)
    if not omitted and marked and size >= 1500 and len(frag) < 0.3 * size:
        absent = [pg["n"] for pg in apages if pg["n"] not in marked]
        return absent[0] if absent else None
    return None


def cut_point(raw: str, nums: list[int], k: int) -> tuple[int, int]:
    """Where to cut a part that stopped short at page nums[k]: just before that page's line, or before the line of
    the last page that has one. (0, 0) when nothing before it can be kept."""
    marks: dict[int, int] = {}
    for m in _PAGE_MARK.finditer(raw or ""):
        marks.setdefault(int(m.group(1)), m.start())
    while k > 0 and nums[k] not in marks:
        k -= 1
    if k == 0 or len((raw or "")[:marks[nums[k]]].strip()) < 80:
        return 0, 0
    return marks[nums[k]], k


def stage_structure(run: Run) -> None:
    p = run.p
    pages, figs = writing_pages(p)
    if not pages:
        raise ValueError("The transcript is empty.")
    d = planned_dossier(p)
    intro = (p.load("intro") or {}).get("text", "")
    conf = p.load("confirmed", {})
    by_n = {pg["n"]: pg for pg in pages}
    full = "\n\n".join(f"%%% page {pg['n']}\n{pg['text']}" for pg in pages)
    sig = hashlib.sha1((full + json.dumps(d, sort_keys=True) + intro + json.dumps(figs, sort_keys=True))
                       .encode("utf-8")).hexdigest()[:16]
    partial = p.load("structure_partial") or {}
    if partial.get("sig") != sig:
        partial = {"sig": sig, "plan_raw": "", "frags": []}

    # a. the order of sections, definitions, lemmas and theorems
    run.progress(0, 3, "deciding the order of sections and results")
    if not partial["plan_raw"]:
        system = prompts.PLAN.format()
        room = _room("reason", system, 1500)
        dos = dossier_text(d, max(1500, int(room * 0.35)))
        user = (f"DOSSIER\n{dos}\n\nINTRODUCTION (already written)\n{fit(intro, max(800, int(room * 0.15)), 'head')}\n\n"
                f"TRANSCRIPT OF THE NOTES\n\n{_view(pages, max(1500, room - len(dos) - int(room * 0.15) - 400))}")
        partial["plan_raw"] = run.ask("reason", system, user, max_out=1500, effort="high", label="Decide paper structure").text
        p.save("structure_partial", partial)
    secs = normalise_plan(fields(partial["plan_raw"], "section", ("title", "items", "from_page", "purpose")), pages, d)
    org = organisation(extract_tag(partial["plan_raw"], "organisation"), secs)
    p.save("plan", {"sections": secs, "organisation": org})
    ptext = plan_text(secs)
    run.log("Structure: " + "; ".join(f"{i}. {s['title']}" for i, s in enumerate(secs, 2)) + ".")

    # b. each section written on its own, with the dossier, the plan and the introduction in front of the writer.
    # A part holds at most UNIT_CHARS of notes: models do not reliably write out more in one answer. Every part is
    # compared with its pages as soon as it is written; one that stops short is written again from where it stopped.
    system = prompts.WRITE_SECTION.format()
    budget_chars = router.budget("writer") * 3 - len(system) - 1500
    fixed_room = max(2500, min(60000, int(budget_chars * 0.35)))
    chunk_chars = max(2000, min(UNIT_CHARS, int((budget_chars - fixed_room) / 2.9)))
    whole_room = budget_chars - fixed_room - int(chunk_chars * 2.9)   # the whole notes go along when they fit
    dos = dossier_text(d, int(fixed_room * 0.45))
    notation = notation_text(conf, int(fixed_room * 0.12))
    cit = fit("\n".join(f"- {x['key']}: {x['title']} ({x.get('year')}). Supports: "
                        + " | ".join(str(f) for f in ((x.get('notes') or {}).get('citable') or (x.get('notes') or {}).get('results') or [])[:3])
                        for x in citable(p)) or "(none)", int(fixed_room * 0.2), "head")
    intro_view = fit(intro, int(fixed_room * 0.2), "head")
    apages = audit_pages(pages)
    by_a = {pg["n"]: pg for pg in apages}
    omitted = left_out_text(p)                    # statements the author left out are missing on purpose

    def group(sec: int, plist: list[dict], limit: int, tries: int = 0) -> list[dict]:
        groups: list[list[dict]] = [[]]
        size = 0
        for pg in plist:
            if groups[-1] and size + len(pg["text"]) > limit:
                groups.append([])
                size = 0
            groups[-1].append(pg)
            size += len(pg["text"])
        return [{"sec": sec, "pages": [pg["n"] for pg in g], "tries": tries} for g in groups if g]

    layout = partial.get("layout") or []
    if layout and all(n in by_n for u in layout for n in u["pages"]):
        units = [dict(u) for u in layout]          # written before an interruption
        frags: list[str | None] = (list(partial.get("frags") or []) + [None] * len(units))[:len(units)]
    else:
        units = [u for si, s in enumerate(secs) for u in group(si, [by_n[n] for n in s["pages"]], chunk_chars)]
        frags = [None] * len(units)

    def renumber() -> None:
        for si in range(len(secs)):
            mine = [u for u in units if u["sec"] == si]
            for k, u in enumerate(mine):
                u["part"], u["parts"] = k, len(mine)

    def save() -> None:
        partial.update(layout=units, frags=frags)
        p.save("structure_partial", partial)

    def span(nums: list[int]) -> str:
        return f"{nums[0]}" if len(nums) == 1 else f"{nums[0]}-{nums[-1]}"

    def ask_unit(ui: int) -> str:
        u = units[ui]
        s = secs[u["sec"]]
        upages = [by_n[n] for n in u["pages"]]
        chunk = "\n\n".join(f"%%% page {pg['n']}\n{pg['text']}" for pg in upages)
        mine = [fid for pg in upages for _, _, _, fid in find_figures(pg["text"]) if fid]
        what = f"Section {u['sec'] + 2}, \"{s['title']}\". Items to present, in this order: {', '.join(s['items']) or '(none numbered)'}."
        cont = ""
        before = [f for f in frags[:ui] if f]
        if not u["part"] and before:
            cont = f"END OF THE PREVIOUS SECTION (written before; do not repeat it)\n{before[-1][-1200:]}\n\n"
        if u["part"]:
            so_far = "\n".join(f for f in frags[ui - u["part"]:ui] if f)
            open_now = paperlib.open_environments(so_far)
            what += (f" This is part {u['part'] + 1} of {u['parts']} of the section: the heading and the earlier part are "
                     f"already written. Continue from where they end.")
            cont = (f"CURRENTLY OPEN: {', '.join(open_now) if open_now else 'nothing'}\n\n"
                    f"END OF WHAT IS ALREADY WRITTEN\n{so_far[-900:]}\n\n")
        elif u["parts"] > 1:
            what += f" This is part 1 of {u['parts']}: write the heading and the material of the pages given; the rest follows later."
        mine_ns = set(u["pages"])
        if len(full) <= whole_room:
            around = f"THE WHOLE OF THE NOTES (for context only; the pages of this section are given again below)\n\n{full}\n\n"
        else:                                      # too long for one request: the pages just before and after
            prev = [pg for pg in pages if pg["n"] < min(mine_ns)][-1:]
            nxt = [pg for pg in pages if pg["n"] > max(mine_ns)][:1]
            around = "".join(f"{label} (for context only)\n%%% page {pg['n']}\n{fit(pg['text'], 1500, end)}\n\n"
                             for label, end, lst in (("THE PAGE BEFORE", "tail", prev), ("THE PAGE AFTER", "head", nxt)) for pg in lst)
        user = (f"DOSSIER\n{dos}\n\nPLAN OF THE PAPER\n{ptext}\n\nINTRODUCTION (already written; do not repeat it)\n{intro_view}\n\n"
                f"{around}"
                f"CONFIRMED NOTATION\n{notation}\n\nCITABLE\n{cit}\n\n"
                f"FIGURES ON THESE PAGES\n" + ("\n".join(f"- {fid}: {figs[fid]['caption']}" for fid in mine if fid in figs) or "(none)")
                + f"\n\nWRITE THIS SECTION\n{what}\n\n{cont}"
                f"NOTES FOR THIS SECTION (pages {u['pages'][0]} to {u['pages'][-1]})\n\n{chunk}")
        return run.ask("writer", system, user, max_out=int(est_tokens(chunk) * 1.8) + 1500, effort="high", temperature=0.2,
                       label=f"Write section {u['sec'] + 2}" + (f", part {u['part'] + 1}" if u["parts"] > 1 else "")
                       + f" (pages {span(u['pages'])})").text

    def write_pending() -> None:
        ui = 0
        while ui < len(units):
            if frags[ui] is not None:
                ui += 1
                continue
            renumber()
            u = units[ui]
            s = secs[u["sec"]]
            upages = [by_n[n] for n in u["pages"]]
            run.progress(1, 3, f"writing section {u['sec'] + 2} of {len(secs) + 1}: {s['title']}"
                         + (f" (part {u['part'] + 1} of {u['parts']})" if u["parts"] > 1 else ""))
            raw = ask_unit(ui)
            first = u["part"] == 0
            shaped = shape_fragment(raw, s["title"], first, figs, upages)
            stop = None if u.get("tries", 0) >= 2 else stops_short([by_a[n] for n in u["pages"]], shaped, omitted)
            if stop is None:
                frags[ui] = shaped
                ui += 1
                save()
                continue
            k = cut_point(raw, u["pages"], u["pages"].index(stop))
            rest = upages[k[1]:]
            tries = u.get("tries", 0) + 1
            if k[1] > 0:                           # keep what was written, write the rest again in smaller parts
                units[ui] = {**u, "pages": u["pages"][:k[1]]}
                frags[ui] = shape_fragment(raw[:k[0]], s["title"], first, figs, upages[:k[1]])
                new = group(u["sec"], rest, max(1500, chunk_chars // 2), tries)
                units[ui + 1:ui + 1] = new
                frags[ui + 1:ui + 1] = [None] * len(new)
                ui += 1
            elif len(rest) > 1:                    # nothing usable: the same pages, in two smaller parts
                new = group(u["sec"], rest, sum(len(pg["text"]) for pg in rest) // 2 + 1, tries)
                units[ui:ui + 1] = new
                frags[ui:ui + 1] = [None] * len(new)
            else:
                units[ui] = {**u, "tries": tries}
            run.log(f"The writing of pages {span(u['pages'])} stopped short: the material of page {stop} did not come out. "
                    f"Pages {span([pg['n'] for pg in rest])} are written again" + (" in smaller parts." if len(rest) > 1 else "."), "warn")
            save()

    in_units = {n for u in units for n in u["pages"]}
    for attempt in (0, 1):
        write_pending()
        renumber()
        # c. every formula of the notes must be in the text: compare, and repair where one is missing or changed
        run.progress(2, 3, "comparing every formula with the notes")
        report = fidelity.audit(apages, "\n".join(frags), omitted)
        problems = report["missing"] + report["altered"]
        todo = [ui for ui, u in enumerate(units) if not u.get("repaired") and any(x["page"] in u["pages"] for x in problems)]
        if todo:
            run.log(f"Audit: {len(problems)} formulas missing or altered after writing. Repairing.")
        sys_r = prompts.REPAIR.format()
        for ui in todo:
            u = units[ui]
            u["repaired"] = True
            mine = [x for x in problems if x["page"] in u["pages"]]
            listing = "\n".join(f"- (page {x['page']}) {x['source']}" + (f"   [currently typeset as: {x['found']}]" if x.get("found") else "")
                                for x in mine[:30])
            chunk = "\n\n".join(f"%%% page {n}\n{by_n[n]['text']}" for n in u["pages"])
            user = (f"FORMULAS TO RESTORE EXACTLY\n{listing}\n\nORIGINAL TRANSCRIPT (for position)\n{chunk}\n\n"
                    f"FRAGMENT TO CORRECT\n{frags[ui]}")
            try:
                res = run.ask("reason", sys_r, user, max_out=int(est_tokens(frags[ui]) * 1.3) + 600, paper=True,
                              label=f"Restore formulas, pages {span(u['pages'])}", temperature=0.0)
            except LLMError as e:
                if passing(e):
                    raise
                continue
            cand = shape_fragment(res.text, secs[u["sec"]]["title"], u["part"] == 0, figs, [by_n[n] for n in u["pages"]])
            trial = frags[:ui] + [cand] + frags[ui + 1:]
            new_rep = fidelity.audit(apages, "\n".join(trial), omitted)
            if new_rep["preserved"] > report["preserved"] and repair_is_faithful(frags[ui], cand):
                frags, report = trial, new_rep
            elif new_rep["preserved"] > report["preserved"]:
                run.log(f"The repair of pages {span(u['pages'])} changed more than the formulas, so it was not used.", "warn")
        save()
        # d. no later step works on a draft that left out pages of the notes
        joined = "\n".join(frags)
        gaps = [n for n in page_gaps(apages, report, page_marks(joined) or None) if n in in_units]
        if not gaps:
            break
        for ui, u in enumerate(units):
            if set(u["pages"]) & set(gaps):
                frags[ui] = None
                units[ui] = {**u, "tries": 1, "repaired": False}
        save()
        where = ", ".join(map(str, gaps[:12])) + ("…" if len(gaps) > 12 else "")
        if attempt:
            raise ValueError(
                f"The first draft still leaves out the material of page{'s' if len(gaps) > 1 else ''} {where} of your notes, "
                "after these pages were written again in smaller parts. The draft is not passed on, so no check or "
                "question is based on an incomplete paper. Press Continue to write these pages once more; if it happens "
                "again, open them on the Transcript screen: a page that is hard to read or very long is the usual cause.")
        run.log(f"After writing, the material of page{'s' if len(gaps) > 1 else ''} {where} is still missing from the draft. "
                "These parts are written again.", "warn")

    body = "\n\n".join(paperlib.close_open("\n\n".join(f for f, u in zip(frags, units) if u["sec"] == si))
                       for si in range(len(secs)))
    blocks = paperlib.parse_body(body)
    ensure_labels(blocks)
    report = fidelity.audit(apages, body, omitted)
    p.save("body", {"blocks": blocks, "organisation": org, "figures": figs})
    p.save("audit", report)
    n_res = sum(1 for b in blocks if b["type"] == "env" and b.get("env") in RESULT_KINDS)
    n_proof = sum(1 for b in blocks if b["type"] == "proof")
    run.progress(3, 3, "first draft written")
    run.log(f"First draft: {len(secs)} sections, {n_res} results, {n_proof} proofs, {len(figs)} figure(s).")
    run.log(f"Fidelity audit: {report['preserved']} of {report['total']} formulas preserved; "
            f"{len(report['altered'])} altered, {len(report['missing'])} missing, {len(report['added'])} added.",
            "warn" if report["altered"] or report["missing"] or report["added"] else "info")
