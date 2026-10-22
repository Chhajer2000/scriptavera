# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Notes that are typed, not handwritten.

- LaTeX (one .tex file, or an Overleaf project downloaded as a .zip), Markdown or plain text: the source IS the
  transcript, so nothing is read by a model and nothing can be misread. The text is cut into "pages" of a few
  thousand characters at paragraph boundaries, never inside an environment or a displayed formula, and each page
  gets an image of its source so that every screen of the app works as for scanned notes. The author's own macros
  (\\newcommand, \\DeclareMathOperator) and the packages for mathematics they load go into the paper's preamble.
- A PDF made by a computer has a text layer. Its words are exact, but its formulas are not (superscripts,
  fractions and symbols are flattened), so the page image is still read, with the text layer given beside it.
- Comments written on a PDF (sticky notes, highlights with a remark) are read from its annotations, with the
  highlighted words, for the referee's report."""
from __future__ import annotations

import ctypes
import io
import re
import zipfile
from pathlib import PurePosixPath

from PIL import Image, ImageDraw, ImageFont

SOURCE_EXT = (".tex", ".ltx", ".md", ".markdown", ".txt")
ASSET_EXT = (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".bib", ".sty", ".cls", ".bst")
PAGE_CHARS = 2600
# loaded by the paper itself: loading them again with other options would stop LaTeX with an option clash
_PAPER_HAS = {"amsmath", "amssymb", "amsthm", "amsfonts", "mathtools", "enumitem", "xcolor", "graphicx", "tikz", "hyperref"}
_MATH_PKGS = {"amsmath", "amssymb", "amsfonts", "mathtools", "mathrsfs", "bm", "bbm", "dsfont", "stmaryrd", "tikz", "tikz-cd",
              "pgfplots", "xy", "cancel", "mathdots", "esint", "braket", "physics", "siunitx", "upgreek", "eucal", "euscript",
              "nicefrac", "xfrac", "centernot", "tensor", "ytableau", "mleftright", "extarrows", "graphicx", "xcolor",
              "enumitem", "mathabx", "accents", "faktor", "quiver", "commath", "bbold", "calrsfs", "eufrak", "yfonts"}


def is_typed(name: str, data: bytes = b"") -> bool:
    low = name.lower()
    if low.endswith(SOURCE_EXT):
        return True
    if low.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return any(n.lower().endswith(SOURCE_EXT) for n in zf.namelist())
        except zipfile.BadZipFile:
            return False
    return False


