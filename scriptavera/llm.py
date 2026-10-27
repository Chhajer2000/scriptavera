# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Model router.

Every AI call in the pipeline names a *role* (what kind of work it is), not a
model. The router discovers which models each configured key can reach, ranks
them per role, paces requests to respect free-tier limits, and falls back to
the next model when one is rate-limited, too small for the request, or gone.

All providers are reached through the OpenAI-compatible chat-completions API,
so adding one is a matter of a base URL and a key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from . import config, tenancy
from .util import est_tokens, strip_think

# ------------------------------------------------------------------ roles

# `strict` roles decide the quality of the paper. For them only the strongest model these keys can use, and models
# almost as good as it (`margin` points below it on this job: for Gemini the same tier and a version at most 0.25
# lower), are used: the best one first, on one key after the other, then the next. A weaker model is never used, not
# even with the author's permission; if none of these is free, the work waits.
ROLES: dict[str, dict] = {
    "vision_fast": {
        "name": "Scout reader", "strict": False,
        "job": "First look at a few pages. Reads words only and skips equations to find the topic.",
        "ideal": "Any fast multimodal model.",
    },
    "vision_best": {
        "name": "Handwriting transcriber", "strict": True, "margin": 1.0,
        "job": "Reads every page twice (notation survey, then the final transcription) and redraws the figures.",
        "ideal": "The strongest multimodal model available. Paid upgrade: Gemini Pro, Claude Opus or GPT-5 class.",
    },
    "fast": {
        "name": "Extractor", "strict": False,
        "job": "Small structured tasks: topic profile, search queries, relevance scoring.",
        "ideal": "A fast, cheap text model. Groq-hosted open models are ideal.",
    },
    "reason": {
        "name": "Mathematical reasoner", "strict": True, "margin": 1.0,
        "job": "Resolves notation, understands the whole paper, decides its structure, looks for shorter arguments, "
               "checks every proof and reads the paper as a referee.",
        "ideal": "The strongest reasoning model available. Paid upgrade: Claude Opus, GPT-5 thinking or Gemini Pro.",
    },
    "reader": {
        "name": "Paper reader", "strict": True, "margin": 1.0,
        "job": "Reads each relevant paper in full and takes structured notes.",
        "ideal": "A long-context model, so a whole paper fits in one request.",
    },
    "writer": {
        "name": "Academic writer", "strict": True, "margin": 1.0,
        "job": "Writes the introduction and every section, does the final copy-edit, writes the abstract and proposes titles.",
        "ideal": "A model with strong prose and exact LaTeX. Paid upgrade: Claude Opus or Sonnet.",
    },
}

# ------------------------------------------------------------- providers

# Gemini models Google sells on its paid tier (pricing page, 21 October 2026). Only these are used in Private mode:
# a model without a paid price may be a free-tier-only model, whose inputs Google may use to improve its products.
PAID_GEMINI = re.compile(r"^gemini-(4-argon|3\.8-flash|3\.6-flash|3\.5-flash-lite|3\.1-flash-lite|3\.1-pro(-preview)?|3-flash-preview|"
                         r"2\.5-pro|2\.5-flash|2\.5-flash-lite)$")


def private_ok(provider: str, model_id: str, settings: dict | None = None) -> bool:
    s = settings if settings is not None else config.load_settings()
    if not s.get("private"):
        return True
    if f"{provider}:{model_id}" in (s.get("private_models") or []) or provider == "ollama":
        return True
    return provider == "gemini" and bool(PAID_GEMINI.match(model_id.lower()))


PROVIDERS: dict[str, dict] = {
    "gemini": {
        "label": "Google Gemini", "env": "GEMINI_API_KEY",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "signup": "https://aistudio.google.com/apikey",
        "why": "Best free handwriting reader and the only free long-context model. Strongly recommended.",
        "fallback": ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                     "gemini-2.5-flash", "gemini-2.5-flash-lite"],
    },
    "groq": {
        "label": "Groq", "env": "GROQ_API_KEY",
        "base": "https://api.groq.com/openai/v1",
        "signup": "https://console.groq.com/keys",
        "why": "Very fast open models for the many small tasks. Free tier allows about 8,000 tokens per minute per model.",
        "fallback": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
                     "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "minimaxai/minimax-m2.7"],
    },
    "cerebras": {
        "label": "Cerebras", "env": "CEREBRAS_API_KEY",
        "base": "https://api.cerebras.ai/v1",
        "signup": "https://cloud.cerebras.ai/",
        "why": "Same open models as Groq with a larger per-minute allowance. Good second text provider.",
        "fallback": ["gpt-oss-120b", "qwen-3.8-27b"],
    },
    "mistral": {
        "label": "Mistral", "env": "MISTRAL_API_KEY",
        "base": "https://api.mistral.ai/v1",
        "signup": "https://console.mistral.ai/api-keys",
        "why": "Free experiment tier with large monthly allowance. Good writer and a second vision model.",
        "fallback": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    },
    "openrouter": {
        "label": "OpenRouter", "env": "OPENROUTER_API_KEY",
        "base": "https://openrouter.ai/api/v1",
        "signup": "https://openrouter.ai/keys",
        "why": "One key for many free models. Only 50 free requests per day, so it is used as a last resort.",
        "fallback": [],
    },
    "ollama": {
        "label": "Ollama (local)", "env": "OLLAMA_BASE_URL", "keyless": True,
        "base": "", "signup": "https://ollama.com/",
        "why": "Models running on your own computer. No limits, lower accuracy. Used only as a fallback.",
        "fallback": [],
    },
    "custom": {
        "label": "Custom endpoint", "env": "CUSTOM_API_KEY",
        "base": "", "signup": "",
        "why": "Any other OpenAI-compatible endpoint (set URL, key and model names).",
        "fallback": [],
    },
}

# Providers a person may bring their own keys for on scriptavera.com (never Ollama or a custom address: the server
# would then fetch any address someone typed).
BYOK = ("gemini", "groq", "cerebras", "mistral", "openrouter")

