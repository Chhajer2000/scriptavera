# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What each drawing in the notes is for, decided once the whole paper is known: a figure the paper's reader needs,
printed where it belongs, or the author's own explanation of an argument, which is not printed.

Up to the check of the transcript, the notes are copied as they are, drawings included: nothing is decided there.
The decision is made at the structure step, when the paper is known as a whole: its problem and storyline, its
sections, every statement in its order, and the statement each drawing belongs to. A drawing in a proof or a proof
sketch is usually the author's aid to see a step, and the proof is complete without it; a drawing that shows
something the text relies on (a construction, labels named only in the drawing, data) belongs in the paper:
  1. the code finds the statement each drawing belongs to, where it sits (in a proof, a sketch, a definition ...),
     the passage around it, and every place in the notes that mentions a drawing;
  2. one reading of the drawings, with the whole paper, decides each: printed (with which statement, inside its
     proof or after it, and a caption) or an explanation; and whether that is sure;
  3. the code holds each verdict to its own reasons: an explanation that the text relies on, a numbered one, or a
     figure for the paper inside a proof that nothing relies on is not sure, and the author is asked.
The author sees every decision on the structure screen and can change it; confirming the structure confirms them.
An explanation is not thrown away: its description stays in the notes the writers read, marked as not printed, and a
sketched proof is completed with the drawings made for it. A decision is kept as long as what it was made from (the
drawing, its passage, its statement and section) is unchanged, and an answer of the author's is always kept."""
from __future__ import annotations

import re

from . import prompts
from .llm import LLMError
from .pipeline import Run, _room, _topic_text, approved_pages, find_figures, passing, strip_figures, transcript_figures
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


def _sq(s: str) -> str:
    return re.sub(r"[\s$]+", "", s or "")


def owner(rec: dict, seq: list[dict], pages: dict[int, str]) -> dict | None:
    """The statement a drawing belongs to: the one of the paper whose pages it is drawn on; where a page has several,
    the last one stated before the drawing; a drawing on a page no statement comes from belongs to the last statement
    before that page."""
    n = rec["page"]
    cands = [x for x in seq if n in (x.get("pages") or [])]
    if not cands:
        before = [x for x in seq if (x.get("pages") or [10 ** 6])[0] <= n]
        return before[-1] if before else (seq[0] if seq else None)
    if len(cands) == 1:
        return cands[0]
    text = pages.get(n, "")
    found = find_figures(text)
    head = _sq(text[:found[rec["k"]][0]] if rec.get("k", 0) < len(found) else text)
    best, who = -1, None
    for x in cands:
        words = _sq(x.get("words") or x.get("original") or "")[:50]
        at = head.rfind(words) if len(words) >= 8 else -1
        if at > best:
            best, who = at, x
    if who is None:                              # stated on an earlier page and continued on this one
        who = next((x for x in cands if min(x.get("pages") or [n]) < n), cands[0])
    return who


def context_block(it: dict, x: dict | None, nums: dict, sec: dict | None, si: int | None) -> str:
    from .drafting import kind_name
    pos = f", drawing {it['k'] + 1} of {it['of']} on the page" if it["of"] > 1 else ""
    belongs = "no statement of the paper"
    if x:
        belongs = (f"{kind_name(x['kind'])} {nums.get(x['id'], '')} ({x['id']})" + (f" ({x['name']})" if x.get("name") else "")
                   + f": {_short(x.get('statement', ''), 600)}")
        if sec is not None:
            belongs += f"\n  It is in section {si + 2}, \"{sec['title']}\"" + (f" ({sec['purpose']})" if sec.get("purpose") else "")
    return (f"DRAWING {it['id']}: page {it['page']}{pos}\nDescription (from the copy of the notes): {it['desc']}\n"
            f"It belongs to: {belongs}\nWhere it sits in the notes: {it['where']}\n"
            f"Passage of the notes (the drawing is at {HERE}):\n{it['passage']}")


