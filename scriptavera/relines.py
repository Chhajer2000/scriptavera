# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The author's own lines and notes, for a page as it was read.

Step 9 shows the notes as the author reads them: line under line as the page has them, a gap where the page has one,
headings as headings, and each note with the words it belongs to. A transcription sometimes runs lines the author
wrote one under the other together into one paragraph; it never says which lines are headings; and it may put a note
(anything written apart from the main lines: in a margin, above or below a line, joined to it by an arrow or a brace)
somewhere else than with the words it belongs to, at the end of the page or beside another line, which changes what
the page says. So each page read is shown once to a model, with its transcription, and the model writes the
transcription out again line for line, each note right after the words it belongs to, marking the headings.

Only the shape is taken from the answer. Its words are matched with the words of the transcription, in order, and the
transcription changes in two ways only: in the space between two of its words (a line break, an empty line or an
indent goes there where the answer has one; a line break the page has is never taken away), and in where a note
stands (a note goes after the word the answer puts it after). Never inside a formula, a drawing, a note, a doubtful
reading or a group in braces; never a word, a formula or a note's own words: what the page says cannot change, and a
note is moved only whole. The answer is kept beside the page image and put into the page once; a page read again is
put into shape from the same answer, without asking again, as long as its words are still mostly those the model saw.

The rules for where a note goes are general (the transcription is asked for the same: prompts.FINAL), never fitted to
one author's pages. It runs in the background, one request per page, when step 9 asks for it; the page is shown as it
is meanwhile. It can be turned off in Settings (and for one paper, with the paper's layout_ai)."""
from __future__ import annotations

import contextvars
import difflib
import hashlib
import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, prompts
from .llm import LLMError, router
from .util import extract_tag

VERSION = 2                  # 2: notes are put with the words they belong to; the answers of 1 are asked again
GATE = 0.75                  # the answer must hold at least this share of the page's words, in order, to be used
NOTE_LIKE = 0.85             # a note in the answer is a note of the page when their words are at least this alike
RETRY_AFTER = 5 * 60         # a page whose request failed (no key free, an unusable answer) is asked again after this
WAITS = (20, 60)             # ... but first asked again in the background, after these pauses (seconds)
MAX_INDENT = 4               # steps of indent taken from an answer
HEAD_WORDS = 14              # a heading is a short line

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scriptavera-lines")
_busy: set[tuple[str, int]] = set()
_lock = threading.Lock()

_MATH = re.compile(r"\$\$.+?\$\$|\$[^$]+?\$", re.S)
_DOUBT = re.compile(r"\[\?.*?\?\]", re.S)
_ENV = re.compile(r"\\begin\{([A-Za-z*]+)\}.*?\\end\{\1\}", re.S)
_MARKER = re.compile(r"\[(?:FIGURE(?:\s+f\d+)?|MARGIN|EXPLANATORY DRAWING)\s*:")
_HEAD_TAG = re.compile(r"^\s*<h>\s*", re.I)
_HEAD_END = re.compile(r"</h>", re.I)


def enabled(p) -> bool:
    """Whether the lines of a paper's pages are asked for: the paper's own choice, else the setting (on by default)."""
    own = p.state.get("layout_ai")
    if own is not None:
        return bool(own)
    return bool(config.load_settings().get("layout_ai", True))


def _key(text: str) -> str:
    return hashlib.sha256(f"{VERSION}|{text}".encode("utf-8")).hexdigest()[:16]


def _path(p, n: int) -> Path:
    return p.dir / "pages" / f"lines_p{int(n):03d}.json"


def answer(p, n: int) -> dict | None:
    """What was asked for page n: {"key", "out"} (the page written out line for line) or {"key", "failed"}. The key
    is that of the text the model was given. An answer to an earlier version of the request does not count."""
    f = _path(p, n)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) and data.get("v") == VERSION else None


def _save(p, n: int, data: dict) -> None:
    _path(p, n).write_text(json.dumps({**data, "v": VERSION, "at": time.time()}, ensure_ascii=False), encoding="utf-8")


def busy(p, n: int) -> bool:
    with _lock:
        return (p.id, int(n)) in _busy


# ------------------------------------------------------------------ the answer put into the page

def squash(s: str) -> str:
    """A line as the screen finds it again: without spaces and dollar signs."""
    return re.sub(r"\s+|\$", "", s or "")


def _tokens(text: str) -> list[dict]:
    """The page in its smallest pieces, in order: a drawing or a note whole (its brackets matched, kind "fig" or
    "note"), and otherwise a run of characters without space (kind "word"). Each with where it starts and ends."""
    out, i, n = [], 0, len(text or "")
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        m = _MARKER.match(text, i)
        if m:
            depth, j = 1, m.end()
            while j < n and depth:
                depth += {"[": 1, "]": -1}.get(text[j], 0)
                j += 1
            if not depth:
                out.append({"a": i, "b": j, "t": text[i:j], "kind": "note" if text.startswith("[MARGIN", i) else "fig"})
                i = j
                continue
        j = i + 1
        while j < n and not text[j].isspace() and not (text[j] == "[" and _MARKER.match(text, j)):
            j += 1
        out.append({"a": i, "b": j, "t": text[i:j], "kind": "word"})
        i = j
    return out