def unpack(files: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """Every file, with the contents of .zip archives, under safe relative names."""
    tree: dict[str, bytes] = {}
    for name, data in files:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    rel = _safe(info.filename)
                    if rel and not info.is_dir() and info.file_size < 30_000_000:
                        tree[rel] = zf.read(info)
        else:
            rel = _safe(name)
            if rel:
                tree[rel] = data
    roots = {r.split("/")[0] for r in tree}
    if len(roots) == 1 and all("/" in r for r in tree):            # an Overleaf zip may wrap everything in one folder
        tree = {r.split("/", 1)[1]: d for r, d in tree.items()}
    return tree


def _safe(name: str) -> str:
    parts = [x for x in PurePosixPath(str(name).replace("\\", "/")).parts if x not in ("", ".", "/")]
    if not parts or any(x == ".." or x.startswith(".") or x == "__MACOSX" for x in parts) or len(parts) > 6:
        return ""
    return "/".join(parts)


def text_of(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def main_tex(tree: dict[str, bytes]) -> str | None:
    texs = [r for r in tree if r.lower().endswith((".tex", ".ltx"))]
    with_class = [r for r in texs if re.search(r"\\documentclass", strip_comments(text_of(tree[r])))]
    for pool in (with_class, texs):
        if pool:
            return sorted(pool, key=lambda r: (PurePosixPath(r).name.lower() != "main.tex", r.count("/"), -len(tree[r])))[0]
    return None


def expand_inputs(tex: str, tree: dict[str, bytes], depth: int = 0) -> str:
    """\\input and \\include replaced by the files they name (as LaTeX would read them)."""
    if depth > 5:
        return tex

    def put(m):
        name = m.group(2).strip()
        for cand in (name, name + ".tex"):
            if cand in tree:
                return "\n" + expand_inputs(strip_comments(text_of(tree[cand])), tree, depth + 1) + "\n"
        return m.group(0)
    return re.sub(r"\\(input|include|subfile)\{([^}]+)\}", put, tex)


def _group(s: str, i: int) -> tuple[str, int]:
    """The brace group starting at s[i] == '{': (contents, index after it)."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        depth += s[j] == "{"
        depth -= s[j] == "}"
        j += 1
        if depth == 0:
            return s[i + 1:j - 1], j
    return s[i + 1:], len(s)


def preamble_parts(preamble: str) -> tuple[list[str], dict[str, str]]:
    """The author's definitions as lines that work in any preamble (an existing command is redefined, not
    refused), and the same macros for the on-screen preview."""
    lines, katex = [], {}
    pre = strip_comments(preamble)
    for m in re.finditer(r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*(\{\\[A-Za-z]+\}|\\[A-Za-z]+)"
                         r"\s*(\[\d\])?\s*(\[[^\]]*\])?\s*(?=\{)", pre):
        name = m.group(2).strip("{}")
        body, _ = _group(pre, m.end())
        args = m.group(3) or ""
        lines.append(f"\\providecommand{{{name}}}{{}}\\renewcommand{{{name}}}{args}{m.group(4) or ''}{{{body}}}")
        if not m.group(4):
            katex[name] = body
    for m in re.finditer(r"\\DeclareMathOperator(\*?)\s*\{?(\\[A-Za-z]+)\}?\s*(?=\{)", pre):
        body, _ = _group(pre, m.end())
        op = "\\operatorname*" if m.group(1) else "\\operatorname"
        lines.append(f"\\providecommand{{{m.group(2)}}}{{}}\\renewcommand{{{m.group(2)}}}{{{op}{{{body}}}}}")
        katex[m.group(2)] = f"{op}{{{body}}}"
    for m in re.finditer(r"\\def\s*(\\[A-Za-z]+)\s*(?=\{)", pre):
        body, _ = _group(pre, m.end())
        lines.append(f"\\def{m.group(1)}{{{body}}}")
        katex[m.group(1)] = body
    for m in re.finditer(r"\\usepackage\s*(\[[^\]]*\])?\s*\{([^}]+)\}", pre):
        keep = [x.strip() for x in m.group(2).split(",") if x.strip() in _MATH_PKGS and x.strip() not in _PAPER_HAS]
        if keep:
            lines.append(f"\\usepackage{m.group(1) or ''}{{{','.join(keep)}}}")
    for m in re.finditer(r"\\usetikzlibrary\s*\{([^}]+)\}", pre):
        lines.append(f"\\usetikzlibrary{{{m.group(1)}}}")
    pkgs = [ln for ln in lines if ln.startswith("\\usepackage")]
    return pkgs + [ln for ln in lines if not ln.startswith("\\usepackage")], katex


def split_document(tex: str) -> tuple[str, str, str]:
    """(preamble, body, title) of a LaTeX source; a file without \\documentclass is all body."""
    tex = strip_comments(tex)
    m = re.search(r"\\begin\{document\}", tex)
    if not m:
        return "", tex, ""
    pre, body = tex[:m.start()], tex[m.end():]
    body = re.split(r"\\end\{document\}", body)[0]
    t = re.search(r"\\title\s*(\[[^\]]*\])?\s*\{", pre)
    title = _group(pre, t.end() - 1)[0].strip() if t else ""
    body = re.sub(r"\\(maketitle|tableofcontents|newpage|clearpage)\b", "", body)
    body = re.sub(r"\\(bibliographystyle|bibliography)\{[^}]*\}", "", body)
    return pre, body.strip(), re.sub(r"\s+", " ", title)


def chunks(body: str, size: int = PAGE_CHARS) -> list[str]:
    """Pages of about `size` characters, cut only between paragraphs and never inside an environment or a display."""
    paras = re.split(r"\n\s*\n", body.strip())
    out, cur, depth, disp = [], [], 0, False
    for para in paras:
        cur.append(para)
        depth += len(re.findall(r"\\begin\{(?!document)", para)) - len(re.findall(r"\\end\{(?!document)", para))
        disp ^= para.count("$$") % 2 == 1
        disp = disp if "\\[" not in para or "\\]" in para else True
        if "\\]" in para and "\\[" not in para:
            disp = False
        if sum(len(x) for x in cur) >= size and depth <= 0 and not disp:
            out.append("\n\n".join(cur).strip())
            cur, depth = [], 0
    if cur:
        out.append("\n\n".join(cur).strip())
    return [c for c in out if c]


_FONT = None


def _font(size: int = 22):
    global _FONT
    if _FONT is None:
        for path in ("DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc"):
            try:
                _FONT = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            try:
                _FONT = ImageFont.load_default(size=size)
            except TypeError:
                _FONT = ImageFont.load_default()
    return _FONT


def render(text: str, n: int, kind: str) -> Image.Image:
    """An image of a typed page's source, for the screens that show pages."""
    wrapped = []
    for line in text.split("\n"):
        while len(line) > 88:
            wrapped.append(line[:88])
            line = "    " + line[88:]
        wrapped.append(line)
    h = max(1754, 150 + 30 * len(wrapped))
    img = Image.new("RGB", (1240, h), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 40), f"Typed notes ({kind}), page {n}", fill=(120, 120, 120), font=_font(22))
    for i, line in enumerate(wrapped):
        d.text((60, 100 + 30 * i), line, fill=(20, 20, 20), font=_font(22))
    return img


