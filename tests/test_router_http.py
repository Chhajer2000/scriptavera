# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Router against a local fake OpenAI-compatible server: listing, fallback, rate limits, images."""
import json, os, sys, threading, time, pathlib, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.pop("SCRIPTAVERA_MOCK", None)
from scriptavera import config
tmp = pathlib.Path(tempfile.mkdtemp())
config.SETTINGS_PATH = tmp / "settings.json"; config.ENV_PATH = tmp / ".env"

calls = {"flaky": 0, "log": []}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer sk-test": return self._send(401, {"error": "bad key"})
        self._send(200, {"object": "list", "data": [{"id": i} for i in ("gone", "tiny", "flaky", "eye-vision", "whisper-large-v3", "thinker", "picky")]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        m = body["model"]; calls["log"].append((m, sorted(k for k in body if k not in ("model", "messages"))))
        if m == "gone": return self._send(404, {"error": {"message": "The model `gone` does not exist", "code": "model_not_found"}})
        if m == "tiny": return self._send(413, {"error": {"message": "Request too large for model `tiny` on tokens per minute (TPM): Limit 6000, Requested 9000"}})
        if m == "flaky":
            calls["flaky"] += 1
            if calls["flaky"] == 1: return self._send(429, {"error": {"message": "Rate limit reached. Please try again in 1.2s."}}, {"retry-after": "1"})
            if "response_format" in body: return self._send(400, {"error": {"message": "response_format not supported"}})
            return self._send(200, {"choices": [{"message": {"content": "<think>hmm</think>{\"ok\": true, \"tex\": \"\\\\frac{1}{2}\"}"}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 11, "completion_tokens": 7}},
                              {"x-ratelimit-remaining-tokens": "5000", "x-ratelimit-reset-tokens": "2.5s", "x-ratelimit-limit-tokens": "8000"})
        if m == "eye-vision":
            parts = body["messages"][1]["content"]
            assert isinstance(parts, list) and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            return self._send(200, {"choices": [{"message": {"content": "<transcript>\nx $a\\le b$\n</transcript>"}, "finish_reason": "stop"}], "usage": {}})
        if m == "picky":
            if "reasoning_effort" in body: return self._send(400, {"error": {"message": "unknown parameter reasoning_effort"}})
            if body["max_tokens"] > 8192: return self._send(400, {"error": {"message": "max_tokens must be less than or equal to 8192"}})
            return self._send(200, {"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}], "usage": {}})
        if m == "strong":
            if self.headers.get("Authorization") == "Bearer sk-test":
                return self._send(429, {"error": {"message": "Quota exceeded for requests per day. Please try again in 5h0m0s."}})
            if calls.get("b") == "busy": return self._send(503, {"error": {"message": "The model is overloaded. Please try again later."}})
            return self._send(200, {"choices": [{"message": {"content": "from the second key"}, "finish_reason": "stop"}], "usage": {}})
        if m == "next":
            return self._send(200, {"choices": [{"message": {"content": "from the next model"}, "finish_reason": "stop"}], "usage": {}})
        if m == "weak":
            return self._send(200, {"choices": [{"message": {"content": "from the weaker model"}, "finish_reason": "stop"}], "usage": {}})
        if m == "thinker":
            if body["max_tokens"] < 4000: return self._send(200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}})
            return self._send(200, {"choices": [{"message": {"content": "part one"}, "finish_reason": "length" if "cut off" not in str(body["messages"]) else "stop"}], "usage": {}})
        self._send(500, {})

srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["CUSTOM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"; os.environ["CUSTOM_API_KEY"] = "sk-test"
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "OLLAMA_BASE_URL"): os.environ.pop(k, None)
from scriptavera.llm import router, RunHooks, LLMError
from scriptavera.util import loads_lenient, extract_tag

st = router.discover(force=True)
ids = sorted(m.id for m in router.models)
assert ids == ["eye-vision", "flaky", "gone", "picky", "thinker", "tiny"], ids      # whisper filtered out
assert st["custom"]["ok"] and st["custom"]["count"] == 6
for m in router.models: m.interval = 0.0
config.save_settings({"routing": {"fast": ["custom:gone", "custom:tiny", "custom:flaky"], "writer": ["custom:thinker"]}})

waits = []; recs = []
class Hk(RunHooks):
    def on_wait(self, msg): waits.append(msg)
    def on_call(self, r): recs.append(r)
t0 = time.time()
res = router.chat("fast", "Return JSON.", "hello", json_mode=True, hooks=Hk(), label="t1")
d = loads_lenient(res.text)
assert res.model == "flaky" and d == {"ok": True, "tex": "\\frac{1}{2}"}, (res, d)
assert any("rate-limited" in w for w in waits), waits
gone = next(m for m in router.models if m.id == "gone"); tiny = next(m for m in router.models if m.id == "tiny")
assert gone.dead and tiny.req_tokens == 5400, (gone.dead, tiny.req_tokens)
flaky = next(m for m in router.models if m.id == "flaky")
assert flaky.tok_remaining == 5000 and flaky.req_tokens == 7200, (flaky.tok_remaining, flaky.req_tokens)
assert [r["ok"] for r in recs] == [False, False, True], recs
print("fallback chain ok in %.1fs:" % (time.time() - t0), [(r["model"], r["ok"], r.get("note", "")) for r in recs])

res = router.chat("vision_best", "sys", "read", images=[b"\xff\xd8fakejpeg"], hooks=Hk(), label="t2")
assert res.model == "eye-vision" and extract_tag(res.text, "transcript") == "x $a\\le b$"
res = router.chat("writer", "sys", "write", max_out=1000, hooks=Hk(), label="t3")
assert res.text.startswith("part one") and res.text.count("part one") == 2 and not res.truncated, res
print("vision payload, empty-answer recovery and continuation ok")

# a model that rejects reasoning_effort and has a low output ceiling: learned once, then remembered
from scriptavera.llm import classify, Router
picky = next(m for m in router.models if m.id == "picky"); picky.effort, picky.think_pad = True, 12000
config.save_settings({"routing": {"reason": ["custom:picky"]}})
n0 = len(calls["log"])
res = router.chat("reason", "s", "u", max_out=3000, effort="high", hooks=Hk(), label="t4"); assert res.text == "fine"
first = len(calls["log"]) - n0
res = router.chat("reason", "s", "u", max_out=3000, effort="high", hooks=Hk(), label="t5")
assert first == 3 and len(calls["log"]) - n0 == 4 and picky.max_total == 8192 and picky.effort is False, (first, len(calls["log"]) - n0, picky.max_total)
# reserved reasoning room per effort, and its ceiling
g = classify("gemini", "gemini-3.8-flash"); q = classify("groq", "qwen/qwen3.8-27b"); o = classify("groq", "openai/gpt-oss-120b")
assert [Router._reasoning_room(g, e) for e in ("low", "medium", "high", None)] == [4000, 12000, 28000, 12000]
assert min(g.out_cap + 28000, g.max_total) == 44000 and q.effort and q.think_pad == 700 and o.think_pad == 700
assert q.req_tokens >= 2100 + 1200 + q.out_cap + q.think_pad, "a page image, the prompt, the answer and the reasoning fit one Groq request"
print("reasoning limits ok")

# a daily limit: the model rests until the time the provider gives, and when every model is resting the
# error says so and carries the restart time
import time as _t
from scriptavera.llm import _retry_after
class _R:  # minimal response
    headers = {}; text = '{"error":{"message":"Rate limit reached for model on tokens per day (TPD): Limit 200000. Please try again in 7m12.5s."}}'
assert abs(_retry_after(_R) - 432.5) < 0.01
class _G: headers = {}; text = '[{"error":{"code":429,"details":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"},{"retryDelay":"34s"}]}}]'
assert _retry_after(_G) == 34.0
class _N: headers = {}; text = "slow down"
assert _retry_after(_N) is None
for m in router.models: m.cooldown_until, m.note = _t.time() + 600 + (0 if m.id == "flaky" else 300), "daily limit reached"
try:
    router.chat("fast", "s", "u", hooks=Hk()); raise SystemExit("should pause")
except LLMError as e:
    assert e.kind == "limits" and 595 < e.retry_at - _t.time() < 615 and "flaky" in str(e) and "free again at" in str(e), (e.kind, e.retry_at - _t.time(), str(e))
    print("limits error:", str(e)[:110])
for m in router.models: m.cooldown_until = 0

# ---- quality-critical jobs: only the strongest model, on one key after the other; never a lesser model by itself
from scriptavera.llm import Model, ROLES
keep = router.models
def mk(mid, score, key="sk-test", ki=0):
    m = Model(provider="custom", id=mid, key=key, key_index=ki, req_tokens=24000, out_cap=6000, interval=0.0)
    m.scores = {r: score for r in ROLES if not r.startswith("vision")}
    return m
router.models = [mk("strong", 90), mk("strong", 90, "sk-two", 1), mk("next", 88), mk("weak", 70)]
config.save_settings({"routing": {}})
assert ROLES["writer"]["strict"] and not ROLES["fast"]["strict"]
assert [m.ref for m in router.peers("writer")] == ["custom:strong", "custom:strong#2"] and router.best("writer").ref == "custom:strong"
plan = router.plan()
assert plan["writer"]["chain"] == ["custom:strong", "custom:strong#2"] and plan["writer"]["weaker"] == ["custom:next", "custom:weak"], plan["writer"]
assert plan["fast"]["chain"] == ["custom:strong", "custom:strong#2", "custom:next", "custom:weak"] and plan["fast"]["weaker"] == []
class Quick(Hk):
    def sleep(self, seconds): pass                 # the waits between retries are not what is tested here
recs.clear()
res = router.chat("writer", "s", "u", hooks=Quick(), label="strict-1")
s1, s2, nx, w = router.models
assert res.model == "strong" and res.text == "from the second key", res        # the first key is out for the day: the second takes over
assert s1.note == "daily limit reached" and 3 * 3600 < s1.cooldown_until - time.time() < 6 * 3600, (s1.note, s1.cooldown_until - time.time())
calls["b"] = "busy"; n0 = len(calls["log"])
try:
    router.chat("writer", "s", "u", hooks=Quick(), label="strict-2"); raise SystemExit("a lesser model must not answer a strict job")
except LLMError as e:
    assert e.kind == "limits" and not e.weaker and "never used" in str(e) and "strong (overloaded at the provider" in str(e), str(e)
    assert 250 < e.retry_at - time.time() < 320, e.retry_at - time.time()               # the first of its keys to come back
tried = [m for m, _ in calls["log"][n0:]]
assert tried == ["strong"] * 4 and "next" not in tried, tried                           # patient with the second key, never the next model
assert router.candidates("writer") == [] and [m.id for m in router.candidates("writer", strict=False)] == ["next"]   # never the weak one
assert router.chat("writer", "s", "u", hooks=Quick(), label="allowed", strict=False).model == "next"    # a call that is not strict
assert router.chat("fast", "s", "u", hooks=Quick(), label="small job").model == "next"                  # small jobs: never to a weaker one
config.save_settings({"routing": {"writer": ["custom:weak"]}})                         # a model the author pins comes first
assert [m.id for m in router.peers("writer")] == ["weak", "strong", "strong"] and router.chat("writer", "s", "u", hooks=Quick()).model == "weak"
config.save_settings({"routing": {}})
for m in router.models: m.cooldown_until = 0
calls["b"] = "ok"
print("strict roles: the same model on the next key, a lesser model refused, wait time reported")
router.models = keep

os.environ["CUSTOM_API_KEY"] = "sk-wrong"
st = router.discover(force=True)
assert not st["custom"]["ok"] and "rejected" in st["custom"]["message"], st
try:
    router.chat("fast", "s", "u", hooks=Hk()); raise SystemExit("should fail")
except LLMError as e:
    print("no-model message:", str(e)[:90])
print("router ok")
