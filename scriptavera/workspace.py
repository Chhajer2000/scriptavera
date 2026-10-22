# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The paper as a LaTeX project that the author edits directly, in the editor.

It is made once from the finished paper: main.tex keeps the preamble, the title and the abstract, and \\input's one
file per section from the folder sections/ and the references from bibliography.tex. From then on the files are the
author's own copy. Scriptavera never rewrites them by itself; every change, the author's or one the author accepted
from the AI, can be undone from the history.

Compiling uses the LaTeX installed on this computer. Each run also records, for every numbered thing in the paper
(sections, definitions, lemmas, theorems, equations, figures), the number LaTeX gave it and the line it comes from:
that is how "Definition 2.1" in a command is found in the source, exactly as it appears in the PDF.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

from . import paper as paperlib
from . import synctex
from .store import Project
from .texutil import find_group
from .util import slug

EDITOR = "editor"                  # the author's files
HISTORY = "editor_history"         # every version, content-addressed
BUILD = "editor_build"             # what LaTeX writes: the PDF, the log, SyncTeX, the numbering map

TEXT_EXT = {".tex", ".bib", ".sty", ".cls", ".bst", ".bbx", ".cbx", ".cfg", ".def", ".clo", ".ltx", ".txt", ".md",
            ".csv", ".dat", ".tikz"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".pdf", ".eps"}
MAX_TEXT = 4 * 1024 * 1024
MAX_BINARY = 25 * 1024 * 1024
MAX_FILES = 500
KEEP_AUTO = 80                     # automatic versions kept; versions with a name are always kept

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,80}$")
_compile_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class WorkspaceError(ValueError):
    """Something the author asked for cannot be done (a bad name, a missing file). The message says why."""


# ------------------------------------------------------------------ places

def root(p: Project) -> Path:
    return p.dir / EDITOR


def build_dir(p: Project) -> Path:
    return p.dir / BUILD


def exists(p: Project) -> bool:
    return (root(p) / main_file(p)).exists()


def meta(p: Project) -> dict:
    return p.load("editor", {}) or {}


def main_file(p: Project) -> str:
    return meta(p).get("main") or "main.tex"


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def clean_path(rel: str, *, new: bool = False) -> str:
    """A path inside the project, written with '/', checked. Hidden files, '..', absolute paths and unknown kinds of
    file are refused. `new` also requires a plain name (letters, digits, '.', '-', '_') for every part."""
    s = str(rel or "").strip().replace("\\", "/")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):          # /etc/x, //server/share, C:/x: never inside the project
        raise WorkspaceError(f"'{rel}' is not a file name the editor accepts: give a path inside the project.")
    s = s.rstrip("/")
    if not s:
        raise WorkspaceError("Give the file a name.")
    parts = s.split("/")
    if len(parts) > 6 or len(s) > 220:
        raise WorkspaceError("That path is too long.")
    for part in parts:
        if part in ("", ".", "..") or part.startswith(".") or ":" in part:
            raise WorkspaceError(f"'{rel}' is not a file name the editor accepts.")
        if new and not _NAME.match(part):
            raise WorkspaceError(f"Use letters, digits, '-', '_' and '.' in file names ('{part}' has other characters). "
                                 "LaTeX handles such names everywhere.")
    return "/".join(parts)


def _kind(rel: str) -> str:
    ext = Path(rel).suffix.lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in BINARY_EXT:
        return "binary"
    return ""


def resolve(p: Project, rel: str, *, new: bool = False, folder: bool = False) -> Path:
    rel = clean_path(rel, new=new)
    if not folder and not _kind(rel):
        raise WorkspaceError(f"The editor works with LaTeX files ({', '.join(sorted(TEXT_EXT))}) and images "
                             f"({', '.join(sorted(BINARY_EXT))}).")
    base = root(p).resolve()
    target = (root(p) / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise WorkspaceError("That path leads outside the project.") from None
    return target


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".saving")
    tmp.write_bytes(data)
    for attempt in range(8):                      # Windows refuses to replace a file another program has open
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.15)


# --------------------------------------------------------------- creation

def _section_bounds(body: str) -> list[int]:
    """Offsets in the document body where a top-level \\section or \\section* starts (at the beginning of a line)."""
    return [m.start() for m in re.finditer(r"(?m)^[ \t]*\\section\*?\s*[\[{]", body)]


def split_tex(tex: str) -> dict[str, str]:
    """The single main.tex as a project: main.tex with the preamble, the front matter and \\input lines, a file per
    section in sections/, and the references in bibliography.tex. Nothing is reworded: joining the files back
    gives the same document."""
    b = tex.find("\\begin{document}")
    e = tex.rfind("\\end{document}")
    if b < 0 or e < 0 or e < b:
        return {"main.tex": tex}
    head = tex[:b + len("\\begin{document}")]
    body = tex[b + len("\\begin{document}"):e]
    tail = tex[e:]
    bib = ""
    bi = body.find("\\begin{thebibliography}")
    if bi >= 0:
        be = body.find("\\end{thebibliography}", bi)
        if be >= 0:
            be += len("\\end{thebibliography}")
            bib = body[bi:be]
            body = body[:bi] + "\n%@@bibliography@@\n" + body[be:]
    starts = _section_bounds(body)
    files: dict[str, str] = {}
    if not starts:
        front, sections = body, []
    else:
        front = body[:starts[0]]
        sections = [body[s:(starts[i + 1] if i + 1 < len(starts) else len(body))] for i, s in enumerate(starts)]
    inputs: list[str] = []
    used: set[str] = set()
    after = ""
    for i, chunk in enumerate(sections, 1):
        if "%@@bibliography@@" in chunk:              # whatever follows the references stays after them
            chunk, after = chunk.split("%@@bibliography@@", 1)
        m = re.match(r"\s*\\section\*?\s*(?:\[[^\]]*\])?\s*\{", chunk)
        title = ""
        if m:
            g = find_group(chunk, m.end() - 1)
            title = g[0] if g else ""
        name = slug(re.sub(r"\$[^$]*\$|\\[A-Za-z]+", " ", title), 36) if title.strip() else "section"
        stem = f"{i:02d}-{name}"
        while stem in used:
            stem += "-x"
        used.add(stem)
        files[f"sections/{stem}.tex"] = chunk.strip("\n") + "\n"
        inputs.append(f"\\input{{sections/{stem}}}")
    if "%@@bibliography@@" in front:
        front, after = front.split("%@@bibliography@@", 1)
    lines = [head, front.rstrip() + "\n" if front.strip() else "\n"]
    lines += [x + "\n" for x in inputs]
    if bib:
        files["bibliography.tex"] = bib.strip() + "\n"
        lines.append("\n\\input{bibliography}   % the references; references.bib has them in BibTeX form\n")
    if after.strip():
        lines.append(after.strip() + "\n")
    banner = ("% The paper as a LaTeX project. Each section is a file in the folder sections/, included below with\n"
              "% \\input. Made by Scriptavera from the finished paper; this copy is yours to edit.\n")
    files["main.tex"] = banner + "".join(lines).rstrip("\n") + "\n" + tail.lstrip("\n").rstrip() + "\n"
    return dict(sorted(files.items(), key=lambda kv: (kv[0] != "main.tex", kv[0])))


