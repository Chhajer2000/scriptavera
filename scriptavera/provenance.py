# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Proof that these notes existed, in exactly this form, at a given time, recorded by their author's copy of
Scriptavera.

As soon as notes are uploaded, before anything else is done with them:

1. the original files are kept as they came, byte for byte (the page images made from them are not the original);
2. each file's SHA-256 fingerprint goes into a short record, with the author's name and the time of this computer;
3. the record is signed with the author's own key (Ed25519, made on this computer and kept here);
4. the fingerprint of the record, and only that (32 bytes, from which nothing of the notes can be read), is sent to
   independent timestamping services: an RFC 3161 time-stamp authority, which answers at once with a signed time,
   and OpenTimestamps, which anchors it in the Bitcoin blockchain within hours.

What this proves: that whoever holds these exact files had them no later than the time stamped, and that the key
shown signed the record. What it does not prove by itself: that the signer is the one who first had the ideas
(binding the key to a person is done by publishing its fingerprint, for example on one's ORCID or university page),
or anything about notes that differ by a single byte. Copyright protects the way something is written, never
mathematical ideas as such; priority of ideas is settled by evidence such as this, and by publication.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path

from . import __version__, config
from . import timestamps as T

PROOF = "proof"
ORIGINALS = "originals"
_busy: set[str] = set()
_busy_lock = threading.Lock()


# --------------------------------------------------------------- the key

def identity_dir() -> Path:
    return getattr(config, "IDENTITY_DIR", config.ROOT / "identity")


def identity() -> dict:
    """The author's signing key on this computer, made the first time it is needed. Returns the public part:
    {public (base64), fingerprint, created, pem}."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    d = identity_dir()
    key_file = d / "signing-key.pem"
    with _busy_lock:
        if not key_file.exists():
            d.mkdir(parents=True, exist_ok=True)
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            key_file.write_bytes(pem)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            (d / "README.txt").write_text(
                "signing-key.pem is your private signing key for Scriptavera's records of your notes.\n"
                "Keep a copy somewhere safe (a USB stick). Never share it, never upload it.\n"
                "Records already made stay verifiable without it; new records can be linked to the old ones only with it.\n",
                encoding="utf-8")
            (d / "identity.json").write_text(json.dumps({"created": time.time()}), encoding="utf-8")
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    created = 0.0
    try:
        created = json.loads((d / "identity.json").read_text(encoding="utf-8")).get("created", 0.0)
    except (OSError, ValueError):
        pass
    return {"public": base64.b64encode(raw).decode(), "fingerprint": fingerprint(raw), "created": created, "pem": pem}


def fingerprint(raw_public: bytes) -> str:
    h = hashlib.sha256(raw_public).hexdigest()[:32].upper()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


def _sign(data: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key((identity_dir() / "signing-key.pem").read_bytes(), password=None)
    return key.sign(data)


def verify_signature(record: bytes, signature_b64: str, public_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(base64.b64decode(signature_b64), record)
        return True
    except (InvalidSignature, ValueError):
        return False


# ------------------------------------------------------------ the record

def _safe(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._\-]+", "_", Path(name or "file").name).strip("._") or "file"
    return base[:80]


def _local_iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def settings() -> dict:
    s = config.load_settings()
    on = s.get("proof_timestamps", True)
    if config.mock_mode() and os.environ.get("SCRIPTAVERA_TEST_TIMESTAMPS") != "1":
        on = False                          # the developer switch means no network: nothing is sent anywhere
    return {"timestamps": on, "tsa": s.get("proof_tsa") or T.DEFAULT_TSA, "calendars": s.get("proof_calendars") or T.DEFAULT_CALENDARS}


def make_record(folder: Path, files: list[tuple[str, bytes]], about: dict, kind: str = "notes",
                keep_files: bool = True, note: str = "") -> dict:
    """Keep the files, write the record and sign it. Fast and offline; the timestamps are asked for afterwards.
    `about` has the author ({name, email, affiliation}) and a title."""
    t = time.time()
    folder.mkdir(parents=True, exist_ok=True)
    proof = folder / PROOF
    proof.mkdir(exist_ok=True)
    entries = []
    for i, (name, data) in enumerate(files, 1):
        stored = ""
        if keep_files:
            stored = f"{ORIGINALS}/{i:02d}-{_safe(name)}"
            (folder / ORIGINALS).mkdir(exist_ok=True)
            (folder / stored).write_bytes(data)
        entries.append({"name": Path(name).name[:200], "stored_as": stored, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(), "sha512": hashlib.sha512(data).hexdigest()})
    me = identity()
    author = about.get("author") or {}
    clip = lambda v, n=300: str(v or "").strip()[:n]                     # noqa: E731
    people = [{k: clip(a.get(k)) for k in ("name", "affiliation", "email", "orcid") if clip(a.get(k))}
              for a in (about.get("authors") or ([author] if author.get("name") else [])) if isinstance(a, dict) and clip(a.get("name"))]
    record = {
        "type": "Scriptavera record of research " + ("notes" if kind == "notes" else "work"),
        "kind": kind,
        "version": 2,
        "title": clip(about.get("title")),
        # the same particulars arXiv takes with a submission: who wrote it, who submits it and what they declare,
        # what it is about (abstract, subject classes), and the rights in it
        "authors": people,
        "author": {k: clip(author.get(k)) for k in ("name", "email", "affiliation", "orcid") if author.get(k)},
        "recorded_at_this_computer": _local_iso(t),
        "files": entries,
        "public_key": {"type": "ed25519", "key": me["public"], "fingerprint": me["fingerprint"]},
        "software": f"Scriptavera {__version__}",
        "rights": ("Copyright in these files belongs to their authors. This record grants no licence to anyone; it is "
                   "evidence of what existed, when, and who declared it."),
    }
    by = about.get("recorded_by") or {}
    if by.get("name"):
        record["recorded_by"] = {"name": clip(by["name"], 120), "is_author": bool(by.get("is_author"))}
    if about.get("declaration"):
        record["declaration"] = clip(about["declaration"], 1000)
    if about.get("description"):
        record["description"] = clip(about["description"], 3000)
    if about.get("keywords"):
        record["keywords"] = [clip(k, 100) for k in about["keywords"]][:20]
    if about.get("subjects"):
        record["subjects"] = [{"code": clip(s.get("code"), 40), "name": clip(s.get("name"), 120),
                               "msc": [clip(m, 20) for m in (s.get("msc") or [])][:8]} for s in about["subjects"]][:3]
    if isinstance(about.get("follows"), dict) and about["follows"].get("fingerprint"):
        record["follows"] = {"kind": clip(about["follows"].get("kind"), 20),
                             "fingerprint": clip(about["follows"]["fingerprint"], 64)}
    if note:
        record["note"] = note[:500]
    data = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(data).digest()
    (proof / "record.json").write_bytes(data)
    (proof / "record.sig").write_text(base64.b64encode(_sign(data)).decode() + "\n", encoding="ascii")
    (proof / "public-key.pem").write_text(me["pem"], encoding="ascii")
    status = {"digest": digest.hex(), "created": t, "kind": kind, "tsa": [], "ots": None, "tries": 0, "next_try": t}
    _save_status(folder, status)
    return status


def _status_file(folder: Path) -> Path:
    return folder / PROOF / "status.json"


def _save_status(folder: Path, st: dict) -> None:
    f = _status_file(folder)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def status(folder: Path) -> dict | None:
    f = _status_file(folder)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record_of(folder: Path) -> dict | None:
    f = folder / PROOF / "record.json"
    if not f.exists():
        return None
    return json.loads(f.read_bytes().decode("utf-8"))


# ------------------------------------------------------------ timestamps

def timestamp(folder: Path) -> dict:
    """Ask the services that have not yet answered. Safe to call again: what is done is kept."""
    st = status(folder)
    if not st:
        return {}
    conf = settings()
    if not conf["timestamps"]:
        return st
    digest = bytes.fromhex(st["digest"])
    proof = folder / PROOF
    have = {x["url"] for x in st.get("tsa", []) if x.get("ok")}
    for url in conf["tsa"]:
        if url in have:
            continue
        res = T.request_tsa(url, digest)
        host = re.sub(r"[^A-Za-z0-9.]+", "_", re.sub(r"^https?://", "", url)).strip("_")[:60]
        entry = {"url": url, "ok": res["ok"], "time": res.get("time"), "serial": res.get("serial"), "tsa": res.get("tsa", ""),
                 "issuer": res.get("issuer", ""), "error": res.get("error", ""), "asked": time.time()}
        if res.get("tsr"):
            (proof / f"tsa-{host}.tsq").write_bytes(res["tsq"])
            (proof / f"tsa-{host}.tsr").write_bytes(res["tsr"])
            entry["file"] = f"tsa-{host}.tsr"
        st["tsa"] = [x for x in st.get("tsa", []) if x["url"] != url] + [entry]
    if not (st.get("ots") or {}).get("ok"):
        res = T.stamp_ots(digest, conf["calendars"])
        if res["ok"]:
            (proof / "record.json.ots").write_bytes(res["ots"])
            st["ots"] = {"ok": True, "status": "pending", "calendars": res["calendars"], "submitted": time.time(),
                         "file": "record.json.ots", "errors": res["errors"]}
        else:
            st["ots"] = {"ok": False, "status": "failed", "errors": res["errors"], "asked": time.time()}
    st["tries"] = int(st.get("tries", 0)) + 1
    complete = any(x.get("ok") for x in st.get("tsa", [])) and (st.get("ots") or {}).get("ok")
    st["next_try"] = 0 if complete else time.time() + min(3600 * 6, 300 * 2 ** min(st["tries"], 6))
    _save_status(folder, st)
    return st


def upgrade(folder: Path) -> dict:
    """See whether the OpenTimestamps proof is in a Bitcoin block yet; if it is, keep the finished proof."""
    st = status(folder)
    ots = (st or {}).get("ots") or {}
    f = folder / PROOF / "record.json.ots"
    if not st or not ots.get("ok") or ots.get("status") == "bitcoin" or not f.exists():
        return st or {}
    res = T.upgrade_ots(f.read_bytes())
    ots["checked"] = time.time()
    if res["changed"]:
        f.write_bytes(res["ots"])
    blocks = res["summary"]["bitcoin"]
    if blocks:
        b = min(blocks, key=lambda x: x["height"])
        block = T.bitcoin_block(b["height"])
        ots.update(status="bitcoin", height=b["height"])
        if block:
            ots["verified"] = block["merkle"].lower() == b["merkle"].lower()
            ots["block_time"] = block["time"]
            ots["block_hash"] = block["hash"]
    st["ots"] = ots
    _save_status(folder, st)
    return st


def due(folder: Path) -> bool:
    st = status(folder)
    if not st or not settings()["timestamps"]:
        return False
    ots = st.get("ots") or {}
    if st.get("next_try") and time.time() >= st["next_try"]:
        return True
    return bool(ots.get("ok") and ots.get("status") == "pending" and time.time() - ots.get("submitted", 0) > 3000
                and time.time() - ots.get("checked", 0) > 1800)


def work_later(folder: Path) -> None:
    """Ask for the timestamps (or the Bitcoin confirmation) in the background, once at a time per record."""
    key = str(folder.resolve())
    with _busy_lock:
        if key in _busy:
            return
        _busy.add(key)

    def go() -> None:
        try:
            st = status(folder) or {}
            if st.get("next_try") and time.time() >= st["next_try"]:
                timestamp(folder)
            upgrade(folder)
        except Exception:  # noqa: BLE001 - a network problem now is retried later
            pass
        finally:
            with _busy_lock:
                _busy.discard(key)
    threading.Thread(target=go, daemon=True).start()


# ------------------------------------------------------- what the app shows

def summary(folder: Path) -> dict | None:
    st = status(folder)
    rec = record_of(folder)
    if not st or not rec:
        return None
    tsa = [x for x in st.get("tsa", []) if x.get("ok")]
    ots = st.get("ots") or {}
    return {"digest": st["digest"], "created": st["created"], "kind": st.get("kind", "notes"), "title": rec.get("title", ""),
            "author": rec.get("author", {}), "authors": rec.get("authors") or ([rec["author"]] if rec.get("author") else []),
            "recorded_by": rec.get("recorded_by") or {}, "declaration": rec.get("declaration", ""),
            "description": rec.get("description", ""), "keywords": rec.get("keywords") or [], "subjects": rec.get("subjects") or [],
            "follows": rec.get("follows") or {}, "record_version": rec.get("version", 1),
            "files": [{"name": f["name"], "bytes": f["bytes"], "sha256": f["sha256"]} for f in rec["files"]],
            "fingerprint": rec["public_key"]["fingerprint"], "note": rec.get("note", ""),
            "tsa": [{"url": x["url"], "time": x["time"], "tsa": x.get("tsa", ""), "serial": x.get("serial")} for x in tsa],
            "tsa_failed": [{"url": x["url"], "error": x.get("error", "")} for x in st.get("tsa", []) if not x.get("ok")],
            "ots": {k: ots.get(k) for k in ("status", "calendars", "submitted", "height", "block_time", "verified") if k in ots},
            "enabled": settings()["timestamps"], "earliest": min([x["time"] for x in tsa if x.get("time")] or [0]) or None}


def certificate_html(folder: Path) -> str:
    """A page that says, in plain words, what was recorded, when, by whom, and how anyone can check it."""
    s = summary(folder)
    if not s:
        return "<p>No record.</p>"
    rec = record_of(folder)
    tsr = next((x["file"] for x in (status(folder) or {}).get("tsa", []) if x.get("ok") and x.get("file")),
               "tsa-freetsa.org_tsr.tsr")          # the command below names this record's own answer
    e = html.escape
    when_tsa = "".join(f"<tr><td>Time-stamp authority</td><td><b>{e(T.iso(x['time']))}</b><br><span class=m>{e(x['tsa'] or x['url'])}"
                       f" · serial {e(str(x.get('serial')))} · {e(x['url'])}</span></td></tr>" for x in s["tsa"])
    o = s["ots"]
    if o.get("status") == "bitcoin":
        ots_row = (f"<tr><td>Bitcoin blockchain</td><td><b>in block {e(str(o.get('height')))}</b>"
                   + (f", mined {e(T.iso(o.get('block_time')))}" if o.get("block_time") else "")
                   + (" (checked against the block)" if o.get("verified") else "") + "<br><span class=m>OpenTimestamps proof: record.json.ots</span></td></tr>")
    elif o.get("status") == "pending":
        ots_row = ("<tr><td>Bitcoin blockchain</td><td>submitted to OpenTimestamps " + e(T.iso(o.get("submitted"))) +
                   "; the confirmation in a block usually follows within a few hours and is added automatically when the "
                   "app is open.</td></tr>")
    else:
        ots_row = "<tr><td>Bitcoin blockchain</td><td>not yet submitted (the computer was offline, or this is turned off)</td></tr>"
    files = "".join(f"<tr><td>{e(f['name'])}<br><span class=m>{f['bytes']:,} bytes</span></td><td class=h>{e(f['sha256'])}</td></tr>"
                    for f in rec["files"])
    def person(a: dict) -> str:
        orcid = (f' · ORCID <a href="https://orcid.org/{e(a["orcid"])}">{e(a["orcid"])}</a>' if a.get("orcid") else "")
        return (f"<b>{e(a.get('name') or '')}</b>" + (f", {e(a['affiliation'])}" if a.get("affiliation") else "")
                + (f' <span class=m>&lt;{e(a["email"])}&gt;</span>' if a.get("email") else "") + orcid)
    authors = s.get("authors") or []
    by = s.get("recorded_by") or {}
    if by.get("name"):
        who = e(by["name"]) + (" (an author)" if by.get("is_author") else " (not an author; for the authors, with their consent)")
    else:
        au = s["author"]
        who = e(au.get("name") or "the author named in Scriptavera") + (f" &lt;{e(au['email'])}&gt;" if au.get("email") else "") \
            + (f", {e(au['affiliation'])}" if au.get("affiliation") else "")
    about_rows = ""
    if authors:
        about_rows += f"<tr><td>Author{'s' if len(authors) > 1 else ''}</td><td>{'<br>'.join(person(a) for a in authors)}</td></tr>"
    if s.get("declaration"):
        about_rows += f"<tr><td>Declaration, signed</td><td><blockquote>{e(s['declaration'])}</blockquote></td></tr>"
    if s.get("description"):
        about_rows += f"<tr><td>What the notes are about</td><td>{e(s['description'])}</td></tr>"
    if s.get("subjects"):
        about_rows += ("<tr><td>Subject</td><td>" + "<br>".join(
            f"{e(x.get('name', ''))} <span class=m>({e(x.get('code', ''))}"
            + (f"; MSC {e(', '.join(x.get('msc') or []))}" if x.get("msc") else "") + ")</span>" for x in s["subjects"]) + "</td></tr>")
    if s.get("keywords"):
        about_rows += f"<tr><td>Keywords</td><td>{e(', '.join(s['keywords']))}</td></tr>"
    if (s.get("follows") or {}).get("fingerprint"):
        about_rows += (f"<tr><td>Follows</td><td>the record of the {e(s['follows'].get('kind') or 'notes')} with fingerprint "
                       f"<span class=h>{e(s['follows']['fingerprint'])}</span></td></tr>")
    if rec.get("rights"):
        about_rows += f"<tr><td>Rights</td><td>{e(rec['rights'])}</td></tr>"
    first = s["earliest"]
    head = (f"These {('notes' if s['kind'] == 'notes' else 'files')} existed, exactly as recorded below, no later than "
            f"<b>{e(T.iso(first))}</b>." if first else
            "The record is signed. Independent timestamps are still to be obtained (the computer may have been offline).")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Record of research {('notes' if s['kind'] == 'notes' else 'work')}</title>
<style>
:root{{--ink:#18212b;--muted:#56616b;--rule:#d5dad4;--paper:#fff;--desk:#eceeea;--blue:#2747c7;--green:#2c7a55}}
body{{margin:0;background:var(--desk);color:var(--ink);font:15px/1.55 "Segoe UI",system-ui,-apple-system,Arial,sans-serif}}
main{{max-width:52rem;margin:2rem auto;background:var(--paper);padding:2.2rem 2.6rem;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
h1{{font:400 1.7rem/1.2 Georgia,"Times New Roman",serif;margin:0 0 .3rem}} h2{{font-size:1.05rem;margin:1.8rem 0 .5rem}}
.lead{{font-size:1.08rem;margin:.8rem 0 1.2rem}} table{{border-collapse:collapse;width:100%}}
td{{border-top:1px solid var(--rule);padding:.55rem .6rem;vertical-align:top}} td:first-child{{width:13rem;color:var(--muted)}}
.m{{color:var(--muted);font-size:.86rem}} .h{{font-family:Consolas,"SF Mono",monospace;font-size:.82rem;word-break:break-all}}
code,pre{{font-family:Consolas,"SF Mono",monospace;font-size:.82rem}} pre{{background:#f4f6f3;padding:.8rem;overflow:auto;white-space:pre-wrap}}
.ok{{color:var(--green);font-weight:600}} ol li{{margin:.4rem 0}} a{{color:var(--blue)}}
blockquote{{margin:0;padding:.1rem 0 .1rem .8rem;border-left:3px solid var(--rule);font-style:italic}}
@media (max-width:640px){{main{{margin:0;padding:1.2rem 1rem}} td:first-child{{width:auto}}}}
@media print{{body{{background:#fff}} main{{box-shadow:none;margin:0}}}}
</style></head><body><main>
<h1>Record of research {('notes' if s['kind'] == 'notes' else 'work')}</h1>
<div class=m>{e(s['title'])}</div>
<p class=lead>{head}</p>
<table>
{about_rows}
<tr><td>Recorded by</td><td>{who}<br><span class=m>signing key {e(s['fingerprint'])}</span></td></tr>
<tr><td>This computer's clock</td><td>{e(rec.get('recorded_at_this_computer', ''))} <span class=m>(not proof by itself: a clock can be set)</span></td></tr>
{when_tsa}
{ots_row}
<tr><td>Fingerprint of the record</td><td class=h>{e(s['digest'])}</td></tr>
</table>
{f"<p class=m>{e(s['note'])}</p>" if s.get('note') else ""}
<h2>Files</h2>
<table>{files}</table>
<h2>What this proves</h2>
<p>Whoever holds files with these SHA-256 fingerprints can show that those exact files existed no later than the times
above: the fingerprints are inside the record, and independent services stamped the record's fingerprint. Changing a
single byte of a file changes its fingerprint completely. The record is signed with the key shown; publishing that key's
fingerprint where people know it is yours (your ORCID record, your university page) ties the key to you.</p>
<p>The declaration above is the recorder's own statement, signed with their key and timestamped with the files,
in the way an arXiv submitter states in arXiv's submission agreement that they are an author; neither the timestamping
services nor arXiv check such a statement themselves.</p>
<p>It does not prove by itself who first had the ideas, and copyright protects how something is written, not
mathematical ideas as such. It is evidence, and strong evidence, for priority and against plagiarism. For a legal dispute,
ask a lawyer how electronic evidence is presented where you are (in India, for example, section 63 of the Bharatiya
Sakshya Adhiniyam, 2023 asks for a certificate that includes the record's hash value).</p>
<h2>How anyone can check it, without Scriptavera</h2>
<ol>
<li>Compute the SHA-256 of each file and compare with the list above: <code>certutil -hashfile notes.pdf SHA256</code> (Windows) or <code>sha256sum notes.pdf</code>.</li>
<li>Check that <code>record.json</code> lists those fingerprints and that its own fingerprint is the one above: <code>sha256sum record.json</code>.</li>
<li>Check the time-stamp authority's answer with OpenSSL: <pre>openssl ts -verify -data record.json -in {e(tsr)} -CAfile cacert.pem -untrusted tsa.crt</pre>
(for FreeTSA, cacert.pem and tsa.crt are at https://freetsa.org/files/; for another authority, its certificates).</li>
<li>Check the Bitcoin proof: drop <code>record.json</code> and <code>record.json.ots</code> on https://opentimestamps.org, or run <code>ots verify record.json.ots</code>.</li>
<li>Check the signature with the public key in <code>public-key.pem</code> (Ed25519) over the exact bytes of <code>record.json</code>; the signature is in <code>record.sig</code> (base64).</li>
</ol>
<p class=m>Made by Scriptavera {e(__version__)}. Only fingerprints were sent to the timestamping services; the notes never left this computer for this record.</p>
</main></body></html>"""


def bundle(folder: Path, with_files: bool) -> bytes:
    """Everything needed to check the record, in one zip; with the original files if asked."""
    buf = io.BytesIO()
    proof = folder / PROOF
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(proof.iterdir()):
            if f.is_file() and f.name not in ("status.json",) and not f.name.endswith(".tmp"):
                z.write(f, f"proof/{f.name}")
        z.writestr("proof/certificate.html", certificate_html(folder))
        z.writestr("README.txt", "Open proof/certificate.html: it says what this record proves and how to check it.\n"
                                 "The files in proof/ are the record (record.json), its signature (record.sig), the public key,\n"
                                 "the time-stamp authority's answers (*.tsr) and the OpenTimestamps proof (record.json.ots).\n"
                   + ("The original files are in originals/.\n" if with_files else
                      "The original files are not included: keep them; the record is useless without them.\n"))
        if with_files:
            rec = record_of(folder) or {}
            for f in rec.get("files", []):
                if f.get("stored_as") and (folder / f["stored_as"]).exists():
                    z.write(folder / f["stored_as"], f["stored_as"])
    return buf.getvalue()


# ---------------------------------------------------------------- checking

def verify(files: list[tuple[str, bytes]]) -> dict:
    """Check a record from its parts, the way a third party would: the files (or some of them) and the proof
    (a bundle zip, or record.json with record.sig, *.tsr and record.json.ots). Nothing is trusted from Scriptavera's
    own folders. Returns a list of findings, each {ok, text}."""
    parts: dict[str, bytes] = {}
    notes: list[tuple[str, bytes]] = []
    for name, data in files:
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        if n.endswith("/"):
                            continue
                        base = n.rsplit("/", 1)[-1]
                        if n.startswith("proof/"):
                            parts[base] = z.read(n)
                        elif n.startswith(ORIGINALS + "/"):
                            notes.append((base, z.read(n)))
            except zipfile.BadZipFile:
                return {"ok": False, "findings": [{"ok": False, "text": f"{name} is not a valid zip file."}]}
        elif name in ("record.json", "record.sig", "public-key.pem", "record.json.ots") or name.endswith((".tsr", ".tsq")):
            parts[name] = data
        else:
            notes.append((name, data))
    out: list[dict] = []
    rec_bytes = parts.get("record.json")
    if not rec_bytes:
        return {"ok": False, "findings": [{"ok": False, "text": "No record.json was given: add the proof (the zip Scriptavera made)."}]}
    try:
        rec = json.loads(rec_bytes.decode("utf-8"))
    except ValueError:
        return {"ok": False, "findings": [{"ok": False, "text": "record.json is not readable."}]}
    digest = hashlib.sha256(rec_bytes).digest()
    out.append({"ok": True, "text": f"The record's fingerprint is {digest.hex()}."})
    sig = (parts.get("record.sig") or b"").decode("ascii", "ignore").strip()
    pub = (rec.get("public_key") or {}).get("key", "")
    if sig and pub:
        good = verify_signature(rec_bytes, sig, pub)
        out.append({"ok": good, "text": ("The signature is valid" if good else "The signature does NOT match the record")
                    + f" (key {rec['public_key'].get('fingerprint', '')})."})
    else:
        out.append({"ok": False, "text": "The record has no signature to check."})
    listed = {f["sha256"]: f for f in rec.get("files", [])}
    for name, data in notes:
        h = hashlib.sha256(data).hexdigest()
        if h in listed:
            out.append({"ok": True, "text": f"{name}: its fingerprint is in the record (recorded as “{listed[h]['name']}”)."})
        else:
            out.append({"ok": False, "text": f"{name}: its fingerprint {h[:16]}… is NOT in the record. It is not the file that was recorded."})
    if not notes:
        out.append({"ok": None, "text": "No notes were given to compare: the record shows only that files with the listed "
                                        "fingerprints existed."})
    times = []
    for name, data in sorted(parts.items()):
        if not name.endswith(".tsr"):
            continue
        try:
            v = T.verify_token(data, digest)
        except (ValueError, IndexError) as e:
            out.append({"ok": False, "text": f"{name}: not a readable timestamp ({e})."})
            continue
        if v["ok"]:
            times.append(v["time"])
            out.append({"ok": True, "text": f"{name}: {v['tsa']} signed that the record existed at {T.iso(v['time'])}."})
        else:
            out.append({"ok": False, "text": f"{name}: " + "; ".join(v["problems"]) + "."})
    ots = parts.get("record.json.ots")
    if ots:
        try:
            s = T.ots_summary(ots)
            if s["digest"] != digest.hex():
                out.append({"ok": False, "text": "record.json.ots is for a different record."})
            elif s["bitcoin"]:
                b = min(s["bitcoin"], key=lambda x: x["height"])
                block = T.bitcoin_block(b["height"])
                if block and block["merkle"].lower() == b["merkle"].lower():
                    times.append(block["time"])
                    out.append({"ok": True, "text": f"Bitcoin block {b['height']} (mined {T.iso(block['time'])}) contains the record's fingerprint."})
                else:
                    out.append({"ok": None, "text": f"The OpenTimestamps proof points to Bitcoin block {b['height']}; it could not "
                                                    "be compared with the block from here. Check it at opentimestamps.org."})
            else:
                out.append({"ok": None, "text": "The OpenTimestamps proof is still waiting for its Bitcoin block."})
        except ValueError as e:
            out.append({"ok": False, "text": f"record.json.ots: {e}."})
    ok = all(f["ok"] is not False for f in out) and bool(times)
    return {"ok": ok, "earliest": min(times) if times else None, "title": rec.get("title", ""), "author": rec.get("author", {}),
            "findings": out}
