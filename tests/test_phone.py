# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Photos from a phone: a separate server on the local network, one page and images only, at a secret address;
it sees no paper, refuses what is not an image, and stops when told."""
import io, time
from helpers import isolate, page_images
isolate("scriptavera_phone_")
import requests
from scriptavera import phone

if not phone.lan_ip():
    print("phone skipped (no local network here)")
    raise SystemExit(0)
st = phone.start()
base = st["url"].rsplit("/m/", 1)[0]
for _ in range(50):
    try:
        if requests.get(st["url"], timeout=2, proxies={"http": None, "https": None}).status_code == 200:
            break
    except requests.RequestException:
        time.sleep(0.1)
s = requests.Session(); s.trust_env = False
assert "Photos of your notes" in s.get(st["url"], timeout=5).text
name, data = page_images(1)[0]
r = s.post(st["url"] + "/upload", files={"file": (name, data, "image/png")}, timeout=10).json()
assert r == {"ok": True, "n": 1}, r
assert s.post(st["url"] + "/upload", files={"file": ("x.txt", b"not an image", "text/plain")}, timeout=10).status_code == 400
assert s.get(st["url"].replace(st["url"].rsplit("/", 1)[1], "wrongsecret"), timeout=5).status_code == 404
assert s.get(base + "/api/projects", timeout=5).status_code == 404, "the phone server sees no paper"
assert phone.status()["files"] == ["001.png"] and phone.file_bytes("001.png") == data and phone.file_bytes("../x") is None
phone.stop()
time.sleep(0.6)
try:
    gone = s.get(st["url"], timeout=2).status_code == 404
except requests.RequestException:
    gone = True
assert gone and not phone.status()["active"]
print("phone ok")
