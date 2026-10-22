# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Which model does a job, and on which key: Gemini models rank by tier, then version; a quality-critical job uses the
strongest model the keys can use, one key after the other, and never a lesser model by itself."""
import json, os, sys, threading, time, pathlib, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.pop("SCRIPTAVERA_MOCK", None)
from scriptavera import config
tmp = pathlib.Path(tempfile.mkdtemp())
config.SETTINGS_PATH = tmp / "settings.json"; config.ENV_PATH = tmp / ".env"
from scriptavera.llm import ROLES, LLMError, Model, Router, RunHooks, classify

# ---- Gemini: tier first (4 Argon, Pro, Flash, Flash-Lite), then version
names = ["gemini-4-argon", "gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
         "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
ms = {n: classify("gemini", n) for n in names}
for role in ("reason", "writer", "vision_best", "reader"):
    assert sorted(names, key=lambda n: -ms[n].scores[role]) == names, role
assert ms["gemini-3.1-pro-preview"].scores["reason"] > ms["gemini-3.8-flash"].scores["reason"], "Pro above the newest Flash"
assert max(names, key=lambda n: ms[n].scores["fast"]) == "gemini-3.5-flash-lite" and max(names, key=lambda n: ms[n].scores["vision_fast"]) == "gemini-3.5-flash-lite"
assert all(ms[n].vision for n in names) and ms["gemini-4-argon"].interval == 13.0
assert classify("gemini", "models/gemini-4-argon") and not any(classify("gemini", n) for n in ("gemini-4-argon-live", "gemini-3.8-flash-tts", "gemini-3.1-flash-image"))
assert all(ROLES[r]["margin"] == 0 for r in ROLES if ROLES[r]["strict"]), "a strict job: the same model only"
print("1. Gemini ranked by tier, then version: 4 Argon, 3.1 Pro, 2.5 Pro, 3.8 Flash ...; Flash-Lite first for small tasks")

# the strongest model the keys can use: on free keys Argon and Pro are refused, and 3.8 Flash on both keys is the class
r = Router()
r.discovered_at = time.time()
r.models = []
for ki, key in enumerate(("AQ.one", "AQ.two")):
    for n in names:
        m = classify("gemini", n); m.key, m.key_index = key, ki
        r.models.append(m)
assert [m.ref for m in r.peers("reason")] == ["gemini:gemini-4-argon", "gemini:gemini-4-argon#2"]
for m in r.models:
    if m.id in ("gemini-4-argon", "gemini-3.1-pro-preview", "gemini-2.5-pro"):
        m.dead = True                                   # "limit: 0": not on these keys' free tier
assert [m.ref for m in r.peers("reason")] == ["gemini:gemini-3.8-flash", "gemini:gemini-3.8-flash#2"], [m.ref for m in r.peers("reason")]
for m in r.models:
    if m.id == "gemini-3.8-flash":
        m.cooldown_until = time.time() + 3600           # both keys out for the day: nothing lesser steps in
assert r.candidates("reason") == [] and r.plan()["reason"]["weaker"][0] == "gemini:gemini-3.7-flash"
print("2. free keys: 3.8 Flash on key 1, then on key 2; 3.7 Flash only if you allow it")

# ---- against a fake provider with two keys
calls = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get("Authorization") not in ("Bearer sk-one", "Bearer sk-two"):
            return self._send(401, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        self._send(200, {"data": [{"id": i} for i in ("top", "mid", "low", "paid")]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers.get("Authorization", "")[7:]
        m = body["model"]; calls.append((m, key))
        ok = lambda text: self._send(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {}})
        if key == "sk-expired":
            return self._send(400, {"error": {"message": "API key expired. Please renew the API key.", "status": "INVALID_ARGUMENT"}})
        if m == "top":
            return self._send(429, {"error": {"message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: top"}})
        if m == "mid":
            if key == "sk-one":
                return self._send(429, {"error": {"code": 429, "message": "Resource exhausted. Please retry.", "details": [{"retryDelay": "20s"}]}})
            return ok("mid, from key two")
        if m == "paid":
            return self._send(403, {"error": {"message": "Permission denied: this model is not available for your project."}})
        return ok("low")

srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "OLLAMA_BASE_URL", "CUSTOM_MODELS"):
    os.environ.pop(k, None)
os.environ["CUSTOM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
os.environ["CUSTOM_API_KEY"] = "sk-one,sk-bad"
from scriptavera.llm import router
st = router.discover(force=True)["custom"]
assert st["ok"] and st["message"] == "4 usable models" and [(k["n"], k["ok"]) for k in st["keys"]] == [(1, True), (2, False)], st
assert "rejected" in st["keys"][1]["message"], st["keys"]
print("3. each key reported on its own: key 1 works, key 2 rejected")

os.environ["CUSTOM_API_KEY"] = "sk-one,sk-two"
router.discover(force=True)
score = {"top": 100, "mid": 90, "low": 80, "paid": 95}
for m in router.models:
    m.interval = 0.0
    m.scores = {r_: score[m.id] for r_ in ROLES if not r_.startswith("vision")}
sleeps = []
class Hk(RunHooks):
    def sleep(self, seconds): sleeps.append(seconds)
res = router.chat("reason", "s", "u", hooks=Hk(), label="one call")
assert res.text == "mid, from key two", res.text
by = {(m.id, m.key): m for m in router.models}
assert by[("top", "sk-one")].dead and by[("top", "sk-two")].dead and "free tier" in by[("top", "sk-one")].note
assert by[("paid", "sk-one")].dead and not by[("low", "sk-one")].dead, "a model this key may not use, not the whole key"
assert 15 < by[("mid", "sk-one")].cooldown_until - time.time() < 25 and not any(x >= 20 for x in sleeps), sleeps
assert [c for c in calls if c[0] != "paid"] == [("top", "sk-one"), ("top", "sk-two"), ("mid", "sk-one"), ("mid", "sk-two")], calls
print("4. in one call: the model these keys cannot use is skipped, and a per-minute limit on key 1 goes straight to key 2")

os.environ["CUSTOM_API_KEY"] = "sk-expired"
router.models = [Model(provider="custom", id="low", key="sk-expired", req_tokens=24000, out_cap=6000, interval=0.0, scores={"fast": 80}),
                 Model(provider="custom", id="mid", key="sk-expired", req_tokens=24000, out_cap=6000, interval=0.0, scores={"fast": 70})]
try:
    router.chat("fast", "s", "u", hooks=Hk(), label="expired"); raise SystemExit("an expired key must fail")
except LLMError:
    pass
assert all(m.dead and m.note == "key rejected" for m in router.models), [(m.id, m.note) for m in router.models]
print("5. an expired key is set aside as a whole")
print("keys and models ok")
