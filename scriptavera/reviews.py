# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""A referee's report, or the journal's proof corrections, carried out in the paper.

The report is split into numbered points. Each point becomes a request to the editor's command engine (the same
one that carries out commands in plain words, with its checks), so every change is a proposal with exact hunks:
- language, presentation and typesetting points are applied by themselves, but only when the change touches no
  formula, label, \\ref or \\cite; otherwise they wait for the author like a mathematical point;
- points about the mathematics, the references, and queries are put to the author: apply the proposed change,
  ask for a different one, decline with a reason, or say they made it themselves.
Nothing else in the paper is touched: changes are made only by applying these proposals, and a version is kept
before the first one. Finally a letter is written from what was actually done: the response to the referee, or
the list of corrections and answers for the production editor."""
from __future__ import annotations

import re
import threading
import time
import uuid

from . import assist, prompts
from . import workspace as W
from .llm import LLMError
from .store import Project
from .util import fit, loads_lenient

KINDS = {"referee": "Referee's report", "proofs": "Proof corrections and queries", "coauthor": "Co-author's comments"}
AUTO_TYPES = ("language", "presentation", "typesetting")
_running: dict[str, threading.Thread] = {}


def load(p: Project) -> dict:
    return p.load("reviews", {}) or {"rounds": []}


def _save_round(p: Project, rnd: dict) -> None:
    with p._lock:
        data = load(p)
        data["rounds"] = [r for r in data["rounds"] if r["id"] != rnd["id"]] + [rnd]
        data["rounds"].sort(key=lambda r: r["at"])
        p.save("reviews", data)


def get_round(p: Project, rid: str) -> dict:
    rnd = next((r for r in load(p)["rounds"] if r["id"] == rid), None)
    if not rnd:
        raise assist.AssistError("That report was not found.")
    return rnd


def is_busy(p: Project) -> bool:
    t = _running.get(p.id)
    return bool(t and t.is_alive())


def split_points(p: Project, kind: str, report: str) -> list[dict]:
    """The report as numbered points, each with what it asks for, where, and what kind of change it is."""
    hooks = assist._Hooks(p)
    from .llm import router
    ol = W.outline(p)
    texts = {rel: W.read_text(p, rel)["text"] for rel in W.files(p) if W._kind(rel) == "text"}
    res = router.chat("reason", prompts.REVIEW_SPLIT.format(kind=KINDS[kind]),
                      f"OUTLINE OF THE PAPER\n{fit(assist.outline_text(ol.get('items', []), texts), 6000, 'head')}\n\n"
                      f"THE {KINDS[kind].upper()}\n{fit(report, 30000, 'head')}",
                      json_mode=True, max_out=6000, effort="medium", label="Split the report into points", hooks=hooks)
    data = loads_lenient(res.text)
    out = []
    for i, x in enumerate((data.get("points") if isinstance(data, dict) else data) or [], 1):
        if not isinstance(x, dict) or not str(x.get("request") or x.get("quote") or "").strip():
            continue
        typ = str(x.get("type") or "other").lower().strip()
        typ = next((t for t in ("mathematics", "language", "presentation", "typesetting", "reference", "query") if t in typ), "other")
        out.append({"id": f"P{i}", "quote": str(x.get("quote") or "").strip()[:2000], "type": typ,
                    "where": str(x.get("where") or "").strip()[:300], "request": str(x.get("request") or x.get("quote")).strip()[:1500],
                    "status": "pending", "summary": "", "proposal": "", "decision": {}})
    return out


def _instruction(pt: dict, kind: str) -> str:
    who = "The referee" if kind == "referee" else "The journal's proof corrections"
    words = f' ({who.lower()} wrote: "{fit(pt["quote"], 600, "head")}")' if pt["quote"] and pt["quote"] != pt["request"] else ""
    where = f" It concerns: {pt['where']}." if pt["where"] else ""
    if pt["type"] in AUTO_TYPES:
        return (f"{who} asks: {pt['request']}{words}.{where} Change only the wording or layout this asks for, only where it "
                "applies. Do not change any formula, \\label, \\ref or \\cite, and nothing else.")
    return f"{who} asks: {pt['request']}{words}.{where} Propose exactly the change this asks for, only where it applies, and nothing else."


def _locations(p: Project, prop: dict) -> list[str]:
    try:
        items = W.outline(p).get("items", [])
    except Exception:  # noqa: BLE001
        items = []
    out = []
    for h in prop.get("hunks", []):
        where = assist._container(items, h["path"], h["old_start"])
        if where not in out:
            out.append(where)
    return out


def _touches_math(p: Project, prop_id: str) -> bool:
    full = assist.load_proposal(p, prop_id)
    for path, old in full.get("old", {}).items():
        new = full.get("new", {}).get(path, "")
        if assist.math_bag(old) != assist.math_bag(new):
            return True
        for rx in (r"\\label\{[^}]*\}", r"\\(?:eq|c|C|auto)?ref\{[^}]*\}", r"\\cite[pt]?\*?(?:\[[^\]]*\])?\{[^}]*\}"):
            if sorted(re.findall(rx, old)) != sorted(re.findall(rx, new)):
                return True
    return False


def propose(p: Project, rnd: dict, pt: dict, extra: str = "") -> None:
    """A proposal for one point; a language point that touches nothing mathematical is applied at once."""
    try:
        prop = assist.run(p, _instruction(pt, rnd["kind"]) + (f" The author adds: {extra}" if extra else ""))
    except (assist.AssistError, LLMError) as e:
        pt.update(status="needs_author", summary=f"No change could be proposed: {e}", proposal="")
        return
    pt["proposal"], pt["summary"] = prop["id"], prop.get("summary", "")
    pt["hunks"] = [{k: h[k] for k in ("id", "path", "old_start", "old", "new")} for h in prop.get("hunks", [])]
    pt["where_changed"] = _locations(p, prop)
    pt["warnings"] = [w.get("message", "") for w in prop.get("warnings", [])][:6]
    if not prop.get("hunks"):
        pt["status"] = "needs_author" if pt["type"] not in AUTO_TYPES else "nothing"
        return
    if pt["type"] in AUTO_TYPES and not prop.get("warnings") and not _touches_math(p, prop["id"]):
        assist.apply(p, prop["id"])
        pt["status"] = "applied"
        pt["decision"] = {"choice": "auto"}
    else:
        if pt["type"] in AUTO_TYPES:
            pt["held"] = "The change would touch a formula, a label, a reference or a citation, so it waits for you."
        pt["status"] = "needs_author"


def _work(p: Project, rid: str) -> None:
    rnd = get_round(p, rid)
    try:
        if not rnd["points"]:
            rnd["points"] = split_points(p, rnd["kind"], rnd["report"])
            rnd["state"] = "working"
            _save_round(p, rnd)
        for pt in rnd["points"]:
            if pt["status"] != "pending":
                continue
            if pt["type"] == "query" and rnd["kind"] == "proofs" and not re.search(r"\b(change|replace|correct|delete|insert|add)\b", pt["request"], re.I):
                pt["status"] = "needs_author"      # a question for the author, not a change
            else:
                propose(p, rnd, pt)
            _save_round(p, rnd)
        rnd["state"] = "ready"
    except (LLMError, assist.AssistError) as e:
        rnd["state"], rnd["error"] = "error", str(e)
    except Exception as e:  # noqa: BLE001 - shown to the author; the paper is unchanged except for applied points
        rnd["state"], rnd["error"] = "error", f"{type(e).__name__}: {e}"
    _save_round(p, rnd)


def start(p: Project, kind: str, report: str, title: str = "") -> dict:
    if kind not in KINDS:
        raise assist.AssistError("Choose a referee's report or proof corrections.")
    report = (report or "").strip()
    if len(report) < 20:
        raise assist.AssistError("Paste the report, or upload it as a PDF or text file.")
    if not W.exists(p):
        raise assist.AssistError("Open the editor first: the changes are made in its files.")
    if is_busy(p):
        raise assist.AssistError("The previous report is still being worked through.")
    W.snapshot(p, f"Before the {KINDS[kind].lower()}", "named", force=True)
    n = sum(1 for r in load(p)["rounds"] if r["kind"] == kind) + 1
    rnd = {"id": uuid.uuid4().hex[:10], "kind": kind, "at": time.time(), "state": "reading", "report": report[:60000],
           "title": title or (f"{KINDS[kind]}, round {n}" if kind == "referee" else f"{KINDS[kind]} ({n})"), "points": [], "letter": ""}
    _save_round(p, rnd)
    t = threading.Thread(target=_work, args=(p, rnd["id"]), daemon=True)
    _running[p.id] = t
    t.start()
    return rnd


def decide(p: Project, rid: str, pid: str, choice: str, note: str = "") -> dict:
    """apply | decline | self | redo | answer"""
    if is_busy(p):
        raise assist.AssistError("Wait until the report has been worked through.")
    rnd = get_round(p, rid)
    pt = next((x for x in rnd["points"] if x["id"] == pid), None)
    if not pt:
        raise assist.AssistError("That point was not found.")
    note = (note or "").strip()
    if choice == "apply":
        if not pt.get("proposal"):
            raise assist.AssistError("There is no proposed change to apply. Ask for one, or say what you did.")
        assist.apply(p, pt["proposal"])
        pt["status"], pt["decision"] = "applied", {"choice": "apply", "note": note}
    elif choice == "redo":
        if not note:
            raise assist.AssistError("Say what the change should be instead.")
        propose(p, rnd, pt, extra=note)
        if pt["status"] == "applied":
            pt["decision"] = {"choice": "auto"}
    elif choice in ("decline", "self", "answer"):
        if not note:
            raise assist.AssistError({"decline": "Give the reason, for the letter.", "self": "Say briefly what you changed, for the letter.",
                                      "answer": "Write the answer."}[choice])
        pt["status"], pt["decision"] = {"decline": "declined", "self": "done_by_author", "answer": "answered"}[choice], {"choice": choice, "note": note}
    else:
        raise assist.AssistError("Unknown choice.")
    rnd["letter"] = ""                      # what was done changed: the letter is written again
    _save_round(p, rnd)
    return rnd


def letter(p: Project, rid: str) -> dict:
    rnd = get_round(p, rid)
    open_pts = [x["id"] for x in rnd["points"] if x["status"] in ("pending", "needs_author")]
    if open_pts:
        raise assist.AssistError(f"Decide first on point{'s' if len(open_pts) > 1 else ''} {', '.join(open_pts)}.")
    rows = []
    for x in rnd["points"]:
        changed = "; ".join(f"in {w}" for w in x.get("where_changed", [])) or "no place"
        new_lines = " / ".join(" ".join(h["new"])[:300] for h in x.get("hunks", [])[:3]) if x["status"] == "applied" else ""
        rows.append(f"{x['id']} [{x['type']}] COMMENT: {fit(x['quote'] or x['request'], 900, 'head')}\n"
                    f"   WHAT WAS DONE: {x['status']}" + (f" ({x['summary']}); changed {changed}; new text: {new_lines}" if x["status"] == "applied" else "")
                    + (f"\n   AUTHOR'S NOTE: {x['decision'].get('note')}" if x.get("decision", {}).get("note") else ""))
    from .llm import router
    res = router.chat("writer", prompts.REVIEW_LETTER.format(kind=KINDS[rnd["kind"]]),
                      f"TITLE OF THE PAPER\n{(p.load('paper') or {}).get('title', '')}\n\nTHE POINTS AND WHAT WAS DONE\n" + "\n".join(rows),
                      max_out=5000, effort="medium", label="Write the letter", hooks=assist._Hooks(p))
    rnd["letter"] = res.text.strip()
    _save_round(p, rnd)
    return rnd


def report_text(name: str, data: bytes) -> str:
    """Text of an uploaded report: PDF (with the comments written on it), a co-author's comments file, or plain text."""
    if name.lower().endswith(".json"):
        from .extras import comments_to_report
        return comments_to_report(data)
    if name.lower().endswith(".pdf") or data[:4] == b"%PDF":
        import pypdfium2 as pdfium
        from .typed import comments_text, pdf_comments
        doc = pdfium.PdfDocument(data)
        try:
            text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
        finally:
            doc.close()
        try:                                    # comments the referee wrote on the PDF itself
            notes = comments_text(pdf_comments(data))
        except Exception:  # noqa: BLE001 - a PDF whose annotations cannot be read still gives its text
            notes = ""
        return (text.strip() + ("\n\n" + notes if notes else "")).strip()
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""
