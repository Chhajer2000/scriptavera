# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Browser walk through the drawings: in step 9 only the copy of each drawing is checked; at the structure each
drawing is decided with the whole paper in view (the picture of the method printed, the one inside a proof only
explaining it), and the author prints the second after all; at the review of the proofs every figure for print is
shown beside the author's drawing, the author keeps their own drawing for one, and approves with the other confirmed.
    python tests/ui_drawings.py [folder for screenshots]"""
import os, pathlib, subprocess, sys, tempfile, time
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"
from playwright.sync_api import sync_playwright
from helpers import ROOT, answers, isolate, new_project, past_topic, wait

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8793"
work = isolate("scriptavera_ui3_")
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
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        # ---- step 9: the copies only
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/review"); pg.wait_for_selector(".review"); time.sleep(0.8)
        card = pg.inner_text("#fig-f1")
        assert "Is the copy what you drew?" in card and "decided with the structure" in card, card
        assert pg.locator('#fig-f1 .choice[data-v="explain"]').count() == 0 and pg.locator("#fig-f1 .capbox").count() == 0
        pg.screenshot(path=str(OUT / "d1_copy.png"))
        for _ in range(20):                                           # every item settled: Approve opens
            left = pg.evaluate("(() => { const x = openItems()[0]; return x ? x.type + ':' + (x.f ? x.f.id : x.id) + ':' + x.n : '' })()")
            if not left:
                break
            t, i, n = left.split(":"); pg.click(f'.rv3-row .rv-p[data-n="{n}"]'); time.sleep(0.3)
            pg.locator(f'#fig-{i} .choice[data-v="orig"]' if t == "fig" else f'#flag-{i} [data-act="dropflag"]').click(); time.sleep(0.4)
        pg.click('#rvpanel [data-act="rvmode"][data-v="approve"]'); time.sleep(0.3)
        summary = pg.inner_text(".approve-card")
        assert "2 drawings copied" in summary and "decided with the structure" in summary, summary
        pg.click('.approve-card [data-act="approve"]'); time.sleep(1.0)
        bb = at("scope")
        for iid in (bb["writing"] or {}).get("open_questions", []):
            api("POST", f"/api/projects/{p.id}/statement", {"id": iid, "answer": "The bound is $\\log(n+1)$."})
        api("POST", f"/api/projects/{p.id}/statement", {"id": "R2", "treatment": "keep"})
        api("POST", f"/api/projects/{p.id}/scope", {"kind": "article"})
        at("outline")
        # ---- the structure: each drawing decided with the whole paper in view
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/outline"); time.sleep(1.5)
        if not pg.locator("#ofigs").count():
            pg.screenshot(path=str(OUT / "d2_fail.png"))
            raise SystemExit("no drawings on the structure screen: " + pg.inner_text("#main")[:3000] + "\nERRORS " + str(errors))
        assert "Printed in the paper" in pg.inner_text("#ofig-f1") and "only explains an argument" in pg.inner_text("#ofig-f2")
        pg.screenshot(path=str(OUT / "d2_structure.png"))
        pg.click('#ofig-f2 [data-act="ofiguse"][data-v="paper"]'); time.sleep(0.8)
        until(pg, lambda: "Printed in the paper" in pg.inner_text("#ofig-f2"), 20)
        assert pg.locator("#ofig-f2 [data-ofigcap]").count() == 1 and "your decision" in pg.inner_text("#ofig-f2")
        pg.fill("#ofig-f2 [data-ofigcap]", "The comparison of $1/k$ with the integral."); pg.click("h1"); time.sleep(0.8)
        figs_ = {f["id"]: f for f in api("GET", f"/api/projects/{p.id}")["writing"]["figures"]}
        assert figs_["f2"]["caption"] == "The comparison of $1/k$ with the integral." and figs_["f2"]["use"] == "paper", figs_["f2"]
        pg.click('[data-act="oconfirm"]'); time.sleep(1.0)
        at("proofcheck")
        # ---- the proofs: every figure for print beside the author's drawing
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/proofcheck"); pg.wait_for_selector("#pfigs"); time.sleep(0.8)
        assert pg.locator("#pfigs .figcard").count() == 2 and "Your drawing" in pg.inner_text("#figpro-f1")
        pg.screenshot(path=str(OUT / "d3_proofs.png"))
        pg.click('#figpro-f1 .choice[data-v="copy"]'); time.sleep(0.8)
        until(pg, lambda: ((api("GET", f"/api/projects/{p.id}")["writing"]["figures"][0].get("pro") or {}).get("choice") == "copy"), 20)
        for _ in range(10):                                  # a proof that needs a decision: kept as it is
            left_ = api("GET", f"/api/projects/{p.id}")["writing"].get("open_proofs") or []
            if not left_:
                break
            pg.click(f'#proof-{left_[0]} [data-act="pdecide"][data-v="keep"]'); time.sleep(0.8)
        pg.click('[data-act="papprove"]'); pg.wait_for_selector(".dialog, dialog, .modal", timeout=5000); time.sleep(0.3)
        text = pg.inner_text("body")
        assert "not confirmed yet" in text and "page 3" in text, text[-1500:]
        pg.screenshot(path=str(OUT / "d4_approve.png"))
        pg.click('[data-act="cfyes"]'); time.sleep(1.0)
        at("proofcheck", ("done",), 30)
        b.close()
    pro = figpro.entries(p)
    assert pro["f1"]["choice"] == "copy" and not pro["f1"].get("auto") and pro["f2"]["choice"] in ("pro", "copy") and pro["f2"]["auto"], pro
    assert not errors, errors
finally:
    srv.terminate()
print("ui drawings ok")
