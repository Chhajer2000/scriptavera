# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Revision: three critical readings of the first draft, then the author's decisions.

  1. Shorter arguments   the whole draft is re-read for arguments carried out twice, methods that can be stated
                         once as a general lemma, and proofs that take a long way round. Every proposal is written
                         out in full and then examined by a second, independent call. Only what passes is offered.
  2. Proof check         each proof is examined on its own, step by step. Every suspected problem goes to a second
                         opinion, and only what that upholds is reported.
  3. Referee's read      the whole paper, read as a journal referee reads a submission.

Then the author decides. Anything that touches the mathematics (a rewritten proof, a correction, a new lemma) is
applied only if the author accepts it. Anything that is only language is never put to the author at all; it is
dealt with in the final copy-edit.
"""

from __future__ import annotations

import hashlib
import re
import time

from . import completion, compute, paper as paperlib, prompts
from .llm import LLMError
from .pipeline import Run, _room, emit, notation_text, passing
from .store import Project
from .texutil import check_balance, clean_fragment
from .util import est_tokens, extract_tag, fit, loads_lenient
from .writing import fields, skeleton

RESULT_ENVS = ("theorem", "lemma", "proposition", "corollary", "claim")
_LABEL = r"[A-Za-z]+:[A-Za-z0-9_.\-]*[A-Za-z0-9]"        # never ends in the full stop of the sentence it stands in

# ----------------------------------------------------------------- units

def result_units(blocks: list[dict]) -> list[dict]:
    """Every stated result with its proof and its place in the paper."""
    temp = {"intro": "x", "blocks": [dict(b) for b in blocks], "figures": {}}
    paperlib.number_blocks(temp)
    units: list[dict] = []
    section = ""
    for i, b in enumerate(temp["blocks"]):
        if b["type"] == "section":
            section = b["title"]
        if b["type"] != "env" or b.get("env") not in RESULT_ENVS:
            continue
        proof_at = None
        for j in range(i + 1, len(blocks)):
            t = blocks[j]["type"]
            if t == "proof":
                proof_at = j
                break
            if t in ("section", "subsection") or (t == "env" and blocks[j].get("env") in RESULT_ENVS):
                break
        name = f"{paperlib.THEOREM_ENVS.get(b['env'], b['env'].capitalize())} {b.get('num', '')}".strip()
        units.append({"label": b.get("label") or "", "env": b["env"], "name": name, "statement": b.get("text", ""), "title": b.get("title") or "",
                      "proof": blocks[proof_at].get("text", "") if proof_at is not None else None,
                      "at": i, "proof_at": proof_at, "section": section, "page": b.get("page", 0)})
    return units


def _unit(units: list[dict], label: str) -> dict | None:
    return next((u for u in units if u["label"] == label), None)


def _statements(units: list[dict], upto: int | None = None, extra: dict | None = None) -> str:
    """The statements of the results in the order of the paper; with `upto`, only those standing before it."""
    lines = []
    for u in units:
        if upto is not None and u["at"] >= upto:
            break
        if extra and extra.get("before") == u["label"]:
            lines.append(f"[{extra['label']}] New {extra['kind']}. {extra['statement']}")
        lines.append(f"[{u['label']}] {u['name']}. {u['statement']}")
    return "\n\n".join(lines) or "(none)"


def _definitions(blocks: list[dict]) -> str:
    return "\n\n".join(b["text"] for b in blocks if b["type"] == "env" and b.get("env") in ("definition", "notation"))


def body_sig(p: Project) -> str:
    body = p.load("body") or {}
    text = paperlib.blocks_to_latex(body.get("blocks", [])) + (p.load("intro") or {}).get("text", "")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _labels_in(text: str, known: set[str]) -> list[str]:
    return [l for l in dict.fromkeys(re.findall(_LABEL, text or "")) if l in known]


# ----------------------------------------------------- 1. shorter arguments

def all_labels(blocks: list[dict]) -> set[str]:
    """Everything in the paper that a \\ref may point to."""
    out = {b["label"] for b in blocks if b.get("label")}
    for b in blocks:
        out.update(re.findall(r"\\label\{([^{}]+)\}", b.get("text") or ""))
        out.update("fig:" + f for f in re.findall(r"\\insertfigure\{([^{}]+)\}", b.get("text") or ""))
    return out


def _parse_rewrite(text: str) -> tuple[dict | None, dict[str, str]]:
    new = None
    first = re.search(r"<proof\s+for", text or "", flags=re.I)
    head = (text or "")[:first.start()] if first else (text or "")     # a missing closing tag must not swallow the proofs
    got = fields(head, "new_result", ("before", "kind", "statement", "proof"))
    if got and got[0].get("statement") and got[0].get("proof"):
        g = got[0]
        statement = clean_fragment(g["statement"])            # the statement only: no environment around it, no label in it
        statement = re.sub(r"\\(?:begin|end)\{(?:lemma|proposition|theorem|corollary|claim)\*?\}(?:\[[^\]]*\])?", "", statement)
        statement = re.sub(r"\\label\{[^{}]*\}", "", statement).strip()
        new = {"before": (re.findall(_LABEL, g.get("before", "")) or [""])[0],
               "kind": "proposition" if "prop" in g.get("kind", "").lower() else "lemma",
               "statement": statement, "proof": _strip_proof(g["proof"])}
    proofs = {}
    for m in re.finditer(r"<proof\s+for\s*=\s*[\"']?([^\"'>\s]+)[\"']?\s*>(.*?)(?:</proof\s*>|(?=<proof\s)|\Z)", text or "", flags=re.S | re.I):
        proofs[m.group(1).strip()] = _strip_proof(m.group(2))
    return new, proofs


def _strip_proof(text: str) -> str:
    t = clean_fragment(text or "")
    t = re.sub(r"^\s*\\begin\{proof\}(\[[^\]]*\])?", "", t)
    t = re.sub(r"\\end\{proof\}\s*$", "", t)
    t = re.sub(r"^\s*(?:\\textit\{Proof\.?\}|\\emph\{Proof\.?\}|Proof\s*[.:])\s*", "", t)
    return t.strip()


def stage_simplify(run: Run) -> None:
    p = run.p
    blocks = (p.load("body") or {}).get("blocks", [])
    units = result_units(blocks)
    proved = [u for u in units if u["proof"] and u["label"]]
    sig = body_sig(p)
    state = p.load("simplify") or {}
    if state.get("sig") != sig:
        state = {"sig": sig, "found": None, "next": 0, "proposals": [], "dropped": []}
    if len(proved) < 2:
        state.update(found=[], done=True)
        p.save("simplify", state)
        run.log("The paper has fewer than two proofs, so there is nothing to consolidate.")
        return
    known = {u["label"] for u in units if u["label"]}
    body_tex = paperlib.blocks_to_latex(blocks)

    # a. what could be shorter
    if state["found"] is None:
        run.progress(0, 1, "re-reading the whole draft for repeated arguments")
        system = prompts.SIMPLIFY_FIND.format(limit=5)
        res = run.ask("reason", system, "THE DRAFT\n\n" + fit(body_tex, _room("reason", system, 1800), "middle"),
                      max_out=1800, effort="high", label="Find shared arguments")
        found = []
        for f in fields(res.text, "proposal", ("kind", "affects", "idea", "benefit")):
            affects = [l for l in _labels_in(f.get("affects", ""), known) if (_unit(units, l) or {}).get("proof")]
            if affects and f.get("idea"):
                kind = next((k for k in ("same-argument", "general-lemma", "direct-step", "repeated-computation")
                             if k in f.get("kind", "").lower()), "same-argument")
                found.append({"kind": kind, "affects": affects, "idea": re.sub(r"\s+", " ", f["idea"]).strip(),
                              "benefit": re.sub(r"\s+", " ", f.get("benefit", "")).strip()})
        state["found"] = found[:5]
        p.save("simplify", state)
    found = state["found"]
    if not found:
        state["done"] = True
        p.save("simplify", state)
        run.log("No argument is carried out twice and no proof takes a detour: nothing to shorten.")
        return

    # b. each proposal is written out in full, then examined by an independent call
    taken = {l for pr in state["proposals"] for l in pr["affects"]}
    for k in range(state["next"], len(found)):
        f = found[k]
        sid = f"S{k + 1}"
        run.progress(k, len(found), f"working out proposal {k + 1} of {len(found)}")

        def drop(reason: str) -> None:
            state["dropped"].append({"id": sid, **f, "reason": reason})
            state["next"] = k + 1
            p.save("simplify", state)
            run.log(f"Proposal {sid} was set aside: {reason}")

        if taken & set(f["affects"]):
            drop("it overlaps with an earlier proposal for the same proof.")
            continue
        mine = [_unit(units, l) for l in f["affects"]]
        others = [u for l in _labels_in(f["idea"], known) if l not in f["affects"] for u in [_unit(units, l)] if u and u["proof"]]
        user = (f"PROPOSAL\nkind: {f['kind']}\nidea: {f['idea']}\n\n"
                f"STATEMENTS OF ALL RESULTS, IN THE ORDER OF THE PAPER\n{_statements(units)}\n\n"
                "RESULTS WHOSE PROOFS YOU REWRITE\n" + "\n\n".join(
                    f"[{u['label']}] {u['name']}\nStatement: {u['statement']}\nCurrent proof:\n{u['proof']}" for u in mine)
                + ("\n\nOTHER PROOFS THE PROPOSAL REFERS TO (for reference only; do not rewrite them)\n" + "\n\n".join(
                    f"[{u['label']}] {u['name']}\nProof:\n{u['proof']}" for u in others) if others else "")
                + ("\n\nIf you add a general lemma, give it the label res:NEW and refer to it as Lemma~\\ref{res:NEW}."
                   if f["kind"] == "general-lemma" else ""))
        old_total = sum(len(u["proof"]) for u in mine)
        res = run.ask("writer", prompts.SIMPLIFY_WRITE.format(), user, max_out=int(est_tokens("x" * old_total) * 1.4) + 1500,
                      effort="high", temperature=0.1, label=f"Rewrite for {sid}")
        new, proofs = _parse_rewrite(res.text)
        if f["kind"] != "general-lemma":
            new = None
        after = {l: proofs.get(l, "") for l in f["affects"]}
        if any(not t.strip() for t in after.values()):
            drop("the rewritten proofs came back incomplete.")
            continue
        new_label = f"res:{sid}"
        if new:
            first = min(u["at"] for u in mine)
            anchor = _unit(units, new["before"])
            if not anchor or anchor["at"] > first:
                new["before"] = next(u["label"] for u in units if u["at"] == first)
            new["label"] = new_label
            new["statement"] = new["statement"].replace("res:NEW", new_label)
            after = {l: t.replace("res:NEW", new_label) for l, t in after.items()}
            new["proof"] = new["proof"].replace("res:NEW", new_label)
        texts = list(after.values()) + ([new["statement"], new["proof"]] if new else [])
        if any(check_balance(t) for t in texts):
            drop("the rewritten text had unbalanced LaTeX.")
            continue
        allowed = all_labels(blocks) | ({new_label} if new else set())
        refs = {r for t in texts for r in re.findall(r"\\(?:eq)?ref\{([^{}]+)\}", t)}
        if refs - allowed:
            drop("it referred to a result that is not in the paper.")
            continue
        new_total = sum(len(t) for t in texts)
        if new_total >= old_total * (1.1 if new else 0.97):
            drop("writing it out did not make the paper shorter.")
            continue
        if all(after[l].split() == (_unit(units, l)["proof"] or "").split() for l in f["affects"]):
            drop("the rewritten proofs were the same as the originals.")
            continue
        check_user = (f"STATEMENTS OF ALL RESULTS, IN THE ORDER OF THE PAPER\n{_statements(units, extra=new)}\n\n"
                      "ORIGINAL PROOFS\n" + "\n\n".join(f"[{u['label']}] {u['name']}\n{u['proof']}" for u in mine + others)
                      + "\n\nREWRITTEN PROOFS\n" + "\n\n".join(f"[{l}] {_unit(units, l)['name']}\n{after[l]}" for l in f["affects"])
                      + (f"\n\nNEW {new['kind'].upper()} [{new_label}], placed before [{new['before']}]\nStatement: {new['statement']}\n"
                         f"Proof:\n{new['proof']}" if new else ""))
        chk = run.ask("reason", prompts.SIMPLIFY_CHECK.format(), check_user, max_out=900, effort="high", label=f"Check {sid}")
        verdict = extract_tag(chk.text, "verdict").strip().lower()
        reason = re.sub(r"\s+", " ", extract_tag(chk.text, "reason")).strip()
        if not verdict.startswith("valid"):
            drop("the independent check did not confirm it" + (f" ({reason})" if reason else "."))
            continue
        state["proposals"].append({
            "id": sid, "kind": f["kind"], "affects": f["affects"], "idea": f["idea"], "benefit": f["benefit"],
            "names": {u["label"]: u["name"] for u in mine}, "before": {u["label"]: u["proof"] for u in mine},
            "after": after, "new": new, "check": reason, "saved": old_total - new_total,
            "written_by": res.model, "checked_by": chk.model})
        taken |= set(f["affects"])
        state["next"] = k + 1
        p.save("simplify", state)
    state["done"] = True
    p.save("simplify", state)
    run.progress(len(found), len(found), "proposals ready")
    n = len(state["proposals"])
    run.log(f"{len(found)} possible simplification(s) found; {n} passed the independent check and "
            + ("will be put to you." if n else "none will be put to you."))


# ------------------------------------------------------------ 2. proof check

def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _translator(run: Run, notation: str):
    """The model as translator only: formulas the code cannot read, into a plain syntax the code checks."""
    def translate(items: list[dict], ctx) -> dict:
        listing = "\n".join(f"{it['id']}: {it['tex']}" for it in items)
        res = run.ask("fast", prompts.COMPUTE_TRANSLATE.format(), f"NOTATION OF THE PAPER\n{notation}\n\nFORMULAS\n{listing}",
                      json_mode=True, max_out=2000, effort="low", label="Translate formulas for the computer check")
        data = loads_lenient(res.text)
        out = {}
        for item in (data.get("steps") if isinstance(data, dict) else data) or []:
            if isinstance(item, dict) and item.get("id"):
                original = next((it["tex"] for it in items if it["id"] == item["id"]), "")
                try:
                    out[str(item["id"])] = compute.from_translation(item, ctx, original)
                except (compute.Unreadable, TypeError, ValueError, AttributeError) as e:
                    out[str(item["id"])] = str(e)
        return out
    return translate


def computer_checks(run: Run, u: dict, glossary: list[dict], notation: str) -> list[dict]:
    try:
        return compute.check_passage(u["statement"], u["proof"] or "", glossary, translate=_translator(run, notation))
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 - the computer check is an aid; the proof check runs without it
        run.log(f"The computer check of {u['name']} could not be done ({type(e).__name__}); the proof check runs without it.", "warn")
        return []


def check_proof(run: Run, u: dict, env: dict, label_prefix: str = "") -> list[dict]:
    """The proof check of one proof, as in stage_verify: a step-by-step reading, then a second opinion on every
    claim. Returns the claims the second opinion upheld."""
    sys_v = prompts.VERIFY.format(field=run.field)
    room = _room("reason", sys_v, 2200)
    context = (f"SETTING AND DEFINITIONS\n{fit(env['setting'], max(800, room // 5), 'head') or '(none stated)'}\n\n"
               f"RESULTS STATED EARLIER (they may be used)\n{fit(_statements(env['units'], upto=u['at']), max(800, room // 3), 'tail')}\n\n"
               f"THE RESULT TO CHECK\n{u['name']} [{u['label']}]\nStatement: {u['statement']}\n\nProof:\n{u['proof']}")
    res = run.ask("reason", sys_v, context, max_out=2200, effort="high", label=f"Check the {label_prefix}{u['name']}")
    claims = [{"severity": next((s for s in ("error", "gap", "slip") if s in f.get("severity", "").lower()), "gap"),
               "quote": f.get("quote", "").strip(), "problem": _norm_space(f["problem"])}
              for f in fields(res.text, "issue", ("severity", "quote", "problem", "correction")) if f.get("problem")]
    if not claims:
        return []
    listing = "\n\n".join(f"{n}. [{c['severity']}] At: {c['quote'] or '(no passage quoted)'}\n   Claim: {c['problem']}\n   Proposed correction: none"
                          for n, c in enumerate(claims, 1))
    adj = run.ask("reason", prompts.ADJUDICATE.format(field=run.field), context + "\n\nCLAIMS OF THE FIRST READER\n" + listing,
                  max_out=1800, effort="high", label=f"Second opinion on the {label_prefix}{u['name']}")
    upheld = set()
    for f in fields(adj.text, "ruling", ("n", "decision", "reason", "correction")):
        m = re.search(r"\d+", f.get("n", ""))
        if m and "upheld" in f.get("decision", "").lower():
            upheld.add(int(m.group(0)))
    return [c for n, c in enumerate(claims, 1) if n in upheld]


def stage_verify(run: Run) -> None:
    p = run.p
    blocks = (p.load("body") or {}).get("blocks", [])
    units = result_units(blocks)
    d = p.load("dossier") or {}
    sig = body_sig(p)
    state = p.load("verify") or {}
    if state.get("sig") != sig:
        state = {"sig": sig, "results": {}}
    setting = "\n\n".join(x for x in (d.get("setting", ""), _definitions(blocks),
                                      "CONFIRMED NOTATION\n" + notation_text(p.load("confirmed", {}), 1500)) if x.strip())
    sys_v = prompts.VERIFY.format(field=run.field)
    sys_a = prompts.ADJUDICATE.format(field=run.field)
    todo = [u for u in units if u["label"]]
    glossary = (p.load("confirmed", {}) or {}).get("glossary", [])
    notation = notation_text(p.load("confirmed", {}), 1500)
    if not compute.AVAILABLE:
        run.log("SymPy is not installed, so no step is checked by computation; run_app.bat installs it on the next start.", "warn")
    cache = p.load("verify_cache", {}) or {}
    for i, u in enumerate(todo):
        if u["label"] in state["results"]:
            continue
        ukey = hashlib.sha256(f"{u['statement']}\n{u['proof']}".encode("utf-8")).hexdigest()[:24]
        if p.state.get("incremental") and ukey in cache:
            state["results"][u["label"]] = {**cache[ukey], "label": u["label"], "name": u["name"], "kept": True}
            p.save("verify", state)
            run.log(f"{u['name']}: the proof is unchanged since the last version, so its check is kept.")
            continue
        run.progress(i, len(todo), f"checking the proof of {u['name']}")
        rec = {"label": u["label"], "name": u["name"], "env": u["env"], "issues": [], "summary": "", "verdict": "correct"}
        if not u["proof"]:
            rec["verdict"] = "cited" if "\\cite" in u["statement"] + u.get("title", "") else "unproved"
            state["results"][u["label"]] = rec
            p.save("verify", state)
            continue
        comp = computer_checks(run, u, glossary, notation) if compute.AVAILABLE else []
        rec["computed"] = comp
        room = _room("reason", sys_v, 2200)
        context = (f"SETTING AND DEFINITIONS\n{fit(setting, max(800, room // 5), 'head') or '(none stated)'}\n\n"
                   f"RESULTS STATED EARLIER (they may be used)\n{fit(_statements(units, upto=u['at']), max(800, room // 3), 'tail')}\n\n"
                   f"THE RESULT TO CHECK\n{u['name']} [{u['label']}]\nStatement: {u['statement']}\n\nProof:\n{u['proof']}")
        if compute.for_model(comp):
            context += "\n\n" + fit(compute.for_model(comp), max(800, room // 4), "head")
        res = run.ask("reason", sys_v, context, max_out=2200, effort="high", label=f"Check proof of {u['name']}")
        rec["model"] = res.model
        rec["summary"] = _norm_space(extract_tag(res.text, "summary"))
        claims = []
        for f in fields(res.text, "issue", ("severity", "quote", "problem", "correction")):
            if not f.get("problem"):
                continue
            sev = next((s for s in ("error", "gap", "slip") if s in f.get("severity", "").lower()), "gap")
            corr = f.get("correction", "").strip()
            claims.append({"severity": sev, "quote": f.get("quote", "").strip(), "problem": _norm_space(f["problem"]),
                           "correction": "" if corr.lower().strip(".") in ("none", "", "n/a") else clean_fragment(corr)})
        said = " ".join(c["quote"] for c in claims).replace(" ", "")
        for c in comp:
            if c["status"] == "counterexample" and c["tex"].replace(" ", "")[:40] not in said:
                claims.append({"severity": "error", "quote": c["tex"], "by": "computer", "correction": "",
                               "problem": f"Exact computation shows that this step is false: {c['detail']}."})
        if claims:
            # a second, independent opinion on every claim; only what it upholds reaches the author
            listing = "\n\n".join(f"{n}. [{c['severity']}] At: {c['quote'] or '(no passage quoted)'}\n   Claim: {c['problem']}\n"
                                  f"   Proposed correction: {c['correction'] or 'none'}" for n, c in enumerate(claims, 1))
            adj = run.ask("reason", sys_a, context + "\n\nCLAIMS OF THE FIRST READER\n" + listing, max_out=1800, effort="high",
                          label=f"Second opinion on {u['name']}")
            rulings = {}
            for f in fields(adj.text, "ruling", ("n", "decision", "reason", "correction")):
                m = re.search(r"\d+", f.get("n", ""))
                if m:
                    rulings[int(m.group(0))] = f
            for n, c in enumerate(claims, 1):
                r = rulings.get(n, {})
                upheld = "upheld" in r.get("decision", "").lower()
                corr = r.get("correction", "").strip()
                if upheld and corr and corr.lower().strip(".") not in ("none", "n/a"):
                    c["correction"] = clean_fragment(corr)
                elif upheld and corr.lower().strip(".") == "none":
                    c["correction"] = ""
                c.update(id=f"V{i + 1}.{n}", status="upheld" if upheld else "dismissed", ruling=_norm_space(r.get("reason", "")),
                         second_model=adj.model)
                if c["correction"] and (check_balance(c["correction"]) or c["correction"].strip() == c["quote"].strip()):
                    c["correction"] = ""
                rec["issues"].append(c)
        if any(c["status"] == "upheld" for c in rec["issues"]):
            rec["verdict"] = "problems"
        state["results"][u["label"]] = rec
        p.save("verify", state)
        cache[ukey] = rec
        p.save("verify_cache", dict(list(cache.items())[-300:]))
    completion.stage(run, blocks, units, sig)          # sketched proofs: completed by their own steps, for the author to decide
    state["done"] = True
    p.save("verify", state)
    upheld = sum(1 for r in state["results"].values() for c in r["issues"] if c["status"] == "upheld")
    dismissed = sum(1 for r in state["results"].values() for c in r["issues"] if c["status"] == "dismissed")
    checked = sum(1 for r in state["results"].values() if r["verdict"] in ("correct", "problems"))
    run.progress(len(todo), len(todo), "proofs checked")
    run.log(f"{checked} proof(s) examined step by step. {upheld} problem(s) stood up to a second opinion"
            + (f"; {dismissed} first suspicion(s) were dismissed by it." if dismissed else "."),
            "warn" if upheld else "info")


# --------------------------------------------------------- 3. referee's read

_AUTO = ("presentation", "notation", "typography")       # dealt with in the copy-edit, never put to the author
_TYPES = ("mathematics", "structure", "presentation", "notation", "citation", "typography")


def stage_referee(run: Run) -> None:
    p = run.p
    body = p.load("body") or {}
    blocks = body.get("blocks", [])
    intro = (p.load("intro") or {}).get("text", "")
    journal = (run.meta.get("journal") or {}).get("name", "")
    system = prompts.REFEREE.format(journal=f" ({journal})" if journal else "", field=run.field)
    library = {x["key"]: x for x in p.load("library", [])}
    from .writing import cited_keys
    cited = [f"[{k}] {library[k]['title']} ({library[k].get('year')})" for k in
             cited_keys([intro, paperlib.blocks_to_latex(blocks)]) if k in library]
    room = _room("reason", system, 3500)
    text = ("INTRODUCTION\n\n" + intro + "\n\n" + (body.get("organisation") or "") + "\n\nBODY\n\n" + paperlib.blocks_to_latex(blocks)
            + "\n\nWORKS CITED\n" + ("\n".join(cited) or "(none)"))
    run.progress(0, 1, "reading the whole paper as a referee")
    res = run.ask("reason", system, fit(text, room, "middle"), max_out=3500, effort="high", label="Referee report")
    points = []
    for f in fields(res.text, "point", ("type", "where", "comment", "action")):
        if not (f.get("comment") or f.get("action")):
            continue
        kind = next((t for t in _TYPES if t in f.get("type", "").lower()), "presentation")
        points.append({"id": f"P{len(points) + 1}", "type": kind, "where": _norm_space(f.get("where", "")),
                       "comment": _norm_space(f.get("comment", "")), "action": _norm_space(f.get("action", "")),
                       "route": "auto" if kind in _AUTO else "author"})
    rec = extract_tag(res.text, "recommendation").strip().lower()
    report = {"sig": body_sig(p), "summary": _norm_space(extract_tag(res.text, "summary")),
              "recommendation": next((r for r in ("minor revision", "major revision", "accept", "reject") if r in rec), ""),
              "points": points[:25], "model": res.model}
    p.save("referee", report)
    mine = sum(1 for x in report["points"] if x["route"] == "author")
    run.progress(1, 1, "report written")
    run.log(f"Referee's report: {len(report['points'])} point(s). {len(report['points']) - mine} concern presentation and are "
            f"dealt with in the copy-edit; {mine} concern the mathematics, the structure or the citations and are for you.")


# ---------------------------------------------------------- the author decides

_QUOTE_HEAD = re.compile(r"^\s*(?:[A-Z][a-z]+\s+[\d.]+\s*)?\[" + _LABEL + r"\]\s*(?:Statement|Proof)?\s*:?\s*")


def _context(p: Project) -> dict:
    """What lets the author check an item against the draft and against the notes: for every result of the draft,
    its statement and proof as written, and the pages of the notes it comes from."""
    from .pipeline import _find
    blocks = (p.load("body") or {}).get("blocks", [])
    units = {u["label"]: u for u in result_units(blocks) if u["label"]}
    dossier = {f"res:{r['id']}": r for r in (p.load("dossier") or {}).get("results", [])}
    approved = (p.load("approved") or {}).get("pages")
    pages = ({int(x["n"]): x.get("text", "") for x in approved} if approved else
             {int(n): v.get("text", "") for n, v in ((p.load("transcript") or {}).get("pages") or {}).items()})
    return {"units": units, "dossier": dossier, "pages": pages, "find": _find}


def _where(ctx: dict, label: str, quote: str = "") -> dict:
    """The draft's text of a result and the pages of the notes it was taken from. The page with the quoted passage
    comes first; then the pages the first reading of the notes gave for the result; then the page the draft gives."""
    u = ctx["units"].get(label)
    pages: list[int] = []
    core = re.sub(r"\s+|\$", "", quote or "")
    if len(core) >= 6:
        pages += [n for n, t in sorted(ctx["pages"].items()) if ctx["find"](t, quote)]
    pages += [n for n in (ctx["dossier"].get(label) or {}).get("pages", []) if n in ctx["pages"]]
    if u and u.get("page") in ctx["pages"]:
        pages.append(u["page"])
    seen: list[int] = []
    for n in pages:
        if n not in seen:
            seen.append(n)
    part = ""
    if u and core:
        squash = lambda x: re.sub(r"\s+|\$", "", x or "")
        part = "statement" if core in squash(u["statement"]) else "proof" if core in squash(u.get("proof") or "") else ""
    return {"label": label, "name": u["name"] if u else "", "statement": u["statement"] if u else "",
            "proof": (u.get("proof") or "") if u else "", "pages": seen[:8], "part": part,
            "quote_pages": [n for n in seen if len(core) >= 6 and ctx["find"](ctx["pages"][n], quote)]}


def decision_items(p: Project) -> dict:
    """Everything of a mathematical nature that came out of the three readings, in the shape the author sees it,
    each with the draft's text it is about and the pages of the notes it comes from, so that it can be checked."""
    simplify = (p.load("simplify") or {}).get("proposals", [])
    issues = []
    remarks = []
    ctx = _context(p)
    for r in ((p.load("verify") or {}).get("results") or {}).values():
        for c in r.get("issues", []):
            if c.get("status") == "upheld":
                quote = _QUOTE_HEAD.sub("", c.get("quote") or "").strip()
                issues.append({**c, "quote": quote, "label": r["label"], "name": r["name"],
                               "context": _where(ctx, r["label"], quote)})
        if r.get("verdict") == "unproved" and r.get("env") != "corollary":
            remarks.append({"id": "U" + re.sub(r"\W", "", r["label"]), "source": "proof check", "where": r["label"],
                            "comment": f"{r['name']} is stated without a proof and without a reference.",
                            "action": "Give a proof or a reference, or say how it follows."})
    for x in (p.load("referee") or {}).get("points", []):
        if x.get("route") == "author":
            lab = next(iter(re.findall(_LABEL, x.get("where") or "")), "")
            remarks.append({"id": x["id"], "source": f"referee, {x['type']}", "where": x["where"], "comment": x["comment"],
                            "action": x["action"], "context": _where(ctx, lab) if lab in ctx["units"] else None})
    for r in remarks:
        if "context" not in r:
            r["context"] = _where(ctx, r["where"]) if r["where"] in ctx["units"] else None
    for pr in simplify:
        labs = [l for l in (pr.get("affects") or []) if l in ctx["units"]]
        if labs:
            w = [_where(ctx, l) for l in labs]
            pr["context"] = {**w[0], "pages": sorted({n for x in w for n in x["pages"]})[:8],
                             "others": [x["label"] for x in w[1:]]}
    complete, failed = completion.items(p)
    for c in complete:
        c["context"] = _where(ctx, c["label"])
    for r in failed:
        r["context"] = _where(ctx, r["where"]) if r["where"] in ctx["units"] else None
    remarks = failed + remarks
    for n, c in enumerate((p.load("dossier") or {}).get("concerns", []), 1):
        remarks.append({"id": f"N{n}", "source": "first reading of your notes", "where": "", "comment": c, "action": ""})
    return {"simplify": simplify, "complete": complete, "issues": issues, "remarks": remarks}


