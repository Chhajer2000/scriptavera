# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Redrawing one figure, from finding it on the page to checking the result.

Each side does what it is reliable at. A model reads meaning: where each drawing is on the page (roughly), what its
labels say, and whether the redrawing shows the same thing as the drawing. Code measures and draws: the exact
region of the drawing, every line, curve, dot and arrow in it, and the clean figure made from them. Neither is
trusted alone:
  1. locate: one reading of the page points at each drawing; the box is then fitted to the ink (figlocate.py), and
     a drawing described twice by the transcription is found as such;
  2. measure: the region is read again from the original file at high resolution (figsource.py, figink.py), and
     the ink is measured (figmeasure.py);
  3. read: the model sees the figure, enlarged, with every group of handwriting numbered, and says what each label
     says (or that a group is part of the drawing, or not part of the figure);
  4. redraw: code draws the figure again, exact where the drawing clearly means it (figredraw.py);
  5. check, twice: the redrawing is laid over the drawing, and every line of the drawing must have a counterpart
     and nothing may be added (figcheck.py); then the model compares the two pictures side by side. A problem
     found either way is corrected once (by the model, editing the code), and checked again;
  6. what is still not right is said to the author, who has the last word on every figure.
Nothing is covered up: a figure that cannot be found, measured or compiled keeps the error, which the author sees next
to their own drawing, and a redrawing that matches the drawing only in part says how far."""

from __future__ import annotations

import io
import json
import math
import re
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import figcheck, figink, figlocate, figmeasure, figredraw, figsource, prompts
from .util import extract_tag, loads_lenient

AI_SIDE = 1800                  # longest side of an image sent to a model


class FigureError(Exception):
    """Why a figure could not be redrawn, in words for the author."""


# ===================================================================== images for the model

def jpeg(img: Image.Image, side: int = AI_SIDE, quality: int = 90) -> bytes:
    im = img.convert("RGB")
    if max(im.size) > side:
        im = im.copy()
        im.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _font(px: int):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:                                   # Pillow before 10.1
        return ImageFont.load_default()


def annotated(ink, labels, scale_to: int = AI_SIDE) -> Image.Image:
    """The figure with a numbered red box around each group of handwriting."""
    img = Image.fromarray(ink.rgb).convert("RGB")
    f = min(1.0, scale_to / max(img.size))
    if f < 1:
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    fs = max(14, int(0.42 * ink.text_h * f))
    font = _font(fs)
    for lb in labels:
        x0, y0, x1, y1 = (v * f for v in lb.box)
        pad = 3
        d.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(230, 0, 0), width=max(2, int(fs / 9)))
        t = str(lb.id)
        tw = d.textlength(t, font=font) if hasattr(d, "textlength") else fs * len(t) * 0.6
        tx, ty = max(0, x0 - pad - tw - 4), max(0, y0 - pad - fs - 2)
        d.rectangle([tx - 2, ty - 1, tx + tw + 2, ty + fs + 2], fill=(255, 255, 255))
        d.text((tx, ty), t, fill=(230, 0, 0), font=font)
    return img


def panel_crops(sc, limit: int = 3) -> list[Image.Image]:
    """Enlarged views of the separate pictures of a wide or crowded figure, so that small writing can be read."""
    ink = sc.ink
    H, W = ink.shape
    if len(sc.panels) < 2:
        return []
    out = []
    ranked = sorted(sc.panels, key=lambda pn: -len(pn.labels))
    for pn in ranked:
        if not pn.labels:
            continue
        x0, y0, x1, y1 = pn.box
        if max(x1 - x0, y1 - y0) * 2.0 > max(W, H):
            continue                                   # not much smaller than the whole figure
        m = int(0.6 * ink.text_h)
        crop = Image.fromarray(ink.rgb[max(0, y0 - m):min(H, y1 + m), max(0, x0 - m):min(W, x1 + m)])
        out.append(crop)
        if len(out) >= limit:
            break
    return out


# ===================================================================== 1. where the drawings are

def locate(p, n: int, recs: list[dict], ask, page_bytes: bytes, page: dict) -> None:
    """One reading of page n for its drawings `recs`: each gets its box (fitted to the ink) in rec["box"] (0..1000
    on the page image) and rec["pbox"] (page units); a drawing described twice gets rec["dup_of"]."""
    from .llm import LLMError
    user = "THE DRAWINGS THE TRANSCRIPTION MARKS ON THIS PAGE, IN READING ORDER\n" + "\n".join(
        f"{i}. {' '.join(str(r['desc']).split())}" for i, r in enumerate(recs, 1))      # no backslash: Python 3.11
    boxes: dict[int, list] = {}
    same: dict[int, int] = {}
    try:
        res = ask("vision_best", prompts.FIG_LOCATE.format(), user, images=[page_bytes], json_mode=True, max_out=900,
                  effort="low", temperature=0.0, label=f"Find the drawings on page {n}")
        data = loads_lenient(res.text) or {}
        for d in (data.get("drawings") or []) if isinstance(data, dict) else []:
            try:
                i = int(d.get("n"))
            except (TypeError, ValueError):
                continue
            if d.get("same_as"):
                try:
                    same[i] = int(d["same_as"])
                except (TypeError, ValueError):
                    pass
            b = d.get("box")
            if d.get("found", True) and isinstance(b, list) and len(b) == 4:
                try:
                    vals = [float(v) for v in b]
                except (TypeError, ValueError):
                    continue
                if max(vals) <= 1.0:
                    vals = [v * 1000 for v in vals]
                y0, x0, y1, x1 = vals
                if y1 - y0 >= 8 and x1 - x0 >= 8:
                    boxes[i] = [y0, x0, y1, x1]
    except LLMError as e:
        from .pipeline import passing
        if passing(e):
            raise
        for r in recs:
            r["locate_error"] = f"the model could not point to the drawings on page {n}: {e}"
    src = figsource.open_page(p.dir, page)
    th = figink.text_height_page(src)
    for i, r in enumerate(recs, 1):
        r["th"] = float(th)
        if i in same and 1 <= same[i] < i:
            r["dup_of"] = recs[same[i] - 1]["id"]
        if i not in boxes:
            r.setdefault("locate_error", f"the model did not find this drawing on page {n}")
            continue
        pbox = src.from_norm(boxes[i])
        others = [src.from_norm(b) for j, b in boxes.items() if j != i and j not in same]
        try:
            snapped, info = figlocate.snap(src, pbox, th, others)
        except Exception as e:  # noqa: BLE001 - kept on the figure: it is drawn with no box, which says why
            import traceback
            traceback.print_exc()
            r["locate_error"] = f"the drawing's box could not be fitted to its ink ({type(e).__name__}: {e})"
            continue
        r.pop("locate_error", None)
        r["pbox"] = [round(v, 3) for v in snapped]
        r["box"] = src.to_norm(snapped)
        r["exclude"] = info.get("exclude", [])[:200]
    # the same box twice is the same drawing
    for i, r in enumerate(recs):
        if r.get("dup_of") or not r.get("pbox"):
            continue
        for q in recs[:i]:
            if q.get("pbox") and not q.get("dup_of") and figlocate.same_drawing(q["pbox"], r["pbox"]):
                r["dup_of"] = q["id"]
                break


# ===================================================================== 3. reading the labels

def read_labels(sc, rec: dict, ask, notation: str) -> dict:
    """What each group of handwriting is. Returns the model's answer (parsed) and applies it to the scene."""
    imgs = [jpeg(Image.fromarray(sc.ink.rgb)), jpeg(annotated(sc.ink, sc.labels))]
    imgs += [jpeg(c, 1400) for c in panel_crops(sc)]
    H, W = sc.ink.shape
    lines = []
    for lb in sc.labels:
        x0, y0, x1, y1 = lb.box
        lines.append(f"box {lb.id}: [{round(y0 / H * 1000)}, {round(x0 / W * 1000)}, {round(y1 / H * 1000)}, {round(x1 / W * 1000)}]")
    found = _summary(sc)
    user = (f"WHAT THE TRANSCRIPTION SAYS THE FIGURE SHOWS\n{rec.get('desc', '')}\n\n"
            f"CONFIRMED NOTATION\n{notation}\n\n"
            f"WHAT THE PROGRAM MEASURED\n{found}\n\n"
            f"THE NUMBERED BOXES (on image 2; [ymin, xmin, ymax, xmax] from 0 to 1000)\n" + ("\n".join(lines) or "none"))
    res = ask("vision_best", prompts.FIG_READ.format(), user, images=imgs, json_mode=True, max_out=1600,
              effort="medium", temperature=0.0, label=f"Read the labels of figure {rec['id'][1:]} (page {rec['page']})")
    data = loads_lenient(res.text)
    if not isinstance(data, dict):
        data = {}
    data["_model"] = (res.model, res.provider)
    apply_reading(sc, data)
    return data


