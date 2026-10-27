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

print(f"Testing {len(keys)} keys...\n")

for i, k in enumerate(keys, 1):
    masked = k[:8] + "..." + k[-4:]
    # Test generation on gemini-2.5-flash
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={k}"
    payload = json.dumps({"contents": [{"parts": [{"text": "ping"}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req) as resp:
            code = resp.getcode()
            headers = dict(resp.info())
            print(f"Key {i} ({masked}): ACTIVE (HTTP {code})")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", "")
            status = err_json.get("error", {}).get("status", "")
        except:
            msg = err_body[:100]
            status = str(e.code)
        
        if e.code == 429:
            print(f"Key {i} ({masked}): HIT LIMIT (HTTP 429: {status} - {msg[:70]})")
        else:
            print(f"Key {i} ({masked}): ERROR (HTTP {e.code}: {status} - {msg[:70]})")
    except Exception as e:
        print(f"Key {i} ({masked}): Network error: {e}")
    time.sleep(0.5)