# What each provider does with what is sent to it, on its free and paid plans (as its terms say, October 2026).
PRIVACY = {
    "gemini": {"free": "On Google's free tier, what you send may be used to improve Google's products and read by human reviewers. Your unpublished notes can leak.",
               "paid": "Paid tier: Google does not use your prompts to improve its products; it keeps them for a limited time to detect abuse.",
               "": "Could not tell whether this key is on the free or the paid tier."},
    "mistral": {"free": "On Mistral's free plan, what you send may be used to train its models unless you opt out in Mistral's admin panel.",
                "paid": "Paid plan: you control whether Mistral may use your data; check that training is turned off in its admin panel.",
                "": "Mistral does not say through its API which plan a key is on. On the free plan, data may be used for training unless you opt out in its admin panel."},
    "openrouter": {"free": "Without credits, only free models are used, and some of the services behind them may log or train on prompts.",
                   "paid": "With credits. Turn off the providers that train on prompts in OpenRouter's privacy settings.",
                   "": "Could not tell whether this key has credits."},
    "groq": {"free": "Groq does not train on what you send, on any plan.", "paid": "Groq does not train on what you send, on any plan.",
             "": "Groq does not train on what you send, on any plan."},
    "cerebras": {"free": "Cerebras does not train on what you send, on any plan.", "paid": "Cerebras does not train on what you send, on any plan.",
                 "": "Cerebras does not train on what you send, on any plan."},
}


def leaks(provider: str, tier: str) -> bool:
    """Whether a key of this provider and tier may let the provider train on, or people read, what is sent."""
    return (provider == "gemini" and tier == "free") or (provider == "mistral" and tier != "paid") \
        or (provider == "openrouter" and tier == "free")


# Getting a key, in a few steps, as each provider's site shows it.
KEY_STEPS = {
    "gemini": {"link": "https://aistudio.google.com/apikey", "steps": [
        "Open Google AI Studio and sign in with your Google account.",
        "Click “Create API key” and pick (or create) a Google Cloud project.",
        "Copy the key (a long code, usually starting with “AIza”) and paste it here.",
        "For privacy, turn on billing for that project (AI Studio, “Set up billing”): the key then becomes a paid-tier key, and Google stops using what you send to improve its products."]},
    "groq": {"link": "https://console.groq.com/keys", "steps": [
        "Open the Groq console and sign in.",
        "Click “Create API Key” and give it a name, such as Scriptavera.",
        "Copy the key at once (it starts with “gsk_” and is shown only once) and paste it here."]},
    "cerebras": {"link": "https://cloud.cerebras.ai/", "steps": [
        "Open Cerebras Cloud and sign in.",
        "Choose “API Keys” in the menu on the left.",
        "Click “Generate API Key”, copy it (it starts with “csk-”) and paste it here."]},
    "mistral": {"link": "https://console.mistral.ai/api-keys", "steps": [
        "Open the Mistral console and sign in.",
        "Under Billing, choose a plan (the free one is enough to try).",
        "Under API Keys, click “Create new key”, copy it (it is shown only once) and paste it here.",
        "In Mistral's admin panel, turn off the use of your data for training."]},
    "openrouter": {"link": "https://openrouter.ai/keys", "steps": [
        "Open OpenRouter and sign in.",
        "Click “Create Key”, copy it (it starts with “sk-or-”) and paste it here.",
        "Buy a few dollars of credits to use paid models, and in Settings, Privacy, turn off providers that train on prompts."]},
}

GEMINI_FLAGSHIPS = {"argon", "ultra"}        # Gemini models named above Pro (gemini-4-argon is Google's flagship)

_NOT_CHAT = re.compile(
    r"whisper|tts|embed|guard|orpheus|imagen|veo|lyria|-live|transcribe|robotics|rerank|moderation|"
    r"\bocr\b|-ocr|aqa|computer-use|deep-research|antigravity|native-audio|-image|omni|nano-banana|"
    r"speech|voxtral|codestral|devstral|audio|sora|dall-e|realtime|customtools", re.I)

_VISION_HINT = re.compile(
    r"vision|llava|pixtral|-vl\b|vl-|qwen-?3\.\d|qwen2\.5-vl|gemma-?3|llama-4|gpt-4o|gpt-4\.1|gpt-5|"
    r"gemini|minicpm|molmo|internvl|mistral-(medium|small)|claude", re.I)


class LLMError(RuntimeError):
    """kind 'limits', 'network' or 'transient' marks a failure that will pass by itself; retry_at says when to
    try again. `weaker` names a lesser model that could do the job at once, if the author allows it."""

    def __init__(self, message: str, kind: str = "", retry_at: float = 0.0):
        super().__init__(message)
        self.kind = kind
        self.retry_at = retry_at
        self.weaker = ""


