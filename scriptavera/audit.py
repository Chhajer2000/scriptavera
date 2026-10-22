# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Fidelity audit: did every formula in the approved transcript reach the paper unchanged?

This is plain string comparison, not a model's opinion. Each formula from the
author's notes is normalised (spacing, delimiters and synonymous commands
removed) and looked up in the typeset body.
"""

from __future__ import annotations

import difflib
import re

from .texutil import drop_brace_labels, split_math

_SYNONYMS = [
    (r"\\leqslant|\\leq\b", r"\\le"), (r"\\geqslant|\\geq\b", r"\\ge"), (r"\\neq\b", r"\\ne"),
    (r"\\Longrightarrow|\\Rightarrow|\\implies", "⇒"), (r"\\Longleftrightarrow|\\Leftrightarrow|\\iff", "⇔"),
    (r"\\longrightarrow|\\rightarrow|\\to\b", "→"), (r"\\[dt]frac", r"\\frac"), (r"\\[dt]binom", r"\\binom"),
    (r"\\lvert|\\rvert|\\vert\b|\\mid\b", "|"), (r"\\lVert|\\rVert|\\Vert\b", "‖"), (r"\\colon", ":"),
    (r"\\ldots|\\cdots|\\dots[bcmio]?\b|\.\.\.", "…"), (r"\\Bbb\b", r"\\mathbb"), (r"\\varphi", r"\\phi"),
    (r"\\varepsilon", r"\\epsilon"), (r"\\triangleleft|\\lhd", "◁"), (r"\\trianglelefteq|\\unlhd", "⊴"),
    (r"\\operatorname\*?", ""), (r"\\mathrm|\\text(?:rm|normal|up)?|\\mbox", r"\\text"),
    (r"\\langle", "⟨"), (r"\\rangle", "⟩"), (r"\\cdot\b", "·"), (r"\\times\b", "×"), (r"\\lt\b", "<"), (r"\\gt\b", ">"),
    (r"\\nmid", "∤"), (r"\\therefore", "∴"), (r"\\land\b", r"\\wedge"), (r"\\lor\b", r"\\vee"),
    (r"\\emptyset|\\varnothing", "∅"), (r"\\setminus|\\smallsetminus", "∖"), (r"\\ast\b", "*"),
]
_DROP = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg|bigl|bigr|Bigl|Bigr|displaystyle|textstyle|nonumber|notag|limits|quad|qquad)"
                   r"(?![A-Za-z])|\\[,;:! ]")


def normalize(eq: str) -> str:
    s = drop_brace_labels(eq or "")
    s = re.sub(r"\\label\{[^{}]*\}|\\tag\{[^{}]*\}", "", s)
    s = re.sub(r"\\(?:left|right)\.", "", s)          # null delimiters, as in a formula broken over lines
    s = re.sub(r"\\begin\{[a-z*]+\}(\{[^{}]*\})?|\\end\{[a-z*]+\}", "", s)
    s = _DROP.sub("", s)
    for pat, rep in _SYNONYMS:
        s = re.sub(pat, rep, s)
    s = s.replace("&", "").replace("~", "").replace("\\\\", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\{([A-Za-z0-9]|\\[A-Za-z]+)\}", r"\1", s)   # x^{2} and x^2 are the same formula
    s = re.sub(r"\\text\{([^{}]*)\}", lambda m: "\\text{" + m.group(1).lower() + "}", s)
    s = s.strip(".,;:")
    s = re.sub(r"^(?:⇒|∴|⇔)+", "", s)                   # a leading "implies" is connective tissue
    return s


def _lines(tex: str) -> list[str]:
    """Split a displayed formula into its lines."""
    body = re.sub(r"\\begin\{(aligned|align\*?|gathered|gather\*?|split|equation\*?|multline\*?|multlined|eqnarray\*?)\}", "", tex)
    body = re.sub(r"\\end\{(aligned|align\*?|gathered|gather\*?|split|equation\*?|multline\*?|multlined|eqnarray\*?)\}", "", body)
    if re.search(r"\\begin\{(cases|pmatrix|bmatrix|matrix|array|vmatrix)", body):
        return [body]                                     # a matrix or case split is one object
    return [l for l in re.split(r"\\\\(?:\[[^\]]*\])?", body) if l.strip()]


_REL = re.compile(r"=|<|>|\\le|\\ge|\\ne|\\in\b|\\cong|\\equiv|\\sim|\\mid|\\nmid|\\subset|\\approx|\\to\b|\\implies|\\Rightarrow|≤|≥|⇒")


def formulas(text: str, inline: bool = True) -> list[dict]:
    """Formulas worth checking: every displayed line, and inline formulas that state a relation."""
    out = []
    for kind, c in split_math(text or ""):
        if kind in ("display", "env"):
            for l in _lines(c):
                n = normalize(l)
                if len(n) >= 3:
                    out.append({"tex": l.strip(), "norm": n, "display": True})
        elif kind == "inline" and inline:
            n = normalize(c)
            if len(n) >= 7 and _REL.search(c):
                out.append({"tex": c.strip(), "norm": n, "display": False})
    return out


def _partial(a: str, b: str) -> float:
    """Similarity of the shorter formula to the best-aligned window of the longer one."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 14 or len(short) < 0.45 * len(long_):
        return 0.0
    sm = difflib.SequenceMatcher(None, short, long_, autojunk=False)
    best = 0.0
    for blk in sm.get_matching_blocks():
        start = max(0, blk.b - blk.a)
        window = long_[start:start + len(short)]
        best = max(best, difflib.SequenceMatcher(None, short, window, autojunk=False).ratio())
    return best


