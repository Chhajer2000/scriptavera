# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Independent timestamps for a fingerprint (a SHA-256 digest). Only the 32 bytes of the fingerprint are sent.

Two kinds, because they fail in different ways:

* RFC 3161: a time-stamp authority signs "this fingerprint existed at this time" with its certificate. The answer is
  immediate and anyone can check it with OpenSSL (`openssl ts -verify`). It relies on trusting the authority.
* OpenTimestamps: public calendar servers gather fingerprints into one and write it into the Bitcoin blockchain,
  usually within a few hours. The finished proof relies on no company at all: anyone can check it against the
  blockchain, with the free `ots` program or at https://opentimestamps.org.

Both formats are standard, so the proofs can be checked without Scriptavera.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import re
import time

import requests

DEFAULT_TSA = ["https://freetsa.org/tsr", "http://timestamp.digicert.com"]
DEFAULT_CALENDARS = ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
                     "https://a.pool.eternitywall.com", "https://ots.btc.catallaxy.com"]
UA = {"User-Agent": "Scriptavera (https://github.com/Chhajer2000/scriptavera)"}


# ======================================================================= DER

def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _len(len(content)) + content


def _int(n: int) -> bytes:
    b = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, b)


def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(chunk)
    return _tlv(0x06, body)


OID = {
    "sha1": "1.3.14.3.2.26", "sha256": "2.16.840.1.101.3.4.2.1", "sha384": "2.16.840.1.101.3.4.2.2",
    "sha512": "2.16.840.1.101.3.4.2.3", "signedData": "1.2.840.113549.1.7.2", "tstInfo": "1.2.840.113549.1.9.16.1.4",
    "messageDigest": "1.2.840.113549.1.9.4", "contentType": "1.2.840.113549.1.9.3",
}
_SIG_HASH = {"1.2.840.113549.1.1.5": "sha1", "1.2.840.113549.1.1.11": "sha256", "1.2.840.113549.1.1.12": "sha384",
             "1.2.840.113549.1.1.13": "sha512", "1.2.840.10045.4.3.2": "sha256", "1.2.840.10045.4.3.3": "sha384",
             "1.2.840.10045.4.3.4": "sha512", "1.2.840.10045.4.1": "sha1"}
_HASH_OF = {v: k for k, v in OID.items() if k.startswith("sha")}


def _read(data: bytes, pos: int) -> tuple[int, int, int]:
    """(tag, content start, content end) of the element at `pos`."""
    tag = data[pos]
    n = data[pos + 1]
    p = pos + 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(data[p:p + k], "big")
        p += k
    if p + n > len(data):
        raise ValueError("truncated DER")
    return tag, p, p + n


