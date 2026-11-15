# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Browser walk through a statement left out that a kept one uses. The author leaves a lemma out and confirms the
statements: a box opens that names the statement which uses it and shows both. A reason that is not enough gets an
answer in the box; another argument that holds closes it, and the statements are confirmed without the lemma.
    python tests/ui_dispute.py [folder for screenshots]"""
import os, pathlib, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright
from helpers import ROOT, answers, isolate, new_project, past_topic, wait

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8795"
work = isolate("scriptavera_ui5_")
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
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/scope"); pg.wait_for_selector('[data-act="stmtsubmit"]'); time.sleep(0.8)
        for k in range(pg.locator('[data-act="stab"]').count()):          # the statements are on tabs by kind: the one with R2
            pg.locator('[data-act="stab"]').nth(k).click(); time.sleep(0.3)
            if pg.locator('[data-act="stmttreat"][data-id="R2"][data-v="omit"]').count():
                break
        pg.click('[data-act="stmttreat"][data-id="R2"][data-v="omit"]'); time.sleep(0.6)
        assert pg.locator("#overlay").is_hidden(), "leaving a statement out asks nothing yet"
        pg.click('[data-act="stmtsubmit"]'); pg.wait_for_selector("#dpdialog"); time.sleep(0.5)
        text = pg.inner_text("#dpdialog")
        assert "which you are leaving out" in text and "Kept:" in text and "Left out:" in text and "(R5)" in text and "(R2)" in text, text[:1200]
        assert "A paper cannot use a result it does not contain" in text and "Leave" in pg.inner_text('[data-act="dpomit"]')
        assert api("GET", f"/api/projects/{p.id}")["state"]["stages"]["scope"]["status"] == "waiting", "nothing is confirmed"
        pg.screenshot(path=str(OUT / "q1_box.png"))
        pg.click('[data-act="dptalk"]'); time.sleep(0.4)
        assert pg.locator("#dpchat .msg").count() == 1, "an empty reply is not sent"
        pg.fill("[data-dptext]", "It is independent."); pg.click('[data-act="dptalk"]')
        until(pg, lambda: pg.locator("#dpchat .msg").count() == 3 and "Checking" not in pg.inner_text("#dpchat"), 40)
        text = pg.inner_text("#dpchat")
        assert "It is independent." in text and "does not replace that step" in text, text
        assert pg.input_value("[data-dptext]") == "" and not pg.locator("#overlay").is_hidden(), "not enough: the box stays for another reply"
        pg.screenshot(path=str(OUT / "q2_not_enough.png"))
        pg.fill("[data-dptext]", "Here is another argument: compare each term with the integral directly."); pg.click('[data-act="dptalk"]')
        until(pg, lambda: pg.locator("#overlay").is_hidden(), 40)
        bb = at("outline")                                   # the statements were confirmed by themselves once it was settled
        items = {x["id"]: x for x in bb["writing"]["items"]}
        assert items["R2"]["treatment"] == "omit" and "R2" not in items["R5"]["depends"] and bb["writing"]["disputes"] == [], items["R5"]
        assert "R2" not in [i for s in bb["writing"]["outline"] for i in s["items"]]
        b.close()
    assert not errors, errors
finally:
    srv.terminate()
print("ui dispute ok")
