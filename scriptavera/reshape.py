# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The plan of the revision: what turns the first draft (every statement, every proof, as the author approved them)
into the form a journal expects. It comes after the first draft, never before it: the first draft is the author's own
paper, and everything here is a proposal measured against it.

A first draft follows the notes: one statement for each claim of the notes, one proof for each, in the order they
were found. A paper is shaped differently. Results that are parts of one statement are stated as one theorem with
parts. An argument run several times is stated once, in general form, and each proof applies it. A proof that repeats
another is not written out again. The sections follow the argument, not the notes.

Structure and proofs are decided together, at two levels: first in outline, then in full.

  1. Skeletons    every proof is reduced to its skeleton (method, steps in general terms, what is particular to it),
                  one request for each, kept for as long as the proof does not change. The skeletons of a whole paper
                  fit in one request, whatever its length: nothing of it is cut.
  2. The plan     one reading of all statements and skeletons proposes changes: merge, method, similar, shorten,
                  sections, kind, and (rarely) an argument of the model's own. Each is planned: every new or changed
                  STATEMENT written in full, every changed PROOF in outline. The program checks the order (every
                  statement after what it uses) and an independent reader checks the plan against the full proofs.
                  Only what passes is put to the author.
  3. The author   sees the plan beside the first draft and accepts or rejects each change (stop "Your plan").
  4. New proofs   the proofs of the accepted changes are written in full, one after the other on the paper as the
                  changes before them left it, so that they fit together; each is checked by the program, by an
                  independent reader (does the paper prove exactly what it proved?) and line by line. A change whose
                  proof fails is dropped with the reason: the first draft's version stands.
  5. The author   sees each new proof beside the one it replaces and accepts it or keeps the old one.

Nothing here changes the statements, the structure or the proofs the author confirmed: those stay as they are (and are
what every earlier screen shows). The accepted changes are applied on top of them each time the draft is written
(model()), so a change made earlier in the paper brings the plan up to date like everything else: a change whose
results did not change is kept with the author's decisions; only what the change touches is looked at again.
"""

from __future__ import annotations

import copy
import re
import threading
import time

from . import prompts
from .llm import LLMError
from .pipeline import Cancelled, Run, _room, _side_by_side, emit
from .store import Project, reuse_ok
from .texutil import check_balance, clean_fragment
from .util import est_tokens, extract_tag, fit
from .writing import fields

KINDS = ("merge", "method", "similar", "shorten", "sections", "kind", "argument")
PROOF_KINDS = ("merge", "method", "similar", "shorten", "argument")     # a proof changes: written once the plan is approved
LIMIT = 8
ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")
_REF = re.compile(r"\\(?:eq|c|C)?ref\{(?:res|def):([A-Za-z0-9]+)\}")
_lock = threading.Lock()

# what each kind is called for the author
KIND_TEXT = {"merge": "One statement instead of several", "method": "An argument stated once", "similar": "A repeated proof not written again",
             "shorten": "A proof made shorter",
             "sections": "The order and the sections", "kind": "A result called by its right name",
             "argument": "Another argument, proposed by the app"}


# ------------------------------------------------------------------ the paper as the author confirmed it

def base(p: Project) -> tuple[list[dict], list[dict], dict]:
    """Statements in order, sections and proofs, as the author confirmed them (copies: nothing here writes to them)."""
    from .drafting import ordered
    seq, secs, _ = ordered(p)
    ids = {x["id"] for x in seq}
    proofs = {k: dict(v) for k, v in ((p.load("proofs") or {}).get("items") or {}).items() if k in ids}
    return [dict(x) for x in seq], [{**s, "items": [i for i in s["items"] if i in ids]} for s in secs], proofs


def eligible(x: dict, proofs: dict) -> bool:
    """A result of the author's with a written proof: what a change of shape may touch."""
    from .drafting import PROVED, needs_text
    return x["kind"] in PROVED and needs_text(x) and bool((proofs.get(x["id"]) or {}).get("text"))


def unit_key(x: dict, proofs: dict) -> str:
    from .drafting import _sig
    return _sig(x["kind"], x["statement"], x.get("treatment"), (proofs.get(x["id"]) or {}).get("text", ""))


def model_sig(seq: list[dict], secs: list[dict], proofs: dict) -> str:
    from .drafting import _sig
    return _sig([(x["id"], x["kind"], x["statement"], x.get("treatment"), x.get("name")) for x in seq],
                [(s["title"], s["items"]) for s in secs], {i: (proofs.get(i) or {}).get("text", "") for i in sorted(proofs)})


def uses(x: dict, proofs: dict, by: dict) -> set[str]:
    """The statements x rests on and that must stand before it: those the notes say it uses and those its statement
    or proof refers to (an example, a remark or a question it mentions need not stand before it)."""
    text = (x.get("statement") or "") + "\n" + ((proofs.get(x["id"]) or {}).get("text") or "")
    out = (set(x.get("depends") or []) | set(_REF.findall(text))) - {x["id"]}
    return {d for d in out if d in by and by[d]["kind"] not in ("example", "remark", "question")}


def order_problems(seq: list[dict], proofs: dict) -> set[str]:
    by = {x["id"]: x for x in seq}
    pos = {x["id"]: k for k, x in enumerate(seq)}
    return {f"{x['id']} uses {d}, which comes after it" for x in seq for d in uses(x, proofs, by) if pos[d] > pos[x["id"]]}


def _name(x: dict, nums: dict) -> str:
    from .drafting import kind_name
    return f"{kind_name(x['kind'])} {nums.get(x['id'], '')}".strip() + (f" ({x['name']})" if x.get("name") else "")


# ------------------------------------------------------------------ applying one proposal to a copy of the paper

def _resequence(m: dict, by: dict) -> None:
    m["secs"] = [s for s in m["secs"] if s["items"]]
    m["seq"] = [by[i] for s in m["secs"] for i in s["items"] if i in by]


def _reref(text: str, old: str, into: str, word: str, part: str) -> str:
    """A reference to a result that became part (part) of the statement `into`, pointed at that part."""
    pat = re.compile(r"(?:(Lemma|Proposition|Theorem|Corollary|Claim)(~|\s+))?\\ref\{res:" + re.escape(old) + r"\}(?!\\,\\textup)")
    return pat.sub(lambda g: (f"{word}~" if g.group(1) else "") + f"\\ref{{res:{into}}}\\,\\textup{{({part})}}", text or "")


