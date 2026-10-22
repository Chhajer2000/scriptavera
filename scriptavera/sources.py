# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""From the paper back to the handwriting.

A passage of the paper (the paragraph or displayed formula around a line of the LaTeX source) is matched to the page
of the notes it came from: its formulas are compared with the formulas of every transcribed page, by the same
normalisation the fidelity audit uses, and its words with the page's words. The page reader then points at the
lines of handwriting the passage was typeset from. Each answer is saved, so asking again costs nothing."""
from __future__ import annotations

import hashlib
import re

from . import audit, prompts
from .llm import router
from .store import Project
from .util import fit, loads_lenient

_WORD = re.compile(r"[A-Za-z]{4,}")
_STOP = set("this that with from then have which there their these those where when into such also only each every some "
            "therefore hence thus since because will would could should must here what them they than other more most "
            "follows following given gives give proof lemma theorem section".split())


def passage_at(text: str, line: int) -> str:
    """The displayed formula or paragraph containing a 1-based line."""
    lines = (text or "").split("\n")
    if not lines:
        return ""
    i = max(0, min(len(lines) - 1, line - 1))
    opens = re.compile(r"\\begin\{(equation|align|gather|multline|eqnarray|flalign)\*?\}|^\s*\\\[|^\s*\$\$")
    closes = re.compile(r"\\end\{(equation|align|gather|multline|eqnarray|flalign)\*?\}|\\\]\s*$|\$\$\s*$")
    for a in range(i, max(-1, i - 40), -1):           # inside a display: take the display
        if closes.search(lines[a]) and a < i:
            break
        if opens.search(lines[a]):
            for b in range(i, min(len(lines), i + 60)):
                if closes.search(lines[b]) and (b > a or lines[b].count("$$") >= 2 or "\\]" in lines[b]):
                    return "\n".join(lines[a:b + 1])
            break
    a = i
    while a > 0 and lines[a - 1].strip() and not lines[a - 1].lstrip().startswith("\\section"):
        a -= 1
    b = i
    while b + 1 < len(lines) and lines[b + 1].strip():
        b += 1
    return "\n".join(lines[a:b + 1])


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(re.sub(r"\\[A-Za-z]+", " ", text or "")) if w.lower() not in _STOP}


def find_page(pages: list[dict], passage: str) -> dict | None:
    """The page a passage came from: formulas weigh most, words break ties. None when nothing matches."""
    mine = audit.formulas(passage, inline=True)
    words = _words(passage)
    best = None
    for pg in pages:
        theirs = audit.formulas(pg["text"], inline=True)
        norms = {f["norm"] for f in theirs}
        f_score, matched = 0.0, []
        for f in mine:
            if f["norm"] in norms:
                f_score += 1
                matched.append(f["tex"])
            else:
                part = max((audit._partial(f["norm"], g["norm"]) for g in theirs), default=0.0)
                if part >= 0.8:
                    f_score += part
                    matched.append(f["tex"])
        pw = _words(pg["text"])
        w_score = len(words & pw) / max(1, len(words)) if words else 0.0
        score = f_score + 0.6 * w_score
        if best is None or score > best["score"]:
            best = {"page": pg["n"], "score": round(score, 3), "formulas": matched[:4], "of": len(mine),
                    "words": round(w_score, 2)}
    if not best or (best["score"] < 0.35 and not best["formulas"]):
        return None
    return best


def locate_passage(p: Project, page: int, passage: str) -> dict:
    """Boxes around the handwriting a passage came from (0-1000 coordinates, as for symbols). Cached per page and passage."""
    from .pipeline import Run, _page_bytes          # late import: pipeline imports much of the app
    key = f"{page}|{hashlib.sha256(passage.encode('utf-8')).hexdigest()[:16]}"
    cache = p.load("source_boxes", {})
    if key in cache:
        return cache[key]
    hooks = Run(p)
    hooks.stage = "finish"
    res = router.chat("vision_best", prompts.LOCATE_PASSAGE.format(), "PASSAGE OF THE PAPER (LaTeX)\n" + fit(passage, 2500, "head"),
                      images=[_page_bytes(p, page)], json_mode=True, max_out=600, effort="low",
                      label=f"Find a passage on page {page}", hooks=hooks, strict=False)
    boxes, note = [], ""
    try:
        data = loads_lenient(res.text)
        raw = data.get("boxes") if isinstance(data, dict) else data
        note = str(data.get("note") or "") if isinstance(data, dict) else ""
        for b in raw or []:
            if isinstance(b, dict):
                b = b.get("box_2d") or b.get("box") or []
            if isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                vals = [float(v) * (1000 if max(map(float, b)) <= 1.0 else 1) for v in b]
                y0, x0, y1, x1 = (max(0, min(1000, int(v))) for v in vals)
                if y1 - y0 >= 2 and x1 - x0 >= 2:
                    boxes.append([y0, x0, y1, x1])
    except ValueError:
        note = "The page reader's answer could not be understood."
    out = {"found": bool(boxes), "boxes": boxes[:6], "note": note[:300], "model": res.model}
    with p._lock:
        cache = p.load("source_boxes", {})
        cache[key] = out
        p.save("source_boxes", cache)
    return out


def source_of(p: Project, text: str, line: int, point: bool = True) -> dict:
    """Where a line of the paper came from: {page, passage, formulas, boxes, found, note}."""
    from .pipeline import approved_pages
    passage = passage_at(text, line).strip()
    if not passage:
        return {"page": None, "note": "This line is empty."}
    pages = approved_pages(p)
    if not pages:
        return {"page": None, "note": "This paper has no transcribed notes to compare with."}
    hit = find_page(pages, passage)
    if not hit:
        return {"page": None, "passage": passage, "note": "No page of your notes has this passage's formulas or words: it was "
                                                          "probably written for the paper (an introduction, a transition, a remark)."}
    out = {**hit, "passage": passage, "boxes": [], "found": False, "note": ""}
    if point:
        out.update({k: v for k, v in locate_passage(p, hit["page"], passage).items() if k in ("boxes", "found", "note")})
    return out