def _summary(sc) -> str:
    from collections import Counter
    c = Counter()
    for p in sc.prims:
        if p.role == "draw":
            c[{"line": "straight lines", "arc": "arcs", "curve": "curves", "circle": "circles", "ellipse": "ellipses",
               "closed": "closed curves", "dot": "dots"}.get(p.kind, p.kind)] += 1
    parts = [f"{n} {k}" for k, n in c.most_common()]
    if sc.arrows:
        parts.append(f"{len(sc.arrows)} arrowheads")
    if sc.rows:
        parts.append(f"{len(sc.rows)} dotted or dashed rows")
    if len(sc.panels) > 1:
        parts.append(f"{len(sc.panels)} separate pictures")
    return ", ".join(parts) or "nothing"


def apply_reading(sc, data: dict) -> None:
    """The model's reading applied to the measurement: labels joined and given their text, groups that are part of
    the drawing returned to it, ink that is not part of the figure set aside, labels the boxes missed added."""
    by_id = {lb.id: lb for lb in sc.labels}
    texts: dict[int, str] = {}
    for item in data.get("labels") or []:
        if not isinstance(item, dict):
            continue
        ids = [int(i) for i in (item.get("boxes") or []) if str(i).lstrip("-").isdigit() and int(i) in by_id]
        text = str(item.get("text") or "").strip()
        if not ids or not text:
            continue
        head = by_id[ids[0]]
        for i in ids[1:]:
            other = by_id[i]
            head.box = (min(head.box[0], other.box[0]), min(head.box[1], other.box[1]),
                        max(head.box[2], other.box[2]), max(head.box[3], other.box[3]))
            head.strokes = sorted(set(head.strokes) | set(other.strokes))
            head.prims = sorted(set(head.prims) | set(other.prims))
            other.kind = "merged"
        head.kind = "label"
        head.text = text
        texts[head.id] = text
    changed = False
    for key, kind in (("drawing", "drawing"), ("ignore", "ignore")):
        for i in data.get(key) or []:
            try:
                lb = by_id.get(int(i))
            except (TypeError, ValueError):
                continue
            if not lb or lb.kind == "label":
                continue
            lb.kind = kind
            for pid in lb.prims:
                sc.prims[pid].role = "draw" if kind == "drawing" else "ignore"
            if kind == "drawing":
                for sid in lb.strokes:
                    sc.kind[sid] = "draw"
                changed = True
    # small pieces of drawing ink inside writing that is not part of the figure go with it (a word read partly as
    # strokes of a drawing)
    gone = [lb.box for lb in sc.labels if lb.kind == "ignore"]
    if gone:
        h = sc.ink.text_h
        for st in sc.ink.strokes:
            ps = [p for p in sc.prims if p.stroke == st.id and p.role == "draw"]
            if not ps or sum(p.length for p in ps) > 3 * h:
                continue
            x0, y0, x1, y1 = st.bbox
            for b in gone:
                if x0 >= b[0] - 0.6 * h and x1 <= b[2] + 0.6 * h and y0 >= b[1] - 0.6 * h and y1 <= b[3] + 0.6 * h:
                    for p in ps:
                        p.role = "ignore"
                    break
    # boxes the model said nothing about stay candidates: their ink is neither drawn nor typeset, but kept apart
    H, W = sc.ink.shape
    for item in data.get("missed") or []:
        if not isinstance(item, dict):
            continue
        b, text = item.get("box"), str(item.get("text") or "").strip()
        if not text or not isinstance(b, list) or len(b) != 4:
            continue
        try:
            y0, x0, y1, x1 = (float(v) for v in b)
        except (TypeError, ValueError):
            continue
        box = (int(x0 / 1000 * W), int(y0 / 1000 * H), int(x1 / 1000 * W), int(y1 / 1000 * H))
        strokes = []
        for st in sc.ink.strokes:
            sx0, sy0, sx1, sy1 = st.bbox
            ix = max(0, min(sx1, box[2]) - max(sx0, box[0]))
            iy = max(0, min(sy1, box[3]) - max(sy0, box[1]))
            if ix * iy >= 0.5 * max(1, (sx1 - sx0) * (sy1 - sy0)):
                ps = [p for p in sc.prims if p.stroke == st.id]
                if any(p.kind == "line" and p.length >= 1.8 * sc.ink.text_h for p in ps):
                    continue
                strokes.append(st.id)
        new = figmeasure.Label(id=max([lb.id for lb in sc.labels] or [0]) + 1, box=box, strokes=strokes,
                               prims=[p.id for p in sc.prims if p.stroke in strokes], text=text, kind="label")
        for pid in new.prims:
            sc.prims[pid].role = "label"
        sc.labels.append(new)
        texts[new.id] = text
        changed = True
    sc.labels = [lb for lb in sc.labels if lb.kind != "merged"]
    changed = _absorb_into_labels(sc) or changed
    if changed:
        figmeasure.redo_shapes(sc)
    sc.texts = texts
    # arrowheads are meaning: those the model sees and the measurement missed (a small head drawn into a box, a
    # blurred one) are put where the model points
    tips = []
    for item in data.get("arrowheads") or []:
        t = item.get("tip") if isinstance(item, dict) else None
        if not isinstance(t, list) or len(t) != 2:
            continue
        try:
            ty, tx = float(t[0]), float(t[1])
        except (TypeError, ValueError):
            continue
        if 0 <= ty <= 1000 and 0 <= tx <= 1000:
            tips.append(((tx / 1000 * W, ty / 1000 * H), str(item.get("head") or "open")))
    sc.heads_added = figmeasure.add_heads(sc, tips) if tips else []


