# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
import urllib.request, json, os, time

env_path = 'C:/Users/vaibh/Desktop/scriptavera/.env'
keys = []
if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('GEMINI_API_KEY='):
                raw = line.strip().split('=', 1)[1]
                keys = [k.strip() for k in raw.split(',') if k.strip()]

print(f"Testing {len(keys)} keys on gemini-3.8-flash and gemini-1.5-flash...\n")

for i, k in enumerate(keys, 1):
    masked = k[:8] + "..." + k[-4:]
    # Check available models
    url_m = f"https://generativelanguage.googleapis.com/v1beta/models?key={k}"
    try:
        with urllib.request.urlopen(url_m) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            avail = [m["name"].replace("models/", "") for m in data.get("models", [])]
            # test one model that is in avail
            test_model = "gemini-3.8-flash" if "gemini-3.8-flash" in avail else ("gemini-1.5-flash" if "gemini-1.5-flash" in avail else avail[0])
            
            # test generation
            url_gen = f"https://generativelanguage.googleapis.com/v1beta/models/{test_model}:generateContent?key={k}"
            payload = json.dumps({"contents": [{"parts": [{"text": "hello"}]}]}).encode("utf-8")
            req = urllib.request.Request(url_gen, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as gresp:
                    print(f"Key {i} ({masked}): OK (Model: {test_model}, HTTP 200, Total models: {len(avail)})")
            except urllib.error.HTTPError as ge:
                eb = ge.read().decode("utf-8", errors="ignore")
                print(f"Key {i} ({masked}): LIMIT/ERROR on {test_model} (HTTP {ge.code})")
    except urllib.error.HTTPError as e:
        print(f"Key {i} ({masked}): Models list error: HTTP {e.code}")
    except Exception as e:
        print(f"Key {i} ({masked}): {e}")
