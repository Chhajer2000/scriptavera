# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Commands to the editor in plain words: "remove Definition 2.1", "move Lemma 3.2 before Theorem 4.2",
"make Lemma 3.2 a proposition", "rewrite the proof of Theorem 4.2 in more detail".

Every command ends as a proposal: the changes, file by file, which the author reads and accepts in whole or in
part. Nothing is written before that.

Commands that only rearrange the paper (remove, move, change the kind of a statement, go to) are carried out by
code, exactly and without a model. Everything else goes to the strongest writing model, which answers with
operations on numbered items or exact text replacements; the code applies them and checks the result.

LaTeX renumbers by itself: the statements after a removed one move up, and every \\ref follows. What LaTeX cannot
fix is checked here and said plainly: a \\ref to something that no longer exists (it would print "??"), a number
typed by hand in the text ("Section~2"), which is corrected in the same proposal, and any formula changed outside
the part the command was about.
"""

from __future__ import annotations

import difflib
import json
import re
import time
import uuid
from collections import Counter

from . import prompts
from . import workspace as W
from .llm import LLMError, RunHooks, clock, router
from .store import Project
from .texutil import check_balance, split_math
from .util import est_tokens

# Words the author may use for each kind of item. Statement kinds declared in the paper are added from its preamble.
ALIASES = {
    "def": "definition", "defn": "definition", "defs": "definition", "lem": "lemma", "thm": "theorem",
    "prop": "proposition", "cor": "corollary", "rem": "remark", "ex": "example", "conj": "conjecture",
    "sec": "section", "§": "section", "subsec": "subsection", "eq": "equation", "eqn": "equation", "formula": "equation",
    "fig": "figure", "tab": "table", "obs": "observation",
}
SECTION_KINDS = ("section", "subsection", "subsubsection")
_REF_CMDS = r"(?:eq|c|C|auto|page|name|v|V)?ref\*?"


class AssistError(ValueError):
    pass


# ---------------------------------------------------------------- targets

def vocabulary(kinds: dict[str, dict]) -> dict[str, str]:
    """word -> kind ('definition', 'section', 'equation', ...), singular and plural."""
    words: dict[str, str] = {}
    for env, k in kinds.items():
        for w in {env.rstrip("*").lower(), k["name"].lower()}:
            if w and re.match(r"^[a-z]+$", w):
                words[w] = env
                words[w + "s"] = env
    for w in SECTION_KINDS + ("equation", "figure", "table"):
        words[w] = w
        words[w + "s"] = w
    for a, full in ALIASES.items():
        if full in words:
            words[a] = words[full]
    return words


# a letter counts as a number only on its own ('Section A', 'Lemma A.2'): in 'the proof of Theorem 3.1', "of T"
# must not be read as a reference and swallow the word after it
_REF = re.compile(r"(?<![A-Za-z])(?P<word>[A-Za-z§]+)\.?\s*(?:~|\\ |\s)?\s*\(?(?P<num>(?:[A-Z](?![A-Za-z])|\d+)(?:\.\d+)*[a-z]?)\)?"
                  r"(?![\w.]*\d)")


def find_refs(text: str, words: dict[str, str]) -> list[dict]:
    """Mentions like 'Definition 2.1', 'lemma 3', 'Section~4', 'eq. (3)' in the author's command."""
    out = []
    for m in _REF.finditer(text):
        w = m.group("word").lower()
        if w in words:
            out.append({"kind": words[w], "num": m.group("num"), "start": m.start(), "end": m.end(), "text": m.group(0)})
    return out


def _of_kind(items: list[dict], kind: str) -> list[dict]:
    if kind in SECTION_KINDS:
        return [it for it in items if it["kind"] in SECTION_KINDS]
    if kind == "equation":
        return [it for it in items if it["kind"] == "equation" and it.get("numbers")]
    if kind in ("figure", "table"):
        return [it for it in items if it["kind"] == "figure" and it.get("name", "").lower() == kind]
    return [it for it in items if it["kind"] == "env" and it["env"] == kind]


def display(it: dict) -> str:
    if it["kind"] in SECTION_KINDS:
        return f"{it['kind'].capitalize()} {it.get('number') or ''}".strip() + (f" ({it.get('title')})" if it.get("title") else "")
    if it["kind"] == "equation":
        return f"equation ({it.get('number')})" if it.get("number") else "an unnumbered display"
    num = it.get("number") or ""
    return f"{it.get('name', it.get('env', 'item'))} {num}".strip()


def resolve(items: list[dict], kind: str, num: str) -> dict:
    """The item the author means, the way they see it in the PDF. Returns {item, note, alternatives}.

    'Definition 2.1' is the definition printed as 2.1. When no item has that number but the number is a whole
    number n, the author may count ('definition 1' for the first definition, printed 2.1 when numbering restarts in
    every section) or use the numbering of their notes, which survives in labels such as def:D1. The first reading
    is taken; the other is offered."""
    cands = _of_kind(items, kind)
    word = kind.capitalize() if kind in SECTION_KINDS + ("equation", "figure", "table") else \
        (cands[0]["name"] if cands else kind.capitalize())
    exact = [it for it in cands if (it.get("number") == num) or (kind == "equation" and num in (it.get("numbers") or []))]
    if kind in SECTION_KINDS and exact:
        exact = [it for it in exact if it["kind"] == ("section", "subsection", "subsubsection")[min(num.count("."), 2)]] or exact
    alts: list[dict] = []
    if exact:
        return {"item": exact[0], "note": "", "alternatives": alts}
    numbered = [it for it in cands if it.get("number")]
    if not numbered:
        raise AssistError(f"There is no numbered {word.lower()} in the paper.")
    if re.fullmatch(r"\d+", num):
        n = int(num)
        by_label = [it for it in numbered if _label_number(it.get("label", "")) == n]
        if 1 <= n <= len(numbered):
            pick = numbered[n - 1]
            listing = ", ".join(x["number"] for x in numbered[:6]) + ("…" if len(numbered) > 6 else "")
            note = (f"No {word.lower()} is printed as {word} {num} in the PDF ({word.lower()}s are numbered {listing}). "
                    f"I took the {_ordinal(n)} one, {display(pick)}.")
            for it in by_label:
                if it is not pick:
                    alts.append({"item": it, "why": f"labelled {it['label']}, the numbering of your notes"})
            return {"item": pick, "note": note, "alternatives": alts}
        if by_label:
            return {"item": by_label[0], "note": f"No {word.lower()} is printed as {word} {num}; I took the one labelled "
                                                 f"{by_label[0]['label']}.", "alternatives": alts}
    listing = ", ".join(display(x) for x in numbered[:8])
    raise AssistError(f"There is no {word} {num}. The paper has: {listing}.")


def _label_number(label: str) -> int | None:
    """The number at the end of a label such as def:D1 or res:R5 (Scriptavera keeps the numbering of the notes there)."""
    m = re.search(r"[:_\-][A-Za-z]{0,3}(\d+)$", label or "")
    return int(m.group(1)) if m else None


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
            9: "ninth", 10: "tenth"}.get(n, f"{n}th")


