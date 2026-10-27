# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
import urllib.request, json

req = urllib.request.urlopen("http://127.0.0.1:8765/api/settings")
data = json.loads(req.read().decode("utf-8"))
models = data.get("models", [])
gemini_models = [m for m in models if m.get("provider") == "gemini"]

print(f"Total Gemini model endpoints discovered: {len(gemini_models)}")
for m in gemini_models:
    mod = m.get("model")
    note = m.get("note") or ""
    free = m.get("free")
    name = m.get("name") or ""
    if note or mod in ["gemini-3.8-flash", "gemini-3.7-flash"]:
        print(f"[{mod}] name={name}: note='{note}', free_until={free}")
