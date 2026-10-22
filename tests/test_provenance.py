# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The record of the notes, made at upload: originals kept byte for byte, fingerprints signed, an RFC 3161 timestamp
(from a time-stamp authority run here with OpenSSL) and an OpenTimestamps proof (from a calendar simulated here) that
is later completed with a Bitcoin block; the certificate, the bundle, and checking it all the way a stranger would."""
import hashlib, http.server, io, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, zipfile
import os
os.environ["SCRIPTAVERA_TEST_TIMESTAMPS"] = "1"          # the services are simulated here, on this computer
from helpers import isolate
tmp = isolate("scriptavera_proof_")
from pathlib import Path
from scriptavera import config, provenance as P, timestamps as T

if not shutil.which("openssl"):
    print("proof ok (skipped: openssl is not installed here)")
    sys.exit(0)

# ---------------------------------------------------------------- a time-stamp authority, with OpenSSL
ca = tmp / "tsa"
ca.mkdir()
(ca / "tsa.cnf").write_text("""[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = Test TSA
[ tsa_ext ]
basicConstraints = critical,CA:false
extendedKeyUsage = critical,timeStamping
keyUsage = critical,digitalSignature
[ tsa ]
default_tsa = t1
[ t1 ]
dir = .
serial = ./serial
crypto_device = builtin
signer_digest = sha256
default_policy = 1.2.3.4.1
digests = sha256, sha512
accuracy = secs:1
ess_cert_id_alg = sha256
certs = ./tsa.crt
""")
(ca / "serial").write_text("01\n")
run = lambda *a: subprocess.run(a, cwd=ca, capture_output=True, check=True)      # noqa: E731
run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt", "-days", "5",
    "-subj", "/CN=Test Root", "-addext", "basicConstraints=critical,CA:true", "-addext", "keyUsage=critical,keyCertSign")
run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tsa.key", "-out", "tsa.csr", "-subj", "/CN=Test TSA")
run("openssl", "x509", "-req", "-in", "tsa.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-out", "tsa.crt",
    "-days", "5", "-extfile", "tsa.cnf", "-extensions", "tsa_ext")

PENDING_AT: dict[bytes, bytes] = {}            # message at the calendar's pending attestation -> its Bitcoin path
MERKLE: dict[int, bytes] = {}
state = {"tsa_down": False, "calendar_hits": 0}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body=b"", ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/tsr":
            if state["tsa_down"]:
                return self.reply(503)
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "q.tsq").write_bytes(data)
                subprocess.run(["openssl", "ts", "-reply", "-config", str(ca / "tsa.cnf"), "-queryfile", str(Path(d) / "q.tsq"),
                                "-inkey", str(ca / "tsa.key"), "-signer", str(ca / "tsa.crt"), "-out", str(Path(d) / "r.tsr")],
                               cwd=ca, capture_output=True)
                return self.reply(200, (Path(d) / "r.tsr").read_bytes(), "application/timestamp-reply")
        if self.path == "/digest" and len(data) == 32:
            state["calendar_hits"] += 1
            salt = b"calendar-salt"
            m1 = hashlib.sha256(salt + data).digest()
            st = T.Stamp(data)
            node = st.ops.setdefault((0xf1, salt), T.Stamp(salt + data)).ops.setdefault((0x08, b""), T.Stamp(m1))
            uri = f"http://127.0.0.1:{port}".encode()
            node.attestations.append((T.PENDING, T._varuint(len(uri)) + uri))
            # the later path into a block: append, double SHA-256, as Bitcoin's merkle tree does
            tail = T.Stamp(m1)
            a = tail.ops.setdefault((0xf0, b"other-tx"), T.Stamp(m1 + b"other-tx"))
            b = a.ops.setdefault((0x08, b""), T.Stamp(hashlib.sha256(m1 + b"other-tx").digest()))
            root_msg = hashlib.sha256(hashlib.sha256(m1 + b"other-tx").digest()).digest()
            c = b.ops.setdefault((0x08, b""), T.Stamp(root_msg))
            c.attestations.append((T.BITCOIN, T._varuint(840123)))
            MERKLE[840123] = root_msg
            PENDING_AT[m1] = tail.write()
            return self.reply(200, st.write())
        self.reply(404)

    def do_GET(self):
        if self.path.startswith("/timestamp/"):
            m = bytes.fromhex(self.path.rsplit("/", 1)[1])
            if m in PENDING_AT and state.get("mined"):
                return self.reply(200, PENDING_AT[m])
            return self.reply(404, b"Pending confirmation in Bitcoin blockchain")
        self.reply(404)


sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{port}"
config.save_settings({"proof_tsa": [B + "/tsr"], "proof_calendars": [B]})
T.bitcoin_block = lambda h, timeout=15.0: ({"height": h, "hash": "00" * 32, "merkle": MERKLE[h][::-1].hex(),
                                            "time": 1790000000.0, "source": "test"} if h in MERKLE else None)

# ---------------------------------------------------------------- the key
me = P.identity()
assert len(me["fingerprint"].split()) == 8 and P.identity()["fingerprint"] == me["fingerprint"], me
assert (config.IDENTITY_DIR / "signing-key.pem").exists() and "PRIVATE KEY" in (config.IDENTITY_DIR / "signing-key.pem").read_text()
assert "PRIVATE" not in me["pem"]
print("1. a signing key is made once on this computer and reused; only its public part is ever shown")

# ---------------------------------------------------------------- the record
notes = [("my notes.pdf", b"%PDF-1.4 handwritten pages ..." * 100), ("page 7.jpg", b"\xff\xd8\xff" + os.urandom(3000))]
folder = tmp / "projects" / "p1"
from scriptavera.server import declaration_text, orcid_id
assert orcid_id("0000-0002-1825-0097") == orcid_id("https://orcid.org/0000000218250097") == "0000-0002-1825-0097"
assert orcid_id("0000-0002-1694-233X") == "0000-0002-1694-233X" and orcid_id("0000-0002-1825-0098") is None and orcid_id("12") is None
people = [{"name": "V. Chhajer", "email": "v@example.org", "affiliation": "Example University, Pune, India", "orcid": "0000-0002-1825-0097"},
          {"name": "A. Coauthor", "affiliation": "Independent"}]
made_by = {"name": "V. Chhajer", "is_author": True}
st = P.make_record(folder, notes, {"title": "Cyclic subgroups", "authors": people, "author": {"name": "V. Chhajer", "email": "v@example.org"},
                                   "recorded_by": made_by, "declaration": declaration_text(people, made_by),
                                   "description": "An upper bound on the order of a finite abelian group with c(G) = |G|/2 + m.",
                                   "keywords": ["cyclic subgroups", "involutions"],
                                   "subjects": [{"code": "math.GR", "name": "Group Theory", "msc": ["20"]}]})
rec = P.record_of(folder)
assert rec["version"] == 2 and [a["name"] for a in rec["authors"]] == ["V. Chhajer", "A. Coauthor"] and rec["authors"][0]["orcid"] == "0000-0002-1825-0097"
assert rec["recorded_by"] == made_by and rec["declaration"].startswith("I, V. Chhajer, am an author of these notes. The other authors listed agree")
assert rec["subjects"][0]["code"] == "math.GR" and rec["keywords"] == ["cyclic subgroups", "involutions"] and "Copyright" in rec["rights"]
assert declaration_text(people[:1], made_by).startswith("I, V. Chhajer, am the author of these notes.")
assert "am not an author" in declaration_text(people, {"name": "R. Assistant", "is_author": False})
raw = (folder / "proof" / "record.json").read_bytes()
assert hashlib.sha256(raw).hexdigest() == st["digest"]
assert [f["sha256"] for f in rec["files"]] == [hashlib.sha256(d).hexdigest() for _, d in notes]
assert (folder / rec["files"][0]["stored_as"]).read_bytes() == notes[0][1], "the original is kept byte for byte"
assert rec["author"] == {"name": "V. Chhajer", "email": "v@example.org"} and rec["public_key"]["fingerprint"] == me["fingerprint"]
assert P.verify_signature(raw, (folder / "proof" / "record.sig").read_text().strip(), me["public"])
assert not P.verify_signature(raw.replace(b"Cyclic", b"Cyclix"), (folder / "proof" / "record.sig").read_text().strip(), me["public"])
assert st["tsa"] == [] and st["ots"] is None
print("2. the originals are kept unchanged; their fingerprints are recorded and the record is signed (offline, at once)")

# ---------------------------------------------------------------- timestamps; a service that is down is retried
state["tsa_down"] = True
st = P.timestamp(folder)
assert not st["tsa"][0]["ok"] and "503" in st["tsa"][0]["error"] and st["ots"]["status"] == "pending", st
assert st["next_try"] > time.time() and not P.due(folder)
state["tsa_down"] = False
s2 = P.status(folder); s2["next_try"] = time.time() - 1; P._save_status(folder, s2)
assert P.due(folder)
hits = state["calendar_hits"]
st = P.timestamp(folder)
assert st["tsa"][0]["ok"] and st["tsa"][0]["tsa"] == "CN=Test TSA" and abs(st["tsa"][0]["time"] - time.time()) < 120, st
assert state["calendar_hits"] == hits, "a calendar that already answered is not asked again"
assert st["next_try"] == 0
tsr = folder / "proof" / st["tsa"][0]["file"]
v = subprocess.run(["openssl", "ts", "-verify", "-data", str(folder / "proof" / "record.json"), "-in", str(tsr),
                    "-CAfile", str(ca / "ca.crt"), "-untrusted", str(ca / "tsa.crt")], capture_output=True, text=True)
assert "Verification: OK" in v.stdout, v.stdout + v.stderr
summ = T.ots_summary((folder / "proof" / "record.json.ots").read_bytes())
assert summ["digest"] == st["digest"] and summ["pending"] == [B] and not summ["bitcoin"], summ
print("3. RFC 3161: the authority's signed time verifies with OpenSSL itself; a service that was down is asked again later")

# ---------------------------------------------------------------- Bitcoin confirmation arrives later
s3 = P.status(folder); s3["ots"]["submitted"] -= 7200; P._save_status(folder, s3)
assert P.due(folder)
st = P.upgrade(folder)
assert st["ots"]["status"] == "pending", "not mined yet: still pending"
state["mined"] = True
s4 = P.status(folder); s4["ots"]["checked"] = 0; P._save_status(folder, s4)
st = P.upgrade(folder)
assert st["ots"]["status"] == "bitcoin" and st["ots"]["height"] == 840123 and st["ots"]["verified"], st["ots"]
summ = T.ots_summary((folder / "proof" / "record.json.ots").read_bytes())
assert summ["bitcoin"][0]["height"] == 840123 and summ["bitcoin"][0]["merkle"] == MERKLE[840123][::-1].hex()
# the finished file reads back and writes out identically
d, stamp = T.read_ots((folder / "proof" / "record.json.ots").read_bytes())
assert T.ots_file(d, stamp) == (folder / "proof" / "record.json.ots").read_bytes()
assert not P.due(folder)
print("4. OpenTimestamps: pending at first, then completed with the path to the Bitcoin block, which is checked")

# ---------------------------------------------------------------- certificate and bundle
html = P.certificate_html(folder)
assert "no later than" in html and "Test TSA" in html and "block 840123" in html and rec["files"][0]["sha256"] in html
assert "Bharatiya Sakshya Adhiniyam" in " ".join(html.split()) and "<script" not in html
assert "A. Coauthor" in html and "https://orcid.org/0000-0002-1825-0097" in html and "Group Theory" in html and "MSC 20" in html
assert "am an author of these notes" in html and "Declaration, signed" in html and "(an author)" in html
z = zipfile.ZipFile(io.BytesIO(P.bundle(folder, with_files=False)))
names = set(z.namelist())
assert {"proof/record.json", "proof/record.sig", "proof/public-key.pem", "proof/record.json.ots", "proof/certificate.html"} <= names
assert not any(n.startswith("originals/") for n in names) and "proof/status.json" not in names
assert any(n.startswith("originals/") for n in zipfile.ZipFile(io.BytesIO(P.bundle(folder, with_files=True))).namelist())
print("5. the certificate says what is proved and how to check it; the bundle has every part, the notes only if asked")

# ---------------------------------------------------------------- checking, as a stranger would
bundle = P.bundle(folder, with_files=False)
res = P.verify([("proof.zip", bundle), notes[0]])
assert res["ok"] and res["earliest"] and all(f["ok"] is not False for f in res["findings"]), res
assert any("signature is valid" in f["text"] for f in res["findings"])
assert any("Bitcoin block 840123" in f["text"] for f in res["findings"])
res = P.verify([("proof.zip", bundle), ("my notes.pdf", notes[0][1] + b" ")])
assert not res["ok"] and any("NOT in the record" in f["text"] for f in res["findings"]), "one byte more is another file"
zb = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(bundle)) as zin, zipfile.ZipFile(zb, "w") as zout:
    for n in zin.namelist():
        data = zin.read(n)
        if n == "proof/record.json":
            data = data.replace(b"V. Chhajer", b"Someone Else")
        zout.writestr(n, data)
res = P.verify([("proof.zip", zb.getvalue()), notes[0]])
assert not res["ok"] and any("does NOT match" in f["text"] for f in res["findings"]), res
assert any("different fingerprint" in f["text"] for f in res["findings"]), "a changed record no longer matches its timestamp"
print("6. checking: the right notes pass; one changed byte, or a changed name in the record, is caught")

# ---------------------------------------------------------------- turned off
config.save_settings({"proof_timestamps": False})
f2 = tmp / "projects" / "p2"
P.make_record(f2, notes[:1], {"title": "x", "author": {}})
calls = state["calendar_hits"]
st = P.timestamp(f2)
assert st["tsa"] == [] and st["ots"] is None and state["calendar_hits"] == calls and not P.due(f2)
assert P.summary(f2)["enabled"] is False
config.save_settings({"proof_timestamps": True})
print("7. with timestamps turned off nothing is sent anywhere; the signed record is still made")
srv.shutdown()
print("proof ok")
