# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Where the lines of a crowded page go, asked of a model that sees the page.

layout.py matches the transcript to the lines of handwriting by length alone, at no cost. On a page where that
matching is not sure (a piece without its line, lines of writing left over, a poor fit), a model is shown the page
with every line of handwriting framed and numbered, and the transcript cut into numbered pieces, and says which piece
stands on which line: one call per page. Its answer is kept beside the page image, for the text it was given; when
the page's text changes (a correction), the page is asked about again.

It runs in the background: the page is shown laid out by length meanwhile, and as placed by the model once its
answer is in. The author can turn it off for a paper (and, on a computer, by default in Settings)."""
from __future__ import annotations

import contextvars
import hashlib
import io
import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, layout, prompts
from .llm import LLMError, router
from .util import extract_tag

VERSION = 1
RETRY_AFTER = 5 * 60                  # a page whose placing failed (no key free, a bad answer) is asked again after this
WAITS = (20, 60)                      # ... but first asked again in the background, after these pauses (seconds)

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="scriptavera-place")
_busy: set[tuple[str, int, str]] = set()
_lock = threading.Lock()


def enabled(p) -> bool:
    """Whether crowded pages of this paper are placed by a model: the paper's own choice, else the default."""
    own = p.state.get("layout_ai")
    if own is not None:
        return bool(own)
    return bool(config.load_settings().get("layout_ai", True))


def _key(text: str) -> str:
    return hashlib.sha256(f"{VERSION}|{layout.VERSION}|{text}".encode("utf-8")).hexdigest()[:16]


def _path(p, n: int) -> Path:
    return p.dir / "pages" / f"place_p{int(n):03d}.json"


def cached(p, n: int, text: str) -> dict | None:
    """What was found for this page as it reads now: {"assign": {piece: [lines]}} or {"failed": time}."""
    f = _path(p, n)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("key") != _key(text):
        return None
    if data.get("assign"):
        return {"assign": {int(k): v for k, v in data["assign"].items()}}
    if data.get("failed") and time.time() - data["failed"] < RETRY_AFTER:
        return {"failed": data["failed"]}
    return None


def busy(p, n: int, text: str) -> bool:
    with _lock:
        return (p.id, int(n), _key(text)) in _busy


def framed(img: Image.Image, lines: list[dict], skip: set[int]) -> tuple[bytes, list[int]]:
    """The page with each line of handwriting framed in red and numbered L1, L2, ... in reading order. Returns the
    image (JPEG) and, for each number, the index of its line."""
    im = img.convert("RGB")
    if im.width > 1600:
        im = im.resize((1600, round(im.height * 1600 / im.width)))
    W, H = im.size
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=max(14, W // 70))
    except TypeError:                                   # an older Pillow: the small default font
        font = ImageFont.load_default()
    order = sorted((j for j, l in enumerate(lines) if not l.get("thin") and j not in skip),
                   key=lambda j: (round(lines[j]["y"] / 0.015), lines[j]["x"]))
    for num, j in enumerate(order, 1):
        l = lines[j]
        x1, y1, x2, y2 = l["x"] * W - 3, l["y"] * H - 3, (l["x"] + l["w"]) * W + 3, (l["y"] + l["h"]) * H + 3
        draw.rectangle([x1, y1, x2, y2], outline=(220, 30, 30), width=2)
        tag = f"L{num}"
        tb = draw.textbbox((0, 0), tag, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        tx, ty = max(0, x1 - tw - 6), max(0, y1)          # the number just left of its box, where it covers least
        draw.rectangle([tx - 2, ty - 2, tx + tw + 2, ty + th + 4], fill=(255, 255, 255), outline=(220, 30, 30))
        draw.text((tx, ty - tb[1]), tag, fill=(200, 0, 0), font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue(), order


def parse(answer: str, n_pieces: int, n_lines: int) -> dict[int, list[int]]:
    """The model's lines "P3: L5 L6" as {piece number: [line numbers]}, both counted from 1; "none" gives []."""
    body = extract_tag(answer, "places") or answer
    out: dict[int, list[int]] = {}
    for m in re.finditer(r"P\s*(\d+)\s*[:=\-]+\s*([^\n]*)", body):
        k = int(m.group(1))
        if not 1 <= k <= n_pieces:
            continue
        out[k] = [int(x) for x in re.findall(r"L\s*(\d+)", m.group(2)) if 1 <= int(x) <= n_lines]
    return out


def place(p, n: int, text: str, lines: list[dict], figure_boxes: dict | None = None) -> dict | None:
    """Ask the model where the pieces of page n stand, and keep the answer. Returns {piece index: [line indices]}
    or None when no good answer came."""
    from .pipeline import Run, _page_bytes
    ps = layout.pieces(text)
    texty = [i for i, q in enumerate(ps) if q["kind"] != "figure"]
    boxes = [b for b in (figure_boxes or {}).values() if b]
    skip = {j for j, l in enumerate(lines)
            if any(b[1] / 1000 <= l["x"] + l["w"] / 2 <= b[3] / 1000 and b[0] / 1000 <= l["y"] + l["h"] / 2 <= b[2] / 1000 for b in boxes)}
    with Image.open(io.BytesIO(_page_bytes(p, n))) as img:
        image, order = framed(img, lines, skip)
    listing = "\n".join(f"P{k}: {ps[i]['t']}" for k, i in enumerate(texty, 1))
    user = (f"PAGE {n}: the lines of handwriting are framed and numbered L1 to L{len(order)}.\n\n"
            f"THE TRANSCRIPTION, IN PIECES\n{listing}")
    hooks = Run(p)
    hooks.stage = "review"
    res = router.chat("vision_best", prompts.PLACE_LINES, user, images=[image], max_out=6000, effort="low",
                      label=f"Place the lines of page {n}", hooks=hooks)
    got = parse(res.text, len(texty), len(order))
    found = sum(1 for v in got.values() if v)
    if found < 0.6 * len(texty):
        return None
    return {texty[k - 1]: [order[x - 1] for x in v] for k, v in got.items()}


def _save(p, n: int, text: str, data: dict) -> None:
    f = _path(p, n)
    f.write_text(json.dumps({"key": _key(text), "at": time.time(), **data}), encoding="utf-8")


def start(p, n: int, text: str, lines: list[dict], figure_boxes: dict | None = None) -> bool:
    """Place page n in the background, unless it is being placed already. True when it is (or now will be)."""
    k = (p.id, int(n), _key(text))
    with _lock:
        if k in _busy:
            return True
        _busy.add(k)

    def work():
        try:
            assign = None
            for wait in (0, *WAITS):                      # an empty answer, a limit reached: asked again soon
                time.sleep(wait)
                try:
                    assign = place(p, n, text, lines, figure_boxes)
                except LLMError:
                    assign = None
                except Exception:  # noqa: BLE001 - an unexpected answer: asked again like an empty one
                    traceback.print_exc()
                    assign = None
                if assign:
                    break
            _save(p, n, text, {"assign": {str(i): v for i, v in assign.items()}} if assign else {"failed": time.time()})
        except Exception:  # noqa: BLE001 - the page stays laid out by length
            traceback.print_exc()
            _save(p, n, text, {"failed": time.time()})
        finally:
            with _lock:
                _busy.discard(k)
            try:
                from .pipeline import emit
                emit(p.id, "layout", page=int(n))
            except Exception:  # noqa: BLE001
                pass
    _pool.submit(contextvars.copy_context().run, work)       # the same owner's keys
    return True