def source_status(p: Project) -> dict:
    """Has Scriptavera rebuilt the paper since the editor copy was made (for example after a change of title in the
    app)? The editor copy is never changed by that; the author decides whether to start again from the new one."""
    m = meta(p)
    out = p.dir / "out" / "main.tex"
    if not m or not out.exists():
        return {"changed": False}
    now = sha(out.read_bytes())
    return {"changed": now != m.get("source_sha"), "built": out.stat().st_mtime}


def create(p: Project, reset: bool = False) -> dict:
    """Make the project from the finished paper. With `reset`, the author's current files are first kept as a
    version, then replaced."""
    out = p.dir / "out" / "main.tex"
    if not out.exists():
        raise WorkspaceError("The editor opens once the paper is written: the LaTeX file is made at the step "
                             "'Final version'.")
    ws = root(p)
    with p._lock:
        if exists(p) and not reset:
            return info(p)
        if exists(p):
            snapshot(p, "Before starting again from Scriptavera's version", "restore", force=True)
            for f in list(ws.rglob("*")):
                if f.is_file():
                    f.unlink()
        ws.mkdir(parents=True, exist_ok=True)
        tex = out.read_text(encoding="utf-8")
        for rel, text in split_tex(tex).items():
            _write_atomic(ws / rel, text.encode("utf-8"))
        bib = p.dir / "out" / "references.bib"
        if bib.exists():
            _write_atomic(ws / "references.bib", bib.read_bytes())
        z = p.dir / "out" / "paper.zip"
        if z.exists():
            try:
                with zipfile.ZipFile(z) as zf:
                    for name in zf.namelist():
                        if name.startswith("figures/") and not name.endswith("/") and _kind(name) == "binary":
                            _write_atomic(ws / clean_path(name), zf.read(name))
            except (zipfile.BadZipFile, WorkspaceError, OSError):
                pass
        p.save("editor", {"created": time.time(), "main": "main.tex", "source_sha": sha(out.read_bytes()),
                          "probe": meta(p).get("probe", True)})
        shutil.rmtree(build_dir(p), ignore_errors=True)
        snapshot(p, "Made from the finished paper" if not reset else "Started again from Scriptavera's version",
                 "start", force=True)
    return info(p)


def import_tree(p: Project, tree: dict[str, bytes], main: str) -> dict:
    """A paper written elsewhere: its files exactly as they are, the main file being the one with \\documentclass."""
    ws = root(p)
    with p._lock:
        ws.mkdir(parents=True, exist_ok=True)
        n = 0
        for rel, data in tree.items():
            try:
                safe = clean_path(rel)
            except WorkspaceError:
                continue
            if not _kind(safe):
                continue
            _write_atomic(ws / safe, data)
            n += 1
        if not (ws / main).exists():
            raise WorkspaceError("The main .tex file could not be written.")
        p.save("editor", {"created": time.time(), "main": main, "imported": True, "probe": True})
        snapshot(p, "Imported", "start", force=True)
    return info(p)


# ------------------------------------------------------------------- files

def files(p: Project) -> list[str]:
    ws = root(p)
    if not ws.exists():
        return []
    out = []
    for f in sorted(ws.rglob("*")):
        rel = f.relative_to(ws).as_posix()
        if f.is_file() and not any(x.startswith(".") for x in rel.split("/")) and not f.name.endswith(".saving") and _kind(rel):
            out.append(rel)
    return out


def tree(p: Project) -> list[dict]:
    ws = root(p)
    out = []
    if not ws.exists():
        return out
    for f in sorted(ws.rglob("*"), key=lambda x: x.relative_to(ws).as_posix().lower()):
        rel = f.relative_to(ws).as_posix()
        if any(x.startswith(".") for x in rel.split("/")) or f.name.endswith(".saving"):
            continue
        if f.is_dir():
            out.append({"path": rel, "kind": "dir"})
        elif _kind(rel):
            st = f.stat()
            out.append({"path": rel, "kind": _kind(rel), "size": st.st_size, "mtime": st.st_mtime})
    return out


def read_text(p: Project, rel: str) -> dict:
    f = resolve(p, rel)
    if _kind(rel) != "text":
        raise WorkspaceError("That file is not a text file.")
    if not f.exists():
        raise WorkspaceError(f"There is no file {clean_path(rel)}.")
    data = f.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return {"path": clean_path(rel), "text": text, "sha": sha(data)}


def read_bytes(p: Project, rel: str) -> bytes:
    f = resolve(p, rel)
    if not f.exists():
        raise WorkspaceError(f"There is no file {clean_path(rel)}.")
    return f.read_bytes()


def write_text(p: Project, rel: str, text: str, base: str | None = None) -> dict:
    """Save a text file. `base` is the version the editor started from: if the file changed since (another window,
    an accepted AI change), nothing is overwritten and the caller is told."""
    f = resolve(p, rel)
    if _kind(rel) != "text":
        raise WorkspaceError("Only text files can be edited here.")
    data = str(text).replace("\r\n", "\n").encode("utf-8")
    if len(data) > MAX_TEXT:
        raise WorkspaceError("That file is too large for the editor.")
    with p._lock:
        if base and f.exists():
            cur = sha(f.read_bytes())
            if cur != base:
                return {"ok": False, "conflict": True, "sha": cur}
        if not f.exists() and len(files(p)) >= MAX_FILES:
            raise WorkspaceError("The project has too many files.")
        _write_atomic(f, data)
        _maybe_autosnapshot(p)
    return {"ok": True, "sha": sha(data), "path": clean_path(rel)}


