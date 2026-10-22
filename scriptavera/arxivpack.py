# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The editor's files as an arXiv submission, checked the way arXiv processes them.

arXiv compiles the source itself with pdfLaTeX and does not run BibTeX, so the package carries the .bbl made here,
under the main file's name. The package is checked for what makes arXiv submissions fail: a missing .bbl, figures
named differently from \\includegraphics (arXiv is case-sensitive), file names with spaces or other characters,
EPS figures under pdfLaTeX, packages that need shell escape (which arXiv refuses), and size. It is then compiled
from a clean folder without BibTeX, as arXiv would. The form's fields (title, authors, abstract, comments,
categories, MSC) are filled in from the paper."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import paper as paperlib, workspace as W
from .store import Project

_SKIP = (".aux", ".log", ".out", ".toc", ".synctex.gz", ".fls", ".fdb_latexmk", ".blg", ".bcf", ".run.xml", ".xdv", ".dvi")
_SHELL = {"minted": "needs shell escape, which arXiv refuses (use listings instead)", "pythontex": "needs to run Python, which arXiv refuses",
          "svg": "converts SVG with Inkscape through shell escape; convert the figures to PDF first",
          "gnuplottex": "runs gnuplot through shell escape", "epstopdf": "converts EPS through shell escape on some systems; give PDF figures"}
LIMIT = 50 * 1024 * 1024


def _check(name: str, level: str, text: str) -> dict:
    return {"item": name, "level": level, "text": text}


