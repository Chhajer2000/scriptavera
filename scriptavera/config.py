# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Paths, API keys (.env) and user settings (settings.json)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
ENV_PATH = ROOT / ".env"
SETTINGS_PATH = ROOT / "settings.json"
MEMORY_DIR = ROOT / "memory"              # what is learned about the author from paper to paper (this computer only)
PHONE_DIR = ROOT / "phone_uploads"        # photos sent from a phone, until they become notes
IDENTITY_DIR = ROOT / "identity"          # the author's signing key for the records of their notes; never shared
STATIC_DIR = Path(__file__).resolve().parent / "static"

_lock = threading.Lock()

# Every key the app understands. `secret` values are never sent to the browser.
ENV_KEYS = {
    "GEMINI_API_KEY": {"label": "Google Gemini", "secret": True},
    "GROQ_API_KEY": {"label": "Groq", "secret": True},
    "CEREBRAS_API_KEY": {"label": "Cerebras", "secret": True},
    "OPENROUTER_API_KEY": {"label": "OpenRouter", "secret": True},
    "MISTRAL_API_KEY": {"label": "Mistral", "secret": True},
    "CUSTOM_API_KEY": {"label": "Custom endpoint key", "secret": True},
    "CUSTOM_BASE_URL": {"label": "Custom endpoint URL", "secret": False},
    "CUSTOM_MODELS": {"label": "Custom endpoint models", "secret": False},
    "OLLAMA_BASE_URL": {"label": "Ollama URL", "secret": False},
    "OPENALEX_API_KEY": {"label": "OpenAlex", "secret": True},
    "S2_API_KEY": {"label": "Semantic Scholar", "secret": True},
    "CONTACT_EMAIL": {"label": "Contact e-mail for scholarly APIs", "secret": False},
}

DEFAULT_SETTINGS = {
    # Pinned models per role, e.g. {"vision_best": ["gemini:gemini-3.8-flash"]}
    "routing": {},
    "scout_batch": 4,          # pages read per scout round
    "scout_confidence": 0.7,   # stop scouting once topic confidence reaches this
    "candidates_keep": 24,     # papers kept after ranking
    "read_top_k": 8,           # papers read in full
    "parallel_pages": 2,       # simultaneous page calls in the survey pass
    "ref_order": "alphabetical",  # or "appearance"
    "document_class": "amsart",   # or "article"
    "proof_timestamps": True,     # timestamp the fingerprint of uploaded notes with independent services
    "proof_tsa": [],              # RFC 3161 authorities; empty means the defaults (FreeTSA, DigiCert)
    "proof_calendars": [],        # OpenTimestamps calendars; empty means the defaults
    "private": False,             # Private mode: only paid-tier (or local) models; nothing that describes results is searched for
    "private_models": [],         # further models allowed in Private mode, e.g. "gemini:gemini-4-argon" once Google prices it
    # Test mode: papers created while it is on search only literature published before `before`, never the papers in
    # `exclude` (arXiv ids, DOIs or titles), and never OEIS; the limits are kept with each test paper.
    "test_mode": {"on": False, "before": "", "exclude": []},
    "prices": {},                 # model -> [input, output] in dollars per million tokens, overriding the built-in table
}


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def load_env() -> None:
    """Load .env into os.environ (file values win so edits take effect)."""
    if ENV_PATH.exists():
        for k, v in _parse_env(ENV_PATH.read_text(encoding="utf-8", errors="ignore")).items():
            if v:
                os.environ[k] = v


def get_env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def get_keys(name: str) -> list[str]:
    """A variable may hold several comma-separated keys."""
    return [k.strip() for k in get_env(name).split(",") if k.strip()]


def save_env(updates: dict[str, str]) -> None:
    """Update .env in place, preserving unrelated lines and comments."""
    with _lock:
        lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines() if ENV_PATH.exists() else []
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in updates:
                    seen.add(k)
                    if updates[k]:
                        out.append(f"{k}={updates[k]}")
                    continue
            out.append(line)
        for k, v in updates.items():
            if k not in seen and v:
                out.append(f"{k}={v}")
        ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        for k, v in updates.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


def mask(value: str) -> str:
    if not value:
        return ""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return ", ".join((p[:4] + "…" + p[-3:]) if len(p) > 10 else "•••" for p in parts)


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return data


def save_settings(updates: dict) -> dict:
    with _lock:
        data = load_settings()
        for k, v in updates.items():
            if k in DEFAULT_SETTINGS:
                data[k] = v
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data


def simulator():
    """The simulated models and databases used by the automated tests. They live in tests/, outside the app
    package: nothing in the app itself contains sample notes or a sample paper."""
    import importlib
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(os.environ.get("SCRIPTAVERA_SIMULATOR", "tests.simulator"))


def mock_mode() -> bool:
    """Developer switch: simulated models and literature, no network."""
    return get_env("SCRIPTAVERA_MOCK") == "1"
