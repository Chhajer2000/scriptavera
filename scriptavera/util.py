# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Small helpers: token estimates and tolerant parsing of model output.

Models write LaTeX, and LaTeX inside JSON is fragile (``\\frac`` becomes a
form-feed, ``\\nu`` a newline). So anything carrying mathematics is exchanged in
a tagged plain-text format, and JSON is only used for math-free data and is
repaired before parsing.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata


def now() -> float:
    return time.time()


def est_tokens(text: str) -> int:
    """Rough token count. LaTeX is denser than prose, so divide by 3."""
    return int(len(text or "") / 3) + 1


def fit(text: str, max_chars: int, where: str = "middle") -> str:
    """Trim text to max_chars, saying so explicitly."""
    text = text or ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if where == "head":
        return text[:max_chars] + "\n[... trimmed ...]"
    if where == "tail":
        return "[... trimmed ...]\n" + text[-max_chars:]
    half = max_chars // 2
    return text[:half] + "\n[... trimmed ...]\n" + text[-half:]


def strip_think(text: str) -> str:
    """Remove reasoning blocks some models put in the visible answer."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I) if "</think>" in text.lower() else text
    return text.strip()


def strip_fences(text: str) -> str:
    """Drop a surrounding ```lang ... ``` fence if the whole answer is fenced."""
    t = (text or "").strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", t, flags=re.S)
    return m.group(1).strip() if m else t


# ---------------------------------------------------------------- tagged text

def extract_tag(text: str, tag: str, default: str = "") -> str:
    """Content of <tag>...</tag>. Tolerates a missing closing tag."""
    if not text:
        return default
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", text, flags=re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(rf"<{tag}\b[^>]*>(.*)", text, flags=re.S | re.I)
    if m:
        body = m.group(1)
        nxt = re.search(r"\n<[a-zA-Z_]+>\s*\n|\n</?[a-zA-Z_]+>\s*$", body)
        return (body[: nxt.start()] if nxt else body).strip()
    return default


def extract_all(text: str, tag: str) -> list[str]:
    return [m.strip() for m in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", text or "", flags=re.S | re.I)]


def parse_rows(block: str, sep: str = "||", min_cols: int = 1) -> list[list[str]]:
    """Lines of 'a || b || c' into lists. Leading bullets and numbering are dropped."""
    rows = []
    for line in (block or "").splitlines():
        s = line.strip()
        if not s or s.lower() in ("none", "(none)", "n/a", "-"):
            continue
        s = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", s)
        cols = [c.strip() for c in s.split(sep)]
        if len(cols) >= min_cols and any(cols):
            rows.append(cols)
    return rows


def parse_records(text: str, tag: str = "entry") -> list[dict]:
    """<entry> blocks of 'key: value' lines into dicts. Continuation lines extend the last key."""
    out = []
    for body in extract_all(text, tag):
        rec: dict[str, str] = {}
        last = None
        for line in body.splitlines():
            if not line.strip():
                continue
            m = re.match(r"^\s*([A-Za-z_][A-Za-z_ ]{0,24}?)\s*:\s?(.*)$", line)
            if m and m.group(1).strip().lower().replace(" ", "_") not in rec:
                last = m.group(1).strip().lower().replace(" ", "_")
                rec[last] = m.group(2).strip()
            elif last:
                rec[last] += "\n" + line.strip()
        if rec:
            out.append(rec)
    return out


def parse_list(block: str) -> list[str]:
    """Bulleted, numbered, comma- or semicolon-separated items into a clean list."""
    block = (block or "").strip()
    if not block or block.lower() in ("none", "(none)", "n/a"):
        return []
    if "\n" in block:
        items = [re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", l.strip()) for l in block.splitlines()]
    else:
        items = re.split(r"\s*[;,]\s*", block)
    seen, out = set(), []
    for it in items:
        it = it.strip().strip('"').strip()
        if it and it.lower() not in seen and it.lower() not in ("none", "n/a"):
            seen.add(it.lower())
            out.append(it)
    return out


def parse_lines(block: str) -> list[str]:
    """One item per line (bullets and numbering dropped). For items that are sentences: a comma inside an item
    is part of it, never a separator."""
    block = (block or "").strip()
    if not block or block.lower().strip(".") in ("none", "(none)", "n/a"):
        return []
    seen, out = set(), []
    for line in block.splitlines():
        it = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", line.strip()).strip()
        if it and it.lower() not in seen and it.lower().strip(".") not in ("none", "(none)", "n/a"):
            seen.add(it.lower())
            out.append(it)
    return out


def to_float(value, default: float = 0.0) -> float:
    try:
        frac = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)", str(value))
        if frac and float(frac.group(2)) > 0:
            return max(0.0, min(1.0, float(frac.group(1)) / float(frac.group(2))))
        m = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not m:
            return default
        f = float(m.group(0))
        if "%" in str(value) or f > 1.0:
            f = f / 100.0 if f > 1.0 else f
        return max(0.0, min(1.0, f))
    except Exception:
        return default


# ---------------------------------------------------------------------- JSON

# Letter runs after a backslash that start with a JSON escape letter (b f n r t u)
# but are really LaTeX commands.
_LATEX_WORDS = set("""
bar beta begin bf big bigg bigl bigr bigcap bigcup bigoplus bigotimes bigsqcup bigvee bigwedge binom bmod
boldsymbol bot bowtie box boxed breve bullet backslash because between bmatrix
frac forall flat frak frown footnote fbox
nu ne neq neg nabla ni nmid not notin nleq ngeq nless ngtr nsubseteq nsupseteq nolimits nonumber newline
nearrow nwarrow natural ncong nexists nparallel nprec nsim nsucc ntriangleleft ntrianglelefteq
rho right rangle rightarrow rightleftharpoons rfloor rceil ref rm restriction rtimes rvert rVert rightharpoonup
rbrace rbrack rightsquigarrow
tau theta to times text textbf textit textrm texttt textsf tfrac tilde top triangle triangleleft
triangleright trianglelefteq trianglerighteq therefore tag tan tanh tbinom thicksim thickapprox
twoheadrightarrow textstyle textcircled
uparrow updownarrow upsilon underline underbrace underset uplus unlhd unrhd upharpoonright
""".split())


def _fix_backslashes(s: str) -> str:
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = s[i + 1] if i + 1 < n else ""
        if nxt == "\\":
            out.append("\\\\")
            i += 2
            continue
        if nxt in '"/':
            out.append(c + nxt)
            i += 2
            continue
        if nxt in "bfnrtu":
            m = re.match(r"[A-Za-z]+", s[i + 1:])
            word = m.group(0) if m else ""
            if nxt == "u" and re.match(r"u[0-9a-fA-F]{4}", s[i + 1:]) and word not in _LATEX_WORDS:
                out.append(c)
                i += 1
                continue
            if word in _LATEX_WORDS:
                out.append("\\\\")
                i += 1
                continue
            if nxt == "u":
                out.append("\\\\")
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        # any other character after a backslash is not a valid JSON escape
        out.append("\\\\")
        i += 1
    return "".join(out)


def loads_lenient(text: str):
    """Parse JSON from a model answer; repairs fences, LaTeX backslashes, trailing commas."""
    t = strip_fences(strip_think(text or ""))
    start = min([p for p in (t.find("{"), t.find("[")) if p >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON object in model output")
    end = max(t.rfind("}"), t.rfind("]"))
    if end <= start:
        raise ValueError("unterminated JSON in model output")
    body = t[start:end + 1]
    attempts = [body, _fix_backslashes(body)]
    attempts.append(re.sub(r",\s*([}\]])", r"\1", attempts[-1]))
    last_err: Exception | None = None
    for cand in attempts:
        try:
            return json.loads(cand, strict=False)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise ValueError(f"could not parse JSON from model output: {last_err}")


# --------------------------------------------------------------------- misc

def ascii_fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch))


def slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", ascii_fold(s)).strip("-").lower()
    return s[:max_len] or "untitled"


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", ascii_fold(s).lower())


def strip_markup(s: str) -> str:
    """Remove HTML/JATS/MathML tags found in bibliographic metadata."""
    s = re.sub(r"<mml:math.*?</mml:math>", " ", s or "", flags=re.S)
    s = re.sub(r"</?[A-Za-z][\w:.-]*(\s[^<>]*)?/?>", "", s)       # real tags only; "a < b and c > d" stays
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'")
    return re.sub(r"\s+", " ", s).strip()