def plan_key(it: dict, x: dict | None, sec: dict | None, problem: str) -> str:
    """What the decision on a drawing rests on: the drawing, the passage of the notes around it, and the statement it
    belongs to. A change elsewhere (another statement, the title of its section, the wording of the topic) leaves the
    decision as it is."""
    import hashlib
    import json
    raw = json.dumps([it["desc"], it["passage"], (x or {}).get("id"), (x or {}).get("statement")], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def printable(p: Project) -> list[dict]:
    """The drawings whose copy the author kept in step 9 (their own drawing, or the copy drawn again)."""
    present = {(w["page"], w["k"]) for w in transcript_figures(p)}
    return [f for f in (p.load("figures") or {}).get("items", [])
            if f.get("choice") in ("tikz", "orig") and not f.get("dup") and (f["page"], f["k"]) in present]


def plan(run: Run, seq: list[dict], secs: list[dict], nums: dict, fresh_ok: bool = True) -> dict:
    """The decision on every drawing, with the whole paper in view (see the module's description). A decision made
    from the same drawing, passage, statement and section is kept; the author's own answer is always kept (it is
    looked at again only if the drawing itself changed). Returns the plan, saved as figure_plan.json."""
    p = run.p
    P = p.load("figure_plan") or {"items": {}}
    figs = printable(p)
    if not figs:
        if P.get("items"):
            p.save("figure_plan", {"items": {}})
        return {"items": {}}
    pages = {pg["n"]: pg["text"] for pg in approved_pages(p)}
    problem, _ = _topic_text(p)
    d = p.load("dossier") or {}
    sec_of = {i: (si, s) for si, s in enumerate(secs) for i in s["items"]}
    items, ctx = {}, {}
    for f in figs:
        it = item(f, pages, before=1500, after=800)
        x = owner(f, seq, pages)
        si, sec = sec_of.get((x or {}).get("id"), (None, None))
        items[f["id"]] = it
        ctx[f["id"]] = (x, sec, si, plan_key(it, x, sec, problem))
    old = P.get("items") or {}
    out: dict = {}
    todo = []
    for fid, (x, sec, si, key) in ctx.items():
        e = old.get(fid)
        same_drawing = e and e.get("desc", items[fid]["desc"]) == items[fid]["desc"]
        if e and (e.get("key") == key or (not e.get("auto", True) and same_drawing) or (e.get("legacy") and not e.get("key"))):
            out[fid] = {**e, "key": key if e.get("auto", True) else e.get("key", key)}
            if not out[fid].get("with") and x:
                out[fid]["with"] = x["id"]
            continue
        todo.append(fid)
    if todo:
        seen = mentions(pages)
        structure = "\n".join(f"{si + 2}. {s['title']}" + (f" ({s['purpose']})" if s.get("purpose") else "") + "\n"
                               + "\n".join(f"   - {nums.get(i, '')} {i}: " + _short(next((y.get('statement', '') for y in seq if y['id'] == i), ''), 160)
                                           for i in s["items"]) for si, s in enumerate(secs))
        paper = (f"THE PROBLEM OF THE PAPER\n{d.get('problem') or problem}\n\n"
                 + (f"HOW THE ARGUMENT GOES\n{d['storyline']}\n\n" if d.get("storyline") else "")
                 + f"THE STRUCTURE OF THE PAPER (sections after the introduction, with every statement)\n{structure}")
        system = prompts.FIG_PLAN.format(field=run.field)
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        for bi, batch in enumerate(batches, 1):
            run.check()
            user = (paper + "\n\n" + "\n\n".join(context_block(items[fid], ctx[fid][0], nums, ctx[fid][1], ctx[fid][2]) for fid in batch)
                    + "\n\nPLACES IN THE NOTES THAT MENTION A DRAWING\n" + ("\n".join(seen) or "none"))
            rows: dict[str, dict] = {}
            model, last = "", ""
            for attempt in range(2):
                try:
                    res = run.ask("reason", system, fit(user, _room("reason", system, 3000), "middle"), json_mode=True,
                                  max_out=700 + 320 * len(batch), effort="medium", temperature=0.0,
                                  label="Decide which drawings the paper prints" + (f" (part {bi} of {len(batches)})" if len(batches) > 1 else "")
                                  + (" (retry)" if attempt else ""))
                    model = res.model
                    data = loads_lenient(res.text)
                    got = (data.get("drawings") or data.get("figures") or []) if isinstance(data, dict) else data
                    for row in got if isinstance(got, list) else []:
                        if isinstance(row, dict) and _clean(row.get("id")) in batch:
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
                run.log(f"{len(batch) - len(rows)} drawing(s) could not be decided by a model ({_short(last, 120) or 'no answer'}); "
                        "they are put to you on the structure screen.", "warn")
            for fid in batch:
                x, sec, si, key = ctx[fid]
                v = settle(items[fid], rows.get(fid), model)
                if v["use"] == "annotation":         # copied in step 9 as a drawing: treated as one that only explains
                    v["use"] = "explanation"
                row = rows.get(fid) or {}
                with_id = _clean(row.get("with"))
                if with_id not in {y["id"] for y in seq}:
                    with_id = (x or {}).get("id", "")
                ip = row.get("in_proof")
                out[fid] = {**v, "with": with_id, "in_proof": ip is True or _clean(ip).lower() in ("true", "yes"),
                            "caption": _clean(row.get("caption")) or items[fid]["desc"], "key": key, "auto": True,
                            "desc": items[fid]["desc"], "page": items[fid]["page"]}
                if fresh_ok:
                    out[fid]["fresh"] = True
    P = {"items": dict(sorted(out.items(), key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0)))}
    p.save("figure_plan", P)
    if todo:
        shown = sum(1 for fid in todo if out[fid]["use"] == "paper")
        run.log(f"Drawings: {plural_drawings(len(todo))} decided with the whole paper in view: {shown} printed as "
                f"figure{'s' if shown != 1 else ''}, {len(todo) - shown} only explain an argument. You see each on the "
                "structure screen and can change it.")
    return P


