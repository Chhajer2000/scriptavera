# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The paper written the way a mathematician writes one: the statements first, then the order in which they are
presented, then each proof on its own, then the text that joins them, and the introduction and abstract last.

  1. Statements. Every definition, lemma, proposition, theorem, corollary, example, remark and question of the notes
     is written as it will stand in the paper. Where a statement is unclear, the author is asked. The author checks
     each one, may change its kind, edit it, state it as a conjecture, credit it to a paper or leave it out.
  2. Structure. The statements are put in sections, each before any statement that uses it (the order is enforced
     here, not left to a model). The author confirms the skeleton.
  3. Proofs. Each proof is written on its own, from the author's argument in the notes, with the statements presented
     before it. The writer is free in the writing and keeps the author's idea. A second reading checks it against the
     author's argument: a wrong proof is written again with the mistake named; a proof that had to repair the author's
     argument is marked for the author to read; a statement that seems false is put to the author.
  4. The paper. The program assembles statements and proofs; a model writes only the text between them, in numbered
     places, so it cannot change them. The introduction and the abstract are written from the finished paper.
What the author fixes stays fixed: a statement is never changed after the author confirmed it, except by the author.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from . import audit as fidelity
from . import paper as paperlib, prompts
from .llm import LLMError
from .pipeline import (Run, _room, _side_by_side, emit, find_figures, notation_text, passing, strip_explanations,
                       writing_pages)
from .store import Project
from .texutil import check_balance, clean_fragment, leaks
from .util import extract_tag, fit, parse_lines
from .writing import (KINDS, KIND_TEXT, default_kind, _none, _view, citable, cited_keys, dossier_text, drop_unsourced, fields,
                      fix_keys, organisation, page_list, sources_text)

ITEM_KINDS = ("definition", "lemma", "proposition", "theorem", "corollary", "conjecture", "example", "remark", "question")
PROVED = ("lemma", "proposition", "theorem", "corollary")          # a proof is written for these
EXPLAINED = ("example", "remark")                                   # a short text is written after these
TREATMENTS = ("keep", "credit", "conjecture", "omit")
_lock = threading.Lock()


