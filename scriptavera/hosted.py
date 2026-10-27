# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The hosted service on top of the app: who is asking, what they may see, what is free, and where their work is
kept. Used only when hosting.public() is true; the desktop app never reaches this module's checks.

Who is asking. Every browser gets a signed guest cookie; signing in with Google adds a signed session cookie.
A paper belongs to the account that made it, or, before signing in, to the browser that made it. Nobody can open,
list or download anyone else's paper (a paper that is not yours is "not found"). The settings, the AI keys and the
test mode belong to the people named in SCRIPTAVERA_ADMIN_EMAILS.

What is free. Three papers per network for guests (counted by the visitor's address, from X-Forwarded-For), and
three per account in all (papers made as a guest in the same browser count, and become the account's). After that a
subscription is needed.

Records. Signed, RFC 3161 timestamped records of notes are made only for people who are signed in and have
connected Google Drive; each record is also copied to the audit ledger in Cloud Storage.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import accounts, cloudstore, config, hosting, memory, provenance, store, tenancy

# The opt-in for the test suite, word for word as the page shows it. Change CONSENT_VERSION whenever the words change:
# each paper records which version its author agreed to, and when.
CONSENT_VERSION = "2026-10-27"
DATASET_LABEL = ("Help improve Scriptavera: let the Scriptavera team use these notes, with your name removed, to test "
                 "how well it reads handwritten mathematics.")
DATASET_DETAILS = [
    "This is optional, and the box stays unticked unless you tick it.",
    "What is kept: the page images of these notes, the text read from them and the LaTeX of the paper. Your name, "
    "e-mail address, affiliation and ORCID iD are removed first, and the copy is filed under a random number, not "
    "under your name.",
    "Who sees it: only the Scriptavera team, to measure and improve how accurately formulas are read. It is never "
    "published, never sold, never shared with any other company or person, and never used to train AI models.",
    "Your research stays yours: we will never use, quote or claim the mathematics in your notes.",
    "You can change your mind at any time: “Withdraw my notes from testing” in your account deletes every copy.",
    "If you leave it unticked, your notes are used only to make your paper, and the uploaded files, page images and "
    "crops are deleted from our server once you download the finished paper.",
]
PROJECT_PATH = re.compile(r"^/(?:api/projects|editor)/([A-Za-z0-9_-]+)(?:/|$)")
ADMIN_PATHS = ("/api/settings", "/api/test_mode", "/api/admin/")
CLOSED_PATHS = ("/api/phone", "/api/shutdown")             # a phone on the same Wi-Fi; stopping the server


def _json(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def fail(message: str, status: int = 400, **kw) -> JSONResponse:
    return _json({"ok": False, "error": message, **kw}, status)


# ------------------------------------------------------------------ caches (one server instance)

_paper_cache: dict[str, tuple[float, dict | None]] = {}
_user_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()
TTL = 30.0


def paper_row(pid: str) -> dict | None:
    with _cache_lock:
        hit = _paper_cache.get(pid)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    row = accounts.db().get_paper(pid)
    with _cache_lock:
        _paper_cache[pid] = (time.time(), row)
    return row


def forget_paper(pid: str) -> None:
    with _cache_lock:
        _paper_cache.pop(pid, None)


def user_row(uid: str | None, fresh: bool = False) -> dict | None:
    if not uid:
        return None
    with _cache_lock:
        hit = _user_cache.get(uid)
        if hit and not fresh and time.time() - hit[0] < TTL:
            return hit[1]
    row = accounts.db().get_user(uid)
    with _cache_lock:
        _user_cache[uid] = (time.time(), row)
    return row


def forget_user(uid: str) -> None:
    with _cache_lock:
        _user_cache.pop(uid, None)


# ------------------------------------------------------------------ who is asking

def identity(request_or_scope) -> dict:
    scope = request_or_scope.scope if isinstance(request_or_scope, Request) else request_or_scope
    return scope.get("scriptavera") or {}


def _resolve(scope: dict, headers: dict) -> dict:
    cookies = hosting.parse_cookies(headers.get("cookie", ""))
    session = hosting.unsign(cookies.get(hosting.SESSION_COOKIE, "")) or {}
    guest = hosting.unsign(cookies.get(hosting.GUEST_COOKIE, "")) or {}
    gid, new_guest = guest.get("g"), False
    if not gid:
        gid, new_guest = hosting.new_guest_id(), True
    peer = (scope.get("client") or ("", 0))[0] or ""
    ip = hosting.client_ip(headers, peer)
    email = str(session.get("email") or "").lower()
    return {"uid": session.get("uid"), "email": email, "guest": gid, "new_guest": new_guest, "ip": ip,
            "ip_key": hosting.ip_key(ip), "admin": bool(email and email in hosting.admins()),
            "secure": headers.get("x-forwarded-proto", "") == "https" or scope.get("scheme") == "https"}


def owner_of(ident: dict) -> str:
    return f"u:{ident['uid']}" if ident.get("uid") else f"g:{ident.get('guest')}"


def owns(ident: dict, row: dict | None) -> bool:
    if not row or row.get("deleted_at"):
        return False
    if row.get("owner_user_id"):
        return bool(ident.get("uid")) and row["owner_user_id"] == ident["uid"]
    return bool(row.get("guest_id")) and row["guest_id"] == ident.get("guest")


def _restore_if_needed(pid: str, row: dict) -> None:
    folder = config.PROJECTS_DIR / pid
    if row.get("owner_user_id") and not (folder / "state.json").exists():
        cloudstore.restore_progress(row["owner_user_id"], pid, folder)


async def gate(scope: dict, headers: dict) -> tuple[JSONResponse | None, list[tuple[bytes, bytes]]]:
    """Run before every request of the hosted service. Returns (a refusal or None, headers to add)."""
    ident = _resolve(scope, headers)
    scope["scriptavera"] = ident
    tenancy.set_owner(owner_of(ident))               # this request uses its own person's keys and memory
    extra: list[tuple[bytes, bytes]] = [
        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"cross-origin-opener-policy", b"same-origin-allow-popups"),     # Google's sign-in window must answer
    ]
    if ident["new_guest"]:
        extra.append((b"set-cookie", hosting.cookie_header(hosting.GUEST_COOKIE, hosting.sign({"g": ident["guest"]}, 365),
                                                           365 * 86400, ident["secure"])))
    path = scope.get("path", "")
    if any(path.startswith(p) for p in CLOSED_PATHS):
        return fail("Not available on scriptavera.com.", 404), extra
    if any(path.startswith(p) for p in ADMIN_PATHS) and not ident["admin"]:
        return fail("Only the administrators of scriptavera.com can change this.", 403), extra
    m = PROJECT_PATH.match(path)
    if m:
        pid = m.group(1)
        row = await run_in_threadpool(paper_row, pid)
        if not (owns(ident, row) or (ident["admin"] and row)):
            return fail("Project not found.", 404), extra
        await run_in_threadpool(_restore_if_needed, pid, row)
    return None, extra


# ------------------------------------------------------------------ the free plan and subscriptions

def _subscription(uid: str) -> dict | None:
    return accounts.db().get_subscription(uid)


def network_limit() -> int:
    """How many guest papers one network (one address) may start in a month, however many people share it: a
    university, a library or an office is one address for hundreds of people. Default 25."""
    try:
        return max(1, int(hosting.env("SCRIPTAVERA_GUEST_NETWORK_LIMIT", "25")))
    except ValueError:
        return 25


BROWSER_LIMIT_MESSAGE = ("Free guest limit of three papers reached in this browser. "
                         "Please sign in with Google to continue.")


def usage(ident: dict) -> dict:
    d = accounts.db()
    limit = hosting.free_papers()
    browser = d.guest_browser_used(ident["guest"]) if ident.get("guest") else 0
    network = d.guest_used(ident["ip_key"])
    out = {"free_limit": limit, "guest_used": browser, "network_used": network, "network_limit": network_limit(),
           "signed_in": bool(ident.get("uid"))}
    out["guest_remaining"] = max(0, min(limit - browser, network_limit() - network))
    if ident.get("uid"):
        u = user_row(ident["uid"]) or {}
        sub = _subscription(ident["uid"])
        out.update(user_free_used=int(u.get("free_papers_used") or 0), subscribed=accounts.subscribed(sub),
                   subscription={k: (sub or {}).get(k) for k in ("status", "current_period_end", "plan")} if sub else None)
        out["user_free_remaining"] = max(0, limit - out["user_free_used"])
    return out


def _paywall_message() -> str:
    price = hosting.env("SCRIPTAVERA_PRICE_LABEL")
    return ("You have used your three free papers. To continue, subscribe to Scriptavera"
            + (f" ({price})" if price else " for a small monthly fee") + ".")


def precheck(request: Request) -> JSONResponse | None:
    """Before anything is done with an upload: is there a free paper left (or a subscription)?"""
    ident = identity(request)
    d = accounts.db()
    if ident.get("uid"):
        u = user_row(ident["uid"], fresh=True)
        if not u:
            return fail("Your session has ended. Please sign in again.", 401, code="signin")
        if accounts.subscribed(_subscription(ident["uid"])):
            return None
        if int(u.get("free_papers_used") or 0) >= hosting.free_papers():
            return fail(_paywall_message(), 402, code="paywall")
        return None
    if ident.get("guest") and d.guest_browser_used(ident["guest"]) >= hosting.free_papers():
        return fail(BROWSER_LIMIT_MESSAGE, 403, code="guest_limit")
    if d.guest_used(ident["ip_key"]) >= network_limit():
        return fail(hosting.GUEST_LIMIT_MESSAGE, 403, code="guest_limit")
    return None


def keys_ready() -> JSONResponse | None:
    """Before an upload: a model that can read handwriting, from this person's own keys (or the service's)."""
    from .llm import NO_KEYS, router
    if any(m.vision and not m.dead for m in router.models):
        return None
    return fail(NO_KEYS.split(" Your keys")[0] + " A free Google Gemini key is enough to start; a paid one keeps your "
                "unpublished notes private.", 400, code="nokeys")


def claim(request: Request):
    """Take one free paper (atomically). Returns (refusal or None, a function that gives it back)."""
    ident = identity(request)
    d = accounts.db()
    if ident.get("uid"):
        if accounts.subscribed(_subscription(ident["uid"])):
            return None, (lambda: None)
        allowed, _ = d.claim_user(ident["uid"], hosting.free_papers())
        forget_user(ident["uid"])
        if not allowed:
            return fail(_paywall_message(), 402, code="paywall"), (lambda: None)
        return None, (lambda: (d.release_user(ident["uid"]), forget_user(ident["uid"])))
    if ident.get("guest") and d.guest_browser_used(ident["guest"]) >= hosting.free_papers():
        return fail(BROWSER_LIMIT_MESSAGE, 403, code="guest_limit"), (lambda: None)
    allowed, _ = d.claim_guest(ident["ip_key"], network_limit())
    if not allowed:
        return fail(hosting.GUEST_LIMIT_MESSAGE, 403, code="guest_limit"), (lambda: None)
    return None, (lambda: d.release_guest(ident["ip_key"]))


def can_sign(request: Request) -> tuple[bool, str]:
    """Signed records (and their RFC 3161 timestamps) are made for signed-in people with Google Drive connected."""
    ident = identity(request)
    if not ident.get("uid"):
        return False, ("No signed record was made: guests get no cryptographic timestamps. Sign in with Google and "
                       "connect your Google Drive to have your unpublished notes signed, timestamped and preserved.")
    u = user_row(ident["uid"], fresh=True) or {}
    if not u.get("drive_connected"):
        return False, ("No signed record was made: connect your Google Drive (Account menu) to have your notes "
                       "signed, timestamped and preserved in your own Drive.")
    return True, ""


def lock_record(folder: Path, reason: str) -> None:
    proof = folder / "proof"
    proof.mkdir(parents=True, exist_ok=True)
    (proof / "locked.json").write_text(json.dumps({"reason": reason, "at": time.time()}), encoding="utf-8")


def locked_reason(folder: Path) -> str:
    f = folder / "proof" / "locked.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("reason", "") if f.exists() else ""
    except (OSError, ValueError):
        return ""


def register(request: Request, pid: str, meta: dict, opt_in: bool, signed: bool) -> None:
    """The paper is this person's (or this browser's), and its authors join the person's collaborators."""
    ident = identity(request)
    d = accounts.db()
    d.add_paper({"id": pid, "owner_user_id": ident.get("uid"), "guest_id": ident.get("guest"),
                 "ip_hash": ident.get("ip_key"), "name": meta.get("name") or "", "title": meta.get("title") or "",
                 "domains": meta.get("domains") or [], "authors": meta.get("authors") or [],
                 "dataset_opt_in": bool(opt_in), "signed": bool(signed), "status": "started",
                 **({"dataset_consent_at": accounts.now_iso(), "dataset_consent_version": CONSENT_VERSION} if opt_in else {})})
    forget_paper(pid)
    if ident.get("uid"):
        _remember(ident["uid"], pid, meta.get("authors") or [])


def _remember(uid: str, pid: str, authors: list[dict]) -> None:
    d = accounts.db()
    u = user_row(uid, fresh=True) or {}
    email = (u.get("email") or "").lower()
    d.remember_collaborators(uid, pid, authors, email)
    me = next((a for a in authors if str(a.get("email") or "").lower() == email), None)
    if me:                       # the person's own ORCID iD and affiliation, kept when they give them
        fields = {}
        if me.get("orcid") and not u.get("orcid"):
            fields["orcid"] = me["orcid"]
        if me.get("affiliation") and not u.get("affiliation"):
            fields["affiliation"] = me["affiliation"][:300]
        if fields:
            d.update_user(uid, fields)
            forget_user(uid)


def visible(request: Request, projects: list[dict]) -> list[dict]:
    ident = identity(request)
    d = accounts.db()
    rows = d.papers_for(user_id=ident["uid"]) if ident.get("uid") else d.papers_for(guest_id=ident.get("guest"))
    mine = set()
    for r in rows:
        mine.add(r["id"])
        if r.get("owner_user_id") and not (config.PROJECTS_DIR / r["id"] / "state.json").exists():
            cloudstore.restore_progress(r["owner_user_id"], r["id"], config.PROJECTS_DIR / r["id"])
    have = {p["id"] for p in projects}
    if mine - have:
        projects = store.list_projects()
    return [p for p in projects if p["id"] in mine]


def deleted(pid: str) -> None:
    d = accounts.db()
    row = d.get_paper(pid)
    d.update_paper(pid, {"deleted_at": accounts.now_iso()})
    forget_paper(pid)
    if row and row.get("owner_user_id"):
        cloudstore.delete_progress(row["owner_user_id"], pid)


def downloaded(pid: str, name: str) -> None:
    if name in ("paper.zip", "archive") and not (paper_row(pid) or {}).get("downloaded_at"):
        accounts.db().update_paper(pid, {"downloaded_at": accounts.now_iso()})
        forget_paper(pid)


# ------------------------------------------------------------------ the archive (download, Google Drive)

def paper_zip_with_proof(p: store.Project) -> bytes | None:
    """paper.zip with the record of the notes inside it: record.json, its signature, the time-stamp authority's
    receipts (.tsr, about 4 KB each), the OpenTimestamps proof and SHA256SUMS.txt."""
    z = p.dir / "out" / "paper.zip"
    if not z.exists():
        return None
    extra = cloudstore.proof_files(p.dir) if provenance.status(p.dir) else {}
    if not extra:
        return z.read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            out.writestr(item, src.read(item.filename))
        for name, data in extra.items():
            out.writestr(name, data)
        out.writestr("proof/certificate.html", provenance.certificate_html(p.dir))
    return buf.getvalue()


def archive(p: store.Project) -> bytes:
    """Everything about one paper, for the author's own Google Drive: the paper (PDF, LaTeX, Overleaf package),
    the signed record with its timestamps, and the original notes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = p.dir / "out"
        for f in sorted(out.iterdir()) if out.is_dir() else []:
            if f.is_file() and f.name != "paper.zip":
                z.write(f, f"paper/{f.name}")
        pz = paper_zip_with_proof(p)
        if pz:
            z.writestr("paper/paper.zip", pz)
        for name, data in (cloudstore.proof_files(p.dir) if provenance.status(p.dir) else {}).items():
            z.writestr(name, data)
        if provenance.status(p.dir):
            z.writestr("proof/certificate.html", provenance.certificate_html(p.dir))
        notes = p.dir / provenance.ORIGINALS
        if notes.is_dir() and any(notes.iterdir()):
            for f in sorted(notes.iterdir()):
                if f.is_file():
                    z.write(f, f"notes/{f.name}")
        else:
            for f in sorted((p.dir / "pages").glob("p*.jpg")):
                z.write(f, f"notes/{f.name}")
        meta = p.state.get("meta", {})
        z.writestr("README.txt", "Scriptavera archive of \"" + (meta.get("title") or meta.get("name") or p.id) + "\"\n\n"
                   "paper/   the paper: PDF, LaTeX and the package for Overleaf\n"
                   "proof/   the signed record of your notes, the RFC 3161 receipts (*.tsr), the OpenTimestamps proof\n"
                   "         and SHA256SUMS.txt; open proof/certificate.html to see what it proves and how to check it\n"
                   "notes/   your notes as you uploaded them\n")
    return buf.getvalue()


# ------------------------------------------------------------------ signatures: the audit ledger

def _on_stamped(folder: Path, st: dict) -> None:
    """Called by provenance whenever a record gets a timestamp: the receipts go to the permanent ledger."""
    try:
        rel = folder.resolve().relative_to(config.PROJECTS_DIR.resolve()).parts
    except ValueError:
        return
    if not rel:
        return
    pid = rel[0]
    kind = "notes" if len(rel) == 1 else f"version-{rel[-1]}"
    prefix = cloudstore.save_signatures(pid, kind, folder)
    tsa = next((x for x in st.get("tsa", []) if x.get("ok")), None)
    d = accounts.db()
    if tsa:
        d.add_signature({"paper_id": pid, "kind": kind, "digest": st.get("digest", ""), "tsa_url": tsa.get("url"),
                         "tsa_time": dt.datetime.fromtimestamp(tsa["time"], dt.timezone.utc).isoformat() if tsa.get("time") else None,
                         "gcs_prefix": prefix})
        if kind == "notes":
            d.update_paper(pid, {"signed": True})
            forget_paper(pid)


# ------------------------------------------------------------------ the janitor

def _finished(p: store.Project) -> bool:
    return (p.state.get("stages", {}).get("finish") or {}).get("status") == "done"


def tidy_once() -> None:
    """Checkpoints of signed-in people's work, the opted-in test suite, the purge of handwritten material after a
    finished paper is downloaded, and the end of abandoned guest papers."""
    d = accounts.db()
    grace = float(hosting.env("SCRIPTAVERA_PURGE_GRACE_MINUTES", "15")) * 60
    guest_ttl = float(hosting.env("SCRIPTAVERA_GUEST_TTL_HOURS", "72")) * 3600
    for info in store.list_projects():
        pid = info["id"]
        try:
            row = paper_row(pid)
            p = store.get(pid)
            if not row or not p:
                continue
            uid = row.get("owner_user_id")
            if row.get("dataset_opt_in") and _finished(p) and not row.get("dataset_saved_at"):
                did = cloudstore.contribute(p.dir, p.state.get("meta", {}))
                if did:
                    d.update_paper(pid, {"dataset_id": did, "dataset_saved_at": accounts.now_iso()})
                    forget_paper(pid)
            if not row.get("dataset_opt_in") and row.get("downloaded_at") and _finished(p) and not row.get("purged_at"):
                when = accounts.parse_iso(row["downloaded_at"])
                if when and time.time() >= when.timestamp() + grace:
                    cloudstore.purge_raw(p.dir)
                    if uid:
                        cloudstore.delete_progress(uid, pid, only_raw=True)
                    d.update_paper(pid, {"purged_at": accounts.now_iso()})
                    forget_paper(pid)
            if uid:
                cloudstore.sync_progress(uid, pid, p.dir)
            elif time.time() - float(info.get("updated") or info.get("created") or time.time()) > guest_ttl:
                store.delete(pid)                       # a guest's paper nobody came back to: it only used memory
                d.update_paper(pid, {"deleted_at": accounts.now_iso()})
                forget_paper(pid)
        except Exception:  # noqa: BLE001 - one paper's problem must not stop the others
            continue


_janitor_started = False


def start() -> None:
    global _janitor_started
    if _on_stamped not in provenance.LISTENERS:
        provenance.LISTENERS.append(_on_stamped)
    if _janitor_started:
        return
    _janitor_started = True
    every = float(hosting.env("SCRIPTAVERA_SYNC_SECONDS", "30"))

    def loop():
        while True:
            time.sleep(every)
            tidy_once()
    threading.Thread(target=loop, daemon=True, name="scriptavera-janitor").start()


# ------------------------------------------------------------------ the routes of the hosted service

async def _body(request: Request) -> dict:
    try:
        data = json.loads(await request.body() or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def account_payload(ident: dict) -> dict:
    out = {"ok": True, "public": True, "google_client_id": hosting.google_client_id(), "admin": ident.get("admin", False),
           "price_label": hosting.env("SCRIPTAVERA_PRICE_LABEL"), "payments": _payments_ready(),
           "storage": bool(cloudstore.bucket()), "dataset_label": DATASET_LABEL, "dataset_details": DATASET_DETAILS,
           "guest_limit_message": hosting.GUEST_LIMIT_MESSAGE, "browser_limit_message": BROWSER_LIMIT_MESSAGE, **usage(ident)}
    with tenancy.owner(owner_of(ident)):
        out["memory"] = memory.stats()
    d = accounts.db()
    out["contributions"] = len(d.contributions(user_id=ident["uid"]) if ident.get("uid") else d.contributions(guest_id=ident.get("guest")))
    if ident.get("uid"):
        u = user_row(ident["uid"]) or {}
        out["user"] = {k: u.get(k) for k in ("email", "full_name", "given_name", "picture_url", "orcid", "affiliation",
                                              "department", "country", "homepage", "drive_connected",
                                              "dataset_opt_in_default")}
    return out


async def api_account(request: Request):
    ident = identity(request)
    return _json(await run_in_threadpool(account_payload, ident))


async def api_auth_google(request: Request):
    body = await _body(request)
    ident = identity(request)
    try:
        info = await run_in_threadpool(accounts.verify_google_credential, str(body.get("credential") or ""))
    except accounts.AccountError as e:
        return fail(str(e), 401)
    d = accounts.db()
    user = await run_in_threadpool(d.upsert_user, accounts.profile_fields(info))
    adopted = await run_in_threadpool(d.adopt, ident.get("guest"), user["id"]) if ident.get("guest") else 0
    if adopted:                                   # the guest papers' authors become collaborators too
        for row in await run_in_threadpool(d.papers_for, user["id"]):
            await run_in_threadpool(_remember, user["id"], row["id"], row.get("authors") or [])
            forget_paper(row["id"])
    forget_user(user["id"])
    from . import llm
    guest_owner, user_owner = f"g:{ident.get('guest')}", f"u:{user['id']}"
    llm.move_person_keys(guest_owner, user_owner)                 # what the guest gave is now the account's
    await run_in_threadpool(memory.merge_guest, guest_owner, user_owner)
    ident = {**ident, "uid": user["id"], "email": user["email"], "admin": user["email"] in hosting.admins()}
    res = _json({**await run_in_threadpool(account_payload, ident), "adopted": adopted})
    res.raw_headers.append((b"set-cookie", hosting.cookie_header(
        hosting.SESSION_COOKIE, hosting.sign({"uid": user["id"], "email": user["email"]}), hosting.SESSION_DAYS * 86400,
        ident.get("secure", False))))
    return res


async def api_logout(request: Request):
    from . import llm
    ident = identity(request)
    if ident.get("uid"):
        llm.drop_person_keys(f"u:{ident['uid']}")                 # signing out: the server forgets the keys
    res = _json({"ok": True})
    res.raw_headers.append((b"set-cookie", hosting.cookie_header(hosting.SESSION_COOKIE, "", 0, identity(request).get("secure", False))))
    return res


def _need_user(request: Request):
    ident = identity(request)
    return (ident, None) if ident.get("uid") else (ident, fail("Sign in with Google first.", 401, code="signin"))


async def api_profile(request: Request):
    from .server import orcid_id
    ident, refusal = _need_user(request)
    if refusal:
        return refusal
    body = await _body(request)
    fields = {}
    if "orcid" in body:
        raw = str(body.get("orcid") or "").strip()
        oid = orcid_id(raw) if raw else None
        if raw and not oid:
            return fail("That ORCID iD is not valid: check its 16 digits (it looks like 0000-0002-1825-0097).")
        fields["orcid"] = oid
    for k, n in (("affiliation", 300), ("department", 200), ("country", 80), ("homepage", 300)):
        if k in body:
            fields[k] = str(body.get(k) or "").strip()[:n] or None
    if "dataset_opt_in_default" in body:
        fields["dataset_opt_in_default"] = bool(body["dataset_opt_in_default"])
    await run_in_threadpool(accounts.db().update_user, ident["uid"], fields)
    forget_user(ident["uid"])
    return _json(await run_in_threadpool(account_payload, ident))


async def api_drive(request: Request):
    """POST: the page connected Google Drive (drive.file); checked with Google. DELETE: disconnected."""
    ident, refusal = _need_user(request)
    if refusal:
        return refusal
    d = accounts.db()
    if request.method == "DELETE":
        await run_in_threadpool(d.update_user, ident["uid"], {"drive_connected": False})
    else:
        body = await _body(request)
        try:
            info = await run_in_threadpool(accounts.verify_drive_token, str(body.get("access_token") or ""))
        except accounts.AccountError as e:
            return fail(str(e), 400)
        await run_in_threadpool(d.update_user, ident["uid"], {"drive_connected": True, "drive_connected_at": accounts.now_iso(),
                                                              "drive_email": str(info.get("email") or "")[:200] or None})
    forget_user(ident["uid"])
    return _json(await run_in_threadpool(account_payload, ident))


async def api_collaborators(request: Request):
    ident, refusal = _need_user(request)
    if refusal:
        return _json({"ok": True, "collaborators": []})
    q = re.sub(r"\s+", " ", request.query_params.get("q") or "").strip()[:80]
    found = await run_in_threadpool(accounts.db().search_collaborators, ident["uid"], q, 8)
    return _json({"ok": True, "collaborators": [{k: c.get(k) for k in ("full_name", "email", "affiliation", "orcid",
                                                                       "times_collaborated")} for c in found]})


async def api_archive(request: Request):
    p = store.get(request.path_params.get("pid", ""))
    if not p:
        return Response(status_code=404)
    data = await run_in_threadpool(archive, p)
    await run_in_threadpool(downloaded, p.id, "archive")
    name = re.sub(r"[^\w.-]+", "-", (p.state.get("meta", {}).get("name") or p.id))[:60].strip("-") or p.id
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-scriptavera-archive.zip"',
                             "Cache-Control": "no-store"})


async def api_drive_saved(request: Request):
    """The page saved the archive in the author's Drive: remember the file, for the author's own reference."""
    ident, refusal = _need_user(request)
    if refusal:
        return refusal
    body = await _body(request)
    pid = request.path_params.get("pid", "")
    await run_in_threadpool(accounts.db().update_paper, pid, {"drive_file_id": str(body.get("file_id") or "")[:200]})
    forget_paper(pid)
    return _json({"ok": True})


async def api_whoami(request: Request):
    """For the administrators, when setting up: the address as received and as understood."""
    ident = identity(request)
    return _json({"ok": True, "x_forwarded_for": request.headers.get("x-forwarded-for", ""), "ip": ident.get("ip"),
                  "proxy_hops": hosting.env("SCRIPTAVERA_PROXY_HOPS", "1"), "host": request.headers.get("host", ""),
                  "database": accounts.db().kind, "storage": bool(cloudstore.bucket()),
                  "session_secret": hosting.secret_configured()})


# ------------------------------------------------------------------ subscriptions (Razorpay)

def _payments_ready() -> bool:
    return bool((hosting.env("RAZORPAY_KEY_ID") and hosting.env("RAZORPAY_KEY_SECRET") and hosting.env("RAZORPAY_PLAN_ID"))
                or hosting.env("SCRIPTAVERA_CHECKOUT_URL"))


async def api_checkout(request: Request):
    """A subscription for the signed-in person. With Razorpay keys, a subscription is created carrying the
    person's account id, and they are sent to Razorpay's page to pay; the webhook then turns it on."""
    ident, refusal = _need_user(request)
    if refusal:
        return refusal
    if hosting.env("RAZORPAY_KEY_ID") and hosting.env("RAZORPAY_KEY_SECRET") and hosting.env("RAZORPAY_PLAN_ID"):
        import requests

        def create():
            r = requests.post("https://api.razorpay.com/v1/subscriptions", timeout=20,
                              auth=(hosting.env("RAZORPAY_KEY_ID"), hosting.env("RAZORPAY_KEY_SECRET")),
                              json={"plan_id": hosting.env("RAZORPAY_PLAN_ID"), "total_count": 120, "customer_notify": 1,
                                    "notes": {"user_id": ident["uid"], "email": ident.get("email", "")}})
            r.raise_for_status()
            return r.json()
        try:
            sub = await run_in_threadpool(create)
        except Exception:  # noqa: BLE001
            return fail("The payment page could not be opened. Please try again in a minute.", 502)
        await run_in_threadpool(accounts.db().set_subscription, ident["uid"],
                                {"provider": "razorpay", "provider_subscription_id": sub.get("id"), "status": "created",
                                 "plan": hosting.env("RAZORPAY_PLAN_ID")})
        return _json({"ok": True, "url": sub.get("short_url")})
    url = hosting.env("SCRIPTAVERA_CHECKOUT_URL")
    if url:
        return _json({"ok": True, "url": url})
    return fail("Subscriptions are not open yet. Please write to us to continue.", 503)


RAZORPAY_STATUS = {"subscription.authenticated": "authenticated", "subscription.activated": "active",
                   "subscription.charged": "active", "subscription.resumed": "active", "subscription.pending": "pending",
                   "subscription.halted": "halted", "subscription.paused": "paused", "subscription.cancelled": "cancelled",
                   "subscription.completed": "completed", "subscription.expired": "expired"}


async def api_webhook(request: Request):
    """Razorpay tells us a subscription changed. The body is signed with the webhook secret (HMAC-SHA256); anything
    not signed is refused, and each event is applied once."""
    raw = await request.body()
    secret = hosting.env("RAZORPAY_WEBHOOK_SECRET")
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(want, request.headers.get("x-razorpay-signature", "")):
        return fail("Bad signature.", 400)
    try:
        event = json.loads(raw)
    except ValueError:
        return fail("Bad body.", 400)
    kind = str(event.get("event") or "")
    sub = (((event.get("payload") or {}).get("subscription") or {}).get("entity")) or {}
    uid = (sub.get("notes") or {}).get("user_id")
    event_id = request.headers.get("x-razorpay-event-id") or hashlib.sha256(raw).hexdigest()
    d = accounts.db()
    if not await run_in_threadpool(d.billing_event_new, event_id, "razorpay", uid, kind, event):
        return _json({"ok": True, "duplicate": True})
    if uid and kind in RAZORPAY_STATUS:
        end = sub.get("current_end")
        await run_in_threadpool(d.set_subscription, uid, {
            "provider": "razorpay", "provider_subscription_id": sub.get("id"), "plan": sub.get("plan_id"),
            "status": RAZORPAY_STATUS[kind],
            "current_period_end": dt.datetime.fromtimestamp(int(end), dt.timezone.utc).isoformat() if end else None})
    return _json({"ok": True})


# ------------------------------------------------------------------ a person's own AI keys

KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-.:]{12,300}$")


def _keys_payload(owner: str, given: dict[str, list[str]] | None = None) -> dict:
    from . import llm
    r = llm.person_router(owner)
    providers = []
    for pid in llm.BYOK:
        p = llm.PROVIDERS[pid]
        st = (r.status.get(pid) if r else None) or {}
        keys = list(st.get("keys") or [])
        mine = (given or {}).get(pid) or []
        for k in keys:
            raw = mine[k["n"] - 1] if 0 < k["n"] <= len(mine) else ""
            k["masked"] = (raw[:4] + "…" + raw[-4:]) if len(raw) > 12 else ""
        providers.append({"id": pid, "label": p["label"], "why": p["why"], "link": llm.KEY_STEPS[pid]["link"],
                          "steps": llm.KEY_STEPS[pid]["steps"], "keys": keys, "ok": bool(st.get("ok"))})
    return {"ok": True, "providers": providers, "shared": llm.shared_keys(),
            "leak": any(k.get("leak") for p in providers for k in p["keys"]),
            "ready": bool(r and any(m.vision and not m.dead for m in r.models))}


async def api_keys(request: Request):
    """A person's own AI keys. They stay in the person's browser; the browser sends them each time the page opens,
    and the server holds them in memory only, for this person's work alone: never in a file, the database, a log or
    a backup. POST: the keys (checked, and whether each is on a free or a paid plan). DELETE: forget them."""
    from . import llm
    ident = identity(request)
    owner = owner_of(ident)
    if request.method == "DELETE":
        llm.drop_person_keys(owner)
        return _json(_keys_payload(owner))
    if request.method == "GET":
        return _json(_keys_payload(owner))
    body = await _body(request)
    given: dict[str, list[str]] = {}
    for pid, items in (body.get("keys") or {}).items():
        if pid not in llm.BYOK or not isinstance(items, list):
            continue
        clean = []
        for k in items[:5]:
            k = str(k or "").strip()
            if k and KEY_SHAPE.match(k) and k not in clean:
                clean.append(k)
        if clean:
            given[pid] = clean
    if not given:
        llm.drop_person_keys(owner)
        return _json(_keys_payload(owner))
    env = {llm.PROVIDERS[pid]["env"]: ks for pid, ks in given.items()}
    await run_in_threadpool(llm.set_person_keys, owner, env)
    return _json(_keys_payload(owner, given))


async def api_withdraw(request: Request):
    """Withdraw every copy of this person's notes from the test suite, and stop any later copy."""
    ident = identity(request)
    d = accounts.db()
    rows = d.contributions(user_id=ident["uid"]) if ident.get("uid") else d.contributions(guest_id=ident.get("guest"))
    for row in rows:
        await run_in_threadpool(cloudstore.delete_dataset, row["dataset_id"])
        d.update_paper(row["id"], {"dataset_id": None, "dataset_opt_in": False})
        forget_paper(row["id"])
    mine = d.papers_for(user_id=ident["uid"]) if ident.get("uid") else d.papers_for(guest_id=ident.get("guest"))
    for row in mine:
        if row.get("dataset_opt_in"):
            d.update_paper(row["id"], {"dataset_opt_in": False})
            forget_paper(row["id"])
    return _json({"ok": True, "withdrawn": len(rows)})


def routes():
    from starlette.routing import Route
    return [
        Route("/api/account", api_account),
        Route("/api/account/profile", api_profile, methods=["POST"]),
        Route("/api/account/drive", api_drive, methods=["POST", "DELETE"]),
        Route("/api/auth/google", api_auth_google, methods=["POST"]),
        Route("/api/auth/logout", api_logout, methods=["POST"]),
        Route("/api/collaborators", api_collaborators),
        Route("/api/billing/checkout", api_checkout, methods=["POST"]),
        Route("/api/billing/webhook", api_webhook, methods=["POST"]),
        Route("/api/admin/whoami", api_whoami),
        Route("/api/keys", api_keys, methods=["GET", "POST", "DELETE"]),
        Route("/api/account/withdraw", api_withdraw, methods=["POST"]),
        Route("/api/projects/{pid}/archive", api_archive),
        Route("/api/projects/{pid}/drive", api_drive_saved, methods=["POST"]),
    ]
