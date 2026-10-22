# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Turn uploaded PDFs and images into page images the models can read."""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageOps

LONG_SIDE = 1800      # pixels; enough to resolve sub- and superscripts in handwriting
THUMB_WIDTH = 420
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def _stats(img: Image.Image) -> dict:
    """Cheap signals: how much ink is on the page, and how colourful it is.

    Ink is counted at full size against the page's own background, so thin pen or pencil strokes on
    grey or tinted paper still register."""
    gray = img.convert("L")
    hist = gray.histogram()
    total = sum(hist) or 1
    acc, median = 0, 255
    for level, count in enumerate(hist):
        acc += count
        if acc >= total / 2:
            median = level
            break
    dark = sum(hist[:int(median * 0.75)]) / total
    small = img.copy()
    small.thumbnail((400, 400))
    sat = small.convert("HSV").split()[1]
    n = sum(sat.histogram()) or 1
    sat_mean = sum(i * c for i, c in enumerate(sat.histogram())) / n
    return {"ink": round(dark, 4), "saturation": round(sat_mean, 1)}


def _save(img: Image.Image, out_dir: Path, n: int) -> dict:
    img = img.convert("RGB")
    w, h = img.size
    scale = LONG_SIDE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    name = f"p{n:03d}.jpg"
    img.save(out_dir / name, "JPEG", quality=86, optimize=True)
    thumb = img.copy()
    thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
    thumb.save(out_dir / f"t{n:03d}.jpg", "JPEG", quality=78)
    st = _stats(img)
    return {
        "n": n, "file": name, "thumb": f"t{n:03d}.jpg", "w": img.size[0], "h": img.size[1],
        "ink": st["ink"], "saturation": st["saturation"],
        # an empty page has almost no dark pixels
        "blank": st["ink"] < 0.0006,
        # a strongly coloured page with little ink is probably a notebook cover
        "decorative": st["saturation"] > 28,
        "skip": False, "kind": "",
    }


def ingest(files: list[tuple[str, bytes]], out_dir: Path, start: int = 0) -> list[dict]:
    """Render every page of every file, in upload order, numbered from start + 1. Returns page records."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    layers: dict[str, str] = {}
    n = start
    for name, data in files:
        ext = Path(name).suffix.lower()
        if ext == ".pdf" or data[:5] == b"%PDF-":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(data)
            try:
                for i in range(len(pdf)):
                    page = pdf[i]
                    w, h = page.get_size()
                    bitmap = page.render(scale=min(4.0, LONG_SIDE / max(w, h)))
                    img = bitmap.to_pil()
                    n += 1
                    rec = _save(img, out_dir, n)
                    rec["source"] = f"{name} p.{i + 1}"
                    pages.append(rec)
                    try:
                        layer = page.get_textpage().get_text_range()
                    except Exception:  # noqa: BLE001 - a page without a usable text layer is read from its image
                        layer = ""
                    if len(re.sub(r"\s", "", layer)) >= 60:
                        layers[str(n)] = layer[:8000]
                    page.close()
            finally:
                pdf.close()
        elif ext in IMAGE_EXT:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            n += 1
            rec = _save(img, out_dir, n)
            rec["source"] = name
            pages.append(rec)
        else:
            raise ValueError(f"Unsupported file type: {name}. Upload a PDF, page images, or typed notes (.tex, an Overleaf .zip, .md, .txt).")
    if not pages:
        raise ValueError("No pages found in the upload.")
    if layers:                                  # the words of a computer-made PDF, given to the reader beside each page image
        import json
        f = out_dir.parent / "textlayer.json"
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        f.write_text(json.dumps({**old, **layers}, ensure_ascii=False), encoding="utf-8")
    for p in pages:
        if p["blank"]:
            p["skip"], p["kind"] = True, "blank"
    if all(p["skip"] for p in pages):          # never refuse a whole upload on a pixel heuristic
        for p in pages:
            p["skip"], p["kind"], p["blank"] = False, "", False
    return pages
