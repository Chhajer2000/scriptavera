# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What a paper costs in AI calls.

Every call is logged with its model and its input, output and thinking tokens (thinking is billed as output). The
cost of a call is input x input price + (output + thinking) x output price, from the table below (Google's prices on
21 October 2026, with the change Google has announced for 1 January 2027), which settings.json can override
("prices": {"gemini-3.8-flash": [0.75, 3.75]}). Calls to free tiers cost nothing; a model whose price is not known
is counted and said so. Before a run, the cost is estimated from the number of pages, by the per-page cost measured
on a real 20-page run scaled to the paper (writing grows a little faster than the page count)."""
from __future__ import annotations

import datetime
import re

from . import config

# (pattern, (input, output) $/1M tokens, (input, output) for prompts over 200k tokens or None, {date: (input, output)})
TABLE = [
    (r"gemini-4-argon$", (2.00, 10.00), None, {"2026-12-01": (4.00, 20.00)}),
    (r"gemini-3\.8-flash$|gemini-3\.6-flash$", (0.75, 3.75), None, {"2027-01-01": (1.50, 7.50)}),
    (r"gemini-3\.5-flash-lite", (0.30, 2.50), None, {}),
    (r"gemini-3\.1-flash-lite", (0.25, 1.50), None, {}),
    (r"gemini-3\.1-pro", (2.00, 12.00), (4.00, 18.00), {}),
    (r"gemini-3-flash-preview", (0.50, 3.00), None, {}),
    (r"gemini-2\.5-pro", (1.25, 10.00), (2.50, 15.00), {}),
    (r"gemini-2\.5-flash-lite", (0.10, 0.40), None, {}),
    (r"gemini-2\.5-flash$", (0.30, 2.50), None, {}),
]
TABLE_DATE = "21 October 2026"
# measured on a real 20-page run (212 calls), per page, with thinking estimated where it was not yet recorded
PER_PAGE_IN, PER_PAGE_OUT = 44_000, 15_000


def price(model: str, when: float | None = None, prompt: int = 0) -> tuple[float, float] | None:
    own = (config.load_settings().get("prices") or {}).get(model)
    if isinstance(own, (list, tuple)) and len(own) == 2:
        return float(own[0]), float(own[1])
    day = datetime.date.fromtimestamp(when).isoformat() if when else datetime.date.today().isoformat()
    for pat, base, big, later in TABLE:
        if re.search(pat, model or ""):
            p = big if (big and prompt > 200_000) else base
            for d, newer in sorted(later.items()):
                if day >= d:
                    p = newer
            return p
    return None


def call_cost(c: dict) -> tuple[float, bool]:
    """(dollars, known). A free tier (Groq, OpenRouter's free models, local Ollama) costs nothing."""
    if not c.get("ok", True):
        return 0.0, True
    if c.get("provider") in ("groq", "cerebras", "ollama") or ":free" in (c.get("model") or ""):
        return 0.0, True
    p = price(c.get("model") or "", c.get("t"), c.get("in") or 0)
    if p is None:
        return 0.0, False
    return ((c.get("in") or 0) * p[0] + ((c.get("out") or 0) + (c.get("think") or 0)) * p[1]) / 1e6, True


def usage(calls: list[dict]) -> dict:
    stages: dict[str, dict] = {}
    total = {"calls": 0, "in": 0, "out": 0, "think": 0, "cost": 0.0, "unknown": 0, "no_think": 0}
    for c in calls:
        if not c.get("ok", True):
            continue
        cost, known = call_cost(c)
        s = stages.setdefault(c.get("stage") or "other", {"calls": 0, "in": 0, "out": 0, "think": 0, "cost": 0.0})
        for d in (s, total):
            d["calls"] += 1
            d["in"] += c.get("in") or 0
            d["out"] += c.get("out") or 0
            d["think"] += c.get("think") or 0
            d["cost"] += cost
        total["unknown"] += 0 if known else 1
        total["no_think"] += 1 if "think" not in c and c.get("provider") == "gemini" else 0
    for d in [total, *stages.values()]:
        d["cost"] = round(d["cost"], 4)
    return {"total": total, "stages": stages, "table_date": TABLE_DATE}


def estimate(pages: int, typed: bool = False, model: str = "gemini-3.8-flash") -> dict:
    """Low, typical and high cost of a whole paper of this many pages, on one model."""
    p = price(model) or (0.75, 3.75)
    scale = (max(1, pages) / 50) ** 1.15 * 50 / max(1, pages)      # the writers read all the notes: a little faster than linear
    inp, out = PER_PAGE_IN * pages * scale, PER_PAGE_OUT * pages * scale
    if typed:
        inp *= 0.8                                                  # no page images, no transcription
    typical = (inp * p[0] + out * p[1]) / 1e6
    return {"low": round(typical * 0.65, 2), "typical": round(typical, 2), "high": round(typical * 1.75, 2), "model": model,
            "pages": pages}