def _absorb_into_labels(sc) -> bool:
    """Small pieces of ink lying inside a label are part of its writing (the cross of an x, the dot of an i, a stroke
    that the removal of the paper's ruling cut off), not marks of the drawing."""
    h = sc.ink.text_h
    labels = [lb for lb in sc.labels if lb.kind == "label"]
    if not labels:
        return False
    own = {sid for lb in sc.labels for sid in lb.strokes}
    by_stroke: dict[int, list] = {}
    for p in sc.prims:
        by_stroke.setdefault(p.stroke, []).append(p)
    changed = False
    for st in sc.ink.strokes:
        ps = by_stroke.get(st.id, [])
        if st.id in own or not ps or any(p.role != "draw" for p in ps):
            continue
        if sum(p.length for p in ps) > 1.2 * h or any(p.kind in ("circle", "ellipse", "closed") for p in ps):
            continue
        x0, y0, x1, y1 = st.bbox
        if max(x1 - x0, y1 - y0) > 0.9 * h:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        m = 0.35 * h                                     # a box read off a picture is not exact
        for lb in labels:
            bx0, by0, bx1, by1 = lb.box
            if bx0 <= cx <= bx1 and by0 <= cy <= by1 and bx0 - m <= x0 and x1 <= bx1 + m and by0 - m <= y0 and y1 <= by1 + m:
                lb.strokes = sorted(set(lb.strokes) | {st.id})
                lb.prims = sorted(set(lb.prims) | {p.id for p in ps})
                for p in ps:
                    p.role = "label"
                changed = True
                break
    return changed