def write_bytes(p: Project, rel: str, data: bytes) -> dict:
    f = resolve(p, rel, new=True)
    if len(data) > (MAX_BINARY if _kind(rel) == "binary" else MAX_TEXT):
        raise WorkspaceError("That file is too large.")
    with p._lock:
        if not f.exists() and len(files(p)) >= MAX_FILES:
            raise WorkspaceError("The project has too many files.")
        _write_atomic(f, data)
    return {"ok": True, "path": clean_path(rel), "sha": sha(data)}


def new_file(p: Project, rel: str) -> dict:
    f = resolve(p, rel, new=True)
    if f.exists():
        raise WorkspaceError(f"{clean_path(rel)} already exists.")
    if _kind(rel) != "text":
        raise WorkspaceError("New files are text files (.tex, .bib, .sty ...). Upload images instead.")
    return write_text(p, rel, "")


def new_folder(p: Project, rel: str) -> dict:
    f = resolve(p, rel, new=True, folder=True)
    f.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": clean_path(rel)}


def rename(p: Project, rel: str, to: str) -> dict:
    src = root(p) / clean_path(rel)
    if not src.exists():
        raise WorkspaceError(f"There is no {clean_path(rel)}.")
    if src.is_dir():
        dst = resolve(p, to, new=True, folder=True)
    else:
        dst = resolve(p, to, new=True)
        if _kind(rel) != _kind(to):
            raise WorkspaceError("Keep the kind of file when renaming (for example .tex stays .tex).")
    if dst.exists():
        raise WorkspaceError(f"{clean_path(to)} already exists.")
    if clean_path(rel) == main_file(p):
        raise WorkspaceError("main.tex is the file LaTeX starts from; it keeps its name.")
    with p._lock:
        snapshot(p, f"Before renaming {clean_path(rel)}", "auto")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    return {"ok": True, "path": clean_path(to)}


def delete(p: Project, rel: str) -> dict:
    """Delete a file or an empty folder. The file stays in the history."""
    target = root(p) / clean_path(rel)
    if not target.exists():
        raise WorkspaceError(f"There is no {clean_path(rel)}.")
    if clean_path(rel) == main_file(p):
        raise WorkspaceError("main.tex is the file LaTeX starts from; it cannot be deleted.")
    with p._lock:
        snapshot(p, f"Before deleting {clean_path(rel)}", "auto")
        if target.is_dir():
            if any(target.rglob("*")):
                raise WorkspaceError("Only an empty folder can be deleted. Delete or move its files first.")
            target.rmdir()
        else:
            target.unlink()
    return {"ok": True}


def zip_project(p: Project) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files(p):
            z.write(root(p) / rel, rel)
        pdf = build_dir(p) / (Path(main_file(p)).stem + ".pdf")
        if pdf.exists():
            z.write(pdf, Path(main_file(p)).stem + ".pdf")
    return buf.getvalue()


def info(p: Project) -> dict:
    res = last_result(p)
    return {"exists": exists(p), "main": main_file(p), "tree": tree(p), "meta": meta(p), "source": source_status(p),
            "engine": paperlib.find_engine(), "build": _public(res) if res else None}


# ----------------------------------------------------------------- history

def _hist(p: Project) -> Path:
    return p.dir / HISTORY


def versions(p: Project) -> list[dict]:
    f = _hist(p) / "versions.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


def _save_versions(p: Project, vs: list[dict]) -> None:
    _write_atomic(_hist(p) / "versions.json", json.dumps(vs, ensure_ascii=False, indent=0).encode("utf-8"))


def current_files(p: Project) -> dict[str, str]:
    return {rel: sha((root(p) / rel).read_bytes()) for rel in files(p)}


def snapshot(p: Project, label: str, kind: str = "auto", force: bool = False) -> dict | None:
    """Keep the current state of every file as a version. Nothing is kept twice: an unchanged state is skipped
    (unless `force`), and file contents are stored once however many versions share them."""
    with p._lock:
        if not root(p).exists():
            return None
        objects = _hist(p) / "objects"
        objects.mkdir(parents=True, exist_ok=True)
        state: dict[str, str] = {}
        for rel in files(p):
            data = (root(p) / rel).read_bytes()
            h = sha(data)
            state[rel] = h
            blob = objects / h
            if not blob.exists():
                _write_atomic(blob, data)
        vs = versions(p)
        if vs and vs[-1]["files"] == state and not force:
            return None
        v = {"id": (vs[-1]["id"] + 1) if vs else 1, "at": time.time(), "label": str(label)[:200], "kind": kind,
             "files": state}
        vs.append(v)
        autos = [x for x in vs if x["kind"] == "auto"]
        if len(autos) > KEEP_AUTO:
            drop = {x["id"] for x in autos[:len(autos) - KEEP_AUTO]}
            vs = [x for x in vs if x["id"] not in drop]
            used = {h for x in vs for h in x["files"].values()}
            for blob in objects.iterdir():
                if blob.name not in used:
                    try:
                        blob.unlink()
                    except OSError:
                        pass
        _save_versions(p, vs)
        return v


def _maybe_autosnapshot(p: Project, every: float = 600.0) -> None:
    """While the author types, a version is kept at most every ten minutes."""
    vs = versions(p)
    if not vs or time.time() - vs[-1]["at"] >= every:
        snapshot(p, "Edited", "auto")


def version_text(p: Project, vid: int, rel: str) -> str | None:
    v = next((x for x in versions(p) if x["id"] == vid), None)
    if not v or rel not in v["files"]:
        return None
    blob = _hist(p) / "objects" / v["files"][rel]
    return blob.read_bytes().decode("utf-8", "replace") if blob.exists() else None


