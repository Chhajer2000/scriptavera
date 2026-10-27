# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Accounts of the hosted service: Google sign-in, the people who use it, their papers, their collaborators, the
free-plan ledger and subscriptions.

Two interchangeable stores with the same tables (supabase/schema.sql):

* Supabase (Postgres), when SUPABASE_URL and SUPABASE_KEY are set. SUPABASE_KEY must be the *service_role* key:
  it is used only on the server, never sent to a browser, and every table has row level security turned on with no
  policy, so the public anon key can read nothing at all.
* SQLite, otherwise (development, tests, a single self-hosted machine). On Cloud Run its file lives in the
  container's memory and is lost when the instance stops, so production must use Supabase.

The counters that decide what is free (papers per network, papers per account) are changed atomically by the
database itself, so two uploads at the same moment can never both take the last free paper.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path

from . import config, hosting

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


class AccountError(Exception):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------------ Google

def verify_google_credential(credential: str) -> dict:
    """The ID token from the "Sign in with Google" button, checked with Google's public keys: signed by Google,
    issued for this app (GOOGLE_CLIENT_ID), not expired, and for a verified e-mail address. Returns the profile."""
    client_id = hosting.google_client_id()
    if not client_id:
        raise AccountError("Google sign-in is not set up on this server (GOOGLE_CLIENT_ID is missing).")
    try:
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token
    except ImportError as e:
        raise AccountError("The google-auth package is not installed on the server.") from e
    try:
        info = id_token.verify_oauth2_token(credential, grequests.Request(), client_id, clock_skew_in_seconds=10)
    except ValueError as e:
        raise AccountError("Google did not confirm this sign-in. Please try again.") from e
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AccountError("This sign-in was not issued by Google.")
    if not info.get("email") or not info.get("email_verified"):
        raise AccountError("Your Google account's e-mail address is not verified.")
    return info


def verify_drive_token(access_token: str) -> dict:
    """An access token the page obtained for Google Drive: issued to this app, still valid, and carrying the
    drive.file permission (which lets Scriptavera see only the files it creates, nothing else in the Drive)."""
    import requests
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": access_token}, timeout=10)
    except requests.RequestException as e:
        raise AccountError("Google could not be reached to check the Drive connection.") from e
    if r.status_code != 200:
        raise AccountError("Google did not accept this Drive connection. Please connect again.")
    info = r.json()
    if hosting.google_client_id() not in (info.get("aud"), info.get("azp")):
        raise AccountError("This Drive permission was given to another app.")
    if DRIVE_SCOPE not in str(info.get("scope", "")).split():
        raise AccountError("Permission to save files in your Google Drive was not given.")
    return info


def profile_fields(info: dict) -> dict:
    return {"google_sub": str(info["sub"]), "email": str(info.get("email", "")).lower(),
            "email_verified": bool(info.get("email_verified")), "full_name": info.get("name") or "",
            "given_name": info.get("given_name") or "", "family_name": info.get("family_name") or "",
            "picture_url": info.get("picture") or "", "locale": info.get("locale") or "",
            "hosted_domain": info.get("hd") or "", "last_login_at": now_iso()}


def subscribed(sub: dict | None) -> bool:
    if not sub or sub.get("status") not in ("active", "authenticated"):
        return False
    end = parse_iso(sub.get("current_period_end"))
    return end is None or end + dt.timedelta(days=2) > dt.datetime.now(dt.timezone.utc)     # two days' grace


def _clean_author(a: dict) -> dict | None:
    name = re.sub(r"\s+", " ", str((a or {}).get("name") or "")).strip()[:120]
    if not name:
        return None
    return {"name": name, "email": str(a.get("email") or "").strip().lower()[:120] or None,
            "affiliation": str(a.get("affiliation") or "").strip()[:300] or None,
            "orcid": str(a.get("orcid") or "").strip().upper()[:19] or None}


# ------------------------------------------------------------------ SQLite