def plural_drawings(n: int) -> str:
    return f"{n} drawing" + ("s" if n != 1 else "")


def set_plan(p: Project, fid: str, use: str | None = None, with_id: str | None = None, in_proof: bool | None = None,
             caption: str | None = None) -> dict:
    """The author's own decision on one drawing: printed or not, with which statement, its caption."""
    with p._lock:
        P = p.load("figure_plan") or {"items": {}}
        e = P["items"].get(fid)
        if e is None:
            raise ValueError("Unknown drawing.")
        if use is not None:
            if use not in ("paper", "explanation"):
                raise ValueError("Unknown choice.")
            e["use"] = use
        if with_id is not None:
            e["with"] = with_id
        if in_proof is not None:
            e["in_proof"] = bool(in_proof)
        if caption is not None:
            e["caption"] = re.sub(r"\s+", " ", caption).strip()[:600]
        e["auto"], e["sure"], e["doubt"] = False, True, ""
        e.pop("fresh", None)
        p.save("figure_plan", P)
        return e


def plan_items(p: Project) -> dict:
    return (p.load("figure_plan") or {}).get("items") or {}


def for_results(p: Project, units: list[dict]) -> dict[str, list[dict]]:
    """The explanation drawings made for each result's proof: label -> [{"page", "desc", "illustrates"}]: those the
    plan keeps as explanations, with the statement it gave them to."""
    figs = {f["id"]: f for f in (p.load("figures") or {}).get("items", [])}
    labels = {u["label"].split(":", 1)[-1]: u["label"] for u in units if u.get("label")}
    out: dict[str, list[dict]] = {}
    for fid, e in plan_items(p).items():
        if e.get("use") != "explanation" or fid not in figs:
            continue
        lab = labels.get(e.get("with") or "")
        if lab:
            out.setdefault(lab, []).append({"page": figs[fid]["page"], "desc": figs[fid].get("desc", ""),
                                            "illustrates": e.get("illustrates", "")})
    return out


def describe(pics: list[dict]) -> str:
    """The drawings of one proof, as the writers and checkers of its completion see them."""
    if not pics:
        return ""
    return ("THE AUTHOR'S DRAWINGS FOR THIS PROOF (in the notes, not printed; they show what the author had in mind)\n"
            + "\n".join(f"- page {d['page']}: {d['desc']}" + (f" (It helps to see {d['illustrates'].rstrip('.')}.)" if d.get("illustrates") else "")
                        for d in pics))
