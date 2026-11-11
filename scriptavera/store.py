# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Project storage. One folder per project; every stage's output is a file, so a
run can stop, resume, or be redone from any stage."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

# The pipeline, in order. `gate` stages wait for the author. Stages are grouped into phases for display.
PHASES: list[dict] = [
    {"id": "read", "name": "Reading your notes"},
    {"id": "write", "name": "First draft"},
    {"id": "revise", "name": "Revision"},
    {"id": "final", "name": "Final version"},
]
STAGES: list[dict] = [
    {"id": "scout", "phase": "read", "name": "Scout read", "desc": "A few pages, words only, to find the topic"},
    {"id": "search1", "phase": "read", "name": "Literature search", "desc": "Papers in your domain, from scholarly databases"},
    {"id": "topiccheck", "phase": "read", "name": "Your topic",
     "desc": "You confirm the topic and the papers found, or say more precisely what the notes are about and the search is done again",
     "gate": True},
    {"id": "survey", "phase": "read", "name": "Notation survey",
     "desc": "Every page read with topic context; all symbols catalogued"},
    {"id": "confirm", "phase": "read", "name": "Your confirmation",
     "desc": "You confirm the topic and every undefined term", "gate": True},
    {"id": "reading", "phase": "read", "name": "Deep reading",
     "desc": "Refined search; the most relevant papers read one by one"},
    {"id": "transcribe", "phase": "read", "name": "Final transcription",
     "desc": "Every page re-read with confirmed notation and context"},
    {"id": "figures", "phase": "read", "name": "Figures",
     "desc": "Every drawing in your notes copied as you drew it: found on its page and drawn again line for line. "
             "Which ones the paper prints is decided later, with the whole paper in view"},
    {"id": "review", "phase": "read", "name": "Your review",
     "desc": "You check the transcript, and that each drawing was copied as you drew it", "gate": True},
    {"id": "understand", "phase": "write", "name": "Understanding the paper",
     "desc": "The whole of your notes read together: problem, results, how they depend on each other"},
    {"id": "assess", "phase": "write", "name": "Assessment of the notes",
     "desc": "For every result: is it fully proved, is the proof incomplete, or is it already in the literature; and does the main claim stand"},
    {"id": "scope", "phase": "write", "name": "Your statements",
     "desc": "Every definition, lemma, proposition and theorem written as a statement for the paper; you check each one, "
             "answer the questions about unclear ones, and decide what kind of paper to write", "gate": True},
    {"id": "outline", "phase": "write", "name": "Your structure",
     "desc": "The skeleton of the paper: sections, every statement in the order it is presented (each before it is "
             "used), and each of your drawings: printed as a figure where it belongs, or kept only to explain an argument",
     "gate": True},
    {"id": "figdesign", "phase": "write", "name": "Figures for the paper",
     "desc": "Each figure the paper prints drawn again the way a research paper draws it, with the whole paper in view: "
             "what it shows stays as you drew it, except a mistake the text proves, which is named for you to confirm"},
    {"id": "proofs", "phase": "write", "name": "Proofs",
     "desc": "Each proof written on its own from your argument, with the statements it uses, and checked against your "
             "argument; a proof that fails the check is written again"},
    {"id": "proofcheck", "phase": "write", "name": "Your review of the proofs",
     "desc": "The paper with its proofs: you read what was added to your arguments, decide where a statement may be false, "
             "and confirm each figure drawn again for the paper", "gate": True},
    {"id": "draft", "phase": "write", "name": "First draft",
     "desc": "The text that joins the results, then the introduction and the abstract, written from the finished paper"},
    {"id": "draftcheck", "phase": "write", "name": "Your first draft",
     "desc": "You read the first draft with its introduction and abstract", "gate": True},
    {"id": "simplify", "phase": "revise", "name": "Shorter arguments",
     "desc": "The draft re-read for arguments used twice and proofs that can be shortened"},
    {"id": "verify", "phase": "revise", "name": "Proof check",
     "desc": "Every proof examined step by step; each suspected problem checked a second time"},
    {"id": "referee", "phase": "revise", "name": "Referee's read",
     "desc": "The whole paper read the way a journal referee reads it"},
    {"id": "decide", "phase": "revise", "name": "Your decisions",
     "desc": "You accept or reject each proposed change to the mathematics", "gate": True},
    {"id": "assemble", "phase": "final", "name": "Final version",
     "desc": "Your decisions applied; language, tone and typography corrected; abstract, title suggestions, journal format"},
    {"id": "finish", "phase": "final", "name": "Title, introduction, citations",
     "desc": "You choose the title from the suggestions, approve the introduction and choose the papers it cites", "gate": True},
]
STAGE_IDS = [s["id"] for s in STAGES]
LAYOUT = 9                      # bumped whenever the list of stages, or what a stage makes, changes

