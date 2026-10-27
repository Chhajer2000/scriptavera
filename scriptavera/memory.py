# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What Scriptavera learns about its author from one paper to the next: on a computer, kept in memory/ on that computer;
on scriptavera.com, kept for each person separately (signed in: their own row in the database; guest: this
server's memory), and only ever used for that person's own papers.

- Notation: every meaning the author confirms is remembered with the area it was used in. In the next paper a
  symbol they have already explained is proposed with that meaning, marked as remembered, and accepted unless they
  change it, so only new symbols need answering.
- Handwriting: when the author corrects a doubtful reading (the suggested reading, or their own), the pair is
  remembered; the readers of later papers are told the author's usual confusions ("their z is read as 2").
Test papers teach nothing (they are copies of published work, often in another hand)."""
from __future__ import annotations

import json
import re
import threading
import time

from . import config, hosting, tenancy

_lock = threading.Lock()
_guests: dict[tuple[str, str], dict] = {}            # a guest's memory: in this server's memory only
_cache: dict[tuple[str, str], tuple[float, str]] = {}
CACHE_SECONDS = 60.0


def _path(name: str):
    d = config.MEMORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _owner() -> str | None:
    """Whose memory: on a computer, the files in memory/; on scriptavera.com, the person whose work this is. Work
    that belongs to no one known (x:...) neither learns nor is helped by anyone's memory."""
    o = tenancy.current()
    return o if o and o[:2] in ("u:", "g:") else None


def _load(name: str) -> dict:
    if not hosting.public():
        f = _path(name)
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            return {}
    o = _owner()
    if not o:
        return {}
    if o.startswith("g:"):
        return json.loads(json.dumps(_guests.get((o, name), {})))
    hit = _cache.get((o, name))
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return json.loads(hit[1])
    from . import accounts
    try:
        data = accounts.db().get_memory(o[2:], name) or {}
    except Exception:  # noqa: BLE001 - the database briefly unreachable: nothing remembered this time
        return {}
    _cache[(o, name)] = (time.time(), json.dumps(data, ensure_ascii=False))
    return data


def _save(name: str, data: dict) -> None:
    if not hosting.public():
        _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    o = _owner()
    if not o:
        return
    if o.startswith("g:"):
        _guests[(o, name)] = json.loads(json.dumps(data))
        return
    from . import accounts
    accounts.db().set_memory(o[2:], name, data)
    _cache[(o, name)] = (time.time(), json.dumps(data, ensure_ascii=False))


def merge_guest(guest: str, user: str) -> None:
    """A guest signs in: what was learned from their guest papers joins what their account remembers."""
    for name in ("notation", "handwriting"):
        g = _guests.pop((guest, name), None)
        if not g:
            continue
        with tenancy.owner(user), _lock:
            mine = _load(name)
            for k, v in g.items():
                if k not in mine:
                    mine[k] = v
                elif name == "handwriting":
                    mine[k]["times"] = mine[k].get("times", 0) + v.get("times", 0)
                else:
                    have = {(m["meaning"].lower(), m["area"]) for m in mine[k].get("meanings", [])}
                    mine[k].setdefault("meanings", []).extend(m for m in v.get("meanings", []) if (m["meaning"].lower(), m["area"]) not in have)
            _save(name, mine)


def _key(symbol: str) -> str:
    return re.sub(r"\s+", "", str(symbol or "")).strip("$").lower()


def learn_notation(meta: dict, confirmed: list[dict]) -> int:
    if meta.get("test"):
        return 0
    area = (meta.get("domains") or ["any"])[0]
    with _lock:
        mem = _load("notation")
        n = 0
        for g in confirmed:
            k, meaning = _key(g.get("symbol")), str(g.get("meaning") or "").strip()
            if not k or not meaning or len(meaning) > 400:
                continue
            rec = mem.setdefault(k, {"symbol": g.get("symbol"), "kind": g.get("kind", "symbol"), "meanings": []})
            same = next((m for m in rec["meanings"] if m["meaning"].lower() == meaning.lower() and m["area"] == area), None)
            if same:
                same["times"] += 1
                same["last"] = time.time()
                same["paper"] = meta.get("name", "")
            else:
                rec["meanings"].append({"meaning": meaning, "area": area, "times": 1, "last": time.time(), "paper": meta.get("name", "")})
            n += 1
        _save("notation", mem)
    return n


def remembered(symbol: str, domains: list[str] | None) -> dict | None:
    """The author's usual meaning of a symbol: in the same area first; elsewhere only if they used it twice."""
    rec = _load("notation").get(_key(symbol))
    if not rec:
        return None
    area = (domains or ["any"])[0]
    pool = [m for m in rec["meanings"] if m["area"] == area] or [m for m in rec["meanings"] if m["times"] >= 2]
    if not pool:
        return None
    best = max(pool, key=lambda m: (m["times"], m["last"]))
    return {"meaning": best["meaning"], "times": best["times"], "paper": best.get("paper", ""), "area": best["area"]}


def learn_reading(meta: dict, read: str, meant: str) -> None:
    if meta.get("test") or not read.strip() or not meant.strip() or read.strip() == meant.strip() or len(read) > 60 or len(meant) > 60:
        return
    with _lock:
        mem = _load("handwriting")
        k = f"{read.strip()}→{meant.strip()}"
        mem[k] = {"read": read.strip(), "meant": meant.strip(), "times": mem.get(k, {}).get("times", 0) + 1, "last": time.time()}
        _save("handwriting", mem)


def quirks(limit: int = 12) -> str:
    rows = sorted(_load("handwriting").values(), key=lambda r: (-r["times"], -r["last"]))[:limit]
    if not rows:
        return ""
    return ("THE AUTHOR'S HANDWRITING (learned from their earlier papers: readings they corrected)\n"
            + "\n".join(f"- read as “{r['read']}”, meant “{r['meant']}” ({r['times']}×)" for r in rows))


def stats() -> dict:
    return {"symbols": len(_load("notation")), "readings": len(_load("handwriting"))}


def clear() -> None:
    with _lock:
        for name in ("notation", "handwriting"):
            if not hosting.public():
                if _path(name).exists():
                    _path(name).unlink()
            else:
                _save(name, {})
