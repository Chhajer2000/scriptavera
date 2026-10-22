# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Photos of the notes straight from a phone on the same Wi-Fi.

The app itself only ever answers this computer. For the phone, a second, separate server is started on the local
network for one session: it serves one page and accepts only images, at an address with a random secret in it,
and it stops by itself after 20 minutes (or when the dialog is closed). It cannot see any paper. The photos land in
a folder of their own; the app then uses them as the notes of a new paper, or as pages added to one."""
from __future__ import annotations

import io
import secrets
import socket
import threading
import time
from pathlib import Path

from . import config

LIFETIME = 20 * 60
MAX_FILES = 300
_lock = threading.Lock()
_session: dict = {}


def lan_ip() -> str | None:
    """This computer's address on the local network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


def folder(token: str) -> Path:
    return config.PHONE_DIR / token


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scriptavera: photos of your notes</title><style>
body{font:17px system-ui,sans-serif;margin:0;padding:18px;background:#f3f5f1;color:#18212b}
h1{font-size:20px}label.big{display:block;background:#21352f;color:#fff;text-align:center;padding:18px;border-radius:10px;margin:14px 0;font-weight:600}
input{display:none}.n{font-size:15px;color:#56616b}#log div{padding:4px 0;border-bottom:1px solid #ddd}
</style></head><body><h1>Photos of your notes</h1>
<p>Photograph each page in order, flat and in good light. They go straight to Scriptavera on your computer.</p>
<label class="big">Take a photo<input id="cam" type="file" accept="image/*" capture="environment"></label>
<label class="big" style="background:#56616b">Choose photos<input id="pick" type="file" accept="image/*" multiple></label>
<div class="n" id="count"></div><div id="log"></div>
<script>
var sent=0;
function send(files){Array.prototype.forEach.call(files,function(f){var fd=new FormData();fd.append("file",f);
var row=document.createElement("div");row.textContent="Sending "+f.name+"…";document.getElementById("log").prepend(row);
fetch(location.pathname+"/upload",{method:"POST",body:fd}).then(function(r){return r.json()}).then(function(d){
row.textContent=d.ok?"Page "+d.n+" received":"Not sent: "+(d.error||"");if(d.ok){sent++;document.getElementById("count").textContent=sent+" page(s) sent. Go back to your computer when you are done."}})
.catch(function(){row.textContent="Not sent: the computer did not answer"})})}
document.getElementById("cam").onchange=function(e){send(e.target.files);e.target.value=""};
document.getElementById("pick").onchange=function(e){send(e.target.files);e.target.value=""};
</script></body></html>"""


def _app(token: str):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    def alive() -> bool:
        return _session.get("token") == token and time.time() < _session.get("until", 0)

    async def page(request: Request):
        if request.path_params["t"] != token or not alive():
            return Response(status_code=404)
        return HTMLResponse(_PAGE)

    async def upload(request: Request):
        if request.path_params["t"] != token or not alive():
            return Response(status_code=404)
        form = await request.form()
        f = form.get("file")
        data = await f.read() if hasattr(f, "read") else b""
        if not data or len(data) > 25_000_000:
            return JSONResponse({"ok": False, "error": "empty or too large"}, status_code=400)
        from PIL import Image
        try:
            Image.open(io.BytesIO(data)).verify()       # images only
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "not an image"}, status_code=400)
        with _lock:
            d = folder(token)
            d.mkdir(parents=True, exist_ok=True)
            n = len(list(d.glob("*"))) + 1
            if n > MAX_FILES:
                return JSONResponse({"ok": False, "error": "too many photos"}, status_code=400)
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            (d / f"{n:03d}{ext}").write_bytes(data)
        return JSONResponse({"ok": True, "n": n})

    return Starlette(routes=[Route("/m/{t}", page), Route("/m/{t}/upload", upload, methods=["POST"])])


def start() -> dict:
    import uvicorn
    stop()
    ip = lan_ip()
    if not ip:
        raise RuntimeError("This computer is not on a local network. Connect it and the phone to the same Wi-Fi.")
    token = secrets.token_urlsafe(16)
    with socket.socket() as s:
        s.bind((ip, 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_app(token), host=ip, port=port, log_level="warning", access_log=False))
    t = threading.Thread(target=server.run, daemon=True, name="scriptavera-phone")
    t.start()
    _session.update(token=token, until=time.time() + LIFETIME, server=server, url=f"http://{ip}:{port}/m/{token}")
    timer = threading.Timer(LIFETIME, lambda: stop(token))
    timer.daemon = True                  # never keeps the app from closing
    timer.start()
    return status()


def stop(token: str | None = None) -> None:
    srv = _session.get("server")
    if srv and (token is None or _session.get("token") == token):
        srv.should_exit = True
        _session.clear()


def status() -> dict:
    tok = _session.get("token")
    files = sorted(p.name for p in folder(tok).glob("*")) if tok and folder(tok).exists() else []
    return {"active": bool(tok) and time.time() < _session.get("until", 0), "url": _session.get("url", ""), "files": files,
            "until": _session.get("until", 0)}


def file_bytes(name: str) -> bytes | None:
    tok = _session.get("token")
    if not tok or "/" in name or "\\" in name or name.startswith("."):
        return None
    f = folder(tok) / name
    return f.read_bytes() if f.exists() else None