_BOUNDARY = {"=", "<", ">", "\\le", "\\ge", "\\ne", "⇒", "⇔", "→", ",", ";", ":", "∴", "\\cong", "\\equiv", "\\sim",
             "\\approx", "\\in", "\\subset", "\\subseteq", "\\ll", "\\gg", "\\mapsto", "\\iff", "∤"}
_PUNCT = {",", ".", ";", ":", "{", "}"}
_SPLIT = re.compile(r"⇒|⇔|∴|\\forall|\\text\{[^{}]*\}")


def _tok(s: str) -> list[str]:
    return re.findall(r"\\[A-Za-z]+|.", s)


def _pieces(n: str) -> list[str]:
    """A formula cut where a writer may legitimately turn symbols into words (implies, for all, 'or')."""
    return [x.strip(".,;:") for x in _SPLIT.split(n) if len(x.strip(".,;:")) >= 2]


def _inside(needle: list[str], hay: list[str]) -> bool:
    """needle occurs in hay as a complete sub-formula: bounded by the ends or by a relation or comma, so that
    'a = b' is found in 'a = b = c' but not in 'a = b + 1' or 'a = b^2'."""
    k = len(needle)
    if not k or k > len(hay):
        return False
    for i in range(len(hay) - k + 1):
        if hay[i:i + k] != needle:
            continue
        left = i == 0 or hay[i - 1] in _BOUNDARY or needle[0] in _BOUNDARY
        right = i + k == len(hay) or hay[i + k] in _BOUNDARY
        if left and right:
            return True
    return False


def _only_punctuation_differs(a: list[str], b: list[str]) -> bool:
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal" and not set(a[i1:i2] + b[j1:j2]) <= _PUNCT:
            return False
    return True


