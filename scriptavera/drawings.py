# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What each drawing in the notes is for: a figure the paper's reader needs, or the author's own explanation of an
argument, which is not printed.

A drawing in a proof or a proof sketch is usually the author's aid to see a step, and the proof is complete without
it; a drawing that shows something the text relies on (a construction, labels named only in the drawing, data)
belongs in the paper. The decision is made from the text around each drawing, not from the drawing itself, so it
does not depend on how well a model reads a sketch:
  1. the code finds where each drawing sits (in a proof, a sketch, a definition, an example ...), the passage around
     it, and every place in the notes that mentions a drawing;
  2. one reading of the drawings with their passages decides each: for the paper, an explanation, or the author's
     labels on a formula; and whether that is sure;
  3. the code holds each verdict to its own reasons: an explanation that the text relies on, a numbered one, or a
     figure for the paper inside a proof that nothing relies on is not sure, and the author is asked.
A sure explanation is settled at once: it is not redrawn and not printed, and the author sees it with one button to
print it instead. Every other drawing is redrawn and put to the author. An explanation is not thrown away: its
description stays in the notes the writers read, marked as not printed, and a sketched proof is completed with the
drawings made for it."""
from __future__ import annotations

import re

from . import prompts
from .llm import LLMError
from .pipeline import Run, _room, _topic_text, approved_pages, find_figures, passing, strip_figures
from .store import Project
from .util import fit, loads_lenient

USES = ("paper", "explanation", "annotation")
BATCH = 10                                   # drawings judged in one reading
HERE = "⟦THE DRAWING⟧"

# How a labelled part of the notes begins: a label at the start of a line ("Proof:", "Lemma 2.1 (bound).",
# "Sketch of proof:", "Idea:"), followed by a colon or a full stop.
_KINDS = {
    "sketch": r"proof\s+sketch|sketch\s+of\s+(?:the\s+)?proof|sketch|idea(?:\s+of\s+(?:the\s+)?proof)?|heuristics?|intuition",
    "proof": r"proof|pf",
    "statement": r"lemma|theorem|thm|proposition|prop|corollary|cor|claim|conjecture",
    "definition": r"definition|defn|def",
    "example": r"example|ex",
    "remark": r"remark|rem|note|observation|obs",
    "problem": r"problem|question|setting|set-?up|notation|goal|aim",
}
_PART = re.compile(
    r"(?im)^[ \t>*•-]*(?:\\textbf\{|\\emph\{|\\textit\{|\*\*|__)?[ \t]*(?:"
    + "|".join(f"(?P<{k}>{v})" for k, v in _KINDS.items())
    + r")\b[ \t]*(?:\d+(?:\.\d+)*)?[ \t]*(?:\([^()\n]{0,40}\))?[ \t]*(?:of[ \t]+[^\n:.]{1,40}?)?[ \t]*\}?[ \t]*(?:\*\*|__)?[ \t]*[:.]")
_END = re.compile(r"∎|□|■|\\square\b|\\blacksquare\b|\\qed\b|\\Box\b|\bQ\.?\s?E\.?\s?D\b\.?", re.I)
_WORDS = {"sketch": "inside a proof sketch, or the idea of a proof", "proof": "inside a proof",
          "statement": "right after the statement of a result, before its proof", "definition": "in a definition",
          "example": "in an example", "remark": "in a remark", "problem": "in the statement of the problem or the set-up"}
_MENTION = re.compile(r"(?i)\b(?:fig(?:ure)?s?\.?\s*\d+|figures?|pictures?|pictured|diagrams?|drawings?|as\s+drawn|"
                      r"drawn\s+(?:above|below|here)|as\s+shown|shown\s+(?:above|below|here)|see\s+(?:above|below)|"
                      r"(?:graph|plot)\s+(?:above|below))\b")
_NUMBERED = re.compile(r"(?i)\bfig(?:ure)?s?\.?\s*\d+")


def _short(s: str, n: int = 80) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def where(before: str, starts: list[tuple[int, int]] | None = None) -> tuple[str, str]:
    """Where a drawing sits, from the labelled parts of the notes before it: (kind, words for the reader). `starts`
    lists (offset, page) for the pages joined in `before`, so that the reader is told where the part begins."""
    last = None
    for m in _PART.finditer(before):
        last = m
    if not last:
        return "none", 'not inside any labelled part of the notes (no label such as "Proof:" or "Lemma 2." comes before it)'
    kind = next(k for k in _KINDS if last.group(k))
    if kind == "proof" and re.search(r"sketch|idea", last.group(0), re.I):
        kind = "sketch"
    line = _short(before[last.start():].split("\n", 1)[0], 70)
    page = next((n for at, n in reversed(starts or []) if at <= last.start()), None)
    on = f" on page {page}" if page is not None else ""
    if kind in ("proof", "sketch") and _END.search(before, last.end()):
        return "after", f'after the end of a proof (the proof began{on} with "{line}")'
    return kind, f'{_WORDS[kind]} (it begins{on} with "{line}")'


def mentions(pages: dict[int, str], limit: int = 15) -> list[str]:
    """Every sentence of the notes that mentions a drawing, with its page."""
    out: list[str] = []
    for n in sorted(pages):
        for s in re.split(r"(?<=[.!?])\s+|\n+", strip_figures(pages[n])):
            if _MENTION.search(s):
                line = f'page {n}: "{_short(s, 180)}"'
                if line not in out:
                    out.append(line)
                if len(out) >= limit:
                    return out
    return out


def _others(text: str, k: int = -1) -> str:
    """A page with drawing k marked as the one being judged and every other drawing named as another drawing."""
    found = find_figures(text)
    for j in range(len(found) - 1, -1, -1):
        a, b, desc, _ = found[j]
        text = text[:a] + (HERE if j == k else f"[another drawing: {_short(desc, 90)}]") + text[b:]
    return text


def item(rec: dict, pages: dict[int, str], before: int = 900, after: int = 500) -> dict:
    """One drawing with what the reading needs: the passage around it (the drawing marked), where it sits, and
    whether it is numbered."""
    order = sorted(pages)
    n, k = rec["page"], rec["k"]
    marked = _others(pages.get(n, ""), k)
    at = marked.find(HERE)
    if at < 0:                                   # the marker is gone (the page was edited): judge by the description
        marked, at = HERE, 0
    head, tail = marked[:at], marked[at + len(HERE):]
    i = order.index(n) if n in order else 0
    # where it sits: the labelled parts before it, on this page and the two before
    joined, starts = "", []
    for m in order[max(0, i - 2):i]:
        starts.append((len(joined), m))
        joined += strip_figures(pages[m]) + "\n"
    starts.append((len(joined), n))
    kind, words = where(joined + head, starts)
    # the passage shown: the text before and after, cut between lines, with the neighbouring page where it is short
    shown_head = head[-before:]
    if len(head) > before and "\n" in shown_head[:-200]:
        shown_head = "…\n" + shown_head[shown_head.index("\n") + 1:]
    if len(head) < 300 and i > 0:
        prev = _others(pages[order[i - 1]])[-500:]
        shown_head = f"(end of page {order[i - 1]})\n…{prev}\n(page {n})\n" + shown_head
    shown_tail = tail[:after]
    if len(tail) > after and "\n" in shown_tail[150:]:
        shown_tail = shown_tail[:shown_tail.rindex("\n")] + "\n…"
    if len(tail.strip()) < 200 and i + 1 < len(order):
        shown_tail += f"\n(page {order[i + 1]})\n" + _others(pages[order[i + 1]])[:300] + "…"
    passage = re.sub(r"\n{3,}", "\n\n", shown_head + HERE + shown_tail).strip()
    desc = rec.get("desc", "")
    return {"id": rec["id"], "rec": rec, "page": n, "k": k, "of": rec.get("of", 1), "desc": desc, "kind": kind,
            "where": words, "passage": passage, "numbered": bool(_NUMBERED.search(desc) or _NUMBERED.search(head[-300:] + tail[:300]))}


def _block(it: dict) -> str:
    pos = f", drawing {it['k'] + 1} of {it['of']} on the page" if it["of"] > 1 else ""
    return (f"DRAWING {it['id']}: page {it['page']}{pos}\nDescription (from the transcription): {it['desc']}\n"
            f"Where it sits: {it['where']}\nPassage (the drawing is at {HERE}):\n{it['passage']}")


def settle(it: dict, row: dict | None, model: str = "") -> dict:
    """The verdict on one drawing, held to its own reasons. Anything the code cannot square is not sure."""
    in_proof = it["kind"] in ("proof", "sketch")
    lean = "explanation" if in_proof else "paper"
    if not row:
        return {"use": lean, "sure": False, "why": f"The app could not decide this by itself. The drawing sits {it['where']}.",
                "illustrates": "", "needed": "", "doubt": "No reading of the notes decided it.", "by": model}
    raw = _clean(row.get("use")).lower()
    use = next((u for u in USES if raw.startswith(u[:5])), "")
    sure = row.get("sure") is True or _clean(row.get("sure")).lower() in ("true", "yes")
    why, ill, needed = _clean(row.get("why")), _clean(row.get("illustrates")), _clean(row.get("needed"))
    if needed.lower().strip(" .") in ("", "none", "nothing", "n/a", "no", "-"):
        needed = ""
    doubt = ""
    if not use:
        use, sure, doubt = lean, False, "The reading of the notes gave no clear answer."
    elif use == "explanation" and needed:
        sure, doubt = False, f"The text seems to rely on it: {needed}"
    elif use == "explanation" and it["numbered"]:
        sure, doubt = False, "It is numbered, or referred to by number, as figures in a paper are."
    elif use == "paper" and in_proof and not needed and not it["numbered"]:
        sure, doubt = False, "It sits in a proof, and the reading names nothing the proof takes from it."
    elif use == "annotation":
        sure, doubt = False, "It may be your own labels on a formula rather than a drawing."
    if not sure and not doubt:
        doubt = "The reading of the notes was not sure."
    return {"use": use, "sure": sure, "why": why or f"It sits {it['where']}.", "illustrates": ill if use == "explanation" else "",
            "needed": needed, "doubt": "" if sure else doubt, "by": model}


def judge(run: Run, recs: list[dict], pages: dict[int, str]) -> None:
    """Decide what each drawing in `recs` is for. The verdict goes into rec["judged"]; a sure explanation that the
    author has not answered yet is settled as not printed (rec["choice"] = "explain", rec["auto"] = True)."""
    if not recs:
        return
    items = [item(r, pages) for r in recs]
    seen = mentions(pages)
    problem, _ = _topic_text(run.p)
    system = prompts.FIG_ROLE.format(field=run.field)
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    for bi, batch in enumerate(batches, 1):
        run.check()
        ids = {it["id"] for it in batch}
        user = ((f"WHAT THE NOTES ARE ABOUT\n{problem}\n\n" if problem else "") + "\n\n".join(_block(it) for it in batch)
                + "\n\nPLACES IN THE NOTES THAT MENTION A DRAWING\n" + ("\n".join(seen) or "none"))
        rows: dict[str, dict] = {}
        model, last = "", ""
        for attempt in range(2):
            try:
                res = run.ask("reason", system, fit(user, _room("reason", system, 2400), "middle"), json_mode=True,
                              max_out=600 + 260 * len(batch), effort="medium", temperature=0.0,
                              label="Decide which drawings are for the paper" + (f" (part {bi} of {len(batches)})" if len(batches) > 1 else "")
                              + (" (retry)" if attempt else ""))
                model = res.model
                data = loads_lenient(res.text)
                got = (data.get("drawings") or data.get("figures") or []) if isinstance(data, dict) else data
                for row in got if isinstance(got, list) else []:
                    if isinstance(row, dict) and _clean(row.get("id")) in ids:
                        rows[_clean(row.get("id"))] = row
                if rows:
                    break
                last = "no verdict for any drawing"
            except ValueError as e:
                last = str(e)
            except LLMError as e:
                if passing(e):
                    raise
                last = str(e)
                break
        if len(rows) < len(batch):
            run.log(f"{len(batch) - len(rows)} drawing(s) could not be judged by a model ({_short(last, 120) or 'no answer'}); "
                    "you will be asked about them.", "warn")
        for it in batch:
            rec = it["rec"]
            rec["judged"] = settle(it, rows.get(it["id"]), model)
            if rec["judged"]["use"] == "explanation" and rec["judged"]["sure"] and not rec.get("choice"):
                rec["choice"], rec["auto"] = "explain", True


def wanted_drawn(rec: dict) -> bool:
    """Whether a drawing is redrawn: everything that may be printed. A sure explanation and the author's labels on a
    formula are not, nor a drawing the author has already kept out of the paper."""
    if rec.get("choice") in ("explain", "none"):
        return False
    if rec.get("asked_print"):
        return True
    j = rec.get("judged") or {}
    return j.get("use") != "annotation" and not (j.get("use") == "explanation" and j.get("sure"))


def _sq(s: str) -> str:
    return re.sub(r"[\s$]+", "", s or "")


def for_results(p: Project, units: list[dict]) -> dict[str, list[dict]]:
    """The explanation drawings made for each result's proof: label -> [{"page", "desc", "illustrates"}]. A drawing
    belongs to the result whose pages it is on; where a page has several, to the last one stated before it."""
    figs = [f for f in (p.load("figures") or {}).get("items", []) if f.get("choice") == "explain"]
    if not figs:
        return {}
    res = {x["id"]: x for x in (p.load("dossier") or {}).get("results", [])}
    pages = {pg["n"]: pg["text"] for pg in approved_pages(p)}
    out: dict[str, list[dict]] = {}
    for f in figs:
        cands = [u for u in units if u.get("label") and f["page"] in (res.get(u["label"].split(":", 1)[-1]) or {}).get("pages", [])]
        if not cands:
            continue
        owner = cands[0]
        if len(cands) > 1:
            text = pages.get(f["page"], "")
            found = find_figures(text)
            before = _sq(text[:found[f["k"]][0]] if f["k"] < len(found) else text)
            best = -1
            for u in cands:
                words = _sq((res.get(u["label"].split(":", 1)[-1]) or {}).get("author_words", ""))[:50]
                at = before.rfind(words) if len(words) >= 8 else -1
                if at > best:
                    best, owner = at, u
            if best < 0:                         # stated on an earlier page and continued here
                owner = next((u for u in cands if min((res.get(u["label"].split(":", 1)[-1]) or {}).get("pages") or [f["page"]]) < f["page"]), cands[0])
        out.setdefault(owner["label"], []).append({"page": f["page"], "desc": f.get("desc", ""),
                                                   "illustrates": (f.get("judged") or {}).get("illustrates", "")})
    return out


def describe(pics: list[dict]) -> str:
    """The drawings of one proof, as the writers and checkers of its completion see them."""
    if not pics:
        return ""
    return ("THE AUTHOR'S DRAWINGS FOR THIS PROOF (in the notes, not printed; they show what the author had in mind)\n"
            + "\n".join(f"- page {d['page']}: {d['desc']}" + (f" (It helps to see {d['illustrates'].rstrip('.')}.)" if d.get("illustrates") else "")
                        for d in pics))