SQLITE_SCHEMA = """
create table if not exists users (
  id text primary key, google_sub text unique not null, email text not null, email_verified integer default 0,
  full_name text, given_name text, family_name text, picture_url text, locale text, hosted_domain text,
  orcid text, affiliation text, department text, country text, homepage text,
  drive_connected integer default 0, drive_connected_at text, drive_email text,
  free_papers_used integer not null default 0, papers_total integer not null default 0,
  dataset_opt_in_default integer default 0, terms_accepted_at text,
  created_at text, updated_at text, last_login_at text);
create table if not exists subscriptions (
  user_id text primary key references users(id) on delete cascade, provider text, provider_subscription_id text unique,
  plan text, status text, current_period_end text, created_at text, updated_at text);
create table if not exists guest_ip_usage (ip_hash text primary key, papers_used integer not null default 0,
  first_seen text, last_seen text);
create table if not exists papers (
  id text primary key, owner_user_id text references users(id) on delete set null, guest_id text, ip_hash text,
  name text, title text, domains text, authors text, dataset_opt_in integer default 0, dataset_id text,
  dataset_saved_at text, signed integer default 0, status text, created_at text, updated_at text, completed_at text,
  downloaded_at text, purge_after text, purged_at text, drive_file_id text, deleted_at text);
create index if not exists papers_owner on papers(owner_user_id);
create index if not exists papers_guest on papers(guest_id);
create table if not exists collaborators (
  id text primary key, user_id text not null references users(id) on delete cascade, full_name text not null,
  email text, affiliation text, orcid text, times_collaborated integer not null default 0,
  first_paper_id text, last_paper_id text, first_seen text, last_seen text);
create index if not exists collaborators_user on collaborators(user_id);
create table if not exists paper_collaborators (paper_id text not null, collaborator_id text not null,
  position integer, primary key (paper_id, collaborator_id));
create table if not exists signatures (paper_id text not null, kind text not null, digest text not null,
  tsa_url text, tsa_time text, gcs_prefix text, created_at text, primary key (paper_id, digest));
create table if not exists billing_events (id text primary key, provider text, user_id text, type text,
  payload text, received_at text);
create table if not exists user_memory (user_id text not null references users(id) on delete cascade, kind text not null,
  data text not null default '{}', updated_at text, primary key (user_id, kind));
"""
SQLITE_LATER = ["alter table papers add column dataset_consent_at text",
                "alter table papers add column dataset_consent_version text"]

_JSON_COLS = {"domains", "authors"}
_BOOL_COLS = {"email_verified", "drive_connected", "dataset_opt_in_default", "dataset_opt_in", "signed"}