def diff_version(p: Project, vid: int) -> list[dict]:
    """What changed from version `vid` to the files as they are now, file by file."""
    import difflib
    v = next((x for x in versions(p) if x["id"] == vid), None)
    if not v:
        raise WorkspaceError("That version does not exist.")
    now = current_files(p)
    out = []
    for rel in sorted(set(v["files"]) | set(now)):
        a, b = v["files"].get(rel), now.get(rel)
        if a == b:
            continue
        if _kind(rel) != "text":
            out.append({"path": rel, "status": "added" if not a else "deleted" if not b else "changed", "diff": ""})
            continue
        old = version_text(p, vid, rel) or "" if a else ""
        new = (root(p) / rel).read_text(encoding="utf-8", errors="replace") if b else ""
        d = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"version {vid}/{rel}", f"now/{rel}", n=2))
        out.append({"path": rel, "status": "added" if not a else "deleted" if not b else "changed", "diff": d[:200000]})
    return out


def restore(p: Project, vid: int) -> dict:
    v = next((x for x in versions(p) if x["id"] == vid), None)
    if not v:
        raise WorkspaceError("That version does not exist.")
    with p._lock:
        snapshot(p, f"Before going back to version {vid}", "restore", force=True)
        objects = _hist(p) / "objects"
        for rel in files(p):
            if rel not in v["files"]:
                (root(p) / rel).unlink()
        for rel, h in v["files"].items():
            blob = objects / h
            if blob.exists():
                _write_atomic(root(p) / rel, blob.read_bytes())
        snapshot(p, f"Back to version {vid} ({v['label']})", "restore", force=True)
    return {"ok": True}


# ----------------------------------------------------------------- compile

# Recorded on every run: each counter LaTeX steps (sections, theorem-like environments, equations, figures), with
# the number as printed, the environment, and the file and line. It is passed on the command line, so nothing is
# added to the author's files, and it writes only main.scvmap; the PDF is the same with or without it.
PROBE = (r"\makeatletter\newwrite\scv@map\immediate\openout\scv@map=\jobname.scvmap\relax"
         r"\def\scv@rec#1{\begingroup\let\protect\string\immediate\write\scv@map{#1|\@currenvir|\csname the#1\endcsname|"
         r"\ifdefined\CurrentFilePath\CurrentFilePath\fi|\ifdefined\CurrentFile\CurrentFile\fi|\the\inputlineno}\endgroup}"
         r"\def\scv@hook{\let\scv@orig@sc\stepcounter\protected\def\stepcounter##1{\scv@orig@sc{##1}\scv@rec{##1}}}"
         r"\ifdefined\AddToHook\AddToHook{begindocument/end}{\scv@hook}\else\AtBeginDocument{\scv@hook}\fi\makeatother")

_MIKTEX: dict[str, bool] = {}


def _is_miktex(engine: str) -> bool:
    if engine not in _MIKTEX:
        try:
            r = subprocess.run([engine, "--version"], capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
            _MIKTEX[engine] = b"miktex" in (r.stdout or b"").lower()
        except (subprocess.TimeoutExpired, OSError):
            _MIKTEX[engine] = False
    return _MIKTEX[engine]


def _lock(pid: str) -> threading.Lock:
    with _guard:
        return _compile_locks.setdefault(pid, threading.Lock())


def last_result(p: Project) -> dict | None:
    f = build_dir(p) / "result.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _public(res: dict) -> dict:
    return {k: v for k, v in res.items() if k not in ("numbers", "sources")}


def compile_project(p: Project, timeout: int = 240) -> dict:
    """Compile with the LaTeX installed here. Runs LaTeX as often as cross-references need (and BibTeX or Biber
    when the project uses them), always to the end, so that a PDF appears even when there are errors."""
    with _lock(p.id):
        return _compile(p, timeout)


def _run(cmd: list[str], cwd: Path, timeout: int, env: dict) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, "LaTeX took too long and was stopped."
    except OSError as e:
        return -2, f"LaTeX could not be started ({e})."


def _compile(p: Project, timeout: int) -> dict:
    ws, out = root(p), build_dir(p)
    t0 = time.time()
    main = main_file(p)
    job = Path(main).stem
    sources = {rel: sha((ws / rel).read_bytes()) for rel in files(p) if _kind(rel) == "text"}
    prev = last_result(p) or {}
    res = {"ok": False, "engine": None, "errors": [], "warnings": [], "pages": None, "seconds": 0.0,
           "build": int(prev.get("build", 0)) + 1, "at": time.time(), "sources": sources, "numbers": [],
           "passes": 0, "has_pdf": False, "probe": False}
    if not exists(p):
        res["errors"] = [{"path": "", "line": 0, "message": "The project has no main.tex."}]
        return res
    engine = paperlib.find_engine()
    res["engine"] = engine
    if not engine:
        res["errors"] = [{"path": "", "line": 0, "message": "No LaTeX is installed on this computer. Install MiKTeX "
                          "(miktex.org) or TeX Live, then compile again; or download the project and upload it to Overleaf."}]
        _save_result(p, res)
        return res
    out.mkdir(parents=True, exist_ok=True)
    for d in ws.rglob("*"):                       # \include writes an .aux file in the same folder under the output
        if d.is_dir():
            (out / d.relative_to(ws)).mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        if f.is_file() and f.suffix.lower() in (".bib", ".bst"):
            f.unlink()
    for rel in files(p):                          # BibTeX and Biber look for the bibliography where they run
        if Path(rel).suffix.lower() in (".bib", ".bst") and "/" not in rel:
            shutil.copyfile(ws / rel, out / rel)
    pdf = out / f"{job}.pdf"
    env = dict(os.environ, max_print_line="10000", error_line="254", half_error_line="238")
    use_probe = engine != "tectonic" and meta(p).get("probe", True)
    log = ""
    if engine == "tectonic":
        code, log = _run([engine, "--synctex", "--keep-logs", "--keep-intermediates", "--outdir", str(out), main], ws, timeout, env)
        res["passes"] = 1
    else:
        flags = list(paperlib.engine_flags(engine))
        first_timeout = max(timeout, 420) if _is_miktex(engine) else timeout

        def latex(probe: bool, limit: int) -> tuple[int, str]:
            arg = (PROBE if probe else "") + "\\input{" + main + "}"
            return _run([engine, *flags, "-synctex=1", "-interaction=nonstopmode", "-file-line-error",
                         f"-output-directory={out}", f"-jobname={job}", arg], ws, limit, env)
        if pdf.exists():
            pdf.unlink()
        code, log = latex(use_probe, first_timeout)
        res["passes"] = 1
        if use_probe and _probe_failed(out / f"{job}.log"):
            use_probe = False                     # an unusual setup the probe disturbs: compile without it from now on
            p.save("editor", {**meta(p), "probe": False})
            code, log = latex(False, first_timeout)
        aux = out / f"{job}.aux"
        if code >= 0 and aux.exists() and "\\bibdata{" in aux.read_text(encoding="utf-8", errors="replace"):
            _run(["bibtex", job], out, timeout, env)
            code, log = latex(use_probe, timeout)
            res["passes"] += 1
        elif code >= 0 and (out / f"{job}.bcf").exists() and shutil.which("biber"):
            _run(["biber", job], out, timeout, env)
            code, log = latex(use_probe, timeout)
            res["passes"] += 1
        for _ in range(3):
            text = _read_log(out / f"{job}.log")
            if code < 0 or not re.search(r"Rerun to get|Label\(s\) may have changed|Please rerun|rerunfilecheck.*has changed|"
                                         r"Package rerunfilecheck Warning|\(rerunfilecheck\)\s+Rerun", text):
                break
            code, log = latex(use_probe, timeout)
            res["passes"] += 1
        res["probe"] = use_probe
        if code < 0:
            res["errors"].append({"path": "", "line": 0, "message": log.strip()[:300]})
    text = _read_log(out / f"{job}.log") or log
    parsed = parse_log(text, ws)
    res["errors"] += parsed["errors"]
    res["warnings"] = parsed["warnings"]
    res["has_pdf"] = pdf.exists()
    if pdf.exists():
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(pdf))
            res["pages"] = len(doc)
            doc.close()
        except Exception:  # noqa: BLE001
            res["pages"] = None
    elif not res["errors"]:
        res["errors"].append({"path": "", "line": 0, "message": "LaTeX stopped without producing a PDF."})
    res["ok"] = res["has_pdf"] and not res["errors"]
    res["numbers"] = read_numbers(out / f"{job}.scvmap", ws) if res["probe"] else []
    res["seconds"] = round(time.time() - t0, 2)
    _save_result(p, res)
    return res