def plain_target(text: str, words: dict[str, str]) -> bool:
    """Is the phrase nothing but one numbered item ('Definition 2.1', 'the proof of Lemma 3.2')? Only then is a
    command carried out by code; 'the second sentence of Definition 2.1' goes to the AI."""
    t = re.sub(r"^(?:the\s+)", "", text.strip().rstrip(".!?"), flags=re.I)
    m = re.match(r"^proof\s+of\s+(?:the\s+)?", t, re.I)
    core = (t[m.end():] if m else t).strip()
    refs = find_refs(core, words)
    return len(refs) == 1 and refs[0]["start"] == 0 and refs[0]["end"] >= len(core.rstrip(")"))


def target_of(text: str, items: list[dict], words: dict[str, str]) -> dict:
    """One item named in a phrase: 'Definition 2.1', 'the proof of Lemma 3.2', 'Section 3'."""
    t = text.strip().rstrip(".")
    proof = bool(re.match(r"^(?:the\s+)?proof\s+of\b", t, re.I))
    refs = find_refs(t, words)
    if len(refs) != 1:
        raise AssistError("Name one numbered item, for example 'Definition 2.1' or 'the proof of Lemma 3.2'.")
    r = resolve(items, refs[0]["kind"], refs[0]["num"])
    if proof:
        it = r["item"]
        if it["kind"] != "env" or not it.get("proof"):
            raise AssistError(f"{display(it)} has no proof right after it.")
    r["proof"] = proof
    return r


# ------------------------------------------------------------ text helpers

def _lines(text: str) -> list[str]:
    return text.split("\n")


def _block(it: dict, with_proof: bool = True) -> tuple[int, int]:
    """1-based first and last line of an item, with its proof if it has one."""
    end = it["proof"]["end"] if with_proof and it.get("proof") else it["end"]
    if it["kind"] in SECTION_KINDS:
        end = it.get("section_end", it["end"])
    return it["line"], end


def _cut(text: str, first: int, last: int) -> tuple[str, list[str]]:
    """Remove lines first..last (1-based) and one blank line around them, so that no gap is left behind."""
    ls = _lines(text)
    a, b = first - 1, last                    # ls[a:b] are the lines removed
    removed = ls[a:b]
    if b < len(ls) and not ls[b].strip() and (a == 0 or not ls[a - 1].strip()):
        b += 1
    return "\n".join(ls[:a] + ls[b:]), removed


def _insert(text: str, at_line: int, block: list[str]) -> str:
    """Insert lines before line `at_line` (1-based), separated from their neighbours by one blank line."""
    ls = _lines(text)
    i = max(0, min(at_line - 1, len(ls)))
    chunk = list(block)
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    before = [""] if i > 0 and ls[i - 1].strip() else []
    after = [""] if i < len(ls) and ls[i].strip() else []
    return "\n".join(ls[:i] + before + chunk + after + ls[i:])


def _section_ends(items: list[dict], texts: dict[str, str]) -> None:
    """Where each section ends: before the next heading of the same or a higher level in the same file."""
    rank = {"section": 0, "subsection": 1, "subsubsection": 2}
    for k, it in enumerate(items):
        if it["kind"] not in rank:
            continue
        end = len(_lines(texts.get(it["path"], "")))
        for nxt in items[k + 1:]:
            if nxt["path"] != it["path"]:
                continue
            if nxt["kind"] in rank and rank[nxt["kind"]] <= rank[it["kind"]]:
                end = nxt["line"] - 1
                break
        it["section_end"] = end


def labels_in(texts: dict[str, str]) -> Counter:
    c: Counter = Counter()
    for t in texts.values():
        for m in re.finditer(r"\\label\{([^{}]+)\}", "\n".join(W.strip_comment(x) for x in _lines(t))):
            c[m.group(1)] += 1
    return c


def refs_to(texts: dict[str, str], label: str) -> list[dict]:
    out = []
    pat = re.compile(r"\\" + _REF_CMDS + r"\{([^{}]*)\}")
    for path, t in texts.items():
        for n, line in enumerate(_lines(t), 1):
            for m in pat.finditer(W.strip_comment(line)):
                if label in [x.strip() for x in m.group(1).split(",")]:
                    out.append({"path": path, "line": n, "text": line.strip()[:200]})
    return out


def math_bag(text: str) -> Counter:
    from .final import math_bag as bag
    return Counter(bag(text))


# ------------------------------------------------------------- proposals

def _store(p: Project) -> "object":
    return p.dir / "editor_proposals"


def _save(p: Project, prop: dict) -> None:
    d = _store(p)
    d.mkdir(parents=True, exist_ok=True)
    for old in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-20]:
        try:
            old.unlink()
        except OSError:
            pass
    W._write_atomic(d / f"{prop['id']}.json", json.dumps(prop, ensure_ascii=False).encode("utf-8"))