# ===================================================================== the whole figure

def draw(p, rec: dict, ask, notation: str, page: dict, page_bytes: bytes, instruction: str = "") -> None:
    """Draw figure `rec` (a figures.json item) again, and check it. Writes the cut-out drawing ({id}_orig.jpg), the
    compiled redrawing ({id}_tikz.png) and the overlay of the check ({id}_check.png); updates rec in place. Raises
    FigureError when the figure cannot be found, measured or compiled."""
    from . import paper as paperlib
    from .llm import router
    folder = p.dir / "figures"
    folder.mkdir(exist_ok=True)
    src = figsource.open_page(p.dir, page)
    if not rec.get("pbox"):
        locate(p, rec["page"], [rec], ask, page_bytes, page)
    if not rec.get("pbox"):
        raise FigureError(rec.get("locate_error") or f"the drawing was not found on page {rec['page']}")
    th = float(rec.get("th") or figink.text_height_page(src))
    rec["th"] = th
    ink = figink.read(src, tuple(rec["pbox"]), text_h_page=th, exclude=rec.get("exclude"))
    orig_img = Image.fromarray(ink.rgb)
    (folder / f"{rec['id']}_orig.jpg").write_bytes(jpeg(orig_img, 2000, 92))
    rec["source"] = "vector strokes" if ink.vector else ("scan" if src.kind != "pageimage" else "page image")
    notes: list[str] = []
    model_name = ""
    sc = m = None
    if instruction and rec.get("tikz"):
        # the author says the drawing is not right: the model edits the code, with the author's words
        tikz = rec["tikz"]
        png, err = paperlib.render_tikz(tikz) if tikz else (None, "")
        fixed, model_name = _fix(rec, ask, orig_img, png, tikz, [f"The author says: {instruction}"])
        if fixed:
            tikz = fixed
        else:
            notes.append("the model did not return a corrected drawing; the drawing is as before")
    else:
        try:
            sc = figmeasure.measure(ink)
        except Exception as e:  # noqa: BLE001 - said to the author
            import traceback
            traceback.print_exc()
            raise FigureError(f"the drawing could not be measured ({type(e).__name__}: {e})") from e
        from .llm import LLMError
        from .pipeline import passing
        try:
            reading = read_labels(sc, rec, ask, notation)
        except LLMError as e:
            if passing(e):
                raise
            raise FigureError(f"the labels of the drawing could not be read: {e}") from e
        model_name = reading.get("_model", ("", ""))[0]
        if reading.get("caption") and not rec.get("caption_edited"):
            rec["caption"] = re.sub(r"\s+", " ", str(reading["caption"])).strip()
        m = figredraw.build(sc, getattr(sc, "texts", {}))
        figredraw.beautify(m)
        figredraw.fit_labels(m, paperlib.measure_tex([lb.text for lb in m.labels]))
        local = _local_check(sc, m)
        arrays = local.pop("_res")
        rec["local"] = local
        tikz = figredraw.tikz(m)
        rec["changes"] = m.log
        figcheck.overlay(ink.rgb, arrays).save(folder / f"{rec['id']}_check.png")
        if local["covered"] < 0.9:
            notes.append(f"the redrawing has only {round(100 * local['covered'])}% of the lines of your drawing; "
                         "the overlay shows what is missing in red")
        if local["precise"] < 0.9:
            notes.append(f"{round(100 * (1 - local['precise']))}% of the redrawing is not in your drawing; "
                         "the overlay shows it in blue")
    png, err = paperlib.render_tikz(tikz)
    if not png:
        if re.search(r"(tikz|pgf\w*)\.sty'? not found|took too long", err or ""):
            err = ("no LaTeX engine could draw it: the TikZ package (called pgf) is missing from the LaTeX on this computer. "
                   "Install it (in MiKTeX Console: Packages, pgf), then press Draw again.")
        elif "no LaTeX engine" not in (err or ""):
            err = f"the redrawing does not compile: {err}"
    # 5. the model compares the two pictures; a problem is corrected once
    check = {"same": None, "problems": []}
    if png and not instruction:
        check = _ai_check(rec, ask, orig_img, png, rec.get("changes") or [], rec.get("local"))
        if check.get("problems"):
            fixed, _ = _fix(rec, ask, orig_img, png, tikz, [_problem_text(x) for x in check["problems"]])
            if fixed and fixed != tikz:
                png2, err2 = paperlib.render_tikz(fixed)
                if not png2:
                    notes.append(f"the model's correction did not compile ({err2}); the drawing is as before")
                else:
                    placed = _placed_check(ink, sc, m, png2) if sc is not None else None
                    if placed is None or placed["covered"] >= 0.8 * rec.get("local", {}).get("covered", 1.0):
                        tikz, png = fixed, png2
                        again = _ai_check(rec, ask, orig_img, png, [], None, label_suffix=" (after the correction)")
                        check = {**again, "corrected": [_problem_text(x) for x in check["problems"]]}
                    else:
                        notes.append("a correction by the model was refused: it moved the figure away from the drawing")
            elif not fixed:
                notes.append("the model did not return a correction")
    target = folder / f"{rec['id']}_tikz.png"
    if png:
        target.write_bytes(png)
    elif target.exists():
        target.unlink()
    top = router.peers("vision_best", True)
    rec["model"] = model_name
    rec["weak"] = bool(top) and bool(model_name) and not any(mm.id == model_name for mm in top)
    rec.update(tikz=tikz, tikz_ok=True if png else (None if "no LaTeX engine" in (err or "") else False), error="" if png else err,
               drawn=time.time(), choice="", method="measured", check=check, notes=notes,
               panels=len(sc.panels) if sc is not None else rec.get("panels", 0))
    if not rec.get("caption"):
        rec["caption"] = re.sub(r"\s+", " ", rec.get("desc", "")).strip()