def _probe_failed(log_path: Path) -> bool:
    text = _read_log(log_path)
    return bool(re.search(r"^! .*\n(?:.*\n){0,3}?<\*> \\makeatletter", text, re.M)) or "scv@" in "".join(
        re.findall(r"^! .*\n(?:.*\n){0,4}", text, re.M))


def _save_result(p: Project, res: dict) -> None:
    build_dir(p).mkdir(parents=True, exist_ok=True)
    _write_atomic(build_dir(p) / "result.json", json.dumps(res, ensure_ascii=False).encode("utf-8"))


def _read_log(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_bytes().decode("utf-8", "replace")


def pdf_path(p: Project) -> Path:
    return build_dir(p) / (Path(main_file(p)).stem + ".pdf")


def log_text(p: Project) -> str:
    return _read_log(build_dir(p) / (Path(main_file(p)).stem + ".log"))


def sync(p: Project) -> synctex.SyncTeX | None:
    return synctex.load(build_dir(p), root(p), Path(main_file(p)).stem)


# ---------------------------------------------------------------- the log

_FLE = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[^:\n]*?\.(?:tex|sty|cls|bib|bbl|aux|ltx|def|clo|cfg|fd|toc|out|tikz|bbx|cbx)):"
                  r"(?P<line>\d+): (?P<msg>.*)$")


def _unwrap(log: str) -> str:
    """TeX breaks log lines at 79 characters unless told otherwise (MiKTeX ignores the setting): join them again."""
    lines = log.split("\n")
    if sum(1 for x in lines if len(x) == 79) < 3:
        return log
    out, buf = [], ""
    for ln in lines:
        buf += ln
        if len(ln) != 79:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n".join(out)


def _rel(path: str, ws: Path) -> str:
    s = path.strip().strip('"')
    r = synctex.to_relative(s, ws)
    if r:
        return r
    s = s.replace("\\", "/")
    return s[2:] if s.startswith("./") else s


_OPEN = re.compile(r'\((?:"([^"]+)"|((?:[A-Za-z]:)?[^\s()"{}]*\.[A-Za-z0-9]{1,8}))')


