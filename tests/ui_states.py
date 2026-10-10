# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Second browser walk: waiting for the strongest model, letting the next best one do one step, automatic restart,
editing the finished paper, direct links, a project from an earlier version, starting over, deleting.
    python tests/ui_states.py [folder for screenshots]"""
import json, os, pathlib, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright
from helpers import ROOT, pages_pdf

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8792"
work = pathlib.Path(tempfile.mkdtemp(prefix="scriptavera_ui2_"))
PDF = work / "notes.pdf"; PDF.write_bytes(pages_pdf())
env = dict(os.environ, SCRIPTAVERA_MOCK="1", SCRIPTAVERA_MOCK_DELAY="0.02", SCRIPTAVERA_MOCK_FAIL="busy:Write section 3", SCRIPTAVERA_MOCK_RESET="7",
           SCRIPTAVERA_PORT=PORT, SCRIPTAVERA_NO_BROWSER="1", SCRIPTAVERA_WORK=str(work))
SERVER = f"""
import sys, os, pathlib; sys.path.insert(0, {str(ROOT)!r})
from scriptavera import config
w = pathlib.Path(os.environ['SCRIPTAVERA_WORK'])
config.PROJECTS_DIR = w / 'projects'; config.SETTINGS_PATH = w / 'settings.json'; config.ENV_PATH = w / '.env'
import uvicorn; from scriptavera.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port={PORT}, log_level='warning')
"""
srv = subprocess.Popen([sys.executable, "-c", SERVER], env=env)
errors = []
U = f"http://127.0.0.1:{PORT}/"
try:
    time.sleep(2.0)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(U); pg.wait_for_selector(".dom")
        pg.click('.dom[data-id="math.NT"]'); time.sleep(0.2)
        pg.set_input_files("#fileInput", str(PDF)); pg.wait_for_selector(".file")
        pg.fill("#fproblem", "Bounds for sums of reciprocal powers by integral comparison.")
        pg.fill("#fkeywords", "partial sums, integral comparison")
        pg.click('[data-act="create"]')
        pg.wait_for_selector('[data-act="topicok"]', timeout=120000); time.sleep(0.3)
        pg.click('[data-act="topicok"]')
        pg.wait_for_selector(".term", timeout=120000)
        assert "Choose a target journal" in pg.inner_text("#topbar")
        for i in range(pg.locator(".term").count()):
            pg.locator(f".term >> nth={i}").locator('.pick[data-v="yes"]').click()
        pg.click('[data-act="confirm"]')
        pg.wait_for_selector(".review", timeout=120000)
        for _ in range(20):                                           # every item settled: Approve opens
            left = pg.evaluate("(() => { const x = openItems()[0]; return x ? x.type + ':' + x.f.id : '' })()")
            if not left:
                break
            t, i = left.split(":")
            pg.locator(f'#fig-{i} .choice[data-v="orig"]' if t == "fig" else f'#flag-{i} [data-act="dropflag"]').click(); time.sleep(0.4)
        pg.click('[data-act="approve"]')
        pg.wait_for_selector(".kinds", timeout=120000); time.sleep(0.3)
        pg.click('[data-act="scopesubmit"]')                            # the suggested plan

        # the strongest writer is resting when the third section is due: the step waits and says so
        pg.wait_for_selector('[data-act="relax"]', timeout=120000); time.sleep(0.3)
        pg.screenshot(path=str(OUT / "40_waiting_for_strongest.png"))
        banner = pg.inner_text(".banner")
        print("banner:", banner[:300].replace("\n", " "))
        assert "waiting for the strongest model" in banner and "sim-small" in banner and "continue by itself" in banner, banner
        assert pg.locator('#topbar [data-act="resume"]').count() == 1 and pg.locator('#topbar [data-act="startover"]').count() == 1
        assert "Paused until" in pg.inner_text(".step.paused")
        pg.click('[data-act="relax"]')                                  # the author allows the next best model for this one step
        pg.wait_for_selector("#deccount", timeout=120000)               # ... and the next step restarts by itself when the strong model is back
        pg.click('.step[data-view="structure"]'); time.sleep(0.4)
        text = pg.inner_text("#main")
        assert "done by a weaker model, with your permission" in text and "sim-small" in text, text[:600]
        pg.screenshot(path=str(OUT / "41_stand_in.png"))
        calls = json.loads(pg.evaluate("JSON.stringify(S.b.calls.filter(c => c.stage === 'simplify').map(c => c.model))"))
        assert calls and set(calls) == {"sim-text"}, calls                # the following step was done by the strongest model again
        print("waited, stood in for one step with permission, then restarted by itself")

        pg.click('.step[data-view="decide"]'); pg.wait_for_selector("#deccount")
        pg.locator('[data-act="decsim"][data-v="reject"]').click()
        pg.locator('[data-act="decissue"][data-v="keep"]').click()
        pg.click('[data-act="decide"]')
        pg.wait_for_selector(".tlist", timeout=120000); time.sleep(0.4)
        pg.click('[data-act="ffinish"]')                                # the first suggested title
        pg.wait_for_selector("#paperHost", timeout=120000); time.sleep(0.5)
        assert "p-fig" in pg.inner_html("#paperHost") and "figures/f1.jpg" in pg.evaluate("fetch(`/api/projects/${S.pid}/download/main.tex`).then(r => r.text())")

        # editing the finished paper
        pg.locator("#paperHost .p-title").click(); time.sleep(0.3)
        assert pg.locator(".editor .btn[data-act='usetitle']").count() == 2, "the other proposed titles are offered"
        pg.fill("#edtext", "A new title with $x^2$ \\& more"); pg.click('[data-act="saveedit"]')
        pg.wait_for_function("/Saved/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.4)
        assert "A new title" in pg.inner_text("#paperHost") and "A new title" in pg.inner_text("#topbar"), "title edit failed"
        pg.locator("#paperHost .thm").first.click(); time.sleep(0.3)
        pg.fill("#edinstr", "shorten"); pg.click('[data-act="revise"]')
        pg.wait_for_function("/Rewritten/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.4)
        st = json.loads(pg.evaluate("JSON.stringify({c: S.b.paper.compile.ok, e: S.b.paper.compile.engine, a: [S.b.paper.audit.preserved, S.b.paper.audit.total], lint: S.b.paper.lint})"))
        print("after edits:", st)
        assert st["c"] == bool(st["e"]) and not st["lint"]
        pg.screenshot(path=str(OUT / "42_after_edit.png"))

        # direct links, links from an earlier version, the list of papers
        pid = pg.evaluate("location.hash").split("/")[1]
        pg.goto(f"{U}#p/{pid}/verify"); pg.reload(); pg.wait_for_selector(".steps"); time.sleep(0.6)
        assert "Proofs checked" in pg.inner_text("#main"), pg.inner_text("#main")[:200]
        pg.goto(f"{U}#p/{pid}/frontmatter"); pg.reload(); pg.wait_for_selector("#paperHost", timeout=20000)       # a step that no longer exists
        pg.click(".brand"); pg.wait_for_selector(".proj"); pg.wait_for_selector(".pcard"); time.sleep(0.3)
        assert "completed" in pg.inner_text(".proj") and "A new title" in pg.inner_text(".proj")
        assert "Completed" in pg.inner_text(".pgroup") and "All steps done" in pg.inner_text(".pcard")    # the home page: your papers
        pg.screenshot(path=str(OUT / "43_home_with_paper.png"))
        pg.click('[data-act="newpaper"]'); pg.wait_for_selector(".domain-search, .dom"); time.sleep(0.2)
        assert pg.locator('[data-act="papers"]').count() >= 1, "from the new-paper form, back to the papers"
        pg.click('.wrap [data-act="papers"]'); pg.wait_for_selector(".pcard"); time.sleep(0.2)
        pg.click('.pcard [data-act="rename"]'); pg.fill("#renamebox", "Partial sums notes"); pg.click('[data-act="renamesave"]'); time.sleep(0.6)
        assert "Partial sums notes" in pg.inner_text(".pcard")
        pg.click(".proj"); pg.wait_for_selector("#paperHost")

        # a project that was started with an earlier version says what was kept
        (work / "projects" / pid / "out_before_update").mkdir()
        pg.once("dialog", lambda d: d.accept()); pg.click('[data-act="redo"][data-stage="understand"]')
        pg.wait_for_selector(".kinds", timeout=120000); time.sleep(0.3)
        pg.click('[data-act="scopesubmit"]')
        pg.wait_for_selector("#deccount", timeout=120000)
        pg.click('.step[data-view="understand"]'); time.sleep(0.4)
        assert "started with an earlier version of the app" in pg.inner_text("#main")
        pg.screenshot(path=str(OUT / "44_earlier_version.png"))

        # start over, then delete
        pg.once("dialog", lambda d: d.accept()); pg.click('#topbar [data-act="startover"]')
        pg.wait_for_selector(".gatebar", timeout=120000)
        assert pg.evaluate("S.b.paper") is None and pg.evaluate("S.b.transcript") is None and pg.evaluate("S.b.dossier") is None, "start over must clear results"
        print("start over ran from the first step to the confirmation step")
        pg.once("dialog", lambda d: d.accept()); pg.click('[data-act="delete"]'); time.sleep(1.0)
        assert pg.locator(".head h1").inner_text().startswith("Which area"), "not back home after delete"
        assert not (work / "projects" / pid).exists()
        b.close()
finally:
    srv.terminate()
print("console errors:", json.dumps(errors[:10], indent=1))
assert not errors, "the browser reported errors"
print("ui states ok")
