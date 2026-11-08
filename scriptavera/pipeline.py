# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The staged pipeline. Each stage is one function; each AI call has one job.

Stages run in order and stop at the two gates where the author must decide.
Every stage saves its result, so a stopped run resumes where it left off.
"""

from __future__ import annotations

import contextlib
import contextvars
import difflib
import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import audit as fidelity
from . import config, domains, literature, paper as paperlib, placing, prompts, tenancy
from .llm import ROLES, Cancelled, LLMError, RunHooks, clock, router
from . import store as storelib
from .store import STAGE_IDS, STAGES, Project
from .texutil import answer_transcript, clean_transcript, collapse_loops, drop_leaks, leaks, looping, unknown_commands
from .util import (est_tokens, extract_tag, fit, loads_lenient, parse_lines, parse_list, parse_records, parse_rows,
                   to_float)

# ------------------------------------------------------------- event bus

_events: dict[str, list[dict]] = {}
_ev_lock = threading.Lock()
_runs: dict[str, "Run"] = {}


def emit(pid: str, kind: str, **data) -> None:
    with _ev_lock:
        lst = _events.setdefault(pid, [])
        lst.append({"id": (lst[-1]["id"] + 1) if lst else 1, "t": time.time(), "type": kind, **data})
        if len(lst) > 1500:
            del lst[:500]


def events_after(pid: str, last_id: int) -> list[dict]:
    with _ev_lock:
        return [e for e in _events.get(pid, []) if e["id"] > last_id]


class Run(RunHooks):
    def __init__(self, project: Project):
        self.p = project
        self.stage = ""
        self._cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.settings = config.load_settings()

    # hooks used by the router
    def on_call(self, record: dict) -> None:
        record["stage"] = self.stage
        record["t"] = time.time()
        self.p.log_call(record)
        emit(self.p.id, "call", **record)

    def on_wait(self, message: str) -> None:
        self.log(message, "wait")

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def stop(self) -> None:
        self._cancel.set()

    # helpers for stages
    def log(self, message: str, level: str = "info") -> None:
        emit(self.p.id, "log", stage=self.stage, level=level, message=message)

    def progress(self, done: int, total: int, note: str = "", more: int = 0) -> None:
        """done of total (pages, papers, figures). more: parts of the step still to come after them (resolving the
        notation once the pages are read, checking the transcript once it is written): the bar is not full until
        those are done too."""
        self.p.set_stage(self.stage, progress={"done": done, "total": total, "note": note, "more": more})
        emit(self.p.id, "progress", stage=self.stage, done=done, total=total, note=note, more=more)

    def changed(self) -> None:
        emit(self.p.id, "state")

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled()

    # How hard a model should think when a stage does not say. Accuracy-critical reasoning gets the most.
    EFFORT = {"reason": "high", "writer": "medium", "reader": "low", "fast": "low",
              "vision_fast": "low", "vision_best": "medium"}

    def ask(self, role: str, system: str, user: str, paper: bool | None = None, **kw):
        """One model call. For text that goes into the paper (every writer's answer, or paper=True), an answer
        that contains the model's own reasoning is asked for again once; what is still reasoning is removed."""
        self.check()
        kw.setdefault("effort", self.EFFORT.get(role))
        res = router.chat(role, system, user, hooks=self, **kw)
        if (role == "writer" if paper is None else paper) and leaks(res.text):
            self.log(f"{kw.get('label') or 'A model'}: the answer contained the model's own reasoning; asked again.", "warn")
            again = (user + "\n\n---\nIMPORTANT: your previous answer contained your own thinking (such as \"Wait, ...\" "
                     "or quotations of these instructions). Return only the requested text itself, with no reasoning, "
                     "no commentary and no quotation of the instructions.")
            kw2 = {**kw, "label": (kw.get("label") or "") + " (again)"}
            res = router.chat(role, system, again, hooks=self, **kw2)
            if leaks(res.text):
                res.text, gone = drop_leaks(res.text)
                res.leaked = gone
                self.log(f"{gone} paragraph(s) of reasoning were removed from the answer. The formula check that "
                         "follows restores anything of yours that went with them.", "warn")
        self.p.set_stage(self.stage, model=f"{res.provider}:{res.model}")
        self._note_stand_in(role, res, bool(kw.get("images")))
        return res

    def _note_stand_in(self, role: str, res, vision: bool) -> None:
        """Remember when a model below the top class did a quality-critical job (only possible when the author
        allowed it), so the step can be redone once the stronger model is back."""
        if not ROLES[role].get("strict"):
            return
        top = router.peers(role, vision)
        if not top or any(m.id == res.model and m.provider == res.provider for m in top):
            return
        sid, wanted, used = self.stage, top[0].id, res.model

        def f(st):
            fb = st["stages"].setdefault(sid, {}).get("stand_in") or {"wanted": [], "used": {}}
            if wanted not in fb["wanted"]:
                fb["wanted"].append(wanted)
            fb["used"][used] = fb["used"].get(used, 0) + 1
            st["stages"][sid]["stand_in"] = fb
        self.p.update_state(f)

    @property
    def meta(self) -> dict:
        return self.p.state.get("meta", {})

    @property
    def field(self) -> str:
        return domains.describe(self.meta.get("domains", []), self.meta.get("subarea", ""))


def is_running(pid: str) -> bool:
    r = _runs.get(pid)
    return bool(r and r.thread and r.thread.is_alive())


# ------------------------------------------------------- automatic restart

_armed: dict[str, float] = {}
_arm_lock = threading.Lock()
_watcher: list[threading.Thread] = []


def arm(pid: str, at: float) -> None:
    """Restart this project by itself at the given time (when a limit resets or the network returns)."""
    with _arm_lock:
        _armed[pid] = at
        if not _watcher or not _watcher[0].is_alive():
            t = threading.Thread(target=_watch, daemon=True, name="scriptavera-auto-restart")
            _watcher[:] = [t]
            t.start()


def disarm(pid: str) -> None:
    with _arm_lock:
        _armed.pop(pid, None)


def _watch() -> None:
    while True:
        time.sleep(3)
        with _arm_lock:
            due = [pid for pid, at in _armed.items() if time.time() >= at]
            for pid in due:
                _armed.pop(pid, None)
        for pid in due:
            p = storelib.get(pid)
            if p and not is_running(pid) and any(s.get("status") == "paused" for s in p.state["stages"].values()):
                emit(pid, "log", stage="", level="stage", message="The waiting time is over. Continuing automatically.")
                start(p)


def cancel_auto(p: Project) -> None:
    """The author does not want the automatic restart: leave the stage stopped, with its reason."""
    disarm(p.id)

    def f(st):
        for s in st["stages"].values():
            if s.get("status") == "paused":
                s["status"], s["resume_at"] = "error", None
    p.update_state(f)
    emit(p.id, "state")


def start(project: Project) -> bool:
    if is_running(project.id) or project.state.get("meta", {}).get("imported"):
        return False                     # an imported paper has no notes to read: it is worked on in the editor
    disarm(project.id)
    if project.state.get("restart"):
        project.update_state(lambda st: st.pop("restart", None))
    run = Run(project)
    _runs[project.id] = run
    run.thread = threading.Thread(target=_drive, args=(run,), daemon=True, name=f"scriptavera-{project.id}")
    run.thread.start()
    return True


def stop(pid: str) -> None:
    r = _runs.get(pid)
    if r:
        r.stop()


def wait_stopped(pid: str, seconds: float) -> None:
    r = _runs.get(pid)
    if r and r.thread and r.thread.is_alive():
        r.thread.join(seconds)


def _stage_func(sid: str):
    """The function that does a step. The writing, revision and final steps live in their own modules, which
    import this one, so they are looked up when needed rather than when this module loads."""
    if sid in STAGE_FUNCS:
        return STAGE_FUNCS[sid]
    from . import drafting, final, revision, writing
    return {"understand": writing.stage_understand, "assess": writing.stage_assess,
            "proofs": drafting.stage_proofs, "draft": drafting.stage_draft,
            "simplify": revision.stage_simplify, "verify": revision.stage_verify, "referee": revision.stage_referee,
            "assemble": final.stage_assemble}[sid]


def _review_not_needed(p: Project) -> str:
    """The transcript is approved without stopping when the upload leaves nothing to check: typed notes are their
    own transcript; in a PDF made on a computer the words are exact, and the author is asked only about figures,
    so the step stops only for a figure that needs a decision. Returns what to tell the author, or "" to stop."""
    kind = storelib.source_kind(p.state, p.dir)
    if kind == "handwritten" or pending_comments(p):
        return ""
    if kind == "typed":
        return "Your notes are typed: their text is the transcript, so there was nothing to read from an image or to check."
    present = {(f["page"], f["k"]) for f in transcript_figures(p)}
    if any((f["page"], f["k"]) in present and f.get("choice") not in ("tikz", "orig", "none", "explain")
           for f in (p.load("figures") or {}).get("items", [])):
        return ""
    return ("The words of your PDF were taken from the file, and no figure needed your decision, so the transcript "
            "was approved without stopping.")


def _gate_prepare(sid: str):
    """The work a stop for the author needs before it can be shown: the statements written, the structure planned.
    It runs as part of that step, so the author sees one step, not two."""
    if sid not in ("scope", "outline"):
        return None
    from . import drafting
    return {"scope": drafting.prepare_statements, "outline": drafting.prepare_outline}[sid]


def _gate_is_empty(p: Project, sid: str) -> bool:
    """A gate with nothing for the author to decide is passed without stopping. The statements and the structure
    always stop: what the paper claims, and in what order, is the author's to confirm."""
    if sid != "decide":
        return False
    from . import revision
    return revision.settle_if_nothing_to_decide(p)


GATE_PASSED = {
    "decide": "Nothing mathematical needed your decision, so this step was passed.",
}


def passing(e: BaseException) -> bool:
    """A failure that goes away by itself (a limit, the network, a busy model). A step waits for it instead of
    working around it with a poorer result."""
    return getattr(e, "kind", "") in ("limits", "network", "transient")


def _drive(run: Run) -> None:
    p = run.p
    tenancy.set_owner(tenancy.of_project(p.id))      # this thread uses its owner's keys and memory, no one else's
    literature.set_limits(p.state.get("meta", {}).get("test"))   # test mode: this thread searches only before the cutoff
    p.update_state(lambda st: st.update(running=True, error=None))
    run.changed()
    try:
        for stage in STAGES:
            sid = stage["id"]
            status = p.state["stages"].get(sid, {}).get("status")
            if status == "done":
                continue
            run.stage = sid
            if stage.get("gate"):
                passed = _review_not_needed(p) if sid == "review" else ""
                if passed:
                    submit_review(p, {})
                    p.set_stage(sid, status="done", finished=time.time(), skipped=True)
                    run.log(passed, "done")
                    continue
                if _gate_is_empty(p, sid):
                    p.set_stage(sid, status="done", finished=time.time(), skipped=True)
                    run.log(GATE_PASSED.get(sid, "Nothing needed your decision, so this step was passed."), "done")
                    continue
                prep = _gate_prepare(sid)
                if prep and not _do_step(run, sid, stage, prep, gate=True):
                    return
                p.set_stage(sid, status="waiting", since=time.time())
                run.log(f"Waiting for you: {stage['name'].lower()}.", "gate")
                return
            if not _do_step(run, sid, stage, _stage_func(sid)):
                return
            p.set_stage(sid, status="done", finished=time.time(), auto_tries=0, resume_at=None, error=None, weaker="", reason=None)
            run.log(f"{stage['name']} finished.", "done")
            run.changed()
    finally:
        p.update_state(lambda st: st.update(running=False))
        run.changed()


def _do_step(run: Run, sid: str, stage: dict, func, gate: bool = False) -> bool:
    """Run the work of one step. False when it stopped: by the author, or on a failure that is kept for Continue
    (or that passes by itself, and then the step resumes at the time said)."""
    p = run.p
    p.set_stage(sid, status="running", started=time.time(), error=None)
    run.changed()
    if not gate:
        run.log(f"{stage['name']} started.", "stage")
    try:
        func(run)
    except Cancelled:
        p.set_stage(sid, status="pending")
        run.log("Stopped by you. Everything finished so far is saved; press Continue to carry on from here.", "warn")
        return False
    except Exception as e:  # noqa: BLE001
        msg = str(e) if isinstance(e, (LLMError, ValueError)) else f"{type(e).__name__}: {e}"
        (p.dir / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        kind, retry_at = getattr(e, "kind", ""), float(getattr(e, "retry_at", 0) or 0)
        tries = int(p.state["stages"].get(sid, {}).get("auto_tries", 0))
        if retry_at and (kind == "limits" or tries < 5):
            # a failure that passes by itself: keep everything, wait, and start again from this point
            p.set_stage(sid, status="paused", error=msg, resume_at=retry_at, reason=kind, auto_tries=tries + 1,
                        weaker=getattr(e, "weaker", "") or "")
            p.update_state(lambda st: st.update(error={"stage": sid, "message": msg, "resume_at": retry_at, "kind": kind}))
            arm(p.id, retry_at)
            run.log(f"Paused: {msg} Progress is saved. It will continue by itself at {clock(retry_at)}.", "warn")
            return False
        p.set_stage(sid, status="error", error=msg, resume_at=None)
        p.update_state(lambda st: st.update(error={"stage": sid, "message": msg}))
        run.log(msg + " Progress is saved; press Continue to try again from this point.", "error")
        return False
    return True


# ---------------------------------------------------------------- helpers

def _page_bytes(p: Project, n: int) -> bytes:
    return (p.dir / "pages" / f"p{n:03d}.jpg").read_bytes()


def _workers(run: Run, role: str, vision: bool, jobs: int) -> int:
    """How many requests of one step go out together: as many per key as the setting parallel_pages (2 unless
    changed), for every key that can do the job with its best model; never more than 8 or than there are jobs."""
    per_key = max(1, min(4, int(run.settings.get("parallel_pages", 2))))
    try:
        lanes = router.lanes(role, vision)
    except Exception:  # noqa: BLE001 - when the keys cannot be counted, one key is assumed
        lanes = 1
    return max(1, min(jobs, 8, per_key * lanes))


def _side_by_side(run: Run, role: str, vision: bool, items: list, work) -> list:
    """work(item) for items that do not depend on each other, several at a time. Each request is exactly the one
    that would be sent if they went one by one: only the waiting is shared. The results come back in the order of
    the items. Every item is tried; then the first failure is raised (a stop asked for by the author first)."""
    if not items:
        return []
    results: list = [None] * len(items)
    errors: list[tuple[int, BaseException]] = []
    with ThreadPoolExecutor(max_workers=_workers(run, role, vision, len(items))) as ex:
        futs = {ex.submit(contextvars.copy_context().run, work, it): i for i, it in enumerate(items)}   # the same owner's keys
        for f in as_completed(futs):
            try:
                results[futs[f]] = f.result()
            except BaseException as e:  # noqa: BLE001 - raised below, once every item has been tried
                errors.append((futs[f], e))
                if isinstance(e, Cancelled):
                    for g in futs:
                        g.cancel()              # stopped by the author: what has not started is not started
    if errors:
        errors.sort(key=lambda x: x[0])
        raise next((e for _, e in errors if isinstance(e, Cancelled)), errors[0][1])
    return results


def exact_layers(p: Project) -> dict:
    """The text layers of the pages whose text a computer drew (ingest.py): their words are exact. A hidden reading
    of handwriting that a tablet or OCR put in the PDF is never given to the readers as exact."""
    layers = p.load("textlayer", {}) or {}
    ok = {str(pg["n"]) for pg in p.state.get("pages", []) if pg.get("text_layer")}
    return {n: v for n, v in layers.items() if n in ok}


def _content_pages(p: Project) -> list[dict]:
    return [pg for pg in p.state.get("pages", []) if not pg.get("skip")]


def _mark_page(p: Project, n: int, kind: str) -> None:
    def f(st):
        for pg in st["pages"]:
            if pg["n"] == n and pg.get("kind") != "included by you":    # the author's say beats a reader's label
                pg["kind"] = kind
                if kind in ("blank", "cover"):
                    pg["skip"] = True
    p.update_state(f)


def _kind(text: str, reading: str = "") -> str:
    k = extract_tag(text, "kind", "content").lower()
    m = re.match(r"\W*([a-z]+)", k)
    # the first word only; an echoed template ("content | blank | ...") or a comment must not skip a page
    kind = m.group(1) if m and "|" not in k and m.group(1) in ("blank", "cover", "figure", "content") else "content"
    if kind in ("blank", "cover") and not _looks_empty(reading):
        return "content"                       # a page with mathematics, print or a drawing is never skipped on a label
    return kind


def _looks_empty(reading: str) -> bool:
    """Does what was read from a page look like a cover or a blank page: a few words and nothing else?"""
    r = reading or ""
    if re.search(r"⟦|\$|\\\(|\\\[|\[FIGURE|\[MARGIN|\\begin\{", r):
        return False
    return len(re.findall(r"[^\W\d_]{2,}", r)) <= 15


def notation_text(confirmed: dict | None, limit: int = 2400) -> str:
    if not confirmed:
        return "(none confirmed yet)"
    lines = [f"{g['symbol']} = {g['meaning']}" for g in confirmed.get("glossary", []) if g.get("meaning")]
    for a in confirmed.get("answers", []):
        if a.get("answer"):
            lines.append(f"Author's answer to \"{a.get('question', '')}\": {a['answer']}")
    if confirmed.get("ignored"):
        lines.append("The author says these items from the draft are NOT notation of the notes (misread or irrelevant); "
                     "re-read them from the page without assuming they exist: " + ", ".join(confirmed["ignored"]))
    if confirmed.get("instructions"):
        lines.append("Author's instructions: " + confirmed["instructions"])
    return fit("\n".join(lines) or "(none)", limit, "head")


def _topic_text(p: Project) -> tuple[str, list[str]]:
    conf = p.load("confirmed") or {}
    topic = {**(p.load("topic") or {}), **(conf.get("topic") or {})}
    own = (p.state.get("meta", {}).get("problem") or "").strip()
    problem = topic.get("problem", "")
    if own and own not in problem:
        problem = f"{problem}\nIn the author's words: {own}".strip()
    return problem, topic.get("keywords", [])


def _room(role: str, system: str, out_tokens: int, vision: bool = False) -> int:
    """Characters of user text that fit in one request for this role."""
    budget = router.budget(role, vision)
    tokens = budget - est_tokens(system) - out_tokens - (2100 if vision else 0) - 300
    return max(1200, int(tokens * 3))


def _as_list(v) -> list[str]:
    """Models sometimes answer null or one string where a list was asked for."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"\s*[;\n]\s*", v) if x.strip()]
    return []


def _json(run: Run, role: str, system: str, user: str, label: str, max_out: int = 1500) -> dict:
    last: Exception | None = None
    for attempt, r in enumerate((role, "reason")):
        try:
            res = run.ask(r, system, user, json_mode=True, max_out=max_out, label=label + (" (retry)" if attempt else ""),
                          effort="low")
            data = loads_lenient(res.text)
            if isinstance(data, dict):
                return data
        except (ValueError, LLMError) as e:
            if passing(e):
                raise
            last = e
    raise LLMError(f"Could not get a usable answer for '{label}': {last}")


# ------------------------------------------------------------ 1. scout

def _scout_order(pages: list[dict]) -> list[int]:
    """First pages first (titles and problem statements live there), then an even spread."""
    ns = [pg["n"] for pg in pages]
    head = ns[:3]
    rest = ns[3:]
    spread: list[int] = []
    if rest:
        step = max(1, len(rest) // 4)
        picks = rest[step - 1::step]
        spread = picks + [n for n in rest if n not in picks]
    return head + spread


SCOUT_LEAST = 5            # pages read before the topic is decided (all of them, if there are fewer)
SCOUT_CONFIDENCE = 0.85    # and read on until the topic is at least this clear
SCOUT_GAIN_MIN = 0.03      # stop reading extra pages if confidence gain per new page is less than this


def stage_scout(run: Run) -> None:
    p = run.p
    pages = _content_pages(p)
    if not pages:
        raise ValueError("Every page looks blank. Check the upload.")
    scout: dict = p.load("scout", {})
    order = _scout_order(pages)
    threshold = max(SCOUT_CONFIDENCE, float(run.settings.get("scout_confidence", SCOUT_CONFIDENCE)))
    least = min(int(run.settings.get("scout_least", SCOUT_LEAST)), len(pages))
    gain_min = float(run.settings.get("scout_gain_min", SCOUT_GAIN_MIN))
    layers = exact_layers(p)
    system = prompts.SCOUT.format(field=run.field)
    total = len(order)
    topic: dict = p.load("topic", {})
    _resolve_key_papers(run)
    rounds = 0
    prev_conf: float | None = None

    while True:
        read_content = sum(1 for v in scout.values() if v["kind"] == "content")
        if topic and read_content >= least:
            conf = float(topic.get("confidence", 0))
            if conf >= 0.95:
                run.log(f"Topic confidence reached {int(conf * 100)}%: stopping scout.")
                break
            if prev_conf is not None:
                gain = conf - prev_conf
                if conf >= threshold and gain < gain_min:
                    run.log(f"Topic confidence held at {int(conf * 100)}% (gain {int(gain * 100)}% < {int(gain_min * 100)}%): stopping scout.")
                    break
                if conf < threshold and read_content >= least + 3 and gain <= 0.01:
                    run.log(f"Topic confidence plateaued at {int(conf * 100)}%: stopping scout.")
                    break

        batch_size = max(1, least - read_content) if read_content < least else 1
        todo = [n for n in order if str(n) not in scout][:batch_size]
        if not todo:
            break
        prev_conf = float(topic.get("confidence", 0)) if topic else None
        rounds += 1
        typed_src = (p.load("typed") or {}).get("pages", {})
        lock = threading.Lock()

        def look(n: int) -> None:
            if str(n) in typed_src or str(n) in layers:   # typed notes, or a PDF's text layer: the words are known
                src = typed_src.get(str(n)) or layers[str(n)]
                rec = {"kind": "content", "prose": fit(re.sub(r"\$[^$]*\$|\\\[.*?\\\]", " ", src, flags=re.S), 2500, "head"),
                       "printed": "", "terms": [], "names": [],
                       "model": "typed source" if str(n) in typed_src else "text layer of the PDF"}
            else:
                res = run.ask("vision_fast", system, f"This is page {n} of {len(p.state['pages'])}.",
                              images=[_page_bytes(p, n)], max_out=1400, effort="low", label=f"Scout page {n}")
                printed = extract_tag(res.text, "printed")
                kind = _kind(res.text, extract_tag(res.text, "prose") + "\n"
                             + ("" if printed.strip().lower() in ("", "none", "(none)") else "$" + printed))
                rec = {
                    "kind": kind, "prose": extract_tag(res.text, "prose"),
                    "printed": extract_tag(res.text, "printed"),
                    "terms": parse_list(extract_tag(res.text, "terms")),
                    "names": parse_list(extract_tag(res.text, "names")), "model": res.model,
                }
                if rec["printed"].lower() in ("none", "(none)"):
                    rec["printed"] = ""
            with lock:
                scout[str(n)] = rec
                p.save("scout", scout)
                if rec["kind"] in ("blank", "cover"):
                    _mark_page(p, n, rec["kind"])
                run.progress(len(scout), total, f"page {n} read")

        _side_by_side(run, "vision_fast", True, todo, look)    # the pages of one round do not depend on each other
        run.progress(len(scout), total, f"{plural(len(scout), 'page')} read · working out the topic from them", more=1)
        topic = _make_topic(run, scout)
        topic["pages_read"] = sorted(int(k) for k in scout)
        p.save("topic", topic)
        run.changed()
        conf = float(topic.get("confidence", 0))
        run.log(f"Topic confidence {int(conf * 100)}% after {len(scout)} page(s): {topic.get('subfield', '')}")
        read_content = sum(1 for v in scout.values() if v["kind"] == "content")
        if read_content >= least:
            if conf >= 0.95:
                run.log(f"Topic confidence reached {int(conf * 100)}%: stopping scout.")
                break
            if prev_conf is not None:
                gain = conf - prev_conf
                if conf >= threshold and gain < gain_min:
                    run.log(f"Topic confidence held at {int(conf * 100)}% (gain {int(gain * 100)}% < {int(gain_min * 100)}%): stopping scout.")
                    break
                if conf < threshold and read_content >= least + 3 and gain <= 0.01:
                    run.log(f"Topic confidence plateaued at {int(conf * 100)}%: stopping scout.")
                    break
        if [n for n in order if str(n) not in scout]:
            run.log(f"Reading more pages: the topic is {int(conf * 100)}% clear"
                    + (f", and at least {least} pages are read" if read_content < least else f"; checking if extra pages increase confidence")
                    + (f" ({topic['missing']})" if topic.get("missing") and read_content >= least else "") + ".")
    if not topic:
        topic = _make_topic(run, scout)
        p.save("topic", topic)
    run.progress(len(scout), len(scout), "topic identified")


def author_brief(run: Run) -> str:
    """What the author said about the notes before anything was read: the problem, the keywords, the papers the work
    relies on most, and any remark. It goes with every request that has to understand the topic."""
    m = run.meta
    out = []
    if (m.get("problem") or "").strip():
        out.append(f"The problem, in the author's words: {m['problem'].strip()}")
    if m.get("keywords"):
        out.append("The author's keywords: " + ", ".join(m["keywords"]))
    if (m.get("subarea") or "").strip():
        out.append(f"Sub-area named by the author: {m['subarea'].strip()}")
    if (m.get("hints") or "").strip():
        out.append(f"The author adds: {m['hints'].strip()}")
    kp = run.p.load("key_papers") or []
    if kp:
        out.append("The papers the work relies on most, named by the author:\n" + "\n".join(
            f"- {x.get('title') or x.get('ident')}" + (f" ({x['year']})" if x.get("year") else "")
            + (f": {fit(x.get('abstract', ''), 600, 'head')}" if x.get("abstract") else "") for x in kp))
    notes = (run.p.load("topic") or {}).get("author_notes") or []
    if notes:
        out.append("The author's comments on earlier summaries:\n" + "\n".join(f"- {n}" for n in notes))
    return "\n".join(out)


def _author_first(run: Run, keywords: list[str]) -> list[str]:
    own = [k.strip() for k in run.meta.get("keywords") or [] if k.strip()]
    seen = {k.lower() for k in own}
    return own + [k for k in keywords if k.lower() not in seen]


def _resolve_key_papers(run: Run) -> list[dict]:
    """The one or two papers the author named as the most important, looked up before the first reading so that the
    topic is understood from them as well."""
    p = run.p
    have = p.load("key_papers")
    if have is not None:
        return have
    out = []
    for ident in [k for k in run.meta.get("key_papers") or [] if k.strip()][:3]:
        run.check()
        got = None if config.mock_mode() else literature.resolve_identifier(ident)
        if got:
            got.update(score=10.0, why="Named by you as a paper the work relies on", pinned=True, state="selected", key_paper=True)
            out.append(got)
            run.log(f"Found the paper you named: {got['title'][:90]}")
        else:
            out.append({"ident": ident, "title": ident if config.mock_mode() else "", "missing": not config.mock_mode()})
            if not config.mock_mode():
                run.log(f"Could not find the paper you named: {ident}. Check the DOI or arXiv number; the search goes on without it.", "warn")
    p.save("key_papers", out)
    return out


def _make_topic(run: Run, scout: dict) -> dict:
    parts = []
    for n in sorted(scout, key=int):
        s = scout[n]
        if s["kind"] in ("blank", "cover"):
            continue
        block = f"--- page {n} ---\n{s['prose']}"
        if s.get("printed"):
            block += f"\nPRINTED TEXT ON THIS PAGE: {s['printed']}"
        if s.get("terms"):
            block += f"\nTERMS: {', '.join(s['terms'])}"
        if s.get("names"):
            block += f"\nNAMES: {', '.join(s['names'])}"
        parts.append(block)
    system = prompts.TOPIC.format()
    user = (f"Declared field: {run.field}\n{author_brief(run)}\n"
            + "\nFirst reading (formulas skipped):\n\n" + fit("\n\n".join(parts), _room("fast", system, 1200), "head"))
    try:
        data = _json(run, "fast", system, user, "Identify topic", 1200)
    except LLMError as e:
        if passing(e):
            raise
        terms = [t for s in scout.values() for t in s.get("terms", [])]
        data = {"subfield": "", "problem": "", "keywords": list(dict.fromkeys(terms))[:12],
                "queries": [" ".join(terms[i:i + 3]) for i in range(0, min(len(terms), 12), 3)], "confidence": 0.3,
                "missing": "The topic could not be summarised automatically."}
    return {
        "subfield": str(data.get("subfield", "")), "problem": str(data.get("problem", "")),
        "keywords": _author_first(run, _as_list(data.get("keywords")))[:16],
        "queries": _as_list(data.get("queries"))[:8],
        "anchor": str(data.get("anchor", "") or ""), "confidence": to_float(data.get("confidence"), 0.5),
        "missing": str(data.get("missing", "")),
    }


# --------------------------------------------------------- 2. search

def _rerank(run: Run, papers: list[dict], problem: str, keywords: list[str], label: str) -> None:
    if not papers:
        return
    system = prompts.RERANK.format()
    per = 12 if router.budget("fast") < 12000 else 24
    for start in range(0, len(papers), per):
        chunk = papers[start:start + per]
        lines = []
        for i, pp in enumerate(chunk, 1):
            lines.append(f"[{i}] {pp['title']} ({pp.get('year') or 'n.d.'}) "
                         f"{'; '.join((pp.get('cats') or [])[:2] + (pp.get('msc') or [])[:2])}\n"
                         f"    {fit(pp.get('abstract') or 'No abstract available.', 380, 'head')}")
        user = (f"Declared field: {run.field}\nThe author's problem: {problem}\nKeywords: {', '.join(keywords)}\n\n"
                "Candidates:\n" + "\n".join(lines))
        try:
            data = _json(run, "fast", system, user, f"{label} ({start + 1}-{start + len(chunk)})", 1400)
        except LLMError as e:
            if passing(e):
                raise
            continue
        for item in data.get("scores", []) if isinstance(data.get("scores"), list) else []:
            try:
                idx = int(item.get("i")) - 1
                if 0 <= idx < len(chunk):
                    chunk[idx]["score"] = max(0.0, min(10.0, float(item.get("score", 0))))
                    chunk[idx]["why"] = str(item.get("why", ""))[:160]
            except (TypeError, ValueError, AttributeError):
                continue


def _select(library: list[dict], keep: int) -> None:
    """Mark the best candidates as selected; never touch the author's own choices."""
    ranked = sorted([x for x in library if x.get("score") is not None], key=lambda x: -x["score"])
    chosen = {id(x) for x in ranked[:keep] if x["score"] >= 5}
    for x in library:
        if x.get("pinned") or x.get("state") == "excluded":
            continue
        x["state"] = "selected" if id(x) in chosen else "candidate"


def _search_and_rank(run: Run, queries: list[str], keywords: list[str], problem: str, label: str) -> dict:
    p = run.p
    doms = run.meta.get("domains", [])
    library: list[dict] = p.load("library", [])
    found, status = literature.search(queries, doms, log=run.log, cancelled=run.cancelled)
    added = literature.add_papers(library, found)
    run.log(f"{len(found)} results from the databases, {added} new papers.")
    for x in library:
        x["lex"] = literature.lexical_score(x, keywords, doms)
    fresh = sorted([x for x in library if x.get("score") is None], key=lambda x: -x["lex"])[:60]
    _rerank(run, fresh, problem, keywords, label)
    for x in library:                       # anything never scored stays as a weak candidate
        if x.get("score") is None:
            x["score"] = 0.0
    library[:] = [x for x in library if x.get("pinned") or (x.get("score") or 0) >= 3 or x.get("read")]
    library.sort(key=lambda x: (-(x.get("score") or 0), -x.get("lex", 0)))
    _select(library, int(run.settings.get("candidates_keep", 24)))
    p.save("library", library)
    return status


def _refine_topic(run: Run, topic: dict, note: str) -> dict:
    """The author's comment on the summary and the papers found: the summary is rewritten to follow it."""
    lib = [x for x in run.p.load("library", []) if x.get("state") != "candidate"][:12]
    excluded = [x for x in run.p.load("library", []) if x.get("state") == "excluded"][:12]
    system = prompts.TOPIC_REFINE.format()
    user = (f"Declared field: {run.field}\n{author_brief(run)}\n\nTHE SUMMARY THE AUTHOR READ\n"
            + json.dumps({k: topic.get(k) for k in ("subfield", "problem", "keywords", "queries")}, ensure_ascii=False)
            + "\n\nPAPERS FOUND FROM IT\n" + ("\n".join(f"- {x['title']}" for x in lib) or "(none)")
            + ("\n\nPAPERS THE AUTHOR EXCLUDED AS OFF-TOPIC\n" + "\n".join(f"- {x['title']}" for x in excluded) if excluded else "")
            + f"\n\nTHE AUTHOR'S COMMENT\n{note}")
    data = _json(run, "fast", system, user, "Refine the topic with your comment", 1200)
    new = dict(topic)
    for k in ("subfield", "problem", "anchor", "missing"):
        if isinstance(data.get(k), str):
            new[k] = data[k]
    new["keywords"] = _author_first(run, _as_list(data.get("keywords")) or topic.get("keywords", []))[:16]
    new["queries"] = _as_list(data.get("queries"))[:8] or topic.get("queries", [])
    new["confidence"] = to_float(data.get("confidence"), topic.get("confidence", 0.5))
    return new


def stage_search1(run: Run) -> None:
    p = run.p
    topic = p.load("topic", {})
    library: list[dict] = p.load("library", [])
    note = (topic.get("pending_note") or "").strip()
    if note or topic.get("pending_refresh"):
        run.progress(0, 3, "rewriting the topic with your comment")
        topic = _refine_topic(run, topic, note or "The author changed the description of the problem and the keywords given above.")
        topic.pop("pending_note", None)
        topic.pop("pending_refresh", None)
        topic["refined"] = int(topic.get("refined", 0)) + 1
        p.save("topic", topic)
        for x in library:                          # every paper is judged again against the corrected topic
            if not x.get("pinned") and x.get("state") != "excluded":
                x["score"] = None
        p.save("library", library)
        run.log(f"Topic rewritten with your comment: {topic.get('subfield', '')}")
    named = [dict(kp) for kp in p.load("key_papers") or [] if kp.get("title") and "ident" not in kp]
    if named:                                      # the papers the author named come first, and are read first
        literature.add_papers(library, named)
        ids = {i for kp in named for i in literature.identity(kp)}
        for x in library:
            if ids & set(literature.identity(x)):
                x.update(pinned=True, state="selected", score=10.0, key_paper=True, why="Named by you as a paper the work relies on")
    known = [k for k in run.meta.get("known_refs", []) if k.strip()]
    for ident in known:
        run.check()
        if config.mock_mode():
            break
        got = literature.resolve_identifier(ident)
        if got:
            literature.add_papers(library, [got])
            run.log(f"Added your reference: {got['title'][:80]}")
        else:
            run.log(f"Could not find the reference you gave: {ident}", "warn")
    p.save("library", library)
    queries = list(topic.get("queries", []))
    if topic.get("anchor"):
        queries.insert(0, " ".join(re.findall(r"[A-Za-z]{3,}", topic["anchor"])[:8]))
    if run.meta.get("subarea", "").strip() and len(queries) < 9:
        queries.append(run.meta["subarea"].strip())
    queries = [q for q in dict.fromkeys(queries) if q.strip()]
    if not queries:
        raise ValueError("No search queries could be formed from the notes. Add a sub-area description and retry.")
    run.progress(0, 2, "querying databases")
    status = _search_and_rank(run, queries, topic.get("keywords", []), topic.get("problem", ""), "Rank papers")
    p.save("search_status", {"round1": status, "queries1": queries})
    lib = p.load("library", [])
    run.progress(2, 2, f"{sum(1 for x in lib if x['state'] == 'selected')} relevant papers")
    if not lib:
        run.log("No papers were found. The pipeline continues, but notation proposals and the introduction "
                "will have no literature behind them. You can add papers by DOI or arXiv id at the next step.", "warn")


# ------------------------------------------------------------ 3. survey

def _norm_symbol(s: str) -> str:
    s = s.strip().strip("$").strip()
    s = re.sub(r"\\left|\\right|\s+", "", s)
    return s


def _loose(s: str) -> str:
    """Letters and digits only, so '{2,3}-group' and '(2,3) group' are the same item."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def rebuild_glossary(p: Project) -> dict:
    """Work out the notation list again from the saved page readings. No page is read again."""
    survey = p.load("survey")
    if not survey:
        raise ValueError("The pages have not been surveyed yet.")
    run = Run(p)
    run.stage = "survey"
    _build_glossary(run, survey)
    run.progress(len(survey), len(survey), "notation catalogued")
    return p.load("glossary")


def stage_survey(run: Run) -> None:
    p = run.p
    pages = _content_pages(p)
    survey: dict = p.load("survey", {})
    problem, keywords = _topic_text(p)
    library = p.load("library", [])
    top = [x for x in library if x["state"] == "selected"][:5]
    lit = ""
    if top:
        lit = "Closest papers found so far (for vocabulary only):\n" + "\n".join(
            f"- {x['title']}: {fit(x.get('abstract', ''), 260, 'head')}" for x in top)
    system = prompts.SURVEY.format(field=run.field, problem=problem or "(not yet clear)",
                                   keywords=", ".join(keywords) or "(none)", lit=fit(lit, 1500, "head"))
    todo = [pg["n"] for pg in pages if str(pg["n"]) not in survey]
    total = len(pages)
    lock = threading.Lock()
    errors: list[str] = []

    typed_src = (p.load("typed") or {}).get("pages", {})
    layers = exact_layers(p)

    def work(n: int) -> None:
        src = typed_src.get(str(n))
        if src is not None:                    # typed: only the notation is catalogued; the source is the transcript
            res = run.ask("vision_best", system, f"This is page {n} of {len(p.state['pages'])}. These notes are TYPED, not "
                          "handwritten: below is the page exactly as the author wrote it. Copy it unchanged as the transcript "
                          f"and catalogue the notation as instructed.\n\nSOURCE OF THE PAGE\n{src}", max_out=3200,
                          effort="medium", label=f"Survey page {n} (typed)")
        else:
            layer = layers.get(str(n))             # a PDF made on a computer: its exact words go beside the image
            res = run.ask("vision_best", system, f"This is page {n} of {len(p.state['pages'])}." + (
                "\n\nTEXT LAYER OF THIS PAGE (copied from the PDF file itself: its words are exact, its formulas are not, "
                "because superscripts, fractions and symbols are flattened in it; read the formulas from the image)\n"
                + fit(layer, 2500, "head") if layer else ""),
                          images=[_page_bytes(p, n)], max_out=3200, effort="medium", label=f"Survey page {n}")
        kind = _kind(res.text, answer_transcript(res.text))
        symbols = []
        for row in parse_rows(extract_tag(res.text, "symbols")):
            if not row[0] or len(row[0]) > 80:
                continue
            defined = len(row) > 2 and row[2].strip().upper().startswith("DEF")
            symbols.append({"symbol": row[0].strip().strip("$"), "use": row[1] if len(row) > 1 else "",
                            "defined": defined, "definition": row[3] if defined and len(row) > 3 else ""})
        terms = [{"term": r[0], "use": r[1] if len(r) > 1 else ""} for r in parse_rows(extract_tag(res.text, "terms"))
                 if r[0] and len(r[0]) < 80]
        rec = {"kind": "notes" if src is not None else kind, "text": src if src is not None else collapse_loops(clean_transcript(answer_transcript(res.text))),
               "symbols": symbols, "terms": terms, "gist": extract_tag(res.text, "gist"), "model": res.model}
        kind = rec["kind"]
        with lock:
            survey[str(n)] = rec
            p.save("survey", survey)
            if kind in ("blank", "cover"):
                _mark_page(p, n, kind)
            run.progress(len(survey), total, f"page {n} surveyed", more=1)    # the notation is resolved after the pages

    workers = _workers(run, "vision_best", True, len(todo))     # more keys, more pages at a time
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(contextvars.copy_context().run, work, n): n for n in todo}     # the same owner's keys
        for f in as_completed(futs):
            try:
                f.result()
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append(f"page {futs[f]}: {e}")
    run.check()
    if errors:
        raise LLMError("Some pages could not be read (" + "; ".join(errors[:3]) + "). Press Resume to retry them.")
    if p.state["stages"].get("confirm", {}).get("status") == "done" and p.load("glossary"):
        # a page added after the author confirmed the notation: that confirmation stands, nothing is asked again
        run.log(f"{plural_pages(len(todo))} read and added to the notes.", "done")
        run.progress(total, total, "pages added")
        return
    run.progress(total, total, "all pages read · now resolving the notation they use, for all pages together", more=1)
    _build_glossary(run, survey)
    run.progress(total, total, "notation catalogued")


def plural_pages(k: int) -> str:
    return "1 page" if k == 1 else f"{k} pages"


def unread_pages(p: Project) -> list[int]:
    """Pages with the author's work on them that were never read: left out on a reader's label although something
    was read on them, or put back by the author but never transcribed."""
    st = p.state
    stages = st["stages"]
    scout = p.load("scout") or {}
    survey = p.load("survey") or {}
    tr = (p.load("transcript") or {}).get("pages", {})
    out = []
    for pg in st.get("pages", []):
        n = str(pg["n"])
        if pg.get("skip"):
            if pg.get("kind") not in ("cover", "blank") or pg.get("decorative"):
                continue                       # left out by the author, or a coloured notebook cover by the image test
            sc, sv = scout.get(n) or {}, survey.get(n) or {}
            printed = sc.get("printed", "")
            reading = "\n".join([sc.get("prose", ""), "$" + printed if printed else "", sv.get("text", "")])
            if not _looks_empty(reading):
                out.append(pg["n"])
        elif stages.get("transcribe", {}).get("status") == "done" and n not in tr \
                and (survey.get(n) or {}).get("kind") not in ("blank", "cover"):
            out.append(pg["n"])
    return out


def recover_pages(p: Project) -> list[int]:
    """Put back, and queue for reading, every page that holds work but was never read. Pages after an approved
    transcript are not touched (that needs the author's say). Returns the pages queued."""
    st = p.state
    if is_running(p.id) or st["stages"].get("review", {}).get("status") == "done" \
            or any(v.get("status") in ("error", "paused", "running") for v in st["stages"].values()):
        return []
    tried = set(st.get("recovered") or [])
    todo = [n for n in unread_pages(p) if n not in tried]     # once per page: a failed reading never loops
    if not todo:
        return []
    p.update_state(lambda s: s.update(recovered=sorted(tried | set(todo))))
    got = [n for n in todo if include_page(p, n) == "now"]
    if got:
        names = ", ".join(str(n) for n in got)
        emit(p.id, "log", stage="transcribe", level="warn",
             message=f"Page{'s' if len(got) > 1 else ''} {names} {'have' if len(got) > 1 else 'has'} your work on "
                     f"{'them' if len(got) > 1 else 'it'} but had not been read. Reading now.")
    return got


def reset_leaked_draft(p: Project) -> bool:
    """A first draft written before the reasoning guard may contain a model's thinking. Such a draft is written
    again, once, from the structure step; the reading, the introduction and the author's answers are kept."""
    st = p.state
    if is_running(p.id) or st.get("leak_reset") or st["stages"].get("assemble", {}).get("status") == "done":
        return False
    body = p.load("body") or {}
    if not any(leaks(b.get("text", "")) or leaks(b.get("title", "")) for b in body.get("blocks", [])):
        return False
    p.update_state(lambda x: x.update(leak_reset=time.time()))
    p.reset_from("proofs")
    emit(p.id, "log", stage="proofs", level="warn",
         message="The first draft contained a model's own reasoning instead of paper text. The proofs are written again "
                 "now; your reading, notation, review, statements and structure are kept.")
    return True


def reset_incomplete_draft(p: Project) -> bool:
    """A first draft written section by section from the pages (as earlier versions did) could stop part of the way
    through the notes, and was then written again. The draft is now put together by the program from the statements
    and proofs the author approved, and the notes are cut on purpose, so there is nothing of this kind to repair."""
    return False


def add_pages(p: Project, new: list[dict]) -> dict:
    """Pages added to notes that already have a paper. Only the new pages are read; the steps after them run again,
    but what is unchanged is kept: a group of pages written before keeps its text, an unchanged proof keeps its
    check, an unchanged part keeps its copy-edit (the write, verify and polish caches survive the reset)."""
    ns = [pg["n"] for pg in new]

    def f(st):
        for pg in new:
            pg["added"] = True
        st["pages"].extend(new)
        inc = st.get("incremental") or {"rounds": []}
        inc["rounds"].append({"at": time.time(), "pages": ns})
        st["incremental"] = inc
    p.update_state(f)
    for n in ns:
        include_page(p, n, force=True)
    emit(p.id, "log", stage="transcribe", level="info",
         message=f"{plural_pages(len(ns)).capitalize()} added ({ns[0]}" + (f"–{ns[-1]}" if len(ns) > 1 else "") + "). Only "
                 "these are read; the paper is then put together again, keeping every part that has not changed.")
    return {"pages": ns}


def include_page(p: Project, n: int, force: bool = False) -> str:
    """The author puts a left-out page back. Steps that read page by page are opened again so that only this page is
    read; nothing already read is read again. Returns "now" (it will be read now), "later" (the steps that read it
    have not run yet) or "confirm" (the transcript was already approved: the author must agree to redo from there)."""
    stages = p.state["stages"]
    started = lambda sid: stages.get(sid, {}).get("status") not in (None, "pending")
    if started("review") and stages["review"].get("status") == "done" and not force:
        return "confirm"

    def f(st):
        for pg in st["pages"]:
            if pg["n"] == n:
                pg["skip"], pg["kind"] = False, "included by you"
    p.update_state(f)
    with p._lock:                              # a reading that called the page empty is not kept: it is read again
        survey = p.load("survey") or {}
        if survey.get(str(n), {}).get("kind") in ("blank", "cover"):
            del survey[str(n)]
            p.save("survey", survey)
    if started("review") and stages["review"].get("status") == "done":
        p.reset_from("review")
    opened = False
    confirmed = stages.get("confirm", {}).get("status") == "done"
    for sid in ("survey", "transcribe", "figures"):
        if sid == "survey" and confirmed:
            continue                           # the notation is settled: the page goes straight to the final reading
        if started(sid):
            p.set_stage(sid, status="pending")
            opened = True
    if opened and stages.get("review", {}).get("status") == "waiting":
        p.set_stage("review", status="pending")
    return "now" if opened else "later"


def _build_glossary(run: Run, survey: dict) -> None:
    p = run.p
    problem, _ = _topic_text(p)
    items: dict[str, dict] = {}
    for n in sorted(survey, key=int):
        rec = survey[n]
        if rec["kind"] in ("blank", "cover"):
            continue
        page_text = re.sub(r"[\s${}]+", "", rec.get("text", ""))
        for s in rec.get("symbols", []):
            key = _norm_symbol(s["symbol"])
            if not key:
                continue
            if page_text and re.sub(r"[\s${}]+", "", s["symbol"]) not in page_text:
                continue                               # listed by the reader but absent from its own reading of the page
            it = items.setdefault(key, {"symbol": s["symbol"], "kind": "symbol", "pages": [], "uses": [], "defs": []})
            if int(n) not in it["pages"]:
                it["pages"].append(int(n))
            if s.get("use") and s["use"] not in it["uses"] and len(it["uses"]) < 3:
                it["uses"].append(s["use"])
            if s.get("defined") and s.get("definition"):
                it["defs"].append(f"p.{n}: {s['definition']}")
        for t in rec.get("terms", []):
            if rec.get("text") and _loose(t["term"]) not in _loose(rec["text"]):
                continue
            key = "term:" + t["term"].lower().strip()
            it = items.setdefault(key, {"symbol": t["term"], "kind": "term", "pages": [], "uses": [], "defs": []})
            if int(n) not in it["pages"]:
                it["pages"].append(int(n))
            if t.get("use") and t["use"] not in it["uses"] and len(it["uses"]) < 3:
                it["uses"].append(t["use"])
    ordered = sorted(items.values(), key=lambda x: (-len(x["pages"]), x["pages"][0] if x["pages"] else 0))[:70]
    library = p.load("library", [])
    lit = "\n".join(f"[{x['key']}] {x['title']} ({x.get('year') or 'n.d.'}): {fit(x.get('abstract', ''), 420, 'head')}"
                    for x in library if x["state"] == "selected")
    system = prompts.GLOSSARY.format(field=run.field, problem=problem or "(not yet clear)")
    room = _room("reason", system, 2600)
    lit = fit(lit, max(800, room // 3), "head")
    per = max(8, min(35, (room - len(lit)) // 330))
    entries: list[dict] = []
    questions: list[dict] = []
    by_loose = {_loose(it["symbol"]): it for it in ordered}
    asks = []
    for start in range(0, len(ordered), per):
        chunk = ordered[start:start + per]
        lines = []
        for it in chunk:
            line = f"- {it['symbol']} ({it['kind']}; pages {', '.join(map(str, it['pages'][:8]))})"
            if it["uses"]:
                line += f"; used as: {' / '.join(it['uses'])}"
            if it["defs"]:
                line += f"; defined in the notes: {' | '.join(it['defs'][:2])}"
            lines.append(line)
        user = ("Items collected from the notes:\n" + "\n".join(lines)
                + "\n\nRelated papers found (use them for conventions; cite by KEY in 'basis'):\n" + (lit or "(none found)"))
        asks.append((user, f"Resolve notation ({start + 1}-{start + len(chunk)})"))
    parts, lock = [0], threading.Lock()

    def resolve(a):
        res = run.ask("reason", system, a[0], max_out=2600, label=a[1])
        with lock:
            parts[0] += 1
            run.progress(len(survey), len(survey), f"all pages read · resolving the notation, part {parts[0]} of {len(asks)} done", more=1)
        return res

    if len(asks) > 1:
        run.progress(len(survey), len(survey), f"all pages read · resolving the notation in {len(asks)} parts", more=1)
    answers = _side_by_side(run, "reason", False, asks, resolve)   # the parts do not depend on each other
    for res in answers:
        for rec in parse_records(res.text, "entry"):
            sym = rec.get("symbol", "").strip().strip("$")
            if not sym:
                continue
            src = items.get(_norm_symbol(sym)) or items.get("term:" + sym.lower()) or by_loose.get(_loose(sym)) or {}
            if not src or _loose(sym) in ("blank", "content", "cover", "figure", "none", ""):
                continue                              # not something collected from the pages
            status = rec.get("status", "undefined").lower().strip()
            status = next((s for s in ("misread", "undefined", "ambiguous", "standard", "defined") if s in status), "undefined")
            if status == "undefined" and src.get("defs"):
                status = "defined"                    # the notes do define it, on another page
            alts = [a for a in re.split(r"\s*;\s*", rec.get("alternatives", "")) if a and a.lower() != "none"]
            entries.append({
                "symbol": sym, "kind": "term" if "term" in rec.get("kind", "").lower() else "symbol",
                "status": status, "meaning": rec.get("meaning", "").strip(),
                "confidence": to_float(rec.get("confidence"), 0.5), "basis": rec.get("basis", "").strip(),
                "alternatives": alts[:4], "question": rec.get("question", "").strip(),
                "pages": src.get("pages", []), "uses": src.get("uses", []),
            })
        for row in parse_rows(extract_tag(res.text, "questions"), min_cols=2):
            pg = re.search(r"\d+", row[0])
            questions.append({"page": int(pg.group(0)) if pg else 0, "question": row[1]})
    seen = set()
    unique = []
    for e in entries:
        k = _loose(e["symbol"])
        if k in seen:
            continue
        seen.add(k)
        unique.append(e)
    order = {"ambiguous": 0, "undefined": 1, "misread": 2, "defined": 3, "standard": 4}
    unique.sort(key=lambda e: (order[e["status"]], e["pages"][0] if e["pages"] else 999))
    from . import memory
    doms = run.meta.get("domains", [])
    known = 0
    for e in unique:                                  # a meaning the author gave in an earlier paper is proposed again
        r = memory.remembered(e["symbol"], doms)
        if r:
            e["remembered"] = r
            if e["status"] in ("undefined", "ambiguous"):
                e["meaning"], e["basis"], known = r["meaning"], f"your meaning in an earlier paper ({r['paper'] or 'earlier'}), {r['times']}\u00d7", known + 1
    if known:
        run.log(f"{known} symbol(s) you explained in earlier papers are proposed with your meaning; change any that differ here.")
    for i, e in enumerate(unique):
        e["id"] = f"g{i + 1}"
    for i, q in enumerate(questions):
        q["id"] = f"q{i + 1}"
    old = p.load("glossary")
    if old:
        p.save("glossary_previous", old)
    p.save("glossary", {"entries": unique, "questions": questions[:12], "built": time.time()})
    need = sum(1 for e in unique if e["status"] in ("undefined", "ambiguous"))
    run.log(f"{len(unique)} notation items catalogued; {need} need your confirmation.")


# ----------------------------------------------------------- 4. reading

def stage_reading(run: Run) -> None:
    p = run.p
    conf = p.load("confirmed", {})
    problem, keywords = _topic_text(p)
    notation = notation_text(conf)
    doms = run.meta.get("domains", [])

    # 4a. refined search using the confirmed meanings
    run.progress(0, 4, "refining the search")
    system = prompts.QUERIES2.format()
    user = f"Declared field: {run.field}\nConfirmed problem: {problem}\nConfirmed notation:\n{notation}"
    try:
        q2 = _json(run, "fast", system, user, "Refine search queries", 900)
        queries = _as_list(q2.get("queries"))[:8]
        keywords = _as_list(q2.get("keywords"))[:16] or keywords
    except LLMError as e:
        if passing(e):
            raise
        queries = []
    status = {}
    if queries:
        status = _search_and_rank(run, queries, keywords, problem, "Rank new papers")

    # 4b. follow the citation trail from the closest papers
    library: list[dict] = p.load("library", [])
    run.progress(1, 4, "following citations")
    if not config.mock_mode():
        seeds = [x for x in library if x["state"] == "selected" and (x.get("score") or 0) >= 8][:3]
        neighbours: list[dict] = []
        for s in seeds:
            run.check()
            try:
                neighbours += literature.openalex_neighbors(s)
            except Exception:  # noqa: BLE001
                continue
        if neighbours:
            before = {id(x) for x in library}
            literature.add_papers(library, neighbours)
            fresh = [x for x in library if id(x) not in before]
            for x in fresh:
                x["lex"] = literature.lexical_score(x, keywords, doms)
            fresh = sorted(fresh, key=lambda x: -x["lex"])[:24]
            _rerank(run, fresh, problem, keywords, "Rank cited and citing papers")
            library[:] = [x for x in library if x.get("pinned") or x.get("read") or (x.get("score") or 0) >= 3]
            library.sort(key=lambda x: (-(x.get("score") or 0), -x.get("lex", 0)))
            _select(library, int(run.settings.get("candidates_keep", 24)))
            run.log(f"Citation trail added {len([x for x in fresh if (x.get('score') or 0) >= 5])} relevant papers.")
    p.save("library", library)
    st = p.load("search_status", {})
    st.update({"round2": status, "queries2": queries})
    p.save("search_status", st)
    run.changed()

    # 4c. read the most relevant papers one at a time
    top_k = int(run.settings.get("read_top_k", 8))
    ranked = [x for x in library if x.get("pinned") and x["state"] != "excluded"]
    ranked += [x for x in library if x["state"] == "selected" and not x.get("pinned") and (x.get("score") or 0) >= 6]
    reading = ranked[:max(top_k, sum(1 for x in ranked if x.get("pinned")))]
    system = prompts.READER.format(field=run.field, problem=problem, notation=fit(notation, 1200, "head"))
    room = min(150_000, _room("reader", system, 1500))
    todo = [pp for pp in reading if not (pp.get("read") and pp.get("notes"))]
    lock = threading.Lock()
    done = [len(reading) - len(todo)]
    if todo:
        run.progress(done[0], len(reading), f"reading {plural(len(todo), 'paper')}, "
                     f"{_workers(run, 'reader', False, len(todo))} at a time", more=1)

    def read(pp: dict) -> None:
        """One paper, read whole in one request. No paper's reading uses another's: the brief that brings them
        together is written once all of them are read."""
        run.check()
        try:
            text, how = literature.fetch_fulltext(pp)
        except Exception:  # noqa: BLE001
            text, how = pp.get("abstract") or "", "abstract only"
        if not text.strip():
            with lock:
                pp["read"], pp["fulltext"], pp["verdict"] = True, "no text available", "unknown"
                pp["notes"] = {"summary": "No abstract or full text could be retrieved.", "results": [], "notation": [],
                               "relation": "", "citable": []}
                p.save("library", library)
            return
        authors = ", ".join((a.get("given", "") + " " + a.get("family", "")).strip() for a in pp.get("authors", [])[:5])
        user = (f"PAPER [{pp['key']}]: {pp['title']}\nAuthors: {authors}\nYear: {pp.get('year')}\n"
                f"Text available: {how}\n\n{fit(text, room, 'head')}")
        res = run.ask("reader", system, user, max_out=1500, label=f"Read {pp['key']}", effort="low")
        verdict = extract_tag(res.text, "verdict", "related").lower()
        with lock:
            pp["verdict"] = next((v for v in ("irrelevant", "core", "related", "background") if v in verdict), "related")
            pp["notes"] = {
                "summary": extract_tag(res.text, "summary"), "results": parse_lines(extract_tag(res.text, "results")),
                "notation": parse_lines(extract_tag(res.text, "notation")), "relation": extract_tag(res.text, "relation"),
                "citable": parse_lines(extract_tag(res.text, "citable")),
            }
            pp["read"], pp["fulltext"] = True, how
            p.save("library", library)
            done[0] += 1
            run.log(f"Read [{pp['key']}] ({how}): {pp['verdict']}")
            run.progress(done[0], len(reading), f"read: {pp['title'][:60]}", more=1)
            run.changed()

    _side_by_side(run, "reader", False, todo, read)
    run.progress(len(reading), len(reading), "all papers read · now writing the research brief from them", more=1)

    # 4d. one brief that every later stage shares
    notes = []
    for pp in library:
        if pp.get("read") and pp.get("notes") and pp.get("verdict") != "irrelevant":
            nt = pp["notes"]
            notes.append(f"[{pp['key']}] {pp['title']} ({pp.get('year')}). Relevance: {pp['verdict']}.\n"
                         f"Summary: {nt['summary']}\nResults: {' | '.join(nt['results'][:5])}\n"
                         f"Notation: {' | '.join(nt['notation'][:6])}\nRelation: {nt['relation']}")
    system = prompts.BRIEF.format()
    user = (f"Declared field: {run.field}\n\nCONFIRMED TOPIC\n{problem}\n\nCONFIRMED NOTATION\n{notation}\n\n"
            f"READING NOTES\n" + fit("\n\n".join(notes) or "(no papers could be read)", _room("reason", system, 1300) - 3000, "head"))
    res = run.ask("reason", system, user, max_out=1300, label="Write context brief")
    p.save("brief", {"text": res.text.strip(), "model": res.model})
    run.progress(len(reading), len(reading), "research brief written")


# ------------------------------------------------------- 5. transcribe

# Words that only carry grammar. A word that can change a statement (not, and, or, if, every, some, only, ...) is
# never among them.
_STOP_WORDS = set("a an the is are was were be been being of to in on at by for that this these those there here we "
                  "it its has have had with as so which who whose our us one".split())
# Words that change what a sentence asserts. A doubt that touches one of them is put to the author.
_MEANING_WORDS = set("not no never none nothing nor neither either or and if iff unless only all every each any some "
                     "exist exists existing least most exactly unique uniquely less more greater smaller larger fewer "
                     "equal equals distinct different same finite infinite finitely infinitely even odd positive "
                     "negative zero nonzero non without except both many few several".split())
# Words that only link sentences. Reading one for another changes the prose, never the mathematics.
_LINK_WORDS = set("since as because hence thus so therefore now clearly obviously indeed note notice observe also "
                  "moreover further furthermore then well ok okay but however recall again first firstly next finally "
                  "similarly consequently whence thereby here yes".split())
_LANG_WORDS = re.compile(r"plural|singular|gramma|spelling|spelt|spelled|misspel|typo|capitali[sz]|punctuation|comma|full stop|"
                         r"apostrophe|hyphen|tense|\barticle\b|wording|word order|missing (the |a |an )?(small )?word|"
                         r"missing \W?(a|an|the|is|are|of|to)\W|verb|noun|agreement", re.I)
_MATH_WORDS = re.compile(r"symbol|index|indices|exponent|subscript|superscript|\bsign\b|formula|equation|inequalit|digit|number|"
                         r"bracket|parenthes|quantifier|operator|variable|letter", re.I)
_QUOTES = "\u201c\u201d\u2018\u2019\"'`*_"


def _math_signature(s: str) -> str:
    """What is left of a reading when the words are taken away: its formulas, digits and symbols."""
    s = s or ""
    inside = "".join(re.findall(r"\$([^$]*)\$", s))
    outside = re.sub(r"\$[^$]*\$", " ", s)
    return re.sub(r"\s+", "", inside + "".join(ch for ch in outside if ch.isdigit() or ch in "\\_^=<>+*/|(){}[]"))


def _bare(s: str) -> str:
    """A reading without what a model wraps around it: 'suggested reading:', quotation marks, bold, '...'."""
    s = (s or "").strip()
    s = re.sub(r"^(suggested|correct|better|alternative|possible|likely)?\s*(reading|version|text)?\s*(is)?\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\*\*|__", "", s)
    s = re.sub(r"\.\.\.|\u2026|\\ldots|\\dots", " \u2026 ", s)
    return s.strip(" " + _QUOTES)


def _tokens(s: str) -> list[tuple[str, str]]:
    """A reading as a list of (kind, value). Kinds: 'm' for mathematics (a formula, a single letter, a number, a symbol),
    'w' for an ordinary word (lower case, plural ending removed), 'gap' for an omission mark. Punctuation and the small
    words of grammar are left out."""
    out: list[tuple[str, str]] = []
    for formula, word, other in re.findall(r"\$([^$]*)\$|([A-Za-z]+)|([^\sA-Za-z$]+)", _bare(s)):
        if formula:
            out.append(("m", re.sub(r"\s+", "", formula)))
        elif word:
            if len(word) == 1:
                out.append(("m", word))                       # a single letter is a variable
            else:
                w = word.lower()
                if w in _STOP_WORDS:
                    continue
                out.append(("w", re.sub(r"(es|s)$", "", w) if len(w) > 3 and w not in _MEANING_WORDS and w not in _LINK_WORDS else w))
        else:
            if "\u2026" in other:
                out.append(("gap", ""))
            sym = re.sub(r"[.,;:!?\u2026" + re.escape(_QUOTES) + r"\-\u2010-\u2014]", "", other)
            if sym:
                out.append(("m", sym))
    return out


def _language_pair(x: tuple[str, str], y: tuple[str, str], said_language: bool) -> bool:
    """Two words read for one another without any change to the mathematics."""
    if x[0] != "w" or y[0] != "w" or x[1] in _MEANING_WORDS or y[1] in _MEANING_WORDS:
        return False
    if x[1] in _LINK_WORDS and y[1] in _LINK_WORDS:
        return True                                           # 'Hence' or 'Thus': the prose changes, not the statement
    return (said_language and len(x[1]) >= 3 and len(y[1]) >= 3
            and difflib.SequenceMatcher(None, x[1], y[1], autojunk=False).ratio() >= 0.7)


def language_only(snippet: str, alt: str, issue: str = "") -> bool:
    """True when a doubt about a reading concerns the language only: every formula, number, symbol, variable and
    every word that changes what is asserted is the same whichever way it is settled. Such doubts are never put to
    the author; the copy-edit corrects the language. When in doubt the answer is False, and the author is asked.

    The second reading may be a whole new version of the passage or only the part that changes ('terms' for
    'term, therefore,'); the part of the passage it leaves out is then kept as it is."""
    issue = issue or ""
    said_language = bool(_LANG_WORDS.search(issue)) and not _MATH_WORDS.search(issue) and not _math_signature(issue)
    alt_b = _bare(alt)
    if not alt_b or alt_b.lower() in ("none", "n/a", "-", "same", "as read"):
        return said_language and not _math_signature(snippet)   # no second reading given: only a doubt about plain words
    a = [t for t in _tokens(snippet) if t[0] != "gap"]
    b = _tokens(alt)
    partial = any(t[0] == "gap" for t in b)
    b = [t for t in b if t[0] != "gap"]
    if not b:
        return False
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    if not any(op == "equal" for op, *_ in ops) and not (len(ops) == 1 and ops[0][0] == "replace"):
        return False
    for k, (op, i1, i2, j1, j2) in enumerate(ops):
        if op == "equal":
            continue
        left, right = a[i1:i2], b[j1:j2]
        at_end = k == 0 or k == len(ops) - 1
        if op == "delete":
            # the second reading leaves this part out. At either end that only means it quotes less of the passage;
            # in the middle it is a deletion, allowed only for linking words.
            if at_end and (partial or (not any(t[0] == "w" and t[1] in _MEANING_WORDS for t in left)
                                       and (said_language or all(t[0] == "w" for t in left)))):
                continue
            if all(t[0] == "w" and t[1] in _LINK_WORDS for t in left):
                continue
            return False
        if op == "insert":
            if all(t[0] == "w" and t[1] in _LINK_WORDS for t in right):
                continue
            return False
        if len(left) != len(right) or not all(_language_pair(x, y, said_language) for x, y in zip(left, right)):
            return False
    return True


_QUOTE_CHARS = "“”‘’\"'`"
_SUGGEST = re.compile(r"^\s*(?:the\s+)?(?:suggested|possible|likely|correct|better|alternative|intended)?\s*(?:reading|version)\s*(?:is|would be)?\s*:\s*", re.I)
_REPLACE = re.compile(r"\breplace\s+(?P<every>(?:every|each|all)\s+(?:occurrences?\s+of\s+)?)?(?:the\s+)?(?:occurrences?\s+of\s+)?"
                      r"(?P<x>[“\"'‘`][^”\"'’`]{1,80}[”\"'’`]|\$[^$]{1,80}\$|\S{1,40})\s+(?:with|by)\s+"
                      r"(?P<y>[“\"'‘`][^”\"'’`]{1,80}[”\"'’`]|\$[^$]{1,80}\$|\S{1,40})", re.I)
_READS = re.compile(r"\breads?\s*(?:as\s*)?:?\s*(\$\$?[^$]{1,200}\$\$?|[“\"][^”\"]{1,200}[”\"])", re.I)
_DESCRIBES = re.compile(r"\b(appears?|likely|should|misread\w*|instead|throughout|inconsisten\w*|probably|suggests?|seems?|"
                        r"elsewhere|everywhere|the author|the notes|transcri\w*)\b", re.I)


def _unquote(s: str) -> str:
    s = (s or "").strip()
    for _ in range(2):
        s = re.sub(r"[.;,]\s*$", "", s.strip()) if s[-1:] in ".;," and s[-2:-1] in _QUOTE_CHARS else s.strip()
        if len(s) >= 2 and s[0] in _QUOTE_CHARS and s[-1] in _QUOTE_CHARS:
            s = s[1:-1].strip()
    return s


def _find(text: str, piece: str) -> list[str]:
    """Where a quoted piece occurs in a page, allowing for differences in spacing and for $ signs: the exact
    stretches of the page that match."""
    core = re.sub(r"\s+|\$", "", piece or "")
    if len(core) < 1:
        return []
    pat = r"\$?\s*".join(re.escape(ch) for ch in core)
    if re.match(r"[A-Za-z0-9]", core[0]):
        pat = r"(?<![A-Za-z0-9\\])" + pat
    if re.match(r"[A-Za-z0-9]", core[-1]):
        pat += r"(?![A-Za-z0-9])"
    return [m.group(0) for m in re.finditer(pat, text or "")]


def _swap(text: str, a: str, b: str) -> str:
    """text with every free-standing a replaced by b (a letter inside a word or a command is left alone)."""
    pat = re.escape(a)
    if re.match(r"[A-Za-z0-9]", a[:1]):
        pat = r"(?<![A-Za-z0-9\\])" + pat
    if re.match(r"[A-Za-z0-9]", a[-1:]):
        pat += r"(?![A-Za-z0-9])"
    return re.sub(pat, lambda _m: b, text)


def _closest(text: str, piece: str) -> str:
    """The stretch of a page closest to a passage a model quoted slightly wrongly ('\\psi(5)3-3' for '\\psi(5)33-'),
    taken from inside one formula or one line of words, so that replacing it can never break a formula. Empty when
    nothing is close enough."""
    core = re.sub(r"\s+|\$", "", piece or "")
    if len(core) < 3:
        return ""
    best, best_r = "", 0.0
    spans = [m for m in re.finditer(r"\$\$(.+?)\$\$|\$([^$\n]+?)\$", text or "", flags=re.S)]
    for m in spans:                                         # inside a formula: any stretch of similar length
        body = m.group(1) if m.group(1) is not None else m.group(2)
        for i in range(len(body)):
            for j in range(i + max(1, len(core) - 4), min(len(body), i + len(core) + 6) + 1):
                cand = body[i:j]
                if cand != cand.strip():
                    continue
                r = difflib.SequenceMatcher(None, core, re.sub(r"\s+", "", cand), autojunk=False).ratio()
                if r > best_r or (r == best_r and len(cand) > len(best)):
                    best, best_r = cand, r
    prose = re.sub(r"\$\$.+?\$\$|\$[^$\n]+?\$", "\n", text or "", flags=re.S)
    n = len(piece.split())
    for line in prose.split("\n"):                         # among words: a run of about as many words
        words = line.split()
        for k in (n - 1, n, n + 1):
            for i in range(0, max(0, len(words) - k) + 1):
                cand = " ".join(words[i:i + k])
                if not cand or cand not in line:
                    continue
                r = difflib.SequenceMatcher(None, core, re.sub(r"\s+", "", cand), autojunk=False).ratio()
                if r > best_r or (r == best_r and len(cand) > len(best)):
                    best, best_r = cand, r
    return best if best_r >= 0.8 and len(_find(text, best)) == 1 else ""


_SCOPE = re.compile(r"\b(throughout|everywhere|elsewhere|likewise|similarly|consistently|as well|(?:every|each|all|other)\s+"
                    r"(?:occurrences?|places?|instances?|times?)|on (?:this|the|that|each|every|all|other) pages?|"
                    r"(?:in|on) the (?:rest|whole)|same (?:symbol|reading|letter|change)|as on|as (?:above|below|before))\b", re.I)


def _remark_off(alt: str, snippet: str) -> tuple[str, str]:
    """A suggested reading replaces the passage word for word, so a remark a model put into it ('S\\mathcal{V}_2 (and
    \\mathcal{V}_2 throughout the page)') would be written into the notes. The remark is taken out: a part in
    brackets, or after a comma, a semicolon or a dash, that speaks of where else the reading applies, and that the
    passage itself does not say. Returns the reading and the remark ('' when there is none)."""
    if not alt:
        return alt, ""
    pats = [r"\s*\(([^()]*)\)", r"\s*\[([^\[\]]*)\]", r"\s*(?:[,;]|\s[—–-])\s*((?:and|also|i\.e\.|here and)\b.*)$",
            r"\s*(?:[,;]|\s[—–-])\s*([^,;]*)$"]
    for pat in pats:
        for m in reversed(list(re.finditer(pat, alt))):
            inner = m.group(1)
            if _SCOPE.search(inner) and not _SCOPE.search(snippet or ""):
                rest = (alt[:m.start()] + alt[m.end():]).strip()
                if rest:
                    return rest, inner.strip()
    return alt, ""


def _wider(snippet: str, alt: str, remark: str) -> tuple[str, str] | None:
    """The remark names a smaller piece to change everywhere ('and $\\mathcal{V}_2$ throughout'): the change of the
    passage made to that piece. Returns (piece as read, piece as it should read), or None."""
    if not snippet or not alt or snippet == alt:
        return None
    i = 0
    while i < min(len(snippet), len(alt)) and snippet[i] == alt[i]:
        i += 1
    j = 0
    while j < min(len(snippet), len(alt)) - i and snippet[-1 - j] == alt[-1 - j]:
        j += 1
    a, b = snippet[i:len(snippet) - j], alt[i:len(alt) - j]
    if not b:
        return None
    for m in re.finditer(r"\$([^$]+)\$|(\\[A-Za-z]+(?:\{[^{}]*\})?(?:[_^](?:\{[^{}]*\}|\w))*)", remark):
        y = (m.group(1) or m.group(2) or "").strip()
        if y and y != alt and y.count(b) == 1:
            x = y.replace(b, a)
            if x != y and x in snippet:
                return x, y
    return None


def tidy_doubt(item: dict, pages: dict[int, str], page: int = 0) -> dict | None:
    """A doubt about a reading as a model wrote it, put into the shape the author is shown: the exact passage of the
    page it is about (or nothing), one plain explanation, and the suggested reading. Models sometimes write the
    explanation where the quotation belongs, put the suggestion in the explanation, quote with ellipses, give the
    wrong page or describe the change ('replace S with s'). Returns None when the doubt has been settled meanwhile
    (the author already changed the passage to the suggested reading)."""
    raw_snippet = (item.get("snippet") or "").strip()
    snippet = _unquote(raw_snippet)
    issue = (item.get("issue") or "").strip()
    alt = (item.get("alt") if "alt" in item else item.get("suggestion")) or ""
    alt = alt.strip()
    if not alt and _SUGGEST.match(issue):
        alt, issue = issue, ""
    alt = _unquote(_SUGGEST.sub("", alt))
    alt = re.sub(r"\*\*|__", "", alt)
    where = lambda piece: {n: _find(t, piece) for n, t in pages.items() if _find(t, piece)}
    if snippet and not where(snippet) and (_DESCRIBES.search(snippet) or "…" in snippet or "..." in snippet):
        if _DESCRIBES.search(snippet):
            issue = (raw_snippet + (" " + issue if issue else "")).strip()
        snippet = ""
    every = False
    alt, remark = _remark_off(alt, snippet)
    if remark:                       # '... (and $\mathcal{V}_2$ throughout the page)': a remark, never put into the page
        every = True
        wider = _wider(snippet, alt, remark)
        if wider and where(wider[0]):
            snippet, alt = wider
    m = _REPLACE.search(alt)
    if m:
        x, y = _unquote(m.group("x")).strip("$ "), _unquote(m.group("y")).strip("$ ")
        every = bool(m.group("every"))
        ctx = _READS.search(alt)
        snippet, alt = x, y
        if page in pages and not where(x) and _find(pages[page], y):
            return None                                     # the notes already have the suggested reading
        if ctx:                                             # '... (so that it reads $f(n)\le a$)': the passage itself
            after = _unquote(ctx.group(1)).strip("$ ")
            before = _swap(after, y, x)
            if before != after and where(before):
                snippet, alt, every = before, after, False
        if not issue:
            issue = "This looks misread."
    found = where(snippet) if snippet else {}
    if snippet and not found:
        if alt and page in pages and _find(pages[page], alt):
            return None                                     # already changed to the suggested reading
        near = _closest(pages.get(page, ""), snippet) if page in pages else ""
        if not near:
            return None                                     # the passage is not on the page (any more): nothing to decide
        snippet, found = near, {page: [near]}
    elsewhere = sorted(found) if page not in pages else []  # no page given: every page where the passage occurs
    if found and page not in found:
        page = min(found)                                   # the model gave the wrong page
    if page not in pages:
        page = min(pages) if pages else 0
    hits = found.get(page, [])
    if len(set(hits)) == 1:
        snippet = hits[0]                                   # the exact stretch of the page, so that it can be replaced
    return {"page": page, "snippet": snippet, "issue": issue, "alt": alt if alt.lower() not in ("none", "n/a", "-") else "",
            "count": len(hits) if len(set(hits)) <= 1 else 0, "every": every, "also": [n for n in elsewhere if n != page]}


def dismiss_doubt(p: Project, page: int, snippet: str, alt: str, how: str = "kept", value: str = "",
                  issue: str = "") -> None:
    """The author answered a doubt (kept as read, the suggested reading, or their own). Remembered, so the doubt is
    not raised again, and listed among the settled items with the answer given."""
    with p._lock:
        gone = p.load("dismissed", [])
        key = [page, snippet, alt]
        if key not in gone:
            gone.append(key)
            p.save("dismissed", gone)
        answers = [a for a in p.load("doubt_answers", []) if [a.get("page"), a.get("snippet"), a.get("alt")] != key]
        answers.append({"page": page, "snippet": snippet, "alt": alt, "issue": issue[:400],
                        "how": how if how in ("kept", "suggested", "own") else "kept", "value": value[:600],
                        "at": time.time()})
        p.save("doubt_answers", answers)
    if how in ("suggested", "own"):                  # a reading the author corrected: the next papers' readers are told
        from . import memory
        memory.learn_reading(p.state.get("meta", {}), snippet, alt if how == "suggested" else value)


def page_notes(p: Project) -> dict:
    """The author's comment on each page of the notes, and the pages they confirmed as read. A comment is the
    author's own word about a handwritten page and is kept when the pages are read again; a confirmation is of one
    reading, and goes when the transcription is done again."""
    notes = (p.load("page_notes") or {}).get("pages", {})
    checks = (p.load("page_checks") or {}).get("pages", {})
    return {"comments": {n: v for n, v in notes.items() if (v or {}).get("text")}, "confirmed": checks}


def set_page_note(p: Project, n: int, comment: str | None = None, confirmed: bool | None = None) -> dict:
    """Save the author's comment on page n (an empty comment removes it), or confirm the page as read (or undo it)."""
    with p._lock:
        if comment is not None:
            data = p.load("page_notes", {"pages": {}})
            text = str(comment).strip()[:4000]
            if text:
                data["pages"][str(n)] = {"text": text, "at": time.time()}
            else:
                data["pages"].pop(str(n), None)
            p.save("page_notes", data)
        if confirmed is not None:
            data = p.load("page_checks", {"pages": {}})
            if confirmed:
                data["pages"][str(n)] = time.time()
            else:
                data["pages"].pop(str(n), None)
            p.save("page_checks", data)
    return page_notes(p)


def pending_comments(p: Project) -> list[int]:
    """Pages with a comment of the author's that has not been carried out yet. The transcript is not approved while
    there is one."""
    return sorted(int(n) for n in page_notes(p)["comments"])


def correct_page(p: Project, n: int, comment: str | None = None) -> dict:
    """The author's comment on page n is carried out: the page is read again with the comment, and only what the
    comment asks for is changed. The comment is then settled and kept with the page, with what was done and the page
    as it was before, so that the correction can be undone."""
    tr = p.load("transcript", {"pages": {}})
    page = tr["pages"].get(str(n))
    if not page:
        raise ValueError("This page has not been read yet.")
    note = (comment if comment is not None else (page_notes(p)["comments"].get(str(n)) or {}).get("text", "")).strip()[:4000]
    if not note:
        raise ValueError("Write your comment on the page first.")
    before = page["text"]
    hooks = Run(p)
    hooks.stage = "review"
    user = (f"PAGE {n}\n\nTHE AUTHOR'S COMMENT ON THIS PAGE\n{note}\n\n"
            f"CONFIRMED NOTATION\n{notation_text(p.load('confirmed', {}), 1200)}\n\n"
            f"TRANSCRIPTION OF THIS PAGE\n{before}")
    res = router.chat("vision_best", prompts.CORRECT_PAGE, user, images=[_page_bytes(p, n)], max_out=4000, effort="medium",
                      label=f"Correct page {n} from the author's comment", hooks=hooks)
    text = clean_transcript(answer_transcript(res.text))
    if not text.strip() or looping(text):
        raise ValueError("The page could not be corrected this time. Try again, or edit its LaTeX by hand.")
    if len(find_figures(text)) != len(find_figures(before)):
        raise ValueError("Carrying out the comment would have added or removed a drawing, so nothing was changed. "
                         "Use the choices for the drawing instead, or say more precisely what is wrong.")
    changes = [c for c in parse_lines(extract_tag(res.text, "changes")) if c.strip().lower() not in ("none", "-")]
    reply = re.sub(r"\s+", " ", extract_tag(res.text, "reply") or "").strip()[:600]
    if text.strip() == before.strip():
        changes = []
    done = {"comment": note, "changes": changes[:30], "reply": reply or ("Corrected." if changes else "Nothing was changed."),
            "before": before, "at": time.time()}
    with p._lock:
        tr = p.load("transcript", {"pages": {}})
        cur = tr["pages"].get(str(n))
        if not cur or cur["text"] != before:
            raise ValueError("The page changed meanwhile. Try again.")
        if changes:
            cur["text"], cur["edited"] = text, True
            cur["uncertain"] = [u for u in cur.get("uncertain", []) if not u.get("snippet") or u["snippet"] in text]
            cur["uncertain"] += [d for d in command_doubts(p, text)
                                 if not any(d["snippet"] == u.get("snippet") for u in cur["uncertain"])]
        cur.setdefault("corrections", []).append(done)
        p.save("transcript", tr)
        notes = p.load("page_notes", {"pages": {}})       # the comment is settled (the one sent is what the author wrote)
        notes["pages"].pop(str(n), None)
        p.save("page_notes", notes)
        if changes:                                   # what was confirmed is not what the page says now
            checks = p.load("page_checks", {"pages": {}})
            checks["pages"].pop(str(n), None)
            p.save("page_checks", checks)
    return {"page": n, "text": cur["text"], "correction": {k: v for k, v in done.items() if k != "before"},
            "changed": bool(changes), "notes": page_notes(p)}


def undo_correction(p: Project, n: int) -> dict:
    """The last correction made from the author's comment is taken back; the comment comes back to be changed."""
    with p._lock:
        tr = p.load("transcript", {"pages": {}})
        cur = tr["pages"].get(str(n))
        if not cur or not cur.get("corrections"):
            raise ValueError("There is no correction on this page to undo.")
        last = cur["corrections"].pop()
        cur["text"] = last["before"]
        p.save("transcript", tr)
        notes = p.load("page_notes", {"pages": {}})
        notes["pages"][str(n)] = {"text": last["comment"], "at": time.time()}
        p.save("page_notes", notes)
    return {"page": n, "text": cur["text"], "notes": page_notes(p)}


def hosted_blocked(p: Project) -> bool:
    """No model can be asked for this paper now (no key chosen that works): nothing is started."""
    try:
        if not router.discovered_at:
            router.discover()
        return not router.models
    except Exception:  # noqa: BLE001
        return True


def page_layouts(p: Project, only: int | None = None) -> dict:
    """For each page read, where each line of its transcript stands on the page (layout.py). Typed pages have no
    handwriting to measure. The lines of the handwriting are measured once per page and kept beside its image."""
    from . import layout
    tr = (p.load("transcript") or {}).get("pages", {})
    typed_src = (p.load("typed") or {}).get("pages", {})
    boxes: dict = {}
    for f in (p.load("figures") or {}).get("items", []):
        if f.get("box"):
            boxes.setdefault(f["page"], {})[f["k"]] = f["box"]
    out = {}
    for n, rec in tr.items():
        if only is not None and int(n) != only:
            continue
        img = p.dir / "pages" / f"p{int(n):03d}.jpg"
        if str(n) in typed_src or not img.exists():
            out[n] = {"ok": False, "items": [], "text": rec.get("text", "")}
            continue
        text = rec.get("text", "")
        try:
            lines = layout.page_lines(img, p.dir / "pages" / f"layout_p{int(n):03d}.json")
            res = layout.layout(text, lines, boxes.get(int(n)))
            # a page where the matching by length is not sure: where a model that sees the page puts its lines
            if lines and (res.get("doubt") or not res.get("ok")) and placing.enabled(p):
                got = placing.cached(p, int(n), text)
                if got and got.get("assign"):
                    ai = layout.layout(text, lines, boxes.get(int(n)), assign=got["assign"])
                    if ai.get("ok"):
                        res = ai
                elif not got and not hosted_blocked(p):
                    res["placing"] = placing.start(p, int(n), text, lines, boxes.get(int(n)))
        except Exception:  # noqa: BLE001 - a page that cannot be measured is shown as plain text
            traceback.print_exc()
            res = {"ok": False, "items": []}
        try:
            from PIL import Image
            with Image.open(img) as im:                  # the shape of the page, so the text is laid out on the same shape
                aspect = round(im.height / max(1, im.width), 4)
        except Exception:  # noqa: BLE001
            aspect = 1.414
        out[n] = {**res, "text": rec.get("text", ""), "aspect": aspect}
    return out


def adding_now(p: Project) -> dict | None:
    """While pages are added after the transcript was first checked: which pages are still being read, and whether
    figures are still being drawn. None when nothing is being added."""
    st = p.state
    stages = st["stages"]
    review = stages.get("review", {})
    if p.load("flags") is None or review.get("status") == "done" or not review.get("since"):
        return None                            # the review was never opened: the steps before it are shown in turn
    tr = (p.load("transcript") or {}).get("pages", {})
    pages = [pg["n"] for pg in st.get("pages", []) if not pg.get("skip") and str(pg["n"]) not in tr]
    drawing = stages.get("figures", {}).get("status") in ("pending", "running")
    if not pages and not drawing:
        return None
    return {"pages": pages, "figures": drawing, "stage": next((sid for sid in ("transcribe", "figures")
                                                               if stages.get(sid, {}).get("status") == "running"), "")}


def visible_doubts(p: Project) -> tuple[dict | None, list | None, int]:
    """The transcript and the possible misreadings as the author sees them. Every doubt is put into a clean shape
    (tidy_doubt), and every doubt about language only is taken out, including doubts saved by an earlier version
    of the app, before these rules existed. Nothing is changed on disk."""
    tr, flags = p.load("transcript"), p.load("flags")
    pages = {int(n): v.get("text", "") for n, v in ((tr or {}).get("pages") or {}).items()}
    core = lambda x: re.sub(r"\s+|\$", "", x or "")
    gone = {(k[0], core(k[1]), core(k[2])) for k in (p.load("dismissed") or []) if isinstance(k, list) and len(k) == 3}
    answered = lambda t: (t["page"], core(t["snippet"]), core(t["alt"])) in gone
    hidden = 0

    def keep(item: dict, page: int) -> dict | None:
        nonlocal hidden
        t = tidy_doubt(item, pages, page)
        if t is None or answered(t):
            return None                                      # settled meanwhile, or the author said it is correct
        if language_only(t["snippet"], t["alt"], t["issue"]) \
                or language_only(item.get("snippet", ""), item.get("alt", item.get("suggestion", "")), item.get("issue", "")):
            hidden += 1
            return None
        return t

    if tr and isinstance(tr.get("pages"), dict):
        shown = {n: {**v, "uncertain": []} for n, v in tr["pages"].items()}
        for n, v in tr["pages"].items():
            for u in (v.get("uncertain") or []):
                t = keep(u, int(n))
                if t:
                    t.pop("also", None)                                        # a doubt that belongs to another page goes there
                    shown[str(t["page"]) if str(t["page"]) in shown else n]["uncertain"].append(t)
        tr = {**tr, "pages": shown}
    if isinstance(flags, list):
        out = []
        for f in flags:
            try:
                page = int(f.get("page") or 0)
            except (TypeError, ValueError):
                page = 0
            t = keep(f, page)
            if t:
                also = t.pop("also", [])
                out.append({**t, "suggestion": t["alt"]})
                for n in also:                   # no page was given and the passage occurs on several
                    again = tidy_doubt(f, pages, n)
                    if again and not answered(again):
                        again.pop("also", None)
                        out.append({**again, "suggestion": again["alt"]})
        flags = out
    # the same doubt raised twice (by the reading and by the check) is shown once
    shown_unc = [u for v in ((tr or {}).get("pages") or {}).values() for u in v["uncertain"]]
    seen = {(u["page"], u["snippet"], u["alt"]): u for u in shown_unc}
    if isinstance(flags, list):
        kept = []
        for f in flags:
            key = (f["page"], f["snippet"], f["alt"])
            if f["snippet"] and key in seen:
                u = seen[key]                                # keep the fuller explanation and the 'every' of either
                u["every"] = u.get("every") or f["every"]
                if len(f["issue"]) > len(u["issue"]):
                    u["issue"] = f["issue"]
                continue
            seen[key] = f
            kept.append(f)
        flags = kept
    return tr, flags, hidden


_NUM = re.compile(r"(?<![\w.])\d[\d.\-/]*\d(?![\w])")


def _numbers(s: str) -> list[str]:
    """The numbers written in the words of a page (years, dates, arXiv numbers, theorem numbers), with at least three
    digits: formulas are left out, their indices and exponents are too short and too many to compare."""
    s = re.sub(r"\$\$.*?\$\$|\$[^$]*\$|\\\[.*?\\\]|\\\(.*?\\\)", " ", s or "", flags=re.S)
    return [m for m in _NUM.findall(s) if sum(c.isdigit() for c in m) >= 3]


def _near(a: str, b: str) -> bool:
    """Two numbers of the same shape that differ in one or two digits: one of them is a misreading of the other."""
    return a != b and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 2 \
        and all(x.isdigit() == y.isdigit() for x, y in zip(a, b))


def number_doubts(text: str, others: dict[str, str], changes: list[str]) -> list[dict]:
    """Numbers on which the readings of one page disagree, found without a model. The final reading has the context and
    the best model, but a model also knows which years and identifiers are usual and may 'correct' a digit towards
    them; an earlier reading that saw another digit, or a change the final reading made to a number, is therefore
    put to the author as a doubt instead of being trusted either way."""
    out, seen = [], set()
    mine = _numbers(text)
    for name, other in others.items():
        theirs = set(_numbers(other))
        for a in mine:
            if a in theirs:
                continue
            for b in sorted(theirs):
                if _near(a, b) and b not in mine and (a, b) not in seen:
                    seen.add((a, b))
                    out.append({"snippet": a, "alt": b, "check": "numbers",
                                "issue": f"The {name} read {b} here. Compare the digits with the page."})
    for c in changes:
        nums = _numbers(c)
        for a in mine:
            for b in nums:
                if _near(a, b) and (a, b) not in seen and (b, a) not in seen:
                    seen.add((a, b))
                    out.append({"snippet": a, "alt": b, "check": "numbers",
                                "issue": f"The final reading changed {b} to {a}. Compare the digits with the page."})
    return out[:6]


def command_doubts(p: Project, text: str) -> list[dict]:
    """Commands in a page's transcription that do not exist (a model invented them, such as \\curvearrowtop): the
    preview would show them as raw LaTeX and the paper would print them as words. Found without a model; the
    author says what symbol is written there. Invented names of standard symbols were already replaced."""
    extra = set(paperlib.MACROS) | set((p.load("typed") or {}).get("katex", {}) or {})
    return [{"snippet": "\\" + name, "alt": "", "check": "latex",
             "issue": f"\\{name} is not a LaTeX command, so it would show as raw LaTeX here and be printed as the "
                      f"word “{name}” in the paper. Say in a comment which symbol or layout is written here."}
            for name in unknown_commands(text, extra)][:6]


def _next_draft(pages: list[dict], i: int, survey: dict, typed_src: dict) -> str:
    """The first lines of the page after page i (1-based), as the notation survey drafted it: a sentence or formula
    that runs over the page break is then read with its continuation in view. Pages are still transcribed in order,
    each with the end of the one before as transcribed."""
    if i >= len(pages):
        return "(this is the last page)"
    n = str(pages[i]["n"])
    text = typed_src.get(n) or (survey.get(n) or {}).get("text", "")
    text = re.sub(r"\[(?:FIGURE|MARGIN)[^\]]*\]", " ", text or "").strip()
    return fit(text, 350, "head") if text else "(not available)"


def stage_transcribe(run: Run) -> None:
    p = run.p
    pages = _content_pages(p)
    survey = p.load("survey", {})
    conf = p.load("confirmed", {})
    brief = (p.load("brief") or {}).get("text", "")
    tr: dict = p.load("transcript", {"pages": {}})
    system = prompts.FINAL.format(field=run.field)
    room = _room("vision_best", system, 2800, vision=True)
    total = len(pages)
    prev_tail = ""
    settled = 0
    old_flags = p.load("flags")                # present when pages are added after the transcript was checked
    fresh: list[int] = []
    typed_src = (p.load("typed") or {}).get("pages", {})
    layers = exact_layers(p)
    scout = p.load("scout") or {}
    from . import memory
    hand = "" if p.state.get("meta", {}).get("test") else memory.quirks()
    for i, pg in enumerate(pages, 1):
        n = pg["n"]
        if str(n) in tr["pages"]:
            prev_tail = tr["pages"][str(n)]["text"][-350:]
            continue
        run.check()
        if str(n) in typed_src:                # typed notes are their own transcript: nothing to misread
            with p._lock:
                tr = p.load("transcript", {"pages": {}})
                tr["pages"][str(n)] = {"text": typed_src[str(n)], "uncertain": [], "changes": [], "model": "typed source", "edited": False}
                p.save("transcript", tr)
            prev_tail = typed_src[str(n)][-350:]
            fresh.append(n)
            run.progress(i, total, f"page {n} taken from the typed source", more=1)
            continue
        draft = (survey.get(str(n)) or {}).get("text", "")
        share = max(600, (room - len(draft) - 400) // 2) if room > len(draft) + 1600 else 700
        user = (f"This is page {n} of {len(p.state['pages'])}.\n\nCONFIRMED NOTATION\n{notation_text(conf, share)}\n\n"
                f"RESEARCH CONTEXT\n{fit(brief, share, 'head') or '(none)'}\n\n"
                f"END OF THE PREVIOUS PAGE\n{prev_tail or '(this is the first page)'}\n\n"
                f"START OF THE NEXT PAGE (from its draft; it may contain misread symbols)\n{_next_draft(pages, i, survey, typed_src)}\n\n"
                f"DRAFT TRANSCRIPTION OF THIS PAGE (may contain misread symbols)\n{fit(draft, max(1500, room - 2 * share - 400), 'head') or '(no draft)'}")
        if hand:
            user += "\n\n" + hand
        if layers.get(str(n)):
            user += ("\n\nTEXT LAYER OF THIS PAGE (copied from the PDF file itself: its words are exact, its formulas are not, "
                     "because superscripts, fractions and symbols are flattened in it; read the formulas from the image)\n"
                     + fit(layers[str(n)], 2500, "head"))
        res = run.ask("vision_best", system, user, images=[_page_bytes(p, n)], max_out=3200, effort="high",
                      label=f"Transcribe page {n}")
        text = clean_transcript(answer_transcript(res.text))
        broke = looping(text)
        if broke:                              # the model repeated itself endlessly: the page is read once more
            again = run.ask("vision_best", system, user + "\n\nIMPORTANT: a previous reading of this page broke down and repeated "
                            "one command endlessly. Transcribe the page once, from the top, and repeat nothing.",
                            images=[_page_bytes(p, n)], max_out=3200, effort="high", label=f"Transcribe page {n} (again)")
            text2 = clean_transcript(answer_transcript(again.text))
            broke = looping(text2)
            res, text = (again, text2) if not broke else (res, collapse_loops(text))
        uncertain = []
        if broke:
            uncertain.append({"snippet": "", "issue": "The reading of this page broke down twice (the model repeated itself), and the "
                              "repetition was cut. Compare the page with the image and correct it by hand.", "alt": ""})
        for row in parse_rows(extract_tag(res.text, "uncertain"), min_cols=2):
            item = {"snippet": row[0], "issue": row[1], "alt": row[2] if len(row) > 2 else ""}
            if language_only(item["snippet"], item["alt"], item["issue"]):
                settled += 1                   # a matter of wording: settled here, never shown to the author
                continue
            uncertain.append(item)
        changes = [c for c in parse_lines(extract_tag(res.text, "changes")) if c.strip().lower() not in ("none", "-")]
        sc = scout.get(str(n)) or {}
        others = {"first look at the page": (sc.get("prose") or "") + "\n" + (sc.get("printed") or ""),
                  "draft reading": draft}
        if layers.get(str(n)):
            others["text layer of the PDF"] = layers[str(n)]
        for d in number_doubts(text, others, changes) + command_doubts(p, text):
            if not any(d["snippet"] in u.get("snippet", "") for u in uncertain):
                uncertain.append(d)
        rec = {"text": text, "uncertain": uncertain, "changes": changes, "model": res.model, "edited": False}
        with p._lock:                          # the author may be editing earlier pages meanwhile: merge, never overwrite
            tr = p.load("transcript", {"pages": {}})
            tr["pages"][str(n)] = rec
            p.save("transcript", tr)
        prev_tail = text[-350:]
        fresh.append(n)
        run.progress(i, total, f"page {n} transcribed", more=1)    # the check for misreadings comes after the pages
    if old_flags is not None and not fresh:
        run.progress(total, total, "transcript ready")
        return                                 # nothing new since the check

    # 5b. consistency check: flags possible misreadings, never edits
    run.progress(total, total, "all pages transcribed · now checking them for misreadings", more=1)
    system = prompts.CHECK.format(notation=notation_text(conf, 1200))
    room = _room("reason", system, 1400)
    flags: list[dict] = []
    chunk: list[str] = []
    size = 0
    to_check = set(fresh) if old_flags is not None else None    # adding a page: check that page, keep the rest

    def flush() -> None:
        nonlocal chunk, size, settled
        if not chunk:
            return
        res = run.ask("reason", system, "\n\n".join(chunk), max_out=1400, label="Check transcript consistency")
        for row in parse_rows(extract_tag(res.text, "flags"), min_cols=3):
            pgm = re.search(r"\d+", row[0])
            item = {"page": int(pgm.group(0)) if pgm else 0, "snippet": row[1], "issue": row[2],
                    "suggestion": row[3] if len(row) > 3 else ""}
            if language_only(item["snippet"], item["suggestion"], item["issue"]):
                settled += 1
                continue
            flags.append(item)
        chunk, size = [], 0

    for pg in pages:
        t = tr["pages"].get(str(pg["n"]), {}).get("text", "")
        if not t or (to_check is not None and pg["n"] not in to_check) or str(pg["n"]) in typed_src:
            continue                           # a typed page is its own source: nothing on it can be misread
        piece = f"=== page {pg['n']} ===\n{t}"
        if size + len(piece) > room and chunk:
            flush()
        chunk.append(piece)
        size += len(piece)
    flush()
    if to_check is not None:
        only = next(iter(to_check)) if len(to_check) == 1 else 0
        for f in flags:
            if not f["page"] and only:
                f["page"] = only                    # only one page was shown: an unnumbered flag is about it
        known = {(f.get("page"), f.get("snippet")) for f in old_flags}
        flags = old_flags + [f for f in flags if f["page"] in to_check and (f["page"], f["snippet"]) not in known]
    p.save("flags", flags)
    run.progress(total, total, "transcript checked")
    tr = p.load("transcript", {"pages": {}})
    n_unc = sum(len(v["uncertain"]) for v in tr["pages"].values())
    run.log(f"Transcription complete: {n_unc} uncertain readings and {len(flags)} possible misreadings for your review."
            + (f" {settled} doubts about wording only were settled without asking you." if settled else ""))


# ---------------------------------------------------------- 6. figures

_FIG_OPEN = re.compile(r"\[FIGURE(?:\s+(f\d+))?\s*:")
_EXPL_OPEN = re.compile(r"\[EXPLANATORY DRAWING\s*:")      # a drawing that only explains the argument: not printed
_TIKZ_FORBIDDEN = re.compile(r"\\(?:input|include|write|openin|openout|read|catcode|immediate|usepackage|csname|def|let)\b")


def _markers(text: str, opener: re.Pattern) -> list[tuple[int, int, str, str]]:
    """Markers such as [FIGURE: ...] as (start, end, description, id). Brackets inside the description are matched."""
    out = []
    text = text or ""
    pos = 0
    while True:
        m = opener.search(text, pos)
        if not m:
            return out
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"[": 1, "]": -1}.get(text[j], 0)
            j += 1
        if depth:                                   # an unmatched bracket inside: end at the first closing one
            k = text.find("]", m.end())
            j = k + 1 if k >= 0 else len(text)
        out.append((m.start(), j, text[m.end():j - 1].strip(), (m.groups() or [""])[0] or ""))
        pos = j


def find_figures(text: str) -> list[tuple[int, int, str, str]]:
    """[FIGURE: ...] markers as (start, end, description, id)."""
    return _markers(text, _FIG_OPEN)


def find_explanations(text: str) -> list[tuple[int, int, str, str]]:
    """[EXPLANATORY DRAWING: ...] markers, which the writers see and never print."""
    return _markers(text, _EXPL_OPEN)


def strip_explanations(text: str) -> str:
    """The text without its explanatory drawings; a marker that stood on a line of its own takes the line with it."""
    for a, b, _, _ in reversed(find_explanations(text)):
        s, e = text.rfind("\n", 0, a) + 1, text.find("\n", b)
        e = len(text) if e < 0 else e
        if not text[s:a].strip() and not text[b:e].strip():
            a, b = s, min(len(text), e + 1)
        text = text[:a] + text[b:]
    return text


def strip_figures(text: str) -> str:
    """The text without its drawings: neither figures nor explanatory drawings are text of the paper."""
    for a, b, _, _ in reversed(find_figures(text)):
        text = text[:a] + text[b:]
    return strip_explanations(text)


def audit_pages(pages: list[dict]) -> list[dict]:
    """A figure's description is not text of the paper, so its formulas are not expected in the body."""
    return [{"n": pg["n"], "text": strip_figures(pg["text"])} for pg in pages]


_PAGE_MARK = re.compile(r"(?m)^[ \t]*%+\s*page\s+(\d+)\s*$")


def page_marks(text: str) -> set[int]:
    """The pages whose "%%% page N" line the writer copied into its text."""
    return {int(n) for n in _PAGE_MARK.findall(text or "")}


def page_gaps(apages: list[dict], report: dict, marked: set[int] | None = None) -> list[int]:
    """Pages whose material did not come out in the written text, judged by the comparison report. A page is a gap
    when more than half of its formulas are missing; or when the writer, who copies the "%%% page N" lines, left
    out this page's line and more than half of its formulas are not found as they are (an argument repeated from
    an earlier page then only looks "changed": it is the earlier page's copy that was found). A formula that is
    changed on a page that is there is not a gap: it is shown to the author. Nor is one the author left out."""
    count, gone, off = {}, {}, {}
    for pg in apages:
        count[pg["n"]] = len(fidelity.formulas(pg["text"]))
    for x in report.get("agreed", []):
        count[x["page"]] = count.get(x["page"], 0) - 1
    for x in report.get("missing", []):
        gone[x["page"]] = gone.get(x["page"], 0) + 1
    for x in report.get("altered", []):
        off[x["page"]] = off.get(x["page"], 0) + 1
    out = []
    for pg in apages:
        n = pg["n"]
        c, m, a = count.get(n, 0), gone.get(n, 0), off.get(n, 0)
        if (c >= 2 and 2 * m > c) or (c >= 1 and marked and n not in marked and 2 * (m + a) > c):
            out.append(n)
    return out


def _crop_page(p: Project, n: int, box: list[int] | None) -> bytes:
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(_page_bytes(p, n))).convert("RGB")
    if box:
        w, h = img.size
        pad = 25
        y0, x0, y1, x1 = box
        img = img.crop((int(max(0, x0 - pad) * w / 1000), int(max(0, y0 - pad) * h / 1000),
                        int(min(1000, x1 + pad) * w / 1000), int(min(1000, y1 + pad) * h / 1000)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _tikz_only(text: str) -> str:
    m = re.search(r"\\begin\{tikzpicture\}.*\\end\{tikzpicture\}", text or "", flags=re.S)
    if not m or _TIKZ_FORBIDDEN.search(m.group(0)):
        return ""
    return m.group(0).strip()


def figure_file(p: Project, fid: str, kind: str):
    name = {"tikz": f"{fid}_tikz.png", "check": f"{fid}_check.png"}.get(kind, f"{fid}_orig.jpg")
    return p.dir / "figures" / name


def _page_record(p: Project, n: int) -> dict:
    return next((pg for pg in p.state.get("pages", []) if pg.get("n") == n), {"n": n, "file": f"p{n:03d}.jpg"})


def _draw_figure(p: Project, rec: dict, ask, conf: dict, instruction: str = "") -> None:
    """Draw a figure again (figflow.py): found on the page, measured from the original file, its labels read by the
    model, redrawn by code and checked twice. A failure is not covered up: the figure keeps the error, which the author
    sees next to their own drawing, and the other figures are still drawn."""
    from . import figflow
    try:
        figflow.draw(p, rec, ask, notation_text(conf, 1200), _page_record(p, rec["page"]), _page_bytes(p, rec["page"]),
                     instruction)
        return
    except (LLMError, Cancelled):
        raise
    except Exception as e:  # noqa: BLE001 - shown to the author on the figure; the other figures are still drawn
        traceback.print_exc()
        why = str(e) if isinstance(e, figflow.FigureError) else f"the redrawing failed ({type(e).__name__}: {e})"
    (p.dir / "figures").mkdir(exist_ok=True)
    orig = figure_file(p, rec["id"], "orig")
    if not orig.exists():                               # the author's own drawing is shown, cut from the page
        orig.write_bytes(_crop_page(p, rec["page"], rec.get("box")))
    target = figure_file(p, rec["id"], "tikz")
    if target.exists():
        target.unlink()
    rec.update(tikz="", tikz_ok=False, error=why, drawn=time.time(), choice="", method="measured",
               check={"same": None, "problems": []})
    if not rec.get("caption"):
        rec["caption"] = re.sub(r"\s+", " ", rec.get("desc", "")).strip()


def transcript_figures(p: Project) -> list[dict]:
    tr = p.load("transcript", {"pages": {}})
    out = []
    for n, v in sorted(tr["pages"].items(), key=lambda kv: int(kv[0])):
        found = find_figures(v.get("text", ""))
        for k, (_, _, desc, _) in enumerate(found):
            out.append({"page": int(n), "k": k, "of": len(found), "desc": desc})
    return out


def stage_figures(run: Run) -> None:
    """What each drawing is for (drawings.py), then a redrawing of every drawing that may be printed. A drawing that
    only explains an argument is settled without being redrawn; the author sees it and can still print it."""
    from . import drawings
    p = run.p
    conf = p.load("confirmed", {})
    wanted = transcript_figures(p)
    if not wanted:
        p.save("figures", {"items": []})
        run.log("The notes contain no drawings, so there are no figures to draw.")
        return
    old = {(f["page"], f["k"]): f for f in (p.load("figures") or {}).get("items", [])}
    taken = {f["id"] for f in old.values()}
    items: list[dict] = []
    for w in wanted:
        prev = old.get((w["page"], w["k"]))
        if prev and prev.get("desc") == w["desc"]:
            items.append({**prev, "of": w["of"]})
            continue
        fid = prev["id"] if prev else next(f"f{n}" for n in range(1, 10000) if f"f{n}" not in taken)
        taken.add(fid)
        items.append({"id": fid, **w, "caption": "", "box": None, "tikz": "", "tikz_ok": False, "error": "", "choice": ""})
    fresh = [f for f in items if not f.get("judged")]
    if fresh:
        run.progress(0, len(items), "deciding which drawings are for the paper and which only explain an argument")
        tr = p.load("transcript", {"pages": {}})
        drawings.judge(run, fresh, {int(n): v.get("text", "") for n, v in tr["pages"].items()})
        _save_figures(p, items)
    todo = [f for f in items if drawings.wanted_drawn(f) and not f.get("drawn")]
    _locate_figures(run, items, todo)
    for rec in todo:
        if rec.get("dup_of") and not rec.get("answered"):
            first = next((f for f in items if f["id"] == rec["dup_of"]), None)
            rec.update(choice="none", auto=True, dup=True)
            rec.setdefault("judged", {})["why"] = (f"The same drawing as the one on page {first['page'] if first else rec['page']}"
                                                   " (the transcription marked it twice), so it is printed once.")
    _save_figures(p, items)
    todo = [f for f in todo if not f.get("dup")]
    lock, drawn = threading.Lock(), [0]
    if todo:
        run.progress(0, len(todo), f"drawing {plural(len(todo), 'figure')}")

    def draw(rec: dict) -> None:                 # each drawing is redrawn from its own page alone
        run.check()
        _draw_figure(p, rec, run.ask, conf)
        with lock:
            drawn[0] += 1
            _save_figures(p, items)
            run.progress(drawn[0], len(todo), f"figure on page {rec['page']} drawn")

    _side_by_side(run, "vision_best", True, todo, draw)
    _save_figures(p, items)
    run.progress(len(todo), len(todo), "figures ready")
    explained = [f for f in items if f.get("choice") == "explain" and f.get("auto")]
    asked = [f for f in items if f.get("choice") not in ("tikz", "orig", "none", "explain")]
    good = sum(1 for f in todo if f.get("tikz_ok"))
    run.log(f"{plural(len(items), 'drawing')} in the notes: {len(explained)} only explain an argument and are not printed "
            f"(you can still print any of them); {plural(len(todo), 'drawing')} redrawn ({good} successfully). "
            + (f"You will be asked about {plural(len(asked), 'drawing')}." if asked else "Nothing needs your answer."))
    if todo and all(f.get("tikz_ok") is None for f in todo):
        run.log("No LaTeX program is installed on this computer, so the redrawn figures cannot be shown. "
                "Your own drawings, cut from the pages, are offered instead.", "warn")


def _locate_figures(run: Run, items: list[dict], todo: list[dict]) -> None:
    """Find on each page the drawings that are to be drawn: one reading of the page for all its drawings, so that
    a drawing the transcription marked twice is found as such."""
    from . import figflow
    p = run.p
    pages = sorted({f["page"] for f in todo if not f.get("pbox")})
    lock = threading.Lock()

    def find(n: int) -> None:                    # each page is looked at alone, for all its drawings at once
        run.check()
        recs = [f for f in items if f["page"] == n]
        try:
            figflow.locate(p, n, recs, run.ask, _page_bytes(p, n), _page_record(p, n))
        except (LLMError, Cancelled):
            raise
        except Exception:  # noqa: BLE001 - each figure is then found when it is drawn
            traceback.print_exc()
        with lock:
            _save_figures(p, items)

    _side_by_side(run, "vision_best", True, pages, find)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _save_figures(p: Project, items: list[dict]) -> None:
    """Save the figures, keeping every answer the author gave meanwhile (they may answer while others are drawn):
    an answer given after the stage read the figures wins."""
    with p._lock:
        cur = {f["id"]: f for f in (p.load("figures") or {}).get("items", [])}
        for f in items:
            c = cur.get(f["id"])
            if c and c.get("desc") == f.get("desc") and c.get("answered", 0) > f.get("answered", 0):
                for k in ("choice", "caption", "caption_edited", "auto", "asked_print", "answered"):
                    if k in c:
                        f[k] = c[k]
                    else:
                        f.pop(k, None)
        p.save("figures", {"items": items})


# ---------------------------------------------------------------- changes asked for in step 9, carried out at once
# A change the author asks for while checking the transcript (a figure drawn again, a page corrected from a comment) is
# started the moment it is asked for and runs in the background: the author goes on checking, the result appears in
# step 9 when it is done (with any new question it raises), and the transcript cannot be approved while one runs.
_jobs: dict[str, dict[str, dict]] = {}          # project -> what is being done: key -> {"key", "what", "since", ...}
_jobs_done: dict[str, list[dict]] = {}          # project -> the latest outcomes: {"key", "ok", "message", "at", ...}
_jobs_lock = threading.Lock()
_job_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scriptavera-review")


def review_jobs(p: Project) -> list[dict]:
    with _jobs_lock:
        return sorted(_jobs.get(p.id, {}).values(), key=lambda j: j["since"])


def review_jobs_done(p: Project) -> list[dict]:
    """What the changes finished in the last quarter of an hour came to, for the screen to say once."""
    with _jobs_lock:
        return [d for d in _jobs_done.get(p.id, []) if time.time() - d["at"] < 900]


def start_review_job(p: Project, key: str, what: str, work, said=None, **info) -> dict:
    """Start one change in the background: work() does it; said(result) is what to tell the author when it is done.
    One change at a time for each figure or page (key)."""
    with _jobs_lock:
        if key in _jobs.get(p.id, {}):
            raise ValueError("This is being done already. It appears here as soon as it is ready.")
        job = {"key": key, "what": what, "since": time.time(), **info}
        _jobs.setdefault(p.id, {})[key] = job

    def run():
        ok, message = True, ""
        try:
            got = work()
            message = said(got) if said else ""
        except Cancelled:
            ok, message = False, f"{what}: stopped."
        except (ValueError, LLMError) as e:
            ok, message = False, str(e)
        except Exception as e:  # noqa: BLE001 - the author is told; the rest of step 9 goes on
            traceback.print_exc()
            ok, message = False, f"{what} failed ({type(e).__name__}: {e})."
        finally:
            with _jobs_lock:
                _jobs.get(p.id, {}).pop(key, None)
                done = _jobs_done.setdefault(p.id, [])
                done.append({"key": key, "ok": ok, "message": message, "at": time.time(), **info})
                del done[:-20]
            emit(p.id, "log", stage="review", level="done" if ok else "error", message=message or f"{what}: done.")
            emit(p.id, "state")
    _job_pool.submit(contextvars.copy_context().run, run)          # the same owner's keys
    emit(p.id, "state")
    return job


_PLACE = ("box", "pbox", "th", "exclude", "dup_of")


@contextlib.contextmanager
def _keeping_place(p: Project, rec: dict):
    """Where a drawing is on its page is found by a model, which can take minutes. When drawing it then fails (a model
    does not answer, say), the place found is kept, so that the next attempt starts from it instead of asking again."""
    try:
        yield
    except BaseException:
        if rec.get("pbox"):
            with p._lock:
                cur = p.load("figures") or {"items": []}
                for f in cur["items"]:
                    if f["id"] == rec["id"] and not f.get("pbox"):
                        f.update({k: rec[k] for k in _PLACE if k in rec})
                        f.pop("locate_error", None)
                p.save("figures", cur)
        raise


def redraw_figure(p: Project, fid: str, instruction: str) -> dict:
    """The author says the drawing is not what they meant: one more attempt, with their correction."""
    data = p.load("figures") or {"items": []}
    rec = next((f for f in data["items"] if f["id"] == fid), None)
    if not rec:
        raise ValueError("Unknown figure.")
    hooks = Run(p)
    hooks.stage = "figures"

    def ask(role, system, user, **kw):
        return router.chat(role, system, user, hooks=hooks, **kw)
    with _keeping_place(p, rec):
        _draw_figure(p, rec, ask, p.load("confirmed", {}), instruction.strip())
    rec["redrawn"] = rec.get("redrawn", 0) + 1
    with p._lock:
        cur = p.load("figures") or {"items": []}
        cur["items"] = [rec if f["id"] == fid else f for f in cur["items"]]
        p.save("figures", cur)
    return rec


def print_figure(p: Project, fid: str) -> dict:
    """The author wants a drawing printed after all (the app took it for an explanation, or they kept it out): it is
    redrawn if it was not, and then asked about like every figure for the paper."""
    data = p.load("figures") or {"items": []}
    rec = next((f for f in data["items"] if f["id"] == fid), None)
    if not rec:
        raise ValueError("Unknown figure.")
    rec.update(choice="", auto=False, asked_print=True, answered=time.time())
    if not rec.get("drawn"):
        hooks = Run(p)
        hooks.stage = "figures"

        def ask(role, system, user, **kw):
            return router.chat(role, system, user, hooks=hooks, **kw)
        with _keeping_place(p, rec):
            _draw_figure(p, rec, ask, p.load("confirmed", {}))
    with p._lock:
        cur = p.load("figures") or {"items": []}
        cur["items"] = [rec if f["id"] == fid else f for f in cur["items"]]
        p.save("figures", cur)
    return rec


def _passage_around(text: str, a: int, b: int) -> tuple[int, int]:
    """The stretch of a page around a figure marker that the annotation can belong to: the displayed formula just
    before it (if any), the marker, and the rest of the marker's line."""
    start = a
    m = re.search(r"(\$\$(?:(?!\$\$).)*\$\$|\\\[(?:(?!\\\[).)*\\\])\s*$", text[:a], flags=re.S)
    if m and a - m.start() < 1500:
        start = m.start()
    start = text.rfind("\n", 0, start) + 1
    end = text.find("\n", b)
    end = len(text) if end < 0 else end
    if end - b > 400:
        end = b
    return start, end


def _math_pieces(text: str) -> list[str]:
    from .texutil import drop_brace_labels, split_math
    out = []
    for kind, body in split_math(drop_brace_labels(text)):
        if kind != "text":
            out.extend(fidelity.normalize(x) for x in fidelity._lines(body) if fidelity.normalize(x))
    return out


def figure_as_text(p: Project, fid: str, note: str = "") -> dict:
    """The author says a "figure" is their own annotation (labels on a formula, a remark). The passage is rewritten
    with the annotation in the text, the figure is dropped, and nothing in the mathematics may change."""
    data = p.load("figures") or {"items": []}
    rec = next((f for f in data["items"] if f["id"] == fid), None)
    if not rec:
        raise ValueError("Unknown figure.")
    tr = p.load("transcript", {"pages": {}})
    page = tr["pages"].get(str(rec["page"]))
    found = find_figures(page["text"]) if page else []
    if not page or rec["k"] >= len(found):
        raise ValueError("This figure is no longer in the transcript of the page.")
    text = page["text"]
    a, b, desc, _ = found[rec["k"]]
    s, e = _passage_around(text, a, b)
    passage = text[s:e]
    hooks = Run(p)
    hooks.stage = "review"
    user = (f"PASSAGE FROM PAGE {rec['page']}\n{passage}\n\n"
            + (f"THE AUTHOR'S NOTE\n{note.strip()}\n\n" if note.strip() else "")
            + f"CONFIRMED NOTATION\n{notation_text(p.load('confirmed', {}), 1000)}")
    res = router.chat("vision_best", prompts.FIG_AS_TEXT.format(), user, images=[_page_bytes(p, rec["page"])],
                      max_out=1800, effort="medium", label=f"Write figure {fid[1:]} into the text (page {rec['page']})",
                      hooks=hooks)
    out = clean_transcript(extract_tag(res.text, "passage") or "").strip()
    if not out or find_figures(out):
        raise ValueError("The annotation could not be written into the text. Edit the page by hand below, "
                         "or leave the figure out.")
    # the mathematics of the passage (outside the marker) must all still be there, labels aside
    kept = _math_pieces(text[s:a] + text[b:e])
    now = "".join(_math_pieces(out))
    lost = [x for x in kept if x not in now]
    if lost:
        raise ValueError("Writing it into the text would have changed a formula, so nothing was changed. "
                         "Edit the page by hand below, or leave the figure out.")
    lead = "" if s == 0 or text[s - 1] == "\n" else "\n"
    with p._lock:
        tr = p.load("transcript", {"pages": {}})
        cur = tr["pages"][str(rec["page"])]
        if cur["text"] != text:
            raise ValueError("The page changed meanwhile. Try again.")
        cur["text"], cur["edited"] = text[:s] + lead + out + text[e:], True
        p.save("transcript", tr)
        data = p.load("figures") or {"items": []}
        items = []
        for f in data["items"]:
            if f["id"] == fid:
                continue
            if f["page"] == rec["page"]:
                f = {**f, "of": max(1, f.get("of", 1) - 1), "k": f["k"] - 1 if f["k"] > rec["k"] else f["k"]}
            items.append(f)
        data["items"] = items
        p.save("figures", data)
        answers = p.load("doubt_answers", [])
        answers.append({"page": rec["page"], "snippet": "[figure " + fid + "]", "alt": "", "issue": desc[:400],
                        "how": "note", "value": "", "at": time.time()})
        p.save("doubt_answers", answers)
    for kind in ("tikz", "orig"):
        try:
            figure_file(p, fid, kind).unlink()
        except OSError:
            pass
    emit(p.id, "log", stage="review", level="done", message=f"Page {rec['page']}: your note is now part of the text, not a figure.")
    return {"page": rec["page"], "text": cur["text"]}


def set_figure(p: Project, fid: str, choice: str | None = None, caption: str | None = None) -> dict:
    with p._lock:
        data = p.load("figures") or {"items": []}
        rec = next((f for f in data["items"] if f["id"] == fid), None)
        if not rec:
            raise ValueError("Unknown figure.")
        if choice is not None:
            if choice not in ("tikz", "orig", "none", "explain", ""):
                raise ValueError("Unknown choice.")
            if choice == "tikz" and not rec.get("tikz_ok"):
                raise ValueError("The redrawn version of this figure could not be produced; use your own drawing or leave it out.")
            if choice == "orig" and not rec.get("drawn"):
                raise ValueError("This drawing has not been cut from the page yet. Choose to print it first, so that it is drawn.")
            rec["choice"], rec["auto"] = choice, False
            if choice in ("explain", "none"):
                rec.pop("asked_print", None)
        if caption is not None:
            rec["caption"], rec["caption_edited"] = re.sub(r"\s+", " ", caption).strip(), True
        rec["answered"] = time.time()
        p.save("figures", data)
    return rec


def approved_pages(p: Project) -> list[dict]:
    appr = p.load("approved")
    if appr:
        return appr["pages"]
    tr = p.load("transcript", {"pages": {}})
    return [{"n": int(n), "text": v["text"]} for n, v in sorted(tr["pages"].items(), key=lambda kv: int(kv[0])) if v["text"].strip()]


def writing_pages(p: Project) -> tuple[list[dict], dict]:
    """The approved transcript with each figure marker carrying its id, and the figures the author kept. A drawing
    that only explains the argument stays in the notes as [EXPLANATORY DRAWING: ...]: the writers read it, and it is
    never printed."""
    recs = {(f["page"], f["k"]): f for f in (p.load("figures") or {}).get("items", [])}
    figs: dict = {}
    pages = []
    for pg in approved_pages(p):
        text = pg["text"]
        for k, (a, b, desc, _) in reversed(list(enumerate(find_figures(text)))):
            rec = recs.get((pg["n"], k))
            if rec and rec.get("choice") in ("tikz", "orig"):
                figs[rec["id"]] = {"mode": rec["choice"], "tikz": rec.get("tikz", ""), "caption": rec.get("caption", ""),
                                   "page": pg["n"]}
                text = text[:a] + f"[FIGURE {rec['id']}: {desc}]" + text[b:]
            elif rec and rec.get("choice") == "none":
                text = text[:a] + text[b:]
            elif rec and rec.get("choice") == "explain":
                text = text[:a] + f"[EXPLANATORY DRAWING: {desc}]" + text[b:]
            else:
                text = text[:a] + f"[FIGURE: {desc}]" + text[b:]
        pages.append({"n": pg["n"], "text": text})
    return pages, dict(sorted(figs.items(), key=lambda kv: int(kv[0][1:])))


# ------------------------------------------------------------------ gates

def submit_confirm(p: Project, data: dict) -> None:
    """Gate 1. The author confirms the topic and every undefined or ambiguous item."""
    glossary = p.load("glossary", {"entries": [], "questions": []})
    by_id = {e["id"]: e for e in glossary["entries"]}
    sent = {str(i.get("id")): i for i in data.get("glossary", [])}
    confirmed, unresolved, ignored = [], [], []
    for e in glossary["entries"]:
        item = sent.get(e["id"], {})
        meaning = (item.get("meaning") if "meaning" in item else e.get("meaning") or "").strip()
        needs = e["status"] in ("undefined", "ambiguous")
        if e["status"] == "misread":
            if not item.get("confirmed") or not (item.get("meaning") or "").strip() or item.get("meaning") == e.get("meaning"):
                meant = re.sub(r"^(?:probably|perhaps|likely)\s+", "", (e.get("meaning") or "").strip(), flags=re.I)
                ignored.append(f"{e['symbol']} (probably {meant or 'a misreading'})")
                continue
        if item.get("dismiss"):
            ignored.append(e["symbol"])
            continue
        if needs and not (item.get("confirmed") and meaning):
            unresolved.append(e["symbol"])
            continue
        if meaning:
            confirmed.append({"symbol": e["symbol"], "meaning": meaning, "kind": e["kind"], "status": e["status"],
                              "by": "author" if meaning != (e.get("meaning") or "").strip() else "confirmed proposal"})
    if unresolved:
        raise ValueError("These items still need your confirmation (or dismissal): " + ", ".join(unresolved[:8]))
    for extra in data.get("extra", []):
        if (extra.get("symbol") or "").strip() and (extra.get("meaning") or "").strip():
            confirmed.append({"symbol": extra["symbol"].strip(), "meaning": extra["meaning"].strip(), "kind": "symbol",
                              "status": "added", "by": "author"})
    topic_in = data.get("topic") or {}
    old = p.load("topic", {})
    topic = {"problem": (topic_in.get("problem") or old.get("problem", "")).strip(),
             "subfield": (topic_in.get("subfield") or old.get("subfield", "")).strip(),
             "keywords": [k.strip() for k in (topic_in.get("keywords") or old.get("keywords", [])) if str(k).strip()]}
    answers = [{"question": a.get("question", ""), "answer": (a.get("answer") or "").strip()}
               for a in data.get("answers", []) if (a.get("answer") or "").strip()]
    p.save("confirmed", {"topic": topic, "glossary": confirmed, "answers": answers, "ignored": ignored,
                         "instructions": (data.get("instructions") or "").strip(), "at": time.time()})
    from . import memory
    memory.learn_notation(p.state.get("meta", {}), confirmed)        # the next paper proposes these meanings
    p.set_stage("confirm", status="done", finished=time.time())
    emit(p.id, "log", stage="confirm", level="done", message=f"You confirmed {len(confirmed)} notation items.")


def locate_symbol(p: Project, symbol: str, page: int, context: str = "") -> dict:
    """Ask the page reader to point at a symbol on the page image. Cached, so each lookup costs one call once."""
    cache = p.load("locations", {})
    key = f"{page}|{symbol}"
    if key in cache:
        return cache[key]
    hooks = Run(p)
    hooks.stage = "confirm"
    user = f"Symbol or term to find: {symbol}" + (f"\nA draft transcription read it in this context: {fit(context, 500, 'head')}" if context else "")
    res = router.chat("vision_best", prompts.LOCATE.format(), user, images=[_page_bytes(p, page)], json_mode=True,
                      max_out=500, effort="low", label=f"Locate {symbol} on page {page}", hooks=hooks, strict=False)
    boxes: list[list[int]] = []
    note, found = "", False
    try:
        data = loads_lenient(res.text)
        raw = data.get("boxes") if isinstance(data, dict) else data
        note = str(data.get("note") or "") if isinstance(data, dict) else ""
        for b in raw or []:
            if isinstance(b, dict):
                b = b.get("box_2d") or b.get("box") or []
            if isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                vals = [float(v) for v in b]
                if max(vals) <= 1.0:
                    vals = [v * 1000 for v in vals]
                y0, x0, y1, x1 = (max(0, min(1000, int(v))) for v in vals)
                if y1 - y0 >= 2 and x1 - x0 >= 2:
                    boxes.append([y0, x0, y1, x1])
        found = bool(boxes)
    except ValueError:
        note = "The reader's answer could not be understood."
    out = {"found": found, "boxes": boxes[:6], "note": note[:300], "model": res.model}
    with p._lock:
        cache = p.load("locations", {})
        cache[key] = out
        p.save("locations", cache)
    return out


def save_transcript_page(p: Project, n: int, text: str, keep_lines: bool = False) -> str:
    """Save the author's page n. keep_lines: the change was made somewhere in the page (an answer to a doubt), not to
    its shape, so the line breaks and the places of the notes the page has on the server are kept where the text sent
    has them otherwise (the page was put into shape meanwhile, relines.py). Returns the page as saved."""
    with p._lock:                          # read and written in one go: nothing else changes the page in between
        tr = p.load("transcript", {"pages": {}})
        rec = tr["pages"].setdefault(str(n), {"text": "", "uncertain": [], "changes": [], "model": "", "edited": False})
        if keep_lines and rec["text"] and rec["text"] != text:
            from . import relines
            lined, _, share = relines.transfer(text, rec["text"])
            if share >= relines.GATE:
                text = lined
        if rec["text"] != text:
            rec["text"], rec["edited"] = text, True
            p.save("transcript", tr)
        return rec["text"]


def submit_topic(p: Project, data: dict) -> str:
    """Gate after the first search. 'confirm': the topic and the papers are right. 'refine': the author's comment (and
    any corrected problem or keywords) goes to the model, which rewrites the topic, and the search is done again.
    Returns what was done."""
    action = data.get("action") or "confirm"
    comment = re.sub(r"\s+", " ", str(data.get("comment") or "")).strip()[:2000]
    meta = p.state.get("meta", {})
    problem = str(data.get("problem") if data.get("problem") is not None else meta.get("problem", "")).strip()[:3000]
    kw = data.get("keywords") if data.get("keywords") is not None else meta.get("keywords", [])
    keywords = [k.strip() for k in (kw if isinstance(kw, list) else str(kw).split(",")) if str(k).strip()][:20]
    changed = problem != (meta.get("problem") or "").strip() or keywords != list(meta.get("keywords") or [])
    if changed:
        if len(problem) < 20 or len(keywords) < 2:
            raise ValueError("Describe the problem in a sentence or two, and give at least two keywords.")
        p.update_state(lambda st: st["meta"].update(problem=problem, keywords=keywords))
    topic = p.load("topic") or {}
    if action == "refine":
        if not comment and not changed:
            raise ValueError("Write what the notes are about more precisely, or correct the problem or the keywords, then search again.")
        if comment:
            topic["author_notes"] = (topic.get("author_notes") or []) + [comment]
            topic["pending_note"] = comment
        else:
            topic["pending_refresh"] = True
        p.save("topic", topic)
        p.reset_from("topiccheck")                 # what was built on the old topic goes; the papers found stay
        p.set_stage("search1", status="pending")
        emit(p.id, "log", stage="topiccheck", level="info", message="Searching again with your comment.")
        return "refine"
    if comment:
        topic["author_notes"] = (topic.get("author_notes") or []) + [comment]
        p.save("topic", topic)
    p.save("topic_ok", {"at": time.time(), "comment": comment})
    p.set_stage("topiccheck", status="done", finished=time.time())
    emit(p.id, "log", stage="topiccheck", level="done", message="Topic confirmed by you.")
    return "confirm"


def assumed_choice(f: dict) -> str:
    """The most likely answer for a figure the author did not answer: as it was judged (drawings.py), the redrawn
    figure when it was drawn, else the author's own drawing; a drawing judged to explain the argument (or to be a
    note on a formula) is not printed, and the writers still read it."""
    use = (f.get("judged") or {}).get("use")
    if use in ("explanation", "annotation"):
        return "explain"
    return "tikz" if f.get("tikz_ok") else "orig"


def _assume_reading(text: str) -> str:
    """A doubtful reading the author did not settle: the reading first given, the most likely one ([?a|b?] gives a)."""
    return re.sub(r"\[\?([^\[\]|?]+?)(?:\|[^\[\]]*?)?\?\]", lambda m: m.group(1).strip(), text)


def submit_review(p: Project, data: dict) -> None:
    """Gate 2. The author approves the transcript (after any edits) and says, for each figure, which drawing to use.
    assume: what is left unanswered gets the most likely answer (a reading stays as it was read, a figure goes as it
    was judged), instead of stopping the approval. Never while a change asked for in step 9 is still being made."""
    if review_jobs(p):
        raise ValueError("Wait: " + "; ".join(j["what"].lower() for j in review_jobs(p)) + " is still being done. "
                         "The transcript is approved with it.")
    for n, text in (data.get("pages") or {}).items():
        save_transcript_page(p, int(n), text)
    for fid, choice in (data.get("figures") or {}).items():
        set_figure(p, fid, choice=choice)
    if data.get("assume"):
        present = {(f["page"], f["k"]) for f in transcript_figures(p)}
        with p._lock:
            figs = p.load("figures") or {"items": []}
            for f in figs["items"]:
                if (f["page"], f["k"]) in present and f.get("choice") not in ("tikz", "orig", "none", "explain"):
                    f["choice"], f["auto"] = assumed_choice(f), True
            p.save("figures", figs)
            tr = p.load("transcript", {"pages": {}})
            for v in tr["pages"].values():
                v["text"] = _assume_reading(v.get("text", ""))
            p.save("transcript", tr)
    tr = p.load("transcript", {"pages": {}})
    pages = [{"n": int(n), "text": v["text"]} for n, v in sorted(tr["pages"].items(), key=lambda kv: int(kv[0]))
             if v["text"].strip()]
    if not pages:
        raise ValueError("The transcript is empty.")
    waiting = pending_comments(p)
    if waiting:
        raise ValueError("Your comment" + (" on page " if len(waiting) == 1 else "s on pages ") + ", ".join(map(str, waiting))
                         + (" has" if len(waiting) == 1 else " have") + " not been carried out yet. Correct the page"
                         + ("" if len(waiting) == 1 else "s") + " from your comments first.")
    present = {(f["page"], f["k"]) for f in transcript_figures(p)}
    open_figs = [f for f in (p.load("figures") or {}).get("items", [])
                 if (f["page"], f["k"]) in present and f.get("choice") not in ("tikz", "orig", "none", "explain")]
    if open_figs:
        raise ValueError("Please say for each figure whether it is the one you meant (still open: page "
                         + ", ".join(str(f["page"]) for f in open_figs[:8]) + ").")
    corrections = {int(n): [{k: c[k] for k in ("comment", "changes", "reply")} for c in v.get("corrections", [])]
                   for n, v in tr["pages"].items() if v.get("corrections")}
    p.save("approved", {"pages": pages, "corrections": corrections, "at": time.time()})
    p.set_stage("review", status="done", finished=time.time())
    edited = sum(1 for v in tr["pages"].values() if v.get("edited"))
    emit(p.id, "log", stage="review", level="done", message=f"Transcript approved ({edited} page(s) edited by you).")


# The reading steps live here. The writing, revision and final steps are resolved by _stage_func.
STAGE_FUNCS = {
    "scout": stage_scout, "search1": stage_search1, "survey": stage_survey, "reading": stage_reading,
    "transcribe": stage_transcribe, "figures": stage_figures,
}