class SqliteDB:
    kind = "sqlite"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SQLITE_SCHEMA)
            for sql in SQLITE_LATER:                 # columns added since the first version of the file
                try:
                    c.execute(sql)
                except sqlite3.OperationalError:
                    pass

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("pragma foreign_keys = on")
        return c

    @staticmethod
    def _row(r) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in list(d):
            if k in _JSON_COLS and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    d[k] = []
            elif k in _BOOL_COLS and d[k] is not None:
                d[k] = bool(d[k])
        return d

    @staticmethod
    def _enc(fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            out[k] = json.dumps(v, ensure_ascii=False) if k in _JSON_COLS and v is not None else (int(v) if isinstance(v, bool) else v)
        return out

    def _one(self, sql: str, args=()) -> dict | None:
        with self._lock, self._conn() as c:
            return self._row(c.execute(sql, args).fetchone())

    def _update(self, table: str, key: str, value, fields: dict) -> None:
        if not fields:
            return
        f = self._enc(fields)
        with self._lock, self._conn() as c:
            c.execute(f"update {table} set {', '.join(k + ' = ?' for k in f)} where {key} = ?", [*f.values(), value])

    # users
    def upsert_user(self, profile: dict) -> dict:
        with self._lock, self._conn() as c:
            c.execute("begin immediate")
            r = c.execute("select id from users where google_sub = ?", (profile["google_sub"],)).fetchone()
            f = self._enc({**profile, "updated_at": now_iso()})
            if r:
                c.execute(f"update users set {', '.join(k + ' = ?' for k in f)} where id = ?", [*f.values(), r["id"]])
                uid = r["id"]
            else:
                uid = str(uuid.uuid4())
                f.update(id=uid, created_at=now_iso())
                c.execute(f"insert into users ({', '.join(f)}) values ({', '.join('?' * len(f))})", list(f.values()))
            c.execute("commit")
        return self.get_user(uid)

    def get_user(self, uid: str) -> dict | None:
        return self._one("select * from users where id = ?", (uid,))

    def update_user(self, uid: str, fields: dict) -> dict | None:
        self._update("users", "id", uid, {**fields, "updated_at": now_iso()})
        return self.get_user(uid)

    # the free plan
    def _claim(self, sql_get: str, sql_set: str, sql_new: str | None, key, limit: int) -> tuple[bool, int]:
        with self._lock, self._conn() as c:
            c.execute("begin immediate")
            r = c.execute(sql_get, (key,)).fetchone()
            used = int(r[0]) if r else 0
            if used >= limit:
                c.execute("commit")
                return False, used
            if r is None and sql_new:
                c.execute(sql_new, (key, now_iso(), now_iso()))
            else:
                c.execute(sql_set, (now_iso(), key))
            c.execute("commit")
            return True, used + 1

    def claim_guest(self, ip_hash: str, limit: int, window_days: int = 30) -> tuple[bool, int]:
        """One more guest paper for this network, unless it has had `limit` in the current window of days."""
        with self._lock, self._conn() as c:
            r = c.execute("select first_seen from guest_ip_usage where ip_hash = ?", (ip_hash,)).fetchone()
            start = parse_iso(r["first_seen"]) if r else None
            if start and dt.datetime.now(dt.timezone.utc) - start > dt.timedelta(days=window_days):
                c.execute("update guest_ip_usage set papers_used = 0, first_seen = ? where ip_hash = ?", (now_iso(), ip_hash))
        return self._claim("select papers_used from guest_ip_usage where ip_hash = ?",
                           "update guest_ip_usage set papers_used = papers_used + 1, last_seen = ? where ip_hash = ?",
                           "insert into guest_ip_usage (ip_hash, papers_used, first_seen, last_seen) values (?, 1, ?, ?)",
                           ip_hash, limit)

    def guest_browser_used(self, guest_id: str) -> int:
        """Papers this browser has started as a guest (including those since moved into an account)."""
        r = self._one("select count(*) as n from papers where guest_id = ?", (guest_id,))
        return int(r["n"]) if r else 0

    def release_guest(self, ip_hash: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("update guest_ip_usage set papers_used = max(papers_used - 1, 0) where ip_hash = ?", (ip_hash,))

    def guest_used(self, ip_hash: str) -> int:
        r = self._one("select papers_used from guest_ip_usage where ip_hash = ?", (ip_hash,))
        return int(r["papers_used"]) if r else 0

    # what is learned about each person's notation and handwriting
    def get_memory(self, uid: str, kind: str) -> dict:
        r = self._one("select data from user_memory where user_id = ? and kind = ?", (uid, kind))
        try:
            return json.loads(r["data"]) if r else {}
        except ValueError:
            return {}

    def set_memory(self, uid: str, kind: str, data: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute("insert into user_memory (user_id, kind, data, updated_at) values (?, ?, ?, ?) on conflict (user_id, kind) "
                      "do update set data = excluded.data, updated_at = excluded.updated_at",
                      (uid, kind, json.dumps(data, ensure_ascii=False), now_iso()))

    def contributions(self, user_id: str | None = None, guest_id: str | None = None) -> list[dict]:
        """Papers this person gave to the test suite (to withdraw them)."""
        with self._lock, self._conn() as c:
            if user_id:
                rows = c.execute("select * from papers where owner_user_id = ? and dataset_id is not null", (user_id,)).fetchall()
            else:
                rows = c.execute("select * from papers where guest_id = ? and owner_user_id is null and dataset_id is not null",
                                 (guest_id,)).fetchall()
        return [self._row(r) for r in rows]

    def claim_user(self, uid: str, limit: int) -> tuple[bool, int]:
        return self._claim("select free_papers_used from users where id = ?",
                           "update users set free_papers_used = free_papers_used + 1, updated_at = ? where id = ?",
                           None, uid, limit)

    def release_user(self, uid: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("update users set free_papers_used = max(free_papers_used - 1, 0) where id = ?", (uid,))

    # papers
    def add_paper(self, row: dict) -> None:
        f = self._enc({**row, "created_at": now_iso(), "updated_at": now_iso()})
        with self._lock, self._conn() as c:
            c.execute(f"insert or replace into papers ({', '.join(f)}) values ({', '.join('?' * len(f))})", list(f.values()))
            if row.get("owner_user_id"):
                c.execute("update users set papers_total = papers_total + 1 where id = ?", (row["owner_user_id"],))

    def get_paper(self, pid: str) -> dict | None:
        return self._one("select * from papers where id = ?", (pid,))

    def update_paper(self, pid: str, fields: dict) -> None:
        self._update("papers", "id", pid, {**fields, "updated_at": now_iso()})

    def papers_for(self, user_id: str | None = None, guest_id: str | None = None) -> list[dict]:
        with self._lock, self._conn() as c:
            if user_id:
                rows = c.execute("select * from papers where owner_user_id = ? and deleted_at is null", (user_id,)).fetchall()
            else:
                rows = c.execute("select * from papers where guest_id = ? and owner_user_id is null and deleted_at is null",
                                 (guest_id,)).fetchall()
        return [self._row(r) for r in rows]

    def adopt(self, guest_id: str, user_id: str) -> int:
        """Papers made as a guest in this browser become the account's, and count towards its free papers."""
        with self._lock, self._conn() as c:
            c.execute("begin immediate")
            n = c.execute("update papers set owner_user_id = ?, updated_at = ? where guest_id = ? and owner_user_id is null "
                          "and deleted_at is null", (user_id, now_iso(), guest_id)).rowcount
            if n:
                c.execute("update users set free_papers_used = free_papers_used + ?, papers_total = papers_total + ? "
                          "where id = ?", (n, n, user_id))
            c.execute("commit")
        return n

    # collaborators
    def remember_collaborators(self, uid: str, pid: str, authors: list[dict], self_email: str = "") -> int:
        n = 0
        t = now_iso()
        with self._lock, self._conn() as c:
            c.execute("begin immediate")
            for pos, raw in enumerate(authors or []):
                a = _clean_author(raw)
                if not a or (self_email and a["email"] == self_email.lower()):
                    continue
                hit = None
                if a["orcid"] or a["email"]:
                    hit = c.execute("select * from collaborators where user_id = ? and ((? is not null and orcid = ?) or "
                                    "(? is not null and lower(email) = ?)) limit 1",
                                    (uid, a["orcid"], a["orcid"], a["email"], a["email"])).fetchone()
                if hit is None:
                    hit = c.execute("select * from collaborators where user_id = ? and lower(full_name) = lower(?) and "
                                    "(orcid is null or ? is null or orcid = ?) and (email is null or ? is null or lower(email) = ?) "
                                    "limit 1", (uid, a["name"], a["orcid"], a["orcid"], a["email"], a["email"])).fetchone()
                if hit:
                    cid = hit["id"]
                    c.execute("update collaborators set full_name = ?, email = coalesce(?, email), affiliation = coalesce(?, affiliation), "
                              "orcid = coalesce(?, orcid), last_paper_id = ?, last_seen = ? where id = ?",
                              (a["name"], a["email"], a["affiliation"], a["orcid"], pid, t, cid))
                else:
                    cid = str(uuid.uuid4())
                    c.execute("insert into collaborators (id, user_id, full_name, email, affiliation, orcid, times_collaborated, "
                              "first_paper_id, last_paper_id, first_seen, last_seen) values (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                              (cid, uid, a["name"], a["email"], a["affiliation"], a["orcid"], pid, pid, t, t))
                if c.execute("insert or ignore into paper_collaborators (paper_id, collaborator_id, position) values (?, ?, ?)",
                             (pid, cid, pos)).rowcount:
                    c.execute("update collaborators set times_collaborated = times_collaborated + 1 where id = ?", (cid,))
                    n += 1
            c.execute("commit")
        return n

    def search_collaborators(self, uid: str, q: str, limit: int = 8) -> list[dict]:
        like = f"%{q.strip().lower()}%"
        with self._lock, self._conn() as c:
            rows = c.execute("select id, full_name, email, affiliation, orcid, times_collaborated, last_seen from collaborators "
                             "where user_id = ? and (? = '%%' or lower(full_name) like ? or lower(coalesce(email, '')) like ? "
                             "or coalesce(orcid, '') like ?) order by (lower(full_name) like ?) desc, times_collaborated desc, "
                             "last_seen desc limit ?", (uid, like, like, like, like, q.strip().lower() + "%", limit)).fetchall()
        return [dict(r) for r in rows]

    # subscriptions and payments
    def get_subscription(self, uid: str) -> dict | None:
        return self._one("select * from subscriptions where user_id = ?", (uid,))

    def set_subscription(self, uid: str, fields: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute("insert or ignore into subscriptions (user_id, created_at) values (?, ?)", (uid, now_iso()))
        self._update("subscriptions", "user_id", uid, {**fields, "updated_at": now_iso()})

    def billing_event_new(self, event_id: str, provider: str, user_id: str | None, kind: str, payload: dict) -> bool:
        with self._lock, self._conn() as c:
            return bool(c.execute("insert or ignore into billing_events (id, provider, user_id, type, payload, received_at) "
                                  "values (?, ?, ?, ?, ?, ?)", (event_id, provider, user_id, kind,
                                                                json.dumps(payload)[:200000], now_iso())).rowcount)

    def add_signature(self, row: dict) -> None:
        f = {**row, "created_at": now_iso()}
        with self._lock, self._conn() as c:
            c.execute(f"insert or replace into signatures ({', '.join(f)}) values ({', '.join('?' * len(f))})", list(f.values()))


# ------------------------------------------------------------------ Supabase

class SupabaseDB:
    kind = "supabase"

    def __init__(self, url: str, key: str):
        from supabase import create_client
        self.c = create_client(url, key)

    def _t(self, name: str):
        return self.c.table(name)

    @staticmethod
    def _first(res) -> dict | None:
        data = res.data if res is not None else None
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def upsert_user(self, profile: dict) -> dict:
        res = self._t("users").upsert({**profile, "updated_at": now_iso()}, on_conflict="google_sub").execute()
        return self._first(res)

    def get_user(self, uid: str) -> dict | None:
        return self._first(self._t("users").select("*").eq("id", uid).limit(1).execute())

    def update_user(self, uid: str, fields: dict) -> dict | None:
        return self._first(self._t("users").update(fields).eq("id", uid).execute())

    def _rpc(self, fn: str, params: dict):
        return self.c.rpc(fn, params).execute().data

    def claim_guest(self, ip_hash: str, limit: int, window_days: int = 30) -> tuple[bool, int]:
        r = self._rpc("claim_guest_paper", {"p_ip_hash": ip_hash, "p_limit": limit, "p_window_days": window_days})
        r = r[0] if isinstance(r, list) else r
        return bool(r["allowed"]), int(r["used"])

    def release_guest(self, ip_hash: str) -> None:
        self._rpc("release_guest_paper", {"p_ip_hash": ip_hash})

    def guest_used(self, ip_hash: str) -> int:
        r = self._first(self._t("guest_ip_usage").select("papers_used").eq("ip_hash", ip_hash).limit(1).execute())
        return int(r["papers_used"]) if r else 0

    def guest_browser_used(self, guest_id: str) -> int:
        res = self._t("papers").select("id", count="exact").eq("guest_id", guest_id).limit(1).execute()
        return int(res.count or 0)

    def get_memory(self, uid: str, kind: str) -> dict:
        r = self._first(self._t("user_memory").select("data").eq("user_id", uid).eq("kind", kind).limit(1).execute())
        return (r or {}).get("data") or {}

    def set_memory(self, uid: str, kind: str, data: dict) -> None:
        self._t("user_memory").upsert({"user_id": uid, "kind": kind, "data": data, "updated_at": now_iso()},
                                      on_conflict="user_id,kind").execute()

    def contributions(self, user_id: str | None = None, guest_id: str | None = None) -> list[dict]:
        q = self._t("papers").select("*").not_.is_("dataset_id", "null")
        q = q.eq("owner_user_id", user_id) if user_id else q.eq("guest_id", guest_id).is_("owner_user_id", "null")
        return q.execute().data or []

    def claim_user(self, uid: str, limit: int) -> tuple[bool, int]:
        r = self._rpc("claim_user_paper", {"p_user": uid, "p_limit": limit})
        r = r[0] if isinstance(r, list) else r
        return bool(r["allowed"]), int(r["used"])

    def release_user(self, uid: str) -> None:
        self._rpc("release_user_paper", {"p_user": uid})

    def add_paper(self, row: dict) -> None:
        self._t("papers").upsert(row, on_conflict="id").execute()

    def get_paper(self, pid: str) -> dict | None:
        return self._first(self._t("papers").select("*").eq("id", pid).limit(1).execute())

    def update_paper(self, pid: str, fields: dict) -> None:
        self._t("papers").update(fields).eq("id", pid).execute()

    def papers_for(self, user_id: str | None = None, guest_id: str | None = None) -> list[dict]:
        q = self._t("papers").select("*").is_("deleted_at", "null")
        q = q.eq("owner_user_id", user_id) if user_id else q.eq("guest_id", guest_id).is_("owner_user_id", "null")
        return q.execute().data or []

    def adopt(self, guest_id: str, user_id: str) -> int:
        return int(self._rpc("adopt_guest_papers", {"p_guest": guest_id, "p_user": user_id}) or 0)

    def remember_collaborators(self, uid: str, pid: str, authors: list[dict], self_email: str = "") -> int:
        clean = [a for a in (_clean_author(x) for x in authors or []) if a]
        return int(self._rpc("remember_collaborators", {"p_user": uid, "p_paper": pid, "p_authors": clean,
                                                         "p_self_email": (self_email or "").lower()}) or 0)

    def search_collaborators(self, uid: str, q: str, limit: int = 8) -> list[dict]:
        return self._rpc("search_collaborators", {"p_user": uid, "p_query": q.strip(), "p_limit": limit}) or []

    def get_subscription(self, uid: str) -> dict | None:
        return self._first(self._t("subscriptions").select("*").eq("user_id", uid).limit(1).execute())

    def set_subscription(self, uid: str, fields: dict) -> None:
        self._t("subscriptions").upsert({"user_id": uid, **fields}, on_conflict="user_id").execute()

    def billing_event_new(self, event_id: str, provider: str, user_id: str | None, kind: str, payload: dict) -> bool:
        if self._first(self._t("billing_events").select("id").eq("id", event_id).limit(1).execute()):
            return False
        self._t("billing_events").insert({"id": event_id, "provider": provider, "user_id": user_id, "type": kind,
                                          "payload": payload}).execute()
        return True

    def add_signature(self, row: dict) -> None:
        self._t("signatures").upsert(row, on_conflict="paper_id,digest").execute()


# ------------------------------------------------------------------ the store in use

_db = None
_db_lock = threading.Lock()


def db():
    """Supabase when its keys are set, SQLite otherwise."""
    global _db
    with _db_lock:
        if _db is None:
            url, key = hosting.env("SUPABASE_URL"), hosting.env("SUPABASE_KEY")
            if url and key:
                _db = SupabaseDB(url, key)
            else:
                _db = SqliteDB(Path(hosting.env("SCRIPTAVERA_DB_PATH") or str(config.ROOT / "accounts.db")))
        return _db


def reset_for_tests() -> None:
    global _db
    with _db_lock:
        _db = None
