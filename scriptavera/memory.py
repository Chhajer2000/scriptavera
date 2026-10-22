# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What Scriptavera learns about its author from one paper to the next, kept on this computer only.

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

from . import config

_lock = threading.Lock()


def _path(name: str):
    d = config.MEMORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _load(name: str) -> dict:
    f = _path(name)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(name: str, data: dict) -> None:
    _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


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
            if _path(name).exists():
                _path(name).unlink()
