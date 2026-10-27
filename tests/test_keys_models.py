# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Which model does a job, and on which key. Each Gemini key's tier is found first (a free key never tries Pro or
Argon); Gemini models rank by tier, then version; a quality-critical job uses the best model on one key after the
other, then the next model almost as good, and never a weaker one."""
import json, os, sys, threading, time, pathlib, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.pop("SCRIPTAVERA_MOCK", None)
from scriptavera import config
tmp = pathlib.Path(tempfile.mkdtemp())
config.SETTINGS_PATH = tmp / "settings.json"; config.ENV_PATH = tmp / ".env"
from scriptavera import llm
from scriptavera.llm import ROLES, LLMError, Model, Router, RunHooks, classify

# ---- Gemini: tier first (4 Argon, Pro, Flash, Flash-Lite), then version
names = ["gemini-4-argon", "gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
         "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
ms = {n: classify("gemini", n) for n in names}
for role in ("reason", "writer", "vision_best", "reader"):
    assert sorted(names, key=lambda n: -ms[n].scores[role]) == names, role
assert [ms[n].tier for n in names] == [3, 2, 2, 1, 1, 1, 1, 1, 0, 0]
assert max(names, key=lambda n: ms[n].scores["fast"]) == "gemini-3.5-flash-lite" and max(names, key=lambda n: ms[n].scores["vision_fast"]) == "gemini-3.5-flash-lite"
assert classify("gemini", "models/gemini-4-argon") and not any(classify("gemini", n) for n in ("gemini-4-argon-live", "gemini-3.8-flash-tts", "gemini-3.1-flash-image"))
assert all(ROLES[r]["margin"] == 1.0 for r in ROLES if ROLES[r]["strict"])
print("1. Gemini ranked by tier, then version: 4 Argon, 3.1 Pro, 2.5 Pro, 3.8 Flash ...; Flash-Lite first for small tasks")

# free keys: 3.8 Flash on key 1, on key 2, then 3.7 and 3.6 Flash the same way; nothing weaker, ever
r = Router()
r.discovered_at = time.time()
r.models = []
for ki, key in enumerate(("AQ.one", "AQ.two")):
    for n in names:
        m = classify("gemini", n); m.key, m.key_index = key, ki
        m.dead = m.tier >= 2                            # what the check of each key's tier does on free keys
        r.models.append(m)
order = ["gemini:gemini-3.8-flash", "gemini:gemini-3.8-flash#2", "gemini:gemini-3.7-flash", "gemini:gemini-3.7-flash#2",
         "gemini:gemini-3.6-flash", "gemini:gemini-3.6-flash#2"]
assert [m.ref for m in r.candidates("reason")] == order, [m.ref for m in r.candidates("reason")]
for m in r.models:
    if m.id == "gemini-3.8-flash" and m.key_index == 0:
        m.cooldown_until = time.time() + 3600           # key 1 has reached 3.8 Flash's limit: key 2 next
assert r.candidates("reason")[0].ref == "gemini:gemini-3.8-flash#2"
for m in r.models:
    if m.id == "gemini-3.8-flash":
        m.cooldown_until = time.time() + 3600           # both keys: the next best model, 3.7 Flash
assert [m.ref for m in r.candidates("reason")][:2] == ["gemini:gemini-3.7-flash", "gemini:gemini-3.7-flash#2"]
for m in r.models:
    if m.id in ("gemini-3.7-flash", "gemini-3.6-flash"):
        m.cooldown_until = time.time() + 3600           # every model almost as good is out: wait, never 3 Flash Preview
assert r.candidates("reason") == []
try:
    r.chat("reason", "s", "u", hooks=RunHooks(), label="x"); raise SystemExit("must wait")
except LLMError as e:
    assert e.kind == "limits" and not e.weaker and "never used" in str(e), str(e)
assert all(m.id not in ("gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.5-flash-lite") for m in r.peers("reason"))
# small jobs move only to a model at least as capable
small = [m.id for m in r.candidates("fast")]
assert small[0] == "gemini-3.5-flash-lite" and "gemini-3.1-flash-lite" in small, small
print("2. free keys: 3.8 Flash on key 1, then key 2, then 3.7 and 3.6 Flash; then wait, never a weaker model")

# ---- the tier of each Gemini key, found before any work: against a fake Gemini service
calls = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        key = self.headers.get("Authorization", "")[7:]
        if key not in ("AQ.free", "AQ.paid", "sk-one", "sk-two"):
            return self._send(401, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        if key.startswith("AQ."):
            return self._send(200, {"data": [{"id": "models/" + i} for i in ("gemini-4-argon", "gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite")]})
        self._send(200, {"data": [{"id": i} for i in ("top", "mid", "low", "paid")]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "")[7:]
        m = body["model"]; calls.append((m, key))
        ok = lambda text: self._send(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {}})
        if key == "sk-expired":
            return self._send(400, {"error": {"message": "API key expired. Please renew the API key.", "status": "INVALID_ARGUMENT"}})
        if key == "AQ.free" and ("argon" in m or "pro" in m):
            return self._send(429, {"error": {"code": 429, "message": f"Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: {m}"}})
        if m == "top":
            return self._send(429, {"error": {"message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: top"}})
        if m == "mid":
            if key == "sk-one":
                return self._send(429, {"error": {"code": 429, "message": "Resource exhausted. Please retry.", "details": [{"retryDelay": "20s"}]}})
            return ok("mid, from key two")
        if m == "paid":
            return self._send(403, {"error": {"message": "Permission denied: this model is not available for your project."}})
        return ok(f"{m} answered")

srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "OLLAMA_BASE_URL", "CUSTOM_MODELS",
          "CUSTOM_API_KEY", "CUSTOM_BASE_URL"):
    os.environ.pop(k, None)
llm.PROVIDERS["gemini"]["base"] = f"http://127.0.0.1:{port}/v1"
os.environ["GEMINI_API_KEY"] = "AQ.free,AQ.paid"
from scriptavera.llm import router
st = router.discover(force=True)["gemini"]
assert [(k["n"], k["tier"]) for k in st["keys"]] == [(1, "free"), (2, "paid")], st["keys"]
assert sorted(calls) == [("gemini-4-argon", "AQ.free"), ("gemini-4-argon", "AQ.paid")], calls      # one tiny request per key
free_pro = [m for m in router.models if m.key == "AQ.free" and m.tier >= 2]
assert free_pro and all(m.dead and "free tier" in m.note for m in free_pro), "set aside before any work"
assert st["keys"][0]["count"] == 3 and st["keys"][1]["count"] == 5, st["keys"]
assert [m.ref for m in router.peers("reason")] == ["gemini:gemini-4-argon#2"], [m.ref for m in router.peers("reason")]
calls.clear()
assert router.chat("reason", "s", "u", hooks=RunHooks(), label="paid").text == "gemini-4-argon answered" and calls == [("gemini-4-argon", "AQ.paid")]
os.environ["GEMINI_API_KEY"] = "AQ.free"
calls.clear()
router.discover(force=True)
assert [m.ref for m in router.peers("reason")] == ["gemini:gemini-3.8-flash", "gemini:gemini-3.7-flash"]
assert router.chat("reason", "s", "u", hooks=RunHooks(), label="free").model == "gemini-3.8-flash"
assert calls == [("gemini-4-argon", "AQ.free"), ("gemini-3.8-flash", "AQ.free")], calls        # the work never tried Argon or Pro
print("3. the tier of each key found first: a free key goes straight to 3.8 Flash, a paid key to Argon")

# ---- two keys of another provider: each reported on its own; one call skips what a key cannot use
os.environ.pop("GEMINI_API_KEY")
os.environ["CUSTOM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
os.environ["CUSTOM_API_KEY"] = "sk-one,sk-bad"
st = router.discover(force=True)["custom"]
assert st["ok"] and st["message"] == "4 usable models" and [(k["n"], k["ok"]) for k in st["keys"]] == [(1, True), (2, False)], st
assert "rejected" in st["keys"][1]["message"], st["keys"]
os.environ["CUSTOM_API_KEY"] = "sk-one,sk-two"
router.discover(force=True)
score = {"top": 100, "mid": 90, "low": 80, "paid": 95}
for m in router.models:
    m.interval = 0.0
    m.scores = {r_: score[m.id] for r_ in ROLES if not r_.startswith("vision")}
sleeps = []
class Hk(RunHooks):
    def sleep(self, seconds): sleeps.append(seconds)
calls.clear()
res = router.chat("reason", "s", "u", hooks=Hk(), label="one call")
assert res.text == "mid, from key two", res.text
by = {(m.id, m.key): m for m in router.models}
assert by[("top", "sk-one")].dead and by[("top", "sk-two")].dead and "free tier" in by[("top", "sk-one")].note
assert by[("paid", "sk-one")].dead and not by[("low", "sk-one")].dead, "a model this key may not use, not the whole key"
assert 15 < by[("mid", "sk-one")].cooldown_until - time.time() < 25 and not any(x >= 20 for x in sleeps), sleeps
assert [c for c in calls if c[0] != "paid"] == [("top", "sk-one"), ("top", "sk-two"), ("mid", "sk-one"), ("mid", "sk-two")], calls
by[("mid", "sk-two")].cooldown_until = time.time() + 3600
try:
    router.chat("reason", "s", "u", hooks=Hk(), label="none left"); raise SystemExit("never the weaker model")
except LLMError as e:
    assert e.kind == "limits", str(e)
assert not any(c[0] == "low" for c in calls), "the weaker model was never asked"
print("4. each key reported on its own; a per-minute limit on key 1 goes straight to key 2; the weaker model never answers")

os.environ["CUSTOM_API_KEY"] = "sk-expired"
router.models = [Model(provider="custom", id="low", key="sk-expired", req_tokens=24000, out_cap=6000, interval=0.0, scores={"fast": 80, "reason": 80}),
                 Model(provider="custom", id="mid", key="sk-expired", req_tokens=24000, out_cap=6000, interval=0.0, scores={"fast": 70, "reason": 80})]
try:
    router.chat("fast", "s", "u", hooks=Hk(), label="expired"); raise SystemExit("an expired key must fail")
except LLMError:
    pass
assert all(m.dead and m.note == "key rejected" for m in router.models), [(m.id, m.note) for m in router.models]
print("5. an expired key is set aside as a whole")
print("keys and models ok")
