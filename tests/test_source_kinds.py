# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""What was uploaded decides the steps. Handwritten notes go through every step; a PDF made on a computer is read
with its exact words, and the author is asked only about its figures ("Your figures", passed when no figure needs a
decision); typed notes need no transcription, no figures and no check. The scout reads at least five pages (all of
them if there are fewer), then one page at a time while the topic's confidence still rises (stopping at 95%)."""
import json
from helpers import answers, isolate, new_project, wait
isolate("scriptavera_kinds_")
import tests.simulator as sim
from scriptavera import config, ingest, pipeline, store

# ---- the scout: at least five pages, then on until the topic is 85% clear
conf = []
real = sim.respond


def respond(label, system, user, vision, model=None):
    out = real(label, system, user, vision, model)
    if label.startswith("Identify topic") and conf:
        data = json.loads(out); data["confidence"] = conf.pop(0); out = json.dumps(data)
    return out


sim.respond = respond
conf[:] = [0.6, 0.8, 0.9]
p = new_project(pages=14)
assert store.source_plan(p.state, p.dir) == {"kind": "handwritten", "label": "Handwritten or scanned notes", "hidden": [], "names": {}}
pipeline.start(p); wait(p, "topiccheck")
read = [c["label"] for c in p.calls(1000) if c.get("label", "").startswith("Scout page")]
# 5 first (60%), then one page at a time: 80%, 90% (above 85%, still gaining), then 85% (no gain): stop
assert len(read) == 5 + 3, read
print("1. the scout read 5 pages, then one at a time while the confidence still rose, and stopped when it no longer did")

conf[:] = [0.95]
q = new_project(pages=3)
pipeline.start(q); wait(q, "topiccheck")
assert len([c for c in q.calls(1000) if c.get("label", "").startswith("Scout page")]) == 3
conf[:] = [0.99]
r = new_project(pages=9)
pipeline.start(r); wait(r, "topiccheck")
assert len([c for c in r.calls(1000) if c.get("label", "").startswith("Scout page")]) == 5, "a clear topic still needs 5 pages"
print("2. three pages: all three read; nine pages and a clear topic: still five read")
sim.respond = real


# ---- a PDF made on a computer: its words come from the file; the author is asked about figures only
def text_pdf(n: int, hidden: bool = False) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n)), n),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i in range(n):
        lines = b"".join(b"BT /F1 13 Tf %s 72 %d Td (Line %d of page %d: the partial sums of the reciprocal powers are bounded.) Tj ET\n"
                         % (b"3 Tr" if hidden else b"", 740 - 22 * k, k, i + 1) for k in range(30))
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objs.append(b"<< /Length %d >>\nstream\n" % len(lines) + lines + b"\nendstream")
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


# hidden text (what tablets and OCR put under handwriting) is not text of the page: those notes are handwriting
hid = ingest.ingest([("scan.pdf", text_pdf(2, hidden=True))], config.PROJECTS_DIR / "hidden" / "pages")
assert not any(pg.get("text_layer") for pg in hid) and not (config.PROJECTS_DIR / "hidden" / "textlayer.json").exists()
assert store.source_kind({"pages": hid}, config.PROJECTS_DIR / "hidden") == "handwritten"
print("3a. a PDF whose text is hidden under the page (a tablet's or OCR's reading of handwriting) counts as handwriting")

pid = store.new_id()
pages = ingest.ingest([("notes.pdf", text_pdf(3))], config.PROJECTS_DIR / pid / "pages")
assert all(pg.get("text_layer") for pg in pages)
meta = {"name": "Typed PDF", "domains": ["math.CA"], "subarea": "", "hints": "", "title": "",
        "problem": "Explicit bounds for the partial sums of the series of reciprocal powers.", "keywords": ["partial sums", "bounds"],
        "key_papers": [], "authors": [{"name": "Test Author"}], "known_refs": [], "journal": None, "statements": {}}
t = store.create(meta, pages, pid)
plan = store.source_plan(t.state, t.dir)
assert plan["kind"] == "text_pdf" and plan["hidden"] == [] and plan["names"] == {"review": "Your figures"}, plan
pipeline.start(t); wait(t, "topiccheck")
assert not any(c.get("label", "").startswith("Scout page") for c in t.calls(1000)), "the scout reads the words from the file"
assert t.load("scout")["1"]["model"] == "text layer of the PDF"
pipeline.submit_topic(t, {"action": "confirm"})
pipeline.start(t); wait(t, "confirm")
if t.state["stages"]["confirm"]["status"] == "waiting":
    pipeline.submit_confirm(t, answers(t))
pipeline.start(t); wait(t, "review", seconds=900)
survey_calls = [c for c in t.calls(1000) if c.get("label", "").startswith("Survey page")]
assert survey_calls, "the notation is catalogued"
figs = [f for f in (t.load("figures") or {}).get("items", []) if f.get("choice") not in ("tikz", "orig", "none", "explain")]
if figs:                                                    # a figure for the paper: the step stops, for the figures only
    assert t.state["stages"]["review"]["status"] == "waiting" and "review" not in store.hidden_stages(t.state, t.dir)
    for f in figs:
        pipeline.set_figure(t, f["id"], choice="orig")
    t.set_stage("review", status="pending")
pipeline.start(t); wait(t, "review", seconds=900)
assert t.state["stages"]["review"]["status"] == "done" and t.state["stages"]["review"].get("skipped") and t.load("approved")
assert "review" in store.hidden_stages(t.state, t.dir), "with no figure to decide, the step is not shown"
print(f"3. a PDF made on a computer: words from the file, {'a figure asked about, then ' if figs else ''}the transcript approved without stopping")

# ---- the kind follows every page: one handwritten page makes the whole upload handwritten
st = json.loads(json.dumps(t.state)); st["pages"].append({"n": 99, "skip": False})
assert store.source_kind(st, t.dir) == "handwritten"
summary = next(x for x in store.list_projects() if x["id"] == t.id)
assert summary["total"] == len(store.STAGE_IDS) - 1 and summary["hidden"] == ["review"], summary
print("4. one page read from an image makes the upload handwritten; the list of papers counts only the steps shown")
print("source kinds ok")
