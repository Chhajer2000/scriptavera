# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The cost meter: input and (output + thinking) at the model's price on the day of the call, free tiers at nothing,
unknown models said so, and an estimate from the page count."""
import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from helpers import isolate
isolate("scriptavera_costs_")
from scriptavera import costs

oct26 = datetime.datetime(2026, 10, 21).timestamp()
jan27 = datetime.datetime(2027, 1, 2).timestamp()
c, known = costs.call_cost({"provider": "gemini", "model": "gemini-3.8-flash", "in": 1_000_000, "out": 100_000, "think": 300_000, "t": oct26})
assert known and abs(c - (0.75 + 0.4 * 3.75)) < 1e-9, c
c2, _ = costs.call_cost({"provider": "gemini", "model": "gemini-3.8-flash", "in": 1_000_000, "out": 0, "t": jan27})
assert abs(c2 - 1.50) < 1e-9, "the announced price change applies from 1 January 2027"
assert costs.call_cost({"provider": "groq", "model": "openai/gpt-oss-20b", "in": 9_999, "out": 9_999})[0] == 0
assert costs.call_cost({"provider": "gemini", "model": "gemini-9-ultra", "in": 10, "out": 10})[1] is False
assert costs.price("gemini-3.1-pro-preview", oct26, 250_000) == (4.00, 18.00)
u = costs.usage([{"stage": "survey", "provider": "gemini", "model": "gemini-3.8-flash", "in": 2000, "out": 400, "think": 600, "ok": True, "t": oct26},
                 {"stage": "survey", "provider": "gemini", "model": "gemini-3.8-flash", "in": 0, "out": 0, "ok": False},
                 {"stage": "verify", "provider": "gemini", "model": "gemini-3.8-flash", "in": 5000, "out": 500, "ok": True, "t": oct26}])
assert u["total"]["calls"] == 2 and u["stages"]["survey"]["calls"] == 1 and u["total"]["no_think"] == 1
e = costs.estimate(50)
assert e["low"] < e["typical"] < e["high"] and 3 < e["typical"] < 7, e
print("costs ok")