def _same(t: dict) -> str:
    """What two pieces must share to be the same piece: a word itself; a drawing or a note, its words."""
    return t["t"] if t["kind"] == "word" else " ".join(t["t"].split())


def _shielded(text: str) -> list[bool]:
    """For each place in the text, whether nothing may be put there: inside a formula, a drawing or a note, a
    doubtful reading, an environment or a group in braces."""
    inside = [False] * (len(text) + 1)

    def cover(a: int, b: int) -> None:                 # the places after a and before b
        for i in range(a + 1, min(b, len(text))):
            inside[i] = True

    for rx in (_MATH, _DOUBT, _ENV):
        for m in rx.finditer(text):
            cover(m.start(), m.end())
    for t in _tokens(text):
        if t["kind"] != "word":
            cover(t["a"], t["b"])
    depth = 0
    for i, ch in enumerate(text):
        if depth > 0:
            inside[i] = True
        if ch in "{}" and (i == 0 or text[i - 1] != "\\"):
            depth = max(0, depth + (1 if ch == "{" else -1))
    return inside


def _answer_lines(out: str) -> list[dict]:
    """The lines of a model's answer: for each, its pieces (_tokens), its indent in steps, whether an empty line comes
    before it, whether it was marked as a heading, and (first) how many of its first pieces the heading is when it is
    not the whole line (0: the whole line)."""
    lines, gap = [], False
    for raw in (out or "").replace("\r", "").split("\n"):
        if raw.strip().startswith("```"):
            continue
        if not raw.strip():
            gap = bool(lines)
            continue
        lead = len(raw) - len(raw.lstrip(" \t"))
        head = bool(_HEAD_TAG.match(raw))
        if head and lead == 0:                             # "<h>  text": the indent after the mark
            rest = re.sub(r"^\s*<h>", "", raw, flags=re.I)
            lead = len(rest) - len(rest.lstrip(" \t"))
        body = _HEAD_TAG.sub("", raw)
        first = 0
        end = _HEAD_END.search(body)
        if head and end and body[end.end():].strip():      # "<h>Theorem</h> [Wu, 2024]": the first words only
            first = len(_tokens(body[:end.start()]))
        toks = _tokens(re.sub(r"</?h>", " ", body, flags=re.I))
        lines.append({"toks": toks, "indent": min(MAX_INDENT, lead // 2), "gap": gap, "head": head and bool(toks),
                      "first": first})
        gap = False
    return lines


def _empty() -> dict:
    return {"heads": [], "leads": {}}


def transfer(text: str, out: str) -> tuple[str, dict, float]:
    """The page's text in the shape of a model's answer: its line breaks, gaps and indents put in where its words meet
    the page's, each note moved (whole) to after the word the answer puts it after, and the headings the answer marked.
    Returns the new text; its format, {"heads": [lines that are headings], "leads": {line: its first words that are a
    heading}}, each as squash() gives it; and the share of the page's pieces found in the answer."""
    mine = _tokens(text)
    lines = _answer_lines(out)
    theirs: list[dict] = []                               # the answer's pieces, each with its line
    begins: dict[int, int] = {}                           # a line of the answer -> its first piece
    for li, ln in enumerate(lines):
        for k, t in enumerate(ln["toks"]):
            if k == 0:
                begins[li] = len(theirs)
            theirs.append({**t, "line": li, "starts": k == 0})
    if not mine or not theirs:
        return text, _empty(), 0.0
    inside = _shielded(text)

    # the words and drawings of both, matched in order
    core_m = [i for i, t in enumerate(mine) if t["kind"] != "note"]
    core_t = [j for j, t in enumerate(theirs) if t["kind"] != "note"]
    sm = difflib.SequenceMatcher(None, [_same(mine[i]) for i in core_m], [_same(theirs[j]) for j in core_t], autojunk=False)
    to_mine: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for d in range(size):
            to_mine[core_t[b + d]] = core_m[a + d]

    # the notes of both: which is which, and the word each stands after in the answer
    notes_m = [i for i, t in enumerate(mine) if t["kind"] == "note"]
    prev_core = {}                                        # a note of the page -> the word it stands after (-1: none)
    last = -1
    for i, t in enumerate(mine):
        if t["kind"] == "note":
            prev_core[i] = last
        else:
            last = i
    slot = dict(prev_core)                                # where each note will stand: after this word of the page
    rank = {i: (1, i) for i in notes_m}                   # the order of the notes after one word
    moved, newline_before, used = set(), {}, set()
    for j, t in enumerate(theirs):
        if t["kind"] != "note":
            continue
        cands = [i for i in notes_m if i not in used and _same(mine[i]) == _same(t)]
        if not cands:
            like = [(difflib.SequenceMatcher(None, _same(mine[i]), _same(t)).ratio(), i) for i in notes_m if i not in used]
            cands = [i for r, i in sorted(like, reverse=True) if r >= NOTE_LIKE][:1]
        if not cands:
            continue
        i = cands[0]
        used.add(i)
        to_mine[j] = i
        k = j - 1
        while k >= 0 and theirs[k]["kind"] == "note":
            k -= 1
        after = -1 if k < 0 else to_mine.get(k)
        if after is None or inside[mine[i]["a"]] or (after >= 0 and inside[mine[after]["b"]]):
            continue                                      # the word before it is not the page's, or no room: it stays
        rank[i] = (0, j)
        if after != slot[i]:
            slot[i], newline_before[i] = after, t["starts"]
            moved.add(i)
    share = len(to_mine) / len(mine)

    # the page in its new order: every word where it was, each note after its word
    at_slot: dict[int, list[int]] = {}
    for i in notes_m:
        at_slot.setdefault(slot[i], []).append(i)
    order = [i for i in sorted(at_slot.get(-1, []), key=lambda i: rank[i])]
    for c in core_m:
        order.append(c)
        order += sorted(at_slot.get(c, []), key=lambda i: rank[i])

    def gap_before(x: int, prev: int | None) -> str:
        if prev is None:
            return ""
        if prev == x - 1:                                  # neighbours on the page as well: the space between them
            return text[mine[prev]["b"]:mine[x]["a"]]
        if x in moved:                                    # a note put after another word: on its line, or below it
            return "\n" if newline_before.get(x) else " "
        lo = max([c for c in core_m if c < x] or [-1])    # a word whose neighbour moved away: the widest space it had
        gaps = [text[mine[y - 1]["b"]:mine[y]["a"]] for y in range(max(lo + 1, 1), x + 1)]
        return max(gaps, key=lambda g: (g.count("\n"), g == gaps[-1])) if gaps else " "

    gaps = {x: gap_before(x, order[k - 1] if k else None) for k, x in enumerate(order)}
    if order and mine[order[0]]["a"] > 0:
        gaps[order[0]] = text[:mine[order[0]]["a"]] if order[0] == 0 else ""

    # line breaks, gaps and indents where the answer's lines begin
    lead_first: dict[int, int] = {}                       # a line of the answer -> the page's piece it begins at
    for li, j in begins.items():
        ln, x = lines[li], to_mine.get(j)
        if x is None and j + 1 in to_mine and theirs[j + 1]["line"] == li and mine[to_mine[j + 1]]["kind"] != "note":
            x = to_mine[j + 1] - 1 if to_mine[j + 1] - 1 in core_m else None
        if x is None:
            continue
        lead_first[li] = x
        if mine[x]["kind"] == "note" or order.index(x) == 0 or inside[mine[x]["a"]]:
            continue
        old = gaps[x]
        breaks = max(old.count("\n"), 2 if ln["gap"] else 1)
        indent = len(old) - len(old.rstrip(" \t")) if "\n" in old else 0
        gaps[x] = "\n" * breaks + " " * (indent or 2 * ln["indent"])

    pos: dict[int, tuple[int, int]] = {}
    parts, length = [], 0
    for x in order:
        parts += [gaps[x], mine[x]["t"]]
        length += len(gaps[x])
        pos[x] = (length, length + len(mine[x]["t"]))
        length += len(mine[x]["t"])
    tail = text[mine[-1]["b"]:] if mine else ""
    new_text = "".join(parts) + (tail if not tail.strip() else "")
    again = _tokens(new_text)                             # never: a word changed, lost or moved, or a note changed
    if [t["t"] for t in again if t["kind"] != "note"] != [mine[i]["t"] for i in core_m] \
            or sorted(t["t"] for t in again if t["kind"] == "note") != sorted(mine[i]["t"] for i in notes_m):
        return text, _empty(), 0.0

    heads: list[str] = []
    leads: dict[str, str] = {}
    if any(ln["head"] for ln in lines):
        for li, x in lead_first.items():
            ln = lines[li]
            if not ln["head"] or mine[x]["kind"] == "note":
                continue
            s = new_text.rfind("\n", 0, pos[x][0]) + 1
            e = new_text.find("\n", pos[x][0])
            line = new_text[s:len(new_text) if e < 0 else e]
            if line.strip().startswith("$$") or _MARKER.match(line.strip()) or new_text[s:pos[x][0]].strip():
                continue                                  # a formula or a drawing is no heading; nor a line's middle
            n_words, k = len(_tokens(line)), ln["first"]
            if k:                                         # its first words only, when they are the page's own
                run = order[order.index(x):order.index(x) + k]
                if k < n_words and len(run) == k and all(mine[y]["kind"] == "word" for y in run) \
                        and all(to_mine.get(begins[li] + d) == run[d] for d in range(k)) and order.index(x) + k < len(order) \
                        and not inside[mine[order[order.index(x) + k]]["a"]]:
                    leads.setdefault(squash(line), squash(new_text[pos[run[0]][0]:pos[run[-1]][1]]))
            elif 0 < n_words <= min(HEAD_WORDS, len(ln["toks"]) + 2):
                if squash(line) not in heads:
                    heads.append(squash(line))
    return new_text, {"heads": heads, "leads": leads}, share


# ------------------------------------------------------------------ asking

def ask(p, n: int, text: str) -> str | None:
    """Ask a model that sees page n for its lines and notes. Returns the answer (the page written out line for line),
    or None when the answer cannot be used. The same model as reads the page: where a note belongs is part of what it
    says."""
    from .pipeline import Run, _page_bytes
    hooks = Run(p)
    hooks.stage = "review"
    user = f"PAGE {n}\n\nTHE TRANSCRIPTION OF THIS PAGE\n{text}"
    res = router.chat("vision_best", prompts.PAGE_LINES, user, images=[_page_bytes(p, n)],
                      max_out=min(16000, 3000 + len(text)), effort="low", label=f"Find the lines of page {n}", hooks=hooks)
    out = extract_tag(res.text, "lines") or res.text
    return out if transfer(text, out)[2] >= GATE else None


def start(p, n: int, text: str) -> None:
    """Ask for the lines of page n in the background, unless they are being asked for already."""
    k = (p.id, int(n))
    with _lock:
        if k in _busy:
            return
        _busy.add(k)

    def work():
        try:
            out = None
            for wait in (0, *WAITS):                      # an empty answer, a limit reached: asked again soon
                time.sleep(wait)
                try:
                    out = ask(p, n, text)
                except LLMError:
                    out = None
                except Exception:  # noqa: BLE001 - an unexpected answer: asked again like an empty one
                    traceback.print_exc()
                    out = None
                if out:
                    break
            _save(p, n, {"key": _key(text), "out": out} if out else {"key": _key(text), "failed": time.time()})
        except Exception:  # noqa: BLE001 - the page stays as it was read
            traceback.print_exc()
        finally:
            with _lock:
                _busy.discard(k)
    _pool.submit(contextvars.copy_context().run, work)       # the same owner's keys


def settle(p, pages: dict[int, str]) -> dict[int, dict]:
    """For each page step 9 shows, with the text it shows: "done" with the page's text and headings once a model's
    answer for it is in (put into the page the first time); "busy" while it is being asked for (a page seen for the
    first time is asked for now); "stale" when the page has changed on the server meanwhile; "none" when nothing is to
    be done (a typed page, turned off, no key, nothing usable)."""
    from .pipeline import hosted_blocked
    on, blocked, out = enabled(p), None, {}
    typed = (p.load("typed") or {}).get("pages", {})
    for n, text in sorted(pages.items()):
        if busy(p, n):
            out[n] = {"state": "busy"}
            continue
        if str(n) in typed or not (p.dir / "pages" / f"p{int(n):03d}.jpg").exists():
            out[n] = {"state": "none"}
            continue
        with p._lock:
            tr = p.load("transcript", {"pages": {}})
            rec = tr["pages"].get(str(n))
            if not rec or not str(rec.get("text") or "").strip():
                out[n] = {"state": "none"}
                continue
            if rec["text"] != text:
                out[n] = {"state": "stale"}
                continue
            got = answer(p, n) or {}
            if got.get("out") and rec.get("lined") == got.get("key"):
                out[n] = {"state": "done", "text": text, "format": rec.get("format") or {}}    # in the page already
                continue
            if got.get("out"):
                new, fmt, share = transfer(text, got["out"])
                if share >= GATE:
                    rec["text"], rec["format"], rec["lined"] = new, fmt, got["key"]
                    p.save("transcript", tr)
                    out[n] = {"state": "done", "text": new, "format": rec["format"]}
                    continue
            if got.get("key") == _key(text) and (got.get("out") or time.time() - float(got.get("failed") or 0) < RETRY_AFTER):
                out[n] = {"state": "none"}                    # asked about this very text: nothing better to be had now
                continue
        if not on:
            out[n] = {"state": "none"}
            continue
        if blocked is None:
            blocked = hosted_blocked(p)
        if blocked:
            out[n] = {"state": "none"}
            continue
        start(p, n, text)
        out[n] = {"state": "busy"}
    return out