def settle_if_nothing_to_decide(p: Project) -> bool:
    items = decision_items(p)
    if items["simplify"] or items["complete"] or items["issues"] or items["remarks"]:
        return False
    p.save("decisions", {"simplify": {}, "complete": {}, "issues": {}, "remarks": {}, "at": time.time(), "auto": True})
    return True


def submit_decisions(p: Project, data: dict) -> None:
    """The author accepts or rejects each proposed change to the mathematics. Remarks need no answer."""
    items = decision_items(p)
    simplify, issues, remarks = {}, {}, {}
    missing = []
    for pr in items["simplify"]:
        choice = (data.get("simplify") or {}).get(pr["id"])
        if choice not in ("accept", "reject"):
            missing.append(pr["id"])
        simplify[pr["id"]] = choice
    complete = {}
    for c in items["complete"]:
        got = (data.get("complete") or {}).get(c["id"]) or {}
        choice, note, text = got.get("choice"), (got.get("note") or "").strip()[:2000], (got.get("text") or "").strip()
        if (choice == "instruct" and not note) or (choice == "edit" and (not text or check_balance(text))):
            choice = None
        if choice not in ("accept", "edit", "instruct", "keep"):
            missing.append(c["id"])
        complete[c["id"]] = {"choice": choice, "note": note, "text": text if choice == "edit" else ""}
    for c in items["issues"]:
        got = (data.get("issues") or {}).get(c["id"]) or {}
        choice, note = got.get("choice"), (got.get("note") or "").strip()
        if choice == "fix" and not c.get("correction"):
            choice = None
        if choice == "instruct" and not note:
            choice = None
        if choice not in ("fix", "keep", "instruct"):
            missing.append(c["id"])
        issues[c["id"]] = {"choice": choice, "note": note}
    if missing:
        raise ValueError(f"{len(missing)} item(s) still need your decision.")
    known = {r["id"] for r in items["remarks"]}
    for rid, note in (data.get("remarks") or {}).items():
        if rid in known and str(note).strip():
            remarks[rid] = str(note).strip()[:1500]
    p.save("decisions", {"simplify": simplify, "complete": complete, "issues": issues, "remarks": remarks, "at": time.time()})
    p.set_stage("decide", status="done", finished=time.time())
    acc = sum(1 for v in simplify.values() if v == "accept")
    fix = sum(1 for v in issues.values() if v["choice"] != "keep") + sum(1 for v in complete.values() if v["choice"] != "keep")
    emit(p.id, "log", stage="decide", level="done",
         message=f"Your decisions: {acc} simplification(s) accepted, {fix} correction(s) to make, {len(remarks)} remark(s) with instructions.")


