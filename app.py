# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Scriptavera: start the local app and open it in the browser.

    python app.py            (or double-click run_app.bat on Windows)
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser


def _missing() -> list[str]:
    need = {"starlette": "starlette", "uvicorn": "uvicorn", "multipart": "python-multipart", "requests": "requests",
            "pypdfium2": "pypdfium2", "PIL": "pillow"}
    out = []
    for module, package in need.items():
        try:
            __import__(module)
        except ImportError:
            out.append(package)
    return out


def _listening(port: int) -> bool:
    """Whether some program is serving on this port. (A port just released can still refuse a test bind for a
    while, which would wrongly send the app to another port.)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        if not _listening(port):
            return port
    return preferred


def _replace_running(port: int) -> None:
    """If Scriptavera is already running (an older copy, still open in another window), stop it, so that only
    this one runs and the browser never talks to an old program. Its work is saved after every step."""
    import json
    import time
    import urllib.request
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/meta", timeout=1.5) as r:
            old = json.loads(r.read().decode("utf-8")).get("version", "?")
    except Exception:
        return                                          # nothing of ours is running there
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/api/shutdown", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=3).read()
    except Exception:
        pass
    for _ in range(30):
        if not _listening(port):
            print(f"  The copy of Scriptavera that was already running (version {old}) was closed.")
            time.sleep(0.5)
            return
        time.sleep(0.2)
    print(f"\n  Scriptavera (version {old}) is already running in another window and could not be closed.")
    print("  Close that window first, then start this again.\n")
    input("  Press Enter to close this window.")
    sys.exit(1)


def main() -> None:
    missing = _missing()
    if missing:
        print("\nSome required packages are not installed:\n  " + ", ".join(missing))
        print("\nInstall them with:\n  python -m pip install -r requirements.txt\n")
        sys.exit(1)
    import uvicorn
    from scriptavera.server import create_app

    preferred = int(os.environ.get("SCRIPTAVERA_PORT", "8765"))
    _replace_running(preferred)
    port = _free_port(preferred)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Scriptavera is running at {url}\n  Keep this window open. Press Ctrl+C to stop.\n")
    if os.environ.get("SCRIPTAVERA_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
