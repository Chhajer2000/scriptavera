# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Whose work this is, for the code that runs it.

On scriptavera.com many people use one server. Two things must never be shared between them: the AI keys a person
brings, and what Scriptavera learns about their notation and handwriting. Both are looked up by the *owner* of the
work in hand: "u:<account id>" for a signed-in person, "g:<browser id>" for a guest. It is carried in a context
variable: set for every request (hosted.gate), and for the background work on a paper (pipeline._drive,
reviews._work) from the paper's owner. On a computer (the desktop app) there is one owner, None, and nothing changes.
"""

from __future__ import annotations

import contextlib
import contextvars

_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar("scriptavera_owner", default=None)


def current() -> str | None:
    return _owner.get()


def set_owner(owner: str | None):
    return _owner.set(owner)


@contextlib.contextmanager
def owner(o: str | None):
    tok = _owner.set(o)
    try:
        yield
    finally:
        _owner.reset(tok)


def of_project(pid: str) -> str | None:
    """The owner of a paper: its account, or the browser that made it as a guest. None on a computer."""
    from . import hosting
    if not hosting.public():
        return None
    from . import hosted
    try:
        row = hosted.paper_row(pid)
    except Exception:  # noqa: BLE001 - the database briefly unreachable: no one's keys, rather than someone else's
        return "x:unknown"
    if not row:
        return "x:unknown"
    return f"u:{row['owner_user_id']}" if row.get("owner_user_id") else f"g:{row.get('guest_id')}"


def user_id(o: str | None) -> str | None:
    return o[2:] if o and o.startswith("u:") else None