def _local_check(sc, m) -> dict:
    ink = sc.ink
    orig = figcheck.drawing_ink(sc)
    drawn = figredraw.raster(m, ink.shape)
    tol = max(2.5 * ink.pen, 0.35 * ink.text_h)
    res = figcheck.compare(orig, drawn, tol, min_len=max(0.8 * ink.text_h, 4 * ink.pen))
    out = figcheck.report(res, ink.shape)
    out["_res"] = res
    return out


def _placed_check(ink, sc, m, png: bytes) -> dict | None:
    """The compiled picture laid over the drawing (for code the model edited), at the scale of the model `m` the code
    was written from."""
    try:
        F = figredraw.Frame(m)
        pic = figcheck.place(png, ink.shape, F.k)
        target = figredraw.raster(m, ink.shape)
        dx, dy = figcheck.best_shift(target, pic)
        drawn = figcheck.paste(ink.shape, pic, dx, dy)
        orig = figcheck.drawing_ink(sc)
        # the labels of the compiled picture are not compared
        for lb in m.labels:
            x0, y0, x1, y1 = lb.box
            drawn[max(0, y0 - 4):y1 + 4, max(0, x0 - 4):x1 + 4] = False
        tol = max(2.5 * ink.pen, 0.35 * ink.text_h)
        res = figcheck.compare(orig, drawn, tol, min_len=max(0.8 * ink.text_h, 4 * ink.pen))
        return figcheck.report(res, ink.shape)
    except Exception:  # noqa: BLE001 - without the overlay the correction is judged by the model's check alone
        import traceback
        traceback.print_exc()
        return None