def _apply_merge(m: dict, pr: dict) -> str:
    from .drafting import kind_name
    new, by = pr["new"], {x["id"]: x for x in m["seq"]}
    ids = [i for i in pr["affects"] if i in by]
    if len(ids) != len(pr["affects"]) or len(ids) < 2:
        return "a result it merges is no longer in the paper"
    pos = {x["id"]: k for k, x in enumerate(m["seq"])}
    members = sorted(ids, key=pos.get)
    group, into = set(members), new["into"]
    deps = {d for i in members for d in uses(by[i], m["proofs"], by) if d not in group}
    users = [x["id"] for x in m["seq"] if x["id"] not in group and uses(x, m["proofs"], by) & group]
    deps_max, users_min = max((pos[d] for d in deps), default=-1), min((pos[u] for u in users), default=len(m["seq"]))
    if deps_max >= users_min:
        return "a result that stands between them is used by one part and uses another, so they cannot be one statement"
    host = max((i for i in members if pos[i] < users_min), key=pos.get)
    merged = {**by[into], "kind": new["kind"], "name": new.get("name") or "", "statement": new["statement"],
              "depends": sorted(deps, key=pos.get), "pages": sorted({n for i in members for n in by[i].get("pages") or []}),
              "role": "main" if any(by[i].get("role") == "main" for i in members) else by[into].get("role", ""),
              "merged": dict(new["parts"]), "reshaped": pr["id"]}
    last_dep = next((x["id"] for x in m["seq"] if pos[x["id"]] == deps_max), None)
    for s in m["secs"]:
        out = []
        for i in s["items"]:
            if i == host and pos[host] > deps_max:
                out.append(into)
            elif i in group:
                continue
            else:
                out.append(i)
                if pos[host] <= deps_max and i == last_dep:
                    out.append(into)
        s["items"] = out
    m["waived"] += [(m["proofs"].get(i) or {}).get("text", "") for i in members] + [by[i]["statement"] for i in members]
    m["proofs"][into] = {**(m["proofs"].get(into) or {}), "text": new["proof"], "status": "accept", "reshaped": pr["id"]}
    for i in members:
        if i != into:
            m["proofs"].pop(i, None)
            by.pop(i, None)
            m["remap"][i] = into
    by[into] = merged
    word = kind_name(new["kind"])
    for x in by.values():                      # every reference to a merged result now points at its part
        for i in members:
            x["statement"] = _reref(x["statement"], i, into, word, new["parts"][i]) if x["id"] != into else x["statement"]
            if x["id"] in m["proofs"] and m["proofs"][x["id"]].get("text"):
                m["proofs"][x["id"]]["text"] = _reref(m["proofs"][x["id"]]["text"], i, into, word, new["parts"][i])
        if x["id"] != into and x.get("depends"):
            x["depends"] = list(dict.fromkeys(into if d in group else d for d in x["depends"]))
    m["approved"] += [new["statement"], new["proof"]]
    m["made"][pr["id"]] = {into} | set(members)
    _resequence(m, by)
    return ""


def _apply_method(m: dict, pr: dict) -> str:
    new, by = pr["new"], {x["id"]: x for x in m["seq"]}
    ids = [i for i in pr["affects"] if i in by and (m["proofs"].get(i) or {}).get("text")]
    if len(ids) != len(pr["affects"]):
        return "a proof it rewrites is no longer in the paper"
    pos = {x["id"]: k for k, x in enumerate(m["seq"])}
    first = min(ids, key=pos.get)
    nid = new["id"]
    item = {"id": nid, "kind": new["kind"], "name": new.get("name") or "", "statement": new["statement"], "original": new["statement"],
            "depends": [], "pages": sorted({n for i in ids for n in by[i].get("pages") or []}), "placement": "main",
            "treatment": "keep", "role": "auxiliary", "settled": True, "question": "", "added": pr["id"], "reshaped": pr["id"]}
    m["proofs"][nid] = {"text": new["text"], "status": "accept", "added": [], "uses": [], "cites": [], "reshaped": pr["id"]}
    by[nid] = item
    item["depends"] = sorted(uses(item, m["proofs"], by), key=lambda d: pos.get(d, 0))
    if any(pos.get(d, -1) >= pos[first] for d in item["depends"]):
        return "the general statement uses a result that is presented after the first proof that applies it"
    for s in m["secs"]:
        if first in s["items"]:
            s["items"].insert(s["items"].index(first), nid)
    for i in ids:
        m["waived"].append(m["proofs"][i]["text"])
        m["proofs"][i] = {**m["proofs"][i], "text": new["proofs"][i], "reshaped": pr["id"]}
        by[i]["depends"] = list(dict.fromkeys((by[i].get("depends") or []) + [nid]))
        m["approved"].append(new["proofs"][i])
    m["approved"] += [new["statement"], new["text"]]
    m["made"][pr["id"]] = {nid}
    _resequence(m, by)
    return ""


def _apply_proofs(m: dict, pr: dict) -> str:
    """similar, shorten, argument: the proofs named are replaced."""
    by = {x["id"]: x for x in m["seq"]}
    for i, text in pr["new"]["proofs"].items():
        if i not in by or not (m["proofs"].get(i) or {}).get("text"):
            return "a proof it replaces is no longer in the paper"
        m["waived"].append(m["proofs"][i]["text"])
        m["proofs"][i] = {**m["proofs"][i], "text": text, "reshaped": pr["id"]}
        m["approved"].append(text)
        if pr["kind"] == "argument":
            m["ai"].append(i)
    return ""


def _apply_kind(m: dict, pr: dict) -> str:
    by = {x["id"]: x for x in m["seq"]}
    for i, kind in pr["new"]["kinds"].items():
        if i not in by:
            return "the result is no longer in the paper"
        by[i]["kind"] = kind
    return ""


def _apply_sections(m: dict, pr: dict) -> str:
    """The proposed sections, brought in line with what the other accepted proposals did: a result merged into
    another is taken out, a statement added since is put right before the first statement that uses it."""
    by = {x["id"]: x for x in m["seq"]}
    old_purpose = {s["title"]: s.get("purpose", "") for s in m["secs"]}
    seen: set[str] = set()
    secs = []
    for s in pr["new"]["sections"]:
        items = []
        for i in s["items"]:
            i = m["remap"].get(i, i)
            if i in by and i not in seen:
                seen.add(i)
                items.append(i)
        secs.append({"title": s["title"], "purpose": old_purpose.get(s["title"], ""), "items": items})
    for x in m["seq"]:                           # in the paper but not in the plan (added by another proposal)
        if x["id"] in seen:
            continue
        users = [y["id"] for y in m["seq"] if x["id"] in uses(y, m["proofs"], by)]
        placed = False
        for s in secs:
            hit = next((k for k, i in enumerate(s["items"]) if i in users), None)
            if hit is not None:
                s["items"].insert(hit, x["id"])
                placed = True
                break
        if not placed:
            return f"the proposed sections leave out {x['id']}"
        seen.add(x["id"])
    m["secs"] = secs
    _resequence(m, by)
    return ""


APPLY = {"merge": _apply_merge, "method": _apply_method, "similar": _apply_proofs, "shorten": _apply_proofs, "argument": _apply_proofs,
         "kind": _apply_kind, "sections": _apply_sections}
_ORDER = {"merge": 0, "method": 1, "similar": 2, "shorten": 2, "argument": 2, "kind": 3, "sections": 4}


