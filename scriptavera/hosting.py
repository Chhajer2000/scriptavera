# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""How the hosted service (scriptavera.com on Google Cloud Run) differs from the desktop app.

The desktop app serves one person on one computer. The hosted service serves everyone, so it needs things the desktop
app never did: the public host names, the visitor's real address behind Google's front end, signed session cookies,
and the limits of the free plan. Everything here is read from environment variables, so the same code runs on a
laptop and in the cloud. Nothing in this module changes the desktop app unless SCRIPTAVERA_PUBLIC=1 (or Cloud Run's
own K_SERVICE variable) is set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
PUBLIC_HOSTS = ("scriptavera.com", "www.scriptavera.com")
REDIRECT_HOSTS = ("scriptavera.org", "www.scriptavera.org")      # the .org domain only ever points people to .com

FREE_PAPERS = 3
GUEST_LIMIT_MESSAGE = ("Free guest limit of three papers reached for this network. "
                       "Please sign in with Google to continue.")
SESSION_COOKIE = "sv_session"
GUEST_COOKIE = "sv_guest"
SESSION_DAYS = 30


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def public() -> bool:
    """The hosted service: set by the Dockerfile, or detected on Cloud Run. SCRIPTAVERA_PUBLIC=0 turns it off."""
    flag = env("SCRIPTAVERA_PUBLIC")
    if flag:
        return flag == "1"
    return bool(env("K_SERVICE"))


def _host_list(name: str) -> list[str]:
    return [h.strip().lower().rstrip(".").split(":")[0] for h in env(name).split(",") if h.strip()]


def allowed_hosts() -> set[str]:
    """Host names the server answers to: this computer, scriptavera.com, www.scriptavera.com, and any listed in
    SCRIPTAVERA_ALLOWED_HOSTS (comma separated, e.g. the service's own *.run.app address)."""
    return {*LOCAL_HOSTS, *PUBLIC_HOSTS, *_host_list("SCRIPTAVERA_ALLOWED_HOSTS")}


def redirect_hosts() -> set[str]:
    return {*REDIRECT_HOSTS, *_host_list("SCRIPTAVERA_REDIRECT_HOSTS")}


def canonical_host() -> str:
    """The one address the service is shown at. www.scriptavera.com and the .org domain send people here, so that
    the sign-in cookie and Google's list of allowed origins only ever need this one name."""
    return env("SCRIPTAVERA_CANONICAL_HOST", "scriptavera.com").lower()


def free_papers() -> int:
    try:
        return max(0, int(env("SCRIPTAVERA_FREE_PAPERS", str(FREE_PAPERS))))
    except ValueError:
        return FREE_PAPERS


def admins() -> set[str]:
    return {e.strip().lower() for e in env("SCRIPTAVERA_ADMIN_EMAILS").split(",") if e.strip()}


def google_client_id() -> str:
    return env("GOOGLE_CLIENT_ID")


# ------------------------------------------------------------------ the visitor's address

def client_ip(headers: dict, peer: str = "") -> str:
    """The visitor's real address, from X-Forwarded-For.

    Google's front end *appends* the address it saw to whatever the visitor sent, so the first entry is whatever the
    visitor chose to write there and must never be trusted. The trusted entry is counted from the right:
    SCRIPTAVERA_PROXY_HOPS=1 (the default) when Cloud Run is reached directly (its own URL or a domain mapping);
    2 behind a Google external Application Load Balancer, which appends "<client-ip>,<load-balancer-ip>"."""
    try:
        hops = max(1, int(env("SCRIPTAVERA_PROXY_HOPS", "1")))
    except ValueError:
        hops = 1
    parts = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    candidate = parts[-hops] if len(parts) >= hops else (parts[0] if parts else peer)
    try:
        return str(ipaddress.ip_address(candidate.strip("[]")))
    except ValueError:
        return peer or "unknown"


def network_of(ip: str) -> str:
    """What one 'network' is for the free plan: the address itself for IPv4; for IPv6 its /64, because every home
    connection is given a whole /64 and can pick a new address inside it at will."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 6:
        if a.ipv4_mapped:                        # ::ffff:1.2.3.4 is the IPv4 address 1.2.3.4
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


def ip_key(ip: str) -> str:
    """The address as it is stored: a keyed hash, so the ledger can count papers per network without keeping a
    list of people's addresses."""
    return hmac.new(_secret(b"ip"), network_of(ip).encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ signed cookies

_fallback_secret = secrets.token_bytes(32)


def _secret(purpose: bytes) -> bytes:
    raw = env("SCRIPTAVERA_SESSION_SECRET").encode() or _fallback_secret
    return hmac.new(raw, purpose, hashlib.sha256).digest()


def secret_configured() -> bool:
    return bool(env("SCRIPTAVERA_SESSION_SECRET"))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict, days: float = SESSION_DAYS) -> str:
    body = dict(payload, exp=int(time.time() + days * 86400))
    data = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    mac = _b64(hmac.new(_secret(b"cookie"), data.encode(), hashlib.sha256).digest())
    return f"{data}.{mac}"


def unsign(token: str) -> dict | None:
    try:
        data, mac = (token or "").split(".", 1)
        want = _b64(hmac.new(_secret(b"cookie"), data.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, want):
            return None
        body = json.loads(_unb64(data))
        if not isinstance(body, dict) or body.get("exp", 0) < time.time():
            return None
        return body
    except (ValueError, TypeError):
        return None


def new_guest_id() -> str:
    return "g_" + secrets.token_urlsafe(18)


def parse_cookies(header: str) -> dict[str, str]:
    out = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def cookie_header(name: str, value: str, max_age: int, secure: bool) -> bytes:
    flags = f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
    if secure:
        flags += "; Secure"
    return flags.encode("latin-1")