def next_pacific_midnight(now: float | None = None) -> float:
    """Gemini's daily allowances reset at midnight Pacific time. Computed without a time-zone database
    (Windows has none): US daylight time runs from the second Sunday of March to the first of November."""
    import calendar
    import datetime
    now = now or time.time()
    year = time.gmtime(now).tm_year

    def sunday(month: int, nth: int) -> int:
        first = (6 - datetime.date(year, month, 1).weekday()) % 7 + 1
        return first + 7 * (nth - 1)

    dst_start = calendar.timegm((year, 3, sunday(3, 2), 10, 0, 0))
    dst_end = calendar.timegm((year, 11, sunday(11, 1), 9, 0, 0))
    offset = -7 * 3600 if dst_start <= now < dst_end else -8 * 3600
    local = now + offset
    return (local // 86400 + 1) * 86400 - offset + 120


def clock(t: float) -> str:
    """A time of day as the author reads it: 19:25, or 12:35 tomorrow."""
    then, today = time.localtime(t), time.localtime()
    days = (time.mktime((then.tm_year, then.tm_mon, then.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    return time.strftime("%H:%M", then) + (" tomorrow" if 0.5 < days < 1.5 else time.strftime(" on %A", then) if days >= 1.5 else "")


class Cancelled(RuntimeError):
    pass


@dataclass
class Model:
    provider: str
    id: str
    key: str = ""
    key_index: int = 0
    vision: bool = False
    scores: dict = field(default_factory=dict)
    req_tokens: int = 16000      # safe input+output tokens for one request
    out_cap: int = 4000          # most output tokens we will request
    think_pad: int = 0           # extra output tokens reserved for hidden reasoning
    max_total: int = 60000       # ceiling for max_tokens (visible answer plus reasoning)
    interval: float = 2.0        # seconds between requests
    effort: bool = False         # accepts reasoning_effort
    tier: int = -1               # Gemini: 0 Flash-Lite, 1 Flash, 2 Pro, 3 flagship (Pro and above: paid tier only)
    # runtime state
    cooldown_until: float = 0.0
    dead: bool = False
    last_call: float = 0.0
    tok_remaining: int | None = None
    tok_reset_at: float = 0.0
    strikes: int = 0
    note: str = ""

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.id}" + (f"#{self.key_index + 1}" if self.key_index else "")

    def available(self) -> bool:
        return not self.dead and time.time() >= self.cooldown_until

    def public(self) -> dict:
        return {"ref": self.ref, "provider": self.provider, "id": self.id, "vision": self.vision,
                "req_tokens": self.req_tokens, "dead": self.dead, "note": self.note,
                "cooldown": max(0, int(self.cooldown_until - time.time())),
                "scores": {k: round(v, 1) for k, v in self.scores.items()}}


def _s(**kw) -> dict:
    base = {r: 0.0 for r in ROLES}
    base.update(kw)
    return base


def classify(provider: str, model_id: str, meta: dict | None = None) -> Model | None:
    """Decide what a discovered model is good for. Returns None for non-chat models."""
    meta = meta or {}
    mid = model_id.split("/", 1)[1] if model_id.startswith("models/") else model_id
    low = mid.lower()
    if _NOT_CHAT.search(low):
        return None
    m = Model(provider=provider, id=mid)

    if provider == "gemini":
        g = re.match(r"^gemini-(\d+(?:\.\d+)?)-([a-z]+)(-lite)?(?:-preview.*|-\d{2,}.*|-exp.*)?$", low)
        if not g:
            if low.startswith("gemma-3"):
                m.vision, m.req_tokens, m.out_cap, m.interval = True, 12000, 4000, 2.5
                m.scores = _s(vision_fast=50, vision_best=45, fast=55, reason=40, reader=35, writer=45)
                return m
            return None
        v, name, lite = float(g.group(1)), g.group(2), bool(g.group(3))
        # Ranked by tier first, then by version: the flagship (Gemini 4 Argon) above Pro, Pro above Flash, Flash above
        # Flash-Lite, and within a tier the newer version first. Pro and Argon are not on Google's free tier: on a
        # free key the first request is refused with "limit: 0" and the model is skipped on that key from then on.
        tier = 3 if name in GEMINI_FLAGSHIPS else 2 if name == "pro" else 0 if lite else 1
        m.tier = tier
        q = (66, 81, 96, 108)[tier] + 4 * v
        m.vision, m.effort = True, True
        m.req_tokens, m.out_cap, m.think_pad = 200_000, 16000, 8000
        m.interval = (4.5, 6.5, 13.0, 13.0)[tier]
        m.scores = _s(vision_best=q, reason=q, reader=q, writer=q,
                      vision_fast=(96 + v, 90 + v, 70.0, 66.0)[tier], fast=(88 + v, 82 + v, 62.0, 58.0)[tier])
        return m

    if provider in ("groq", "cerebras"):
        big = provider == "cerebras"
        m.req_tokens, m.out_cap = (26000, 6000) if big else (7000, 3000)
        m.interval = 12.5 if big else 2.2
        bonus = 2 if big else 0
        if re.search(r"gpt-oss|qwen|minimax", low):
            # these models reason before answering; the reasoning is billed as output and counts toward the
            # per-minute allowance, so a fixed slice is reserved for it and effort is kept low on Groq
            m.effort, m.think_pad = True, (1500 if big else 700)
        if "gpt-oss-120b" in low:
            m.effort = True
            m.scores = _s(fast=94, reason=84 + bonus, reader=62 + 6 * bonus, writer=82 + bonus)
        elif "gpt-oss-20b" in low:
            m.effort = True
            m.scores = _s(fast=97, reason=62, reader=45, writer=60)
        elif "qwen" in low:
            m.vision = True
            m.scores = _s(vision_fast=84 + bonus, vision_best=78 + bonus, fast=78, reason=74, reader=55, writer=70)
        elif "llama-3.3-70b" in low:
            m.scores = _s(fast=96, reason=66, reader=50, writer=76)
        elif "llama-3.1-8b" in low:
            m.req_tokens = 5500
            m.scores = _s(fast=70, reason=30, reader=25, writer=35)
        elif "llama-4" in low:
            m.vision = True
            m.scores = _s(vision_fast=76, vision_best=68, fast=74, reason=60, reader=50, writer=64)
        elif "minimax" in low:
            m.scores = _s(fast=70, reason=78, reader=60, writer=78)
        elif "kimi" in low:
            m.scores = _s(fast=72, reason=76, reader=58, writer=78)
        elif "deepseek" in low:
            m.scores = _s(fast=60, reason=78, reader=55, writer=68)
        elif "compound" in low:
            return None
        else:
            m.vision = bool(_VISION_HINT.search(low)) and "gpt-oss" not in low
            m.scores = _s(fast=55, reason=50, reader=40, writer=50, vision_fast=45 if m.vision else 0,
                          vision_best=40 if m.vision else 0)
        return m

    if provider == "mistral":
        caps = meta.get("capabilities") or {}
        if caps and not caps.get("completion_chat", True):
            return None
        m.req_tokens, m.out_cap, m.interval = 60000, 8000, 1.5
        m.vision = bool(caps.get("vision")) or bool(re.search(r"pixtral|medium|small", low))
        if "large" in low:
            m.scores = _s(fast=60, reason=78, reader=76, writer=84)
            if m.vision:
                m.scores.update(vision_best=74, vision_fast=60)
        elif "medium" in low or "pixtral-large" in low:
            m.scores = _s(vision_best=76, vision_fast=70, fast=66, reason=74, reader=72, writer=78)
        elif "magistral" in low:
            m.scores = _s(fast=40, reason=80, reader=60, writer=70)
        elif "small" in low or "pixtral" in low:
            m.scores = _s(vision_best=62, vision_fast=72, fast=76, reason=58, reader=60, writer=62)
        else:
            return None
        return m

    if provider == "openrouter":
        pricing = meta.get("pricing") or {}
        if not (low.endswith(":free") or str(pricing.get("prompt", "1")) in ("0", "0.0")):
            return None
        mods = (meta.get("architecture") or {}).get("input_modalities") or []
        m.vision = "image" in mods or (not mods and bool(_VISION_HINT.search(low)))
        m.req_tokens, m.out_cap, m.interval = 20000, 6000, 3.5
        q = 50
        for pat, val in (("gemini", 74), ("deepseek-r1", 72), ("deepseek", 70), ("gpt-oss-120b", 72), ("qwen3", 68),
                         ("llama-3.3-70b", 66), ("llama-4", 66), ("mistral", 62), ("gemma-3-27b", 58), ("kimi", 68)):
            if pat in low:
                q = val
                break
        m.scores = _s(fast=q - 6, reason=q, reader=q - 4, writer=q,
                      vision_best=(q - 4) if m.vision else 0, vision_fast=(q - 6) if m.vision else 0)
        return m

    # ollama / custom
    m.vision = bool(_VISION_HINT.search(low))
    if provider == "ollama":
        m.req_tokens, m.out_cap, m.interval = 6000, 2500, 0.0
        q = 30
    else:
        m.req_tokens, m.out_cap, m.interval = 24000, 6000, 1.5
        q = 60
    m.scores = _s(fast=q, reason=q, reader=q - 5, writer=q,
                  vision_best=q if m.vision else 0, vision_fast=q if m.vision else 0)
    return m


@dataclass
class Result:
    text: str
    model: str
    provider: str
    in_tokens: int = 0
    out_tokens: int = 0
    seconds: float = 0.0
    truncated: bool = False


class RunHooks:
    """What a caller can observe: each call, each wait, and a cancel switch."""

    def on_call(self, record: dict) -> None: ...
    def on_wait(self, message: str) -> None: ...
    def cancelled(self) -> bool:
        return False

    def sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.cancelled():
                raise Cancelled()
            time.sleep(min(0.25, max(0.0, end - time.time())))


def _retry_after(resp: requests.Response) -> float | None:
    h = resp.headers.get("retry-after")
    if h:
        try:
            return float(h)
        except ValueError:
            pass
    body = resp.text or ""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", body)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))
    m = re.search(r"retry(?:Delay)?[\"']?\s*[:=in]+\s*[\"']?([\d.]+)s", body, re.I)
    if m:
        return float(m.group(1))
    return None


def _duration(s: str | None) -> float:
    """Parse '7.66s', '1m2.5s', '120ms' from rate-limit headers."""
    if not s:
        return 0.0
    m = re.match(r"^(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?$", s.strip())
    if not m:
        try:
            return float(s)
        except ValueError:
            return 0.0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0) + float(m.group(4) or 0) / 1000


class Router:
    def __init__(self, keys: dict[str, list[str]] | None = None) -> None:
        """keys: a person's own keys ({"GEMINI_API_KEY": [...], ...}), held in memory only; None reads .env."""
        self.models: list[Model] = []
        self.status: dict[str, dict] = {}
        self.discovered_at = 0.0
        self._lock = threading.RLock()
        self._pace = threading.Lock()
        self._own = keys

    # -------------------------------------------------------- discovery

    def _base(self, provider: str) -> str:
        if self._own is not None and provider not in BYOK:
            return ""
        if provider == "ollama":
            url = config.get_env("OLLAMA_BASE_URL").rstrip("/")
            return (url if url.endswith("/v1") else url + "/v1") if url else ""
        if provider == "custom":
            return config.get_env("CUSTOM_BASE_URL").rstrip("/")
        return PROVIDERS[provider]["base"]

    def _keys(self, provider: str) -> list[str]:
        if self._own is not None:
            return list(self._own.get(PROVIDERS[provider]["env"]) or []) if provider in BYOK else []
        if provider == "ollama":
            return ["ollama"] if config.get_env("OLLAMA_BASE_URL") else []
        if provider == "custom":
            return (config.get_keys("CUSTOM_API_KEY") or ["none"]) if config.get_env("CUSTOM_BASE_URL") else []
        return config.get_keys(PROVIDERS[provider]["env"])

    def discover(self, force: bool = False) -> dict:
        """Ask each configured provider which models this key can use."""
        with self._lock:
            if not force and self.models and time.time() - self.discovered_at < 6 * 3600:
                return self.status
            if config.mock_mode():
                devmock = config.simulator()
                self.models = devmock.models()
                self.status = {"mock": {"ok": True, "label": "Simulated models", "count": len(self.models), "message": "Developer mode"}}
                self.discovered_at = time.time()
                return self.status
            models: list[Model] = []
            status: dict[str, dict] = {}
            jobs = [(pid, ki, key) for pid in PROVIDERS for ki, key in enumerate(self._keys(pid))]
            with ThreadPoolExecutor(max_workers=8) as ex:         # ask all providers at once
                listed = list(ex.map(lambda j: self._list_models(j[0], j[2]), jobs)) if jobs else []
            results = {(pid, ki): res for (pid, ki, _), res in zip(jobs, listed)}
            for pid, p in PROVIDERS.items():
                keys = self._keys(pid)
                st = {"label": p["label"], "configured": bool(keys), "ok": False, "count": 0, "message": "No key", "keys": []}
                status[pid] = st
                for ki, key in enumerate(keys):
                    found, msg = results[(pid, ki)]
                    if pid == "custom" and config.get_env("CUSTOM_MODELS"):
                        found = [(x.strip(), {}) for x in config.get_env("CUSTOM_MODELS").split(",") if x.strip()]
                        msg = "Using CUSTOM_MODELS"
                    if not found and msg.startswith("unreachable") and p["fallback"]:
                        found = [(x, {}) for x in p["fallback"]]
                        msg = "Could not list models; using known defaults"
                    mine = [m for m in (classify(pid, mid, meta) for mid, meta in found) if m]
                    for mdl in mine:
                        mdl.key, mdl.key_index = key, ki
                    tier = self._tier(pid, key, mine) if mine else ""
                    if tier == "free":                    # Pro and Argon: not on this key; it goes straight to its best model
                        for mdl in mine:
                            if mdl.tier >= 2:
                                mdl.dead, mdl.note = True, "not on this key's free tier"
                    models.extend(mine)
                    n = sum(1 for m in mine if not m.dead)
                    st["count"] += n
                    st["ok"] = st["ok"] or n > 0
                    st["message"] = msg if not n else f"{st['count']} usable models"
                    st["keys"].append({"n": ki + 1, "ok": n > 0, "count": n, "tier": tier,
                                       "leak": n > 0 and leaks(pid, tier),
                                       "privacy": PRIVACY.get(pid, {}).get(tier, "") if n else "",
                                       "message": msg if not n else f"{n} usable models"})
                if st["ok"]:
                    st["message"] = f"{st['count']} usable models"          # a key that failed is reported under "keys"
            self.models = models
            self.status = status
            self.discovered_at = time.time()
            return status

    def _tier(self, provider: str, key: str, mine: list[Model]) -> str:
        """Whether a key is on its provider's free or paid plan: "free", "paid", or "" when it cannot be told."""
        try:
            if provider == "gemini":
                return self._probe_tier(key, mine)
            if provider == "openrouter":
                return self._probe_openrouter(key)
            if provider in ("groq", "cerebras"):
                return self._probe_limits(provider, key, mine)
        except Exception:  # noqa: BLE001 - not knowing the tier never stops a key from working
            return ""
        return ""

    @staticmethod
    def _probe_openrouter(key: str) -> str:
        """OpenRouter says it directly: a key whose account has never bought credits is on the free tier."""
        for path in ("/key", "/auth/key"):
            try:
                r = requests.get(PROVIDERS["openrouter"]["base"] + path, headers={"Authorization": f"Bearer {key}"}, timeout=12)
            except requests.RequestException:
                return ""
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or {}
                if "is_free_tier" in data:
                    return "free" if data["is_free_tier"] else "paid"
        return ""

    def _probe_limits(self, provider: str, key: str, mine: list[Model]) -> str:
        """Groq and Cerebras: the limits they report with an answer. A free plan allows a few thousand tokens a
        minute per model; a paid plan allows many times more. One request of one token to the smallest model."""
        small = sorted(mine, key=lambda m: (0 if re.search(r"8b|20b|instant|mini|small", m.id) else 1, m.id))
        if not small:
            return ""
        body = {"model": small[0].id, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}
        try:
            r = requests.post(self._base(provider) + "/chat/completions", data=json.dumps(body), timeout=30,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except requests.RequestException:
            return ""
        h = {k.lower(): v for k, v in r.headers.items()}
        try:
            tpm = float(h.get("x-ratelimit-limit-tokens") or h.get("x-ratelimit-limit-tokens-minute") or 0)
        except ValueError:
            return ""
        if not tpm:
            return ""
        return "paid" if tpm >= 200_000 else "free" if tpm <= 100_000 else ""

    def _probe_tier(self, key: str, mine: list[Model]) -> str:
        """Whether a Gemini key is on Google's free tier, found before any work is given to it: one tiny request to the
        best model that only the paid tier serves. "free" when Google answers that its free-tier limit is 0, "paid"
        when it answers (or names a limit above 0), "" when it cannot be told; then the first real request tells."""
        paid_only = sorted((m for m in mine if m.tier >= 2), key=lambda m: -m.scores.get("reason", 0))
        if not paid_only:
            return ""
        body = {"model": paid_only[0].id, "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 64}
        try:
            r = requests.post(self._base("gemini") + "/chat/completions", data=json.dumps(body), timeout=40,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except requests.RequestException:
            return ""
        low = (r.text or "").lower()
        if r.status_code == 200:
            return "paid"
        if r.status_code == 429:
            return "free" if ("limit: 0" in low or 'limit": 0' in low) else "paid"
        if r.status_code in (400, 403) and re.search(r"free[ _-]?tier|billing|not available", low):
            return "free"
        return ""

    def _list_models(self, provider: str, key: str) -> tuple[list[tuple[str, dict]], str]:
        base = self._base(provider)
        if not base:
            return [], "No URL configured"
        try:
            r = requests.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=12)
        except requests.RequestException as e:
            return [], f"unreachable: {type(e).__name__}"
        if r.status_code in (401, 403):
            return [], "Key rejected (check that it is correct and active)"
        if r.status_code != 200:
            return [], f"unreachable: HTTP {r.status_code}"
        try:
            data = r.json()
            items = data.get("data") if isinstance(data, dict) else data
            return [(it["id"], it) for it in items or [] if isinstance(it, dict) and it.get("id")], "ok"
        except Exception:
            return [], "unreachable: unexpected response"

    # ----------------------------------------------------------- routing

    def _pinned(self, role: str) -> list[str]:
        return config.load_settings().get("routing", {}).get(role) or []

    def _rank(self, role: str):
        pinned = self._pinned(role)

        def rank(m: Model) -> float:
            ref = f"{m.provider}:{m.id}"
            return m.scores.get(role, 0) + (1000 - pinned.index(ref) if ref in pinned else 0)
        return rank

    def _pool(self, role: str, vision: bool) -> list[Model]:
        """Every model these keys reach that could do the job at all (in Private mode, only the paid or local ones)."""
        s = config.load_settings()
        return [m for m in self.models if m.scores.get(role, 0) > 0 and (m.vision or not vision) and not m.dead
                and private_ok(m.provider, m.id, s)]

    def peers(self, role: str, vision: bool = False) -> list[Model]:
        """The top class for a job: the strongest model these keys reach and every model equivalent to it
        (the same model on another key, or one within the role's margin of it). Models the author pinned in
        Settings always belong. A model that is merely busy still belongs; one that does not work on this key
        does not."""
        self.discover()
        pool = self._pool(role, vision)
        if not pool:
            return []
        pinned = self._pinned(role)
        best = max(m.scores[role] for m in pool)
        margin = ROLES[role].get("margin", 4)
        top = [m for m in pool if m.scores[role] >= best - margin or f"{m.provider}:{m.id}" in pinned]
        return sorted(top, key=self._rank(role), reverse=True)

    def best(self, role: str, vision: bool = False) -> Model | None:
        top = self.peers(role, vision)
        return top[0] if top else None

    def candidates(self, role: str, need_tokens: int = 0, vision: bool = False, max_out: int = 0,
                   strict: bool | None = None) -> list[Model]:
        """Models to try now, best first. For a strict role only the top class is offered."""
        self.discover()
        strict = ROLES[role].get("strict", False) if strict is None else strict
        pool = self.peers(role, vision) if strict else sorted(self._pool(role, vision), key=self._rank(role), reverse=True)
        if not strict and pool:                 # a small job moves only to a model at least as capable, never a weaker one
            cap = "vision_best" if vision or role.startswith("vision") else "reason"
            floor = pool[0].scores.get(cap, 0) - 2
            pinned = self._pinned(role)
            pool = [m for m in pool if m.scores.get(cap, 0) >= floor or f"{m.provider}:{m.id}" in pinned]
        avail = [m for m in pool if m.available()]

        def fits_in(m: Model) -> bool:          # the same sum the request itself will be judged by
            return m.req_tokens >= need_tokens + min(max_out, m.out_cap) + (0 if m.provider == "gemini" else m.think_pad)

        fits = [m for m in avail if fits_in(m)]
        rest = sorted([m for m in avail if not fits_in(m)], key=lambda m: -m.req_tokens)
        return fits + rest

    def budget(self, role: str, vision: bool = False) -> int:
        """Tokens one request may use with the model that will do this job."""
        c = self.candidates(role, 0, vision) or self.peers(role, vision)
        return c[0].req_tokens if c else 6000

    def plan(self) -> dict:
        """For each job: the models of its top class, and the weaker ones that are used only if the author allows."""
        self.discover()
        out = {}
        for role, info in ROLES.items():
            vision = role.startswith("vision")
            top = self.peers(role, vision)
            others = [m for m in sorted(self._pool(role, vision), key=self._rank(role), reverse=True) if m not in top]
            chain = top + ([] if info.get("strict") else others)
            out[role] = {**info, "chain": [m.ref for m in chain[:6]], "primary": chain[0].ref if chain else None,
                         "weaker": [m.ref for m in others[:4]] if info.get("strict") else []}
        return out

    # -------------------------------------------------------------- call

    def chat(self, role: str, system: str, user: str, *, images: list[bytes] | None = None,
             json_mode: bool = False, max_out: int = 3000, temperature: float = 0.1,
             effort: str | None = None, label: str = "", hooks: RunHooks | None = None,
             allow_continue: bool = True, strict: bool | None = None, patience: int | None = None) -> Result:
        """One job for one role.

        strict: None follows the role. True uses only the role's top class of models and waits (by raising an
        LLMError of kind "limits" with the time to retry) when none of them is free. False lets weaker models help.
        patience: extra tries on a model that answers "overloaded" before the next one is asked."""
        hooks = hooks or RunHooks()
        images = images or []
        vision = bool(images)
        strict = ROLES[role].get("strict", False) if strict is None else strict
        patience = (2 if strict else 0) if patience is None else patience
        need = est_tokens(system) + est_tokens(user) + 2100 * len(images)
        cands = self.candidates(role, need, vision, max_out, strict)
        if not cands and not self.models:
            self.discover(force=True)
            cands = self.candidates(role, need, vision, max_out, strict)
        if not cands:
            if strict and self.peers(role, vision):
                raise self._busy_error(role, vision)
            if self._cooling(role, vision):
                raise self._limits_error(role, vision)
            what = "model that can read images" if images else "model"
            if config.load_settings().get("private"):
                raise LLMError(f"No paid-tier {what} is available for the '{ROLES[role]['name']}' role. Private mode uses only "
                               "Gemini models that Google sells on its paid tier (4 Argon, 3.8 Flash, 3.6 Flash, 3.1 Pro, 3.5 and 3.1 "
                               "Flash-Lite, 3 Flash Preview, 2.5) or a local Ollama model. Check that your Gemini key's project "
                               "has billing enabled, or add a model to private_models in settings.json.")
            raise LLMError(f"No {what} is available for the '{ROLES[role]['name']}' role. "
                           f"Add an API key in Settings (Gemini is recommended) or wait for rate limits to reset.")
        errors: list[str] = []
        tried: set[int] = set()
        for _round in range(4):
            for m in cands:
                if hooks.cancelled():
                    raise Cancelled()
                if id(m) in tried:
                    continue
                tried.add(id(m))
                try:
                    res = self._call_model(m, role, system, user, images, json_mode, max_out, temperature, effort, label, hooks,
                                           patience=patience if len(tried) == 1 else min(patience, 1))
                except _Skip as e:
                    errors.append(f"{m.ref}: {e}")
                    hooks.on_call({"role": role, "label": label, "provider": m.provider, "model": m.id, "ok": False, "note": str(e)})
                    continue
                if res.truncated and allow_continue and not json_mode:
                    res = self._continue(m, role, system, user, images, res, max_out, temperature, label, hooks)
                return res
            # every model asked has failed. If some turned out not to work on their key at all (Pro and Argon on a free
            # key), the strongest model these keys can use is now another one: ask it in this same call.
            if not any(m.dead for m in cands if id(m) in tried):
                break
            cands = [m for m in self.candidates(role, need, vision, max_out, strict) if id(m) not in tried]
            if not cands:
                break
        detail = " | ".join(errors[-4:])
        if errors and all("network error" in e for e in errors):
            raise LLMError(f"The AI providers could not be reached while working on '{label or role}'. "
                           f"Check the internet connection. ({detail})", kind="network", retry_at=time.time() + 120)
        if strict:
            if any(m.cooldown_until > time.time() for m in self.peers(role, vision)):
                raise self._busy_error(role, vision)
            raise LLMError(f"The strongest models failed on '{label or role}': {detail}", kind="transient",
                           retry_at=time.time() + 120)
        if self._cooling(role, vision) and not self.candidates(role, 0, vision, strict=False):
            raise self._limits_error(role, vision)          # every model is waiting for its limit to reset
        raise LLMError(f"Every model failed for '{label or role}': {detail}")

    def _cooling(self, role: str, vision: bool) -> list[Model]:
        now = time.time()
        return [m for m in self._pool(role, vision) if m.cooldown_until > now]

    @staticmethod
    def _when(m: Model) -> str:
        return f"{m.id} ({m.note or 'rate limited'}, free again at {clock(m.cooldown_until)})"

    def _limits_error(self, role: str, vision: bool) -> LLMError:
        cooling = sorted(self._cooling(role, vision), key=lambda m: m.cooldown_until)
        what = "read page images" if vision else f"do the '{ROLES[role]['name']}' job"
        return LLMError(f"Every model that can {what} has reached its free-tier limit: "
                        + "; ".join(self._when(m) for m in cooling[:4]) + ".",
                        kind="limits", retry_at=cooling[0].cooldown_until + 5)

    def _busy_error(self, role: str, vision: bool) -> LLMError:
        """The top class is resting. Wait for it; say which weaker model could step in if the author allows."""
        now = time.time()
        top = self.peers(role, vision)
        resting = sorted([m for m in top if m.cooldown_until > now], key=lambda m: m.cooldown_until)
        retry_at = (resting[0].cooldown_until + 5) if resting else now + 120
        names = "; ".join(self._when(m) for m in resting[:4]) or ", ".join(m.id for m in top[:4])
        err = LLMError(f"The strongest models for the '{ROLES[role]['name']}' job are not free right now: {names}. "
                       "A weaker model is never used for this job; the work continues by itself as soon as one of them is free.",
                       kind="limits", retry_at=retry_at)
        return err

    def _continue(self, m, role, system, user, images, res, max_out, temperature, label, hooks) -> Result:
        text = res.text
        for _ in range(2):
            follow = (user + "\n\n---\nYour previous answer was cut off. Here is what you wrote so far:\n\n" + text[-6000:]
                      + "\n\n---\nContinue EXACTLY from where it stops. Do not repeat anything already written. "
                        "Do not add commentary. Close any open tags when you finish.")
            try:
                nxt = self._call_model(m, role, system, follow, images, False, max_out, temperature, None, label + " (cont.)", hooks)
            except _Skip:
                break
            text = text.rstrip() + "\n" + nxt.text.lstrip()
            res.out_tokens += nxt.out_tokens
            res.seconds += nxt.seconds
            if not nxt.truncated:
                res.truncated = False
                break
        res.text = text
        return res

    @staticmethod
    def _reasoning_room(m: Model, effort: str | None) -> int:
        """Output tokens to reserve for hidden reasoning.

        Gemini counts thoughts inside the output limit. Its documented budgets for Gemini 2.5 are about 1,000
        (low), 8,000 (medium) and 24,600 (high) tokens; newer versions use levels without a published number, so
        the same sizes are used with headroom. Elsewhere the model's fixed reserve applies."""
        if m.provider == "gemini":
            return {"low": 4000, "medium": 12000, "high": 28000}.get(effort or "", 12000)
        return m.think_pad * (2 if effort == "high" else 1)

    def _wait_turn(self, m: Model, need: int, hooks: RunHooks) -> None:
        """Respect the per-model request interval and the provider's token window."""
        while True:
            with self._pace:
                now = time.time()
                wait = m.last_call + m.interval - now
                if m.tok_remaining is not None and need > m.tok_remaining and now < m.tok_reset_at:
                    wait = max(wait, m.tok_reset_at - now + 0.3)
                if wait <= 0:
                    m.last_call = now
                    return
            if wait > 75:
                raise _Skip(f"token window resets in {int(wait)}s")
            if wait > 4:
                hooks.on_wait(f"Pacing {m.id}: waiting {int(wait)}s for the free-tier limit")
            hooks.sleep(min(wait, 5.0))

    def _call_model(self, m: Model, role, system, user, images, json_mode, max_out, temperature, effort, label, hooks,
                    patience: int = 0) -> Result:
        if config.mock_mode():
            devmock = config.simulator()
            t0 = time.time()
            hooks.sleep(devmock.DELAY)
            text = devmock.respond(label, system, user, bool(images), m)
            rec = {"role": role, "label": label, "provider": m.provider, "model": m.id, "ok": True,
                   "in": est_tokens(system + user), "out": est_tokens(text), "seconds": round(time.time() - t0, 2)}
            hooks.on_call(rec)
            return Result(text, m.id, m.provider, rec["in"], rec["out"], rec["seconds"])

        content: list | str = user
        if images:
            content = [{"type": "text", "text": user}]
            for img in images:
                content.append({"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}})
        # Hidden reasoning is counted inside max_tokens, so room for it is added on top of the visible
        # answer, sized to the effort requested and capped by the model's output ceiling.
        eff = "low" if m.provider == "groq" else effort
        pad = self._reasoning_room(m, eff)
        out_tokens = min(min(max_out, m.out_cap) + pad, m.max_total)
        body: dict = {
            "model": m.id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": out_tokens,
        }
        optional: dict = {}
        if json_mode:
            optional["response_format"] = {"type": "json_object"}
        if eff and m.effort:
            optional["reasoning_effort"] = eff
        need = est_tokens(system) + est_tokens(user) + 2100 * len(images) + min(max_out, m.out_cap) \
            + (0 if m.provider == "gemini" else pad)
        url = self._base(m.provider) + "/chat/completions"
        headers = {"Authorization": f"Bearer {m.key}", "Content-Type": "application/json"}
        if m.provider == "openrouter":
            headers["X-Title"] = "Scriptavera"
        timeout = 240 if images or need > 20000 else 150
        if eff == "high" or max_out > 6000:
            timeout = 480                      # long reasoning before a long answer: several minutes is normal

        attempt = 0
        net_errors = 0
        limited = 0
        server_errors = 0
        r = None
        while attempt < 4 + patience:
            attempt += 1
            self._wait_turn(m, need, hooks)
            t0 = time.time()
            try:
                r = requests.post(url, headers=headers, data=json.dumps({**body, **optional}), timeout=timeout)
            except requests.RequestException as e:
                net_errors += 1
                if net_errors >= 2:
                    raise _Skip(f"network error ({type(e).__name__})")
                hooks.sleep(3)
                continue
            dt = time.time() - t0
            self._learn_headers(m, r)
            if r.status_code == 200:
                try:
                    data = r.json()
                    choice = data["choices"][0]
                    msg = choice.get("message") or {}
                    text = msg.get("content") or ""
                    if isinstance(text, list):
                        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                except Exception:
                    raise _Skip("unreadable response")
                text = strip_think(text)
                usage = data.get("usage") or {}
                finish = choice.get("finish_reason") or ""
                if not text.strip():
                    grown = min(max(pad * 2, 4000), 40000, m.max_total - min(max_out, m.out_cap))
                    if finish == "length" and grown > pad and grown + min(max_out, m.out_cap) <= m.req_tokens * 0.7:
                        pad = grown           # hidden reasoning used the whole allowance; give it more room
                        body["max_tokens"] = min(max_out, m.out_cap) + pad
                        continue
                    raise _Skip("empty answer")
                m.strikes = 0
                rec = {"role": role, "label": label, "provider": m.provider, "model": m.id, "ok": True,
                       "in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0),
                       "seconds": round(dt, 2)}
                # thinking is billed as output; Gemini's OpenAI-style usage counts it only in total_tokens
                details = usage.get("completion_tokens_details") or {}
                total = usage.get("total_tokens") or 0
                rec["think"] = int(details.get("reasoning_tokens") or (max(0, total - rec["in"] - rec["out"]) if total else 0))
                hooks.on_call(rec)
                return Result(text, m.id, m.provider, rec["in"], rec["out"], dt, truncated=(finish == "length"))

            err = (r.text or "")[:4000]
            low = err.lower()
            if r.status_code == 429:
                told = _retry_after(r)             # None when the provider does not say
                delay = told if told is not None else 20.0
                daily = bool(re.search(r"per ?day|perday|daily|\brpd\b|\btpd\b", err, re.I))
                zero = "limit: 0" in low or "limit\": 0" in low
                if zero:
                    m.dead, m.note = True, "not available on this key's free tier"
                    raise _Skip(m.note)
                if daily or delay > 90:
                    if not daily:
                        until = time.time() + delay
                    elif m.provider == "gemini":
                        until = next_pacific_midnight()             # Google resets daily allowances then
                    else:
                        until = time.time() + (told if told and told > 90 else 3600)   # rolling 24-hour windows
                    m.cooldown_until = until + 5
                    m.note = "daily limit reached" if daily else "rate limited"
                    raise _Skip(m.note)
                twin = next((o for o in self.models if o is not m and o.provider == m.provider and o.id == m.id
                             and o.key != m.key and o.available()), None)
                if twin:                              # the same model on the next key takes over at once
                    m.cooldown_until, m.note = time.time() + delay + 1, "rate limited"
                    raise _Skip(f"rate limited on key {m.key_index + 1}; key {twin.key_index + 1} takes over")
                limited += 1
                if limited >= 3:                      # keeps refusing: rest this model and move on
                    m.cooldown_until = time.time() + 900
                    m.note = "rate limited repeatedly"
                    raise _Skip(m.note)
                hooks.on_wait(f"{m.id} is rate-limited; retrying in {int(delay) + 1}s")
                hooks.sleep(delay + 0.5)
                continue
            if r.status_code == 413 or "too large" in low or "context length" in low or "maximum context" in low \
                    or "reduce the length" in low or "context_length_exceeded" in low:
                lim = re.search(r"limit:?\s*(\d{3,7})", low)
                learned = int(int(lim.group(1)) * 0.9) if lim else int(need * 0.7)
                if learned >= m.req_tokens:           # our estimate was too low: shrink anyway so the next request fits
                    learned = int(need * 0.85)
                m.req_tokens = max(1500, min(m.req_tokens, learned))
                raise _Skip(f"request too large for this model (limit now {m.req_tokens} tokens)")
            key_bad = r.status_code == 401 or bool(re.search(r"api key not valid|api_key_invalid|api key expired|unregistered callers|"
                                                         r"service_disabled|has not been used in project", low))
            if r.status_code in (400, 401, 403) and key_bad:
                for other in self.models:
                    if other.provider == m.provider and other.key == m.key:
                        other.dead, other.note = True, "key rejected"
                raise _Skip("API key rejected: " + err[:160])
            if r.status_code == 403:                  # this model only: not on this key's tier, region or project
                m.dead, m.note = True, "not available to this key"
                raise _Skip(f"{m.note}: {err[:160]}")
            if r.status_code == 400 and re.search(r"max_?(output_?|completion_?)?tokens", low) and body["max_tokens"] > 4096:
                cap = re.search(r"(?:at most|maximum(?: value)?(?: is| of)?|<=|less than or equal to|limit(?: is| of)?)\D{0,12}(\d{4,6})", low)
                m.max_total = min(m.max_total, int(cap.group(1)) if cap else body["max_tokens"] // 2)
                body["max_tokens"] = min(body["max_tokens"], m.max_total)     # this model's output ceiling is lower
                continue
            if r.status_code == 400 and "reasoning_effort" in optional:
                optional.pop("reasoning_effort")      # this model does not take the setting: remember, retry
                m.effort = False
                continue
            if r.status_code == 400 and optional:
                optional = {}     # the provider rejected an optional parameter; retry plain
                continue
            if r.status_code == 404 or "model_not_found" in low or "does not exist" in low or "decommissioned" in low:
                m.dead, m.note = True, "model no longer available"
                raise _Skip(m.note)
            if r.status_code >= 500 or r.status_code == 408:
                server_errors += 1
                busy = "overload" in low or "high demand" in low or r.status_code == 503
                if server_errors >= 2 + patience:     # overloaded or broken right now: rest it, do not keep every call waiting
                    m.cooldown_until = time.time() + 300
                    m.note = "overloaded at the provider" if busy else f"server error {r.status_code}"
                    raise _Skip(f"{m.note}: {err[:120]}")
                if server_errors >= 2:
                    pause = min(12.0 * (server_errors - 1), 30.0)
                    hooks.on_wait(f"{m.id} is overloaded; trying it again in {int(pause)}s before asking another model")
                    hooks.sleep(pause)
                else:
                    hooks.sleep(3.0)
                continue
            m.strikes += 1                    # an error we do not recognise: after two in a row, rest the model
            if m.strikes >= 2:
                m.cooldown_until, m.note = time.time() + 600, f"rested after HTTP {r.status_code}"
                m.strikes = 0
            raise _Skip(f"HTTP {r.status_code}: {err[:160]}")
        last = f"HTTP {r.status_code}: {(r.text or '')[:120]}" if r is not None else "no answer"
        m.cooldown_until, m.note = time.time() + 180, "kept failing"       # rest it briefly instead of asking again at once
        raise _Skip(f"gave up after repeated retries (last answer: {last})")

    @staticmethod
    def _learn_headers(m: Model, r: requests.Response) -> None:
        h = r.headers
        try:
            if "x-ratelimit-remaining-tokens" in h:
                m.tok_remaining = int(float(h["x-ratelimit-remaining-tokens"]))
                m.tok_reset_at = time.time() + _duration(h.get("x-ratelimit-reset-tokens"))
            if "x-ratelimit-limit-tokens" in h:
                lim = int(float(h["x-ratelimit-limit-tokens"]))
                if 0 < lim < 1_000_000:
                    m.req_tokens = min(m.req_tokens, int(lim * 0.9)) if lim * 0.9 < m.req_tokens else m.req_tokens
        except (ValueError, TypeError):
            pass


class _Skip(Exception):
    """This model cannot serve the request right now; try the next one."""


# ------------------------------------------------------------------ whose keys

NO_KEYS = ("Add your own AI key first (AI keys, at the top of the page). Your keys are kept only in your browser, "
           "so after the server restarts Scriptavera needs you to open the page once; then press Continue.")

_service = Router()                                   # the keys in .env: the desktop app, or the service's own
_people: dict[str, Router] = {}                       # owner -> that person's keys, in memory only
_fingerprints: dict[str, str] = {}
_people_lock = threading.Lock()


def shared_keys() -> bool:
    """On scriptavera.com, whether people without keys of their own may use the service's (SCRIPTAVERA_SHARED_KEYS=1)."""
    return config.get_env("SCRIPTAVERA_SHARED_KEYS") == "1"


def keys_fingerprint(keys: dict[str, list[str]]) -> str:
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()


def set_person_keys(owner: str, keys: dict[str, list[str]]) -> Router:
    """A person's keys, for as long as this server runs: never written anywhere. The same keys sent again (each
    time the page opens) reuse what was found out about them."""
    fp = keys_fingerprint(keys)
    with _people_lock:
        if _fingerprints.get(owner) == fp and owner in _people:
            return _people[owner]
    r = Router(keys)
    r.discover(True)
    with _people_lock:
        _people[owner], _fingerprints[owner] = r, fp
    return r


def drop_person_keys(owner: str) -> None:
    with _people_lock:
        _people.pop(owner, None)
        _fingerprints.pop(owner, None)


def move_person_keys(src: str, dst: str) -> None:
    """A guest signs in: the keys they gave as a guest are now their account's."""
    with _people_lock:
        if src in _people and dst not in _people:
            _people[dst], _fingerprints[dst] = _people.pop(src), _fingerprints.pop(src, "")


def person_router(owner: str | None) -> Router | None:
    with _people_lock:
        return _people.get(owner) if owner else None


class _Empty(Router):
    """The router of someone who has given no keys: it knows no model, and says why when asked for one."""

    def __init__(self) -> None:
        super().__init__({})
        self.discovered_at = time.time()

    def discover(self, force: bool = False) -> dict:
        return self.status

    def chat(self, *a, **kw):
        raise LLMError(NO_KEYS, "nokeys")


_empty = _Empty()


class _RouterProxy:
    """`router` everywhere in the app: the router of whoever's work this is (tenancy.current()). On a computer, and
    for work that is nobody's, the keys in .env."""

    @staticmethod
    def _target() -> Router:
        owner = tenancy.current()
        if owner is None:
            return _service
        mine = person_router(owner)
        if mine is not None and mine.models:
            return mine
        return _service if shared_keys() else _empty

    def __getattr__(self, name):
        return getattr(self._target(), name)

    def __setattr__(self, name, value):
        setattr(self._target(), name, value)


router = _RouterProxy()