def _empty(seq: list[dict], secs: list[dict], proofs: dict) -> dict:
    return {"seq": seq, "secs": secs, "proofs": proofs, "remap": {}, "waived": [], "approved": [], "ai": [], "applied": [],
            "unapplied": [], "made": {}}


def apply(m: dict, proposals: list[dict]) -> dict:
    """The proposals applied one after the other to a copy of the paper. One that cannot be applied (its results are
    gone, or it would put a statement before something it uses) is left out, with the reason; the others stand."""
    wrong = order_problems(m["seq"], m["proofs"])
    for pr in sorted(proposals, key=lambda x: _ORDER.get(x["kind"], 9)):
        trial = copy.deepcopy(m)
        err = APPLY[pr["kind"]](trial, pr)
        if not err:
            new = order_problems(trial["seq"], trial["proofs"]) - wrong
            err = ("it would put a statement before one it uses (" + "; ".join(sorted(new)[:2]) + ")") if new else ""
        if err:
            m["unapplied"].append({"id": pr["id"], "why": err})
            continue
        m = trial
        m["applied"].append(pr["id"])
    return m


def _current(pr: dict, keys: dict) -> bool:
    return all(keys.get(i) == k for i, k in (pr.get("inputs") or {}).items())


def usable(st: dict, keys: dict) -> list[dict]:
    """The changes that are part of the paper, in the order they are applied: accepted in the plan and, where a proof
    was written for them, written without a failure and accepted again by the author. A change written on top of
    another stands only if that one does."""
    dec, dec2 = st.get("decisions") or {}, st.get("decisions2") or {}
    out, ok = [], set()
    for pr in sorted(st.get("proposals", []), key=lambda x: _ORDER.get(x["kind"], 9)):
        if dec.get(pr["id"]) != "accept" or not _current(pr, keys) or not pr.get("new"):
            continue
        if pr["kind"] in PROOF_KINDS and (pr.get("fail") or dec2.get(pr["id"]) != "accept" or any(q not in ok for q in pr.get("on") or [])):
            continue
        ok.add(pr["id"])
        out.append(pr)
    return out


def model(p: Project) -> tuple[dict, dict]:
    """The paper the draft is written from: the statements, sections and proofs the author confirmed, with the
    changes the author accepted (in the plan, and then as written) applied. Returns it (seq, secs, proofs, remap of
    merged results, the text replaced and the text put in, the proofs whose argument the app proposed) and the number
    of each statement. Before the revision is planned this is the paper as the author confirmed it: the first draft."""
    from .drafting import numbers
    seq, secs, proofs = base(p)
    st = p.load("reshape") or {}
    keys = {x["id"]: unit_key(x, proofs) for x in seq}
    m = apply(_empty(seq, secs, proofs), usable(st, keys))
    return m, numbers(m["secs"], m["seq"])


def applied(p: Project) -> bool:
    seq, _, proofs = base(p)
    return bool(usable(p.load("reshape") or {}, {x["id"]: unit_key(x, proofs) for x in seq}))


# ------------------------------------------------------------------ parsing what the writer returns

def _proofs_in(text: str, tag: str = "proof") -> dict[str, str]:
    out = {}
    for i, body in re.findall(r'<%s\s+id\s*=\s*"?\s*(?:res:)?([A-Za-z0-9]+)\s*"?\s*>(.*?)</%s>' % (tag, tag), text or "", flags=re.S):
        body = re.sub(r"\\begin\{proof\}(\[[^\]]*\])?|\\end\{proof\}", "", clean_fragment(body)).strip()
        if body:
            out[i] = body
    return out