# What each stage writes. Redoing a stage removes its own files and those of every later stage.
ARTIFACTS: dict[str, list[str]] = {
    "scout": ["scout", "topic", "key_papers"],
    "search1": ["library", "search_status"],
    "topiccheck": ["topic_ok"],
    "survey": ["survey", "glossary", "locations"],
    "confirm": ["confirmed"],
    "reading": ["brief"],
    "transcribe": ["transcript", "flags", "dismissed", "doubt_answers", "page_checks"],
    "figures": ["figures"],
    "review": ["approved"],
    "understand": ["dossier"],
    "assess": ["assessment", "novelty"],
    "scope": ["scope", "statements"],
    "outline": ["outline", "figure_plan"],
    "figdesign": ["figures_pro"],
    "proofs": ["proofs"],
    "proofcheck": ["proofs_ok"],
    "draft": ["plan", "body", "audit", "intro", "abstract_draft", "structure_partial", "connect_partial"],
    "draftcheck": ["draft_ok"],
    "simplify": ["simplify"],
    "verify": ["verify"],
    "referee": ["referee"],
    "decide": ["decisions"],
    "assemble": ["front", "paper", "final_partial", "revision", "citations"],
    "finish": ["finished"],
}


# What kind of notes were uploaded decides which steps are needed. Handwritten notes (scans, photos, a PDF of images)
# go through every step. Typed notes (LaTeX, Markdown, text) are their own transcript: nothing is read from an image,
# so there is no transcription to make, no drawing to find and nothing to check. A PDF made on a computer has the
# exact words in its text layer; its formulas are still read from the page, and the author is asked only about the
# figures, under "Your figures" (the step disappears when no figure needs a decision).
SOURCE_LABELS = {"handwritten": "Handwritten or scanned notes", "text_pdf": "A PDF made on a computer",
                 "typed": "LaTeX or typed notes"}
SKIPPED_FOR = {"typed": ["transcribe", "figures", "review"], "text_pdf": [], "handwritten": []}


def source_kind(state: dict, folder: Path) -> str:
    pages = [pg for pg in state.get("pages", []) if isinstance(pg, dict) and not pg.get("skip")]
    if not pages:
        return "handwritten"

    def read(name: str) -> dict:
        f = folder / f"{name}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except Exception:
            return {}
    typed_pages = read("typed").get("pages", {})
    # text_layer is set at upload only for text a computer drew on the page: the hidden text that tablets and OCR put
    # under handwriting does not count, and a page uploaded before this check is treated as handwriting
    kinds = {"typed" if (pg.get("typed") or str(pg["n"]) in typed_pages) else "text" if pg.get("text_layer") else "image"
             for pg in pages}
    if "image" in kinds:
        return "handwritten"                  # one page read from its image makes the whole upload need every step
    return "typed" if kinds == {"typed"} else "text_pdf"