def _sig(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def label_of(x: dict) -> str:
    return ("def:" if x["kind"] == "definition" else "res:") + x["id"]


def kind_name(kind: str) -> str:
    return paperlib.THEOREM_ENVS.get(kind, kind.capitalize())


# ------------------------------------------------------------------ 1. statements

def _items_from_dossier(d: dict, a: dict) -> list[dict]:
    """The dossier's definitions, results and other items, with what the assessment found, before the statements
    are written. 'original' is what the notes say; 'statement' is what the paper will say."""
    res = a.get("results") or {}
    out = []
    for x in d.get("definitions", []):
        out.append({"id": x["id"], "kind": "definition", "original": x["text"], "words": x.get("name", ""),
                    "pages": [x["page"]] if x.get("page") else [], "depends": [], "role": "auxiliary"})
    for x in d.get("results", []):
        info = res.get(x["id"], {})
        out.append({"id": x["id"], "kind": x["kind"], "original": x["statement"], "words": x.get("words", ""),
                    "pages": x.get("pages", []), "depends": x.get("depends", []), "role": x.get("role", "intermediate"),
                    "proof": info.get("proof", "complete"), "where": _none(info.get("where", "")),
                    "known": info.get("known", "new"), "key": info.get("key", ""), "evidence": _none(info.get("evidence", ""))})
    for x in d.get("others", []):
        out.append({"id": x["id"], "kind": x["kind"], "original": x["text"], "words": "",
                    "pages": [x["page"]] if x.get("page") else [], "depends": x.get("relates", []), "role": "auxiliary"})
    return out


def changed_formulas(original: str, statement: str) -> list[str]:
    """The formulas of the notes that the statement no longer contains as written: shown to the author beside it, so
    that a rewritten statement can never change a hypothesis unseen."""
    if not original.strip() or not statement.strip():
        return []
    rep = fidelity.audit([{"n": 0, "text": original}], statement)
    return [x["source"] for x in rep["missing"] + rep["altered"]][:6]


def parse_items(text: str) -> dict[str, dict]:
    out = {}
    for r in fields(text, "item", ("id", "kind", "name", "placement", "needed", "statement", "question")):
        iid = re.sub(r"[^A-Za-z0-9]", "", r.get("id", ""))
        if iid and r.get("statement", "").strip():
            out[iid] = r
    return out


def default_treatment(x: dict) -> str:
    if x.get("known", "new") != "new" and x.get("key"):
        return "credit"
    return "keep"


def _merge_statement(x: dict, r: dict | None) -> dict:
    """One item with the statement written for it (or, when none came back, the dossier's own)."""
    r = r or {}
    kind = next((k for k in ITEM_KINDS if k == (r.get("kind") or "").strip().lower().rstrip(".")), None) \
        or next((k for k in ITEM_KINDS if k in (r.get("kind") or "").lower()), x["kind"])
    if x["kind"] == "definition" and kind in PROVED:
        kind = "definition"                      # a definition never becomes a result by a model's choice
    known = x.get("known", "new") != "new"
    place = (r.get("placement") or "").lower()
    placement = "preliminaries" if ("prelim" in place or (not place and known)) else "main"
    statement = clean_fragment(r.get("statement") or "") or x["original"]
    question = _none(r.get("question", ""))
    needed = (r.get("needed") or "yes").strip()
    cut = needed.lower().startswith("no") and x.get("role") != "main"
    why = re.sub(r"^no\s*[:,.-]?\s*", "", needed, flags=re.I).strip() if cut else ""
    return {**x, "kind": kind, "suggested_kind": kind, "name": _none(r.get("name", "")).strip("()"),
            "placement": placement, "statement": statement,
            "question": "" if cut else question, "answer": "", "settled": cut or not question,
            "treatment": "omit" if cut else default_treatment(x), "cut": why or ("not needed" if cut else ""),
            "changed": changed_formulas(x["original"], statement), "edited": False}


_KEPT_FIELDS = ("statement", "kind", "name", "placement", "treatment", "settled", "question", "asked", "answer", "edited", "cut")


def remember(p: Project, S: dict) -> None:
    """What the author decided about each statement, kept apart from the step's own files: when the notes are read
    again (pages added, a step redone), a statement whose source is unchanged keeps the author's decisions."""
    mem = p.load("statements_memory", {}) or {}
    for x in S.get("items", []):
        mem[_sig(x["id"], x["original"])] = {k: x.get(k) for k in _KEPT_FIELDS}
    p.save("statements_memory", dict(list(mem.items())[-600:]))


def recall(p: Project, items: list[dict]) -> int:
    mem = p.load("statements_memory", {}) or {}
    n = 0
    for x in items:
        old = mem.get(_sig(x["id"], x["original"]))
        if old:
            if old.get("treatment") == "credit" and not x.get("key"):
                old = {k: v for k, v in old.items() if k != "treatment"}     # no paper to credit any more
            x.update({k: v for k, v in old.items() if v is not None})
            x["changed"] = changed_formulas(x["original"], x["statement"])
            n += 1
    return n


def unused_pages(items: list[dict], pages: list[dict]) -> list[int]:
    """Pages of the notes whose material no statement comes from: shown, so that nothing is lost unseen."""
    used = {n for x in items for n in x.get("pages") or []}
    return [pg["n"] for pg in pages if pg["n"] not in used and len(pg["text"].strip()) > 80]


def prepare_statements(run: Run) -> None:
    """Before the author's statements step: every statement written as it will stand in the paper."""
    p = run.p
    d = p.load("dossier") or {}
    a = p.load("assessment") or {}
    sig = _sig(d, a)
    S = p.load("statements") or {}
    if S.get("sig") == sig and S.get("items"):
        return
    items = _items_from_dossier(d, a)
    if not items:
        raise ValueError("No definition or result was found in your notes, so there is nothing to state.")
    pages, _ = writing_pages(p)
    system = prompts.STATEMENTS.format(field=run.field)
    out = min(12000, 900 + 380 * len(items))
    room = _room("reason", system, out)
    dos = dossier_text(d, max(2500, int(room * 0.35)))
    assess = "\n".join(f"- {x['id']}: the notes' proof is {x.get('proof', 'complete')}"
                       + (f" ({x['where']})" if x.get("where") else "")
                       + (f"; already known from [{x['key']}]" if x.get("known", "new") != "new" else "; not found in the papers read")
                       for x in items if x["kind"] not in ("definition", "example", "remark", "question"))
    notation = notation_text(p.load("confirmed", {}), max(800, int(room * 0.1)))
    user = (f"DOSSIER\n{dos}\n\nASSESSMENT OF THE RESULTS\n{assess or '(none)'}\n\nCONFIRMED NOTATION\n{notation}\n\n"
            f"THE NOTES\n\n{_view(pages, max(2000, room - len(dos) - len(assess) - len(notation) - 600))}")
    run.progress(0, 1, "writing every statement as it will stand in the paper")
    got: dict[str, dict] = {}
    model = ""
    for attempt in range(2):
        res = run.ask("reason", system, user, max_out=out, effort="high",
                      label="Write the statements" + (" (retry)" if attempt else ""))
        got, model = parse_items(res.text), res.model
        if len([x for x in items if x["id"] in got]) >= max(1, len(items) // 2):
            break
    merged = [_merge_statement(x, got.get(x["id"])) for x in items]
    kept = recall(p, merged)
    if kept:
        run.log(f"{kept} statement{'s' if kept != 1 else ''} unchanged in your notes keep{'' if kept != 1 else 's'} what you decided before.")
    missing = [x["id"] for x in items if x["id"] not in got]
    if missing:
        run.log(f"No statement came back for {', '.join(missing)}: the wording of the notes is shown for "
                f"{'it' if len(missing) == 1 else 'them'}; check and edit it.", "warn")
    S = {"sig": sig, "items": merged, "model": model, "unused": unused_pages(merged, pages),
         "kind": default_kind(a, {x["id"] for x in merged if x.get("role") == "main"}), "note": ""}
    p.save("statements", S)
    remember(p, S)
    asks = sum(1 for x in merged if not x["settled"])
    run.progress(1, 1, "statements written")
    run.log(f"Statements written: {len(merged)}" + (f"; {asks} question{'s' if asks != 1 else ''} for you about unclear ones." if asks else "."))


def _save_item(p: Project, iid: str, change) -> dict:
    with _lock:
        S = p.load("statements") or {}
        x = next((y for y in S.get("items", []) if y["id"] == iid), None)
        if not x:
            raise ValueError("This statement no longer exists. Reload the page.")
        change(x, S)
        p.save("statements", S)
        remember(p, S)
        return x


def edit_statement(p: Project, iid: str, data: dict) -> dict:
    """The author's own change to one statement: its kind, name, place, how it is presented, or its text."""
    def change(x: dict, S: dict) -> None:
        if data.get("kind") in ITEM_KINDS:
            x["kind"] = data["kind"]
        if isinstance(data.get("name"), str):
            x["name"] = data["name"].strip()[:120]
        if data.get("placement") in ("preliminaries", "main"):
            x["placement"] = data["placement"]
        if data.get("treatment") in TREATMENTS:
            if data["treatment"] == "credit" and not x.get("key"):
                raise ValueError("No paper was found that contains this result, so it cannot be credited to one.")
            x["treatment"] = data["treatment"]
        if isinstance(data.get("statement"), str) and data["statement"].strip() and data["statement"].strip() != x["statement"]:
            x["statement"], x["edited"] = data["statement"].strip(), True
            x["changed"] = changed_formulas(x["original"], x["statement"])
        if data.get("dismiss"):                      # the question needs no answer: the statement stays as it is
            x["settled"] = True
    return _save_item(p, iid, change)


def answer_statement(p: Project, iid: str, answer: str) -> dict:
    """The author's answer to a question about a statement: the statement is written again by it."""
    answer = (answer or "").strip()[:3000]
    if not answer:
        raise ValueError("Write your answer first.")
    S = p.load("statements") or {}
    x = next((y for y in S.get("items", []) if y["id"] == iid), None)
    if not x:
        raise ValueError("This statement no longer exists. Reload the page.")
    pages, _ = writing_pages(p)
    near = [pg for pg in pages if pg["n"] in (x.get("pages") or [])]
    run = Run(p)
    run.stage = "scope"
    user = (f"FIELD\n{run.field}\n\nCONFIRMED NOTATION\n{notation_text(p.load('confirmed', {}), 1500)}\n\n"
            f"THE NOTES WHERE IT STANDS\n" + "\n\n".join(f"%%% page {pg['n']}\n{fit(pg['text'], 5000, 'head')}" for pg in near)
            + f"\n\nTHE {x['kind'].upper()} AS WRITTEN\n{x['statement']}\n\nYOUR QUESTION\n{x['question']}\n\nTHE AUTHOR'S ANSWER\n{answer}")
    res = run.ask("reason", prompts.STATEMENT_ANSWER, user, max_out=1500, effort="high", label=f"Rewrite {iid} with your answer")
    new = clean_fragment(extract_tag(res.text, "statement"))
    if not new:
        raise ValueError("The statement could not be rewritten this time. Try again, or edit it yourself.")
    again = _none(extract_tag(res.text, "question"))

    def change(y: dict, S: dict) -> None:
        y.setdefault("asked", []).append({"question": y["question"], "answer": answer})
        y["statement"], y["answer"] = new, answer
        y["question"], y["settled"] = (again, False) if again else ("", True)
        y["changed"] = changed_formulas(y["original"], new)
    return _save_item(p, iid, change)


def open_questions(p: Project) -> list[str]:
    return [x["id"] for x in (p.load("statements") or {}).get("items", [])
            if not x.get("settled") and x.get("treatment") != "omit"]


def submit_statements(p: Project, data: dict) -> dict:
    """Gate: the author's statements and the kind of paper. 'not_ready' stops here on purpose."""
    S = p.load("statements") or {}
    if not S.get("items"):
        raise ValueError("The statements have not been written yet.")
    kind = data.get("kind") if data.get("kind") in KINDS else S.get("kind", "article")
    if kind != "not_ready":
        ask = open_questions(p)
        if ask:
            raise ValueError(f"Answer the question about {', '.join(ask)} first, or keep "
                             f"{'that statement' if len(ask) == 1 else 'those statements'} as written.")
        kept = {x["id"] for x in S["items"] if x["treatment"] != "omit"}
        for x in S["items"]:
            used = [i for i in x.get("depends", []) if i not in kept and any(y["id"] == i for y in S["items"])]
            if x["id"] in kept and used and x["kind"] not in ("example", "remark", "question"):
                raise ValueError(f"{x['id']} uses {', '.join(used)}. Leave {x['id']} out as well, or keep {', '.join(used)}.")
    S["kind"], S["note"] = kind, str(data.get("note") or "").strip()[:1500]
    p.save("statements", S)
    # the plan in the form the later steps read it
    scope = {"kind": kind, "note": S["note"], "at": time.time(),
             "results": {x["id"]: ("conjecture" if x["kind"] == "conjecture" and x["treatment"] == "keep" else x["treatment"])
                         for x in S["items"] if x["kind"] not in ("definition",)}}
    p.save("scope", scope)
    if kind == "not_ready":
        p.set_stage("scope", status="waiting", since=time.time(), stopped=True)
        emit(p.id, "log", stage="scope", level="warn",
             message="Stopped here, as you chose. Complete the notes and start a new paper with them; nothing more is "
                     "written for this one unless you choose another kind of paper.")
        return scope
    p.set_stage("scope", status="done", finished=time.time(), stopped=False)
    emit(p.id, "log", stage="scope", level="done", message=f"Statements confirmed. {KIND_TEXT.get(kind, kind)}")
    return scope


def kept_items(p: Project) -> list[dict]:
    return [x for x in (p.load("statements") or {}).get("items", []) if x.get("treatment") != "omit"]


# ------------------------------------------------------------------ 2. structure

def _clean_title(t: str) -> str:
    t = re.sub(r"^\s*(?:section\s*)?\d+[.:)]?\s*", "", t or "", flags=re.I).strip().rstrip(".")
    return "" if t.lower() in ("introduction", "conclusion", "conclusions", "references", "abstract") else t


def order_items(raw: list[dict], items: list[dict]) -> list[dict]:
    """The sections as planned, made safe: every statement in exactly one section, and every statement after all
    the statements it uses (a statement needed earlier is brought forward, into the section that needs it)."""
    by_id = {x["id"]: x for x in items}
    secs = []
    seen: set[str] = set()
    for r in raw:
        title = _clean_title(r.get("title", ""))
        if not title:
            continue
        ids = [i for i in re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", r.get("items", "")) if i in by_id and i not in seen]
        seen.update(ids)
        secs.append({"title": title, "purpose": re.sub(r"\s+", " ", r.get("purpose", "")).strip(), "items": ids})
    if not secs:
        secs = [{"title": "Main results", "purpose": "", "items": []}]
    for x in items:                                  # a statement the plan forgot
        if x["id"] in seen:
            continue
        users = [s for s in secs if any(x["id"] in by_id[i].get("depends", []) for i in s["items"])]
        target = secs[0] if x.get("placement") == "preliminaries" else (users[0] if users else secs[-1])
        target["items"].append(x["id"])
        seen.add(x["id"])
    placed: list[tuple[int, str]] = []
    done: set[str] = set()

    def place(iid: str, si: int, visiting: set) -> None:
        if iid in done or iid in visiting:
            return
        visiting.add(iid)
        for dep in by_id[iid].get("depends", []):
            if dep in by_id and by_id[dep]["kind"] not in ("example", "remark", "question"):
                place(dep, si, visiting)
        done.add(iid)
        placed.append((si, iid))

    for si, s in enumerate(secs):
        for iid in s["items"]:
            place(iid, si, set())
    out = [{"title": s["title"], "purpose": s["purpose"], "items": []} for s in secs]
    for si, iid in placed:
        out[si]["items"].append(iid)
    return [s for s in out if s["items"]]


def numbers(sections: list[dict], items: list[dict]) -> dict[str, str]:
    """The number each statement will carry: one counter per section, shared by all kinds, as in the paper."""
    out = {}
    for si, s in enumerate(sections, 2):
        for k, iid in enumerate(s["items"], 1):
            out[iid] = f"{si}.{k}"
    return out


def _outline_request(items: list[dict], extra: str = "") -> str:
    lines = [f"- {x['id']} [{x['kind']}, {x.get('placement', 'main')}"
             + (f", credited to [{x.get('key')}]" if x.get("treatment") == "credit" else "")
             + (f", uses {', '.join(x['depends'])}" if x.get("depends") else "") + f"]: {fit(x['statement'], 500, 'head')}"
             for x in items]
    return "THE STATEMENTS\n" + "\n".join(lines) + extra


def prepare_outline(run: Run) -> None:
    p = run.p
    items = kept_items(p)
    if not items:
        raise ValueError("Every statement was left out, so there is no paper to structure.")
    sig = _sig([(x["id"], x["kind"], x["statement"], x.get("depends"), x.get("placement"), x.get("treatment")) for x in items])
    O = p.load("outline") or {}
    if O.get("sig") == sig and O.get("sections"):
        return
    run.progress(0, 1, "putting the statements in order")
    res = run.ask("reason", prompts.OUTLINE, f"FIELD\n{run.field}\n\n" + _outline_request(items), max_out=1500, effort="high",
                  label="Plan the structure")
    secs = order_items(fields(res.text, "section", ("title", "items", "purpose")), items)
    p.save("outline", {"sig": sig, "sections": secs, "model": res.model, "leave_out": leave_out(res.text, items)})
    run.progress(1, 1, "structure planned")
    run.log("Structure: " + "; ".join(f"{i}. {s['title']}" for i, s in enumerate(secs, 2)) + ".")


def leave_out(text: str, items: list[dict]) -> list[dict]:
    """The statements the structure suggests leaving out, with the reason. Never a main result."""
    ids = {x["id"]: x for x in items}
    out = []
    for line in parse_lines(extract_tag(text, "leave_out")):
        iid, _, why = line.partition("||")
        iid = re.sub(r"[^A-Za-z0-9]", "", iid)
        if iid in ids and ids[iid].get("role") != "main" and _none(why) and not any(o["id"] == iid for o in out):
            out.append({"id": iid, "why": why.strip()})
    return out


def drop_from_outline(p: Project, iid: str) -> dict:
    """The author agrees to leave a statement out at the structure step: it goes from the structure, and the
    statements that listed it as used are told they do without it (the proofs are written without it)."""
    items = kept_items(p)
    x = next((y for y in items if y["id"] == iid), None)
    if not x:
        raise ValueError("This statement is not in the paper.")
    if x.get("role") == "main":
        raise ValueError("A main result cannot be left out here; change it at the statements step.")

    def change(y: dict, S: dict) -> None:
        y["treatment"], y["cut"] = "omit", y.get("cut") or "left out at the structure step"
        for z in S["items"]:
            if iid in (z.get("depends") or []):
                z["depends"] = [d for d in z["depends"] if d != iid]
    _save_item(p, iid, change)
    O = p.load("outline") or {}
    for s in O.get("sections") or []:
        s["items"] = [i for i in s["items"] if i != iid]
    O["sections"] = [s for s in O.get("sections") or [] if s["items"]]
    O["leave_out"] = [o for o in O.get("leave_out") or [] if o["id"] != iid]
    p.save("outline", O)
    return O


def replan_outline(p: Project, instruction: str) -> dict:
    """The structure planned again with the author's instruction."""
    instruction = (instruction or "").strip()[:2000]
    if not instruction:
        raise ValueError("Say how the structure should change.")
    items = kept_items(p)
    O = p.load("outline") or {}
    now = "\n".join(f"{i}. {s['title']}: {', '.join(s['items'])}" for i, s in enumerate(O.get("sections", []), 2))
    run = Run(p)
    run.stage = "outline"
    res = run.ask("reason", prompts.OUTLINE, f"FIELD\n{run.field}\n\n" + _outline_request(
        items, f"\n\nTHE CURRENT STRUCTURE\n{now}\n\nTHE AUTHOR'S INSTRUCTION (follow it)\n{instruction}"),
        max_out=1500, effort="high", label="Plan the structure with your instruction")
    secs = order_items(fields(res.text, "section", ("title", "items", "purpose")), items)
    O.update(sections=secs, instruction=instruction, model=res.model, leave_out=leave_out(res.text, items))
    p.save("outline", O)
    return O


def edit_outline(p: Project, data: dict) -> dict:
    """The author's own change: a section renamed, or a statement moved up, down or to another section. A move that
    would put a statement before one it uses is refused."""
    O = p.load("outline") or {}
    secs = O.get("sections") or []
    by_id = {x["id"]: x for x in kept_items(p)}
    if data.get("drop"):
        return drop_from_outline(p, str(data["drop"]))
    if not data:
        return O
    if "rename" in data:
        i = int(data.get("section", -1))
        title = _clean_title(str(data["rename"]))[:120]
        if not (0 <= i < len(secs)) or not title:
            raise ValueError("Give the section a title.")
        secs[i]["title"] = title
    if data.get("move"):
        iid, how = data["move"], data.get("to")
        flat = [(si, k) for si, s in enumerate(secs) for k in range(len(s["items"]))]
        pos = next(((si, k) for si, k in flat if secs[si]["items"][k] == iid), None)
        if not pos:
            raise ValueError("This statement is not in the structure.")
        si, k = pos
        secs[si]["items"].pop(k)
        if how == "up":
            if k > 0:
                secs[si]["items"].insert(k - 1, iid)
            elif si > 0:
                secs[si - 1]["items"].append(iid)
            else:
                secs[si]["items"].insert(0, iid)
        elif how == "down":
            if k < len(secs[si]["items"]):
                secs[si]["items"].insert(k + 1, iid)
            elif si + 1 < len(secs):
                secs[si + 1]["items"].insert(0, iid)
            else:
                secs[si]["items"].append(iid)
        else:
            t = int(how)
            if not (0 <= t < len(secs)):
                raise ValueError("Unknown section.")
            secs[t]["items"].append(iid)
        order = [i for s in secs for i in s["items"]]
        for a_i, a in enumerate(order):
            for dep in by_id.get(a, {}).get("depends", []):
                if dep in order and order.index(dep) > a_i and by_id.get(dep, {}).get("kind") not in ("example", "remark", "question"):
                    refuse_move(p, by_id[a], by_id[dep], iid, how)
        O["sections"] = [s for s in secs if s["items"]]
    p.save("outline", O)
    return O


def _named(x: dict, nums: dict) -> str:
    return f"{kind_name(x['kind'])} {nums.get(x['id'], '')}".strip() + f" ({x['id']})"


def refuse_move(p: Project, a: dict, b: dict, iid: str, how) -> None:
    """A move that would put A before B, which A uses, is refused, and the reason is written into the conversation
    about the structure: the author may answer that A does not need B, or give another argument for that step."""
    O = p.load("outline") or {}
    nums = numbers(O.get("sections") or [], kept_items(p))
    where = f" (page {', '.join(map(str, a['pages']))} of your notes)" if a.get("pages") else ""
    text = (f"{_named(a, nums)} uses {_named(b, nums)} in its argument{where}, so {_named(b, nums)} must come before it. "
            f"If it does not really need it, or if you have another argument for that step, say so here: if the argument "
            f"holds, the order is changed and the proof is written with your argument.")
    O.setdefault("chat", []).append({"who": "app", "text": text, "at": time.time(),
                                     "move": {"id": iid, "to": how}, "a": a["id"], "b": b["id"]})
    p.save("outline", O)
    raise ValueError(text)


def talk_outline(p: Project, text: str) -> dict:
    """The conversation about the structure. A reply to a refused move is judged: when the statement does not need
    the one it was said to use, or the author's other argument holds, the dependence goes, the author's argument is
    kept for the writer of that proof, and the move is made. Anything else is an instruction for a new structure."""
    text = (text or "").strip()[:4000]
    if not text:
        raise ValueError("Write your message first.")
    O = p.load("outline") or {}
    chat = O.setdefault("chat", [])
    pending = next((m for m in reversed(chat) if m.get("who") == "app" and m.get("move") and not m.get("resolved")), None)
    chat.append({"who": "you", "text": text, "at": time.time()})
    p.save("outline", O)
    if not pending:
        replan_outline(p, text)
        O = p.load("outline") or {}
        O.setdefault("chat", []).append({"who": "ai", "text": "The structure was planned again with your instruction.", "at": time.time()})
        p.save("outline", O)
        return O
    items = {x["id"]: x for x in kept_items(p)}
    a, b = items.get(pending["a"]), items.get(pending["b"])
    if not a or not b:
        pending["resolved"] = True
        p.save("outline", O)
        return O
    pages, _ = writing_pages(p)
    nums = numbers(O.get("sections") or [], list(items.values()))
    thread = chat[chat.index(pending):-1]
    run = Run(p)
    run.stage = "outline"
    user = (f"STATEMENT A, {_named(a, nums)}\n{a['statement']}\n\nSTATEMENT B, {_named(b, nums)}\n{b['statement']}\n\n"
            f"THE AUTHOR'S ARGUMENT FOR A, FROM THE NOTES\n{author_argument(a, pages, 6000)}\n\n"
            + ("THE EXCHANGE SO FAR\n" + "\n".join(f"{m['who']}: {m['text']}" for m in thread) + "\n\n" if thread else "")
            + f"THE AUTHOR'S REPLY\n{text}")
    res = run.ask("reason", prompts.ARGUE_ORDER.format(field=run.field), user, max_out=1200, effort="high",
                  label=f"Judge your reply about {a['id']} and {b['id']}")
    verdict = (extract_tag(res.text, "verdict") or "needed").strip().lower()
    reply = re.sub(r"\s+", " ", extract_tag(res.text, "reply") or "").strip() or "No clear answer came back; try again."
    instruction = _none(extract_tag(res.text, "instruction"))
    O = p.load("outline") or {}
    chat = O.setdefault("chat", [])
    pend = next((m for m in reversed(chat) if m.get("who") == "app" and m.get("move") and not m.get("resolved")), None)
    if verdict.startswith(("not_used", "avoided")):
        def change(y: dict, S: dict) -> None:
            y["depends"] = [d for d in y.get("depends", []) if d != b["id"]]
            if verdict.startswith("avoided"):
                y["instruction"] = (y.get("instruction", "") + "\n" + (instruction or text)).strip()
        _save_item(p, a["id"], change)
        if pend:
            pend["resolved"] = True
        chat.append({"who": "ai", "text": reply, "at": time.time(), "verdict": verdict})
        p.save("outline", O)
        try:
            edit_outline(p, {"move": pend["move"]["id"], "to": pend["move"]["to"]} if pend else {})
            O = p.load("outline") or {}
            O.setdefault("chat", []).append({"who": "ai", "text": "The order was changed."
                                             + (" The proof will be written with your argument." if verdict.startswith("avoided") else ""),
                                             "at": time.time()})
            p.save("outline", O)
        except ValueError:
            pass                                   # another statement still stands in the way: said in the conversation
        return p.load("outline") or {}
    chat.append({"who": "ai", "text": reply, "at": time.time(), "verdict": "needed"})
    p.save("outline", O)
    return O


def dismiss_refusal(p: Project) -> dict:
    """The author lets a refused move be: the conversation goes back to the structure as a whole."""
    O = p.load("outline") or {}
    for m in O.get("chat") or []:
        if m.get("who") == "app" and m.get("move"):
            m["resolved"] = True
    p.save("outline", O)
    return O


def submit_outline(p: Project) -> dict:
    O = p.load("outline") or {}
    if not O.get("sections"):
        raise ValueError("The structure has not been planned yet.")
    O["approved"] = time.time()
    p.save("outline", O)
    p.set_stage("outline", status="done", finished=time.time())
    emit(p.id, "log", stage="outline", level="done", message="Structure confirmed.")
    return O


def ordered(p: Project) -> tuple[list[dict], list[dict], dict[str, str]]:
    """The kept statements in the order of the paper, the sections, and the number of each statement."""
    items = {x["id"]: x for x in kept_items(p)}
    secs = (p.load("outline") or {}).get("sections") or []
    seq = [items[i] for s in secs for i in s["items"] if i in items]
    return seq, secs, numbers(secs, list(items.values()))


# ------------------------------------------------------------------ 3. proofs

def credited(x: dict) -> bool:
    return x.get("treatment") == "credit" and bool(x.get("key"))


def needs_text(x: dict) -> bool:
    """A proof is written for a result presented as the author's; a short text for an example or a remark."""
    if credited(x) or x.get("treatment") in ("conjecture", "omit") or x["kind"] in ("conjecture", "definition", "question"):
        return False
    return x["kind"] in PROVED + EXPLAINED


def _statement_line(x: dict, num: str) -> str:
    head = f"{kind_name(x['kind'])} {num}" + (f" ({x['name']})" if x.get("name") else "")
    return f"{head} [label {label_of(x)}]: {x['statement']}"


def author_argument(x: dict, pages: list[dict], room: int) -> str:
    near = [pg for pg in pages if pg["n"] in (x.get("pages") or [])]
    lead = f"The author states it on page {x['pages'][0]} as: {x['words']}\n\n" if x.get("words") and x.get("pages") else ""
    if not near:
        return lead + "(The notes give no argument for it.)"
    return lead + fit("\n\n".join(f"%%% page {pg['n']}\n{pg['text']}" for pg in near), room, "head")


def proof_context(p: Project, run: Run) -> dict:
    seq, secs, nums = ordered(p)
    pages, figs = writing_pages(p)
    d = p.load("dossier") or {}
    return {"seq": seq, "nums": nums, "pages": pages, "figs": figs, "library": {x["key"]: x for x in p.load("library", [])},
            "sources": sources_text(p), "notation": notation_text(p.load("confirmed", {}), 1500),
            "setting": "\n".join(x for x in (d.get("setting", ""), d.get("notation_choice", "")) if x)}


def _before(ctx: dict, x: dict) -> list[dict]:
    seq = ctx["seq"]
    return seq[:next(i for i, y in enumerate(seq) if y["id"] == x["id"])]


def write_request(ctx: dict, x: dict, room: int, previous: dict | None = None, feedback: str = "", comment: str = "") -> str:
    before = _before(ctx, x)
    deps = set(x.get("depends") or [])
    listing = "\n".join(("* " if y["id"] in deps else "- ") + _statement_line(y, ctx["nums"].get(y["id"], "?"))
                        + (f" (from [{y['key']}])" if y.get("treatment") == "credit" else "")
                        for y in before if y["kind"] not in ("question",))
    listing = fit(listing, max(1500, int(room * 0.25)), "head")
    assess = {"complete": "the notes give an argument for every step", "gap": "the notes' argument has a gap",
              "sketch": "the notes only sketch the proof", "breaks off": "the notes' argument breaks off",
              "none": "the notes give no proof"}.get(x.get("proof", "complete"), "")
    parts = [f"FIELD\n{ctx.get('field', '')}",
             f"SETTING AND NOTATION OF THE PAPER\n{fit(ctx['setting'], 1500, 'head') or '(none)'}\n\nCONFIRMED NOTATION\n{ctx['notation']}",
             f"STATEMENTS PRESENTED BEFORE THIS ONE (the proof may use these; * marks those the notes say it uses)\n{listing or '(none)'}",
             f"THE STATEMENT TO PROVE\n{_statement_line(x, ctx['nums'].get(x['id'], '?'))}",
             f"THE AUTHOR'S ARGUMENT, FROM THE NOTES\n{author_argument(x, ctx['pages'], max(2500, int(room * 0.35)))}",
             f"WHAT THE ASSESSMENT FOUND\n{assess}" + (f": {x['where']}" if x.get("where") else ""),
             f"PAPERS YOU MAY CITE\n{fit(ctx['sources'], max(800, int(room * 0.12)), 'head') or '(none)'}"]
    if previous:
        parts.append(f"YOUR PREVIOUS PROOF\n{previous.get('text', '')}")
    if feedback:
        parts.append(f"WHAT THE REFEREE FOUND IN IT (correct these mistakes and do not repeat them)\n{feedback}")
    if comment:
        parts.append(f"THE AUTHOR'S INSTRUCTION ABOUT THIS PROOF (follow it)\n{comment}")
    return "\n\n".join(parts)


def screen_proof(text: str, x: dict, ctx: dict, log: list[str]) -> tuple[str, list[str]]:
    """What the program checks in a written proof before a model reads it: well-formed LaTeX, no statement of its own,
    no reference to a statement presented later or not at all, citations of known papers only."""
    problems: list[str] = []
    text = re.sub(r"\\begin\{proof\}(\[[^\]]*\])?|\\end\{proof\}", "", clean_fragment(text)).strip()
    text = strip_explanations(text)                      # a drawing that only explains the argument is never printed
    text = re.sub(r"\[FIGURE\s*:[^\]]*\]\s*", "", text)      # nor one the author did not keep
    text = re.sub(r"\[FIGURE\s+(f\d+)\s*:[^\]]*\]", r"\\insertfigure{\1}", text)
    text = fix_keys(text, ctx["library"], log)
    if leaks(text):
        problems.append("The proof contains your own reasoning about the task instead of paper text.")
    if check_balance(text):
        problems.append("The LaTeX is not well formed: " + "; ".join(check_balance(text)) + ".")
    envs = set(re.findall(r"\\begin\{([a-z]+)\*?\}", text)) & set(paperlib.THEOREM_ENVS)
    if envs or re.search(r"\\(?:sub)*section\*?\{", text):
        problems.append("The proof contains a statement environment or a section heading; write the proof only.")
    allowed = {label_of(y) for y in _before(ctx, x)}
    local = set(re.findall(r"\\label\{([^{}]+)\}", text))
    later = {label_of(y): y for y in ctx["seq"]}
    for ref in re.findall(r"\\(?:eq|c)?ref\{([^{}]+)\}", text):
        if ref in allowed or ref in local or ref.startswith(("eq", "fig:")):
            continue
        if ref in later:
            problems.append(f"It uses {kind_name(later[ref]['kind'])} {ctx['nums'].get(later[ref]['id'], '')} "
                            f"({ref}), which is presented later; it may use only statements presented before it.")
        else:
            problems.append(f"It refers to \\ref{{{ref}}}, which is not a statement of the paper.")
    if len(re.sub(r"\s+", " ", text)) < 40 and x["kind"] in PROVED:
        problems.append("The proof is empty.")
    return text, problems


def write_once(run: Run, ctx: dict, x: dict, previous: dict | None = None, feedback: str = "", comment: str = "") -> dict:
    explained = x["kind"] in EXPLAINED
    system = (prompts.WRITE_EXAMPLE.format(field=run.field, kind=x["kind"]) if explained
              else prompts.WRITE_PROOF.format(field=run.field))
    out = 1500 if explained else 5000
    room = _room("reason", system, out)
    user = write_request({**ctx, "field": run.field}, x, room, previous, feedback, comment)
    num = ctx["nums"].get(x["id"], x["id"])
    res = run.ask("reason", system, fit(user, room, "middle"), max_out=out, effort="high", paper=True,
                  label=f"Write the {'text' if explained else 'proof'} of {kind_name(x['kind'])} {num} ({x['id']})")
    log: list[str] = []
    raw = extract_tag(res.text, "body" if explained else "proof") or ("" if explained else res.text)
    text, problems = screen_proof(raw, x, ctx, log)
    if explained:
        problems = [q for q in problems if "empty" not in q]
    return {"text": text, "added": [a for a in parse_lines(extract_tag(res.text, "added")) if _none(a)],
            "uses": re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", extract_tag(res.text, "uses")),
            "doubt": _none(extract_tag(res.text, "doubt")), "problems": problems, "notes": log, "model": res.model}


def parse_check(text: str) -> dict:
    verdict = (extract_tag(text, "verdict") or "").strip().lower()
    verdict = next((v for v in ("accept", "review", "rewrite", "statement") if verdict.startswith(v)), "")
    added = []
    for line in parse_lines(extract_tag(text, "added")):
        if not _none(line):
            continue
        what, _, judged = line.partition("||")
        judged = judged.strip() or "correct"
        added.append({"what": what.strip(), "ok": judged.lower().startswith("correct"), "note": judged})
    stmt = (extract_tag(text, "statement") or "holds").strip()
    proof = (extract_tag(text, "proof") or "correct").strip()
    return {"follows": (extract_tag(text, "follows") or "yes").strip().lower().split()[0] if extract_tag(text, "follows").strip() else "yes",
            "departures": [d for d in parse_lines(extract_tag(text, "departures")) if _none(d)],
            "added": added, "author": (extract_tag(text, "author") or "complete").strip(),
            "proof": proof, "statement": stmt, "verdict": verdict, "fix": _none(extract_tag(text, "fix"))}


def check_once(run: Run, ctx: dict, x: dict, w: dict) -> dict:
    system = prompts.CHECK_PROOF.format(field=run.field)
    room = _room("reason", system, 1800)
    deps = [y for y in _before(ctx, x)]
    listing = fit("\n".join("- " + _statement_line(y, ctx["nums"].get(y["id"], "?")) for y in deps), max(1200, int(room * 0.2)), "head")
    user = (f"THE STATEMENT\n{_statement_line(x, ctx['nums'].get(x['id'], '?'))}\n\n"
            f"STATEMENTS THE PROOF MAY USE\n{listing or '(none)'}\n\n"
            f"THE AUTHOR'S ARGUMENT, FROM THE NOTES\n{author_argument(x, ctx['pages'], max(2500, int(room * 0.3)))}\n\n"
            f"THE WRITTEN PROOF\n{w['text']}\n\n"
            f"WHAT THE WRITER SAYS IT ADDED\n" + ("\n".join(f"- {a}" for a in w["added"]) or "nothing")
            + (f"\n\nTHE WRITER'S DOUBT\n{w['doubt']}" if w.get("doubt") else ""))
    res = run.ask("reason", system, fit(user, room, "middle"), max_out=1800, effort="high", temperature=0.0,
                  label=f"Check the proof of {kind_name(x['kind'])} {ctx['nums'].get(x['id'], x['id'])} ({x['id']})")
    c = parse_check(res.text)
    c["model"] = res.model
    if not c["verdict"]:                    # an answer without a verdict: judged from what it says
        wrong = c["proof"].lower().startswith("wrong")
        bad_stmt = c["statement"].lower().startswith(("false", "doubtful"))
        c["verdict"] = "statement" if bad_stmt and wrong else "rewrite" if wrong else \
            "review" if not c["author"].lower().startswith("complete") else "accept"
    return c


def feedback_of(c: dict, problems: list[str]) -> str:
    lines = list(problems)
    if c.get("proof", "").lower().startswith("wrong"):
        lines.append("The proof is wrong: " + c["proof"].split(":", 1)[-1].strip())
    lines += [f"An added argument is wrong: {a['what']}: {a['note']}" for a in c.get("added", []) if not a["ok"]]
    if c.get("follows") == "no" and c.get("verdict") == "rewrite":
        lines.append("The proof does not follow the author's argument, although that argument works: follow it.")
    if c.get("fix"):
        lines.append("What to change: " + c["fix"])
    return "\n".join(f"- {s}" for s in lines if s.strip())


MAX_TRIES = 3


def prove(run: Run, ctx: dict, x: dict, comment: str = "", previous: dict | None = None) -> dict:
    """One proof written, checked, and written again with the referee's findings until it passes, at most
    MAX_TRIES times. Returns the record kept for it."""
    tries: list[dict] = []
    feedback, prev = "", previous
    rec: dict = {}
    for attempt in range(MAX_TRIES):
        w = write_once(run, ctx, x, prev, feedback, comment)
        if x["kind"] in EXPLAINED:
            return {"text": w["text"], "added": [], "check": None, "status": "accept", "tries": [], "model": w["model"],
                    "notes": w["notes"]}
        if w["problems"] and not w["text"]:
            c = {"verdict": "rewrite", "proof": "wrong: empty", "added": [], "fix": "", "follows": "yes",
                 "departures": [], "author": "", "statement": "holds"}
        else:
            c = check_once(run, ctx, x, w)
        if w["problems"] and c["verdict"] in ("accept", "review"):
            c["verdict"] = "rewrite"         # the program's own checks come first
        rec = {"text": w["text"], "added": w["added"], "uses": w["uses"], "doubt": w["doubt"], "check": c,
               "problems": w["problems"], "model": w["model"], "notes": w["notes"]}
        tries.append({"verdict": c["verdict"], "feedback": feedback_of(c, w["problems"])})
        if c["verdict"] in ("accept", "review", "statement"):
            break
        feedback, prev = feedback_of(c, w["problems"]), {"text": w["text"]}
    status = rec["check"]["verdict"] if rec["check"]["verdict"] in ("accept", "review", "statement") else "failed"
    return {**rec, "status": status, "tries": tries, "comment": comment, "at": time.time()}


def proof_key(ctx: dict, x: dict) -> str:
    """What a proof is written from: its statement, the statements before it, and the author's pages."""
    before = [(y["id"], y["kind"], y["statement"]) for y in _before(ctx, x)]
    pages = [pg["text"] for pg in ctx["pages"] if pg["n"] in (x.get("pages") or [])]
    return _sig(x["kind"], x["statement"], x.get("name"), x.get("instruction", ""), before, pages)


def stage_proofs(run: Run) -> None:
    p = run.p
    ctx = proof_context(p, run)
    todo = [x for x in ctx["seq"] if needs_text(x)]
    P = p.load("proofs") or {"items": {}}
    keys = {x["id"]: proof_key(ctx, x) for x in todo}
    # pages added to notes that already have a paper: a proof written before from exactly the same statements and
    # pages is kept. Otherwise (a step redone, the paper written again) every proof is written anew.
    cache = (p.load("proof_cache", {}) or {}) if p.state.get("incremental") else {}
    for x in todo:
        hit = cache.get(keys[x["id"]])
        if hit and (P["items"].get(x["id"]) or {}).get("key") != keys[x["id"]]:
            P["items"][x["id"]] = {**hit, "kept": True}
    fresh = [x for x in todo if (P["items"].get(x["id"]) or {}).get("key") != keys[x["id"]]]
    P["items"] = {k: v for k, v in P["items"].items() if k in keys}
    if len(todo) > len(fresh):
        run.log(f"{len(todo) - len(fresh)} proof{'s' if len(todo) - len(fresh) != 1 else ''} written before from the same "
                "statements and pages kept.")
    done = [len(todo) - len(fresh)]
    run.progress(done[0], len(todo), "writing each proof on its own")

    def work(x: dict) -> None:
        rec = prove(run, ctx, x, comment=x.get("instruction", ""))
        rec["key"] = keys[x["id"]]
        with _lock:
            cur = p.load("proofs") or {"items": {}}
            cur["items"][x["id"]] = rec
            p.save("proofs", cur)
            if rec["status"] in ("accept", "review"):
                c = p.load("proof_cache", {}) or {}
                c[rec["key"]] = rec
                p.save("proof_cache", dict(list(c.items())[-300:]))
            done[0] += 1
            run.progress(done[0], len(todo), f"{kind_name(x['kind'])} {ctx['nums'].get(x['id'], '')}: "
                         + {"accept": "written and checked", "review": "written; marked for you to read",
                            "statement": "the statement may be false", "failed": "could not be written correctly"}[rec["status"]])

    p.save("proofs", P)
    _side_by_side(run, "reason", False, fresh, work)
    P = p.load("proofs") or {"items": {}}
    counts = {s: sum(1 for v in P["items"].values() if v["status"] == s) for s in ("accept", "review", "statement", "failed")}
    run.log(f"Proofs: {counts['accept']} checked and accepted, {counts['review']} marked for you to read, "
            f"{counts['statement']} with a statement that may be false, {counts['failed']} that could not be written correctly.",
            "warn" if counts["statement"] or counts["failed"] else "info")


def open_proof_problems(p: Project) -> list[str]:
    """Proofs the author must decide on before the paper is put together: a statement that may be false, or a proof
    that could not be written correctly, not yet settled."""
    P = p.load("proofs") or {"items": {}}
    kept = {x["id"] for x in kept_items(p) if needs_text(x)}
    return [k for k, v in P["items"].items() if k in kept and v["status"] in ("statement", "failed") and not v.get("settled")]


def rewrite_proof(p: Project, iid: str, comment: str = "", statement: str | None = None) -> dict:
    """The author's instruction carried out on one proof (or a new statement given for it): written and checked again."""
    comment = (comment or "").strip()[:3000]
    if statement is not None:
        statement = statement.strip()
        if not statement:
            raise ValueError("Write the statement first.")
        edit_statement(p, iid, {"statement": statement})
    if not comment and statement is None:
        raise ValueError("Say what should change in this proof.")
    run = Run(p)
    run.stage = "proofcheck"
    ctx = proof_context(p, run)
    x = next((y for y in ctx["seq"] if y["id"] == iid), None)
    if not x or not needs_text(x):
        raise ValueError("This statement has no proof to write.")
    old = (p.load("proofs") or {"items": {}})["items"].get(iid)
    full = "\n".join(s for s in (x.get("instruction", ""), comment) if s)
    rec = prove(run, ctx, x, comment=full, previous=old if (comment and old) else None)
    rec["key"] = proof_key(ctx, x)
    rec["history"] = ((old or {}).get("history") or []) + ([{k: old.get(k) for k in ("text", "status", "comment")}] if old else [])
    with _lock:
        P = p.load("proofs") or {"items": {}}
        P["items"][iid] = rec
        p.save("proofs", P)
    return rec


def decide_proof(p: Project, iid: str, action: str) -> dict:
    """The author's decision on a proof put to them: keep it as it is, state the result as a conjecture, or leave the
    result out."""
    with _lock:
        P = p.load("proofs") or {"items": {}}
        rec = P["items"].get(iid)
        if not rec:
            raise ValueError("There is no proof for this statement.")
        if action == "keep":
            rec["settled"] = "kept"
        elif action in ("conjecture", "omit"):
            rec["settled"] = action
        else:
            raise ValueError("Unknown decision.")
        p.save("proofs", P)
    if action == "conjecture":
        edit_statement(p, iid, {"kind": "conjecture"})
    elif action == "omit":
        users = [x["id"] for x in kept_items(p) if iid in (x.get("depends") or []) and x["id"] != iid
                 and x["kind"] not in ("example", "remark", "question")]
        if users:
            with _lock:
                P = p.load("proofs") or {"items": {}}
                P["items"][iid].pop("settled", None)
                p.save("proofs", P)
            raise ValueError(f"{', '.join(users)} use{'s' if len(users) == 1 else ''} {iid}. Decide on "
                             f"{'it' if len(users) == 1 else 'them'} first, or keep {iid} as a conjecture.")
        edit_statement(p, iid, {"treatment": "omit"})
    return rec


def submit_proofs(p: Project) -> None:
    left = open_proof_problems(p)
    if left:
        raise ValueError(f"Decide first on {', '.join(left)}: the statement may be false, or its proof could not be "
                         "written correctly.")
    p.save("proofs_ok", {"at": time.time()})
    p.set_stage("proofcheck", status="done", finished=time.time())
    emit(p.id, "log", stage="proofcheck", level="done", message="Proofs approved.")


# ------------------------------------------------------------------ 4. the paper

def item_latex(x: dict, num: str, proof: dict | None) -> str:
    env = x["kind"] if x["kind"] in paperlib.THEOREM_ENVS else "remark"
    head = []
    if x.get("name"):
        head.append(x["name"])
    if credited(x):
        head.append(f"\\cite{{{x['key']}}}")
    if x.get("treatment") == "conjecture" and env in PROVED:
        env = "conjecture"
    opt = f"[{', '.join(head)}]" if head else ""
    out = f"\\begin{{{env}}}{opt}\\label{{{label_of(x)}}}\n{x['statement'].strip()}\n"
    if x["kind"] in EXPLAINED and proof and proof.get("text"):
        out += "\n" + proof["text"].strip() + "\n"
    out += f"\\end{{{env}}}"
    if env in PROVED and proof and proof.get("text") and not credited(x) and x.get("treatment") != "conjecture":
        out += f"\n\n\\begin{{proof}}\n{proof['text'].strip()}\n\\end{{proof}}"
    return out


def section_parts(secs: list[dict], seq: list[dict], nums: dict, proofs: dict) -> list[list[str]]:
    """Each statement with its proof, in the order of its section. A line "%%% page N" before it says which page of
    the notes it comes from: the paper's passages stay linked to the author's pages."""
    by_id = {x["id"]: x for x in seq}

    def one(x: dict) -> str:
        mark = f"%%% page {x['pages'][0]}\n" if x.get("pages") else ""
        return mark + item_latex(x, nums.get(x["id"], ""), proofs.get(x["id"]))
    return [[one(by_id[i]) for i in s["items"] if i in by_id] for s in secs]


def screen_slot(text: str, library: dict, log: list[str]) -> str:
    """Text between results: words only. Anything that would add mathematics of its own is not used."""
    t = clean_fragment(text or "").strip()
    if not t or leaks(t) or check_balance(t):
        return ""
    if re.search(r"\\begin\{|\\section|\\label|\\\[|\$\$|\\insertfigure", t):
        return ""
    return fix_keys(t, library, log)


def _slots(parts: list[str]) -> str:
    out = []
    for k, part in enumerate(parts, 1):
        out.append(f"[[SLOT {k}]]\n\n{part}")
    out.append(f"[[SLOT {len(parts) + 1}]]")
    return "\n\n".join(out)


def stage_draft(run: Run) -> None:
    """The paper put together: statements and proofs exactly as approved, with the text that joins them written in
    its own places, then the introduction and the abstract from the finished paper."""
    p = run.p
    seq, secs, nums = ordered(p)
    if not seq:
        raise ValueError("There are no statements to put in the paper.")
    proofs = (p.load("proofs") or {"items": {}})["items"]
    pages, figs = writing_pages(p)
    library = {x["key"]: x for x in p.load("library", [])}
    parts = section_parts(secs, seq, nums, proofs)
    plan_lines = "\n".join(f"{i}. {s['title']}: " + ", ".join(f"{kind_name(next(x['kind'] for x in seq if x['id'] == j))} {nums.get(j, '')}"
                                                            for j in s["items"]) for i, s in enumerate(secs, 2))
    partial = p.load("connect_partial") or {}
    log: list[str] = []
    run.progress(0, 3, "writing the text that joins the results")

    def connect(si: int) -> list[str]:
        sig = _sig(secs[si]["title"], parts[si])
        hit = partial.get(str(si))
        if hit and hit.get("sig") == sig:
            return hit["slots"]
        system = prompts.CONNECT.format()
        user = (f"THE PLAN OF THE PAPER\n1. Introduction\n{plan_lines}\n\nTHE SECTION: Section {si + 2}, "
                f"\"{secs[si]['title']}\"" + (f" ({secs[si]['purpose']})" if secs[si].get("purpose") else "")
                + f"\n\n{_slots(parts[si])}")
        res = run.ask("writer", system, fit(user, _room("writer", system, 2500), "middle"), max_out=2500, effort="medium",
                      temperature=0.3, label=f"Join the results of section {si + 2}")
        got = {int(k): v for k, v in re.findall(r'<slot\s+k\s*=\s*"?(\d+)"?\s*>(.*?)</slot>', res.text, flags=re.S)}
        slots = [screen_slot(got.get(k, ""), library, log) for k in range(1, len(parts[si]) + 2)]
        with _lock:
            cur = p.load("connect_partial") or {}
            cur[str(si)] = {"sig": sig, "slots": slots}
            p.save("connect_partial", cur)
        return slots

    slots = _side_by_side(run, "writer", False, list(range(len(secs))), connect)
    body_parts = []
    for si, s in enumerate(secs):
        chunk = [f"\\section{{{s['title']}}}"]
        for k, part in enumerate(parts[si]):
            if slots[si][k]:
                chunk.append(slots[si][k])
            chunk.append(part)
        if slots[si][-1]:
            chunk.append(slots[si][-1])
        body_parts.append("\n\n".join(chunk))
    body = "\n\n".join(body_parts)
    body = place_figures(body, seq, figs, secs)
    body = strip_explanations(body)
    run.progress(1, 3, "writing the introduction from the finished paper")

    # the introduction and the organisation of the paper
    S = p.load("statements") or {}
    kind = S.get("kind", "article")
    system = prompts.INTRO_FINAL.format()
    room = _room("writer", system, 2400)
    stmts = "\n".join(_statement_line(x, nums.get(x["id"], "")) + (f" (credited to [{x['key']}])" if x.get("treatment") == "credit" else "")
                      + (" (MAIN RESULT)" if x.get("role") == "main" and x["kind"] in PROVED else "") for x in seq)
    openings = "\n".join(f"Section {i}, {s['title']}: {fit(slots[i - 2][0], 500, 'head') or s.get('purpose', '')}"
                         for i, s in enumerate(secs, 2))
    d = p.load("dossier") or {}
    user = (f"FIELD\n{run.field}\n\nKIND OF PAPER\n{KIND_TEXT.get(kind, kind)}" + (f"\nThe author's note: {S['note']}" if S.get("note") else "")
            + f"\n\nTHE PROBLEM\n{d.get('problem', '')}\n\nTHE STATEMENTS OF THE PAPER, IN ORDER\n{fit(stmts, int(room * 0.4), 'head')}\n\n"
            f"THE SECTIONS\n{openings}\n\nRESEARCH CONTEXT BRIEF\n{fit((p.load('brief') or {}).get('text', ''), int(room * 0.12), 'head') or '(none)'}\n\n"
            f"PAPERS YOU MAY CITE\n{fit(sources_text(p), int(room * 0.25), 'head') or '(none: write the introduction without citations)'}")
    intro, notes = "", []
    for attempt in range(2):
        res = run.ask("writer", system, user, max_out=2400, effort="high", temperature=0.3,
                      label="Write introduction" + (" (retry)" if attempt else ""))
        notes = []
        intro = clean_fragment(extract_tag(res.text, "intro") or res.text)
        intro = re.sub(r"\\section\*?\{[^{}]*\}\s*", "", intro).strip()
        intro = fix_keys(drop_unsourced(intro, library, notes), library, notes)
        org = organisation(extract_tag(res.text, "organisation"), [{"title": s["title"]} for s in secs])
        if len(intro) >= 500:
            break
    if len(intro) < 200:
        raise LLMError("The introduction came back empty.", kind="transient", retry_at=time.time() + 60)
    for n in notes + log:
        run.log(n, "warn")
    p.save("intro", {"text": intro, "notes": notes, "model": res.model})

    blocks = paperlib.parse_body(paperlib.close_open(body))
    from .writing import ensure_labels
    ensure_labels(blocks)
    plan = [{"title": s["title"], "purpose": s.get("purpose", ""), "items": s["items"],
             "pages": sorted({n for i in s["items"] for x in seq if x["id"] == i for n in x.get("pages") or []}) or [0]}
            for s in secs]
    p.save("plan", {"sections": plan, "organisation": org})
    p.save("body", {"blocks": blocks, "organisation": org, "figures": figs})
    from .pipeline import audit_pages
    p.save("audit", fidelity.audit(audit_pages(approved_source(p)), body))

    # the abstract, from the finished paper
    run.progress(2, 3, "writing the abstract")
    from . import journals
    prof = journals.profile(p.state.get("meta", {}).get("journal"))
    system = prompts.ABSTRACT.format(field=run.field, length=journals.length_phrase(prof).capitalize(),
                                     keywords=journals.keywords_phrase(prof))
    room = _room("writer", system, 900)
    from .writing import planned_dossier
    user = (f"DOSSIER\n{dossier_text(planned_dossier(p), max(1200, int(room * 0.3)))}\n\nINTRODUCTION\n{fit(intro, int(room * 0.25), 'head')}\n\n"
            f"BODY\n{fit(body, int(room * 0.4), 'middle')}")
    res = run.ask("writer", system, user, max_out=900, effort="medium", label="Write abstract")
    abstract = re.sub(r"\\cite[tp]?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}", "", clean_fragment(extract_tag(res.text, "abstract"))).strip()
    if len(abstract) < 80:
        raise LLMError("The abstract came back empty.", kind="transient", retry_at=time.time() + 60)
    p.save("abstract_draft", {"abstract": abstract, "keywords": parse_lines(extract_tag(res.text, "keywords").replace(",", "\n"))[:12],
                              "msc": [m for m in (re.sub(r"[^0-9A-Za-z\-]", "", y) for y in extract_tag(res.text, "msc").split(","))
                                      if re.match(r"^\d{2}[A-Z\-]", m)][:4], "model": res.model})
    run.progress(3, 3, "first draft ready")
    n_proofs = sum(1 for b in blocks if b["type"] == "proof")
    run.log(f"First draft: {len(secs)} sections, {len(seq)} statements, {n_proofs} proofs; introduction and abstract written.")


def place_figures(body: str, seq: list[dict], figs: dict, secs: list[dict]) -> str:
    """Every figure the author kept appears once: where a proof put it, or else after the statement whose pages it
    is drawn on, or at the end of the first section."""
    seen: set[str] = set()

    def keep(m: re.Match) -> str:
        fid = m.group(1)
        if fid not in figs or fid in seen:
            return ""
        seen.add(fid)
        return m.group(0)
    body = paperlib._FIG_RE.sub(keep, body)
    body = re.sub(r"\[FIGURE\s+f\d+\s*:[^\]]*\]", "", body)
    for fid, f in figs.items():
        if fid in seen:
            continue
        owner = next((x for x in seq if f.get("page") in (x.get("pages") or [])), None)
        end = None
        if owner:
            m = re.search(r"\\label\{" + re.escape(label_of(owner)) + r"\}", body)
            if m:
                close = re.search(r"\\end\{proof\}|\\end\{[a-z]+\}", body[m.end():])
                after = body[m.end() + close.end():] if close else ""
                nxt_proof = re.match(r"\s*\\begin\{proof\}.*?\\end\{proof\}", after, flags=re.S)
                end = m.end() + close.end() + (nxt_proof.end() if nxt_proof else 0) if close else None
        if end is None:
            nxt = re.search(r"\\section\{", body[10:])
            end = nxt.start() + 10 if nxt else len(body)
        body = body[:end] + f"\n\n\\insertfigure{{{fid}}}\n\n" + body[end:]
        seen.add(fid)
    return body


def submit_draft(p: Project, data: dict) -> None:
    """Gate: the author has read the first draft; the introduction or the abstract may have been edited."""
    edit_draft(p, data)
    p.save("draft_ok", {"at": time.time()})
    p.set_stage("draftcheck", status="done", finished=time.time())
    emit(p.id, "log", stage="draftcheck", level="done", message="First draft approved.")


def edit_draft(p: Project, data: dict) -> None:
    if isinstance(data.get("intro"), str) and data["intro"].strip():
        cur = p.load("intro") or {}
        if data["intro"].strip() != cur.get("text", "").strip():
            p.save("intro", {**cur, "text": data["intro"].strip(), "edited": True})
    if isinstance(data.get("abstract"), str) and data["abstract"].strip():
        cur = p.load("abstract_draft") or {}
        if data["abstract"].strip() != cur.get("abstract", "").strip():
            p.save("abstract_draft", {**cur, "abstract": data["abstract"].strip(), "edited": True})


def approved_source(p: Project) -> list[dict]:
    """What the author approved for the paper, in the form the formula comparison reads: each statement with its
    proof, under the page of the notes it comes from. Later changes to the paper are compared with this, not with
    the notes, whose lines are cut and rewritten on purpose."""
    if not (p.load("statements") or {}).get("items"):
        return []
    P = (p.load("proofs") or {}).get("items", {})
    out = []
    for x in kept_items(p):
        text = x["statement"]
        if x["id"] in P and needs_text(x) and P[x["id"]].get("text"):
            text += "\n\n" + P[x["id"]]["text"]
        out.append({"n": (x.get("pages") or [0])[0], "text": text})
    return out


# ------------------------------------------------------------------ for the screens

def view(p: Project) -> dict:
    """What the screens of these steps show: the statements with their numbers and tabs, the structure, the proofs."""
    S = p.load("statements")
    if not S:
        return {}
    O = p.load("outline") or {}
    items = S.get("items", [])
    nums = numbers(O.get("sections") or [], items) if O.get("sections") else {}
    P = (p.load("proofs") or {}).get("items", {})
    by_tab = {"definitions": ["definition"], "lemmas": ["lemma"], "propositions": ["proposition"], "theorems": ["theorem"],
              "corollaries": ["corollary"], "other": ["conjecture", "example", "remark", "question"]}
    proofs = {}
    for k, v in P.items():
        c = v.get("check") or {}
        proofs[k] = {"text": v.get("text", ""), "status": v.get("status"), "added": v.get("added", []),
                     "checked_added": c.get("added", []), "departures": c.get("departures", []), "follows": c.get("follows"),
                     "author": c.get("author", ""), "statement": c.get("statement", ""), "proof": c.get("proof", ""),
                     "doubt": v.get("doubt", ""), "settled": v.get("settled"), "comment": v.get("comment", ""),
                     "tries": len(v.get("tries") or []), "problems": v.get("problems", [])}
    kind = S.get("kind")
    if kind == "not_ready" and (p.load("scope") or {}).get("kind") != "not_ready":
        # proposed by an earlier version, never chosen by the author: stopping is only ever the author's choice
        kind = default_kind(p.load("assessment") or {}, {x["id"] for x in items if x.get("role") == "main"})
    return {"items": items, "kind": kind, "note": S.get("note", ""), "unused": S.get("unused", []),
            "leave_out": O.get("leave_out") or [], "chat": O.get("chat") or [],
            "tabs": by_tab, "outline": O.get("sections") or [], "numbers": nums, "proofs": proofs,
            "abstract": (p.load("abstract_draft") or {}).get("abstract", ""),
            "open_questions": open_questions(p), "open_proofs": open_proof_problems(p) if P else []}