class _Index:
    """Every formula of one text, whole and in pieces, ready for lookups."""

    def __init__(self, text: str):
        self.items = []
        for kind, c in split_math(text or ""):
            for l in (_lines(c) if kind in ("display", "env") else [c] if kind == "inline" else []):
                n = normalize(l)
                if n:
                    self.items.append({"tex": l.strip(), "norm": n, "display": kind != "inline"})
        self.wholes = [(it["norm"], _tok(it["norm"])) for it in self.items]
        strings = {n for n, _ in self.wholes}
        for kind, c in split_math(text or ""):       # a display broken over lines is also the one formula it was
            if kind in ("display", "env") and len(_lines(c)) > 1:
                joined = normalize(c)
                if joined:
                    strings.add(joined)
        for n, _ in self.wholes:
            strings.update(_pieces(n))
        self.strings = strings
        self.tokens = [_tok(x) for x in strings]

    def has(self, n: str) -> bool:
        if n in self.strings:
            return True
        if len(n) < 3:
            return False
        t = _tok(n)
        return any(_inside(t, h) for h in self.tokens)

    def covers(self, n: str) -> bool:
        """The formula appears, possibly as several lines or with its connectives turned into words."""
        if self.has(n):
            return True
        parts = _pieces(n)
        if len(parts) > 1 and all(self.has(x) for x in parts):
            return True
        src = _tok(n)                                       # one long chain typeset over several lines
        covered = [False] * len(src)
        for _, t in self.wholes:
            k = len(t)
            if k < 3 or k >= len(src):
                continue
            for i in range(len(src) - k + 1):
                if src[i:i + k] == t and (i == 0 or src[i - 1] in _BOUNDARY or t[0] in _BOUNDARY) \
                        and (i + k == len(src) or src[i + k] in _BOUNDARY):
                    covered[i:i + k] = [True] * k
        return bool(src) and sum(covered) >= 0.9 * len(src)

    def nearest(self, n: str) -> tuple[float, dict | None, bool]:
        """(similarity, closest formula, differs only in punctuation)."""
        t = _tok(n)
        best, item, punct = 0.0, None, False
        for it, (wn, wt) in zip(self.items, self.wholes):
            if abs(len(wt) - len(t)) > max(6, 0.5 * len(t)):
                r = _partial(n, wn) * 0.99 if _partial(n, wn) >= 0.7 else 0.0
            else:
                r = difflib.SequenceMatcher(None, t, wt, autojunk=False).ratio()
                if r < 0.75:
                    r = max(r if r >= 0.75 else 0.0, _partial(n, wn) if _partial(n, wn) >= 0.7 else 0.0)
            if r > best:
                best, item = r, it
                punct = _only_punctuation_differs(t, wt)
        return best, item, punct


def audit(pages: list[dict], body_latex: str, waived: str = "", approved: str = "") -> dict:
    """pages: [{n, text}] approved transcript. Returns the comparison report.

    A formula counts as preserved only if it is found token for token (formatting aside). Any other
    difference, however small (a sign, an index, the direction of an inequality), is reported.

    `waived` is text that the author agreed to have replaced (a proof shortened or corrected with their consent):
    a formula of the notes that is gone because of that is counted as changed with approval, not as missing.
    `approved` is the text put in its place: its formulas are not reported as additions."""
    body = _Index(body_latex)
    gone = _Index(waived) if waived.strip() else None
    exact = close = total = 0
    altered, missing, agreed = [], [], []
    for pg in pages:
        for f in formulas(pg.get("text", "")):
            total += 1
            n = f["norm"]
            if body.covers(n):
                exact += 1
                continue
            score, item, punct = body.nearest(n)
            if item is not None and punct:
                close += 1
            elif gone is not None and gone.covers(n):
                agreed.append({"page": pg["n"], "source": f["tex"], "display": f["display"]})
            elif item is not None and score >= 0.7:
                altered.append({"page": pg["n"], "source": f["tex"], "found": item["tex"],
                                "ratio": round(score, 2), "display": f["display"]})
            else:
                missing.append({"page": pg["n"], "source": f["tex"], "display": f["display"]})
    notes = _Index("\n".join(pg.get("text", "") for pg in pages))
    notes_joined = "\x01".join(n for n, _ in notes.wholes)
    ok_new = _Index(approved) if approved.strip() else None
    added = []
    for it in body.items:
        if not it["display"] or len(it["norm"]) < 3:
            continue
        n = it["norm"]
        if notes.covers(n) or n in notes_joined:
            continue
        score, _, punct = notes.nearest(n)
        if punct or score >= 0.8:
            continue                                         # a variant of a formula in the notes; reported above if changed
        if ok_new is not None and ok_new.covers(n):
            continue                                         # part of a change the author accepted
        added.append({"tex": it["tex"]})
    preserved = exact + close
    return {
        "total": total, "exact": exact, "close": close, "preserved": preserved,
        "altered": altered, "missing": missing, "added": added, "agreed": agreed,
        "score": round(preserved / total, 4) if total else 1.0,
    }
