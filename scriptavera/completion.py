# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Completing a proof the notes only sketch, by following the sketch, and only with the author's agreement.

How the completion is kept to the sketch:
  1. The sketch's own words are never rewritten. It is divided into its steps (each a quotation of the sketch, found
     in it by the code), and the writer only writes what is inserted after each step. Nothing of the sketch can be
     changed, dropped or reordered, and the statement is never handed over for editing.
  2. Every addition is screened by code: balanced LaTeX; no new result, label, section or citation; no reference to a
     later result; a believable length; symbols that appear from nowhere are listed for the faithfulness check.
  3. Every computation in the additions is checked exactly by the computer; a false one sends the completion back.
  4. An independent reading judges each addition: faithful to its step, a new idea, or wrong; and whether the whole
     proves exactly the statement.
  5. The completed proof then goes through the same proof check as every other proof, with a second opinion.
The writer gets one chance to answer what the checks found. A completion that still fails is not offered; the
author is told which step could not be completed and why. One that passes is put to the author, who replaces the
sketch with it, edits it first, asks for changes, or keeps the sketch."""
from __future__ import annotations

import re

from . import compute, prompts
from .pipeline import Run, _room, notation_text
from .store import Project
from .texutil import check_balance, clean_fragment
from .util import est_tokens, extract_tag, fit, loads_lenient

KINDS = ("sketch",)                          # how the assessment describes a proof that is to be completed
_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\$([^$]+?)\$|\\begin\{(align\*?|equation\*?|gather\*?|multline\*?)\}(.+?)\\end\{\4\}", re.S)
_GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
          "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
          "Sigma", "Phi", "Psi", "Omega"}
_INTRO = re.compile(r"(?:let|write|put|set|denote|define|call|choose|fix|take)\b[^.]{0,40}$", re.I)


def _flex(quote: str) -> str:
    return r"\s+".join(re.escape(t) for t in quote.split())


def _math_open(text: str) -> bool:
    """True if `text` ends inside a formula (a cut there would break the LaTeX)."""
    t = re.sub(r"\\\$", "", re.sub(r"(?<!\\)%.*", "", text))
    if (t.count("$") - 2 * t.count("$$")) % 2 or t.count("$$") % 2:
        return True
    if len(re.findall(r"\\\[", t)) > len(re.findall(r"\\\]", t)):
        return True
    stack = []
    for m in re.finditer(r"\\(begin|end)\{([^{}]+)\}", t):
        if m.group(1) == "begin":
            stack.append(m.group(2))
        elif stack and stack[-1] == m.group(2):
            stack.pop()
    return bool(stack)


def _sentence_end(text: str, i: int) -> int:
    """The end of the sentence that position i is in: an addition goes between sentences, never inside one (and so
    never between "gives" and the formula it introduces)."""
    if i > 0 and text[i - 1] in ".!?" and (i == len(text) or text[i].isspace()):
        return i
    if re.search(r"(?:\\\]|\$\$|\\end\{[^{}]+\})\s*$", text[:i]) and _new_sentence(text, i):
        return _after_punct(text, i)                     # the quotation ends with a display, and a new sentence follows
    j = i
    while j < len(text):
        if text.startswith("\n\n", j) or re.match(r"\n\s*\n", text[j:j + 4]):
            return j
        m = re.match(r"\\begin\{([^{}]+)\}", text[j:])
        if m:
            close = text.find("\\end{" + m.group(1) + "}", j)
            j = len(text) if close < 0 else close + len(m.group(1)) + 6
            if _new_sentence(text, j):
                return _after_punct(text, j)
            continue
        for a, b in (("$$", "$$"), ("\\[", "\\]"), ("$", "$")):
            if text.startswith(a, j) and not (a == "$" and j > 0 and text[j - 1] == "\\"):
                close = text.find(b, j + len(a))
                j = len(text) if close < 0 else close + len(b)
                if a != "$" and _new_sentence(text, j):        # a display followed by a capital starts a new sentence
                    return _after_punct(text, j)
                break
        else:
            if text[j] in ".!?" and (j + 1 == len(text) or text[j + 1].isspace()):
                return j + 1
            j += 1
    return len(text)


def _new_sentence(text: str, j: int) -> bool:
    return bool(re.match(r"\s*[.;]?\s*[A-Z]", text[j:]))


def _after_punct(text: str, j: int) -> int:
    m = re.match(r"\s*[.;,]", text[j:])
    return j + m.end() if m else j


def locate_steps(sketch: str, steps: list[dict]) -> tuple[list[dict], str]:
    """Find each step's quotation in the sketch, in order, without overlap. Its addition goes at the end of the
    sentence the quotation ends in, outside any formula. Returns the located steps and a problem, or ""."""
    out, pos, last = [], 0, 0
    for n, s in enumerate(steps, 1):
        q = str(s.get("quote") or "").strip()
        m = re.search(_flex(q), sketch[pos:]) if q else None
        if not m:
            return out, f"step {n} is not quoted exactly from the sketch"
        stop = pos + m.end()
        while stop < len(sketch) and _math_open(sketch[:stop]):     # a quotation ending inside a formula: after it
            nxt = re.search(r"\$\$|\\\]|\$|\\end\{[^{}]+\}", sketch[stop:])
            if not nxt:
                return out, f"step {n} ends inside a formula"
            stop += nxt.end()
        end = max(_sentence_end(sketch, stop), last)
        if _math_open(sketch[:end]):
            return out, f"step {n} ends inside a formula"
        out.append({"n": n, "quote": q, "claim": str(s.get("claim") or "").strip(),
                    "missing": str(s.get("missing") or "").strip(), "end": end})
        pos, last = stop, end
    return out, ""


def assemble(sketch: str, steps: list[dict], additions: dict[int, str]) -> tuple[str, list[dict]]:
    """The sketch with each addition inserted after its step. Also the same text as parts, marked as the author's
    or as added, for the author to see what is new."""
    parts, pos = [], 0
    for s in steps:
        parts.append({"text": sketch[pos:s["end"]], "added": False})
        add = (additions.get(s["n"]) or "").strip()
        if add:
            parts.append({"text": add, "added": True, "step": s["n"]})
        pos = s["end"]
    parts.append({"text": sketch[pos:], "added": False})
    parts = [p for p in parts if p["text"]]
    text = ""
    for i, p in enumerate(parts):
        if p["added"]:
            nxt = parts[i + 1]["text"] if i + 1 < len(parts) else ""
            text = text.rstrip() + " " + p["text"].strip() + (" " if nxt and not nxt[:1].isspace() else "")
        else:
            text += p["text"]
    return text.strip(), parts


def symbols(tex: str) -> set[str]:
    """The letters and Greek letters used in the formulas of a passage (indices and operators included)."""
    out: set[str] = set()
    for m in _MATH.finditer(tex or ""):
        body = next(g for g in m.groups() if g and g not in ("align", "align*", "equation", "equation*", "gather", "gather*", "multline", "multline*"))
        body = re.sub(r"\\(?:text|mathrm|operatorname|mbox)\{[^{}]*\}", " ", body)
        out.update(g for g in re.findall(r"\\([A-Za-z]+)", body) if g in _GREEK)
        out.update(re.findall(r"(?<![\\A-Za-z])([A-Za-z])(?![A-Za-z])", re.sub(r"\\[A-Za-z]+", " ", body)))
    return out


def introduced(tex: str, sym: str) -> bool:
    """Whether a passage introduces the symbol with its meaning ("write $d = ...$", "let $d$ be ...")."""
    for m in _MATH.finditer(tex or ""):
        if sym in symbols(m.group(0)):
            before = tex[max(0, m.start() - 60):m.start()]
            inside = m.group(0)
            if _INTRO.search(before) or re.search(r"(?<![A-Za-z\\])" + re.escape(sym if len(sym) == 1 else "\\" + sym) + r"\s*(?::=|=)", inside):
                return True
            return False
    return False


def screen(additions: dict[int, str], ctx: dict) -> tuple[list[str], list[str]]:
    """What the code can rule out by itself. Returns (problems, new symbols for the faithfulness check)."""
    problems, new = [], []
    known_refs, later, cited = ctx["labels"], ctx["later"], ctx["cited"]
    for n, add in additions.items():
        if not add:
            continue
        if check_balance(add):
            problems.append(f"the addition to step {n} has unbalanced LaTeX ({check_balance(add)[0]})")
        if re.search(r"\\begin\{(?:theorem|lemma|proposition|corollary|claim|definition|proof)\*?\}|\\(?:sub)*section\*?\{|\\label\{", add):
            problems.append(f"the addition to step {n} states a new result, a label or a section")
        for r in re.findall(r"\\(?:eq|auto|c)?ref\{([^{}]+)\}", add):
            if r in later:
                problems.append(f"the addition to step {n} uses {r}, which comes after this result")
            elif r not in known_refs:
                problems.append(f"the addition to step {n} refers to {r}, which is not in the paper")
        for keys in re.findall(r"\\cite[tp]?\*?(?:\[[^\]]*\])?\{([^{}]+)\}", add):
            for k in (x.strip() for x in keys.split(",")):
                if k and k not in cited:
                    problems.append(f"the addition to step {n} cites {k}, which the paper does not cite")
        quote_len = max(len(ctx["steps"][n]["quote"]) if n in ctx["steps"] else 0, 80)
        if len(add) > 8 * quote_len + 1200:
            problems.append(f"the addition to step {n} is far longer than its step")
        for s in sorted(symbols(add) - ctx["known"]):
            if not introduced(add, s):
                new.append(f"step {n}: {s if len(s) == 1 else chr(92) + s}")
    total = sum(len(a) for a in additions.values() if a)
    if total > 5 * len(ctx["sketch"]) + 3000:
        problems.append("the additions are far longer than the sketch")
    return problems, new


def _additions(text: str, steps: list[dict]) -> tuple[dict[int, str], dict[int, str]]:
    adds, fails = {}, {}
    for m in re.finditer(r"<step\s+n\s*=\s*[\"']?(\d+)[\"']?\s*>(.*?)(?:</step\s*>|(?=<(?:step|fail)\b)|\Z)", text or "", flags=re.S | re.I):
        t = clean_fragment(m.group(2)).strip()
        adds[int(m.group(1))] = "" if t.strip(" .").upper() == "NONE" else t
    for m in re.finditer(r"<fail\s+n\s*=\s*[\"']?(\d+)[\"']?\s*>(.*?)(?:</fail\s*>|(?=<(?:step|fail)\b)|\Z)", text or "", flags=re.S | re.I):
        fails[int(m.group(1))] = re.sub(r"\s+", " ", m.group(2)).strip()
    valid = {s["n"] for s in steps}
    return {n: a for n, a in adds.items() if n in valid}, {n: f for n, f in fails.items() if n in valid}


def _squash(s: str) -> str:
    return re.sub(r"\s+|\$", "", s or "")


def _in_added(quote: str, additions: dict[int, str]) -> bool:
    q = _squash(quote)
    return len(q) >= 6 and any(q[:60] in _squash(a) for a in additions.values() if a)


def complete_one(run: Run, u: dict, env: dict, note: str = "", previous: dict | None = None) -> dict:
    """Try to complete one sketched proof. Returns {"status": "ready", ...} or {"status": "failed", "reason": ...}."""
    from .drawings import describe
    from .revision import _statements, check_proof, computer_checks
    sketch = u["proof"] or ""
    rec = {"label": u["label"], "name": u["name"], "sketch": sketch, "statement": u["statement"], "status": "failed", "reason": "", "note": note}
    pics = describe((env.get("drawings") or {}).get(u["label"]) or [])   # the author's own drawings for this proof
    rec["drawings"] = len((env.get("drawings") or {}).get(u["label"]) or [])
    head = (f"SETTING AND NOTATION\n{env['setting'] or '(none stated)'}\n\n"
            f"RESULTS STATED EARLIER (they may be used)\n{fit(_statements(env['units'], upto=u['at']), 4000, 'tail')}\n\n"
            f"THE STATEMENT (fixed; do not change it)\n{u['name']} [{u['label']}]\n{u['statement']}\n\nTHE SKETCH\n{sketch}"
            + (f"\n\n{pics}" if pics else ""))

    # 1. the sketch's steps, each a quotation found in it by the code
    steps, why = (previous or {}).get("steps") or [], ""
    if not steps:
        for attempt in range(2):
            res = run.ask("reason", prompts.SKETCH_STEPS.format(field=run.field), head + (f"\n\nYOUR LAST ANSWER WAS REFUSED: {why}. Quote the sketch exactly." if why else ""),
                          json_mode=True, max_out=2500, effort="high", temperature=0.0, label=f"Divide the sketch of {u['name']} into steps")
            data = loads_lenient(res.text)
            raw = (data.get("steps") if isinstance(data, dict) else data) or []
            steps, why = locate_steps(sketch, [s for s in raw if isinstance(s, dict)])
            if not why and steps:
                break
        if why or not steps:
            rec["reason"] = f"the sketch could not be divided into its steps reliably ({why or 'no steps'})."
            return rec
    rec["steps"] = [{k: s[k] for k in ("n", "quote", "claim", "missing", "end")} for s in steps]
    listing = "\n".join(f"Step {s['n']}. Quote: {s['quote']}\n   Claim: {s['claim']}\n   Missing: {s['missing'] or 'none'}" for s in steps)
    ctx = {**env["screen"], "sketch": sketch, "steps": {s["n"]: s for s in steps},
           "known": env["known"] | symbols(sketch) | symbols(u["statement"]) | symbols(pics),
           "later": {x["label"] for x in env["units"] if x["at"] >= u["at"] and x["label"]}}
    system = prompts.COMPLETE_SKETCH.format(field=run.field)
    feedback = ""
    if note and previous and previous.get("additions"):
        feedback = ("YOUR EARLIER ADDITIONS\n" + "\n".join(f"Step {n}: {a or 'NONE'}" for n, a in previous["additions"].items())
                    + f"\n\nTHE AUTHOR'S INSTRUCTION FOR THIS COMPLETION\n{note}\nFollow it, within the rules.")
    elif note:
        feedback = f"THE AUTHOR'S INSTRUCTION FOR THIS COMPLETION\n{note}\nFollow it, within the rules."

    for attempt in range(2):
        # 2. the additions: only what goes after each step
        user = head + f"\n\nTHE SKETCH, DIVIDED INTO STEPS\n{listing}" + (f"\n\n{feedback}" if feedback else "")
        res = run.ask("writer", system, fit(user, _room("writer", system, 6000), "middle"),
                      max_out=min(12000, int(est_tokens(sketch) * 6) + 2500), effort="high", temperature=0.0, label=f"Complete the sketch of {u['name']}")
        rec["written_by"] = res.model
        adds, fails = _additions(res.text, steps)
        if fails:
            n, f = sorted(fails.items())[0]
            rec.update(reason=f"step {n} could not be completed by the idea the sketch gives for it: {f}", step=n, additions=adds)
            return rec                                         # the writer says the step does not go through: that is the answer
        if not any(adds.values()):
            rec["reason"] = "nothing was added: the sketch may already be complete."
            return rec
        problems, new = screen(adds, ctx)
        proof, parts = assemble(sketch, steps, adds)
        if not problems and compute.AVAILABLE:                 # 3. every computation in the additions, exactly
            comp = computer_checks(run, {"statement": u["statement"], "proof": proof, "name": u["name"]}, env["glossary"], env["notation"])
            problems += [f"exact computation shows that this added step is false: {c['tex']} ({c['detail']})"
                         for c in comp if c["status"] == "counterexample" and _in_added(c["tex"], adds)]
        verdicts: list[str] = []
        if not problems:                                       # 4. faithful to the sketch?
            check = (f"RESULTS STATED EARLIER\n{fit(_statements(env['units'], upto=u['at']), 3000, 'tail')}\n\n"
                     f"THE STATEMENT\n{u['statement']}\n\nTHE SKETCH, DIVIDED INTO STEPS\n{listing}\n\n"
                     + (f"{pics}\n\n" if pics else "") +
                     "THE ADDITIONS\n" + "\n\n".join(f"Addition {n} (inserted after step {n}):\n{a}" for n, a in sorted(adds.items()) if a)
                     + (f"\n\nSYMBOLS THAT APPEAR ONLY IN THE ADDITIONS\n" + "\n".join(new) if new else ""))
            chk = run.ask("reason", prompts.COMPLETE_FAITHFUL.format(), check, max_out=1800, effort="high", temperature=0.0,
                          label=f"Check the completion of {u['name']} against the sketch")
            rec["checked_by"] = chk.model
            for m in re.finditer(r"<addition\s+n\s*=\s*[\"']?(\d+)[\"']?\s*>(.*?)(?:</addition\s*>|(?=<addition\b)|\Z)", chk.text, flags=re.S | re.I):
                body = m.group(2)
                v = (re.search(r"verdict\s*:\s*(.+)", body, re.I) or [None, ""])[1].lower()
                r = (re.search(r"reason\s*:\s*(.+)", body, re.I) or [None, ""])[1].strip()
                if "new" in v or "wrong" in v:
                    problems.append(f"addition {m.group(1)} was judged {'a new idea' if 'new' in v else 'wrong'}: {r}")
                verdicts.append(v.strip())
            if not extract_tag(chk.text, "proves").strip().lower().startswith("yes"):
                problems.append("the completed proof does not prove exactly the statement: " + re.sub(r"\s+", " ", extract_tag(chk.text, "reason")).strip())
        if not problems:                                       # 5. the proof check, with a second opinion
            issues = check_proof(run, {**u, "proof": proof}, env, label_prefix="completed proof of ")
            problems += [f"the proof check found ({c['severity']}) at “{c['quote']}”: {c['problem']}"
                         for c in issues if _in_added(c["quote"], adds) or not c["quote"]]
        if not problems:
            rec.update(status="ready", additions={str(n): a for n, a in adds.items()}, proof=proof, parts=parts, reason="",
                       added=sum(len(a) for a in adds.values() if a), steps_completed=sum(1 for a in adds.values() if a))
            return rec
        rec["reason"] = problems[0]
        rec["additions"] = {str(n): a for n, a in adds.items()}
        feedback = ("YOUR PREVIOUS ADDITIONS\n" + "\n".join(f"Step {n}: {a or 'NONE'}" for n, a in sorted(adds.items()))
                    + "\n\nWHAT THE CHECKS FOUND (correct these, within the rules; if a step cannot be completed by its own idea, FAIL it)\n"
                    + "\n".join(f"- {p}" for p in problems) + (f"\n\nTHE AUTHOR'S INSTRUCTION\n{note}" if note else ""))
    rec["reason"] = f"the completion did not pass the checks: {rec['reason']}"
    return rec


def environment(p: Project, blocks: list[dict], units: list[dict]) -> dict:
    from .revision import _definitions, all_labels
    from .writing import cited_keys
    d = p.load("dossier") or {}
    confirmed = p.load("confirmed", {}) or {}
    setting = "\n\n".join(x for x in (d.get("setting", ""), _definitions(blocks), "CONFIRMED NOTATION\n" + notation_text(confirmed, 1500)) if x.strip())
    known = symbols(setting) | {s for u in units for s in symbols(u["statement"])}
    known |= {s for g in confirmed.get("glossary", []) for s in symbols("$" + str(g.get("symbol", "")) + "$")}
    from . import drawings, paper as paperlib
    return {"setting": fit(setting, 2500, "head"), "units": units, "glossary": confirmed.get("glossary", []),
            "notation": notation_text(confirmed, 1500), "known": known, "drawings": drawings.for_results(p, units),
            "screen": {"labels": all_labels(blocks), "cited": set(cited_keys([paperlib.blocks_to_latex(blocks), (p.load("intro") or {}).get("text", "")]))}}


def sketched(p: Project, units: list[dict]) -> list[dict]:
    """The results whose proof the assessment of the notes calls a sketch, and that have a proof in the draft."""
    res = (p.load("assessment") or {}).get("results") or {}
    out = []
    for u in units:
        rid = u["label"].split(":", 1)[-1]
        if u["proof"] and (res.get(rid) or {}).get("proof") in KINDS:
            out.append(u)
    return out


def stage(run: Run, blocks: list[dict], units: list[dict], sig: str) -> None:
    """Part of the proof check: every sketched proof is completed, if it can be, and kept for the author's decision."""
    p = run.p
    state = p.load("completion") or {}
    if state.get("sig") != sig:
        state = {"sig": sig, "items": {}}
    todo = sketched(p, units)
    if not todo:
        state["done"] = True
        p.save("completion", state)
        return
    env = environment(p, blocks, units)
    for i, u in enumerate(todo):
        if u["label"] in state["items"]:
            continue
        run.progress(i, len(todo), f"completing the sketch of {u['name']} by its own steps")
        rec = complete_one(run, u, env)
        state["items"][u["label"]] = rec
        p.save("completion", state)
        if rec["status"] == "ready":
            run.log(f"{u['name']}: the sketch was completed by its own steps ({rec['steps_completed']} step(s) filled in) and passed "
                    "every check. It is put to you; nothing changes unless you accept it.")
        else:
            run.log(f"{u['name']}: the sketch could not be completed safely, so none is offered: {rec['reason']}", "warn")
    state["done"] = True
    p.save("completion", state)


def items(p: Project) -> tuple[list[dict], list[dict]]:
    """Completions ready for the author, and remarks about sketches that could not be completed."""
    state = p.load("completion") or {}
    ready, failed = [], []
    for label, rec in (state.get("items") or {}).items():
        if rec.get("status") == "ready":
            ready.append({"id": "C" + re.sub(r"\W", "", label), **{k: rec[k] for k in ("label", "name", "sketch", "proof", "parts", "steps", "statement")}})
        else:
            failed.append({"id": "CF" + re.sub(r"\W", "", label), "source": "proof check", "where": label,
                           "comment": f"The proof of {rec['name']} is a sketch, and it could not be completed by following its steps: {rec['reason']}",
                           "action": "Complete the argument, or give a reference."})
    return ready, failed
