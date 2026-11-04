# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The keys a paper uses: only the ones chosen for it, the free ones first until their limit, then the paid ones; and
what the author is told about each key (plan, limits reached and when they renew)."""
import time, types
from helpers import isolate
isolate("scriptavera_keys_")
from scriptavera import llm
from scriptavera.llm import KEY_SCOPE, ROLES, Model, Router, key_fp


def model(mid, key, tier, score):
    m = Model(provider="gemini", id=mid, vision=True, req_tokens=100000, out_cap=8000)
    m.scores = {r: score for r in ROLES}
    m.key, m.key_fp, m.key_tier = key, key_fp(key), tier
    return m


r = Router({})
free_a = model("gemini-3.8-flash", "AIzaFREEKEY-aaaaaaaa", "free", 80)
paid_flash = model("gemini-3.8-flash", "AIzaPAIDKEY-bbbbbbbb", "paid", 80)
paid_pro = model("gemini-3.1-pro", "AIzaPAIDKEY-bbbbbbbb", "paid", 95)
r.models = [free_a, paid_flash, paid_pro]
r.discovered_at = time.time()
r.status = {"gemini": {"label": "Google Gemini", "ok": True, "keys": [
    {"n": 1, "ok": True, "count": 1, "tier": "free", "fp": free_a.key_fp, "masked": "AIza…aaaa"},
    {"n": 2, "ok": True, "count": 2, "tier": "paid", "fp": paid_flash.key_fp, "masked": "AIza…bbbb"}]}}

assert r._pool("reason", False) == [free_a], "with free and paid keys, the free key is used first"
free_a.cooldown_until = time.time() + 3600; free_a.note = "daily limit reached"
assert set(map(id, r._pool("reason", False))) == {id(paid_flash), id(paid_pro)}, "its limit reached: the paid key takes over"
assert r.peers("reason")[0] is paid_pro
print("1. free keys first; when their best model has reached its limit, the paid keys")

tok = KEY_SCOPE.set(frozenset({free_a.key_fp}))
assert r._pool("reason", False) == [free_a], "only the keys chosen for the paper"
KEY_SCOPE.reset(tok)
free_a.cooldown_until = 0
paper = types.SimpleNamespace(state={"meta": {"keys": {"fps": [paid_flash.key_fp, "gone-key-fp"]}}})
assert r.chosen_keys(types.SimpleNamespace(p=paper)) == frozenset({paid_flash.key_fp})
assert r.chosen_keys(types.SimpleNamespace(p=types.SimpleNamespace(state={"meta": {"keys": {"fps": ["gone"]}}}))) is None
print("2. a paper uses only the keys chosen for it; a choice of keys that no longer exist means every key")

free_a.cooldown_until = time.time() + 7200
rep = {k["fp"]: k for k in r.key_report()}
a = rep[free_a.key_fp]
assert a["tier"] == "free" and a["all_resting"] and a["resting"][0]["note"] == "daily limit reached" and a["free_again"] > time.time()
assert rep[paid_flash.key_fp]["tier"] == "paid" and not rep[paid_flash.key_fp]["all_resting"]
print("3. each key's plan, the limits it has reached and when they renew")
print("keys ok")