def parse_log(log: str, ws: Path) -> dict:
    """Errors and warnings with the file and line they belong to. Errors come with -file-line-error; for warnings
    the file is followed through the log's ( and ) as LaTeX opens and closes files."""
    log = _unwrap(log or "")
    lines = log.split("\n")
    errors: list[dict] = []
    warnings: list[dict] = []
    stack: list[str | None] = []

    def current() -> str:
        for f in reversed(stack):
            if f:
                return f
        return ""

    file_line = any(_FLE.match(x) for x in lines)      # with -file-line-error every error names its file and line
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = _FLE.match(ln)
        if m:
            msg = m.group("msg").strip()
            ctx = []
            for k in range(i + 1, min(i + 8, len(lines))):
                if re.match(r"^l\.\d+", lines[k]):
                    ctx = [lines[k][:240]] + ([lines[k + 1][:240]] if k + 1 < len(lines) and lines[k + 1].strip() else [])
                    break
            path = _rel(m.group("file"), ws)
            if "Emergency stop" not in msg and "==> Fatal error" not in msg:
                errors.append({"path": path, "line": int(m.group("line")), "message": msg[:300],
                               "context": "\n".join(ctx)[:500], "inside": synctex.to_relative(m.group("file"), ws) is not None
                               or not re.match(r"^(?:/|[A-Za-z]:)", m.group("file"))})
            i += 1
            continue
        if ln.startswith("! ") and not file_line:
            msg = ln[2:].strip()
            if "Emergency stop" not in msg:
                line_no = 0
                for k in range(i + 1, min(i + 10, len(lines))):
                    lm = re.match(r"^l\.(\d+)", lines[k])
                    if lm:
                        line_no = int(lm.group(1))
                        break
                errors.append({"path": current(), "line": line_no, "message": msg[:300], "context": "", "inside": True})
        w = re.search(r"(LaTeX|Package (\w+)|Class (\w+)) Warning: (.*)$", ln)
        if w:
            text = w.group(4).strip()
            k = i + 1
            while k < len(lines) and re.match(r"^\((?:\w+)\)\s+", lines[k]):
                text += " " + re.sub(r"^\((?:\w+)\)\s+", "", lines[k]).strip()
                k += 1
            if k == i + 1:
                while k < len(lines) and lines[k].strip() and not re.search(r"Warning:|^[!(]|^\)", lines[k]) and k - i < 4 \
                        and not text.rstrip().endswith("."):
                    text += " " + lines[k].strip()
                    k += 1
            lm = re.search(r"on input line (\d+)", text)
            who = w.group(2) or w.group(3) or "LaTeX"
            kind = "reference" if re.search(r"Reference|Citation|undefined references", text) else "warning"
            if not re.search(r"Label\(s\) may have changed|There were undefined references|Rerun to get", text) or kind == "warning":
                warnings.append({"path": current(), "line": int(lm.group(1)) if lm else 0, "message": text[:300],
                                 "source": who, "kind": kind})
        b = re.match(r"^(Overfull|Underfull) \\[hv]box \(([^)]*)\) (?:in paragraph |detected )?at lines? (\d+)", ln)
        if b and b.group(1) == "Overfull":
            warnings.append({"path": current(), "line": int(b.group(3)), "message": f"Overfull box ({b.group(2)})",
                             "source": "TeX", "kind": "box"})
        # follow files opening and closing on this line
        pos = 0
        while pos < len(ln):
            ch = ln[pos]
            if ch == "(":
                om = _OPEN.match(ln, pos)
                if om:
                    name = om.group(1) or om.group(2)
                    stack.append(_rel(name, ws))
                    pos = om.end()
                    continue
                stack.append(None)
            elif ch == ")":
                if stack:
                    stack.pop()
            pos += 1
        i += 1
    seen, uniq = set(), []
    for e in errors:
        key = (e["path"], e["line"], e["message"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    wseen, wuniq = set(), []
    for w in warnings:
        key = (w["path"], w["line"], w["message"])
        if key not in wseen:
            wseen.add(key)
            wuniq.append(w)
    return {"errors": uniq[:60], "warnings": wuniq[:200]}


# ---------------------------------------------------------- the numbering

_SKIP_COUNTERS = {"page", "bookmark@seq@number", "enumi", "enumii", "enumiii", "enumiv", "footnote", "mpfootnote",
                  "Hfootnote", "Item", "parentequation", "lstnumber", "FancyVerbLine", "tabx@nest", "paragraph",
                  "subparagraph", "part", "chapter"}


def read_numbers(path: Path, ws: Path) -> list[dict]:
    """The numbering map written by the probe: what LaTeX numbered, where in the source, and the number printed."""
    if not path.exists():
        return []
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        counter, env, number, folder, name, ln = parts[0], parts[1], parts[2], parts[3], parts[4], parts[-1]
        if counter in _SKIP_COUNTERS or not number or re.search(r"(?:^|\.)0$", number) or not ln.strip().isdigit():
            continue
        folder = folder.strip().replace("\\", "/").rstrip("/")
        rel = _rel((folder + "/" if folder else "") + name.strip(), ws) if name.strip() else ""
        key = (counter, number, rel, ln)
        if key in seen:
            continue                              # amsmath measures an alignment before setting it: the same step twice
        seen.add(key)
        out.append({"counter": counter, "env": env, "number": number.strip(), "path": rel, "line": int(ln)})
    return out


# ------------------------------------------------------------- the outline

_SEC = re.compile(r"\\(section|subsection|subsubsection)(\*?)\s*(?:\[[^\]]*\])?\s*\{")
_BEGIN = re.compile(r"\\begin\{([A-Za-z@]+\*?)\}")
_END = re.compile(r"\\end\{([A-Za-z@]+\*?)\}")
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")
_NEWTHM = re.compile(r"\\newtheorem(\*?)\s*\{([A-Za-z@]+\*?)\}\s*(?:\[([A-Za-z@]+)\])?\s*\{([^{}]*)\}\s*(?:\[([A-Za-z@]+)\])?")
EQUATION_ENVS = ("equation", "align", "gather", "multline", "flalign", "alignat", "eqnarray")


def strip_comment(line: str) -> str:
    m = re.search(r"(?<!\\)%", line)
    return line[:m.start()] if m else line


def _resolve_input(ws: Path, name: str) -> str | None:
    n = name.strip().replace("\\", "/")
    for cand in (n, n + ".tex"):
        try:
            rel = clean_path(cand)
        except WorkspaceError:
            return None
        if (ws / rel).is_file() and _kind(rel) == "text":
            return rel
    return None


def reading_order(ws: Path, main: str = "main.tex", texts: dict[str, str] | None = None) -> list[tuple[str, int, str]]:
    """Every line of the project in the order LaTeX reads it, following \\input and \\include. `texts` replaces
    files with other contents (a proposed change), without touching the disk."""
    out: list[tuple[str, int, str]] = []
    seen: list[str] = []

    def text_of(rel: str) -> str | None:
        if texts is not None and rel in texts:
            return texts[rel]
        f = ws / rel
        return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None

    def visit(rel: str, depth: int) -> None:
        if depth > 12 or rel in seen:
            return
        src = text_of(rel)
        if src is None:
            return
        seen.append(rel)
        for n, line in enumerate(src.split("\n"), 1):
            out.append((rel, n, line))
            for m in _INPUT.finditer(strip_comment(line)):
                target = m.group(1).strip()
                cand = None
                for c in (target, target + ".tex"):
                    if texts is not None and c in texts:
                        cand = c
                        break
                cand = cand or _resolve_input(ws, target)
                if cand:
                    visit(cand, depth + 1)
        seen.pop()

    visit(main, 0)
    return out


def theorem_kinds(lines: list[tuple[str, int, str]]) -> dict[str, dict]:
    """Environments declared with \\newtheorem: their printed name, the counter they use, and what resets it."""
    kinds: dict[str, dict] = {}
    for _, _, raw in lines:
        for m in _NEWTHM.finditer(strip_comment(raw)):
            star, env, shared, title, within = m.groups()
            kinds[env] = {"name": title.strip() or env.capitalize(), "numbered": not star,
                          "counter": shared or env, "within": within or ""}
    for env, k in kinds.items():                   # a shared counter is reset by what resets its owner
        if k["counter"] != env and k["counter"] in kinds:
            k["within"] = kinds[k["counter"]]["within"]
    return kinds


def scan(lines: list[tuple[str, int, str]]) -> tuple[list[dict], dict[str, dict]]:
    """The numbered things of the paper as they stand in the source, in reading order, with where each begins and
    ends. Numbers are filled in later (from the last compile, or by counting)."""
    kinds = theorem_kinds(lines)
    items: list[dict] = []
    stack: list[dict] = []
    in_doc = False
    for idx, (rel, n, raw) in enumerate(lines):
        line = strip_comment(raw)
        if "\\begin{document}" in line:
            in_doc = True
        if not in_doc:
            continue
        if re.search(r"\\appendix\b", line):
            items.append({"kind": "appendix", "path": rel, "line": n, "end": n})
        for m in _SEC.finditer(line):
            g = find_group(line, m.end() - 1)
            title = (g[0] if g else line[m.end():]).strip()
            rest = line[g[1]:] if g else ""
            lab = re.match(r"\s*\\label\{([^{}]+)\}", rest)
            if not lab and idx + 1 < len(lines) and lines[idx + 1][0] == rel:
                lab = re.match(r"\s*\\label\{([^{}]+)\}", strip_comment(lines[idx + 1][2]))
            items.append({"kind": m.group(1), "starred": bool(m.group(2)), "title": title, "path": rel, "line": n,
                          "end": n, "label": lab.group(1) if lab else "", "labels": [lab.group(1)] if lab else []})
        tokens = sorted([(m.start(), "b", m) for m in _BEGIN.finditer(line)] + [(m.start(), "e", m) for m in _END.finditer(line)],
                        key=lambda t: t[0])
        for pos, what, m in tokens:
            env = m.group(1)
            if what == "b":
                base = env.rstrip("*")
                kind = ("env" if env in kinds else "proof" if base == "proof" else "equation" if base in EQUATION_ENVS
                        else "figure" if base in ("figure", "table") else "")
                rec = {"env": env, "path": rel, "line": n, "kind": kind, "start_idx": idx, "pos": pos}
                if kind:
                    after = line[m.end():]
                    title = ""
                    if kind in ("env", "proof") and after.lstrip().startswith("["):
                        depth, k0 = 0, after.index("[")
                        for k in range(k0, len(after)):
                            if after[k] == "[":
                                depth += 1
                            elif after[k] == "]":
                                depth -= 1
                                if depth == 0:
                                    title = after[k0 + 1:k]
                                    break
                    rec["title"] = title.strip()
                stack.append(rec)
            else:
                k = len(stack) - 1
                while k >= 0 and stack[k]["env"] != env:
                    k -= 1
                if k < 0:
                    continue
                rec = stack[k]
                del stack[k:]
                if not rec["kind"]:
                    continue
                body = [lines[j] for j in range(rec["start_idx"], idx + 1) if lines[j][0] == rel]
                text = "\n".join(strip_comment(x[2]) for x in body)
                labels = re.findall(r"\\label\{([^{}]+)\}", text)
                item = {"kind": rec["kind"], "env": env, "path": rel, "line": rec["line"], "end": n,
                        "title": rec.get("title", ""), "labels": labels, "label": labels[0] if labels else ""}
                if rec["kind"] == "env":
                    item["starred"] = not kinds[env]["numbered"]
                    item["name"] = kinds[env]["name"]
                    inner_eq = re.search(r"\\begin\{(?:" + "|".join(EQUATION_ENVS) + r")\*?\}", text)
                    first = re.search(r"\\label\{([^{}]+)\}", text[:inner_eq.start()] if inner_eq else text)
                    item["label"] = first.group(1) if first else ""
                elif rec["kind"] == "equation":
                    item["starred"] = env.endswith("*")
                    item["name"] = "Equation"
                elif rec["kind"] == "figure":
                    item["starred"] = False
                    item["name"] = "Figure" if env.startswith("figure") else "Table"
                    cap = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*\{", text)
                    if cap:
                        g = find_group(text, cap.end() - 1)
                        item["title"] = (g[0] if g else "").strip()[:200]
                else:
                    item["name"] = "Proof"
                item["snippet"] = _snippet(text)
                item["order_key"] = (rec["start_idx"], rec["pos"])
                items.append(item)
    for it in items:
        it.setdefault("order_key", (next((j for j, x in enumerate(lines) if x[0] == it["path"] and x[1] == it["line"]), 0), 0))
    items.sort(key=lambda x: x["order_key"])
    # a proof right after a statement belongs to it
    for k, it in enumerate(items):
        if it["kind"] == "proof" and k > 0:
            prev = next((items[j] for j in range(k - 1, -1, -1) if items[j]["kind"] in ("env", "proof", "section",
                                                                                       "subsection", "subsubsection")), None)
            if prev and prev["kind"] == "env" and prev["path"] == it["path"] and _only_blank_between(lines, prev, it):
                prev["proof"] = {"line": it["line"], "end": it["end"]}
                it["of"] = f"{prev['path']}:{prev['line']}"
    for it in items:
        it["id"] = f"{it['path']}:{it['line']}"
        it.pop("order_key", None)
    return items, kinds


def _only_blank_between(lines: list[tuple[str, int, str]], a: dict, b: dict) -> bool:
    between = [strip_comment(x[2]).strip() for x in lines if x[0] == a["path"] and a["end"] < x[1] < b["line"]]
    return all(not s for s in between)


def _snippet(text: str) -> str:
    t = re.sub(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?|\\label\{[^{}]*\}", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:140]


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s or "0"


def count_numbers(items: list[dict], kinds: dict[str, dict], lines: list[tuple[str, int, str]]) -> None:
    """Number the items by counting, the way LaTeX does with the declarations found. Used when there is no compile
    to read the numbers from (LaTeX not installed, or the files changed since)."""
    body = "\n".join(strip_comment(x[2]) for x in lines)
    eq_within = re.search(r"\\numberwithin\{equation\}\{(section|subsection)\}", body)
    sec = [0, 0, 0]
    appendix = False
    counters: dict[str, int] = {}
    eq = 0
    fig = {"figure": 0, "table": 0}

    def sec_str(level: int) -> str:
        head = _alpha(sec[0]) if appendix else str(sec[0])
        return ".".join([head] + [str(x) for x in sec[1:level + 1]])

    for it in items:
        k = it["kind"]
        if k == "appendix":
            appendix, sec = True, [0, 0, 0]
            continue
        if k in ("section", "subsection", "subsubsection"):
            if it.get("starred"):
                it["number"] = ""
                continue
            level = ("section", "subsection", "subsubsection").index(k)
            sec[level] += 1
            for j in range(level + 1, 3):
                sec[j] = 0
            levels = ("section", "subsection", "subsubsection")
            for kd in kinds.values():             # stepping a section resets what is numbered within it, or below it
                if kd["within"] in levels and levels.index(kd["within"]) >= level:
                    counters[kd["counter"]] = 0
            if eq_within and eq_within.group(1) == k:
                eq = 0
            it["number"] = sec_str(level)
        elif k == "env":
            kd = kinds.get(it["env"], {})
            if it.get("starred") or not kd.get("numbered", True):
                it["number"] = ""
                continue
            c = kd.get("counter", it["env"])
            counters[c] = counters.get(c, 0) + 1
            w = kd.get("within", "")
            level = {"section": 0, "subsection": 1, "subsubsection": 2}.get(w)
            it["number"] = (sec_str(level) + "." if level is not None else "") + str(counters[c])
        elif k == "equation":
            if it.get("starred"):
                it["number"], it["numbers"] = "", []
                continue
            text = "\n".join(strip_comment(x[2]) for x in lines if x[0] == it["path"] and it["line"] <= x[1] <= it["end"])
            rows = [text] if it["env"].startswith(("equation", "multline")) else re.split(r"\\\\(?:\[[^\]]*\])?", text)
            nums = []
            for r in rows:
                if not r.strip() or re.search(r"\\(?:nonumber|notag)\b", r):
                    continue
                tag = re.search(r"\\tag\*?\{([^{}]*)\}", r)
                if tag:
                    nums.append(tag.group(1))
                    continue
                eq += 1
                prefix = sec_str(0 if eq_within.group(1) == "section" else 1) + "." if eq_within else ""
                nums.append(prefix + str(eq))
            it["numbers"] = nums
            it["number"] = nums[0] if nums else ""
        elif k == "figure":
            base = "figure" if it["env"].startswith("figure") else "table"
            fig[base] += 1
            it["number"] = str(fig[base])
        else:
            it["number"] = ""


def apply_numbers(items: list[dict], numbers: list[dict], kinds: dict[str, dict]) -> int:
    """Put the numbers LaTeX printed (read by the probe) on the items found in the source. Returns how many matched."""
    matched = 0
    by_path: dict[str, list[dict]] = {}
    for it in items:
        by_path.setdefault(it["path"], []).append(it)
        if it["kind"] in ("env", "equation", "figure", "section", "subsection", "subsubsection"):
            it["number"] = "" if it.get("starred") else it.get("number", "")
            if it["kind"] == "equation":
                it["numbers"] = []
    for rec in numbers:
        cands = by_path.get(rec["path"], [])
        hit = None
        if rec["counter"] in ("section", "subsection", "subsubsection") and rec["env"] not in kinds:
            ok = [it for it in cands if it["kind"] == rec["counter"] and it["line"] <= rec["line"] and not it.get("starred")]
            hit = max(ok, key=lambda it: it["line"]) if ok else None
        elif rec["env"] in kinds:
            ok = [it for it in cands if it["kind"] == "env" and it["env"] == rec["env"] and it["line"] <= rec["line"] <= it["end"]]
            hit = max(ok, key=lambda it: it["line"]) if ok else None
        elif rec["counter"] == "equation":
            ok = [it for it in cands if it["kind"] == "equation" and it["line"] <= rec["line"] <= it["end"]]
            hit = max(ok, key=lambda it: it["line"]) if ok else None
            if hit:
                if rec["number"] not in hit["numbers"]:
                    hit["numbers"].append(rec["number"])
                hit["number"] = hit["numbers"][0]
                matched += 1
                continue
        elif rec["counter"] in ("figure", "table"):
            ok = [it for it in cands if it["kind"] == "figure" and it["line"] <= rec["line"] <= it["end"]]
            hit = max(ok, key=lambda it: it["line"]) if ok else None
        if hit:
            hit["number"] = rec["number"]
            matched += 1
    return matched


def outline(p: Project, texts: dict[str, str] | None = None, prefer_compile: bool = True) -> dict:
    """Every numbered thing of the paper, with its number as LaTeX prints it, where it is, and its label.
    The numbers come from the last compile when the files have not changed since; otherwise they are counted."""
    ws = root(p)
    lines = reading_order(ws, main_file(p), texts)
    items, kinds = scan(lines)
    res = last_result(p) if prefer_compile and texts is None else None
    fresh = bool(res and res.get("numbers") and res.get("sources") == {rel: sha((ws / rel).read_bytes())
                                                                        for rel in files(p) if _kind(rel) == "text"})
    count_numbers(items, kinds, lines)
    source = "counted"
    if fresh:
        apply_numbers(items, res["numbers"], kinds)
        source = "compiled"
    return {"items": [it for it in items if it["kind"] != "appendix"], "kinds": kinds, "numbers_from": source,
            "build": res.get("build") if res else None}


def fresh_numbers(p: Project) -> bool:
    res = last_result(p)
    return bool(res and res.get("numbers") and res.get("sources") == {rel: sha((root(p) / rel).read_bytes())
                                                                       for rel in files(p) if _kind(rel) == "text"})