def _kids(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """Children of a constructed element: (tag, content start, content end, element start)."""
    out = []
    p = start
    while p < end:
        tag, cs, ce = _read(data, p)
        out.append((tag, cs, ce, p))
        p = ce
    return out


def _oid_str(b: bytes) -> str:
    first = b[0]
    parts = [first // 40, first % 40]
    val = 0
    for x in b[1:]:
        val = (val << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(val)
            val = 0
    return ".".join(str(x) for x in parts)


# ================================================================ RFC 3161

def tsa_request(digest: bytes, nonce: int | None = None) -> bytes:
    """A TimeStampReq for a SHA-256 digest, asking for the authority's certificate in the answer."""
    imprint = _tlv(0x30, _tlv(0x30, _oid(OID["sha256"]) + b"\x05\x00") + _tlv(0x04, digest))
    body = _int(1) + imprint + (_int(nonce) if nonce is not None else b"") + b"\x01\x01\xff"
    return _tlv(0x30, body)


def _gentime(raw: bytes) -> float:
    s = raw.decode("ascii").rstrip("Z")
    frac = 0.0
    if "." in s:
        s, f = s.split(".", 1)
        frac = float("0." + re.sub(r"\D", "", f) or "0")
    t = dt.datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    return t.timestamp() + frac


def parse_token(resp: bytes) -> dict:
    """Read a TimeStampResp (or a bare TimeStampToken): status, time, serial, the digest stamped, the authority, and
    the parts needed to check the signature."""
    tag, cs, ce = _read(resp, 0)
    top = _kids(resp, cs, ce)
    token_at = None
    status = 0
    if top and top[0][0] == 0x30 and len(top) >= 1 and resp[top[0][1]] == 0x02:      # TimeStampResp: status first
        st = _kids(resp, top[0][1], top[0][2])
        status = int.from_bytes(resp[st[0][1]:st[0][2]], "big")
        if len(top) > 1:
            token_at = top[1][3]
    else:
        token_at = 0
    if token_at is None:
        return {"status": status, "granted": False}
    _, tcs, tce = _read(resp, token_at)
    ci = _kids(resp, tcs, tce)
    if _oid_str(resp[ci[0][1]:ci[0][2]]) != OID["signedData"]:
        raise ValueError("not a CMS SignedData token")
    _, sdcs, sdce = _read(resp, ci[1][1])                 # [0] EXPLICIT SignedData
    sd = _kids(resp, sdcs, sdce)
    encap = _kids(resp, sd[2][1], sd[2][2])
    if _oid_str(resp[encap[0][1]:encap[0][2]]) != OID["tstInfo"]:
        raise ValueError("the token does not hold a TSTInfo")
    _, ocs, oce = _read(resp, encap[1][1])               # [0] EXPLICIT OCTET STRING
    tst_der = resp[ocs:oce]
    tst = _kids(tst_der, *_read(tst_der, 0)[1:])
    imp = _kids(tst_der, tst[2][1], tst[2][2])
    alg = _kids(tst_der, imp[0][1], imp[0][2])
    hash_oid = _oid_str(tst_der[alg[0][1]:alg[0][2]])
    stamped = tst_der[imp[1][1]:imp[1][2]]
    serial = int.from_bytes(tst_der[tst[3][1]:tst[3][2]], "big")
    gen = _gentime(tst_der[tst[4][1]:tst[4][2]])
    nonce = None
    for t in tst[5:]:
        if t[0] == 0x02:
            nonce = int.from_bytes(tst_der[t[1]:t[2]], "big", signed=True)
    certs, signers = [], []
    for t in sd[3:]:
        if t[0] == 0xA0:                                  # certificates [0] IMPLICIT SET OF Certificate
            certs = [resp[k[3]:k[2]] for k in _kids(resp, t[1], t[2]) if k[0] == 0x30]
        elif t[0] == 0x31:                                # signerInfos
            signers = [k for k in _kids(resp, t[1], t[2])]
    return {"status": status, "granted": status in (0, 1), "time": gen, "serial": serial, "hash": _HASH_OF.get(hash_oid, hash_oid),
            "digest": stamped.hex(), "nonce": nonce, "tst": tst_der, "certs": certs, "signers": signers, "raw": resp}


def verify_token(resp: bytes, digest: bytes, nonce: int | None = None) -> dict:
    """Check a token: it stamps this digest (and nonce), and the authority's signature over it is valid with the
    certificate it carries. Returns {ok, time, serial, tsa, issuer, problems}."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    info = parse_token(resp)
    out = {"ok": False, "time": info.get("time"), "serial": info.get("serial"), "tsa": "", "issuer": "", "problems": []}
    if not info.get("granted"):
        out["problems"].append(f"the authority refused (status {info.get('status')})")
        return out
    if info["digest"] != digest.hex():
        out["problems"].append("the token stamps a different fingerprint")
    if nonce is not None and info["nonce"] is not None and info["nonce"] != nonce:
        out["problems"].append("the token answers a different request")
    raw = info["raw"]
    algs = {"sha1": hashes.SHA1(), "sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512()}
    good = False
    for si_tag, si_cs, si_ce, _ in info["signers"]:
        si = _kids(raw, si_cs, si_ce)
        dalg = _HASH_OF.get(_oid_str(raw[_kids(raw, si[2][1], si[2][2])[0][1]:_kids(raw, si[2][1], si[2][2])[0][2]]), "sha256")
        k = 3
        signed_attrs = None
        if si[k][0] == 0xA0:
            signed_attrs = si[k]
            k += 1
        salg = _oid_str(raw[_kids(raw, si[k][1], si[k][2])[0][1]:_kids(raw, si[k][1], si[k][2])[0][2]])
        sig = raw[si[k + 1][1]:si[k + 1][2]]
        if signed_attrs:
            md = None
            for attr in _kids(raw, signed_attrs[1], signed_attrs[2]):
                a = _kids(raw, attr[1], attr[2])
                if _oid_str(raw[a[0][1]:a[0][2]]) == OID["messageDigest"]:
                    vals = _kids(raw, a[1][1], a[1][2])
                    md = raw[vals[0][1]:vals[0][2]]
            if md != hashlib.new(dalg, info["tst"]).digest():
                out["problems"].append("the signed digest of the token does not match its content")
                continue
            signed = b"\x31" + raw[signed_attrs[3] + 1:signed_attrs[2]]       # [0] IMPLICIT -> SET for the signature
        else:
            signed = info["tst"]
        hname = _SIG_HASH.get(salg, dalg)
        for der in info["certs"]:
            try:
                cert = x509.load_der_x509_certificate(der)
                key = cert.public_key()
                if isinstance(key, rsa.RSAPublicKey):
                    key.verify(sig, signed, padding.PKCS1v15(), algs[hname])
                elif isinstance(key, ec.EllipticCurvePublicKey):
                    key.verify(sig, signed, ec.ECDSA(algs[hname]))
                else:
                    continue
            except (InvalidSignature, ValueError, KeyError, TypeError):
                continue
            good = True
            out["tsa"] = cert.subject.rfc4514_string()
            out["issuer"] = cert.issuer.rfc4514_string()
            out["certificate"] = base64.b64encode(der).decode()
            try:
                eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                if x509.oid.ExtendedKeyUsageOID.TIME_STAMPING not in eku:
                    out["problems"].append("the signing certificate is not a time-stamping certificate")
            except x509.ExtensionNotFound:
                out["problems"].append("the signing certificate does not say it is for time-stamping")
            break
        if good:
            break
    if not good:
        out["problems"].append("the authority's signature could not be checked with the certificate in the token")
    out["ok"] = good and not out["problems"]
    return out


def request_tsa(url: str, digest: bytes, timeout: float = 20.0) -> dict:
    """Ask one authority for a timestamp. Returns {ok, url, time, serial, tsa, tsq, tsr, error}."""
    nonce = int.from_bytes(os.urandom(8), "big") >> 1
    tsq = tsa_request(digest, nonce)
    try:
        r = requests.post(url, data=tsq, timeout=timeout,
                          headers={**UA, "Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"})
    except requests.RequestException as e:
        return {"ok": False, "url": url, "error": f"could not reach the service ({type(e).__name__})"}
    if r.status_code != 200 or not r.content:
        return {"ok": False, "url": url, "error": f"the service answered {r.status_code}"}
    try:
        v = verify_token(r.content, digest, nonce)
    except (ValueError, IndexError) as e:
        return {"ok": False, "url": url, "error": f"the answer is not a valid timestamp ({e})"}
    return {"ok": v["ok"], "url": url, "time": v["time"], "serial": v["serial"], "tsa": v["tsa"], "issuer": v["issuer"],
            "tsq": tsq, "tsr": r.content, "error": "; ".join(v["problems"])}


# ============================================================ OpenTimestamps

MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
PENDING = bytes.fromhex("83dfe30d2ef90c8e")
BITCOIN = bytes.fromhex("0588960d73d71901")
_UNARY = {0x08: "sha256", 0x02: "sha1", 0x03: "ripemd160", 0x67: "keccak256", 0xf2: "reverse", 0xf3: "hexlify"}
_BINARY = {0xf0: "append", 0xf1: "prepend"}


def _varuint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("truncated proof")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def bytes(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("truncated proof")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def varuint(self) -> int:
        n, shift = 0, 0
        while True:
            b = self.byte()
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                return n
            shift += 7
            if shift > 63:
                raise ValueError("bad number in proof")

    def varbytes(self, limit: int = 8192) -> bytes:
        n = self.varuint()
        if n > limit:
            raise ValueError("oversized field in proof")
        return self.bytes(n)


def _apply(op: int, arg: bytes, msg: bytes | None) -> bytes | None:
    if msg is None:
        return None
    if op == 0x08:
        return hashlib.sha256(msg).digest()
    if op == 0x02:
        return hashlib.sha1(msg).digest()
    if op == 0x03:
        try:
            return hashlib.new("ripemd160", msg).digest()
        except ValueError:
            return None
    if op == 0xf0:
        return msg + arg
    if op == 0xf1:
        return arg + msg
    if op == 0xf2:
        return msg[::-1]
    if op == 0xf3:
        return msg.hex().encode()
    return None                               # keccak256 and others: not computed here


class Stamp:
    """A node of the timestamp tree: the message at this point, what attests it, and operations leading on."""

    def __init__(self, msg: bytes | None):
        self.msg = msg
        self.attestations: list[tuple[bytes, bytes]] = []       # (8-byte tag, payload)
        self.ops: dict[tuple[int, bytes], Stamp] = {}

    # -------------------------------------------------- reading and writing

    @classmethod
    def read(cls, r: _Reader, msg: bytes | None, depth: int = 0) -> "Stamp":
        if depth > 256:
            raise ValueError("proof nested too deeply")
        st = cls(msg)

        def entry(tag: int) -> None:
            if tag == 0x00:
                st.attestations.append((r.bytes(8), r.varbytes()))
            elif tag in _BINARY:
                arg = r.varbytes(4096)
                st.ops[(tag, arg)] = cls.read(r, _apply(tag, arg, msg), depth + 1)
            elif tag in _UNARY:
                st.ops[(tag, b"")] = cls.read(r, _apply(tag, b"", msg), depth + 1)
            else:
                raise ValueError(f"unknown operation 0x{tag:02x} in proof")
        tag = r.byte()
        while tag == 0xFF:
            entry(r.byte())
            tag = r.byte()
        entry(tag)
        return st

    def write(self) -> bytes:
        parts: list[bytes] = []
        atts = sorted(self.attestations)
        for t, payload in atts:
            parts.append(b"\x00" + t + _varuint(len(payload)) + payload)
        for (op, arg), child in sorted(self.ops.items()):
            head = bytes([op]) + (_varuint(len(arg)) + arg if op in _BINARY else b"")
            parts.append(head + child.write())
        if not parts:
            raise ValueError("an empty timestamp cannot be written")
        return b"".join(b"\xff" + x for x in parts[:-1]) + parts[-1]

    def merge(self, other: "Stamp") -> None:
        for a in other.attestations:
            if a not in self.attestations:
                self.attestations.append(a)
        for key, child in other.ops.items():
            if key in self.ops:
                self.ops[key].merge(child)
            else:
                self.ops[key] = child

    def walk(self):
        yield self
        for child in self.ops.values():
            yield from child.walk()


def ots_file(digest: bytes, stamp: Stamp) -> bytes:
    return MAGIC + _varuint(1) + b"\x08" + digest + stamp.write()


def read_ots(data: bytes) -> tuple[bytes, Stamp]:
    if not data.startswith(MAGIC):
        raise ValueError("not an OpenTimestamps proof")
    r = _Reader(data, len(MAGIC))
    if r.varuint() != 1:
        raise ValueError("unknown OpenTimestamps version")
    if r.byte() != 0x08:
        raise ValueError("only SHA-256 proofs are read here")
    digest = r.bytes(32)
    return digest, Stamp.read(r, digest)


def ots_summary(data: bytes) -> dict:
    """What a proof contains: calendars still to confirm it, and Bitcoin blocks that do."""
    digest, st = read_ots(data)
    pending, blocks = [], []
    for node in st.walk():
        for tag, payload in node.attestations:
            if tag == PENDING:
                try:
                    pending.append(_Reader(payload).varbytes(1000).decode("utf-8", "replace"))
                except ValueError:
                    pass
            elif tag == BITCOIN:
                try:
                    blocks.append({"height": _Reader(payload).varuint(), "merkle": (node.msg or b"")[::-1].hex()})
                except ValueError:
                    pass
    return {"digest": digest.hex(), "pending": sorted(set(pending)), "bitcoin": blocks}


def stamp_ots(digest: bytes, calendars: list[str] | None = None, timeout: float = 15.0) -> dict:
    """Submit a fingerprint to the OpenTimestamps calendars. Returns {ok, ots (bytes), calendars, errors}.
    As the official client does, a random nonce is appended first, so the calendars never see the fingerprint."""
    calendars = calendars or DEFAULT_CALENDARS
    nonce = os.urandom(16)
    commit = hashlib.sha256(digest + nonce).digest()
    root = Stamp(digest)
    node = root.ops.setdefault((0xf0, nonce), Stamp(digest + nonce)).ops.setdefault((0x08, b""), Stamp(commit))
    ok, errors = [], []
    for cal in calendars:
        try:
            r = requests.post(cal.rstrip("/") + "/digest", data=commit, timeout=timeout,
                              headers={**UA, "Accept": "application/vnd.opentimestamps.v1"})
            if r.status_code != 200:
                errors.append(f"{cal}: answered {r.status_code}")
                continue
            node.merge(Stamp.read(_Reader(r.content), commit))
            ok.append(cal)
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{cal}: {type(e).__name__}")
    if not ok:
        return {"ok": False, "calendars": [], "errors": errors}
    return {"ok": True, "ots": ots_file(digest, root), "calendars": ok, "errors": errors}


def upgrade_ots(data: bytes, timeout: float = 15.0) -> dict:
    """Ask the calendars whether the fingerprint is in the blockchain yet; if so, add the path to the block.
    Returns {ots (bytes), changed, summary}."""
    digest, root = read_ots(data)
    changed = False
    for node in list(root.walk()):
        for tag, payload in list(node.attestations):
            if tag != PENDING or node.msg is None:
                continue
            try:
                uri = _Reader(payload).varbytes(1000).decode("utf-8")
            except ValueError:
                continue
            if not re.match(r"^(?:https://[A-Za-z0-9.\-]+|http://(?:127\.0\.0\.1|localhost))(?::\d+)?(?:/[A-Za-z0-9._\-/]*)?$", uri):
                continue                           # only plain https calendars are asked (or one on this computer)
            try:
                r = requests.get(uri.rstrip("/") + "/timestamp/" + node.msg.hex(), timeout=timeout,
                                 headers={**UA, "Accept": "application/vnd.opentimestamps.v1"})
            except requests.RequestException:
                continue
            if r.status_code != 200:
                continue
            try:
                got = Stamp.read(_Reader(r.content), node.msg)
            except ValueError:
                continue
            if any(t == BITCOIN for n in got.walk() for t, _ in n.attestations):
                node.merge(got)
                changed = True
    out = ots_file(digest, root) if changed else data
    return {"ots": out, "changed": changed, "summary": ots_summary(out)}


def bitcoin_block(height: int, timeout: float = 15.0) -> dict | None:
    """The merkle root and time of a Bitcoin block, from a public block explorer (two are tried)."""
    for base in ("https://blockstream.info/api", "https://mempool.space/api"):
        try:
            h = requests.get(f"{base}/block-height/{int(height)}", timeout=timeout, headers=UA)
            if h.status_code != 200:
                continue
            b = requests.get(f"{base}/block/{h.text.strip()}", timeout=timeout, headers=UA)
            if b.status_code != 200:
                continue
            j = b.json()
            return {"height": int(height), "hash": h.text.strip(), "merkle": j.get("merkle_root", ""),
                    "time": float(j.get("timestamp") or 0), "source": base}
        except (requests.RequestException, ValueError):
            continue
    return None


def iso(t: float | None) -> str:
    if not t:
        return ""
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def now() -> float:
    return time.time()
