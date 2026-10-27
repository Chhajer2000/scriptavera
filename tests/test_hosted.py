# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""scriptavera.com: the host names and the redirect of the .org domain, the visitor's address, three free papers per
network for guests (HTTP 403 after that), Google sign-in bypassing it, papers nobody else can see, the account's
free papers and the subscription (Razorpay webhook), signatures only with Google Drive connected, the audit ledger,
paper.zip with its proof, collaborators remembered, the opt-in test suite, the purge after download, progress
checkpoints and their restore. Google, Supabase and Cloud Storage are stood in for; nothing leaves this computer."""
import hashlib, hmac, io, json, os, pathlib, socket, threading, time, zipfile
from helpers import isolate, page_images
tmp = isolate("scriptavera_hosted_")
os.environ.update({"SCRIPTAVERA_PUBLIC": "1", "SCRIPTAVERA_SESSION_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "test-client",
                   "SCRIPTAVERA_DB_PATH": str(tmp / "accounts.db"), "SCRIPTAVERA_ADMIN_EMAILS": "admin@example.org",
                   "SCRIPTAVERA_ALLOWED_HOSTS": "scriptavera-abc-el.a.run.app,.example.net", "SCRIPTAVERA_SYNC_SECONDS": "3600",
                   "SCRIPTAVERA_PURGE_GRACE_MINUTES": "0", "RAZORPAY_WEBHOOK_SECRET": "whsec", "SCRIPTAVERA_PROXY_HOPS": "1",
                   "SCRIPTAVERA_GUEST_NETWORK_LIMIT": "4"})
for k in ("SCRIPTAVERA_SHARED_KEYS",):
    os.environ.pop(k, None)
for k in ("SUPABASE_URL", "SUPABASE_KEY", "SCRIPTAVERA_GCS_BUCKET", "K_SERVICE"):
    os.environ.pop(k, None)
import requests, uvicorn
from scriptavera import accounts, cloudstore, config, hosted, hosting, llm, memory, provenance, store, tenancy
from scriptavera.server import create_app

# ------------------------------------------------------------------ 1. the address of the visitor
ip = hosting.client_ip
assert ip({"x-forwarded-for": "6.6.6.6, 1.2.3.4"}) == "1.2.3.4", "the first entry is the visitor's own writing"
assert ip({"x-forwarded-for": "1.2.3.4"}) == "1.2.3.4" and ip({}, "9.9.9.9") == "9.9.9.9"
os.environ["SCRIPTAVERA_PROXY_HOPS"] = "2"
assert ip({"x-forwarded-for": "6.6.6.6,1.2.3.4,34.1.1.1"}) == "1.2.3.4", "behind the load balancer: second from the right"
os.environ["SCRIPTAVERA_PROXY_HOPS"] = "1"
assert hosting.network_of("2001:db8:1:2:aaaa::1") == hosting.network_of("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
assert hosting.ip_key("1.2.3.4") != "1.2.3.4" and len(hosting.ip_key("1.2.3.4")) == 64, "addresses are kept only hashed"
tok = hosting.sign({"uid": "u1"})
assert hosting.unsign(tok)["uid"] == "u1" and hosting.unsign(tok[:-2] + "xx") is None and hosting.unsign("junk") is None
print("1. the visitor's address is read from the right of X-Forwarded-For and kept hashed; cookies are signed")


# ------------------------------------------------------------------ stand-ins for Google and Cloud Storage
def fake_google(credential):
    sub, email, name = credential.split("|")
    return {"sub": sub, "email": email, "email_verified": True, "name": name, "given_name": name.split()[0],
            "iss": "https://accounts.google.com"}
accounts.verify_google_credential = fake_google
accounts.verify_drive_token = lambda token: {"aud": "test-client", "scope": accounts.DRIVE_SCOPE, "email": "drive@example.org"}


class FakeBucket:
    def __init__(self):
        self.objects = {}
    def put(self, name, data, content_type=None):
        self.objects[name] = bytes(data)
    def get(self, name):
        return self.objects.get(name)
    def names(self, prefix):
        return sorted(n for n in self.objects if n.startswith(prefix))
    def delete(self, name):
        self.objects.pop(name, None)
bucket = FakeBucket()
cloudstore.use_bucket(bucket)

sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
B = f"http://127.0.0.1:{port}"


class Browser:
    """One browser: its cookies, its address and the host name it asks for."""
    def __init__(self, addr, host="scriptavera.com"):
        self.s = requests.Session()
        self.addr, self.host = addr, host

    def req(self, method, path, **kw):
        h = {"Host": self.host, "X-Forwarded-For": f"203.0.113.250, {self.addr}", **kw.pop("headers", {})}
        if method != "GET":
            h.setdefault("Origin", f"https://{self.host}")
        return self.s.request(method, B + path, headers=h, timeout=60, allow_redirects=False, **kw)

    def new_paper(self, authors=None, opt_in=False):
        meta = {"name": "Notes", "domains": ["math.CA"], "problem": "Explicit bounds for the partial sums of reciprocal powers.",
                "keywords": ["partial sums", "reciprocal powers"], "authors": authors or [{"name": "Guest Author"}],
                "declaration": {"agreed": True, "author": 0}, "dataset_opt_in": opt_in}
        files = [("files", (n, d, "image/png")) for n, d in page_images(2)]
        return self.req("POST", "/api/projects", files=files, data={"meta": json.dumps(meta)})

    def add_keys(self, **keys):
        return self.req("POST", "/api/keys", json={"keys": keys})

    def sign_in(self, sub, email, name):
        return self.req("POST", "/api/auth/google", json={"credential": f"{sub}|{email}|{name}"})


# ------------------------------------------------------------------ 2. host names
g = Browser("198.51.100.1")
assert g.req("GET", "/").status_code == 200
assert "/static/hosted.js" in g.req("GET", "/").text
for host, path, want in (("www.scriptavera.com", "/", "https://scriptavera.com/"),
                         ("scriptavera.org", "/editor/x?y=1", "https://scriptavera.com/editor/x?y=1"),
                         ("www.scriptavera.org", "/", "https://scriptavera.com/")):
    r = Browser("198.51.100.1", host).req("GET", path)
    assert r.status_code == 301 and r.headers["location"] == want, (host, r.status_code, r.headers.get("location"))
assert Browser("198.51.100.1", "scriptavera-abc-el.a.run.app").req("GET", "/api/meta").status_code == 200
assert Browser("198.51.100.1", "app.example.net").req("GET", "/api/meta").status_code == 200, "a .suffix entry"
assert Browser("198.51.100.1", "attacker.example").req("GET", "/api/meta").status_code == 403
assert g.req("POST", "/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
r = Browser("198.51.100.9").req("GET", "/api/meta")                 # a browser's first visit
assert r.headers.get("strict-transport-security") and "sv_guest=" in r.headers.get("set-cookie", "")
assert "sv_guest=" not in g.req("GET", "/api/meta").headers.get("set-cookie", ""), "given once, then kept"
meta = r.json()
assert meta["hosted"]["public"] and not meta["hosted"]["admin"] and not any(v["set"] for v in meta["env"].values())
print("2. scriptavera.com and the listed hosts are served; www and .org are sent to https://scriptavera.com")

# ------------------------------------------------------------------ 3. administrators only, closed features
assert g.req("POST", "/api/settings", json={"settings": {}}).status_code == 403
assert g.req("POST", "/api/test_mode", json={"on": False}).status_code == 403
assert g.req("GET", "/api/phone").status_code == 404 and g.req("POST", "/api/shutdown", json={}).status_code == 404
assert g.req("GET", "/api/admin/whoami").status_code == 403
print("3. settings, keys and test mode belong to the administrators; the phone page and shutdown are closed")

# ------------------------------------------------------------------ 4. three free papers per network for guests
acct = g.req("GET", "/api/account").json()
assert acct["guest_remaining"] == 3 and acct["dataset_label"].startswith("Help improve Scriptavera: let the Scriptavera team")
assert any("never used to train AI models" in x for x in acct["dataset_details"]) and not any("open" in x.split() for x in acct["dataset_details"])
r = g.new_paper()
assert r.status_code == 400 and r.json()["code"] == "nokeys", "no key of one's own: nothing is read, no free paper is taken"
assert g.req("GET", "/api/account").json()["guest_remaining"] == 3
G_KEY = "AIzaSyTEST-guest-key-0001"
k = g.add_keys(gemini=[G_KEY, "AIzaSyTEST-guest-key-0002"], groq=["bad key with spaces"], ollama=["http://evil"]).json()
assert k["ready"] and [p["id"] for p in k["providers"]] == list(llm.BYOK) and all(len(p["steps"]) >= 3 and p["link"].startswith("https://") for p in k["providers"])
assert llm.person_router("g:" + hosted.hosting.unsign(g.s.cookies["sv_guest"])["g"]) is not None
pids = []
for i in range(3):
    r = g.new_paper()
    assert r.status_code == 200, r.text
    pids.append(r.json()["id"])
assert g.req("GET", "/api/account").json()["guest_remaining"] == 0
r = g.new_paper()
assert r.status_code == 403 and r.json()["error"] == hosted.BROWSER_LIMIT_MESSAGE and r.json()["code"] == "guest_limit"
classmate = Browser("198.51.100.1")                     # someone else on the same university network
classmate.add_keys(gemini=["AIzaSyTEST-classmate-0001"])
assert classmate.new_paper().status_code == 200, "a shared network is not used up by one person"
third = Browser("198.51.100.1")
third.add_keys(gemini=["AIzaSyTEST-third-00000001"])
r = third.new_paper()
assert r.status_code == 403 and r.json()["error"] == ("Free guest limit of three papers reached for this network. "
                                                      "Please sign in with Google to continue."), r.text
spoof = Browser("198.51.100.1")
r = spoof.req("POST", "/api/projects", headers={"X-Forwarded-For": "10.9.9.9, 198.51.100.1"}, data={"meta": "{}"})
assert r.status_code == 403, "writing another address first changes nothing"
d0 = accounts.db()
d0.claim_guest("old-net", 4)
with d0._conn() as c0:
    c0.execute("update guest_ip_usage set papers_used = 4, first_seen = '2026-01-01T00:00:00+00:00' where ip_hash = 'old-net'")
assert d0.claim_guest("old-net", 4, 30) == (True, 1), "a network's count starts again each month"
print("4. three free guest papers per browser, a ceiling per network (403 with the message); a forged X-Forwarded-For does not help")

# ------------------------------------------------------------------ 5. nobody sees another person's paper
h = Browser("192.0.2.7")
assert h.req("GET", f"/api/projects/{pids[0]}/proof").status_code == 404
assert h.req("GET", f"/editor/{pids[0]}").status_code == 404
assert h.req("GET", f"/api/projects/{pids[0]}/download/paper.zip").status_code == 404
assert h.req("DELETE", f"/api/projects/{pids[0]}").status_code == 404
assert [x["id"] for x in h.req("GET", "/api/projects").json()["projects"]] == []
assert sorted(x["id"] for x in g.req("GET", "/api/projects").json()["projects"]) == sorted(pids)
proof = g.req("GET", f"/api/projects/{pids[0]}/proof").json()["proof"]
assert proof["missing"] and proof["locked"] and "Sign in with Google" in proof["error"], proof
assert not (config.PROJECTS_DIR / pids[0] / "originals").exists() and not provenance.status(config.PROJECTS_DIR / pids[0])
print("5. each paper is seen only by its owner; guests get no signed record and are told why")

# ------------------------------------------------------------------ 6. signing in: guest papers move in, then 402
r = g.sign_in("sub-a", "alice@example.org", "Alice Abel")
assert r.status_code == 200 and r.json()["adopted"] == 3 and "sv_session=" in r.headers.get("set-cookie", ""), r.text
a = g.req("GET", "/api/account").json()
assert a["user"]["email"] == "alice@example.org" and a["user_free_used"] == 3 and a["user_free_remaining"] == 0
r = g.new_paper()
assert r.status_code == 402 and r.json()["code"] == "paywall", r.text
assert sorted(x["id"] for x in g.req("GET", "/api/projects").json()["projects"]) == sorted(pids)


def webhook(event, sig=None, event_id=None):
    raw = json.dumps(event).encode()
    s = sig or hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    return requests.post(B + "/api/billing/webhook", data=raw, timeout=30, headers={
        "Host": "scriptavera.com", "Content-Type": "application/json", "X-Razorpay-Signature": s,
        "X-Razorpay-Event-Id": event_id or hashlib.sha1(raw).hexdigest()})
uid_a = a["user"] and accounts.db().upsert_user(accounts.profile_fields(fake_google("sub-a|alice@example.org|Alice Abel")))["id"]
event = {"event": "subscription.activated", "payload": {"subscription": {"entity": {
    "id": "sub_123", "plan_id": "plan_1", "current_end": int(time.time()) + 30 * 86400, "notes": {"user_id": uid_a}}}}}
assert webhook(event, sig="0" * 64).status_code == 400, "an unsigned webhook is refused"
assert webhook(event).status_code == 200
assert webhook(event).json().get("duplicate"), "each event is applied once"
assert g.req("GET", "/api/account").json()["subscribed"]
r = g.new_paper()
assert r.status_code == 200, r.text
print("6. signing in brings the guest papers along and counts them; after three, 402 until the subscription is active")

# ------------------------------------------------------------------ 7. a new account: the network's limit no longer applies
c = Browser("198.51.100.1")                                      # the network whose guest papers are used up
C_KEY = "AIzaSyTEST-carol-key-0001"
c.add_keys(gemini=[C_KEY])
assert c.sign_in("sub-c", "carol@example.org", "Carol Cauchy").status_code == 200
assert c.req("GET", "/api/keys").json()["ready"], "the keys given as a guest are the account's after signing in"
me = {"name": "Carol Cauchy", "email": "carol@example.org", "affiliation": "Univ. Lyon", "orcid": "0000-0002-1825-0097"}
bob = {"name": "Bob Builder", "email": "bob@example.edu", "affiliation": "ETH Zurich"}
r = c.new_paper(authors=[me, bob])
assert r.status_code == 200, r.text
p1 = r.json()["id"]
pr = c.req("GET", f"/api/projects/{p1}/proof").json()["proof"]
assert pr["locked"] and "connect your Google Drive" in pr["error"], "signed in, but no Drive yet: no signature"
assert c.req("POST", f"/api/projects/{p1}/proof", json={"action": "record_now", "declaration": {"agreed": True, "author": 0}}).status_code == 403
assert c.req("POST", "/api/account/drive", json={"access_token": "t"}).json()["user"]["drive_connected"]
r = c.new_paper(authors=[me, {"name": "Bob Builder"}, {"name": "Dan Dirichlet", "orcid": "0000-0001-5109-3700"}])
assert r.status_code == 200, r.text
p2 = r.json()["id"]
folder = config.PROJECTS_DIR / p2
st = provenance.status(folder)
assert st and (folder / "originals").exists() and not (folder / "proof" / "locked.json").exists(), "Drive connected: signed"
acct = c.req("GET", "/api/account").json()
assert acct["user_free_used"] == 2 and acct["user"]["orcid"] == "0000-0002-1825-0097" and acct["user"]["affiliation"] == "Univ. Lyon"
print("7. signed in, the network's guest limit is gone; records only once Google Drive is connected")

# ------------------------------------------------------------------ 7b. memory: each person's own
uid_carol = accounts.db().get_paper(p2)["owner_user_id"]
with tenancy.owner(f"u:{uid_carol}"):
    memory.learn_notation({"domains": ["math.CA"], "name": "x"}, [{"symbol": "\\varphi", "meaning": "Euler's totient function"}])
    memory.learn_reading({"name": "x"}, "z", "2")
    assert memory.remembered("\\varphi", ["math.CA"])["meaning"] == "Euler's totient function" and "“z”" in memory.quirks()
uid_alice = accounts.db().upsert_user(accounts.profile_fields(fake_google("sub-a|alice@example.org|Alice Abel")))["id"]
with tenancy.owner(f"u:{uid_alice}"):
    assert memory.remembered("\\varphi", ["math.CA"]) is None and memory.quirks() == "", "Alice never sees Carol's notation"
with tenancy.owner("g:someone"):
    assert memory.remembered("\\varphi", ["math.CA"]) is None
    memory.learn_notation({"domains": ["math.CA"]}, [{"symbol": "\\psi", "meaning": "a test function"}])
memory.merge_guest("g:someone", f"u:{uid_alice}")
with tenancy.owner(f"u:{uid_alice}"):
    assert memory.remembered("\\psi", ["math.CA"])["meaning"] == "a test function", "a guest's memory joins their account"
assert c.req("GET", "/api/account").json()["memory"] == {"symbols": 1, "readings": 1}
assert g.req("GET", "/api/account").json()["memory"] == {"symbols": 1, "readings": 0}
assert c.req("POST", "/api/memory", json={"action": "clear"}).status_code == 200
assert c.req("GET", "/api/account").json()["memory"] == {"symbols": 0, "readings": 0}
assert g.req("GET", "/api/account").json()["memory"]["symbols"] == 1, "clearing one's memory leaves the others'"
print("7b. what is learned about notation and handwriting is each person's own")

# ------------------------------------------------------------------ 8. collaborators remembered
found = c.req("GET", "/api/collaborators?q=bo").json()["collaborators"]
assert len(found) == 1 and found[0]["email"] == "bob@example.edu" and found[0]["times_collaborated"] == 2, found
assert {x["full_name"] for x in c.req("GET", "/api/collaborators?q=").json()["collaborators"]} == {"Bob Builder", "Dan Dirichlet"}
assert not [x for x in c.req("GET", "/api/collaborators?q=carol").json()["collaborators"]], "the person is not their own collaborator"
assert g.req("GET", "/api/collaborators?q=bo").json()["collaborators"] == [], "another account sees none of them"
print("8. every co-author is remembered with their details, and offered again by name")

# ------------------------------------------------------------------ 9. the ledger, paper.zip with its proof, the archive
(folder / "proof" / "tsa-freetsa.org_tsr.tsr").write_bytes(b"\x30\x82" + b"r" * 4000)
st["tsa"] = [{"url": "https://freetsa.org/tsr", "ok": True, "time": time.time(), "file": "tsa-freetsa.org_tsr.tsr"}]
provenance._notify(folder, st)
ledger = bucket.names(f"signatures/{p2}/notes/")
assert {f"signatures/{p2}/notes/{n}" for n in ("record.json", "record.sig", "tsa-freetsa.org_tsr.tsr", "SHA256SUMS.txt")} <= set(ledger), ledger
sums = bucket.get(f"signatures/{p2}/notes/SHA256SUMS.txt").decode()
assert hashlib.sha256((folder / "proof" / "record.json").read_bytes()).hexdigest() + "  proof/record.json" in sums
assert accounts.db().get_paper(p2)["signed"]

p = store.get(p2)
(folder / "out").mkdir(exist_ok=True)
from scriptavera import paper as paperlib
(folder / "out" / "main.tex").write_text("\\documentclass{amsart}\\author{Carol Cauchy}\\email{carol@example.org}\\begin{document}$x^2$\\end{document}")
(folder / "out" / "paper.zip").write_bytes(paperlib.make_zip({"main.tex": "x", "README.txt": "y"}))
p.save("transcript", {"pages": {"1": {"text": "Lemma (Carol Cauchy). $a^2+b^2$"}}})
p.update_state(lambda s: s["stages"]["finish"].update(status="done"))
z = zipfile.ZipFile(io.BytesIO(c.req("GET", f"/api/projects/{p2}/download/paper.zip").content))
assert {"main.tex", "proof/record.json", "proof/tsa-freetsa.org_tsr.tsr", "proof/SHA256SUMS.txt", "proof/certificate.html"} <= set(z.namelist())
arch = zipfile.ZipFile(io.BytesIO(c.req("GET", f"/api/projects/{p2}/archive").content))
names = arch.namelist()
assert "paper/paper.zip" in names and "proof/SHA256SUMS.txt" in names and any(n.startswith("notes/") for n in names), names
print("9. the receipts and the SHA-256 manifest go to the ledger and into paper.zip; the archive has paper, proof, notes")

# ------------------------------------------------------------------ 10. checkpoints, purge after download, restore
uid_c = accounts.db().get_paper(p2)["owner_user_id"]
hosted.forget_paper(p2)
hosted.tidy_once()
assert not (folder / "pages").exists() and not (folder / "originals").exists(), "not opted in: notes purged after the download"
assert (folder / "out" / "main.tex").exists() and accounts.db().get_paper(p2)["purged_at"]
assert bucket.names(f"progress/{uid_c}/{p1}/") and not [n for n in bucket.names(f"progress/{uid_c}/{p2}/") if "/pages/" in n]
assert not [n for n in bucket.objects if n.startswith("dataset/")], "nothing goes to the test suite without the tick"
import shutil
shutil.rmtree(config.PROJECTS_DIR / p1)
assert c.req("GET", f"/api/projects/{p1}/proof").status_code == 200 and (config.PROJECTS_DIR / p1 / "state.json").exists(), "restored"
print("10. a signed-in person's work is checkpointed and comes back after a restart; notes are purged after download")

# ------------------------------------------------------------------ 11. the opt-in test suite, anonymous
r = c.new_paper(authors=[me, bob], opt_in=True)
assert r.status_code == 200, r.text
p3 = r.json()["id"]
f3 = config.PROJECTS_DIR / p3
q = store.get(p3)
(f3 / "out").mkdir(exist_ok=True)
(f3 / "out" / "main.tex").write_text("\\documentclass{amsart}\\title{T}\\author{Bob Builder}\\address{ETH Zurich}\\email{bob@example.edu}"
                                     "\\begin{document}\\maketitle $\\int f$ \\section*{Acknowledgements} Thanks to Bob. \\end{document}")
q.save("transcript", {"pages": {"1": {"text": "Write to bob@example.edu. $\\sum a_n$"}}})
q.update_state(lambda s: s["stages"]["finish"].update(status="done"))
hosted.forget_paper(p3)
hosted.tidy_once()
ds = [n for n in bucket.objects if n.startswith("dataset/")]
assert any(n.endswith("/main.tex") for n in ds) and any("/pages/" in n for n in ds) and any(n.endswith("transcript.json") for n in ds), ds
tex = bucket.get(next(n for n in ds if n.endswith("main.tex"))).decode()
text = bucket.get(next(n for n in ds if n.endswith("transcript.json"))).decode()
assert "Bob" not in tex and "bob@" not in tex and "ETH" not in tex and "\\int f" in tex, tex
assert "bob@" not in text and "\\\\sum a_n" in text, text
assert p3 not in "".join(ds) and uid_c not in "".join(ds), "the test suite's folder says nothing of whose paper it was"
assert (f3 / "pages").exists(), "an opted-in paper is not purged"
row3 = accounts.db().get_paper(p3)
assert row3["dataset_consent_version"] == hosted.CONSENT_VERSION and row3["dataset_consent_at"]
assert c.req("GET", "/api/account").json()["contributions"] == 1
assert c.req("POST", "/api/account/withdraw").json()["withdrawn"] == 1
assert not [n for n in bucket.objects if n.startswith("dataset/")] and not accounts.db().get_paper(p3)["dataset_opt_in"]
hosted.forget_paper(p3)
hosted.tidy_once()
assert not [n for n in bucket.objects if n.startswith("dataset/")], "withdrawn: it is not copied again"
print("11. with the tick, pages, text and LaTeX go to the test suite without names, e-mails or affiliations")

# ------------------------------------------------------------------ 12. signing out, deleting
assert c.req("POST", "/api/auth/logout").status_code == 200
assert c.req("GET", f"/api/projects/{p2}/proof").status_code == 404, "signed out: the account's papers are closed"
c.sign_in("sub-c", "carol@example.org", "Carol Cauchy")
assert c.req("DELETE", f"/api/projects/{p1}").status_code == 200
assert not bucket.names(f"progress/{uid_c}/{p1}/") and accounts.db().get_paper(p1)["deleted_at"]
assert bucket.names(f"signatures/{p2}/"), "the audit ledger stays"
adm = Browser("192.0.2.200")
adm.sign_in("sub-adm", "admin@example.org", "Ada Admin")
w = adm.req("GET", "/api/admin/whoami").json()
assert w["ip"] == "192.0.2.200" and w["database"] == "sqlite" and w["storage"]
assert adm.req("POST", "/api/settings", json={"settings": {}}).status_code == 200
print("12. signing out closes the papers; deleting removes the checkpoints but not the ledger; admins have settings")

# ------------------------------------------------------------------ 13. keys: never stored, forgotten, plans told apart
blob = (tmp / "accounts.db").read_bytes() + b"".join(x.read_bytes() for x in config.PROJECTS_DIR.rglob("*") if x.is_file())
blob += b"".join(bucket.objects.values())
assert G_KEY.encode() not in blob and C_KEY.encode() not in blob, "a key is never written anywhere"
assert c.req("DELETE", "/api/keys").json()["ready"] is False
assert not c.req("GET", "/api/meta").json()["has_vision"], "forgotten: nothing reads for Carol any more"
assert c.req("POST", f"/api/projects/{p2}/run", json={}).status_code == 400
c.req("POST", "/api/auth/logout")
assert llm.person_router(f"u:{uid_carol}") is None


class Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers, self.text = code, body or {}, headers or {}, json.dumps(body or {})
    def json(self):
        return self._b
real_get, real_post = llm.requests.get, llm.requests.post
try:
    llm.requests.get = lambda url, **kw: Resp(200, {"data": {"is_free_tier": "sk-or-free" in kw["headers"]["Authorization"]}})
    r0 = llm.Router({})
    assert r0._tier("openrouter", "sk-or-free-1", []) == "free" and r0._tier("openrouter", "sk-or-paid-1", []) == "paid"
    small = [llm.Model(provider="groq", id="llama-3.1-8b-instant"), llm.Model(provider="groq", id="openai/gpt-oss-120b")]
    seen = []
    def post(url, **kw):
        seen.append(json.loads(kw["data"])["model"])
        return Resp(200, {}, {"x-ratelimit-limit-tokens": "6000" if "free" in kw["headers"]["Authorization"] else "300000"})
    llm.requests.post = post
    assert r0._tier("groq", "gsk_free", small) == "free" and r0._tier("groq", "gsk_paid", small) == "paid" and seen[0] == "llama-3.1-8b-instant"
    llm.requests.post = lambda url, **kw: Resp(200, {}, {"x-ratelimit-limit-tokens-minute": "60000"})
    assert r0._tier("cerebras", "csk-x", [llm.Model(provider="cerebras", id="gpt-oss-120b")]) == "free"
    assert r0._tier("mistral", "m", [llm.Model(provider="mistral", id="mistral-small-latest")]) == ""
    def boom(*a, **kw):
        raise llm.requests.ConnectionError("down")
    llm.requests.get = llm.requests.post = boom
    assert r0._tier("openrouter", "x", []) == "" and r0._tier("groq", "x", small) == "", "not knowing never breaks a key"
finally:
    llm.requests.get, llm.requests.post = real_get, real_post
assert llm.leaks("gemini", "free") and not llm.leaks("gemini", "paid") and llm.leaks("mistral", "") and not llm.leaks("groq", "free")
assert llm.Router({"GEMINI_API_KEY": ["k"]})._keys("ollama") == [] and llm.Router({"GEMINI_API_KEY": ["k"]})._base("custom") == ""
print("13. keys are held in memory only and forgotten on request or sign-out; free and paid plans are told apart")
print("hosted ok")
