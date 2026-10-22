# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Browser walk through the review of drawings: a drawing that only explains a proof is settled without a question
and shown with the reason; the author prints it after all, keeps it out again, and approves.
    python tests/ui_drawings.py [folder for screenshots]"""
import os, pathlib, subprocess, sys, tempfile, time
os.environ["SCRIPTAVERA_MOCK_DRAWINGS"] = "1"
from playwright.sync_api import sync_playwright
from helpers import ROOT, answers, isolate, new_project, past_topic, wait

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8793"
work = isolate("scriptavera_ui3_")
from scriptavera import pipeline
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
try:
    time.sleep(2.0)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/#p/{p.id}/review"); pg.wait_for_selector(".review"); time.sleep(0.8)
        line = pg.inner_text(".autoline")
        assert "One drawing only explains your argument" in line and "page 3" in line, line
        assert pg.locator("#fig-f2").count() == 0, "a sure explanation is not asked about"
        assert "For the paper." in pg.inner_text("#fig-f1 .figverdict")
        pg.screenshot(path=str(OUT / "d1_todo.png"))
        pg.click('.autoline [data-act="rvtab"]'); time.sleep(0.5)
        row = pg.locator(".srow", has_text="Page 3")
        assert "Not printed" in row.inner_text() and "Decided by the app" in row.inner_text(), row.inner_text()
        pg.screenshot(path=str(OUT / "d2_settled.png"))
        assert row.locator("button").count() == 1, "one button: Change"
        row.locator('[data-act="figreopen"]').click(); pg.wait_for_selector("#fig-f2"); time.sleep(0.3)
        pg.click('#fig-f2 [data-act="figprint"]')
        pg.wait_for_selector("#fig-f2 .figpair", timeout=30000); time.sleep(0.4)
        assert "You asked for it to be printed" in pg.inner_text("#fig-f2 .figverdict")
        pg.screenshot(path=str(OUT / "d3_printed.png"))
        pg.click('#fig-f2 .choice[data-v="explain"]'); time.sleep(0.6)
        assert pg.locator("#fig-f2").count() == 0 and "Not printed" in pg.inner_text("#toast")
        for _ in range(20):                                           # every other item settled: Approve opens
            left = pg.evaluate("(() => { const x = openItems()[0]; return x ? x.type + ':' + x.f.id : '' })()")
            if not left:
                break
            t, i = left.split(":")
            pg.locator(f'#fig-{i} .choice[data-v="orig"]' if t == "fig" else f'#flag-{i} [data-act="dropflag"]').click(); time.sleep(0.4)
        assert "1 only explaining your argument (not printed)" in pg.inner_text(".approve-card"), pg.inner_text(".approve-card")
        pg.click('[data-act="approve"]'); time.sleep(1.5)
        b.close()
    figs = {f["id"]: f for f in p.load("figures")["items"]}
    assert figs["f2"]["choice"] == "explain" and not figs["f2"]["auto"] and p.load("approved"), figs["f2"]
    assert not errors, errors
finally:
    srv.terminate()
print("ui drawings ok")
