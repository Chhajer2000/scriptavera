# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The figures the paper prints, drawn again for print.

Up to the check of the transcript a drawing is copied as the author drew it (figflow.py), and at the structure step
the paper decides which drawings it prints (drawings.py). Each printed figure is then drawn the way a figure in a
published research paper is drawn, before the proofs are written, so that the proofs can refer to it:
  - with the whole of what it belongs to in view: the paper's problem, the statement it is printed with, the passage
    of the notes around it, what the paper takes from it, the author's notation and the caption;
  - with freedom in how it looks: one line width, one kind of arrow, labels set in type at one size, points aligned,
    spacing made even and shapes made symmetric where the drawing means them to be, lines straight where they are
    meant straight, curves smooth, the picture laid out to read well;
  - and never with another meaning: every object, label, arrow and its direction, which lines meet or cross, every
    count and every order stays as drawn; a curve stays a curve where its shape means something. A mistake in the
    drawing is corrected only where the notes themselves show it (the text says four leaves and three are drawn),
    and every such correction is named, with the words of the notes that show it.
A second reader (another model of the class, where there is one) compares the figure for print with the author's
drawing: anything it shows differently that is not a named correction, and any correction the notes do not bear
out, is sent back to be put right, at most twice. A figure that still differs, or does not compile, is not printed:
the copy of the author's drawing is. The author sees every figure drawn for print next to their own drawing before
the first draft is written, and decides: this one, the copy of their drawing, a change of their own, or not printed.
A figure is drawn again only when what it is drawn from changes (the drawing, its caption, its statement or passage,
or the author's instruction)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import time

from . import paper as paperlib, prompts
from .llm import LLMError
from .pipeline import (Run, _room, _side_by_side, _tikz_only, approved_pages, emit, figure_file, notation_for,
                       notation_text, passing, plural)
from .store import Project
from .util import extract_tag, fit, loads_lenient, parse_lines

FIX_ROUNDS = 2                  # times a figure for print is sent back after the second reader's check
_lock = threading.Lock()


def entries(p: Project) -> dict:
    data = p.load("figures_pro") or {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(p: Project, fid: str, rec: dict | None) -> None:
    with _lock:
        data = p.load("figures_pro") or {}
        if rec is None:
            data.pop(fid, None)
        else:
            data[fid] = rec
        p.save("figures_pro", data)


def _sig(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _num(fid: str) -> str:
    return re.sub(r"\D", "", fid) or fid


# ------------------------------------------------------------------ what is printed

def uses_print_version(pro: dict | None) -> bool:
    """Whether the figure drawn for print is the one printed: it was drawn and compiles, and the author did not choose
    the copy of their own drawing instead."""
    if not pro or not pro.get("tikz"):
        return False
    return pro.get("choice") == "pro" or (bool(pro.get("ok")) and pro.get("choice") in (None, ""))


def printed(p: Project, f: dict, plan: dict | None, pro: dict | None) -> dict | None:
    """What the paper prints for drawing f: {"mode": "tikz" | "orig", "tikz", "caption", "page", "version"}, or None when
    it is not printed."""
    if not plan or plan.get("use") != "paper" or f.get("choice") not in ("tikz", "orig"):
        return None
    caption = (plan.get("caption") or f.get("caption") or f.get("desc") or "").strip()
    if uses_print_version(pro):
        return {"mode": "tikz", "tikz": pro["tikz"], "caption": (pro.get("caption") or caption).strip(), "page": f["page"],
                "version": "print"}
    if f.get("choice") == "tikz" and f.get("tikz_ok") and f.get("tikz"):
        return {"mode": "tikz", "tikz": f["tikz"], "caption": caption, "page": f["page"], "version": "copy"}
    return {"mode": "orig", "tikz": "", "caption": caption, "page": f["page"], "version": "copy"}


def writers_desc(f: dict, pro: dict | None) -> str:
    """What the writers of the proofs are told the printed figure shows: the description of the drawing, and the
    corrections of the figure for print when that one is printed."""
    desc = f.get("desc", "")
    if uses_print_version(pro) and pro.get("corrections"):
        desc += " (The figure printed corrects the drawing: " + "; ".join(c.get("what", "") for c in pro["corrections"]) + ".)"
    return desc


# ------------------------------------------------------------------ drawing for print

def _context(p: Project, f: dict, plan: dict, seq: list[dict], nums: dict) -> str:
    from . import drawings
    from .drafting import kind_name
    pages = {pg["n"]: pg["text"] for pg in approved_pages(p)}
    it = drawings.item(f, pages, before=1500, after=800)
    d = p.load("dossier") or {}
    x = next((y for y in seq if y["id"] == plan.get("with")), None)
    where = ""
    if x:
        where = (f"It is printed {'inside the proof of' if plan.get('in_proof') else 'right after'} "
                 f"{kind_name(x['kind'])} {nums.get(x['id'], '')} ({x['id']}): {x.get('statement', '')}")
    return (f"THE PROBLEM OF THE PAPER\n{d.get('problem', '')}\n\n"
            + (f"WHERE THE FIGURE STANDS IN THE PAPER\n{where}\n\n" if where else "")
            + f"THE CAPTION\n{plan.get('caption') or f.get('desc', '')}\n\n"
            + (f"WHAT THE PAPER TAKES FROM THE FIGURE\n{plan['needed']}\n\n" if plan.get("needed") else "")
            + f"THE DESCRIPTION OF THE DRAWING (from the copy of the notes)\n{f.get('desc', '')}\n\n"
            + f"THE PASSAGE OF THE NOTES AROUND THE DRAWING (the drawing is at {drawings.HERE})\n{it['passage']}\n\n"
            + f"THE AUTHOR'S CONFIRMED NOTATION\n{notation_text(p.load('confirmed', {}), 1200)}")


def key_of(p: Project, f: dict, plan: dict, seq: list[dict], instruction: str) -> str:
    """What a figure for print is drawn from: the copy of the drawing, its caption and what the paper takes from it,
    the passage of the notes around it, the statement it is printed with, the part of the notation that occurs there,
    and the author's instruction. The number the statement has, the topic's wording or another statement changing
    leave it as it is."""
    from . import drawings
    pages = {pg["n"]: pg["text"] for pg in approved_pages(p)}
    it = drawings.item(f, pages, before=1500, after=800)
    x = next((y for y in seq if y["id"] == plan.get("with")), None) or {}
    return _sig(f.get("choice"), f.get("tikz") if f.get("choice") == "tikz" else f.get("drawn"), f.get("desc"), it["passage"],
                x.get("id"), x.get("statement"), bool(plan.get("in_proof")), plan.get("needed"), plan.get("caption"),
                notation_for(p.load("confirmed", {}), it["passage"] + "\n" + (f.get("desc") or "")), instruction,
                bool(paperlib.find_engine()))


def _images(p: Project, f: dict) -> list[bytes]:
    from PIL import Image
    from .figflow import jpeg
    out = []
    orig = figure_file(p, f["id"], "orig")
    if orig.exists():
        out.append(jpeg(Image.open(orig).convert("RGB")))
    copy = figure_file(p, f["id"], "tikz")
    if f.get("choice") == "tikz" and f.get("tikz_ok") and copy.exists():
        out.append(jpeg(Image.open(copy).convert("RGB")))
    return out


def _parse(text: str) -> dict:
    tikz = _tikz_only(extract_tag(text, "tikz") or text)
    changes = [c for c in parse_lines(extract_tag(text, "changes")) if c and c.strip().lower().strip(".") not in ("none", "n/a")]
    corrections = []
    for line in parse_lines(extract_tag(text, "corrections")):
        parts = [x.strip() for x in line.split("||")]
        if not parts[0] or parts[0].lower().strip(".") in ("none", "n/a"):
            continue
        corrections.append({"what": parts[0], "instead": parts[1] if len(parts) > 1 else "", "why": parts[2] if len(parts) > 2 else ""})
    caption = re.sub(r"\s+", " ", extract_tag(text, "caption")).strip()
    caption = re.sub(r"^(?:fig(?:ure)?\.?\s*\d*[:.]\s*)", "", caption, flags=re.I)
    return {"tikz": tikz, "changes": changes[:12], "corrections": corrections[:8], "caption": caption}


def _check(ask, f: dict, context: str, imgs: list[bytes], png: bytes, got: dict, other_than: str, n: int) -> dict:
    from PIL import Image
    from .figflow import jpeg
    listed = ("\n".join(f"- {c['what']}" + (f" (the figure shows instead: {c['instead']})" if c.get("instead") else "")
                        + (f"; the notes: {c['why']}" if c.get("why") else "") for c in got["corrections"]) or "none")
    user = (context + "\n\nWHAT THE FIGURE FOR PRINT CHANGES IN HOW IT LOOKS (not differences)\n"
            + ("; ".join(got["changes"]) or "nothing named") + "\n\nTHE CORRECTIONS IT NAMES (differences on purpose)\n" + listed)
    try:
        res = ask("vision_best", prompts.FIG_PRO_CHECK.format(), user,
                  images=[imgs[0], jpeg(Image.open(io.BytesIO(png)).convert("RGB"))], json_mode=True, max_out=1400,
                  effort="medium", temperature=0.0, other_than=other_than, label=f"Check figure {n} for print against the drawing")
    except LLMError as e:
        if passing(e):
            raise
        return {"same": None, "problems": [], "error": str(e)}
    data = loads_lenient(res.text)
    if not isinstance(data, dict):
        return {"same": None, "problems": [], "model": res.model}
    probs = []
    for x in data.get("problems") or []:
        if isinstance(x, dict):
            t = " — ".join(str(x.get(k) or "").strip() for k in ("what", "where", "fix") if str(x.get(k) or "").strip())
        else:
            t = str(x).strip()
        if t:
            probs.append(t)
    return {"same": bool(data.get("same")) and not probs, "problems": probs[:8], "model": res.model}


def draw_for_print(p: Project, f: dict, plan: dict, ask, seq: list[dict], nums: dict, instruction: str = "") -> dict:
    """One figure drawn for print, compiled and checked (see the module's description). Returns its record."""
    n = _num(f["id"])
    context = _context(p, f, plan, seq, nums)
    key = key_of(p, f, plan, seq, instruction)
    rec = {"key": key, "ok": False, "tikz": "", "caption": "", "changes": [], "corrections": [], "check": {}, "model": "",
           "error": "", "instruction": instruction, "at": time.time(), "choice": "", "fresh": True}
    if not paperlib.find_engine():
        rec["error"] = "no LaTeX program is installed on this computer, so a figure for print cannot be compiled"
        return rec
    imgs = _images(p, f)
    if not imgs:
        rec["error"] = "the drawing was not cut from the page"
        return rec
    copy_code = f.get("tikz", "") if f.get("choice") == "tikz" and f.get("tikz_ok") else ""
    system = prompts.FIG_PRO.format()
    base = (context + ("\n\nTHE EXACT COPY OF THE DRAWING, IN TIKZ (its coordinates were measured from the drawing)\n"
                       + copy_code if copy_code else "\n\n(There is no exact copy in TikZ: draw it from the author's drawing.)")
            + (f"\n\nTHE AUTHOR'S INSTRUCTION FOR THIS FIGURE (follow it)\n{instruction}" if instruction else ""))
    user, model, err, best = base, "", "", None
    room = _room("vision_best", system, 7000, vision=True)
    for rnd in range(FIX_ROUNDS + 1):
        res = ask("vision_best", system, fit(user, room, "middle"), images=imgs, max_out=7000, effort="high",
                  label=f"Draw figure {n} for print" + (f" (corrected, {rnd})" if rnd else ""))
        model = res.model
        got = _parse(res.text)
        if not got["tikz"]:
            err = "the answer had no usable TikZ picture"
            user = base + "\n\nYOUR PREVIOUS ANSWER HAD NO USABLE PICTURE: answer with one tikzpicture in <tikz> ... </tikz>."
            continue
        png, why = paperlib.render_tikz(got["tikz"])
        if not png:
            err = f"it does not compile ({why})"
            user = (base + f"\n\nYOUR PREVIOUS FIGURE\n{got['tikz']}\n\nIT DOES NOT COMPILE WITH pdfLaTeX: {why}\n"
                    "Give the whole figure again, corrected.")
            continue
        chk = _check(ask, f, context, imgs, png, got, model, n)
        best = (got, png, chk)
        if chk.get("same") is not False:
            break
        user = (base + f"\n\nYOUR PREVIOUS FIGURE\n{got['tikz']}\n\nWHAT A SECOND READER FOUND (the figure must not say "
                "anything the drawing does not, except a correction the notes bear out)\n" + "\n".join(f"- {x}" for x in chk["problems"]))
    rec["model"] = model
    if not best:
        rec["error"] = err or "the figure could not be drawn for print"
        return rec
    got, png, chk = best
    rec.update(tikz=got["tikz"], caption=got["caption"], changes=got["changes"], corrections=got["corrections"], check=chk,
               ok=chk.get("same") is not False)
    if not rec["ok"]:
        rec["error"] = "a second reader still finds it different from your drawing: " + "; ".join(chk.get("problems", [])[:3])
    _write_png(p, f["id"], png)
    return rec


def _write_png(p: Project, fid: str, png: bytes) -> None:
    target = figure_file(p, fid, "pro")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(png)


# ------------------------------------------------------------------ the step

def stage_figdesign(run: Run) -> None:
    """Every figure the paper prints drawn for print; one drawn before from exactly the same drawing, caption, place
    and passage is kept with the author's choice on it."""
    from .drafting import ordered
    p = run.p
    figs = {f["id"]: f for f in (p.load("figures") or {}).get("items", [])}
    plan = (p.load("figure_plan") or {}).get("items") or {}
    wanted = [fid for fid, e in plan.items() if e.get("use") == "paper" and figs.get(fid, {}).get("choice") in ("tikz", "orig")]
    have = entries(p)
    for fid in list(have):
        if fid not in wanted:
            _save(p, fid, None)                  # no longer printed
    if not wanted:
        run.log("No drawing is printed in the paper, so no figure is drawn for print.")
        return
    seq, secs, nums = ordered(p)
    todo = []
    for fid in wanted:
        old = have.get(fid)
        k = key_of(p, figs[fid], plan[fid], seq, (old or {}).get("instruction", ""))
        if old and old.get("key") == k:          # drawn before from exactly this (also earlier in a step that was stopped)
            continue
        todo.append(fid)
    if len(wanted) > len(todo):
        run.log(f"{plural(len(wanted) - len(todo), 'figure')} drawn for print before from the same drawing and place kept.")
    done = [0]
    run.progress(0, len(todo), f"drawing {plural(len(todo), 'figure')} for print")

    def work(fid: str) -> None:
        run.check()
        old = have.get(fid) or {}
        rec = draw_for_print(p, figs[fid], plan[fid], run.ask, seq, nums, old.get("instruction", ""))
        _save(p, fid, rec)
        with _lock:
            done[0] += 1
        run.progress(done[0], len(todo), f"figure {_num(fid)} drawn for print" + ("" if rec["ok"] else ": the copy of your drawing is used"))

    _side_by_side(run, "vision_best", True, todo, work)
    got = entries(p)
    ok = [fid for fid in todo if got.get(fid, {}).get("ok")]
    fixed = [fid for fid in ok if got[fid].get("corrections")]
    if todo:
        run.log(f"{plural(len(todo), 'figure')} drawn for print: {len(ok)} ready"
                + (f", {len(fixed)} with a correction the notes show, for you to confirm" if fixed else "")
                + (f"; for {len(todo) - len(ok)} the copy of your drawing is used" if len(ok) < len(todo) else "")
                + ". You confirm each one before the first draft is written.", "warn" if len(ok) < len(todo) else "info")


# ------------------------------------------------------------------ the author's answers

CHOICES = ("pro", "copy", "none")


def set_choice(p: Project, fid: str, choice: str) -> dict:
    """The author on one figure drawn for print: print this one ("pro"), print the copy of their own drawing
    ("copy"), or do not print the drawing at all ("none": it only explains an argument, as far as the paper goes)."""
    from . import drawings
    if choice not in CHOICES:
        raise ValueError("Unknown choice.")
    with _lock:
        data = p.load("figures_pro") or {}
        rec = data.get(fid)
        if rec is None:
            raise ValueError("This figure is not drawn for print.")
        if choice == "pro" and not (rec.get("ok") or rec.get("tikz")):
            raise ValueError("This figure could not be drawn for print: the copy of your drawing is printed, or leave it out.")
        rec["choice"], rec["answered"] = choice, time.time()
        rec.pop("fresh", None)
        p.save("figures_pro", data)
    if choice == "none":
        drawings.set_plan(p, fid, use="explanation")
    return rec


def confirm_all(p: Project, assume: bool = True) -> None:
    """At the approval of the proofs: every figure drawn for print has the author's answer. One left unanswered (the
    author approved with the list of them in view) is printed for print when its check passed and it names no
    correction; else the copy of the author's drawing."""
    with _lock:
        data = p.load("figures_pro") or {}
        changed = False
        for fid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            if not rec.get("choice"):
                if not assume:
                    raise ValueError(f"Say for figure {_num(fid)} which version the paper prints.")
                # approved without an answer: the figure for print only when nothing but its look changed; a correction
                # the author has not confirmed is never printed, so the author's own drawing is
                rec["choice"], rec["auto"] = ("pro" if rec.get("ok") and not rec.get("corrections") else "copy"), True
                changed = True
            if rec.pop("fresh", None):
                changed = True
        if changed:
            p.save("figures_pro", data)


def unanswered(p: Project) -> list[str]:
    """Figures drawn for print that the author has not answered and that change something the drawing shows (a
    correction), or whose check did not pass: these are put to the author explicitly at the approval."""
    out = []
    for fid, rec in entries(p).items():
        if rec.get("choice"):
            continue
        if rec.get("corrections") or not rec.get("ok"):
            out.append(fid)
    return out


def redraw(p: Project, fid: str, instruction: str) -> dict:
    """The author's own instruction for one figure for print, carried out at once (in the background)."""
    from .drafting import ordered
    instruction = (instruction or "").strip()[:1500]
    if not instruction:
        raise ValueError("Say what should change in the figure.")
    figs = {f["id"]: f for f in (p.load("figures") or {}).get("items", [])}
    plan = (p.load("figure_plan") or {}).get("items") or {}
    if fid not in figs or (plan.get(fid) or {}).get("use") != "paper":
        raise ValueError("This drawing is not printed in the paper.")
    from .pipeline import Run as _Run
    run = _Run(p)
    run.stage = "figdesign"

    def ask(role, system, user, **kw):
        from .llm import router
        return router.chat(role, system, user, hooks=run, **kw)
    seq, secs, nums = ordered(p)
    rec = draw_for_print(p, figs[fid], plan[fid], ask, seq, nums, instruction)
    _save(p, fid, rec)
    emit(p.id, "log", stage="proofcheck", level="done" if rec["ok"] else "warn",
         message=f"Figure {_num(fid)} drawn again for print with your instruction" + ("." if rec["ok"] else f": {rec['error']}."))
    return rec
