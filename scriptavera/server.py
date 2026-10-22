# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Local web server: the interface and its JSON API. Listens on 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import json
import re
import os
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, config, domains, final, ingest, journals, literature, paper as paperlib, pipeline, revision, store, writing
from . import arxivpack, assist, citecheck, costs, extras, memory, phone, provenance, reviews, sources, typed, workspace
from .llm import PROVIDERS, ROLES, LLMError, router

DOWNLOADS = {
    "main.tex": "text/x-tex", "main_standard_class.tex": "text/x-tex", "references.bib": "text/plain",
    "paper.zip": "application/zip", "paper.html": "text/html", "paper.pdf": "application/pdf",
    "summary.txt": "text/plain; charset=utf-8", "slides.tex": "text/x-tex", "slides.pdf": "application/pdf",
    "poster.tex": "text/x-tex", "poster.pdf": "application/pdf",
    "arxiv-submission.zip": "application/zip", "arxiv-metadata.txt": "text/plain; charset=utf-8",
}


def ok(data=None, **kw) -> JSONResponse:
    return JSONResponse({"ok": True, **(data or {}), **kw})


def fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _project(request: Request) -> store.Project | None:
    return store.get(request.path_params.get("pid", ""))


async def _body(request: Request) -> dict:
    """The JSON object sent with a request; an empty or malformed body is an empty object."""
    try:
        data = json.loads(await request.body() or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def orcid_id(text: str) -> str | None:
    """An ORCID iD written as 0000-0002-1825-0097, from any of its usual forms (with or without the
    https://orcid.org/ prefix); None when the digits do not pass its check digit (ISO 7064 MOD 11-2)."""
    d = re.sub(r"[^0-9Xx]", "", re.sub(r"(?i)^\s*(?:https?://)?(?:www\.)?orcid\.org/", "", str(text or "")))
    if len(d) != 16 or not d[:15].isdigit():
        return None
    total = 0
    for ch in d[:15]:
        total = (total + int(ch)) * 2
    r = (12 - total % 11) % 11
    if d[15].upper() != ("X" if r == 10 else str(r)):
        return None
    d = d.upper()
    return "-".join(d[i:i + 4] for i in range(0, 16, 4))


def _people(items) -> list[dict]:
    out = []
    for a in items if isinstance(items, list) else []:
        if isinstance(a, dict) and str(a.get("name") or "").strip():
            person = {"name": str(a["name"]).strip()[:120], "affiliation": str(a.get("affiliation") or "").strip()[:300],
                      "email": str(a.get("email") or "").strip()[:120]}
            oid = orcid_id(a.get("orcid") or "")
            if oid:
                person["orcid"] = oid
            out.append(person)
    return out[:12]


def declaration_text(authors: list[dict], made_by: dict) -> str:
    """The statement signed in the record of the notes, like the one arXiv asks of every submitter."""
    name = made_by.get("name") or "the person making this record"
    if made_by.get("is_author"):
        if len(authors) <= 1:
            return (f"I, {name}, am the author of these notes. The details in this record are correct to the best of my "
                    "knowledge, and the files are the notes as I uploaded them.")
        return (f"I, {name}, am an author of these notes. The other authors listed agree to this record. The details in "
                "this record are correct to the best of my knowledge, and the files are the notes as I uploaded them.")
    return (f"I, {name}, am not an author of these notes. I record them for the authors listed, with their consent. The "
            "details in this record are correct to the best of my knowledge, and the files are the notes as I uploaded them.")


def _declaration(meta: dict, authors: list[dict]) -> tuple[dict | None, str]:
    """The declaration sent with new notes: {agreed, author (index into the authors, or null), name}. Returns
    (who made it, error)."""
    d = meta.get("declaration") if isinstance(meta.get("declaration"), dict) else {}
    if d.get("agreed") is not True:
        return None, ("Tick the declaration: the record of your notes says, as arXiv does for every submission, who "
                      "wrote them and that the details are correct.")
    idx = d.get("author")
    if isinstance(idx, int) and 0 <= idx < len(authors):
        return {"name": authors[idx]["name"], "is_author": True}, ""
    name = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()[:120]
    if not name:
        return None, "Say who is making the record: choose your name among the authors, or give your name."
    return {"name": name, "is_author": False}, ""


def _record_about(meta: dict, made_by: dict | None) -> dict:
    """What the record of the notes says about them, taken from what the author entered, as arXiv takes it."""
    subjects = []
    for d in meta.get("domains") or []:
        info = domains.get(d)
        if info:
            subjects.append({"code": d, "name": info["name"], "msc": list(info.get("msc") or [])})
    authors = meta.get("authors") or []
    about = {"title": meta.get("title") or meta.get("name") or "", "authors": authors,
             "author": {k: (authors[0] if authors else {}).get(k, "") for k in ("name", "email", "affiliation")},
             "description": meta.get("problem") or "", "keywords": list(meta.get("keywords") or []), "subjects": subjects}
    if made_by:
        about["recorded_by"] = made_by
        about["declaration"] = declaration_text(authors, made_by)
    return about


# ------------------------------------------------------------------ meta

def _meta_payload() -> dict:
    env = {}
    for k, info in config.ENV_KEYS.items():
        v = config.get_env(k)
        env[k] = {"label": info["label"], "set": bool(v), "shown": config.mask(v) if info["secret"] else v}
    providers = []
    for pid, pr in PROVIDERS.items():
        st = router.status.get(pid, {})
        providers.append({"id": pid, "label": pr["label"], "env": pr["env"], "signup": pr["signup"], "why": pr["why"],
                          "configured": bool(router._keys(pid)), "ok": st.get("ok", False),
                          "count": st.get("count", 0), "message": st.get("message", ""), "keys": st.get("keys", [])})
    return {
        "version": __version__, "mock": config.mock_mode(), "domains": domains.DOMAINS,
        "phases": store.PHASES, "stages": store.STAGES,
        "roles": ROLES, "providers": providers, "plan": router.plan() if router.models else {},
        "models": [m.public() for m in router.models], "settings": config.load_settings(), "env": env, "memory": memory.stats(),
        "engine": paperlib.find_engine(), "macros": paperlib.MACROS, "has_vision": any(m.vision and not m.dead for m in router.models),
        "discovered": bool(router.discovered_at),
        "templates": journals.TEMPLATES,
        "families": [{"id": f["id"], "name": f["name"], "house": f["house"], "template": f["template"]}
                     for f in journals.FAMILIES if f["id"] != "generic"],
        "statements": [{"id": k, "name": name} for k, name in paperlib.STATEMENTS],
        "identity": {k: v for k, v in _identity_public().items() if k in ("fingerprint", "error")},
    }


async def api_meta(request: Request):
    if not router.discovered_at:
        await run_in_threadpool(router.discover)
    return ok(_meta_payload())


# What "the source" is: the program, its tests and its terms. Never the author's papers, keys or settings: only
# files of known kinds, inside the program's own folders, no hidden files and no links that lead elsewhere.
SOURCE_FILES = ("app.py", "run_app.bat", "requirements.txt", "README.md", "LICENSE", "LICENSING.md", "CLA.md",
                "CONTRIBUTING.md", "TRADEMARKS.md", "THIRD_PARTY_NOTICES.md", "CITATION.cff", "funding.json",
                ".gitignore")
SOURCE_DIRS = ("scriptavera", "tests", "docs", ".github")
SOURCE_KINDS = {".py", ".js", ".mjs", ".css", ".html", ".svg", ".png", ".woff2", ".woff", ".ttf", ".md", ".txt", ".bat",
                ".cff", ".yml", ".yaml", ""}


def source_files() -> list:
    root = config.ROOT.resolve()
    out = []

    def fine(f) -> bool:
        if f.is_symlink() or not f.is_file():
            return False
        try:
            f.resolve().relative_to(root)
        except ValueError:
            return False
        return True
    for name in SOURCE_FILES:
        f = config.ROOT / name
        if fine(f):
            out.append(f)
    for d in SOURCE_DIRS:
        base = config.ROOT / d
        if not base.is_dir() or base.is_symlink():
            continue
        for f in sorted(base.rglob("*")):
            rel = f.relative_to(config.ROOT)
            parts = rel.parts[1:] if d == ".github" else rel.parts
            if any(x.startswith(".") for x in parts) or "__pycache__" in rel.parts:
                continue                                   # hidden files: editor backups, local secrets
            if any((config.ROOT.joinpath(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
                continue                                   # a link could lead anywhere, the keys included
            if f.suffix.lower() not in SOURCE_KINDS or (f.suffix == "" and f.name != "LICENSE"):
                continue                                   # only files of known kinds
            if fine(f):
                out.append(f)
    return out


def source_zip() -> bytes:
    """The source of the running copy, as the GNU AGPL (section 13) asks for a program used over a network."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in source_files():
            z.write(f, f"scriptavera-{__version__}/{f.relative_to(config.ROOT).as_posix()}")
    return buf.getvalue()


async def api_source(request: Request):
    if config.get_env("SCRIPTAVERA_SOURCE_LINK") == "0":
        return Response(status_code=404)
    data = await run_in_threadpool(source_zip)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="scriptavera-{__version__}-source.zip"'})


async def api_settings(request: Request):
    body = await _body(request)
    env = {k: str(v).strip() for k, v in (body.get("env") or {}).items() if k in config.ENV_KEYS}
    if env:
        config.save_env(env)
    if body.get("settings"):
        config.save_settings(body["settings"])
    await run_in_threadpool(router.discover, True)
    return ok(_meta_payload())


async def api_test_mode(request: Request):
    """Test mode on or off, with its cutoff date and the papers to exclude. Quick: no models are asked."""
    import datetime
    body = await _body(request)
    on = bool(body.get("on"))
    before = str(body.get("before") or "").strip()[:10]
    if on:
        try:
            datetime.date.fromisoformat(before)
        except ValueError:
            return fail("Give the cutoff date: papers that appeared on or after it are never searched for (for a test "
                        "paper, the day before it first appeared on arXiv).")
    exclude = [x.strip()[:300] for x in str(body.get("exclude") or "").splitlines() if x.strip()][:50]
    config.save_settings({"test_mode": {"on": on, "before": before, "exclude": exclude}})
    return ok(_meta_payload())


async def api_test(request: Request):
    await run_in_threadpool(router.discover, True)
    return ok(_meta_payload())


async def api_journals(request: Request):
    """Look a journal up by name in the scholarly databases."""
    q = (request.query_params.get("q") or "").strip()[:160]
    found, message = await run_in_threadpool(journals.search, q)
    return ok(journals=found, message=message)


# -------------------------------------------------------------- projects

async def api_projects(request: Request):
    out = store.list_projects()
    for x in out:
        x["running"] = pipeline.is_running(x["id"])     # papers carry on in the background while you work on another
    return ok(projects=out)


async def api_name(request: Request):
    """Rename a paper (its name in the list of papers; the title of the paper is chosen at the end)."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    name = re.sub(r"\s+", " ", str((await _body(request)).get("name") or "")).strip()[:120]
    if not name:
        return fail("Give the paper a name.")
    p.update_state(lambda st: st["meta"].update(name=name))
    return ok()


async def api_create(request: Request):
    form = await request.form()
    try:
        meta = json.loads(form.get("meta") or "{}")
    except ValueError:
        return fail("Invalid project details.")
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            data = await f.read()
            if data:
                files.append((f.filename or "upload", data))
    if not files:
        return fail("Upload your notes: a scanned PDF or photos of the pages, or typed notes (a .tex file, an Overleaf "
                    "project as a .zip, Markdown or text).")
    doms = [d for d in meta.get("domains", []) if domains.get(d)]
    if not doms:
        return fail("Choose the domain you are working in. It limits the literature search.")
    problem = re.sub(r"\s+", " ", str(meta.get("problem") or "")).strip()
    kw = meta.get("keywords") or []
    keywords = [k.strip() for k in (kw if isinstance(kw, list) else str(kw).split(",")) if str(k).strip()]
    if len(problem) < 20:
        return fail("Describe the problem you are working on in a sentence or two. It is what the literature search and the reading start from.")
    if len(keywords) < 2:
        return fail("Give at least two keywords, separated by commas.")
    for a in meta.get("authors") or []:
        if isinstance(a, dict) and str(a.get("name") or "").strip() and str(a.get("orcid") or "").strip() \
                and not orcid_id(a["orcid"]):
            return fail(f"The ORCID iD given for {str(a['name']).strip()[:80]} is not valid: check its 16 digits "
                        "(it looks like 0000-0002-1825-0097).")
    authors = _people(meta.get("authors"))
    if not authors:
        return fail("Name at least one author. The record of your notes says who wrote them, as arXiv does for every "
                    "submission.")
    made_by, why = _declaration(meta, authors)
    if not made_by:
        return fail(why)
    clean = {
        "name": (meta.get("name") or files[0][0]).strip()[:120],
        "domains": doms[:3], "subarea": (meta.get("subarea") or "").strip()[:300],
        "hints": (meta.get("hints") or "").strip()[:1500], "title": (meta.get("title") or "").strip()[:300],
        "authors": authors,
        "known_refs": [r.strip() for r in meta.get("known_refs", []) if r.strip()][:15],
        "problem": problem[:3000], "keywords": keywords[:20],
        "key_papers": [r.strip() for r in meta.get("key_papers", []) if str(r).strip()][:3],
        "files": [n for n, _ in files],
        "journal": journals.clean_journal(meta.get("journal")), "statements": {},
        "declaration": {**made_by, "text": declaration_text(authors, made_by), "at": time.time()},
    }
    pid = store.new_id()
    folder = config.PROJECTS_DIR / pid
    try:                    # before anything else is done with the notes: keep them, fingerprint them, sign the record
        await run_in_threadpool(provenance.make_record, folder, files, _record_about(clean, made_by))
    except Exception as e:  # noqa: BLE001 - a missing library must not stop the paper; the author is told on the page
        (folder / "proof").mkdir(parents=True, exist_ok=True)
        (folder / "proof" / "error.txt").write_text(f"The record could not be made: {e}", encoding="utf-8")
    try:
        pages = await run_in_threadpool(_ingest_any, files, folder, 0)
    except Exception as e:  # noqa: BLE001
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        return fail(f"Could not read the upload: {e}")
    tinfo = json.loads((folder / "typed.json").read_text(encoding="utf-8")) if (folder / "typed.json").exists() else {}
    if tinfo.get("title") and not clean["title"]:
        clean["title"] = tinfo["title"][:300]
    if tinfo:
        clean["typed"] = tinfo.get("kind")
    tm = config.load_settings().get("test_mode") or {}
    if tm.get("on") and tm.get("before"):        # a test paper keeps its limits, whatever is switched later
        clean["test"] = {"before": tm["before"], "exclude": tm.get("exclude") or [], "at": time.time()}
    store.create(clean, pages, pid)
    if provenance.status(folder):
        provenance.work_later(folder)     # the timestamps are asked for in the background; only fingerprints leave
    return ok(id=pid)


def _bundle(p: store.Project) -> dict:
    st = p.state
    meta = st.get("meta", {})
    library = p.load("library", [])
    slim = [{k: v for k, v in x.items() if k not in ("lex", "openalex", "cats", "msc")} for x in library]
    for x in slim:
        x["abstract"] = (x.get("abstract") or "")[:900]
    body = p.load("body")
    draft = None
    if body and body.get("blocks"):
        blocks = body["blocks"]
        draft = {"html": final.draft_html(p), "audit": p.load("audit"),
                 "results": sum(1 for b in blocks if b["type"] == "env" and b.get("env") in revision.RESULT_ENVS),
                 "proofs": sum(1 for b in blocks if b["type"] == "proof"),
                 "sections": sum(1 for b in blocks if b["type"] == "section")}
    shown_tr, shown_flags, settled = pipeline.visible_doubts(p)    # doubts about language never reach the author
    assessment = p.load("assessment")
    return {
        "state": {**st, "running": pipeline.is_running(p.id)},
        "topic": p.load("topic"), "key_papers": p.load("key_papers"), "scouted": sorted(int(k) for k in (p.load("scout") or {})),
        "library": slim, "search_status": p.load("search_status"),
        "glossary": p.load("glossary"), "confirmed": p.load("confirmed"),
        "survey": {n: {"text": v.get("text", ""), "gist": v.get("gist", ""), "kind": v.get("kind", "")}
                   for n, v in (p.load("survey") or {}).items()},
        "brief": p.load("brief"), "transcript": shown_tr, "flags": shown_flags, "language_settled": settled,
        "figures": (p.load("figures") or {}).get("items"), "approved": bool(p.load("approved")),
        "doubt_answers": p.load("doubt_answers", []), "adding": pipeline.adding_now(p),
        "dossier": p.load("dossier"), "intro": p.load("intro"), "plan": p.load("plan"), "draft": draft,
        "assessment": assessment, "novelty": p.load("novelty"), "scope": p.load("scope"), "scope_default": writing.default_scope(assessment) if assessment else None,
        "finish": final.finish_info(p) if p.load("paper") else None,
        "revision": revision.state_json(p),
        "journal": meta.get("journal"), "profile": journals.profile(meta.get("journal")),
        "statements": meta.get("statements") or {},
        "paper": p.load("paper"), "calls": p.calls(300),
        "downloads": [n for n in DOWNLOADS if (p.dir / "out" / n).exists()],
        "kept": (p.dir / "out_before_update").exists(),
        "proof": _proof_info(p),
        "editor": {"exists": workspace.exists(p), "can": (p.dir / "out" / "main.tex").exists()},
        "katex_macros": (p.load("typed") or {}).get("katex", {}),
        "usage": costs.usage(p.calls(100000)),
        "estimate": costs.estimate(len([x for x in st.get("pages", []) if not x.get("skip")]), bool(meta.get("typed"))),
    }


def _proof_info(p: store.Project) -> dict:
    s = provenance.summary(p.dir)
    if s:
        return s
    err = p.dir / "proof" / "error.txt"
    return {"missing": True, "error": err.read_text(encoding="utf-8") if err.exists() else "",
            "originals": (p.dir / provenance.ORIGINALS).exists()}


async def api_project(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if request.method == "DELETE":
        pipeline.stop(p.id)
        pipeline.disarm(p.id)
        await run_in_threadpool(pipeline.wait_stopped, p.id, 8.0)
        store.delete(p.id)
        return ok()
    ready = any(m.vision and not m.dead for m in router.models)
    got = await run_in_threadpool(pipeline.recover_pages, p) if ready else []
    if ready and not got and await run_in_threadpool(pipeline.reset_leaked_draft, p):
        got = [0]                             # a draft with a model's reasoning in it: written again now
    if ready and not got and await run_in_threadpool(pipeline.reset_incomplete_draft, p):
        got = [0]                             # a draft that stopped part of the way through the notes: written again
    if got:
        pipeline.start(p)                     # a page with work on it that was never read: read it now
    if provenance.due(p.dir):
        provenance.work_later(p.dir)          # timestamps not yet obtained, or the Bitcoin confirmation not yet added
    paper = p.load("paper")
    if paper and paper.get("macros") and paper.get("macros_version") != 2 and not pipeline.is_running(p.id):
        # built before commands were checked with TeX itself: \cancel could have been declared as the word "cancel".
        # Built again once (no model is called), so the files and the preview are right.
        try:
            await run_in_threadpool(final.build_outputs, p, paper)
        except Exception:  # noqa: BLE001 - the paper still opens; the next change rebuilds it
            pass
    return ok(await run_in_threadpool(_bundle, p))


async def api_run(request: Request):
    """Start, continue, or redo from a step. `relax` lets the next best model do the step that is waiting for
    the strongest one."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    if pipeline.is_running(p.id):
        return fail("This project is already running.")
    if not router.discovered_at:
        await run_in_threadpool(router.discover)
    if not any(m.vision and not m.dead for m in router.models):
        return fail("No model that can read images is available. Add a Google Gemini key in Settings (free), "
                    "or a Groq key.")
    frm = body.get("from")
    if frm in store.STAGE_IDS:
        pipeline.disarm(p.id)
        p.reset_from(frm)
    else:                                   # continue: clear the stopped state, keep everything that is finished
        relax = bool(body.get("relax"))

        def f(st):
            st["error"] = None
            for s in st["stages"].values():
                if s.get("status") in ("error", "running", "paused"):
                    if relax and s.get("status") == "paused":
                        s["relax"] = True
                    s["status"], s["resume_at"], s["auto_tries"] = "pending", None, 0
        p.update_state(f)
    pipeline.start(p)
    return ok()


async def api_autostop(request: Request):
    """Cancel the automatic restart of a paused project."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    pipeline.cancel_auto(p)
    return ok()


async def api_stop(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    pipeline.stop(p.id)
    return ok()


async def api_events(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    try:
        last = int(request.query_params.get("last") or request.headers.get("last-event-id") or 0)
    except ValueError:
        last = 0

    async def gen():
        nonlocal last
        idle = 0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            evs = pipeline.events_after(p.id, last)
            if not evs and last and not pipeline.events_after(p.id, 0):
                last = 0                    # the server restarted; ids began again
            for e in evs:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            if evs:
                idle = 0
            else:
                idle += 1
                if idle % 50 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def api_page_image(request: Request):
    p = _project(request)
    if not p:
        return Response(status_code=404)
    try:
        n = int(request.path_params["n"])
    except ValueError:
        return Response(status_code=404)
    name = f"{'t' if request.query_params.get('thumb') else 'p'}{n:03d}.jpg"
    path = p.dir / "pages" / name
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


async def api_pages(request: Request):
    """Include or exclude a page (before the run, or to drop a stray page)."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    try:
        n = int(body.get("n"))
    except (TypeError, ValueError):
        return fail("Unknown page.")

    if not any(pg["n"] == n for pg in p.state["pages"]):
        return fail("Unknown page.")
    if not body.get("skip"):
        if pipeline.is_running(p.id):
            return fail("Wait for the current step to finish, or press Stop, then include the page.")
        what = pipeline.include_page(p, n, force=bool(body.get("force")))
        if what == "confirm":
            return ok(confirm=True, message="You already approved the transcript. Including this page means the steps "
                      "after your review are done again (the reading of every other page is kept).")
        if what == "now":
            pipeline.start(p)
        return ok(reading=what == "now")

    def f(st):
        for pg in st["pages"]:
            if pg["n"] == n:
                pg["skip"], pg["kind"] = True, "excluded by you"
    p.update_state(f)
    return ok()


# ----------------------------------------------------------------- gates

def _later_started(p: store.Project, sid: str) -> bool:
    """Has anything after this step been done or begun? Then answering the step again makes it stale."""
    idx = store.STAGE_IDS.index(sid)
    stages = p.state["stages"]
    return any(stages.get(s, {}).get("status") not in (None, "pending") for s in store.STAGE_IDS[idx + 1:])


async def api_confirm(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    if p.state["stages"]["confirm"].get("status") not in ("waiting", "done"):
        return fail("The project is not at the confirmation step.")
    try:
        pipeline.submit_confirm(p, await _body(request))
    except ValueError as e:
        return fail(str(e))
    if _later_started(p, "confirm"):
        pipeline.disarm(p.id)
        p.reset_from("reading")             # confirming again invalidates what was built on the old answers
    pipeline.start(p)
    return ok()


async def api_locate(request: Request):
    """Mark on the page image where a symbol was found."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    symbol = (body.get("symbol") or "").strip()
    try:
        page = int(body.get("page"))
    except (TypeError, ValueError):
        return fail("Unknown page.")
    if not symbol or not (p.dir / "pages" / f"p{page:03d}.jpg").exists():
        return fail("Unknown symbol or page.")
    try:
        res = await run_in_threadpool(pipeline.locate_symbol, p, symbol, page, (body.get("context") or "")[:600])
    except LLMError as e:
        return fail(str(e))
    return ok(res)


async def api_reglossary(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    try:
        await run_in_threadpool(pipeline.rebuild_glossary, p)
    except (ValueError, LLMError) as e:
        return fail(str(e))
    pipeline.emit(p.id, "state")
    return ok()


async def api_library(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("The paper list is in use by the running step. Try again when it finishes, or press Stop.")
    body = await _body(request)
    library = p.load("library", [])
    action = body.get("action")
    if action == "add":
        ident = (body.get("identifier") or "").strip()
        if not ident:
            return fail("Enter a DOI, an arXiv id or link, or a paper title.")
        try:
            got = await run_in_threadpool(_resolve_for, p, ident)
        except Exception as e:  # noqa: BLE001
            return fail(f"Lookup failed: {e}")
        if not got:
            return fail("No paper was found for that identifier. Try the DOI or the arXiv number.")
        got["score"] = got.get("score") or 10.0
        got["why"] = "Added by you"
        literature.add_papers(library, [got])
        p.save("library", library)
        return ok(title=got["title"])
    target = next((x for x in library if x.get("key") == body.get("key")), None)
    if not target:
        return fail("Paper not found.")
    if action == "state" and body.get("state") in ("selected", "excluded", "candidate"):
        target["state"] = body["state"]
        target["pinned"] = body["state"] == "selected"     # the author's choice outranks later re-ranking
    elif action == "pin":
        target["pinned"] = bool(body.get("pinned"))
        if target["pinned"]:
            target["state"] = "selected"
    else:
        return fail("Unknown action.")
    p.save("library", library)
    return ok()


async def api_transcript(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    try:
        n = int(request.path_params["n"])
    except ValueError:
        return fail("Unknown page.")
    pipeline.save_transcript_page(p, n, str(body.get("text") or ""))
    return ok()


async def api_doubt(request: Request):
    """The author says a doubtful reading is correct as read: it is not shown again."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    try:
        page = int(body.get("page") or 0)
    except (TypeError, ValueError):
        return fail("Unknown page.")
    pipeline.dismiss_doubt(p, page, str(body.get("snippet") or ""), str(body.get("alt") or ""),
                           how=str(body.get("how") or "kept"), value=str(body.get("value") or ""),
                           issue=str(body.get("issue") or ""))
    return ok()


async def api_figure_image(request: Request):
    """A figure as an image: the redrawn version, or the author's own drawing cut from the page."""
    p = _project(request)
    fid = request.path_params.get("fid", "")
    if not p or not fid.isalnum():
        return Response(status_code=404)
    kind = "tikz" if request.query_params.get("kind") == "tikz" else "orig"
    path = pipeline.figure_file(p, fid, kind)
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png" if kind == "tikz" else "image/jpeg", headers={"Cache-Control": "no-store"})


async def api_figure(request: Request):
    """The author's answer for one figure: which drawing to use, its caption, or a request to draw it again."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    fid = request.path_params.get("fid", "")
    drawing = p.state["stages"].get("figures", {}).get("status") == "running"
    if ("astext" in body or "redraw" in body or "print" in body) and drawing:
        return fail("A figure is being drawn right now. Try again in a minute; your other answers are saved.")
    try:
        if "astext" in body:
            got = await run_in_threadpool(pipeline.figure_as_text, p, fid, str(body.get("astext") or "")[:1200])
            return ok(figure=None, page=got["page"], text=got["text"])
        if "print" in body:                         # print a drawing the app took for an explanation
            rec = await run_in_threadpool(pipeline.print_figure, p, fid)
        elif "redraw" in body:
            rec = await run_in_threadpool(pipeline.redraw_figure, p, fid, str(body.get("redraw") or "")[:1200])
        else:
            choice = body.get("choice") if isinstance(body.get("choice"), str) else None
            caption = body.get("caption") if isinstance(body.get("caption"), str) else None
            rec = pipeline.set_figure(p, fid, choice=choice, caption=caption[:600] if caption is not None else None)
    except (ValueError, LLMError) as e:
        return fail(str(e))
    return ok(figure=rec)


async def api_review(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    if p.state["stages"]["review"].get("status") not in ("waiting", "done"):
        return fail("The project is not at the review step.")
    try:
        pipeline.submit_review(p, await _body(request))
    except ValueError as e:
        return fail(str(e))
    if _later_started(p, "review"):
        pipeline.disarm(p.id)
        p.reset_from("understand")          # a transcript approved again: the paper is written again from it
    pipeline.start(p)
    return ok()


async def api_topic(request: Request):
    """The stop after the first search: confirm the topic, or comment and search again."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    if p.state["stages"]["topiccheck"].get("status") not in ("waiting", "done"):
        return fail("The project is not at this step.")
    body = await _body(request)
    later = _later_started(p, "topiccheck")
    try:
        done = pipeline.submit_topic(p, body)
    except ValueError as e:
        return fail(str(e))
    if done == "confirm" and later and (body.get("comment") or "").strip():
        pipeline.disarm(p.id)
        p.reset_from("survey")              # a comment after the notes were read: they are read again with it
    pipeline.start(p)
    return ok(action=done)


async def api_scope(request: Request):
    """The author's plan for the paper, after the assessment of the notes."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    if p.state["stages"]["scope"].get("status") not in ("waiting", "done") or not p.load("assessment"):
        return fail("The project is not at this step.")
    later = _later_started(p, "scope")
    try:
        scope = writing.submit_scope(p, await _body(request))
    except ValueError as e:
        return fail(str(e))
    if later:
        pipeline.disarm(p.id)
        p.reset_from("intro")               # a new plan: the paper is written again by it (or, if not ready, not at all)
    if scope["kind"] == "not_ready":
        pipeline.emit(p.id, "state")
        return ok(scope=scope)
    pipeline.start(p)
    return ok(scope=scope)


async def api_finish(request: Request):
    """The last step: title, introduction and citations. `action` is 'titles' (more suggestions), 'cite' (rewrite the
    introduction for the ticked papers) or 'finish' (save the author's choices and build the files)."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish.")
    if not p.load("paper") or p.state["stages"]["finish"].get("status") not in ("waiting", "done"):
        return fail("The final version is not ready yet.")
    body = await _body(request)
    action = body.get("action") or "finish"
    try:
        if action == "titles":
            await run_in_threadpool(final.more_titles, p)
        elif action == "cite":
            keys = [str(k) for k in (body.get("keys") or []) if isinstance(k, str)]
            await run_in_threadpool(final.cite_intro, p, keys, str(body.get("instruction") or "")[:2000])
        elif action == "finish":
            await run_in_threadpool(final.finish, p, body)
        else:
            return fail("Unknown action.")
    except (ValueError, LLMError) as e:
        return fail(str(e))
    pipeline.emit(p.id, "state")
    return ok(finish=final.finish_info(p))


async def api_decisions(request: Request):
    """The author's decisions on every proposed change to the mathematics."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current step to finish, or press Stop.")
    if p.state["stages"]["decide"].get("status") not in ("waiting", "done"):
        return fail("The project is not at the decision step.")
    try:
        revision.submit_decisions(p, await _body(request))
    except ValueError as e:
        return fail(str(e))
    if _later_started(p, "decide"):
        pipeline.disarm(p.id)
        p.reset_from("assemble")            # decided again: the final version is made again
    pipeline.start(p)
    return ok()


# ------------------------------------------------------- the finished paper

async def api_paper(request: Request):
    """Author edits to the finished paper. Rebuilds every output; no model is called."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    paper = p.load("paper")
    if not paper:
        return fail("The paper has not been assembled yet.")
    if pipeline.is_running(p.id):
        return fail("Wait for the current run to finish.")
    body = await _body(request)
    for k in ("title", "abstract", "intro"):
        if k in body and isinstance(body[k], str):
            paper[k] = body[k].strip()
            paper[f"{k}_source"] = "author"
    if isinstance(body.get("title"), str) and paper["title"]:
        p.update_state(lambda st: st["meta"].update(title=paper["title"]))      # the author's title stays if the paper is written again
    if isinstance(body.get("keywords"), list):
        paper["keywords_full"] = paper["keywords"] = [str(x).strip() for x in body["keywords"] if str(x).strip()][:20]
    if isinstance(body.get("msc"), list):
        paper["msc"] = [str(x).strip() for x in body["msc"] if str(x).strip()][:6]
    if isinstance(body.get("authors"), list):
        paper["authors"] = _people(body["authors"])
        p.update_state(lambda st: st["meta"].update(authors=paper["authors"]))
    blk = body.get("block")
    if isinstance(blk, dict):
        target = next((b for b in paper["blocks"] if b.get("id") == blk.get("id")), None)
        if not target:
            return fail("Passage not found.")
        if blk.get("delete"):
            paper["blocks"].remove(target)
        else:
            if isinstance(blk.get("text"), str) and target["type"] not in ("section", "subsection"):
                target["text"] = blk["text"]
            if isinstance(blk.get("title"), str):
                target["title"] = blk["title"]
    if isinstance(body.get("settings"), dict):
        config.save_settings(body["settings"])
    paper = await run_in_threadpool(final.build_outputs, p, paper)
    pipeline.emit(p.id, "state")
    return ok(paper=paper)


async def api_revise(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Wait for the current run to finish.")
    body = await _body(request)
    if not (body.get("instruction") or "").strip():
        return fail("Say what should change.")
    try:
        paper = await run_in_threadpool(final.revise_block, p, str(body.get("target") or ""), str(body["instruction"]).strip()[:2000])
    except (ValueError, LLMError) as e:
        return fail(str(e))
    pipeline.emit(p.id, "state")
    return ok(paper=paper)


async def api_journal(request: Request):
    """Choose, change or clear the target journal, and store the author's own statements. If the paper
    exists it is refitted to the journal (abstract length, keywords, references, template) and rebuilt."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    has_paper = bool(p.load("paper"))
    if has_paper and pipeline.is_running(p.id):
        return fail("Wait for the current run to finish.")
    journal = body.get("journal") if "journal" in body else p.state.get("meta", {}).get("journal")
    statements = body.get("statements") if isinstance(body.get("statements"), dict) else None
    try:
        paper = await run_in_threadpool(final.set_journal, p, journal, statements)
    except LLMError as e:
        return fail(str(e))
    meta = p.state.get("meta", {})
    pipeline.emit(p.id, "state")
    return ok(journal=meta.get("journal"), profile=journals.profile(meta.get("journal")), paper=paper)


async def api_journal_guide(request: Request):
    """Read a journal's guide for authors (a web address or pasted text) into values for the author to check."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    source = str((await _body(request)).get("source") or "").strip()
    if not source:
        return fail("Give the web address of the journal's instructions for authors, or paste their text.")
    try:
        res = await run_in_threadpool(final.read_guide, p, source[:80000])
    except (RuntimeError, LLMError) as e:
        return fail(str(e))
    return ok(res)


async def api_extra(request: Request):
    """A plain-language summary, slides or a poster, made from the finished paper."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    try:
        return ok(await run_in_threadpool(extras.make, p, str(body.get("kind") or "")))
    except (ValueError, LLMError) as e:
        return fail(str(e))


async def api_review_pack(request: Request):
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    try:
        data = await run_in_threadpool(extras.review_pack, p)
    except ValueError as e:
        return fail(str(e))
    return Response(data, media_type="text/html", headers={"Content-Disposition": 'attachment; filename="review-pack.html"'})


async def api_phone(request: Request):
    """Photos from a phone on the same Wi-Fi: start or stop the upload page, list what arrived, hand over a photo."""
    if request.method == "GET":
        name = request.query_params.get("file")
        if name:
            data = phone.file_bytes(name)
            return Response(data, media_type="image/png" if name.endswith(".png") else "image/jpeg") if data else Response(status_code=404)
        return ok(phone.status())
    body = await _body(request)
    try:
        if body.get("action") == "start":
            return ok(await run_in_threadpool(phone.start))
        phone.stop()
        return ok(phone.status())
    except (RuntimeError, OSError) as e:
        return fail(str(e))


async def api_download(request: Request):
    p = _project(request)
    name = request.path_params.get("name", "")
    if not p or name not in DOWNLOADS:
        return Response(status_code=404)
    path = p.dir / "out" / name
    if not path.exists():
        return Response(status_code=404)
    inline = name in ("paper.html", "paper.pdf") and request.query_params.get("view")
    return FileResponse(path, media_type=DOWNLOADS[name],
                        filename=None if inline else name,
                        headers={"Cache-Control": "no-store"})


async def api_shutdown(request: Request):
    """Asked by a newer copy of the app that is starting: this one stops, so that only one runs. Everything is saved
    after every step, and a step that was running continues from where it was when the app is started again."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost"):
        return fail("Not allowed.", 403)
    for pid in [pid for pid in list(pipeline._runs) if pipeline.is_running(pid)]:
        pipeline.stop(pid)
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return ok({"version": __version__})


# --------------------------------------------------------------- the LaTeX editor

def _editor_project(request: Request) -> store.Project | None:
    p = _project(request)
    return p


async def editor_page(request: Request):
    return FileResponse(config.STATIC_DIR / "editor.html", headers={"Cache-Control": "no-store"})


async def api_editor(request: Request):
    """The project for the editor: its files and the last compile. POST makes it from the finished paper."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    try:
        if request.method == "POST":
            body = await _body(request)
            await run_in_threadpool(workspace.create, p, bool(body.get("reset")))
        info = await run_in_threadpool(workspace.info, p)
    except workspace.WorkspaceError as e:
        return fail(str(e))
    meta = p.state.get("meta", {})
    return ok(info, name=meta.get("name") or "Untitled notes", title=(p.load("paper") or {}).get("title", ""),
              finished=bool(p.load("paper")), version=__version__)


async def api_editor_file(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    try:
        if request.method == "GET":
            path = request.query_params.get("path", "")
            if request.query_params.get("raw"):
                data = await run_in_threadpool(workspace.read_bytes, p, path)
                import mimetypes
                kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
                return Response(data, media_type=kind, headers={"Cache-Control": "no-store"})
            return ok(await run_in_threadpool(workspace.read_text, p, path))
        body = await _body(request)
        res = await run_in_threadpool(workspace.write_text, p, str(body.get("path") or ""), str(body.get("text") or ""),
                                      body.get("base") or None)
        if not res.get("ok"):
            return JSONResponse({"ok": False, "conflict": True, "sha": res.get("sha"),
                                 "error": "This file changed since you opened it (another window, or an accepted AI "
                                          "change). Reload it to see the latest version."}, status_code=409)
        return ok(res)
    except workspace.WorkspaceError as e:
        return fail(str(e))


async def api_editor_files(request: Request):
    """New file, new folder, rename, delete."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    action, path, to = body.get("action"), str(body.get("path") or ""), str(body.get("to") or "")
    try:
        if action == "new_file":
            await run_in_threadpool(workspace.new_file, p, path)
        elif action == "new_folder":
            await run_in_threadpool(workspace.new_folder, p, path)
        elif action == "rename":
            await run_in_threadpool(workspace.rename, p, path, to)
        elif action == "delete":
            await run_in_threadpool(workspace.delete, p, path)
        else:
            return fail("Unknown action.")
    except workspace.WorkspaceError as e:
        return fail(str(e))
    return ok(tree=workspace.tree(p))


async def api_editor_upload(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    form = await request.form()
    folder = str(form.get("folder") or "").strip().strip("/")
    saved = []
    try:
        for f in form.getlist("files"):
            if not hasattr(f, "read"):
                continue
            data = await f.read()
            name = re.sub(r"[^A-Za-z0-9._\-]+", "-", (f.filename or "file").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip("-.")
            rel = f"{folder}/{name}" if folder else name
            await run_in_threadpool(workspace.write_bytes, p, rel, data)
            saved.append(rel)
    except workspace.WorkspaceError as e:
        return fail(str(e))
    return ok(saved=saved, tree=workspace.tree(p))


async def api_editor_compile(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    res = await run_in_threadpool(workspace.compile_project, p)
    return ok(build=workspace._public(res))


async def api_editor_pdf(request: Request):
    p = _editor_project(request)
    if not p:
        return Response(status_code=404)
    f = workspace.pdf_path(p)
    if not f.exists():
        return Response(status_code=404)
    name = re.sub(r"[^A-Za-z0-9._\-]+", "-", (p.state.get("meta", {}).get("name") or "paper"))[:60].strip("-") or "paper"
    return FileResponse(f, media_type="application/pdf", filename=f"{name}.pdf" if request.query_params.get("download") else None,
                        headers={"Cache-Control": "no-store"})


async def api_editor_log(request: Request):
    p = _editor_project(request)
    if not p:
        return Response(status_code=404)
    return Response(workspace.log_text(p) or "No log yet: compile first.", media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


def _completions(p: store.Project, items: list[dict], kinds: dict) -> dict:
    """What the editor offers while typing \\ref{, \\cite{, \\begin{ or \\input{."""
    labels = []
    for it in items:
        for lab in it.get("labels") or ([it["label"]] if it.get("label") else []):
            name = assist.display(it) if lab == it.get("label") else f"in {assist.display(it)}"
            labels.append({"label": lab, "what": name, "path": it["path"], "line": it["line"]})
    cites: list[dict] = []
    for rel in workspace.files(p):
        if workspace._kind(rel) != "text":
            continue
        text = (workspace.root(p) / rel).read_text(encoding="utf-8", errors="replace")
        if rel.endswith(".bib"):
            for m in re.finditer(r"@\w+\s*\{\s*([^,\s]+)\s*,", text):
                if not any(c["key"] == m.group(1) for c in cites):
                    t = re.search(r"title\s*=\s*[{\"]+([^}\"]+)", text[m.end():m.end() + 1500])
                    cites.append({"key": m.group(1), "what": (t.group(1) if t else "")[:90]})
        else:
            for m in re.finditer(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}([^\n]{0,160})", text):
                if not any(c["key"] == m.group(1) for c in cites):
                    t = re.search(r"\\emph\{([^{}]+)\}", m.group(2))
                    cites.append({"key": m.group(1), "what": (t.group(1) if t else m.group(2).strip())[:90]})
    envs = sorted(set(kinds) | {"proof", "equation", "equation*", "align", "align*", "gather", "gather*", "multline",
                                "itemize", "enumerate", "description", "figure", "table", "center", "tabular", "cases",
                                "pmatrix", "bmatrix", "abstract", "quote"})
    files = workspace.files(p)
    return {"labels": labels, "cites": cites, "envs": envs,
            "tex": [f[:-4] for f in files if f.endswith(".tex")], "images": [f for f in files if workspace._kind(f) == "binary"]}


async def api_editor_outline(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    o = await run_in_threadpool(workspace.outline, p)
    comp = await run_in_threadpool(_completions, p, o["items"], o["kinds"])
    return ok(items=o["items"], numbers_from=o["numbers_from"], complete=comp)


async def api_editor_sync(request: Request):
    """SyncTeX: from a line to the PDF (forward), or from a point of the PDF to the line (inverse)."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    s = await run_in_threadpool(workspace.sync, p)
    if not s:
        return fail("Compile first: the link between the source and the PDF comes from compiling.")
    try:
        if body.get("direction") == "inverse":
            got = s.inverse(int(body.get("page") or 1), float(body.get("x") or 0), float(body.get("y") or 0))
        else:
            got = s.forward(str(body.get("path") or ""), int(body.get("line") or 1))
    except (TypeError, ValueError):
        return fail("Bad position.")
    if not got:
        return fail("That place could not be found in the PDF. Compile again, then try once more.")
    return ok(got)


def _ingest_any(files: list[tuple[str, bytes]], folder, start: int) -> list[dict]:
    """Pages from any upload: typed notes (LaTeX, an Overleaf zip, Markdown, text) become pages whose source is their
    transcript; PDFs and images are rendered as before (a computer-made PDF keeps its text layer beside them)."""
    typed_files = [(n, d) for n, d in files if typed.is_typed(n, d)]
    other = [(n, d) for n, d in files if not typed.is_typed(n, d)]
    pages: list[dict] = []
    if typed_files:
        pages, info = typed.ingest_typed(typed_files, folder / "pages", start)
        f = folder / "typed.json"
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        if old:                                   # typed pages added later: their pages join the earlier ones
            info = {**old, "pages": {**old.get("pages", {}), **info["pages"]},
                    "preamble": list(dict.fromkeys(old.get("preamble", []) + info["preamble"])),
                    "katex": {**old.get("katex", {}), **info["katex"]}}
        f.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        typed.save_assets(typed_files, folder)
    if other:
        pages += ingest.ingest(other, folder / "pages", start + len(pages))
    return pages


def _resolve_for(p: store.Project, ident: str):
    """A paper added by hand, within the project's test-mode limits (if it was made in test mode)."""
    literature.set_limits(p.state.get("meta", {}).get("test"))
    try:
        return literature.resolve_identifier(ident)
    finally:
        literature.set_limits(None)


async def import_paper(request: Request):
    """A paper written elsewhere (a .tex file, or an Overleaf project as a .zip), opened in the editor for a
    referee's report or the proofs. Nothing is read by a model and no pipeline runs on it."""
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            data = await f.read()
            if data:
                files.append((f.filename or "upload", data))
    if not files:
        return fail("Choose the paper's .tex file, or its Overleaf project as a .zip (with its figures and .bib).")
    try:
        tree = typed.unpack(files)
    except Exception as e:  # noqa: BLE001
        return fail(f"Could not read the upload: {e}")
    main = typed.main_tex(tree)
    if not main:
        return fail("No .tex file with \\documentclass was found in the upload.")
    title = typed.split_document(typed.text_of(tree[main]))[2]
    pid = store.new_id()
    meta = {"name": (str(form.get("name") or "") or title or main)[:120], "title": title[:300], "imported": True,
            "domains": [], "subarea": "", "hints": "", "authors": [], "known_refs": [], "problem": "", "keywords": [],
            "key_papers": [], "files": sorted(tree)[:200], "journal": None, "statements": {}}
    p = store.create(meta, [], pid)
    try:
        await run_in_threadpool(workspace.import_tree, p, tree, main)
    except workspace.WorkspaceError as e:
        store.delete(pid)
        return fail(str(e))
    return ok(id=pid)


async def api_add_pages(request: Request):
    """More pages for notes that already have a paper: recorded like the first upload, then read on their own."""
    p = store.get(request.path_params["pid"])
    if not p:
        return fail("Project not found.", 404)
    if pipeline.is_running(p.id):
        return fail("Stop the run before adding pages.")
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            data = await f.read()
            if data:
                files.append((f.filename or "upload", data))
    if not files:
        return fail("Choose a PDF or page images to add.")
    meta = p.state["meta"]
    start = max([pg["n"] for pg in p.state.get("pages", [])] or [0])
    k = len(list((p.dir / "additions").iterdir())) + 1 if (p.dir / "additions").exists() else 1
    folder = p.dir / "additions" / f"{k:03d}"
    try:                    # the added pages get their own signed, timestamped record, following the record of the notes
        about = _record_about(meta, meta.get("declaration"))
        notes = provenance.status(p.dir)
        if notes:
            about["follows"] = {"kind": "notes", "fingerprint": notes["digest"]}
        await run_in_threadpool(provenance.make_record, folder, files, about, "notes", True,
                                f"Pages added to the notes (pages {start + 1} onwards)")
        provenance.work_later(folder)
    except Exception as e:  # noqa: BLE001 - the record must not stop the reading; the error is kept beside it
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "error.txt").write_text(f"The record could not be made: {e}", encoding="utf-8")
    try:
        new = await run_in_threadpool(_ingest_any, files, p.dir, start)
    except Exception as e:  # noqa: BLE001
        return fail(f"Could not read the upload: {e}")
    if not new:
        return fail("No pages were found in the upload.")
    return ok(await run_in_threadpool(pipeline.add_pages, p, new))


async def api_reply(request: Request):
    """The referee's report or the proof corrections: read, carried out point by point, and answered in a letter."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    if request.method == "GET":
        return ok({**reviews.load(p), "busy": reviews.is_busy(p)})
    try:
        if (request.headers.get("content-type") or "").startswith("multipart/"):
            form = await request.form()
            body = {k: form.get(k) for k in ("action", "kind", "text", "title")}
            f = form.get("file")
            if hasattr(f, "read"):
                data = await f.read()
                if data:
                    body["text"] = ((body.get("text") or "") + "\n\n" + reviews.report_text(f.filename or "", data)).strip()
        else:
            body = await _body(request)
        action = body.get("action")
        if action == "start":
            res = await run_in_threadpool(reviews.start, p, str(body.get("kind") or ""), str(body.get("text") or ""), str(body.get("title") or ""))
        elif action == "decide":
            res = await run_in_threadpool(reviews.decide, p, str(body.get("round") or ""), str(body.get("point") or ""),
                                          str(body.get("choice") or ""), str(body.get("note") or ""))
        elif action == "letter":
            res = await run_in_threadpool(reviews.letter, p, str(body.get("round") or ""))
        else:
            return fail("Unknown action.")
    except (assist.AssistError, LLMError) as e:
        return fail(str(e))
    return ok({"round": res, **reviews.load(p), "busy": reviews.is_busy(p)})


async def api_refcheck(request: Request):
    """Every reference of the paper looked up: exists, is the paper the entry says, not retracted."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if workspace.exists(p):
        bib = "\n".join(workspace.read_text(p, rel)["text"] for rel in workspace.files(p) if rel.lower().endswith(".bib"))
    else:
        f = p.dir / "out" / "references.bib"
        bib = f.read_text(encoding="utf-8") if f.exists() else ""
    if not bib.strip():
        return fail("The paper has no .bib file to check.")
    return ok(await run_in_threadpool(citecheck.check_bib, bib))


async def api_arxiv(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    try:
        return ok(await run_in_threadpool(arxivpack.prepare, p))
    except (ValueError, workspace.WorkspaceError) as e:
        return fail(str(e))


async def api_memory(request: Request):
    body = await _body(request)
    if body.get("action") == "clear":
        memory.clear()
    return ok(memory.stats())


async def api_editor_source(request: Request):
    """Where a line of the paper came from in the handwritten notes: the page, and the lines on it."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    try:
        doc = await run_in_threadpool(workspace.read_text, p, str(body.get("path") or ""))
        line = int(body.get("line") or 1)
    except (workspace.WorkspaceError, TypeError, ValueError) as e:
        return fail(str(e) or "Bad position.")
    try:
        res = await run_in_threadpool(sources.source_of, p, doc.get("text", ""), line, bool(body.get("point", True)))
    except LLMError as e:
        return fail(f"The page reader is not available: {e}")
    return ok(res)


async def api_editor_history(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    if request.method == "GET":
        vs = workspace.versions(p)
        return ok(versions=[{k: v for k, v in x.items() if k != "files"} | {"files": len(x["files"])} for x in reversed(vs)])
    body = await _body(request)
    try:
        if body.get("action") == "save":
            v = await run_in_threadpool(workspace.snapshot, p, str(body.get("label") or "Saved by you")[:120] or "Saved by you",
                                        "manual", True)
            return ok(version={k: x for k, x in (v or {}).items() if k != "files"})
        if body.get("action") == "diff":
            return ok(files=await run_in_threadpool(workspace.diff_version, p, int(body.get("id"))))
        if body.get("action") == "restore":
            await run_in_threadpool(workspace.restore, p, int(body.get("id")))
            return ok(tree=workspace.tree(p))
    except (workspace.WorkspaceError, TypeError, ValueError) as e:
        return fail(str(e) or "Unknown version.")
    return fail("Unknown action.")


async def api_editor_download(request: Request):
    p = _editor_project(request)
    if not p:
        return Response(status_code=404)
    data = await run_in_threadpool(workspace.zip_project, p)
    name = re.sub(r"[^A-Za-z0-9._\-]+", "-", (p.state.get("meta", {}).get("name") or "paper"))[:60].strip("-") or "paper"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}-latex.zip"'})


async def api_editor_assist(request: Request):
    """A command in plain words, answered with a proposal to review. Nothing is written here."""
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    where = body.get("where") if isinstance(body.get("where"), dict) else {}
    try:
        if isinstance(body.get("error"), dict):
            prop = await run_in_threadpool(assist.explain_error, p, body["error"])
        else:
            prop = await run_in_threadpool(assist.run, p, str(body.get("command") or "")[:4000], where, bool(body.get("relax")))
    except assist.AssistError as e:
        return fail(str(e))
    except workspace.WorkspaceError as e:
        return fail(str(e))
    return ok(proposal=prop)


async def api_editor_apply(request: Request):
    p = _editor_project(request)
    if not p:
        return fail("Project not found.", 404)
    body = await _body(request)
    keep = body.get("keep")
    try:
        res = await run_in_threadpool(assist.apply, p, str(body.get("id") or ""),
                                      [str(x) for x in keep] if isinstance(keep, list) else None)
    except (assist.AssistError, workspace.WorkspaceError) as e:
        return fail(str(e))
    return ok(res)


# ------------------------------------------------------------ proof of authorship

async def api_proof(request: Request):
    """GET: the record of the notes. POST: make it now (a paper started before records existed), ask for the
    timestamps again, or record the current version of the paper from the editor."""
    p = _project(request)
    if not p:
        return fail("Project not found.", 404)
    if request.method == "GET":
        return ok(proof=_proof_info(p), versions=_version_proofs(p), identity=_identity_public())
    body = await _body(request)
    action = body.get("action")
    meta = p.state.get("meta", {})
    try:
        if action == "record_now":
            if provenance.status(p.dir):
                return fail("These notes already have a record.")
            authors = meta.get("authors") or []
            if not authors:
                return fail("Name at least one author first (the paper's details): the record says who wrote the notes.")
            made_by, why = _declaration(body, authors)
            if not made_by:
                return fail(why)
            pages = sorted((p.dir / "pages").glob("p*.jpg"))
            if not pages:
                return fail("No page images were found for these notes.")
            await run_in_threadpool(provenance.make_record, p.dir, [(f.name, f.read_bytes()) for f in pages],
                                    _record_about(meta, made_by), "notes", False,
                                    "Recorded after the upload, from the page images Scriptavera kept: the version of "
                                    "Scriptavera that read these notes did not keep the original files.")
            p.update_state(lambda st: st["meta"].update(declaration={**made_by, "text": declaration_text(authors, made_by),
                                                                         "at": time.time()}))
            provenance.work_later(p.dir)
        elif action == "retry":
            if not provenance.status(p.dir):
                return fail("There is no record yet.")
            await run_in_threadpool(provenance.timestamp, p.dir)
            await run_in_threadpool(provenance.upgrade, p.dir)
        elif action == "version":
            if not workspace.exists(p):
                return fail("Open the editor first.")
            n = len(_version_proofs(p)) + 1
            folder = p.dir / "editor_proofs" / f"{n:03d}"
            files = [(rel, (workspace.root(p) / rel).read_bytes()) for rel in workspace.files(p)]
            about = _record_about(meta, None)
            about["title"] = (p.load("paper") or {}).get("title") or about["title"]
            notes = provenance.status(p.dir)
            if notes:                         # the chain: this version of the paper follows the record of the notes
                about["follows"] = {"kind": "notes", "fingerprint": notes["digest"]}
            await run_in_threadpool(provenance.make_record, folder, files, about, "paper", False,
                                    str(body.get("label") or "A version of the paper, from the LaTeX editor")[:300])
            provenance.work_later(folder)
        else:
            return fail("Unknown action.")
    except Exception as e:  # noqa: BLE001
        return fail(f"The record could not be made: {e}")
    return ok(proof=_proof_info(p), versions=_version_proofs(p))


def _version_proofs(p: store.Project) -> list[dict]:
    base = p.dir / "editor_proofs"
    out = []
    if base.exists():
        for d in sorted(base.iterdir()):
            s = provenance.summary(d) if d.is_dir() else None
            if s:
                if provenance.due(d):
                    provenance.work_later(d)
                out.append({"id": d.name, **s})
    return out


def _proof_folder(p: store.Project, rec: str):
    if rec in ("", "notes"):
        return p.dir
    if re.fullmatch(r"\d{3}", rec) and (p.dir / "editor_proofs" / rec).is_dir():
        return p.dir / "editor_proofs" / rec
    return None


async def api_proof_certificate(request: Request):
    p = _project(request)
    folder = _proof_folder(p, request.query_params.get("rec", "")) if p else None
    if not folder or not provenance.status(folder):
        return Response("No record.", status_code=404)
    return Response(provenance.certificate_html(folder), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


async def api_proof_bundle(request: Request):
    p = _project(request)
    rec = request.query_params.get("rec", "")
    folder = _proof_folder(p, rec) if p else None
    if not folder or not provenance.status(folder):
        return Response(status_code=404)
    data = await run_in_threadpool(provenance.bundle, folder, bool(request.query_params.get("files")))
    name = "notes" if rec in ("", "notes") else f"version-{rec}"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="proof-of-{name}.zip"'})


async def api_proof_verify(request: Request):
    """Check a record the way anyone would: from the proof zip and the files themselves."""
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            data = await f.read()
            if data:
                files.append((f.filename or "file", data))
    if not files:
        return fail("Add the proof (the zip) and the files it is about.")
    res = await run_in_threadpool(provenance.verify, files)
    return ok(check=res)            # nested: a record that does not hold is a finding, not a failed request


def _identity_public() -> dict:
    try:
        me = provenance.identity()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return {"fingerprint": me["fingerprint"], "created": me["created"], "pem": me["pem"]}


async def api_identity(request: Request):
    me = await run_in_threadpool(_identity_public)
    if request.query_params.get("download") and me.get("pem"):
        return Response(me["pem"], media_type="application/x-pem-file",
                        headers={"Content-Disposition": 'attachment; filename="scriptavera-public-key.pem"'})
    return ok(identity=me)


async def index(request: Request):
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


routes = [
    Route("/", index),
    Route("/api/meta", api_meta),
    Route("/api/source", api_source),
    Route("/api/shutdown", api_shutdown, methods=["POST"]),
    Route("/api/settings", api_settings, methods=["POST"]),
    Route("/api/settings/test", api_test, methods=["POST"]),
    Route("/api/test_mode", api_test_mode, methods=["POST"]),
    Route("/api/journals", api_journals),
    Route("/api/projects", api_projects, methods=["GET"]),
    Route("/api/projects", api_create, methods=["POST"]),
    Route("/api/import_paper", import_paper, methods=["POST"]),
    Route("/api/phone", api_phone, methods=["GET", "POST"]),
    Route("/api/projects/{pid}", api_project, methods=["GET", "DELETE"]),
    Route("/api/projects/{pid}/run", api_run, methods=["POST"]),
    Route("/api/projects/{pid}/stop", api_stop, methods=["POST"]),
    Route("/api/projects/{pid}/autostop", api_autostop, methods=["POST"]),
    Route("/api/projects/{pid}/events", api_events),
    Route("/api/projects/{pid}/page/{n}", api_page_image),
    Route("/api/projects/{pid}/pages", api_pages, methods=["POST"]),
    Route("/api/projects/{pid}/confirm", api_confirm, methods=["POST"]),
    Route("/api/projects/{pid}/library", api_library, methods=["POST"]),
    Route("/api/projects/{pid}/locate", api_locate, methods=["POST"]),
    Route("/api/projects/{pid}/add_pages", api_add_pages, methods=["POST"]),
    Route("/api/projects/{pid}/reglossary", api_reglossary, methods=["POST"]),
    Route("/api/projects/{pid}/transcript/{n}", api_transcript, methods=["PUT"]),
    Route("/api/projects/{pid}/doubt", api_doubt, methods=["POST"]),
    Route("/api/projects/{pid}/figure/{fid}", api_figure_image, methods=["GET"]),
    Route("/api/projects/{pid}/figure/{fid}", api_figure, methods=["POST"]),
    Route("/api/projects/{pid}/review", api_review, methods=["POST"]),
    Route("/api/projects/{pid}/decisions", api_decisions, methods=["POST"]),
    Route("/api/projects/{pid}/topic", api_topic, methods=["POST"]),
    Route("/api/projects/{pid}/scope", api_scope, methods=["POST"]),
    Route("/api/projects/{pid}/finish", api_finish, methods=["POST"]),
    Route("/api/projects/{pid}/paper", api_paper, methods=["PUT"]),
    Route("/api/projects/{pid}/revise", api_revise, methods=["POST"]),
    Route("/api/projects/{pid}/journal", api_journal, methods=["POST"]),
    Route("/api/projects/{pid}/name", api_name, methods=["POST"]),
    Route("/api/projects/{pid}/journal/guide", api_journal_guide, methods=["POST"]),
    Route("/api/projects/{pid}/download/{name}", api_download),
    Route("/api/projects/{pid}/extra", api_extra, methods=["POST"]),
    Route("/api/projects/{pid}/review_pack", api_review_pack),
    Route("/editor/{pid}", editor_page),
    Route("/api/projects/{pid}/editor", api_editor, methods=["GET", "POST"]),
    Route("/api/projects/{pid}/editor/file", api_editor_file, methods=["GET", "PUT"]),
    Route("/api/projects/{pid}/editor/files", api_editor_files, methods=["POST"]),
    Route("/api/projects/{pid}/editor/upload", api_editor_upload, methods=["POST"]),
    Route("/api/projects/{pid}/editor/compile", api_editor_compile, methods=["POST"]),
    Route("/api/projects/{pid}/editor/pdf", api_editor_pdf),
    Route("/api/projects/{pid}/editor/log", api_editor_log),
    Route("/api/projects/{pid}/editor/outline", api_editor_outline),
    Route("/api/projects/{pid}/editor/sync", api_editor_sync, methods=["POST"]),
    Route("/api/projects/{pid}/editor/source", api_editor_source, methods=["POST"]),
    Route("/api/projects/{pid}/editor/reply", api_reply, methods=["GET", "POST"]),
    Route("/api/projects/{pid}/refcheck", api_refcheck, methods=["POST"]),
    Route("/api/projects/{pid}/editor/arxiv", api_arxiv, methods=["POST"]),
    Route("/api/memory", api_memory, methods=["POST"]),
    Route("/api/projects/{pid}/editor/history", api_editor_history, methods=["GET", "POST"]),
    Route("/api/projects/{pid}/editor/download", api_editor_download),
    Route("/api/projects/{pid}/editor/assist", api_editor_assist, methods=["POST"]),
    Route("/api/projects/{pid}/editor/apply", api_editor_apply, methods=["POST"]),
    Route("/api/projects/{pid}/proof", api_proof, methods=["GET", "POST"]),
    Route("/api/projects/{pid}/proof/certificate", api_proof_certificate),
    Route("/api/projects/{pid}/proof/bundle", api_proof_bundle),
    Route("/api/proof/verify", api_proof_verify, methods=["POST"]),
    Route("/api/identity", api_identity),
    Mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static"),
]


class LocalOnly:
    """The app serves only this computer. A request must name this computer as its host (which stops a web page from
    reaching the app through a renamed address), and a request that changes anything must come from the app's own
    page (which stops another website from sending it orders through the browser)."""
    HOSTS = ("127.0.0.1", "localhost", "[::1]")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            host = headers.get("host", "").rsplit(":", 1)[0] if not headers.get("host", "").startswith("[") \
                else headers.get("host", "").split("]")[0] + "]"
            bad = host not in self.HOSTS
            if not bad and scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS"):
                origin = headers.get("origin")
                if origin and origin != "null":
                    o = origin.split("://", 1)[-1]
                    o_host = o.split("]")[0] + "]" if o.startswith("[") else o.rsplit(":", 1)[0]
                    bad = o_host not in self.HOSTS or o != headers.get("host", "")
                elif origin == "null":
                    bad = True
            if bad:
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"ok": false, "error": "Requests are accepted only from this app on this computer."}'})
                return
        await self.app(scope, receive, send)


def create_app():
    import mimetypes
    for ext, kind in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                      (".woff2", "font/woff2"), (".pdf", "application/pdf")):
        mimetypes.add_type(kind, ext)     # Windows can map .js to text/plain in its registry; modules then refuse to load
    config.load_env()
    config.PROJECTS_DIR.mkdir(exist_ok=True)
    _recover()
    return LocalOnly(Starlette(routes=routes))


def _recover() -> None:
    """After a restart nothing is running: clear stale 'running' marks so Continue works, and keep the
    appointments of projects that are waiting for a limit to reset."""
    for info in store.list_projects():
        p = store.get(info["id"])
        if not p:
            continue

        def f(st):
            st["running"] = False
            for s in st["stages"].values():
                if s.get("status") == "running":
                    s["status"] = "pending"
        st = p.update_state(f)
        for s in st.get("stages", {}).values():           # a project that was waiting for a limit keeps waiting
            if s.get("status") == "paused" and s.get("resume_at"):
                pipeline.arm(p.id, max(float(s["resume_at"]), time.time() + 5))