# ------------------------------------------------- applying what was decided

def _replace_once(text: str, quote: str, new: str) -> str | None:
    """Replace the one occurrence of `quote` (spacing aside). None if it is absent or occurs more than once."""
    if not quote.strip():
        return None
    pat = r"\s+".join(re.escape(tok) for tok in quote.split())
    found = list(re.finditer(pat, text))
    if len(found) != 1:
        return None                                # absent, or there are two places it could mean
    return text[:found[0].start()] + new + text[found[0].end():]


def _safe_edit(old: str, new: str) -> bool:
    """An edit to one proof that keeps its labels and citations and has a believable length."""
    if not new.strip() or check_balance(new):
        return False
    a, b = skeleton(old), skeleton(new)
    return a["labels"] == b["labels"] and set(a["cites"]) <= set(b["cites"]) and 0.4 * len(old) <= len(new) <= 2.2 * len(old) + 400


def apply_decisions(run: Run, blocks: list[dict], intro: str = "") -> tuple[list[dict], str, str, str, dict]:
    """Carry out the author's decisions on a copy of the draft.

    Returns the new blocks, the introduction, the text that the approved changes removed and the text they put
    in its place (so that the comparison with the notes does not report what the author agreed to), and a report
    of what was and was not done."""
    p = run.p
    dec = p.load("decisions") or {}
    items = decision_items(p)
    blocks = [dict(b) for b in blocks]
    waived: list[str] = []
    approved: list[str] = []
    report = {"simplified": [], "completed": [], "corrected": [], "instructed": [], "remarks": [], "unapplied": []}

    rewritten: set[str] = set()                # proofs replaced by an accepted simplification

    # 1. accepted simplifications
    for pr in items["simplify"]:
        if (dec.get("simplify") or {}).get(pr["id"]) != "accept":
            continue
        units = result_units(blocks)
        targets = {l: _unit(units, l) for l in pr["affects"]}
        if any(not u or u["proof_at"] is None for u in targets.values()):
            report["unapplied"].append({"id": pr["id"], "why": "a proof it rewrites is no longer where it was."})
            continue
        for l, u in targets.items():
            waived.append(blocks[u["proof_at"]]["text"])
            approved.append(pr["after"][l])
            blocks[u["proof_at"]]["text"] = pr["after"][l]
            rewritten.add(l)
        if pr.get("new"):
            anchor = _unit(result_units(blocks), pr["new"]["before"]) or min(targets.values(), key=lambda u: u["at"])
            page = blocks[anchor["at"]].get("page", 0)
            blocks[anchor["at"]:anchor["at"]] = [
                {"type": "env", "env": pr["new"]["kind"], "title": "", "label": pr["new"]["label"], "text": pr["new"]["statement"], "page": page},
                {"type": "proof", "env": "proof", "title": "", "label": "", "text": pr["new"]["proof"], "page": page}]
            approved += [pr["new"]["statement"], pr["new"]["proof"]]
        report["simplified"].append({"id": pr["id"], "names": list(pr["names"].values()), "labels": list(pr["affects"]),
                                     "idea": pr["idea"], "new": pr["new"]["label"] if pr.get("new") else ""})

    # 2. sketched proofs the author chose to complete: as offered, as the author edited it, or done again as told
    completed: set[str] = set()
    for c in items["complete"]:
        d = (dec.get("complete") or {}).get(c["id"]) or {}
        if d.get("choice") not in ("accept", "edit", "instruct"):
            continue
        if c["label"] in rewritten:
            report["unapplied"].append({"id": c["id"], "why": f"the proof of {c['name']} was replaced by a shorter one that you accepted; "
                                                               "the completion of the sketch was not applied on top of it."})
            continue
        units = result_units(blocks)
        u = _unit(units, c["label"])
        if not u or u["proof_at"] is None:
            report["unapplied"].append({"id": c["id"], "why": f"the proof of {c['name']} could not be found."})
            continue
        new = c["proof"] if d["choice"] == "accept" else d.get("text", "") if d["choice"] == "edit" else ""
        if d["choice"] == "instruct":
            prev = ((p.load("completion") or {}).get("items") or {}).get(c["label"]) or {}
            env = completion.environment(p, blocks, units)
            rec = completion.complete_one(run, {**u, "proof": c["sketch"]}, env, note=d.get("note", ""),
                                          previous={"steps": prev.get("steps"), "additions": prev.get("additions")})
            if rec["status"] != "ready":
                report["unapplied"].append({"id": c["id"], "why": f"the completion of {c['name']} done again as you asked did not pass "
                                                                   f"the checks ({rec['reason']}); the sketch is kept."})
                continue
            new = rec["proof"]
        if not new.strip() or check_balance(new):
            report["unapplied"].append({"id": c["id"], "why": f"the completed proof of {c['name']} could not be used; the sketch is kept."})
            continue
        waived.append(blocks[u["proof_at"]]["text"])
        approved.append(new)
        blocks[u["proof_at"]]["text"] = new
        completed.add(c["label"])
        report["completed"].append({"id": c["id"], "name": c["name"], "label": c["label"], "how": d["choice"], "note": d.get("note", "")})

    # 3. corrections to proofs: the suggested one, or the author's own instruction
    for c in items["issues"]:
        d = (dec.get("issues") or {}).get(c["id"]) or {}
        if d.get("choice") not in ("fix", "instruct"):
            continue
        if c["label"] in completed:
            report["unapplied"].append({"id": c["id"], "why": f"the sketch of {c['name']} was replaced by the completed proof you chose; "
                                                               "check in the paper view whether this point still applies."})
            continue
        u = _unit(result_units(blocks), c["label"])
        if not u or u["proof_at"] is None:
            report["unapplied"].append({"id": c["id"], "why": f"the proof of {c['name']} could not be found."})
            continue
        old = blocks[u["proof_at"]]["text"]
        new = _replace_once(old, c["quote"], c["correction"]) if d["choice"] == "fix" else None
        if new is None and d["choice"] == "fix" and c["label"] in rewritten:
            report["unapplied"].append({"id": c["id"], "why": f"the proof of {c['name']} was replaced by a shorter one that you "
                                                               "accepted, and the passage to correct is no longer in it. Check in "
                                                               "the paper view whether the correction is still needed."})
            continue
        if new is None:
            what = (f"THE CORRECTION\nReplace: {c['quote'] or '(the passage with the problem)'}\nBy: {c['correction']}"
                    if d["choice"] == "fix" else f"THE AUTHOR'S INSTRUCTION\n{d.get('note', '')}")
            user = (f"STATEMENT ({u['name']})\n{u['statement']}\n\nTHE PROBLEM FOUND\n{c['problem']}\n"
                    f"At: {c['quote'] or '(not quoted)'}\n\n{what}\n\nPROOF\n{old}")
            try:
                res = run.ask("writer", prompts.APPLY_FIX.format(), user, max_out=int(est_tokens(old) * 1.4) + 700, effort="high",
                              temperature=0.0, label=f"Apply correction to {c['name']}")
                cand = re.sub(r"\\end\{proof\}\s*$", "", re.sub(r"^\s*\\begin\{proof\}", "", clean_fragment(res.text))).strip()
                new = cand if _safe_edit(old, cand) and cand.split() != old.split() else None
            except LLMError as e:
                if passing(e):
                    raise
                new = None
        if new is None:
            report["unapplied"].append({"id": c["id"], "why": f"the change to the proof of {c['name']} could not be made safely; "
                                                               "make it yourself in the paper view."})
            continue
        waived.append(old)
        approved.append(new)
        blocks[u["proof_at"]]["text"] = new
        report["corrected" if d["choice"] == "fix" else "instructed"].append(
            {"id": c["id"], "name": c["name"], "label": c["label"], "problem": c["problem"], "note": d.get("note", "")})

    # 4. remarks the author gave an instruction for
    for r in items["remarks"]:
        note = (dec.get("remarks") or {}).get(r["id"])
        if not note:
            continue
        units = result_units(blocks)
        label = next(iter(_labels_in(r["where"], {u["label"] for u in units})), "")
        target: int | str | None = None
        if label:
            u = _unit(units, label)
            target = u["proof_at"] if u["proof_at"] is not None and "proof" in (r["comment"] + r["action"] + note).lower() else u["at"]
        elif r["where"] and r["where"].lower() != "whole paper":
            q = _norm_space(r["where"]).strip("\"'“”")
            target = next((i for i, b in enumerate(blocks) if b.get("text") and q and q in _norm_space(b["text"])), None)
            if target is None and intro and (q in _norm_space(intro) or "introduction" in q.lower()):
                target = "intro"
        if target is None:
            report["unapplied"].append({"id": r["id"], "why": "the passage it concerns could not be located; apply it in the paper view."})
            continue
        old = intro if target == "intro" else blocks[target]["text"]
        user = (f"AUTHOR'S INSTRUCTION\n{note}\n\nTHE REMARK IT ANSWERS\n{r['comment']} {r['action']}\n\n"
                f"CONFIRMED NOTATION\n{notation_text(p.load('confirmed', {}), 1000)}\n\nPASSAGE\n{old}")
        try:
            res = run.ask("writer", prompts.REVISE.format(), user, max_out=int(est_tokens(old) * 1.5) + 600, effort="high",
                          temperature=0.1, label=f"Apply your instruction for {r['id']}")
            cand = clean_fragment(res.text)
        except LLMError as e:
            if passing(e):
                raise
            cand = ""
        if not _safe_edit(old, cand):
            report["unapplied"].append({"id": r["id"], "why": "the instruction could not be carried out safely; apply it in the paper view."})
            continue
        waived.append(old)
        approved.append(cand)
        if target == "intro":
            intro = cand
        else:
            blocks[target]["text"] = cand
        report["remarks"].append({"id": r["id"], "note": note, "where": "the introduction" if target == "intro" else label})
    for i, b in enumerate(blocks):
        b["id"] = f"b{i + 1}"
    return blocks, intro, "\n\n".join(waived), "\n\n".join(approved), report


def state_json(p: Project) -> dict:
    """What the interface needs from the three readings."""
    return {"simplify": p.load("simplify"), "verify": p.load("verify"), "referee": p.load("referee"), "completion": p.load("completion"),
            "decisions": p.load("decisions"), "items": decision_items(p)}
