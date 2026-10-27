# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Google Cloud Storage for the hosted service (bucket: SCRIPTAVERA_GCS_BUCKET). Three folders, kept apart:

  signatures/<paper id>/...    the permanent audit ledger: each signed record, its RFC 3161 receipts (.tsr), the
                               OpenTimestamps proof and the SHA-256 manifest. Fingerprints and receipts only:
                               never the notes themselves.
  progress/<user id>/<paper id>/...
                               private checkpoints of a signed-in person's paper while it is being made, so that
                               nothing is lost when the server restarts. Deleted with the paper, and stripped of the
                               handwritten pages once the person has downloaded a finished paper they did not
                               share for testing.
  dataset/<random id>/...      only for papers whose author ticked the opt-in box: the page images, the text read
                               from them and the LaTeX, with names, e-mails and affiliations removed. The random id
                               is not derived from the paper or the person.

Everything here is a no-op when no bucket is configured.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import re
import secrets
import threading
from pathlib import Path

from . import hosting

# The handwritten material and the files made from it, removed once a finished paper has been downloaded by an
# author who did not opt in. (The paper itself, its LaTeX and its record stay.)
RAW_DIRS = ("originals", "pages", "editor_build")
RAW_PATTERNS = (re.compile(r"^figures/.*orig.*", re.I), re.compile(r".*\.tmp$"))
NO_SYNC = (re.compile(r"^editor_build/"), re.compile(r".*\.tmp$"))


class Bucket:
    """The few operations used, over a google.cloud.storage bucket (or a stand-in in the tests)."""

    def __init__(self, gcs_bucket):
        self.b = gcs_bucket

    def put(self, name: str, data: bytes, content_type: str | None = None) -> None:
        blob = self.b.blob(name)
        blob.upload_from_string(data, content_type=content_type or mimetypes.guess_type(name)[0] or "application/octet-stream")

    def get(self, name: str) -> bytes | None:
        blob = self.b.blob(name)
        return blob.download_as_bytes() if blob.exists() else None

    def names(self, prefix: str) -> list[str]:
        return [b.name for b in self.b.client.list_blobs(self.b, prefix=prefix)]

    def delete(self, name: str) -> None:
        try:
            self.b.blob(name).delete()
        except Exception:  # noqa: BLE001 - already gone
            pass


_bucket: Bucket | None = None
_tried = False
_lock = threading.Lock()


def bucket() -> Bucket | None:
    global _bucket, _tried
    with _lock:
        if _bucket is None and not _tried:
            _tried = True
            name = hosting.env("SCRIPTAVERA_GCS_BUCKET")
            if name:
                from google.cloud import storage            # credentials: the Cloud Run service account
                _bucket = Bucket(storage.Client().bucket(name))
        return _bucket


def use_bucket(b) -> None:
    """For the tests: any object with put/get/names/delete."""
    global _bucket, _tried
    with _lock:
        _bucket, _tried = b, True


def _rel(folder: Path, f: Path) -> str:
    return f.relative_to(folder).as_posix()


# ------------------------------------------------------------------ signatures: the audit ledger