def prepare(p: Project) -> dict:
    if not W.exists(p):
        raise ValueError("Open the editor first: the package is made from its files.")
    main = W.main_file(p)
    job = Path(main).stem
    res = W.compile_project(p) if paperlib.find_engine() else {"ok": False}
    files = {rel: (W.root(p) / rel).read_bytes() for rel in W.files(p)
             if not rel.lower().endswith(_SKIP) and not rel.startswith(("editor_build/", "."))}
    checks: list[dict] = []
    tex_all = "\n".join(files[r].decode("utf-8", "replace") for r in files if r.lower().endswith((".tex", ".sty", ".cls")))
    body = re.sub(r"(?<!\\)%.*", "", tex_all)
    bbl = W.build_dir(p) / f"{job}.bbl"
    uses_bib = bool(re.search(r"\\bibliography\{|\\addbibresource\{|\\printbibliography", body))
    if uses_bib:
        if bbl.exists():
            files[f"{job}.bbl"] = bbl.read_bytes()
            checks.append(_check("Bibliography", "ok", f"{job}.bbl is included (arXiv does not run BibTeX)."))
        else:
            checks.append(_check("Bibliography", "error", "No .bbl could be made: compile the paper here first (arXiv does not run BibTeX)."))
        if re.search(r"\\usepackage(\[[^\]]*\])?\{biblatex\}", body):
            checks.append(_check("Bibliography", "warn", "biblatex: arXiv needs a .bbl made by the same Biber version as its own TeX Live; "
                                                          "if arXiv's compile fails, switch to BibTeX (natbib) or ask arXiv's help."))
    bad_names = [r for r in files if not re.fullmatch(r"[A-Za-z0-9._+\-/]+", r)]
    checks.append(_check("File names", "error" if bad_names else "ok",
                         ("Rename: " + ", ".join(bad_names[:6]) + " (use letters, digits, '.', '-', '_' only).") if bad_names else "All file names are safe."))
    missing, eps = [], []
    for m in re.finditer(r"\\includegraphics\s*(\[[^\]]*\])?\s*\{([^}]+)\}", body):
        name = m.group(2).strip()
        cands = [name] if Path(name).suffix else [name + e for e in (".pdf", ".png", ".jpg", ".jpeg", ".eps")]
        hit = next((c for c in cands if c in files), None)
        if not hit:
            low = {r.lower(): r for r in files}
            near = next((low[c.lower()] for c in cands if c.lower() in low), None)
            missing.append(f"{name}" + (f" (the file is {near}: arXiv is case-sensitive)" if near else ""))
        elif hit.lower().endswith(".eps"):
            eps.append(hit)
    checks.append(_check("Figures", "error" if missing else "ok",
                         ("Not found: " + "; ".join(missing[:6])) if missing else "Every \\includegraphics finds its file."))
    if eps:
        checks.append(_check("Figures", "warn", "EPS figures (" + ", ".join(eps[:4]) + ") do not work with pdfLaTeX: convert them to PDF."))
    for pkg in sorted({x.strip() for m in re.finditer(r"\\usepackage(\[[^\]]*\])?\{([^}]+)\}", body) for x in m.group(2).split(",")}):
        if pkg in _SHELL:
            checks.append(_check(f"Package {pkg}", "error", _SHELL[pkg] + "."))
    if re.search(r"\\write18|\\immediate\\write18", body):
        checks.append(_check("Shell escape", "error", "\\write18 is used: arXiv runs no shell commands."))
    main_text = files[main].decode("utf-8", "replace")
    if not re.search(r"\\pdfoutput\s*=\s*1", "\n".join(main_text.splitlines()[:5])):
        files[main] = ("\\pdfoutput=1\n" + main_text).encode("utf-8")
        checks.append(_check("pdfLaTeX", "ok", "\\pdfoutput=1 added as the first line of the packaged main file, so arXiv uses pdfLaTeX."))
    size = sum(len(d) for d in files.values())
    checks.append(_check("Size", "error" if size > LIMIT else "ok", f"{size / 1048576:.1f} MB" + (" (arXiv's limit is 50 MB)" if size > LIMIT else "")))
    pages = None
    engine = shutil.which("pdflatex")
    if engine:                                   # compiled as arXiv would: a clean folder, pdfLaTeX twice, no BibTeX
        with tempfile.TemporaryDirectory() as tmp:
            for rel, data in files.items():
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / rel).write_bytes(data)
            ok = True
            for _ in range(2):
                try:
                    r = subprocess.run([engine, "-interaction=nonstopmode", "-halt-on-error", main], cwd=tmp, capture_output=True,
                                       text=True, errors="replace", timeout=240)
                    ok = r.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    ok = False
                if not ok:
                    break
            pdf = Path(tmp) / f"{job}.pdf"
            if ok and pdf.exists():
                try:
                    import pypdfium2 as pdfium
                    doc = pdfium.PdfDocument(pdf.read_bytes())
                    pages = len(doc)
                    doc.close()
                except Exception:  # noqa: BLE001
                    pages = None
            log = (Path(tmp) / f"{job}.log").read_text(errors="replace") if (Path(tmp) / f"{job}.log").exists() else ""
            first = re.search(r"^! .*$", log, re.M)
            checks.append(_check("Compiles like arXiv", "ok" if ok else "error",
                                 f"Compiled from a clean folder without BibTeX ({pages or '?'} pages)." if ok else
                                 f"Does not compile from a clean folder: {first.group(0) if first else 'see the log'}"))
    else:
        checks.append(_check("Compiles like arXiv", "warn", "No LaTeX here to try it; arXiv will compile it on upload."))
    meta = metadata(p, main_text, pages, len(re.findall(r"\\begin\{figure", body)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(files.items()):
            z.writestr(rel, data)
    out = p.dir / "out"
    out.mkdir(exist_ok=True)
    (out / "arxiv-submission.zip").write_bytes(buf.getvalue())
    (out / "arxiv-metadata.txt").write_text("\n\n".join(f"{k.upper()}\n{v}" for k, v in meta.items() if v), encoding="utf-8")
    return {"checks": checks, "metadata": meta, "ok": not any(c["level"] == "error" for c in checks), "files": len(files)}


def _plain(tex: str) -> str:
    s = re.sub(r"\\(emph|textit|textbf|textrm)\{([^{}]*)\}", r"\2", tex or "")
    s = re.sub(r"\\cite[pt]?\*?(\[[^\]]*\])?\{[^}]*\}", "", s)
    s = re.sub(r"~", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def metadata(p: Project, main_text: str, pages: int | None, figures: int) -> dict:
    meta = p.state.get("meta", {})
    P = p.load("paper") or {}
    m = re.search(r"\\title\s*(\[[^\]]*\])?\s*\{(.+?)\}\s*\n", main_text, re.S)
    title = P.get("title") or (m.group(2) if m else meta.get("title", ""))
    a = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", main_text, re.S)
    abstract = _plain(P.get("abstract") or (a.group(1) if a else ""))
    authors = ", ".join(f"{x['name']}" + (f" ({x['affiliation']})" if x.get("affiliation") else "") for x in meta.get("authors", []) if x.get("name"))
    doms = [d for d in meta.get("domains", []) if re.fullmatch(r"[a-z\-]+\.[A-Za-z\-]+", d)]
    comments = ", ".join(x for x in ((f"{pages} pages" if pages else ""), (f"{figures} figure{'s' if figures != 1 else ''}" if figures else "")) if x)
    return {"title": _plain(title), "authors": authors, "abstract": abstract[:1920],
            "abstract_note": "" if len(abstract) <= 1920 else "arXiv allows 1,920 characters: the abstract was cut, shorten it.",
            "comments": comments, "primary category": doms[0] if doms else "", "cross-lists": ", ".join(doms[1:]),
            "MSC class": ", ".join(P.get("msc") or []), "journal reference": "", "DOI": "",
            "licence": "Choose on arXiv: arXiv's non-exclusive licence (the usual choice) or CC BY 4.0. The choice cannot be changed later."}