def ingest_typed(files: list[tuple[str, bytes]], out_dir, start: int = 0) -> tuple[list[dict], dict]:
    """Pages from typed notes, and what the paper needs from them (macros, packages, the author's other files)."""
    from .ingest import _save
    out_dir.mkdir(parents=True, exist_ok=True)
    tree = unpack(files)
    main = main_tex(tree)
    preamble_lines, katex, title, kind = [], {}, "", "text"
    if main:
        kind = "LaTeX"
        pre, body, title = split_document(expand_inputs(text_of(tree[main]), tree))
        preamble_lines, katex = preamble_parts(pre)
    else:
        srcs = sorted(r for r in tree if r.lower().endswith((".md", ".markdown", ".txt")))
        if not srcs:
            raise ValueError("No LaTeX, Markdown or text file was found in the upload.")
        kind = "Markdown" if any(r.lower().endswith((".md", ".markdown")) for r in srcs) else "text"
        body = "\n\n".join(text_of(tree[r]).strip() for r in srcs)
    parts = chunks(body)
    if not parts:
        raise ValueError("The file has no text after its preamble.")
    pages = []
    for i, text in enumerate(parts, start + 1):
        rec = _save(render(text, i, kind), out_dir, i)
        rec.update(typed=True, kind="typed notes", blank=False, decorative=False)
        pages.append(rec)
    assets = {r: d for r, d in tree.items() if r.lower().endswith(ASSET_EXT)}
    info = {"kind": kind, "main": main, "title": title, "pages": {str(start + i + 1): t for i, t in enumerate(parts)},
            "preamble": preamble_lines, "katex": katex, "assets": sorted(assets)}
    return pages, info


def save_assets(tree_files: list[tuple[str, bytes]], folder) -> None:
    """The author's figures and bibliography, for compiling the paper."""
    tree = unpack(tree_files)
    for rel, data in tree.items():
        if rel.lower().endswith(ASSET_EXT):
            path = folder / "source_files" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


# ------------------------------------------------------------------ PDFs made by a computer

def text_layers(data: bytes) -> dict[int, str]:
    """The text layer of each page of a PDF (1-based), where it has one worth having."""
    import pypdfium2 as pdfium
    out = {}
    doc = pdfium.PdfDocument(data)
    try:
        for i in range(len(doc)):
            t = doc[i].get_textpage().get_text_range()
            if len(re.sub(r"\s", "", t)) >= 60:
                out[i + 1] = t[:8000]
    finally:
        doc.close()
    return out


def pdf_comments(data: bytes) -> list[dict]:
    """Comments written on a PDF: sticky notes, and highlights, underlines or strike-outs with the words marked."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    kinds = {c.FPDF_ANNOT_TEXT: "note", c.FPDF_ANNOT_FREETEXT: "note", c.FPDF_ANNOT_HIGHLIGHT: "highlight",
             c.FPDF_ANNOT_UNDERLINE: "underline", c.FPDF_ANNOT_STRIKEOUT: "strike-out", c.FPDF_ANNOT_SQUIGGLY: "squiggly",
             c.FPDF_ANNOT_CARET: "insertion"}
    out = []
    doc = pdfium.PdfDocument(data)
    try:
        for i in range(len(doc)):
            page = doc[i]
            tp = None
            for k in range(c.FPDFPage_GetAnnotCount(page.raw)):
                annot = c.FPDFPage_GetAnnot(page.raw, k)
                try:
                    kind = kinds.get(c.FPDFAnnot_GetSubtype(annot))
                    if not kind:
                        continue
                    comment = _annot_string(c, annot, b"Contents")
                    marked = ""
                    if kind in ("highlight", "underline", "strike-out", "squiggly"):
                        tp = tp or page.get_textpage()
                        q = c.FS_QUADPOINTSF()
                        words = []
                        for j in range(c.FPDFAnnot_CountAttachmentPoints(annot)):
                            if c.FPDFAnnot_GetAttachmentPoints(annot, j, q):
                                xs, ys = (q.x1, q.x2, q.x3, q.x4), (q.y1, q.y2, q.y3, q.y4)
                                words.append(tp.get_text_bounded(min(xs), min(ys), max(xs), max(ys)).strip())
                        marked = " ".join(w for w in words if w)
                    if comment.strip() or marked:
                        out.append({"page": i + 1, "kind": kind, "comment": comment.strip(), "marked": marked})
                finally:
                    c.FPDFPage_CloseAnnot(annot)
    finally:
        doc.close()
    return out


def _annot_string(c, annot, key: bytes) -> str:
    n = c.FPDFAnnot_GetStringValue(annot, key, None, 0)
    if n <= 2:
        return ""
    buf = ctypes.create_string_buffer(n)
    c.FPDFAnnot_GetStringValue(annot, key, ctypes.cast(buf, ctypes.POINTER(c.FPDF_WCHAR)), n)
    return buf.raw[:n - 2].decode("utf-16-le", errors="replace")


def comments_text(items: list[dict]) -> str:
    if not items:
        return ""
    lines = []
    for x in items:
        on = f' on "{x["marked"]}"' if x["marked"] else ""
        lines.append(f"- Page {x['page']} ({x['kind']}{on}): {x['comment'] or '(marked without a comment)'}")
    return "COMMENTS WRITTEN ON THE PDF\n" + "\n".join(lines)