def _parse(kind: str, text: str, pr: dict, seq: list[dict], nid: str, tag: str = "proof") -> tuple[dict | None, str]:
    """What the writer returned for one proposal, as the record that is applied; or None and what is missing. With
    tag "outline" it is the plan of the proposal: the same record, with the outline of each proof where its text goes."""
    from .drafting import PROVED
    by = {x["id"]: x for x in seq}
    proofs = _proofs_in(text, tag)
    if kind == "merge":
        f = next(iter(fields(text, "merged", ("kind", "name", "statement", "parts"))), {})
        st = clean_fragment(f.get("statement", "")).strip()
        parts = {}
        for i, r in re.findall(r"\b([A-Za-z]+\d+[a-z]?)\s*=\s*\(?([ivx]+)\)?", f.get("parts", "")):
            if i in pr["affects"]:
                parts[i] = r
        if not st or set(parts) != set(pr["affects"]) or len(set(parts.values())) != len(parts):
            return None, "the merged statement came back incomplete"
        into = next((i for i in proofs if i in pr["affects"]), None)
        if not into and tag == "outline" and proofs:          # the outline of the one proof, whatever id it was given
            into = pr["affects"][0]
            proofs[into] = next(iter(proofs.values()))
        if not into:
            return None, "the proof of the merged statement did not come back"
        mk = next((k for k in ("theorem", "proposition", "lemma") if k in f.get("kind", "").lower()), by[into]["kind"])
        name = "" if f.get("name", "").strip().lower().strip(".") in ("", "none") else f["name"].strip()
        return {"into": into, "kind": mk, "name": name, "statement": st, "parts": parts, "proof": proofs[into]}, ""
    if kind == "method":
        f = next(iter(fields(text, "new", ("kind", "name", "statement"))), {})
        st = clean_fragment(f.get("statement", "")).strip()
        body = proofs.pop("NEW", "")
        mk = next((k for k in ("lemma", "proposition", "method", "construction") if k in f.get("kind", "").lower()), "lemma")
        if not st or not body or any(i not in proofs for i in pr["affects"]):
            return None, "the general statement or one of the proofs that apply it did not come back"
        lab = f"res:{nid}"
        name = "" if f.get("name", "").strip().lower().strip(".") in ("", "none") else f["name"].strip()
        return {"id": nid, "kind": mk, "name": name, "statement": st.replace("res:NEW", lab), "text": body.replace("res:NEW", lab),
                "proofs": {i: proofs[i].replace("res:NEW", lab) for i in pr["affects"]}}, ""
    if kind in ("similar", "shorten", "argument"):
        got = {i: t for i, t in proofs.items() if i in pr["affects"]}
        if not got or (kind != "similar" and set(got) != set(pr["affects"])):
            return None, "the new proof did not come back"
        return {"proofs": got}, ""
    if kind == "kind":
        kinds = {i: k.strip().lower() for i, k in re.findall(r'<kind\s+id\s*=\s*"?\s*(?:res:)?([A-Za-z0-9]+)\s*"?\s*>(.*?)</kind>', text, flags=re.S)}
        kinds = {i: k for i, k in kinds.items() if i in by and by[i]["kind"] in PROVED and k in PROVED and k != by[i]["kind"]}
        return ({"kinds": kinds}, "") if kinds else (None, "no result would be called differently")
    secs = []
    for f in fields(text, "section", ("title", "items")):
        ids = [i for i in re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", f.get("items", "")) if i in by]
        title = re.sub(r"^\s*(?:section\s*)?\d+[.:)]?\s*", "", f.get("title", ""), flags=re.I).strip().rstrip(".")
        if ids and title:
            secs.append({"title": title, "items": ids})
    flat = [i for s in secs for i in s["items"]]
    if not secs or sorted(flat) != sorted(by):
        return None, "the proposed sections do not contain every statement exactly once"
    if flat == [x["id"] for x in seq] and [s["title"] for s in secs] == pr.get("titles_now"):
        return None, "the proposed sections are the ones the paper has"
    return {"sections": secs, "ids": sorted(by)}, ""


# ------------------------------------------------------------------ the step

def _texts(pr: dict) -> list[str]:
    new = pr["new"]
    out = list((new.get("proofs") or {}).values())
    out += [new[k] for k in ("statement", "proof", "text") if new.get(k)]
    return out


def _listing(seq: list[dict], nums: dict, proofs: dict, only: list[str] | None = None, with_proofs: bool = True) -> str:
    from .drafting import _statement_line
    out = []
    for x in seq:
        if only is not None and x["id"] not in only:
            continue
        t = f"[{x['id']}] " + _statement_line(x, nums.get(x["id"], ""))
        if with_proofs and (proofs.get(x["id"]) or {}).get("text"):
            t += "\nProof:\n" + proofs[x["id"]]["text"]
        out.append(t)
    return "\n\n".join(out)


def _plan_text(pr: dict) -> str:
    """The plan of one change in words: what the checker of the plan and the writer of its proofs are given."""
    pl, out = pr["plan"], []
    if pr["kind"] == "merge":
        out.append(f"ONE STATEMENT ({pl['kind']}" + (f", {pl['name']}" if pl.get("name") else "") + f") in place of {', '.join(pr['affects'])}; parts: "
                   + ", ".join(f"{i} = ({r})" for i, r in pl["parts"].items()) + f"\n{pl['statement']}\nOUTLINE OF ITS PROOF\n{pl['proof']}")
    elif pr["kind"] == "method":
        out.append(f"NEW GENERAL STATEMENT ({pl['kind']}" + (f", {pl['name']}" if pl.get("name") else "") + f"), label res:{pl['id']}\n"
                   f"{pl['statement']}\nOUTLINE OF ITS PROOF\n{pl['text']}")
    out += [f"OUTLINE OF THE NEW PROOF OF {i}\n{t}" for i, t in (pl.get("proofs") or {}).items()]
    return "\n\n".join(out)


def stage_reshape(run: Run) -> None:
    """The plan of the revision: every proof reduced to its skeleton, the whole paper looked at at once, and each
    change planned (statements in full, proofs in outline) and checked before the author sees it."""
    from .drafting import PROVED, credited, kind_name, numbers
    p = run.p
    seq, secs, proofs = base(p)
    sig = model_sig(seq, secs, proofs)
    old = p.load("reshape") or {}
    if old.get("sig") == sig and old.get("done"):
        run.log("The statements, the structure and the proofs are as they were when the revision was planned: the plan stands.")
        return
    keys = {x["id"]: unit_key(x, proofs) for x in seq}
    nums = numbers(secs, seq)
    by = {x["id"]: x for x in seq}
    kept: list[dict] = []
    if reuse_ok(p.state) and old.get("proposals") is not None:
        # brought up to date: a change about results that did not change is kept, with the author's decisions
        kept = [pr for pr in old["proposals"] if _current(pr, keys) and pr.get("plan")
                and (pr["kind"] != "sections" or pr["plan"].get("ids") == sorted(by))]
        if kept:
            run.log(f"{len(kept)} planned change(s) about results that did not change {'are' if len(kept) != 1 else 'is'} kept, with your decision.")
    ids_kept = {pr["id"] for pr in kept}
    state = {"sig": sig, "proposals": kept, "dropped": [], "done": False,
             "decisions": {k: v for k, v in (old.get("decisions") or {}).items() if k in ids_kept},
             "decisions2": {k: v for k, v in (old.get("decisions2") or {}).items() if k in ids_kept},
             "base": max([int(re.sub(r"\D", "", x["id"]) or 0) for x in (old.get("proposals") or []) + (old.get("dropped") or [])] or [0])}
    cand = [x for x in seq if eligible(x, proofs)]
    if len(cand) < 2:
        state["done"] = True
        p.save("reshape", state)
        run.log("The paper has fewer than two proved results: there is nothing to state as one or to state once.")
        return

    # 1. the skeleton of every proof
    cache = p.load("reshape_cache") or {}
    todo = [x for x in cand if keys[x["id"]] not in cache]
    done = [len(cand) - len(todo)]
    run.progress(done[0], len(cand) + 1, "reading each proof for its method")
    sys_o = prompts.RESHAPE_OUTLINE.format(field=run.field)

    def outline(x: dict) -> None:
        user = (f"{kind_name(x['kind'])} {nums.get(x['id'], '')} [label res:{x['id']}]\nStatement: {x['statement']}\n\n"
                f"Proof:\n{proofs[x['id']]['text']}")
        res = run.ask("reason", sys_o, fit(user, _room("reason", sys_o, 700), "middle"), max_out=700,
                      label=f"Outline the argument of {kind_name(x['kind'])} {nums.get(x['id'], '')} ({x['id']})")
        with _lock:
            cur = p.load("reshape_cache") or {}
            cur[keys[x["id"]]] = re.sub(r"\n{3,}", "\n\n", (res.text or "").strip())[:2500]
            p.save("reshape_cache", dict(list(cur.items())[-400:]))
            done[0] += 1
            run.progress(done[0], len(cand) + 1, f"method of {kind_name(x['kind'])} {nums.get(x['id'], '')} read")
    _side_by_side(run, "reason", False, todo, outline)
    cache = p.load("reshape_cache") or {}

    # 2. the whole paper in one view: statements and skeletons
    d = p.load("dossier") or {}
    sys_f = prompts.RESHAPE_FIND.format(field=run.field, limit=LIMIT)
    room = _room("reason", sys_f, 2600)
    share = max(500, int(room * 0.55) // max(1, len(cand)))
    lines = []
    for s_i, s in enumerate(secs, 2):
        lines.append(f"SECTION {s_i}: {s['title']}")
        for i in s["items"]:
            x = by[i]
            tag = ("credited to the literature" if credited(x) else "conjecture" if x.get("treatment") == "conjecture" or x["kind"] == "conjecture"
                   else "main result" if x.get("role") == "main" and x["kind"] in PROVED else "")
            u = sorted(uses(x, proofs, by))
            lines.append(f"- {i} [{kind_name(x['kind'])} {nums.get(i, '')}" + (f", {tag}" if tag else "")
                         + (f", uses {', '.join(u)}" if u else "") + f"]: {fit(x['statement'], 900, 'head')}")
            if keys[i] in cache and eligible(x, proofs):
                lines.append("  SKELETON OF ITS PROOF\n  " + fit(cache[keys[i]], share, "head").replace("\n", "\n  "))
    already = "\n".join(f"- {pr['kind']} of {', '.join(pr['affects']) or 'the sections'}: {fit(pr['idea'], 300, 'head')}" for pr in kept)
    user = (f"WHAT THE PAPER IS ABOUT\n{fit(d.get('problem', ''), 1200, 'head')}\n{fit(d.get('contribution', ''), 1200, 'head')}\n\n"
            + (f"ALREADY PROPOSED\n{already}\n\n" if already else "") + "THE PAPER\n" + "\n".join(lines))
    run.progress(len(cand), len(cand) + 1, "looking at the whole paper")
    res = run.ask("reason", sys_f, fit(user, room, "middle"), max_out=2600, effort="high", label="Plan the revision")
    found = []
    taken = {i for pr in kept if pr["kind"] in PROOF_KINDS for i in pr["affects"]}
    for f in fields(res.text, "proposal", ("kind", "affects", "idea", "why")):
        kind = next((k for k in KINDS if k in f.get("kind", "").lower().split()), "")
        ids = [i for i in dict.fromkeys(re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", f.get("affects", ""))) if i in by]
        if not kind or not f.get("idea"):
            continue
        if kind == "sections":
            if any(pr["kind"] == "sections" for pr in kept + found):
                continue
            ids = []
        elif kind == "kind":
            ids = [i for i in ids if by[i]["kind"] in PROVED and not credited(by[i])]
        else:
            ids = [i for i in ids if eligible(by[i], proofs)]
            if (kind in ("merge", "method") and len(ids) < 2) or not ids:
                continue
        if kind != "sections" and not ids:
            continue
        found.append({"kind": kind, "affects": ids, "idea": re.sub(r"\s+", " ", f["idea"]).strip(),
                      "why": re.sub(r"\s+", " ", f.get("why", "")).strip()})
    found = found[:LIMIT]

    # 3. each one planned: its statements in full, its proofs in outline; checked by the program, then by an
    #    independent reader against the full proofs
    sys_p, sys_c = prompts.RESHAPE_PLAN.format(field=run.field), prompts.RESHAPE_PLAN_CHECK.format(field=run.field)
    all_statements = _listing(seq, nums, proofs, with_proofs=False)
    for k, f in enumerate(found):
        run.check()
        pid = f"H{state['base'] + k + 1}"
        run.progress(k, len(found), f"planning change {k + 1} of {len(found)}: {KIND_TEXT[f['kind']].lower()}")

        def drop(reason: str) -> None:
            state["dropped"].append({"id": pid, "kind": f["kind"], "affects": f["affects"], "idea": f["idea"], "reason": reason})
            p.save("reshape", state)
            run.log(f"A possible change ({KIND_TEXT[f['kind']].lower()}"
                    + (f": {', '.join(_name(by[i], nums) for i in f['affects'])}" if f["affects"] else "") + f") was set aside: {reason}")

        if f["kind"] in PROOF_KINDS and taken & set(f["affects"]):
            drop("it overlaps with an earlier change for the same result.")
            continue
        try:
            pr = _plan(run, f, pid, f"X{state['base'] + k + 1}", seq, secs, proofs, nums, keys, by, all_statements, sys_p, sys_c, drop)
        except (LLMError, Cancelled):
            raise                                  # a limit or a stop: the step waits or stops, as every step does
        except Exception as e:  # noqa: BLE001 - one change that cannot be planned never holds up the paper
            drop(f"it could not be planned ({type(e).__name__}: {str(e)[:120]}).")
            continue
        if pr is None:
            continue
        state["proposals"].append(pr)
        if f["kind"] in PROOF_KINDS:
            taken |= set(f["affects"])
        p.save("reshape", state)
    state["done"] = True
    p.save("reshape", state)
    new_n = len(state["proposals"]) - len(kept)
    run.progress(1, 1, "the revision is planned")
    run.log(f"Plan of the revision: {len(found)} possible change(s) found; {new_n} passed every check and "
            + ("will be put to you." if new_n else "none will be put to you.")
            + (" The first draft keeps its shape." if not state["proposals"] else ""))


def _plan(run: Run, f: dict, pid: str, nid: str, seq: list[dict], secs: list[dict], proofs: dict, nums: dict, keys: dict, by: dict,
          all_statements: str, sys_p: str, sys_c: str, drop) -> dict | None:
    """One change planned and checked: its statements in full, its proofs in outline. Returns it ready for the author,
    or None when it was set aside (drop was called with the reason)."""
    mine = [by[i] for i in f["affects"]]
    named = [i for i in dict.fromkeys(re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", f["idea"])) if i in by and i not in f["affects"]]
    user = (f"THE CHANGE\nkind: {f['kind']}\nwhat to do: {f['idea']}\n\nTHE STATEMENTS OF THE PAPER, IN ORDER\n"
            + fit(all_statements, max(3000, _room('reason', sys_p, 6000) // 3), "middle")
            + ("\n\nTHE RESULTS THE CHANGE IS ABOUT, AS THEY STAND\n" + _listing(seq, nums, proofs, f["affects"]) if mine else "")
            + ("\n\nOTHER RESULTS IT REFERS TO (for reference)\n" + _listing(seq, nums, proofs, named) if named else "")
            + ("\n\nTHE SECTIONS AS THEY ARE\n" + "\n".join(f"{n}. {s['title']}: {', '.join(s['items'])}" for n, s in enumerate(secs, 2))
               + "\nEach statement uses: " + "; ".join(f"{x['id']} uses {', '.join(sorted(uses(x, proofs, by)))}" for x in seq if uses(x, proofs, by))
               if f["kind"] == "sections" else ""))
    res = run.ask("reason", sys_p, fit(user, _room("reason", sys_p, 2600), "middle"), max_out=2600, effort="high",
                  temperature=0.1, label=f"Plan {pid}: {f['kind']}")
    plan, err = _parse(f["kind"], res.text or "", {**f, "titles_now": [s["title"] for s in secs]}, seq, nid, tag="outline")
    if not plan:
        drop(err + ".")
        return None
    pr = {"id": pid, "kind": f["kind"], "affects": f["affects"], "idea": f["idea"], "why": f["why"], "plan": plan,
          "inputs": {i: keys[i] for i in f["affects"]}, "planned_by": res.model, "at": time.time()}
    if any(check_balance(t) for t in _texts({"new": plan})):
        drop("the text written for it had unbalanced LaTeX.")
        return None
    trial = apply(_empty(*copy.deepcopy((seq, secs, proofs))), [{**pr, "new": plan}])
    if trial["unapplied"]:
        drop(trial["unapplied"][0]["why"] + ".")
        return None
    if f["kind"] not in PROOF_KINDS:
        pr["new"] = plan                           # no proof changes: the plan is the change, and the program checked its order
    else:
        before = _listing(seq, nums, proofs, f["affects"] + named)
        check_user = (f"THE CHANGE\nkind: {f['kind']}\n{f['idea']}\n\nTHE STATEMENTS OF THE PAPER, IN ORDER\n"
                      + fit(all_statements, 6000, "middle") + f"\n\nAS IT STANDS\n{before}\n\nTHE PLAN\n{_plan_text(pr)}")
        chk = run.ask("reason", sys_c, fit(check_user, _room("reason", sys_c, 700), "middle"), max_out=700, effort="high",
                      other_than=res.model, label=f"Check the plan {pid}")
        verdict = extract_tag(chk.text, "verdict").strip().lower()
        reason = re.sub(r"\s+", " ", extract_tag(chk.text, "reason")).strip()
        if not verdict.startswith("sound"):
            drop("an independent reading of the plan against your proofs did not confirm it" + (f" ({reason})" if reason else "."))
            return None
        pr.update(check=reason, checked_by=chk.model)
    pr["names"] = {i: _name(by[i], nums) for i in f["affects"]}
    return pr


# ------------------------------------------------------------------ the new proofs, written for what the author accepted

def stage_reproofs(run: Run) -> None:
    """The proofs of the changes the author accepted in the plan, in full. They are written one change after the
    other, each on the paper as the changes before it left it, so that they fit together. Each is checked by the
    program, then by an independent reader (does the paper prove exactly what it proved?); mathematics that is not the
    author's (a general statement, another argument) is also read line by line. A change that fails is dropped, with
    the reason: the first draft's version stands. Every proof of the second draft is read line by line in the proof
    check that follows."""
    from .drafting import _sig
    p = run.p
    st = p.load("reshape") or {}
    seq, secs, proofs = base(p)
    keys = {x["id"]: unit_key(x, proofs) for x in seq}
    dec = st.get("decisions") or {}
    todo = [pr for pr in sorted(st.get("proposals", []), key=lambda x: _ORDER.get(x["kind"], 9))
            if dec.get(pr["id"]) == "accept" and _current(pr, keys) and pr["kind"] in PROOF_KINDS]
    if not todo:
        run.log("No change that needs a new proof was accepted: the proofs of the first draft stand.")
        return
    anew = (p.state.get("catchup") or {}).get("anew") == "reproofs"        # the author asked for them to be written again
    d = p.load("dossier") or {}
    library = {x["key"]: x for x in p.load("library", [])}
    sys_w, sys_c = prompts.RESHAPE_WRITE.format(field=run.field), prompts.RESHAPE_CHECK.format(field=run.field)
    m = _empty(seq, secs, proofs)
    chain: list = []
    kept = written = failed = 0
    for n, pr in enumerate(todo):
        run.check()
        wkey = _sig(pr["inputs"], pr["plan"], chain)
        if not anew and pr.get("wkey") == wkey and (pr.get("new") or pr.get("fail")):
            # written for exactly this plan on exactly this paper: it stands, with the author's decision on it
            if not pr.get("fail"):
                trial = apply(copy.deepcopy(m), [pr])
                if pr["id"] in trial["applied"]:
                    m = trial
            kept += 1
        else:
            run.progress(n, len(todo), f"writing the proofs of change {n + 1} of {len(todo)}: {KIND_TEXT[pr['kind']].lower()}")
            for key in ("new", "fail", "check2", "on", "saved", "written_by", "checked2_by"):
                pr.pop(key, None)
            st.setdefault("decisions2", {}).pop(pr["id"], None)
            try:
                new, info = _write(run, pr, m, d, library, sys_w, sys_c)
            except (LLMError, Cancelled):
                p.save("reshape", st)
                raise                              # a limit or a stop: the step waits or stops, as every step does
            except Exception as e:  # noqa: BLE001 - one change that cannot be written never holds up the paper
                new, info = None, {"fail": f"it could not be written ({type(e).__name__}: {str(e)[:120]})."}
            pr["wkey"] = wkey
            if new is None:
                pr["fail"] = info["fail"]
                failed += 1
                run.log(f"The change {pr['id']} ({KIND_TEXT[pr['kind']].lower()}) is dropped: {pr['fail']} The first draft's version stands.", "warn")
            else:
                pr["new"] = new
                pr.update(info["set"])
                m = info["model"]
                written += 1
            p.save("reshape", st)
        chain.append([pr["id"], wkey, bool(pr.get("fail"))])
    st["written"] = time.time()
    p.save("reshape", st)
    run.progress(1, 1, "new proofs written")
    run.log(f"New proofs: {written} change(s) written and checked" + (f", {kept} kept as written before" if kept else "")
            + (f", {failed} dropped (the first draft's version stands)" if failed else "") + ".")


def _write(run: Run, pr: dict, m: dict, d: dict, library: dict, sys_w: str, sys_c: str) -> tuple[dict | None, dict]:
    """The proofs of one accepted change, written on the paper as it stands (m) and checked. Returns the record that
    is applied and what to note with it (and the paper with the change applied), or None and why it failed."""
    from . import revision
    from .drafting import numbers, screen_proof
    seq, secs, proofs = m["seq"], m["secs"], m["proofs"]
    by = {x["id"]: x for x in seq}
    nums = numbers(secs, seq)
    if any(i not in by or not (proofs.get(i) or {}).get("text") for i in pr["affects"]):
        return None, {"fail": "a result it changes was already changed by another change you accepted."}
    plan = pr["plan"]
    all_statements = _listing(seq, nums, proofs, with_proofs=False)
    named = [i for i in dict.fromkeys(re.findall(r"\b[A-Za-z]+\d+[a-z]?\b", pr["idea"])) if i in by and i not in pr["affects"]]
    moved = "; ".join(f"{a} is now part of {b}" for a, b in m["remap"].items())
    user = (f"THE CHANGE\nkind: {pr['kind']}\nwhat to do: {pr['idea']}\n\nTHE PLAN THE AUTHOR APPROVED\n{_plan_text(pr)}\n\n"
            "THE STATEMENTS OF THE PAPER, IN ORDER\n" + fit(all_statements, max(3000, _room('reason', sys_w, 6000) // 3), "middle")
            + (f"\n(Changes already made in this revision: {moved}.)" if moved else "")
            + "\n\nTHE RESULTS THE CHANGE IS ABOUT, AS THEY STAND\n" + _listing(seq, nums, proofs, pr["affects"])
            + ("\n\nOTHER RESULTS IT REFERS TO (for reference; do not rewrite them)\n" + _listing(seq, nums, proofs, named) if named else ""))
    size = sum(len(proofs[i]["text"]) for i in pr["affects"])
    out = min(12000, int(est_tokens("x" * size) * 1.4) + 1800)
    res = run.ask("reason", sys_w, fit(user, _room("reason", sys_w, out), "middle"), max_out=out, effort="high", paper=True,
                  temperature=0.1, label=f"Write out {pr['id']}: {pr['kind']}")
    new, err = _parse(pr["kind"], res.text or "", pr, seq, plan.get("id") or "X0")
    if not new:
        return None, {"fail": err + "."}
    if pr["kind"] == "merge":                      # the statement is the one the author approved, word for word
        new = {"into": plan["into"], "kind": plan["kind"], "name": plan["name"], "statement": plan["statement"],
               "parts": plan["parts"], "proof": new["proof"]}
    elif pr["kind"] == "method":
        new = {**new, "id": plan["id"], "kind": plan["kind"], "name": plan["name"], "statement": plan["statement"]}
    cand = {**pr, "new": new}
    if any(check_balance(t) for t in _texts(cand)):
        return None, {"fail": "the text written for it had unbalanced LaTeX."}
    trial = apply(copy.deepcopy(m), [cand])
    if pr["id"] not in trial["applied"]:
        return None, {"fail": (trial["unapplied"][-1]["why"] if trial["unapplied"] else "it could not be put into the paper") + "."}
    # every new proof: well formed, no statement of its own, references only to what stands before it
    t_nums = numbers(trial["secs"], trial["seq"])
    ctx = {"seq": trial["seq"], "nums": t_nums, "library": library}
    t_by = {x["id"]: x for x in trial["seq"]}
    changed = [i for i in t_by if (trial["proofs"].get(i) or {}).get("reshaped") == pr["id"]]
    bad = []
    for i in changed:
        _, problems = screen_proof(trial["proofs"][i]["text"], t_by[i], ctx, [])
        bad += [f"{i}: {x}" for x in problems]
    if bad:
        return None, {"fail": "the text written for it did not pass the program's checks (" + fit(bad[0], 200, "head") + ")."}
    before = _listing(seq, nums, proofs, pr["affects"] + named)
    after = _listing(trial["seq"], t_nums, trial["proofs"], [i for i in changed if i in t_by] + [new.get("into") or ""])
    check_user = (f"THE CHANGE\nkind: {pr['kind']}\n{pr['idea']}\n\nTHE STATEMENTS OF THE PAPER BEFORE THE CHANGE, IN ORDER\n"
                  + fit(all_statements, 6000, "middle") + f"\n\nAS IT WAS\n{before}\n\nAS IT IS AFTER THE CHANGE\n{after}")
    chk = run.ask("reason", sys_c, fit(check_user, _room("reason", sys_c, 900), "middle"), max_out=900, effort="high",
                  other_than=res.model, label=f"Check {pr['id']}")
    verdict = extract_tag(chk.text, "verdict").strip().lower()
    reason = re.sub(r"\s+", " ", extract_tag(chk.text, "reason")).strip()
    if not verdict.startswith("valid"):
        return None, {"fail": "the independent check did not confirm it" + (f" ({reason})" if reason else ".")}
    if pr["kind"] in ("argument", "method"):
        # mathematics that is not in the notes as such (another argument, a general statement): also read line by
        # line, as the proof check reads every proof, before the author is asked to look at it
        units = [{"label": "res:" + x["id"], "name": _name(x, t_nums), "statement": x["statement"],
                  "proof": (trial["proofs"].get(x["id"]) or {}).get("text"), "at": n} for n, x in enumerate(trial["seq"])]
        env = {"setting": "\n".join(x for x in (d.get("setting", ""), d.get("notation_choice", "")) if x), "units": units}
        targets = [u for u in units if u["label"][4:] in (changed if pr["kind"] == "argument" else [new["id"]]) and u["proof"]]
        issues = [c for u in targets for c in revision.check_proof(run, u, env, label_prefix="proposed ")]
        if issues:
            return None, {"fail": "a line-by-line reading found a problem in it (" + fit(issues[0]["problem"], 220, "head") + ")."}
    refs = set(_REF.findall("\n".join(_texts(cand))))
    on = [q for q in m["applied"] if refs & m["made"].get(q, set())]       # the earlier changes this text builds on
    saved = (sum(len(t) for t in trial["waived"]) - sum(len(t) for t in m["waived"])
             - sum(len(t) for t in trial["approved"]) + sum(len(t) for t in m["approved"]))
    return new, {"set": {"written_by": res.model, "check2": reason, "checked2_by": chk.model, "saved": saved, "on": on}, "model": trial}


# ------------------------------------------------------------------ the author decides: the plan, then the new proofs

_MARK = {"merge": "one statement", "method": "applies the general statement", "similar": "proof by reference to another",
         "shorten": "proof shortened", "argument": "another argument"}


def _written(st: dict) -> list[dict]:
    """The changes whose proofs were written and stand: what the author reviews at the second stop."""
    dec = st.get("decisions") or {}
    return [pr for pr in st.get("proposals", []) if pr["kind"] in PROOF_KINDS and dec.get(pr["id"]) == "accept"
            and pr.get("new") and not pr.get("fail")]


def view(p: Project) -> dict:
    """What the author sees. At the first stop: the plan (each change with the statements it writes and the outline
    of each proof it changes) and the paper as a list of sections and results, as it is and as it would be. At the
    second stop: each new proof, in full, beside the one it replaces."""
    from .drafting import kind_name, numbers
    st = p.load("reshape") or {}
    if not st:
        return {"proposals": [], "written": [], "decisions": {}, "decisions2": {}, "dropped": [], "failed": [], "skeleton": None}
    seq, secs, proofs = base(p)
    nums = numbers(secs, seq)
    by = {x["id"]: x for x in seq}
    dec = st.get("decisions") or {}

    def said(text: str) -> str:
        """A text of the models, with each result called as the author knows it (Lemma 2.2), not by its id (R2)."""
        return re.sub(r"\b(?:res:|def:)?([A-Z]+\d+[a-z]?)\b", lambda g: _name(by[g.group(1)], nums) if g.group(1) in by else g.group(0), text or "")

    def card(pr: dict, new: dict, outline: bool) -> dict:
        v = {"id": pr["id"], "kind": pr["kind"], "title": KIND_TEXT[pr["kind"]], "idea": said(pr["idea"]), "why": said(pr.get("why", "")),
             "check": said(pr.get("check", "") if outline else pr.get("check2", "")), "affects": pr["affects"],
             "names": {i: _name(by[i], nums) for i in pr["affects"]}, "ai": pr["kind"] == "argument", "outline": outline,
             "saved": 0 if outline else pr.get("saved", 0), "on": [] if outline else list(pr.get("on") or [])}
        v["before"] = [{"id": i, "name": _name(by[i], nums), "statement": by[i]["statement"],
                        "proof": (proofs.get(i) or {}).get("text", "")} for i in pr["affects"]]
        if pr["kind"] == "merge":
            v["after"] = [{"name": kind_name(new["kind"]) + (f" ({new['name']})" if new.get("name") else ""),
                           "statement": new["statement"], "proof": new["proof"]}]
            v["parts"] = {_name(by[i], nums): f"({r})" for i, r in new["parts"].items()}
        elif pr["kind"] == "method":
            v["added"] = {"name": kind_name(new["kind"]) + (f" ({new['name']})" if new.get("name") else ""),
                          "statement": new["statement"], "proof": new["text"]}
            v["after"] = [{"id": i, "name": _name(by[i], nums), "proof": t} for i, t in new["proofs"].items()]
        elif pr["kind"] in ("similar", "shorten", "argument"):
            v["after"] = [{"id": i, "name": _name(by[i], nums), "proof": t} for i, t in new["proofs"].items()]
            v["before"] = [b for b in v["before"] if b["id"] in new["proofs"]]
        elif pr["kind"] == "kind":
            v["renamed"] = [{"name": _name(by[i], nums), "to": kind_name(k)} for i, k in new["kinds"].items() if i in by]
            v["before"] = []
        else:
            line = lambda s, nn: {"title": s["title"], "items": [_name(by[i], nn) if i in by else i for i in s["items"]]}  # noqa: E731
            v["sections_now"] = [line(s, nums) for s in secs]
            v["sections_new"] = [line(s, numbers(new["sections"], seq)) for s in new["sections"]]
            v["before"] = []
        return v

    here = [pr for pr in st.get("proposals", []) if pr.get("plan") and all(i in by for i in pr["affects"])]
    # the paper as a list: as the first draft has it, and as it would be with every change of the plan
    live = sorted(here, key=lambda x: _ORDER.get(x["kind"], 9))
    trial = apply(_empty(*copy.deepcopy((seq, secs, proofs))), [{**pr, "new": pr["plan"]} for pr in live])
    t_nums, t_by = numbers(trial["secs"], trial["seq"]), {x["id"]: x for x in trial["seq"]}
    kind_of = {pr["id"]: pr["kind"] for pr in here}
    renamed = {i: pr["id"] for pr in live if pr["kind"] == "kind" for i in pr["plan"]["kinds"]}
    after = []
    for s in trial["secs"]:
        items = []
        for i in s["items"]:
            x, pf = t_by[i], trial["proofs"].get(i) or {}
            pid = x.get("reshaped") or pf.get("reshaped") or renamed.get(i, "")
            mark = ("new" if i not in by else _MARK.get(kind_of.get(pf.get("reshaped") or x.get("reshaped"), ""), "")
                    or ("called " + kind_name(x["kind"]).lower() if i in renamed else ""))
            items.append({"name": _name(x, t_nums), "mark": mark, "by": pid if pid in trial["applied"] else ""})
        after.append({"title": s["title"], "items": items})
    skeleton = {"now": [{"title": s["title"], "items": [{"name": _name(by[i], nums)} for i in s["items"] if i in by]} for s in secs],
                "new": after, "moved": any(pr["kind"] == "sections" for pr in live)}
    return {"proposals": [card(pr, pr["plan"], True) for pr in here],
            "written": [card(pr, pr["new"], False) for pr in _written(st) if pr in here],
            "failed": [{"id": pr["id"], "title": KIND_TEXT[pr["kind"]], "names": [_name(by[i], nums) for i in pr["affects"] if i in by],
                        "reason": said(pr["fail"])} for pr in here if dec.get(pr["id"]) == "accept" and pr.get("fail")],
            "decisions": dec, "decisions2": st.get("decisions2") or {}, "done": bool(st.get("done")), "skeleton": skeleton,
            "dropped": [{"kind": x["kind"], "title": KIND_TEXT.get(x["kind"], x["kind"]), "reason": said(x["reason"]),
                         "names": [_name(by[i], nums) for i in x.get("affects", []) if i in by]} for x in st.get("dropped", [])]}


def nothing_to_decide(p: Project) -> bool:
    st = p.load("reshape") or {}
    return not [pr for pr in st.get("proposals", []) if (st.get("decisions") or {}).get(pr["id"]) not in ("accept", "reject")]


def needed(p: Project) -> list[str]:
    """After the paper was brought up to date: the planned changes the author has not decided."""
    st = p.load("reshape") or {}
    return [f"the change {pr['id']} ({KIND_TEXT[pr['kind']].lower()}) is new" for pr in st.get("proposals", [])
            if (st.get("decisions") or {}).get(pr["id"]) not in ("accept", "reject")]


def submit(p: Project, data: dict) -> None:
    """The author accepts or rejects each change of the plan. Nothing is written or applied that the author did not accept."""
    with _lock:
        st = p.load("reshape") or {"proposals": []}
        dec, missing = {}, []
        for pr in st.get("proposals", []):
            choice = (data or {}).get(pr["id"])
            if choice not in ("accept", "reject"):
                missing.append(pr["id"])
            dec[pr["id"]] = choice
        if missing:
            raise ValueError(f"{len(missing)} change(s) of the plan still need your decision.")
        st["decisions"] = dec
        st["decided"] = time.time()
        p.save("reshape", st)
    p.set_stage("reshapecheck", status="done", finished=time.time())
    acc = sum(1 for v in dec.values() if v == "accept")
    emit(p.id, "log", stage="reshapecheck", level="done",
         message=f"Your plan: {acc} change(s) accepted, {len(dec) - acc} rejected.")


def nothing_to_review(p: Project) -> bool:
    st = p.load("reshape") or {}
    return not [pr for pr in _written(st) if (st.get("decisions2") or {}).get(pr["id"]) not in ("accept", "reject")]


def needed2(p: Project) -> list[str]:
    """After the paper was brought up to date: the new proofs the author has not looked at."""
    st = p.load("reshape") or {}
    return [f"the proofs of the change {pr['id']} ({KIND_TEXT[pr['kind']].lower()}) are new" for pr in _written(st)
            if (st.get("decisions2") or {}).get(pr["id"]) not in ("accept", "reject")]


def submit_proofs(p: Project, data: dict) -> None:
    """The author accepts each new proof or keeps the proof of the first draft. A change that was written on top of
    another one stands only if that one does."""
    with _lock:
        st = p.load("reshape") or {"proposals": []}
        dec2, missing = {}, []
        for pr in _written(st):
            choice = (data or {}).get(pr["id"])
            if choice not in ("accept", "reject"):
                missing.append(pr["id"])
            dec2[pr["id"]] = choice
        if missing:
            raise ValueError(f"{len(missing)} new proof(s) still need your decision.")
        forced = []
        again = True
        while again:
            again = False
            for pr in _written(st):
                if dec2[pr["id"]] == "accept" and any(dec2.get(q) != "accept" for q in pr.get("on") or []):
                    dec2[pr["id"]] = "reject"
                    forced.append(pr["id"])
                    again = True
        st["decisions2"] = dec2
        st["reviewed"] = time.time()
        p.save("reshape", st)
    p.set_stage("reproofcheck", status="done", finished=time.time())
    acc = sum(1 for v in dec2.values() if v == "accept")
    emit(p.id, "log", stage="reproofcheck", level="done",
         message=f"Your review of the new proofs: {acc} accepted, {len(dec2) - acc} kept as in the first draft."
                 + (f" ({', '.join(forced)} builds on a change you did not accept, so it is not used either.)" if forced else ""))