def hidden_stages(state: dict, folder: Path) -> list[str]:
    """The steps this upload does not need: they still run where they have work to do, but are not shown."""
    kind = source_kind(state, folder)
    out = list(SKIPPED_FOR[kind])
    if kind == "text_pdf" and state.get("stages", {}).get("review", {}).get("skipped"):
        out.append("review")                  # no figure needed the author's decision
    return out


def source_plan(state: dict, folder: Path) -> dict:
    kind = source_kind(state, folder)
    return {"kind": kind, "label": SOURCE_LABELS[kind], "hidden": hidden_stages(state, folder),
            "names": {"review": "Your figures"} if kind == "text_pdf" else {}}


def reuse_ok(state: dict) -> bool:
    """Whether a step may keep what it made before from exactly the same inputs: when the paper is being brought up
    to date after a change (or pages were added). A step the author asked to redo is done anew: while it runs,
    nothing of it is kept; the steps after it are brought up to date from what it made."""
    cu = state.get("catchup") or {}
    anew = cu.get("anew")
    if anew and ((state.get("stages") or {}).get(anew) or {}).get("status") == "running":
        return False
    return bool(cu or state.get("incremental"))


def is_stale(s: dict) -> bool:
    """A step being brought up to date: marked so, or stopped (by the author or a failure) while it was."""
    return s.get("status") == "stale" or (bool(s.get("was")) and s.get("status") not in ("done", "waiting"))


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(pid: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.RLock())


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.dir = config.PROJECTS_DIR / pid
        self._lock = _lock_for(pid)      # shared by every Project object for this id

    # ------------------------------------------------------------ files

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def load(self, name: str, default=None):
        p = self.dir / f"{name}.json"
        with self._lock:
            if not p.exists():
                return default
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return default

    def save(self, name: str, data) -> None:
        with self._lock:
            p = self.dir / f"{name}.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(6):          # Windows refuses to replace a file another program has open
                try:
                    tmp.replace(p)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.15)

    def remove(self, *names: str) -> None:
        for n in names:
            p = self.dir / f"{n}.json"
            if p.exists():
                p.unlink()

    # ------------------------------------------------------------ state

    @property
    def state(self) -> dict:
        return self.load("state", {})

    def update_state(self, fn) -> dict:
        with self._lock:
            if not (self.dir / "state.json").exists():     # the project was deleted under a running stage
                return {}
            st = self.load("state", {})
            fn(st)
            st["updated"] = time.time()
            self.save("state", st)
            return st

    def set_stage(self, stage: str, **kw) -> None:
        def f(st):
            st["stages"].setdefault(stage, {}).update(kw)
        self.update_state(f)

    def log_call(self, rec: dict) -> None:
        with self._lock:
            with open(self.dir / "calls.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def calls(self, limit: int = 400) -> list[dict]:
        p = self.dir / "calls.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for l in lines:
            try:
                out.append(json.loads(l))
            except Exception:
                pass
        return out

    # ---------------------------------------------------- invalidation

    def update_from(self, stage: str, why: str = "") -> None:
        """A change made at this step, or before it, after the paper went further: this step and every later one are
        brought up to date instead of being done again. Nothing is deleted. Each step redoes only the parts whose
        inputs changed (every part keeps a fingerprint of what it was made from) and keeps the rest with the author's
        decisions on them; a stop the author had already passed is passed again by itself unless something there
        needs them anew, and then it shows only what changed. A step not reached yet stays as it was."""
        idx = STAGE_IDS.index(stage)
        now = time.time()

        def f(st):
            stages = st.setdefault("stages", {})
            reached = next((sid for sid in STAGE_IDS if stages.get(sid, {}).get("status") != "done"), None)
            for sid in STAGE_IDS[idx:]:
                s = stages.setdefault(sid, {"status": "pending"})
                status = s.get("status")
                if status in (None, "pending") and not s.get("was"):
                    continue
                was = (s.get("was") or "done") if is_stale(s) else ("waiting" if status == "waiting" else
                                                                    "done" if status == "done" else "pending")
                stages[sid] = {"status": "stale", "was": was, "since": now}
            cu = st.get("catchup") or {}
            first = min([i for i in (STAGE_IDS.index(cu["from"]) if cu.get("from") in STAGE_IDS else None, idx)
                         if i is not None])
            st["catchup"] = {"from": STAGE_IDS[first], "reached": cu.get("reached") or reached,
                             "why": ((cu.get("why") or []) + ([why] if why else []))[-8:], "at": now}
            st["error"] = None
        self.update_state(f)

    def redo_from(self, stage: str, why: str = "") -> None:
        """The author asked for this step to be done again: it is done anew, with nothing of its earlier result kept,
        and every later step is brought up to date from the new result (update_from): only what the new result
        changes is done again; the rest stays, with the author's decisions on it."""
        idx = STAGE_IDS.index(stage)
        if not any((self.state["stages"].get(sid) or {}).get("status") not in (None, "pending")
                   for sid in STAGE_IDS[idx + 1:]):
            self.reset_from(stage)                 # nothing after it was started: there is nothing to bring up to date
            return
        self.remove(*ARTIFACTS.get(stage, []))
        self.update_from(stage, why or "you asked for the step to be done again")

        gate = bool(STAGES[idx].get("gate"))

        def f(st):
            st["catchup"]["anew"] = stage
            # a stop done again is shown to the author again; a step done again runs as new
            st["stages"][stage] = {"status": "pending", "reset": time.time(), **({"was": "waiting"} if gate else {})}
        self.update_state(f)
        if stage == "reading":
            lib = self.load("library")
            if lib:
                for p in lib:
                    p["read"], p["notes"], p["fulltext"], p["verdict"] = False, None, "", ""
                self.save("library", lib)

    def reset_from(self, stage: str) -> None:
        """Forget this stage and everything after it."""
        idx = STAGE_IDS.index(stage)
        for sid in STAGE_IDS[idx:]:
            self.remove(*ARTIFACTS.get(sid, []))

        now = time.time()

        def f(st):
            for sid in STAGE_IDS[idx:]:
                st["stages"][sid] = {"status": "pending", "reset": now}     # the calls of the earlier run stay in the log
            st["error"] = None
            st.pop("paper_title", None)
            st.pop("catchup", None)               # done again from here: nothing is brought up to date, all is new
            st.pop("incremental", None)
        self.update_state(f)
        if idx <= STAGE_IDS.index("reading"):
            lib = self.load("library")
            if lib and idx > STAGE_IDS.index("search1"):
                for p in lib:
                    p["read"], p["notes"], p["fulltext"], p["verdict"] = False, None, "", ""
                self.save("library", lib)
        out = self.dir / "out"
        if out.exists() and idx <= STAGE_IDS.index("assemble"):
            shutil.rmtree(out, ignore_errors=True)


def create(meta: dict, pages: list[dict], pid: str | None = None) -> Project:
    pid = pid or (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    proj = Project(pid)
    proj.dir.mkdir(parents=True, exist_ok=True)
    proj.save("state", {
        "id": pid, "created": time.time(), "updated": time.time(), "meta": meta, "pages": pages,
        "stages": {sid: {"status": "pending"} for sid in STAGE_IDS}, "running": False, "error": None, "layout": LAYOUT,
    })
    return proj


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def get(pid: str) -> Project | None:
    if not pid or "/" in pid or "\\" in pid or ".." in pid:
        return None
    p = Project(pid)
    if not (p.dir / "state.json").exists():
        return None
    st = p.state
    if "stages" not in st:
        return None
    if st.get("layout") != LAYOUT:
        migrate(p)
    return p


def migrate(p: Project) -> None:
    """Bring a project saved by an earlier version to the current list of steps.

    Everything already finished in a step that still exists is kept: the reading, the confirmed notation, the
    transcript and the author's edits. Steps that are new are added as not yet done, and any step that comes
    after a step not yet done is done again, because it would have been built without it."""
    with p._lock:
        st = p.state
        if not st or st.get("layout") == LAYOUT:
            return
        before = st.get("layout") or 0

        def f(s):
            # the first draft used to be written section by section from the pages, in the steps "intro" and
            # "structure": a draft made that way is kept, and the steps that now make it count as done; a paper not
            # that far yet goes back to its statements, which the old plan step did not have
            old_draft = s["stages"].get("structure", {}).get("status") == "done"
            if "structure" in s["stages"] and not old_draft and s["stages"].get("scope", {}).get("status") == "done":
                s["stages"]["scope"] = {"status": "pending", "reset": time.time()}
            if old_draft:
                for sid in ("outline", "proofs", "proofcheck", "draft", "draftcheck"):
                    s["stages"].setdefault(sid, {"status": "done", "finished": time.time(), "migrated": True})
            for sid in list(s["stages"]):
                if sid not in STAGE_IDS:
                    s["stages"].pop(sid)                  # a step that no longer exists
            new = [sid for sid in STAGE_IDS if sid not in s["stages"]]
            for sid in STAGE_IDS:
                s["stages"].setdefault(sid, {"status": "pending"})
            for sid in new:                               # a new stop for the author, placed before work already done:
                i = STAGE_IDS.index(sid)                  # the author went past that point, so it counts as answered
                gate = next(x for x in STAGES if x["id"] == sid).get("gate")
                if gate and all(s["stages"][x].get("status") == "done" for x in STAGE_IDS[:i] if x not in new) \
                        and any(s["stages"][x].get("status") == "done" for x in STAGE_IDS[i + 1:]):
                    s["stages"][sid] = {"status": "done", "finished": time.time(), "migrated": True}
            s["layout"] = LAYOUT
        st = p.update_state(f)
        if before < 9:
            _figures_v9(p)
        first = next((i for i, sid in enumerate(STAGE_IDS) if st["stages"][sid].get("status") != "done"), None)
        if first is not None and before < 9 and STAGE_IDS[first] == "figdesign" \
                and st["stages"]["figdesign"].get("status") == "pending":
            # the one new step, after the structure: everything made is kept, the figures for print are drawn, and
            # the steps after it are brought up to date (nothing in them changes unless a figure does)
            p.update_from("figdesign", "a new step: the figures the paper prints are drawn for print")
        elif first is not None and first + 1 < len(STAGE_IDS) \
                and any(st["stages"][sid].get("status") not in (None, "pending") for sid in STAGE_IDS[first + 1:]):
            out = p.dir / "out"
            if out.exists() and any(out.iterdir()):       # the paper built by the earlier version is kept aside
                keep = p.dir / "out_before_update"
                shutil.rmtree(keep, ignore_errors=True)
                try:
                    out.rename(keep)
                except OSError:
                    pass
            p.reset_from(STAGE_IDS[first + 1])
        # (before version 6 every drawing was copied, as it is again now: nothing of those is done again)
        if before < 7:
            _wake_waits(p)
            _repair_readings(p)


def _figures_v9(p: Project) -> None:
    """Figures saved before version 9 mixed two answers in one: whether the copy of a drawing was right, and whether
    the paper prints it. They are now two: the copy is checked in step 9, and what the paper prints is decided at
    the structure. A drawing the author kept out of the paper as an explanation keeps that decision; one the author
    chose to print keeps it too; nothing is redrawn."""
    data = p.load("figures") or {}
    items = data.get("items") or []
    if not items:
        return
    plan = p.load("figure_plan") or {"items": {}}
    for f in items:
        choice = f.get("choice") or ""
        judged = f.get("judged") or {}
        if choice == "explain":
            f["choice"] = ("tikz" if f.get("tikz_ok") else "orig") if f.get("drawn") else ""
            plan["items"].setdefault(f["id"], {"use": "explanation", "sure": True, "why": judged.get("why", ""),
                                               "illustrates": judged.get("illustrates", ""), "needed": "", "with": "",
                                               "in_proof": False, "caption": f.get("caption", ""), "auto": bool(f.get("auto")),
                                               "by": judged.get("by", ""), "legacy": True})
        elif choice in ("tikz", "orig"):
            plan["items"].setdefault(f["id"], {"use": "paper", "sure": True, "why": judged.get("why", ""), "illustrates": "",
                                               "needed": judged.get("needed", ""), "with": "", "in_proof": False,
                                               "caption": f.get("caption", ""), "auto": False, "by": "", "legacy": True})
    p.save("figures", data)
    stages = p.state.get("stages", {})
    if plan["items"] and stages.get("outline", {}).get("status") in ("done", "waiting"):
        p.save("figure_plan", plan)
    if stages.get("review", {}).get("status") != "done" and stages.get("figures", {}).get("status") == "done" \
            and any(not f.get("drawn") and f.get("choice") != "none" and not f.get("dup_of") for f in items):
        # the transcript is not approved yet: the drawings that were not copied (judged then to need no copy) are
        # copied now, so that step 9 can show every drawing as it was drawn
        def reopen(st):
            st["stages"]["figures"] = {"status": "pending", "reset": time.time()}
            if st["stages"].get("review", {}).get("status") == "waiting":
                st["stages"]["review"] = {"status": "pending", "reset": time.time()}
        p.update_state(reopen)


def _wake_waits(p: Project) -> None:
    """A step that an earlier version paused while waiting for the strongest model may now go on at once with a model
    almost as good, on either key: it is tried again right away (the app restarts it a few seconds after it opens), and
    the earlier note that a weaker model could be allowed is dropped."""
    import re
    stale = re.compile(r"\s*A weaker model is not used for this job unless you allow it\.")

    def f(s):
        for x in s["stages"].values():
            if x.get("status") == "paused" and x.get("reason") == "limits":
                x.update(resume_at=time.time(), auto_tries=0, weaker="", error=stale.sub("", x.get("error") or ""))
        e = s.get("error")
        if isinstance(e, dict) and e.get("kind") == "limits":
            e.update(resume_at=time.time(), message=stale.sub("", e.get("message") or ""))
    p.update_state(f)


def _repair_readings(p: Project) -> None:
    """Readings saved by earlier versions could keep the end of the model's answer (its doubts and changes, or a second
    start of the page), HTML markup such as <u>Def</u>, or a command repeated endlessly. While the transcript is not yet
    approved, the first two are cut away and a page that looped is read again (that page only). A paper whose reading
    steps are open again carries on by itself when it is next opened. A name made from a file name loses its
    underscores."""
    import re
    from .texutil import answer_transcript, clean_transcript, looping, same_commands
    st = p.state
    name = (st.get("meta") or {}).get("name") or ""
    if "_" in name and " " not in name.strip():
        p.update_state(lambda s: s["meta"].update(name=re.sub(r"_+", " ", name).strip()))
        st = p.state
    stages = st["stages"]
    if stages.get("review", {}).get("status") == "done" or (st.get("meta") or {}).get("imported"):
        return
    tr = p.load("transcript")
    reread: list[int] = []
    if tr and tr.get("pages"):
        changed = False
        for n, rec in list(tr["pages"].items()):
            old = rec.get("text") or ""
            if rec.get("model") == "typed source":
                continue
            new = old
            if re.search(r"</?(?:transcript|uncertain|changes)\b|</?(?:u|b|i|br|em|strong|mark|span)\b[^<>]{0,80}>", old, re.I):
                new = clean_transcript(answer_transcript(old))
            if same_commands(new) != new or re.search(r"\\(?:begin\{(?:itemize|enumerate|description)\}|rule\{)", new):
                new = clean_transcript(new)       # an invented command, or a list or rule of a LaTeX document
            if looping(new):
                del tr["pages"][n]
                reread.append(int(n))
                changed = True
            elif new != old:
                rec["text"] = new
                changed = True
        if changed:
            p.save("transcript", tr)

    # the Figures step under way began with judging the drawings; the calls before that belong to an earlier run
    judged = [c.get("t") or 0 for c in p.calls(20000)
              if c.get("stage") == "figures" and str(c.get("label", "")).startswith("Decide which drawings")]

    def f(s):
        if reread:
            for sid in ("transcribe", "figures", "review"):
                s["stages"][sid] = {"status": "pending", "reset": time.time()}
        g = s["stages"]
        fig = g.get("figures", {})
        if fig.get("status") in ("pending", "paused", "error") and not fig.get("reset"):
            fig["reset"] = (min(judged) - 1) if judged and fig.get("status") != "pending" else time.time()   # earlier calls are not this run's
        if tr and tr.get("pages") is not None and fig.get("status") == "pending" \
                and g.get("transcribe", {}).get("status") in ("done", "pending") and g.get("review", {}).get("status") != "done":
            s["restart"] = True
    p.update_state(f)


def list_projects() -> list[dict]:
    out = []
    if not config.PROJECTS_DIR.exists():
        return out
    for d in config.PROJECTS_DIR.iterdir():
        sp = d / "state.json"
        if not sp.exists():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(st, dict) or "stages" not in st or "id" not in st:
            continue                                        # a half-deleted project folder
        if st.get("layout") != LAYOUT:
            proj = get(st["id"])
            st = proj.state if proj else st
        hidden = hidden_stages(st, d)
        done = sum(1 for sid, s in st.get("stages", {}).items() if s.get("status") == "done" and sid not in hidden)
        waiting = next((sid for sid in STAGE_IDS if st["stages"].get(sid, {}).get("status") == "waiting"), None)
        paused = next((s.get("resume_at") for s in st["stages"].values() if s.get("status") == "paused"), None)
        now = next((sid for sid in STAGE_IDS if st["stages"].get(sid, {}).get("status") != "done"), None)
        meta = st.get("meta", {})
        first = next((pg.get("n") for pg in st.get("pages", []) if isinstance(pg, dict) and not pg.get("skip")), None)
        out.append({"stages": [st["stages"].get(sid, {}).get("status", "pending") for sid in STAGE_IDS],
                    "imported": bool(meta.get("imported")), "test": (meta.get("test") or {}).get("before") or None,
                    "authors": [a.get("name") for a in meta.get("authors") or [] if isinstance(a, dict) and a.get("name")][:6],
                    "first_page": first, "record": (d / "proof" / "record.json").exists(),
                    "current": now, "current_name": next((x["name"] for x in STAGES if x["id"] == now), "") if now not in hidden
                    else next((x["name"] for x in STAGES if x["id"] not in hidden and st["stages"].get(x["id"], {}).get("status") != "done"), ""),
                    "completed": st["stages"].get("finish", {}).get("status") == "done",
                    "stopped": next((s.get("error") for s in st["stages"].values() if s.get("status") == "error"), None),
                    "paused": paused, "id": st["id"], "name": st.get("meta", {}).get("name") or "Untitled notes",
                    "domains": st.get("meta", {}).get("domains", []), "pages": len(st.get("pages", [])),
                    "created": st.get("created"), "updated": st.get("updated"), "done": done,
                    "total": len(STAGE_IDS) - len(hidden), "hidden": hidden, "waiting": waiting, "error": bool(st.get("error")),
                    "title": st.get("meta", {}).get("title") or st.get("paper_title") or ""})
    out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
    return out


def delete(pid: str) -> bool:
    p = get(pid)
    if not p:
        return False
    shutil.rmtree(p.dir, ignore_errors=True)
    return True