def load_proposal(p: Project, pid: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{12,40}", pid or ""):
        raise AssistError("Unknown proposal.")
    f = _store(p) / f"{pid}.json"
    if not f.exists():
        raise AssistError("That proposal is no longer available. Give the command again.")
    return json.loads(f.read_text(encoding="utf-8"))


def _opcodes(a: list[str], b: list[str]) -> list[tuple]:
    """Line differences, with each pure deletion or insertion slid down as far as it goes while the text stays the
    same, so that a removed block shows as itself and not as the end of the block before it."""
    raw = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    changes = []
    for k, (tag, i1, i2, j1, j2) in enumerate(raw):
        if tag == "equal":
            continue
        nxt = raw[k + 1] if k + 1 < len(raw) and raw[k + 1][0] == "equal" else None
        if nxt and tag == "delete":
            while i2 < nxt[2] and a[i1] == a[i2]:
                i1, i2, j1, j2 = i1 + 1, i2 + 1, j1 + 1, j2 + 1
        elif nxt and tag == "insert":
            while j2 < nxt[4] and b[j1] == b[j2]:
                i1, i2, j1, j2 = i1 + 1, i2 + 1, j1 + 1, j2 + 1
        changes.append((tag, i1, i2, j1, j2))
    out, ia, ja = [], 0, 0
    for tag, i1, i2, j1, j2 in changes:
        if i1 > ia or j1 > ja:
            out.append(("equal", ia, i1, ja, j1))
        out.append((tag, i1, i2, j1, j2))
        ia, ja = i2, j2
    if ia < len(a) or ja < len(b):
        out.append(("equal", ia, len(a), ja, len(b)))
    return out


def hunks_of(old: str, new: str, path: str, start_id: int = 1) -> list[dict]:
    a, b = _lines(old), _lines(new)
    out = []
    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        if tag == "equal":
            continue
        out.append({"id": f"h{start_id + len(out)}", "path": path, "old_start": i1 + 1, "old_end": i2, "new_start": j1 + 1,
                    "new_end": j2, "old": a[i1:i2], "new": b[j1:j2],
                    "before": a[max(0, i1 - 2):i1], "after": a[i2:i2 + 2]})
    return out


def apply_hunks(old: str, new: str, keep: set[str] | None, path: str) -> str:
    """The new text with only the chosen changes made (all of them when `keep` is None)."""
    if keep is None:
        return new
    a, b = _lines(old), _lines(new)
    out: list[str] = []
    k = 0
    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        if tag == "equal":
            out += a[i1:i2]
            continue
        k += 1
        out += b[j1:j2] if f"{path}#h{k}" in keep else a[i1:i2]
    return "\n".join(out)


def _proposal(p: Project, command: str, old: dict[str, str], new: dict[str, str], summary: str, how: str,
              warnings: list[dict], alternatives: list[dict], extra: dict | None = None) -> dict:
    changed = {path for path in set(old) | set(new) if old.get(path) != new.get(path)}
    hunks = []
    for path in sorted(changed):
        hs = hunks_of(old.get(path, ""), new.get(path, ""), path)
        for i, h in enumerate(hs, 1):
            h["id"] = f"{path}#h{i}"
        hunks += hs
    prop = {"id": uuid.uuid4().hex[:16], "at": time.time(), "command": command, "summary": summary, "how": how,
            "kind": "edit" if hunks else "none", "base": {path: W.sha(old.get(path, "")) for path in changed},
            "old": {path: old.get(path, "") for path in changed}, "new": {path: new.get(path, "") for path in changed},
            "hunks": hunks, "warnings": warnings,
            "alternatives": [{"label": a["label"], "command": a["command"]} for a in alternatives], **(extra or {})}
    if not hunks and prop.get("kind") == "none" and not extra:
        prop["summary"] = summary or "Nothing needed to change."
    _save(p, prop)
    return public(prop)


def public(prop: dict) -> dict:
    return {k: v for k, v in prop.items() if k not in ("old", "new", "base")}


# ------------------------------------------------------------------ checks

_TYPED = re.compile(r"\b(?P<word>Definitions?|Lemmas?|Theorems?|Propositions?|Corollar(?:y|ies)|Remarks?|Examples?|"
                    r"Sections?|Conjectures?|Claims?|Figures?|Tables?|Notations?|Observations?|Facts?|Assumptions?|"
                    r"Hypothes[ie]s|Problems?|Questions?)"
                    r"(?P<sep>~|\\ |\s+)(?P<num>(?:[A-Z]|\d+)(?:\.\d+)*)\b")


def _identity(it: dict) -> tuple:
    """What stays the same about an item when the paper around it changes (its kind of statement may change)."""
    base = "section" if it["kind"] in SECTION_KINDS else it["kind"]
    return (base, it.get("label") or it.get("title") or it.get("snippet", "")[:80], it["path"] if not it.get("label") else "")


def _word(it: dict) -> str:
    return "section" if it["kind"] in SECTION_KINDS else (it.get("name") or it.get("env") or "").lower()


def _singular(w: str) -> str:
    w = w.lower()
    return {"corollaries": "corollary", "hypotheses": "hypothesis"}.get(w, w[:-1] if w.endswith("s") and not w.endswith("ss") else w)


_DEF_NAMES = ("definition", "definitions", "notation", "convention")
# words right before a symbol that say it is being introduced: "…, denoted by $s$", "the parameter $m$",
# "the \emph{p-adic valuation} $\nu_p(n)$", "we write $x \sim y$"
_CUE = re.compile(r"(?:denoted\s+by|denote[sd]?\s+by|is\s+denoted|we\s+write|written|we\s+call|called|parameter|"
                  r"quantity|function|number|notation|\\emph\{[^{}]*\}|\\textit\{[^{}]*\})\s*,?\s*(?:the\s+|by\s+)?$", re.I)
_LET = re.compile(r"(?:^|[\s.,;(])(?:let|define|set|write)\s+$", re.I)
# "Let $i(G)$ be the number …" defines; "Let $p$ be a prime" only introduces a variable
_LET_AFTER = re.compile(r"^\s*(?:denotes?|be\s+the\b|stands?\s+for|is\s+the|means)", re.I)


def defined_symbols(block: str) -> list[str]:
    """The symbols a definition introduces, read from its wording: 'denoted by $s$', 'Let $i(G)$ denote …',
    'let $H_n^{(s)} = …$', 'the integer parameter $m$'. A symbol only mentioned in passing is not one of them."""
    text = re.sub(r"\\label\{[^{}]*\}", "", "\n".join(W.strip_comment(x) for x in _lines(block)))
    out: list[str] = []
    for m in re.finditer(r"(?<!\\)\$([^$]+)\$", text):
        inner = m.group(1).strip()
        before, after = text[max(0, m.start() - 70):m.start()], text[m.end():m.end() + 30]
        has_eq = re.search(r":=|\\coloneqq|(?<![<>!\\])=", inner)
        lhs = re.split(r":=|\\coloneqq|(?<![<>!\\])=", inner)[0].strip() if has_eq else inner
        sym = None
        if _CUE.search(before):
            sym = lhs
        elif _LET.search(before) and (_LET_AFTER.match(after) or has_eq):
            sym = lhs
        sym = (sym or "").strip().rstrip(".,;")
        fn = re.fullmatch(r"(.+?)\s*\(\s*[a-z]\s*\)", sym)     # \nu_p(n): the notation is \nu_p, n is a dummy
        if fn:
            sym = fn.group(1).strip()
        if sym and len(sym) <= 24 and not re.search(r"\\(?:sum|int|prod|lim|frac|in|le|ge|subset)\b|[<>]", sym) and sym not in out:
            out.append(sym)
    return out


def _symbol_pattern(sym: str) -> re.Pattern:
    toks = re.findall(r"\\[A-Za-z]+|\S", sym)
    core = r"\s*".join(re.escape(t) for t in toks)
    head = r"(?<![A-Za-z\\])" if re.match(r"[A-Za-z]", sym) else ""
    tail = r"(?![A-Za-z])" if re.search(r"[A-Za-z]$", sym) and not re.search(r"\\[A-Za-z]+$", sym) else \
        (r"(?![A-Za-z])" if re.search(r"\\[A-Za-z]+$", sym) else "")
    return re.compile(head + core + tail)


def _math_uses(lines: list[tuple[str, int, str]], pat: re.Pattern) -> list[tuple[str, int]]:
    """Where a symbol occurs inside mathematics, in reading order: [(path, line)]."""
    buf, starts = [], []
    pos = 0
    for rel, n, line in lines:
        code = W.strip_comment(line)
        starts.append((pos, rel, n))
        buf.append(code)
        pos += len(code) + 1
    text = "\n".join(buf)
    hits: list[tuple[str, int]] = []
    off = 0
    for kind, part in split_math(text):
        k = text.find(part, off)
        k = off if k < 0 else k
        if kind != "text":
            for m in pat.finditer(part):
                at = k + m.start()
                lo, hi = 0, len(starts) - 1
                while lo < hi:                      # the line holding this offset
                    mid = (lo + hi + 1) // 2
                    if starts[mid][0] <= at:
                        lo = mid
                    else:
                        hi = mid - 1
                hits.append((starts[lo][1], starts[lo][2]))
        off = k + len(part)
    return hits


def _container(items: list[dict], path: str, line: int) -> str:
    """'the proof of Theorem 3.1', 'Lemma 2.3' or the section a line belongs to, for messages."""
    best = ""
    for it in items:
        if it["path"] != path:
            continue
        if it["kind"] == "env" and it.get("proof") and it["proof"]["line"] <= line <= it["proof"]["end"]:
            return f"the proof of {display(it)}"
        if it["kind"] == "env" and it["line"] <= line <= it["end"]:
            return display(it)
        if it["kind"] in SECTION_KINDS and it["line"] <= line:
            best = display(it)
    return best or path


def orphaned_symbols(p: Project, before_all: dict[str, str], after_all: dict[str, str], targets: list[dict],
                     before_items: list[dict]) -> list[dict]:
    """A definition removed while what it defines is still used: say which symbol, how often and where."""
    out: list[dict] = []
    lines_before = W.reading_order(W.root(p), W.main_file(p), before_all)
    index = {(rel, n): i for i, (rel, n, _) in enumerate(lines_before)}
    still = labels_in(after_all)
    flat = lambda t: re.sub(r"\s+", " ", t).strip()          # noqa: E731
    after_flat = flat("\n".join(after_all.values()))
    for t in targets:
        if "new_text" in t and (t.get("new_text") or "").strip():
            continue                                # a rewrite keeps its own definitions, or says so in its formulas
        for it in before_items:
            if not (it["kind"] == "env" and it["path"] == t.get("path") and t["line"] <= it["line"] <= t["end"]):
                continue
            if (it.get("name") or "").lower() not in _DEF_NAMES and not it.get("env", "").lower().startswith("def"):
                continue
            block = "\n".join(_lines(before_all[it["path"]])[it["line"] - 1:it["end"]])
            if (it.get("label") and it["label"] in still) or (not it.get("label") and flat(block) in after_flat):
                continue                            # moved, not removed
            # the uses that would lose their definition: those after it (an abstract or an introduction may mention
            # a symbol informally before it is defined), outside the part removed
            start = index.get((t["path"], t["end"]))
            if start is None:
                continue
            rest = [x for x in lines_before[start + 1:] if not (x[0] == t["path"] and t["line"] <= x[1] <= t["end"])]
            for sym in defined_symbols(block):
                uses = _math_uses(rest, _symbol_pattern(sym))
                if not uses:
                    continue
                path, line = uses[0]
                out.append({"level": "warn", "path": path, "line": line,
                            "text": f"{display(it)} defines ${sym}$, which the paper still uses {len(uses)} time"
                                    f"{'' if len(uses) == 1 else 's'} (first in {_container(before_items, path, line) or path}). "
                                    f"Without this definition those uses are undefined: keep it, or say where ${sym}$ "
                                    "should be defined instead.",
                            "alt": {"label": f"Remove it but keep the definition of ${sym}$ as a sentence",
                                    "command": f"remove {display(it)}, but keep the definition of ${sym}$ as a sentence in "
                                               f"the text just before it is first used"}})
    return out


def new_forward_refs(p: Project, before_all: dict[str, str], after_all: dict[str, str]) -> list[dict]:
    """References that now point ahead to a statement not yet stated (a lemma used before it appears), which the
    change introduced. References ahead to sections and equations are left alone: papers announce those."""
    def ahead(texts: dict[str, str]) -> tuple[dict[tuple, dict], list[dict]]:
        lines = W.reading_order(W.root(p), W.main_file(p), texts)
        items = W.outline(p, texts=texts, prefer_compile=False)["items"]
        pos = {(rel, n): i for i, (rel, n, _) in enumerate(lines)}
        where = {it["label"]: it for it in items if it.get("label") and it["kind"] == "env"}
        found: dict[tuple, dict] = {}
        seen: Counter = Counter()
        pat = re.compile(r"\\" + _REF_CMDS + r"\{([^{}]*)\}")
        for i, (rel, n, line) in enumerate(lines):
            for m in pat.finditer(W.strip_comment(line)):
                for lab in (x.strip() for x in m.group(1).split(",")):
                    it = where.get(lab)
                    if not it:
                        continue
                    seen[(lab, rel)] += 1                 # the k-th reference to this label in this file: stable when
                    at = pos.get((it["path"], it["line"]))  # other words on the line change
                    if at is not None and i < at:
                        found[(lab, rel, seen[(lab, rel)])] = {"path": rel, "line": n, "item": it}
        return found, items
    was, _ = ahead(before_all)
    now, items = ahead(after_all)
    out = []
    for key, v in now.items():
        if key in was:
            continue
        out.append({"level": "warn", "path": v["path"], "line": v["line"],
                    "text": f"{_container(items, v['path'], v['line'])} uses {display(v['item'])}, which now comes after it. "
                            "A statement should come before the places that use it: move it back, or move "
                            f"{display(v['item'])} earlier."})
    return out


def _alts_from(warns: list[dict]) -> list[dict]:
    out = []
    for w in warns:
        a = w.pop("alt", None)
        if a and a not in out:
            out.append(a)
    return out


def included(p: Project, texts: dict[str, str]) -> dict[str, str]:
    """Only what LaTeX actually reads: the files reached from main.tex through \\input, each as the lines read."""
    keep: dict[str, list[str]] = {}
    for rel, _, line in W.reading_order(W.root(p), W.main_file(p), texts):
        keep.setdefault(rel, []).append(line)
    return {rel: "\n".join(ls) for rel, ls in keep.items()}


def check(p: Project, old: dict[str, str], new: dict[str, str], targets: list[dict], fix_typed: bool = True) -> tuple[dict[str, str], list[dict]]:
    """What LaTeX will not fix by itself, said and (where it can be done exactly) fixed: references to labels that
    no longer exist, numbers typed by hand that change, formulas changed outside the items the command was about,
    and LaTeX left unbalanced."""
    warnings: list[dict] = []
    disk = {rel: W.read_text(p, rel)["text"] for rel in W.files(p) if W._kind(rel) == "text"}
    before_all = {**disk, **old}
    after_all = {**disk, **new}
    before_in, after_in = included(p, before_all), included(p, after_all)
    # 1. labels that disappear but are still referred to
    gone = set(labels_in(before_in)) - set(labels_in(after_in))
    for lab in sorted(gone):
        for r in refs_to(after_in, lab):
            warnings.append({"level": "error", "path": r["path"], "line": r["line"],
                             "text": f"\\ref{{{lab}}} is still used here, but this change removes what it points to: LaTeX "
                                     f"would print '??'. Rewrite that sentence, or ask the AI to: “{r['text'][:140]}”"})
    # 2. numbers typed by hand: LaTeX renumbers, but text it does not know is a number stays as typed
    before = W.outline(p, texts=before_all, prefer_compile=False)["items"]
    after = W.outline(p, texts=after_all, prefer_compile=False)["items"]
    shape = lambda its: [(x["kind"], x.get("number")) for x in its if x["kind"] != "equation"]   # noqa: E731
    trust = True
    if W.fresh_numbers(p) and not set(old) - set(disk):
        trust = shape(W.outline(p)["items"]) == shape(W.outline(p, texts=disk, prefer_compile=False)["items"])
    renamed: dict[tuple[str, str], tuple[str, str] | None] = {}
    twins: dict[tuple, dict] = {}
    for it in after:
        twins.setdefault(_identity(it), it)
    for it in before:
        if it.get("number") and it["kind"] != "equation":
            t = twins.get(_identity(it))
            renamed[(_word(it), it["number"])] = (_word(t), t["number"]) if t and t.get("number") else None
    fixed = dict(new)
    if trust and fix_typed and any(v != k for k, v in renamed.items()):
        for path, text in after_in.items():
            if not path.endswith(".tex"):
                continue
            full = fixed.get(path, after_all[path])
            ls = _lines(full)
            touched = []
            for n, line in enumerate(ls):
                code = W.strip_comment(line)
                masked = re.sub(r"\\(?:" + _REF_CMDS + r"|label|cite[tp]?)\{[^{}]*\}", lambda m: "\x00" * len(m.group(0)), code)
                out, pos, hit = [], 0, False
                for m in _TYPED.finditer(masked):
                    real = code[m.start():m.end()]
                    key = (_singular(m.group("word")), m.group("num"))
                    out.append(code[pos:m.start()])
                    pos = m.end()
                    if key not in renamed:
                        out.append(real)
                        continue
                    to = renamed[key]
                    if to is None:
                        warnings.append({"level": "warn", "path": path, "line": n + 1,
                                         "text": f"“{real}” is typed by hand and names what this change removes: rewrite that "
                                                 "sentence."})
                        out.append(real)
                        continue
                    word = m.group("word")
                    if to[0] != key[0]:
                        cap = to[0].capitalize()
                        word = cap + ("s" if word.lower().endswith("s") and not key[0].endswith("s") else "")
                    rep = f"{word}{m.group('sep')}{to[1]}"
                    hit = hit or rep != real
                    out.append(rep)
                out.append(code[pos:])
                if hit:
                    ls[n] = "".join(out) + line[len(code):]
                    touched.append(n + 1)
            if touched:
                fixed[path] = "\n".join(ls)
                warnings.append({"level": "info", "path": path, "line": touched[0],
                                 "text": f"{len(touched)} line(s) of {path} name a statement or section by a number typed by "
                                         "hand; updated to the new numbering (LaTeX updates \\ref by itself, but not numbers "
                                         "typed as text)."})
    elif not trust:
        warnings.append({"level": "info", "text": "Numbers typed by hand in the text were not checked: counting the numbers "
                                                  "did not give the numbers of the last compile."})
    # 3. formulas changed outside what the command named
    inside_old, inside_new = Counter(), Counter()
    for t in targets:
        if t.get("path") in before_all:
            inside_old += math_bag("\n".join(_lines(before_all[t["path"]])[t["line"] - 1:t["end"]]))
        if t.get("new_text"):
            inside_new += math_bag(t["new_text"])
    final_in = included(p, {**after_all, **fixed})
    o = sum((math_bag(v) for k, v in before_in.items() if k.endswith(".tex")), Counter())
    nb = sum((math_bag(v) for k, v in final_in.items() if k.endswith(".tex")), Counter())
    removed, added = (o - nb) - inside_old, (nb - o) - inside_new
    if removed or added:
        lost, new_f = list(removed.elements())[:6], list(added.elements())[:6]
        warnings.append({"level": "warn" if targets else "info", "formulas": True,
                         "text": ("Formulas changed outside the part the command named. " if targets else "Formulas changed by "
                                  "this edit. ") + (f"Removed: {'; '.join('$' + x + '$' for x in lost)}. " if lost else "")
                         + (f"Added: {'; '.join('$' + x + '$' for x in new_f)}." if new_f else "")})
    # 3a. formulas a rewrite changes inside the part it was asked to rewrite: expected, but the mathematics is the
    # author's, so it is said (a part removed by the command is not reported: its formulas simply go with it)
    for t in targets:
        if "new_text" not in t or t.get("path") not in before_all:
            continue
        was = math_bag("\n".join(_lines(before_all[t["path"]])[t["line"] - 1:max(t["end"], t["line"] - 1)]))
        now = math_bag(t["new_text"] or "")
        lost, new_f = list((was - now).elements())[:6], list((now - was).elements())[:6]
        if lost or new_f:
            span = f"lines {t['line']}–{t['end']}" if t["end"] > t["line"] else f"line {t['line']}"
            warnings.append({"level": "info", "formulas": True, "path": t["path"], "line": t["line"],
                             "text": f"The new text changes formulas in {t['path']} ({span}). "
                                     + (f"No longer there: {'; '.join('$' + x + '$' for x in lost)}. " if lost else "")
                                     + (f"New: {'; '.join('$' + x + '$' for x in new_f)}. " if new_f else "")
                                     + "Check the mathematics before you accept."})
    # 4. LaTeX left unbalanced
    for path, text in fixed.items():
        if path.endswith(".tex"):
            was = set(check_balance(before_all.get(path, "")))
            for prob in [x for x in check_balance(text) if x not in was][:3]:
                warnings.append({"level": "warn", "path": path, "text": f"LaTeX may not compile: {prob}."})
    # 5. a removed definition whose symbol is still used; 6. a statement now used before it is stated
    final_all = {**after_all, **fixed}
    try:
        warnings += orphaned_symbols(p, before_all, final_all, targets, before)
        warnings += new_forward_refs(p, before_all, final_all)
    except Exception:  # noqa: BLE001 - these are advice; a paper the reader cannot follow must not stop the proposal
        pass
    rank = {"error": 0, "warn": 1, "info": 2}
    warnings.sort(key=lambda w: rank.get(w.get("level"), 3))
    return fixed, warnings


# ------------------------------------------------------------ rule commands

_DELETE = re.compile(r"^(?:please\s+)?(?:remove|delete|drop|cut|erase|get\s+rid\s+of)\s+(?P<t>.+?)(?P<proof>\s+(?:and|with)\s+its\s+proof)?[.!]?$", re.I)
_MOVE = re.compile(r"^(?:please\s+)?(?:move|put|place)\s+(?P<a>.+?)\s+(?P<w>before|after|above|below|ahead\s+of)\s+(?P<b>.+?)[.!]?$", re.I)
_CONVERT = re.compile(r"^(?:please\s+)?(?:turn|make|change|convert)\s+(?P<a>.+?)\s+(?:in)?to\s+(?:an?\s+)?(?P<k>[A-Za-z]+)[.!]?$|"
                      r"^(?:please\s+)?make\s+(?P<a2>.+?)\s+(?:an?\s+)(?P<k2>[A-Za-z]+)[.!]?$", re.I)
_GOTO = re.compile(r"^(?:please\s+)?(?:go\s+to|show(?:\s+me)?|find|open|jump\s+to|where\s+is)\s+(?P<t>.+?)[.?!]?$", re.I)


def rule_command(p: Project, command: str, texts: dict[str, str], ol: dict) -> dict | None:
    """Rearranging commands, carried out by code. None when the command is not one of them."""
    items, kinds = ol["items"], ol["kinds"]
    words = vocabulary(kinds)
    _section_ends(items, texts)
    c = re.sub(r"\s+", " ", command.strip())
    m = _GOTO.match(c)
    if m and plain_target(m.group("t"), words):
        r = target_of(m.group("t"), items, words)
        it = r["item"]
        line = it["proof"]["line"] if r["proof"] else it["line"]
        return {"kind": "navigate", "summary": f"{display(it)} is in {it['path']}, line {line}." + (f" {r['note']}" if r["note"] else ""),
                "navigate": {"path": it["path"], "line": line}, "alternatives": _alts(r, "go to")}
    m = _DELETE.match(c)
    if m and plain_target(m.group("t"), words):
        r = target_of(m.group("t"), items, words)
        it = r["item"]
        new = dict(texts)
        if r["proof"]:
            first, last = it["proof"]["line"], it["proof"]["end"]
            what = f"the proof of {display(it)}"
            region = {"path": it["path"], "line": first, "end": last}
        elif it["kind"] in SECTION_KINDS and it["line"] == 1 and it.get("section_end", 0) >= len(_lines(texts[it["path"]])) - 1 \
                and it["kind"] == "section":
            # a whole section file: it is no longer \input; the file itself stays, in case it is wanted again
            main = W.main_file(p)
            stem = it["path"][:-4] if it["path"].endswith(".tex") else it["path"]
            ls = _lines(new[main])
            hit = [i for i, ln in enumerate(ls) if re.search(r"\\(?:input|include)\s*\{\s*(?:" + re.escape(stem) + r"|" +
                                                             re.escape(it["path"]) + r")\s*\}", W.strip_comment(ln))]
            if not hit:
                raise AssistError(f"{display(it)} is in {it['path']}, but main.tex does not \\input it directly.")
            new[main], _ = _cut(new[main], hit[0] + 1, hit[0] + 1)
            what = display(it)
            region = {"path": it["path"], "line": 1, "end": len(_lines(texts[it["path"]]))}
            fixed, warns = check(p, {main: texts[main]}, {main: new[main]}, [region])
            warns.insert(0, {"level": "info", "text": f"The file {it['path']} is kept but no longer included. Delete it in "
                                                       "the file list if you do not want it any more."})
            return _done(p, command, texts, fixed, f"Remove {what}.", warns, r, "remove")
        else:
            first, last = _block(it)
            what = display(it) + (" and its proof" if it.get("proof") else "")
            region = {"path": it["path"], "line": first, "end": last}
        new[it["path"]], _ = _cut(texts[it["path"]], first, last)
        fixed, warns = check(p, {it["path"]: texts[it["path"]]}, {it["path"]: new[it["path"]]}, [region])
        return _done(p, command, texts, fixed, f"Remove {what}.", warns, r, "remove")
    m = _MOVE.match(c)
    if m and plain_target(m.group("a"), words) and plain_target(m.group("b"), words):
        ra, rb = target_of(m.group("a"), items, words), target_of(m.group("b"), items, words)
        a, b = ra["item"], rb["item"]
        if a is b:
            raise AssistError("That moves an item next to itself.")
        if a["kind"] in SECTION_KINDS or b["kind"] in SECTION_KINDS:
            raise AssistError("Moving whole sections is done in main.tex: change the order of the \\input lines.")
        before = m.group("w").lower() in ("before", "above", "ahead of")
        af, al = _block(a)
        block = _lines(texts[a["path"]])[af - 1:al]
        new = dict(texts)
        bf, bl = _block(b)
        if a["path"] == b["path"]:
            ls = _lines(texts[a["path"]])
            at = bf if before else bl + 1
            if af <= at <= al + 1:
                raise AssistError(f"{display(a)} is already there.")
            cut, _ = _cut(texts[a["path"]], af, al)
            shift = (len(ls) - len(_lines(cut))) if at > al else 0
            new[a["path"]] = _insert(cut, at - shift, block)
        else:
            new[a["path"]], _ = _cut(texts[a["path"]], af, al)
            new[b["path"]] = _insert(texts[b["path"]], bf if before else bl + 1, block)
        changed_old = {k: texts[k] for k in {a["path"], b["path"]}}
        fixed, warns = check(p, changed_old, {k: new[k] for k in changed_old}, [{"path": a["path"], "line": af, "end": al}])
        what = f"Move {display(a)}{' and its proof' if a.get('proof') else ''} {'before' if before else 'after'} {display(b)}."
        return _done(p, command, texts, fixed, what, warns, ra, "move", extra_alts=_alts(rb, "move"))
    m = _CONVERT.match(c)
    if m:
        target, kind = (m.group("a"), m.group("k")) if m.group("a") else (m.group("a2"), m.group("k2"))
        kw = (kind or "").lower()
        env = words.get(kw)
        if target and env and env in kinds and plain_target(target, words):
            r = target_of(target, items, words)
            it = r["item"]
            if it["kind"] != "env":
                raise AssistError("Only statements (definitions, lemmas, theorems and the like) can change kind.")
            ls = _lines(texts[it["path"]])
            for k in range(it["line"] - 1, it["end"]):
                ls[k] = ls[k].replace("\\begin{" + it["env"] + "}", "\\begin{" + env + "}", 1) if k == it["line"] - 1 else ls[k]
                if k == it["end"] - 1:
                    idx = ls[k].rfind("\\end{" + it["env"] + "}")
                    if idx >= 0:
                        ls[k] = ls[k][:idx] + "\\end{" + env + "}" + ls[k][idx + len("\\end{" + it["env"] + "}"):]
            new = dict(texts)
            new[it["path"]] = "\n".join(ls)
            fixed, warns = check(p, {it["path"]: texts[it["path"]]}, {it["path"]: new[it["path"]]}, [])
            if kinds[env]["counter"] != kinds[it["env"]]["counter"]:
                warns.insert(0, {"level": "info", "text": f"{kinds[env]['name']} and {it['name']} are numbered separately "
                                                          "in this paper, so the numbers of both kinds change."})
            return _done(p, command, texts, fixed, f"Make {display(it)} a {kinds[env]['name'].lower()}.", warns, r, "make")
    return None


def _alts(r: dict, verb: str) -> list[dict]:
    out = []
    for a in r.get("alternatives") or []:
        out.append({"label": f"{verb.capitalize()} {display(a['item'])} instead ({a['why']})",
                    "command": f"{verb} {display(a['item'])}"})
    return out


def _done(p: Project, command: str, texts: dict[str, str], new: dict[str, str], summary: str, warns: list[dict],
          r: dict, verb: str, extra_alts: list[dict] | None = None) -> dict:
    if r.get("note"):
        warns.insert(0, {"level": "info", "text": r["note"]})
    alts = _alts(r, verb) + (extra_alts or []) + _alts_from(warns)
    if any(w["level"] == "error" and "\\ref" in w["text"] for w in warns):
        alts.append({"label": "Ask the AI to do this and rewrite the sentences that refer to it",
                     "command": command.rstrip(".") + ", and rewrite the sentences that refer to it so that they still read correctly"})
    old = {k: texts[k] for k in new if texts.get(k) != new[k]}
    return {"kind": "edit", "summary": summary, "old": old, "new": {k: new[k] for k in old}, "warnings": warns,
            "alternatives": alts, "how": "rule"}


# ---------------------------------------------------------------- AI commands

def outline_text(items: list[dict], texts: dict[str, str]) -> str:
    rows = []
    for it in items:
        if it["kind"] in ("proof",) or (it["kind"] == "equation" and not it.get("number")):
            continue
        where = f"{it['path']}:{it['line']}-{it['end']}"
        if it.get("proof"):
            where += f" (proof {it['proof']['line']}-{it['proof']['end']})"
        lab = f" [label {it['label']}]" if it.get("label") else ""
        if it["kind"] in SECTION_KINDS:
            rows.append(f"{it['kind'].capitalize()} {it.get('number') or '(unnumbered)'} “{it.get('title', '')}” — {where}{lab}")
        elif it["kind"] == "equation":
            rows.append(f"Equation ({', '.join(it.get('numbers') or [])}) — {where}{lab}")
        else:
            title = f" ({it['title']})" if it.get("title") else ""
            rows.append(f"{display(it)}{title} — {where}{lab} — “{it.get('snippet', '')[:90]}”")
    return "\n".join(rows)


def _parse_ops(text: str) -> dict:
    t = text or ""
    edits = []
    for m in re.finditer(r"<edit\s+file=\"([^\"]+)\"\s*>(.*?)</edit>", t, re.S | re.I):
        body = m.group(2)
        f = re.search(r"<find>\n?(.*?)\n?</find>", body, re.S)
        rp = re.search(r"<replace>\n?(.*?)\n?</replace>", body, re.S)
        if f and rp is not None:
            edits.append({"op": "edit", "file": m.group(1).strip(), "find": f.group(1), "replace": rp.group(1)})
    rest = re.sub(r"<edit\s+file=\"[^\"]+\"\s*>.*?</edit>", "", t, flags=re.S | re.I)
    ops = []
    for m in re.finditer(r"<(delete|move)\s+([^<>]*?)/?>", rest, re.I):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        ops.append({"op": m.group(1).lower(), **attrs})
    for m in re.finditer(r"<(replace|insert)\s+([^<>]*?)>\n?(.*?)\n?</\1>", rest, re.S | re.I):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        ops.append({"op": m.group(1).lower(), **attrs, "text": m.group(3)})

    def tag(name: str) -> str:
        mm = re.search(rf"<{name}>(.*?)</{name}>", t, re.S | re.I)
        return mm.group(1).strip() if mm else ""
    return {"ops": ops + edits, "summary": tag("summary"), "notes": [x.strip() for x in re.findall(r"<note>(.*?)</note>", t, re.S | re.I)],
            "question": tag("question"), "answer": tag("answer")}


def _find_unique(text: str, find: str) -> tuple[int, int] | None:
    if not find:
        return None
    k = text.find(find)
    if k >= 0 and text.find(find, k + 1) < 0:
        return k, k + len(find)
    # the same words with different spacing or line breaks
    pat = r"\s+".join(re.escape(x) for x in find.split())
    hits = list(re.finditer(pat, text))
    if len(hits) == 1:
        return hits[0].start(), hits[0].end()
    return None


def apply_ops(ops: list[dict], texts: dict[str, str], items: list[dict], words: dict[str, str]) -> tuple[dict[str, str], list[str], list[dict]]:
    """Carry out the model's operations on a copy of the files. Returns (new texts, problems, regions changed)."""
    new = dict(texts)
    problems: list[str] = []
    regions: list[dict] = []
    # item operations refer to the original line numbers: do them from the bottom of each file upwards
    item_ops = []
    for op in ops:
        if op["op"] == "edit":
            continue
        name = op.get("item") or op.get("after") or op.get("before") or ""
        try:
            r = target_of(name, items, words)
        except AssistError as e:
            problems.append(f"{op['op']} {name!r}: {e}")
            continue
        it = r["item"]
        if r.get("note"):
            problems.append(f"{op['op']} {name!r}: no item has exactly that number")
            continue
        item_ops.append((op, it, r))
    moves = [x for x in item_ops if x[0]["op"] == "move"]
    singles = [x for x in item_ops if x[0]["op"] != "move"]
    singles.sort(key=lambda x: (x[1]["path"], -x[1]["line"]))
    for op, it, r in singles:
        path = it["path"]
        if op["op"] == "delete":
            first, last = (it["proof"]["line"], it["proof"]["end"]) if r.get("proof") else _block(it)
            new[path], _ = _cut(new[path], first, last)
            regions.append({"path": path, "line": first, "end": last})
        elif op["op"] == "replace":
            first, last = (it["proof"]["line"], it["proof"]["end"]) if r.get("proof") else (it["line"], it["end"])
            ls = _lines(new[path])
            body = op.get("text", "").strip("\n")
            ls[first - 1:last] = _lines(body)
            new[path] = "\n".join(ls)
            regions.append({"path": path, "line": first, "end": last, "new_text": body})
        elif op["op"] == "insert":
            if op.get("after"):
                at = _block(it)[1] + 1
            else:
                at = it["line"]
            new[path] = _insert(new[path], at, _lines(op.get("text", "")))
            regions.append({"path": path, "line": at, "end": at - 1, "new_text": op.get("text", "")})
    if moves and (singles or len(moves) > 1 or any(op["op"] == "edit" for op in ops)):
        problems.append("a move must be the only change in an answer; give other changes as a separate command")
        moves = []
    for op, it, r in moves:
        dest = op.get("before") or op.get("after")
        try:
            rb = target_of(dest, items, words) if dest else None
        except AssistError as e:
            problems.append(f"move {op.get('item')!r}: {e}")
            continue
        if not rb:
            problems.append(f"move {op.get('item')!r}: say before or after which item")
            continue
        b = rb["item"]
        af, al = _block(it)
        block = _lines(new[it["path"]])[af - 1:al]
        bf, bl = _block(b)
        before = bool(op.get("before"))
        if it["path"] == b["path"]:
            ls = _lines(new[it["path"]])
            at = bf if before else bl + 1
            cut, _ = _cut(new[it["path"]], af, al)
            shift = (len(ls) - len(_lines(cut))) if at > al else 0
            new[it["path"]] = _insert(cut, at - shift, block)
        else:
            new[it["path"]], _ = _cut(new[it["path"]], af, al)
            new[b["path"]] = _insert(new[b["path"]], bf if before else bl + 1, block)
        regions.append({"path": it["path"], "line": af, "end": al})
    for op in ops:
        if op["op"] != "edit":
            continue
        path = op["file"].strip().lstrip("./")
        if path not in new:
            problems.append(f"edit in {path}: there is no such file")
            continue
        span = _find_unique(new[path], op["find"])
        if not span:
            problems.append(f"edit in {path}: the text to find does not occur exactly once: {op['find'][:80]!r}")
            continue
        a, b = span
        line_a = new[path].count("\n", 0, a) + 1
        line_b = new[path].count("\n", 0, b) + 1
        new[path] = new[path][:a] + op["replace"] + new[path][b:]
        regions.append({"path": path, "line": line_a, "end": line_b, "new_text": op["replace"]})
    return new, problems, regions


def ai_command(p: Project, command: str, texts: dict[str, str], ol: dict, where: dict) -> dict:
    items, kinds = ol["items"], ol["kinds"]
    words = vocabulary(kinds)
    _section_ends(items, texts)
    ctx_files = dict(texts)
    budget = max(20000, min(240000, (router.budget("writer") - 6000) * 3)) if router.models else 120000
    size = sum(len(t) for t in ctx_files.values())
    if size > budget:
        keep = {W.main_file(p), where.get("path") or ""}
        for r in find_refs(command, words):
            try:
                keep.add(resolve(items, r["kind"], r["num"])["item"]["path"])
            except AssistError:
                pass
        ctx_files = {k: v for k, v in texts.items() if k in keep}
    listing = "\n\n".join(f"=== {k} ===\n{v}" for k, v in ctx_files.items() if k.endswith(".tex"))
    omitted = [k for k in texts if k not in ctx_files and k.endswith(".tex")]
    sel = (where.get("selection") or "").strip()
    here = (f"File {where.get('path')}, line {where.get('line')}" if where.get("path") else "(not given)") + \
           (f"\nSelected text:\n{sel[:4000]}" if sel else "")
    user = (f"THE AUTHOR'S INSTRUCTION\n{command}\n\nOUTLINE (numbers as printed in the PDF)\n{outline_text(items, texts)}\n\n"
            f"WHERE THE AUTHOR IS IN THE EDITOR\n{here}\n\nFILES\n{listing}"
            + (f"\n\n(Not shown, to save room: {', '.join(omitted)}.)" if omitted else ""))
    hooks = _Hooks(p)
    label = "Editor command"
    try:
        res = router.chat("writer", prompts.EDIT_COMMAND.format(), user, max_out=min(16000, est_tokens(listing) // 2 + 3000),
                          temperature=0.1, effort="medium", label=label, hooks=hooks)
    except LLMError as e:
        raise AssistError(_why(e)) from None
    parsed = _parse_ops(res.text)
    if parsed["answer"] and not parsed["ops"]:
        return {"kind": "answer", "summary": parsed["answer"], "answer": parsed["answer"], "how": "ai", "model": res.model}
    if parsed["question"] and not parsed["ops"]:
        return {"kind": "question", "summary": parsed["question"], "question": parsed["question"], "how": "ai", "model": res.model}
    new, problems, regions = apply_ops(parsed["ops"], texts, items, words)
    if problems:
        retry = (user + "\n\n---\nYour previous answer could not be applied:\n" + "\n".join(f"- {x}" for x in problems)
                 + "\n\nAnswer again in full. Copy the text in each <find> exactly from the file, and use the numbers "
                   "of the OUTLINE for item operations.")
        try:
            res = router.chat("writer", prompts.EDIT_COMMAND.format(), retry, max_out=min(16000, est_tokens(listing) // 2 + 3000),
                              temperature=0.1, effort="medium", label=label + " (second try)", hooks=hooks)
        except LLMError as e:
            raise AssistError(_why(e)) from None
        parsed = _parse_ops(res.text)
        new, problems, regions = apply_ops(parsed["ops"], texts, items, words)
        if problems:
            raise AssistError("The AI's changes could not be placed in your files: " + "; ".join(problems[:3])
                              + ". Try saying more precisely where, or select the passage first.")
    changed = {k: v for k, v in new.items() if texts.get(k) != v}
    fixed, warns = check(p, {k: texts[k] for k in changed}, changed, regions)
    for n in parsed["notes"]:
        warns.insert(0, {"level": "info", "text": n[:600]})
    if not changed:
        return {"kind": "none", "summary": parsed["summary"] or "The AI proposed no change.", "how": "ai", "model": res.model,
                "warnings": warns}
    old = {k: texts[k] for k in fixed if texts.get(k) != fixed[k]}
    return {"kind": "edit", "summary": parsed["summary"] or "Changes proposed by the AI.", "old": old,
            "new": {k: fixed[k] for k in old}, "warnings": warns, "alternatives": _alts_from(warns), "how": "ai",
            "model": res.model}


class _Hooks(RunHooks):
    def __init__(self, p: Project):
        self.p = p

    def on_call(self, record: dict) -> None:
        self.p.log_call({**record, "stage": "editor", "at": time.time()})


def _why(e: LLMError) -> str:
    msg = str(e)
    if getattr(e, "kind", "") == "limits" and getattr(e, "retry_at", 0):
        return msg + f" Try the command again after {clock(e.retry_at)}."
    return msg


# --------------------------------------------------------------- the entry

def run(p: Project, command: str, where: dict | None = None) -> dict:
    """Carry out one command as a proposal."""
    command = re.sub(r"\s+", " ", str(command or "")).strip()
    if not command:
        raise AssistError("Say what should change.")
    if not W.exists(p):
        raise AssistError("Open the editor first.")
    if not W.fresh_numbers(p) and W.paperlib.find_engine():
        W.compile_project(p)                      # the numbers the author sees are those of a compiled PDF
    texts = {rel: W.read_text(p, rel)["text"] for rel in W.files(p) if W._kind(rel) == "text"}
    ol = W.outline(p)
    where = where or {}
    out = rule_command(p, command, texts, ol)    # a plain rearrangement: done by code (its errors are the author's to see)
    if out is None:
        out = ai_command(p, command, texts, ol, where)
    extra = {k: v for k, v in out.items() if k in ("navigate", "answer", "question", "model")}
    extra["kind"] = out["kind"]
    if out["kind"] == "edit":
        prop = _proposal(p, command, out["old"], out["new"], out["summary"], out.get("how", "rule"), out.get("warnings", []),
                         out.get("alternatives", []), {"model": out.get("model", "")})
        return prop
    prop = {"id": uuid.uuid4().hex[:16], "at": time.time(), "command": command, "summary": out.get("summary", ""),
            "how": out.get("how", "rule"), "hunks": [], "warnings": out.get("warnings", []),
            "alternatives": [{"label": a["label"], "command": a["command"]} for a in out.get("alternatives", [])], **extra}
    return prop


def apply(p: Project, proposal_id: str, keep: list[str] | None = None) -> dict:
    """Write the accepted changes. `keep` lists the ids of the changes to make; None makes all of them."""
    prop = load_proposal(p, proposal_id)
    if prop.get("applied"):
        raise AssistError("This proposal was already applied.")
    keep_set = set(keep) if keep is not None else None
    with p._lock:
        for path, h in prop["base"].items():
            cur = W.read_text(p, path)["sha"] if (W.root(p) / path).exists() else W.sha("")
            if cur != h:
                raise AssistError(f"{path} changed after the proposal was made. Give the command again.")
        result: dict[str, str] = {}
        for path in prop["new"]:
            result[path] = apply_hunks(prop["old"][path], prop["new"][path], keep_set, path)
            if keep_set is not None and path.endswith(".tex"):     # ticking half of a pair (\begin without its \end)
                was = set(check_balance(prop["old"][path])) | set(check_balance(prop["new"][path]))
                broken = [x for x in check_balance(result[path]) if x not in was]
                if broken:
                    raise AssistError(f"The changes you ticked in {path} belong together with one you left out "
                                      f"({broken[0]}). Tick both, or accept all of them.")
        changed = {k: v for k, v in result.items() if v != prop["old"][k]}
        if not changed:
            return {"ok": True, "files": {}, "changed": 0}
        W.snapshot(p, f"Before: {prop['command'][:120]}", "ai", force=False)
        out = {}
        for path, text in changed.items():
            r = W.write_text(p, path, text)
            out[path] = {"text": text, "sha": r["sha"]}
        W.snapshot(p, f"AI: {prop['command'][:120]}", "ai", force=True)
        prop["applied"] = time.time()
        _save(p, prop)
    return {"ok": True, "files": out, "changed": len(changed)}


def explain_error(p: Project, error: dict) -> dict:
    """A compile error, explained, with the fix proposed like any command."""
    path, line, msg = error.get("path") or "", int(error.get("line") or 0), str(error.get("message") or "")
    cmd = (f"Fix the LaTeX error “{msg}” reported at {path} line {line}. Change as little as possible, and nothing "
           "in the mathematics.")
    return run(p, cmd, {"path": path, "line": line})