def manifest(folder: Path) -> str:
    """SHA-256 of every file the record covers and of the record's own files, in the format of `sha256sum`, so
    anyone can check it with `sha256sum -c SHA256SUMS.txt`."""
    proof = folder / "proof"
    lines = []
    try:
        rec = json.loads((proof / "record.json").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        rec = {}
    for f in rec.get("files", []):
        lines.append(f"{f['sha256']}  {f.get('stored_as') or f['name']}")
    for f in sorted(proof.iterdir()) if proof.is_dir() else []:
        if f.is_file() and f.name not in ("status.json", "locked.json") and not f.name.endswith(".tmp"):
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  proof/{f.name}")
    return "\n".join(lines) + "\n"


def proof_files(folder: Path) -> dict[str, bytes]:
    proof = folder / "proof"
    out = {}
    for f in sorted(proof.iterdir()) if proof.is_dir() else []:
        if f.is_file() and f.name not in ("status.json", "locked.json") and not f.name.endswith(".tmp"):
            out[f"proof/{f.name}"] = f.read_bytes()
    if out:
        out["proof/SHA256SUMS.txt"] = manifest(folder).encode()
    return out


def save_signatures(pid: str, kind: str, folder: Path) -> str | None:
    b = bucket()
    if not b:
        return None
    prefix = f"signatures/{pid}/{kind}/"
    for name, data in proof_files(folder).items():
        b.put(prefix + name.split("/", 1)[1], data)
    st = folder / "proof" / "status.json"
    if st.exists():
        b.put(prefix + "status.json", st.read_bytes(), "application/json")
    return prefix


# ------------------------------------------------------------------ progress checkpoints

_synced: dict[str, dict[str, tuple[int, int]]] = {}


def _progress_prefix(uid: str, pid: str) -> str:
    return f"progress/{uid}/{pid}/"


def sync_progress(uid: str, pid: str, folder: Path) -> int:
    """Send the files of the paper that changed since the last time; remove the ones deleted. Returns how many."""
    b = bucket()
    if not b or not folder.is_dir():
        return 0
    prefix = _progress_prefix(uid, pid)
    seen = _synced.setdefault(pid, {})
    now: dict[str, tuple[int, int]] = {}
    n = 0
    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        rel = _rel(folder, f)
        if any(rx.match(rel) for rx in NO_SYNC):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        sig = (st.st_size, st.st_mtime_ns)
        now[rel] = sig
        if seen.get(rel) != sig:
            try:
                b.put(prefix + rel, f.read_bytes())
                n += 1
            except OSError:
                now.pop(rel, None)
    for rel in set(seen) - set(now):
        b.delete(prefix + rel)
        n += 1
    _synced[pid] = now
    return n


def restore_progress(uid: str, pid: str, folder: Path) -> bool:
    """Bring a paper back after a restart of the server (its files are kept in memory only)."""
    b = bucket()
    if not b:
        return False
    prefix = _progress_prefix(uid, pid)
    names = b.names(prefix)
    if not names:
        return False
    seen = {}
    for name in names:
        rel = name[len(prefix):]
        if not rel or ".." in rel.split("/"):
            continue
        data = b.get(name)
        if data is None:
            continue
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        st = target.stat()
        seen[rel] = (st.st_size, st.st_mtime_ns)
    _synced[pid] = seen
    return True


def delete_progress(uid: str, pid: str, only_raw: bool = False) -> None:
    b = bucket()
    if not b:
        return
    prefix = _progress_prefix(uid, pid)
    for name in b.names(prefix):
        rel = name[len(prefix):]
        if not only_raw or is_raw(rel):
            b.delete(name)
    if not only_raw:
        _synced.pop(pid, None)
    else:
        _synced[pid] = {k: v for k, v in _synced.get(pid, {}).items() if not is_raw(k)}


def is_raw(rel: str) -> bool:
    return rel.split("/", 1)[0] in RAW_DIRS or any(rx.match(rel) for rx in RAW_PATTERNS)


def purge_raw(folder: Path) -> int:
    """Delete the handwritten material from this server: the uploaded files, the page images and the crops."""
    import shutil
    n = 0
    for d in RAW_DIRS:
        if (folder / d).exists():
            n += sum(1 for f in (folder / d).rglob("*") if f.is_file())
            shutil.rmtree(folder / d, ignore_errors=True)
    for f in list(folder.rglob("*")):
        if f.is_file() and is_raw(_rel(folder, f)):
            f.unlink(missing_ok=True)
            n += 1
    return n


# ------------------------------------------------------------------ the opt-in test suite

def _strip_command(tex: str, name: str) -> str:
    """Remove \\name[...]{...} with its balanced argument."""
    out, i = [], 0
    pat = re.compile(r"\\" + name + r"\*?\s*(\[[^\]]*\])?\s*\{")
    while True:
        m = pat.search(tex, i)
        if not m:
            out.append(tex[i:])
            return "".join(out)
        out.append(tex[i:m.start()])
        depth, j = 1, m.end()
        while j < len(tex) and depth:
            depth += {"{": 1, "}": -1}.get(tex[j], 0) if tex[j - 1] != "\\" else 0
            j += 1
        i = j


def anonymize_tex(tex: str, people: list[dict]) -> str:
    for cmd in ("author", "address", "curraddr", "email", "urladdr", "thanks", "affil", "affiliation", "orcid",
                "dedicatory", "date"):
        tex = _strip_command(tex, cmd)
    tex = re.sub(r"(?is)\\section\*?\{(acknowledg(e)?ments?|funding|declarations?)\}.*?(?=\\section|\\begin\{thebibliography\}|\\bibliography|\\end\{document\})",
                 "", tex)
    return anonymize_text(tex, people)


def anonymize_text(text: str, people: list[dict]) -> str:
    for a in people or []:
        for k in ("email", "orcid", "name", "affiliation"):
            v = str((a or {}).get(k) or "").strip()
            if len(v) >= 3:
                text = re.sub(re.escape(v), "[removed]", text, flags=re.I)
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[e-mail removed]", text)


def _clean_image(data: bytes) -> bytes:
    """The picture only: saving it again drops every embedded detail (camera, place, time)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return b""


def contribute(folder: Path, meta: dict) -> str | None:
    """Copy one opted-in paper into the test suite, without who wrote it. Returns the anonymous id."""
    b = bucket()
    if not b:
        return None
    people = list(meta.get("authors") or [])
    if meta.get("declaration", {}).get("name"):
        people.append({"name": meta["declaration"]["name"]})
    did = secrets.token_hex(12)
    prefix = f"dataset/{did}/"
    pages = sorted((folder / "pages").glob("p*.jpg"))
    for f in pages:
        data = _clean_image(f.read_bytes())
        if data:
            b.put(prefix + "pages/" + f.name, data, "image/jpeg")
    for f in sorted((folder / "figures").glob("*orig*")) if (folder / "figures").is_dir() else []:
        data = _clean_image(f.read_bytes())
        if data:
            b.put(prefix + "figures/" + f.stem + ".jpg", data, "image/jpeg")
    try:
        tr = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tr = {}
    texts = {str(n): anonymize_text(str((v or {}).get("text") or ""), people) for n, v in (tr.get("pages") or {}).items()}
    if texts:
        b.put(prefix + "transcript.json", json.dumps({"pages": texts}, ensure_ascii=False, indent=1).encode(), "application/json")
    tex = folder / "out" / "main.tex"
    if tex.exists():
        b.put(prefix + "main.tex", anonymize_tex(tex.read_text(encoding="utf-8"), people).encode(), "text/x-tex")
    b.put(prefix + "about.json", json.dumps({"domains": meta.get("domains") or [], "pages": len(pages),
                                             "use": "Scriptavera's internal tests of formula recognition only; never published, "
                                                    "sold, shared or used to train AI models"}).encode(),
          "application/json")
    return did


def delete_dataset(did: str) -> int:
    """A withdrawn contribution: every file of it is deleted."""
    b = bucket()
    if not b or not did or not re.fullmatch(r"[0-9a-f]{24}", did):
        return 0
    names = b.names(f"dataset/{did}/")
    for n in names:
        b.delete(n)
    return len(names)
