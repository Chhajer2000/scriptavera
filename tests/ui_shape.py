# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Browser walk through the revision: after the first draft is approved, "Your plan" shows the paper as it is and as
it would be, and each change with its new statements in full and its proofs in outline; the author accepts the merge,
the repeated proof and the new sections and rejects the argument the app found. Then "Your review of the new proofs"
shows the proof written for the merge beside the two it replaces, and says that the repeated proof was dropped by its
check; the author accepts, and the second draft has that shape.
    python tests/ui_shape.py [folder for screenshots]"""
import os, pathlib, subprocess, sys, tempfile, time
os.environ["SCRIPTAVERA_MOCK_SHAPE"] = "merge,argument,similar,sections"
from playwright.sync_api import sync_playwright
from helpers import ROOT, answers, isolate, new_project, past_topic, wait

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8794"
work = isolate("scriptavera_ui4_")
from scriptavera import figpro, pipeline
p = new_project()
past_topic(p)
pipeline.start(p); wait(p, "confirm"); pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
SERVER = f"""
import sys, os, pathlib; sys.path.insert(0, {str(ROOT)!r})
from scriptavera import config
w = pathlib.Path({str(work)!r})
config.PROJECTS_DIR = w / 'projects'; config.SETTINGS_PATH = w / 'settings.json'; config.ENV_PATH = w / '.env'
config.MEMORY_DIR = w / 'memory'; config.PHONE_DIR = w / 'phone_uploads'; config.IDENTITY_DIR = w / 'identity'
import uvicorn; from scriptavera.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port={PORT}, log_level='warning')
"""
srv = subprocess.Popen([sys.executable, "-c", SERVER], env=dict(os.environ, SCRIPTAVERA_MOCK="1", SCRIPTAVERA_MOCK_DELAY="0"))
errors = []


import requests
B = f"http://127.0.0.1:{PORT}"


def api(method, path, body=None):
    r = requests.request(method, B + path, json=body, timeout=120)
    assert r.ok, (path, r.status_code, r.text[:400])
    return r.json()


def at(stage, statuses=("waiting",), seconds=120):
    """The project as the server has it, once the stage has one of the statuses and nothing runs."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        b_ = api("GET", f"/api/projects/{p.id}")
        if b_["state"]["stages"][stage].get("status") in statuses and not b_["state"]["running"]:
            return b_
        time.sleep(0.3)
    raise SystemExit(f"timeout waiting for {stage}")


def until(pg, test, seconds=60):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if test():
            return
        time.sleep(0.3)
    raise SystemExit("timeout: " + pg.inner_text("#main")[:1500])


try:
    time.sleep(2.0)
    api("POST", f"/api/projects/{p.id}/review", {"figures": {"f1": "orig"}})
    bb = at("scope")
    for iid in (bb["writing"] or {}).get("open_questions", []):
        api("POST", f"/api/projects/{p.id}/statement", {"id": iid, "answer": "The bound is $\\log(n+1)$."})
    api("POST", f"/api/projects/{p.id}/statement", {"id": "R2", "treatment": "keep"})
    api("POST", f"/api/projects/{p.id}/scope", {"kind": "article"})
    at("outline"); api("POST", f"/api/projects/{p.id}/outline", {"action": "confirm"})
    bb = at("proofcheck")
    for iid in bb["writing"].get("open_proofs") or []:
        api("POST", f"/api/projects/{p.id}/proofs", {"action": "keep", "id": iid})
    api("POST", f"/api/projects/{p.id}/proofs", {"action": "approve", "assume": True})
    at("draftcheck"); api("POST", f"/api/projects/{p.id}/draftcheck", {"approve": True})
    bb = at("reshapecheck")
    kinds = [x["kind"] for x in bb["reshape"]["proposals"]]
    assert sorted(kinds) == ["argument", "merge", "sections", "similar"], (kinds, bb["reshape"]["dropped"])
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}"); pg.wait_for_selector(".dcard"); time.sleep(0.8)
        text = pg.inner_text("#main")
        assert "Your plan for the revision" in text and "One statement instead of several" in text and "becomes part (i)" in text, text[:1500]
        assert "How its proof will go (outline" in text and "No proof has been written yet" in text, text[:2500]
        sk = pg.inner_text("#shape-skeleton")
        assert "The first draft" in sk and "If you accept every change of the plan" in sk and "one statement" in sk and "another argument" in sk, sk
        assert "This argument is not in your notes: the app found it." in text and "\\emph" not in text
        assert pg.locator('[data-act="shapeconfirm"]').is_disabled()
        pg.screenshot(path=str(OUT / "s1_plan.png"), full_page=False)
        ids = {x["kind"]: x["id"] for x in bb["reshape"]["proposals"]}
        for kind, v in (("merge", "accept"), ("argument", "reject"), ("similar", "accept"), ("sections", "accept")):
            pg.click(f'#shape-{ids[kind]} [data-act="shapepick"][data-v="{v}"]'); time.sleep(0.4)
        pg.locator(f'#shape-{ids["merge"]}').scroll_into_view_if_needed(); pg.screenshot(path=str(OUT / "s2_merge_plan.png"))
        assert "have the proofs written" in pg.inner_text('[data-act="shapeconfirm"]')
        pg.click('[data-act="shapeconfirm"]'); time.sleep(1.0)
        bb = at("reproofcheck")
        assert [x["kind"] for x in bb["reshape"]["written"]] == ["merge"] and len(bb["reshape"]["failed"]) == 1, bb["reshape"]["failed"]
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/reproofcheck"); pg.wait_for_selector(f'#shape-w{ids["merge"]}'); time.sleep(0.8)
        text = pg.inner_text("#main")
        assert "Your review of the new proofs" in text and "Proof of (i)." in text and "The proofs it replaces" in text, text[:2000]
        assert "dropped: the first draft's version stands" in text and "A repeated proof not written again" in text, text[-1500:]
        assert pg.locator('[data-act="proofsconfirm"]').is_disabled()
        pg.screenshot(path=str(OUT / "s3_new_proofs.png"))
        pg.click(f'#shape-w{ids["merge"]} [data-act="shapepick"][data-v="accept"]'); time.sleep(0.4)
        pg.click('[data-act="proofsconfirm"]'); time.sleep(1.0)
        at("redraft", ("done",), 240)
        names = [x.inner_text() for x in pg.locator(".step .step-name, .steps .name").all()][:40]
        b.close()
    from scriptavera import paper as paperlib
    body = p.load("body")
    tex = paperlib.blocks_to_latex(body["blocks"])
    assert "\\begin{enumerate}" in tex and "Bounds from above" in tex and "telescopes" not in tex and len(body["shape"]["applied"]) == 2, body["shape"]
    assert "\\label{res:R4}" in paperlib.blocks_to_latex(p.load("draft_first")["blocks"]), "the first draft is kept"
    assert not errors, errors
finally:
    srv.terminate()
print("ui shape ok")
