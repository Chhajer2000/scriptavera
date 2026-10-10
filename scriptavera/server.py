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
from .llm import PROVIDERS, ROLES, LLMError, router

DOWNLOADS = {
    "main.tex": "text/x-tex", "main_standard_class.tex": "text/x-tex", "references.bib": "text/plain",
    "paper.zip": "application/zip", "paper.html": "text/html", "paper.pdf": "application/pdf",
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


def _people(items) -> list[dict]:
    out = []
    for a in items if isinstance(items, list) else []:
        if isinstance(a, dict) and str(a.get("name") or "").strip():
            out.append({"name": str(a["name"]).strip()[:120], "affiliation": str(a.get("affiliation") or "").strip()[:300],
                        "email": str(a.get("email") or "").strip()[:120]})
    return out[:12]


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
                          "count": st.get("count", 0), "message": st.get("message", "")})
    return {
        "version": __version__, "mock": config.mock_mode(), "domains": domains.DOMAINS,
        "phases": store.PHASES, "stages": store.STAGES,
        "roles": ROLES, "providers": providers, "plan": router.plan() if router.models else {},
        "models": [m.public() for m in router.models], "settings": config.load_settings(), "env": env,
        "engine": paperlib.find_engine(), "macros": paperlib.MACROS, "has_vision": any(m.vision and not m.dead for m in router.models),
        "discovered": bool(router.discovered_at),
        "templates": journals.TEMPLATES,
        "families": [{"id": f["id"], "name": f["name"], "house": f["house"], "template": f["template"]}
                     for f in journals.FAMILIES if f["id"] != "generic"],
        "statements": [{"id": k, "name": name} for k, name in paperlib.STATEMENTS],
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
SOURCE_KINDS = {".py", ".js", ".css", ".html", ".svg", ".png", ".woff2", ".woff", ".ttf", ".md", ".txt", ".bat",
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
        return fail("Upload a PDF or page images of your notes.")
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
    clean = {
        "name": (meta.get("name") or files[0][0]).strip()[:120],
        "domains": doms[:3], "subarea": (meta.get("subarea") or "").strip()[:300],
        "hints": (meta.get("hints") or "").strip()[:1500], "title": (meta.get("title") or "").strip()[:300],
        "authors": _people(meta.get("authors")),
        "known_refs": [r.strip() for r in meta.get("known_refs", []) if r.strip()][:15],
        "problem": problem[:3000], "keywords": keywords[:20],
        "key_papers": [r.strip() for r in meta.get("key_papers", []) if str(r).strip()][:3],
        "files": [n for n, _ in files],
        "journal": journals.clean_journal(meta.get("journal")), "statements": {},
    }
    pid = store.new_id()
    try:
        pages = await run_in_threadpool(ingest.ingest, files, config.PROJECTS_DIR / pid / "pages")
    except Exception as e:  # noqa: BLE001
        return fail(f"Could not read the upload: {e}")
    store.create(clean, pages, pid)
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
        "assessment": assessment, "scope": p.load("scope"), "scope_default": writing.default_scope(assessment) if assessment else None,
        "finish": final.finish_info(p) if p.load("paper") else None,
        "revision": revision.state_json(p),
        "journal": meta.get("journal"), "profile": journals.profile(meta.get("journal")),
        "statements": meta.get("statements") or {},
        "paper": p.load("paper"), "calls": p.calls(300),
        "downloads": [n for n in DOWNLOADS if (p.dir / "out" / n).exists()],
        "kept": (p.dir / "out_before_update").exists(),
    }


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
            got = await run_in_threadpool(literature.resolve_identifier, ident)
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
    if ("astext" in body or "redraw" in body) and drawing:
        return fail("A figure is being drawn right now. Try again in a minute; your other answers are saved.")
    try:
        if "astext" in body:
            got = await run_in_threadpool(pipeline.figure_as_text, p, fid, str(body.get("astext") or "")[:1200])
            return ok(figure=None, page=got["page"], text=got["text"])
        if "redraw" in body:
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


async def index(request: Request):
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


routes = [
    Route("/", index),
    Route("/api/meta", api_meta),
    Route("/api/source", api_source),
    Route("/api/shutdown", api_shutdown, methods=["POST"]),
    Route("/api/settings", api_settings, methods=["POST"]),
    Route("/api/settings/test", api_test, methods=["POST"]),
    Route("/api/journals", api_journals),
    Route("/api/projects", api_projects, methods=["GET"]),
    Route("/api/projects", api_create, methods=["POST"]),
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