def _problem_text(x) -> str:
    if isinstance(x, dict):
        parts = [str(x.get(k) or "").strip() for k in ("what", "where", "fix")]
        return " — ".join(q for q in parts if q)
    return str(x)


def _ai_check(rec, ask, orig_img, png: bytes, changes: list, local: dict | None, label_suffix: str = "") -> dict:
    from .llm import LLMError
    from .pipeline import passing
    lines = [f"WHAT THE TRANSCRIPTION SAYS THE FIGURE SHOWS\n{rec.get('desc', '')}"]
    if changes:
        lines.append("WHAT THE PROGRAM MADE EXACT WHEN REDRAWING (not differences)\n" + "; ".join(changes))
    if local and (local.get("missing") or local.get("extra")):
        lines.append("THE PROGRAM'S OWN OVERLAY FOUND (boxes on image 1, [ymin, xmin, ymax, xmax] from 0 to 1000)\n"
                     + "".join(f"- ink with no counterpart in the redrawing at {b}\n" for b in local.get("missing", []))
                     + "".join(f"- redrawing with no counterpart in the ink at {b}\n" for b in local.get("extra", [])))
    try:
        res = ask("vision_best", prompts.FIG_CHECK.format(), "\n\n".join(lines),
                  images=[jpeg(orig_img), jpeg(Image.open(io.BytesIO(png)))], json_mode=True, max_out=1200,
                  effort="low", temperature=0.0, label=f"Check figure {rec['id'][1:]} against the drawing{label_suffix}")
    except LLMError as e:
        if passing(e):
            raise
        return {"same": None, "problems": [], "error": str(e)}
    data = loads_lenient(res.text)
    if not isinstance(data, dict):
        return {"same": None, "problems": []}
    probs = [x for x in (data.get("problems") or []) if x]
    return {"same": bool(data.get("same")) and not probs, "problems": probs[:8]}


def _fix(rec, ask, orig_img, png: bytes | None, tikz: str, problems: list[str]) -> tuple[str, str]:
    from .llm import LLMError
    from .pipeline import _tikz_only, passing
    user = ("CORRECTIONS TO MAKE\n" + "\n".join(f"- {x}" for x in problems) + f"\n\nTHE CURRENT TIKZ CODE\n{tikz}")
    imgs = [jpeg(orig_img)] + ([jpeg(Image.open(io.BytesIO(png)))] if png else [])
    try:
        res = ask("vision_best", prompts.FIG_FIX.format(), user, images=imgs, max_out=6000, effort="medium",
                  label=f"Correct figure {rec['id'][1:]}")
    except LLMError as e:
        if passing(e):
            raise
        return "", ""
    out = _tikz_only(extract_tag(res.text, "tikz") or res.text)
    return out, res.model
