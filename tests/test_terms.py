# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The licence files are the official texts, every program file carries its notice, the source download never
contains private files, and only this app's own pages can send it orders."""
import hashlib, io, os, socket, threading, time, zipfile
from helpers import ROOT, isolate
isolate("scriptavera_terms_")
import requests, uvicorn
from scriptavera import server

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(ROOT / "LICENSE") == "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0", "LICENSE must be the official AGPL-3.0 text"
assert sha(ROOT / "scriptavera/static/vendor/katex/LICENSE") == "766ccc1f306c885aa45542a9846bbd0a505b27a0374f146778171c2254ce18e3"
for name in ("LICENSING.md", "CLA.md", "CONTRIBUTING.md", "TRADEMARKS.md", "THIRD_PARTY_NOTICES.md", "CITATION.cff", "funding.json"):
    assert (ROOT / name).is_file(), name
missing = [str(f.relative_to(ROOT)) for f in ROOT.rglob("*")
           if f.suffix in (".py", ".js", ".css", ".html", ".bat") and "vendor" not in f.parts and "__pycache__" not in f.parts
           and "projects" not in f.parts and "SPDX-License-Identifier: AGPL-3.0-only" not in f.read_text(encoding="utf-8")]
assert not missing, missing
print("1. the licence texts are the official ones and every program file carries the notice")

names = [str(f.relative_to(ROOT)) for f in server.source_files()]
assert "LICENSE" in names and "scriptavera/server.py" in names and "scriptavera/static/vendor/katex/LICENSE" in names
assert not [n for n in names if n.startswith(("projects", "input_notes")) or n.endswith((".env", "settings.json"))
            or any(p.startswith(".") for p in n.split("/")[1:] if not n.startswith(".github"))], names
z = zipfile.ZipFile(io.BytesIO(server.source_zip()))
assert all(n.startswith("scriptavera-") for n in z.namelist()) and any(n.endswith("/LICENSE") for n in z.namelist())
print("2. the source download holds the program and its terms, and nothing private")

sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
srv = uvicorn.Server(uvicorn.Config(server.create_app(), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    try:
        requests.get(f"http://127.0.0.1:{port}/api/source", timeout=2); break
    except requests.ConnectionError:
        time.sleep(0.1)
B = f"http://127.0.0.1:{port}"
post = lambda origin=None, host=None: requests.post(B + "/api/settings", json={"settings": {}}, timeout=10,
                                                    headers={k: v for k, v in (("Origin", origin), ("Host", host)) if v}).status_code
assert requests.get(B + "/api/source", timeout=20).status_code == 200
assert post("https://evil.example") == 403 and post("null") == 403
assert post(f"http://127.0.0.1:{port}") == 200 and post() == 200          # this app's page, or a program on this computer
assert requests.get(B + "/api/projects", headers={"Host": "attacker.example"}, timeout=10).status_code == 403, "a renamed address must not reach the app"
os.environ["SCRIPTAVERA_SOURCE_LINK"] = "0"
assert requests.get(B + "/api/source", timeout=10).status_code == 404
print("3. only this app's pages can send it orders; another website or address is refused")
print("terms ok")
