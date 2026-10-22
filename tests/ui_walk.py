# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Drive the real interface in a browser against simulated models, from an empty app to a finished paper,
taking screenshots on the way.   python tests/ui_walk.py [folder for screenshots]"""
import json, os, pathlib, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright
from helpers import ROOT, pages_pdf

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="scriptavera_shots_"))
OUT.mkdir(parents=True, exist_ok=True)
PORT = "8791"
work = pathlib.Path(tempfile.mkdtemp(prefix="scriptavera_ui_"))
PDF = work / "notes.pdf"; PDF.write_bytes(pages_pdf())
env = dict(os.environ, SCRIPTAVERA_MOCK="1", SCRIPTAVERA_MOCK_DELAY=os.environ.get("SCRIPTAVERA_UI_DELAY", "0.12"), SCRIPTAVERA_PORT=PORT, SCRIPTAVERA_NO_BROWSER="1", SCRIPTAVERA_WORK=str(work))
env.pop("SCRIPTAVERA_MOCK_FAIL", None)
SERVER = f"""
import sys, os, pathlib; sys.path.insert(0, {str(ROOT)!r})
from scriptavera import config
w = pathlib.Path(os.environ['SCRIPTAVERA_WORK'])
config.PROJECTS_DIR = w / 'projects'; config.SETTINGS_PATH = w / 'settings.json'; config.ENV_PATH = w / '.env'
config.MEMORY_DIR = w / 'memory'; config.PHONE_DIR = w / 'phone_uploads'; config.IDENTITY_DIR = w / 'identity'
import uvicorn; from scriptavera.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port={PORT}, log_level='warning')
"""
srv = subprocess.Popen([sys.executable, "-c", SERVER], env=env)
errors = []
U = f"http://127.0.0.1:{PORT}/"


def shot(pg, name, full=False):
    """A screenshot; `full` makes the window tall enough to show a long screen whole (the app scrolls inside it)."""
    if full:
        tall = pg.evaluate("Math.max(900, ...[...document.querySelectorAll('#main, .rv-right, #overlay')].map(e => e.scrollHeight)) + 80")
        pg.set_viewport_size({"width": 1440, "height": min(int(tall), 6000)}); time.sleep(0.25)
    pg.screenshot(path=str(OUT / f"{name}.png"))
    if full:
        pg.set_viewport_size({"width": 1440, "height": 900}); time.sleep(0.1)


try:
    time.sleep(2.0)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(U)
        pg.wait_for_selector(".home-empty")                       # no papers yet: the home page offers the two ways in
        shot(pg, "00_home_empty")
        hs = [pg.locator(f'.home-acts [data-act="{a}"]').bounding_box()["height"] for a in ("newpaper", "importpaper")]
        assert hs[0] == hs[1], f"the two buttons on the home page differ in height: {hs}"
        pg.click('.home-acts [data-act="newpaper"]')
        pg.wait_for_selector(".dom")
        assert pg.evaluate("location.hash") == "#new" and pg.locator('.crumbs [data-act="home"]').count() == 1
        shot(pg, "01_home")
        pg.click('.dom[data-id="math.CA"]')
        pg.fill('[data-bind="subarea"]', "explicit bounds for sums")
        pg.set_input_files("#fileInput", str(PDF))
        pg.wait_for_selector(".file")
        pg.click('[data-act="create"]'); time.sleep(0.3)                   # refused: the problem and the keywords are required
        assert "Describe the problem" in pg.inner_text("#toast")
        pg.fill("#fproblem", "Explicit bounds for the partial sums of the series of reciprocal powers.")
        pg.fill("#fkeywords", "partial sums, reciprocal powers, integral comparison")
        pg.fill('[data-bind="keypapers"]', "10.9999/example.2015.1")
        pg.wait_for_selector(".file")
        pg.fill("#formjq", "analysis"); pg.press("#formjq", "Enter")
        pg.wait_for_selector(".jr-item"); shot(pg, "02_home_journal", full=True)
        pg.locator(".jr-item").first.click()
        assert "Simulated Journal of Analysis" in pg.inner_text(".sheet")
        pg.locator("details.more summary").first.click()
        pg.click('[data-act="create"]'); time.sleep(0.3)                   # refused: the record names its authors
        assert "Name at least one author" in pg.inner_text("#toast")
        pg.fill('[data-bind="authors.0.name"]', "A. N. Author")
        pg.fill('[data-bind="authors.0.affiliation"]', "Department of Mathematics, Example University")
        pg.fill('[data-bind="authors.0.orcid"]', "0000-0002-1825-0097")
        assert "I, A. N. Author, am the author of these notes." in pg.inner_text("#fdeclareText"), "the declaration is shown word for word"
        pg.click('[data-act="create"]'); time.sleep(0.3)                   # refused: the declaration is not ticked
        assert "Tick the declaration" in pg.inner_text("#toast")
        pg.check("#fdeclare")
        shot(pg, "03_home_filled", full=True)
        pg.click('[data-act="create"]')
        pg.wait_for_selector(".steps", timeout=30000)
        assert pg.locator(".phase").count() == 4 and pg.locator(".step").count() == 20
        time.sleep(1.2); shot(pg, "04_running")

        # ---- the topic and the papers found, confirmed before anything else is read
        pg.wait_for_selector('[data-act="topicok"]', timeout=120000); time.sleep(0.5)
        shot(pg, "04b_topic"); shot(pg, "04b_topic_full", full=True)
        assert "Is this what your notes are about?" in pg.inner_text("#main") and pg.locator(".lit .paper, .lit > *").count() >= 1
        pg.click('[data-act="topicrefine"]'); time.sleep(0.3)
        assert "more precisely" in pg.inner_text("#toast")
        pg.fill('[data-tgf="comment"]', "Explicit inequalities for every exponent, via the integral test.")
        pg.click('[data-act="topicrefine"]')
        pg.wait_for_function("document.querySelector('#main') && /Searched again with your comment/.test(document.querySelector('#main').innerText)", timeout=120000)
        time.sleep(0.4)
        pg.click('[data-act="topicok"]')

        # ---- first stop: notation
        pg.wait_for_selector(".gatebar", timeout=120000); time.sleep(0.5)
        shot(pg, "05_confirm"); shot(pg, "05_confirm_full", full=True)
        pg.click('[data-act="confirm"]'); time.sleep(0.3)             # refused: nothing answered yet
        assert "still need an answer" in pg.inner_text("#toast")
        pg.locator('.term [data-act="where"]').first.click(); pg.wait_for_selector(".loc-box", timeout=20000); time.sleep(0.4)
        shot(pg, "06_where"); pg.keyboard.press("Escape")
        terms = pg.locator(".term").count(); assert terms == 3, terms
        pg.locator(".term >> nth=0").locator('.pick[data-v="yes"]').click()
        pg.locator(".term >> nth=1").locator('.pick[data-v="own"]').click()
        assert "Write the meaning below" in pg.locator(".term >> nth=1").inner_text()
        pg.locator(".term >> nth=1").locator("textarea").fill("the Riemann zeta function $\\zeta(s)$")
        pg.locator('.term:has(.term-sym.word) [data-act="where"]').first.click(); pg.wait_for_selector(".loc-status.warn", timeout=20000)
        assert "could not be found" in pg.inner_text("#locstatus"); pg.keyboard.press("Escape")
        pg.locator(".term >> nth=2").locator('.pick[data-v="skip"]').click()
        assert pg.inner_text("#gatecount").startswith("3 of 3"), pg.inner_text("#gatecount")
        pg.reload(); pg.wait_for_selector(".gatebar"); time.sleep(0.5)       # typed answers survive a reload
        assert pg.inner_text("#gatecount").startswith("3 of 3"), "answers lost on reload"
        pg.click('[data-act="confirm"]')

        # ---- second stop: transcript and figures, as one list of items to check
        pg.wait_for_selector(".review", timeout=180000); time.sleep(0.6)
        assert pg.locator("#fig-f1").count() == 1 and "is the redrawn version what you drew" in pg.inner_text("#fig-f1") and pg.locator('#fig-f1 [data-act="figtext"]').count() == 1
        shot(pg, "07_review_figure")
        pg.click('[data-act="rvtab"][data-v="approve"]'); time.sleep(0.3)
        assert "still to check" in pg.inner_text(".rv-body") and pg.locator('[data-act="approve"]').count() == 0, "no approving with items open"
        pg.click('[data-act="rvtab"][data-v="todo"]'); time.sleep(0.3)
        pg.fill("#figwhy-f1", "The rectangles should start at 1."); pg.click('#fig-f1 [data-act="figredraw"]')
        pg.wait_for_function("document.querySelector('#toast') && /Drawn again|could not be redrawn/.test(document.querySelector('#toast').textContent)", timeout=60000)
        drawn = pg.locator('#fig-f1 .choice[data-v="tikz"]').is_enabled()
        pg.locator(f'#fig-f1 .choice[data-v="{"tikz" if drawn else "orig"}"]').click(); time.sleep(0.6)
        assert pg.locator("#fig-f1").count() == 0 and "Moved to Settled" in pg.inner_text("#toast"), "an answered figure leaves To check"
        pg.click('[data-act="rvtab"][data-v="settled"]'); time.sleep(0.3)
        assert "goes into the paper" in pg.inner_text(".rv-body") and pg.locator(".srow .dq-mark.ok").count() >= 1
        pg.locator(".rv-body .capwrap summary").first.click(); time.sleep(0.2)
        pg.fill('[data-figcap="f1"]', "Rectangles of width $1$ under the graph of a decreasing function."); pg.locator('[data-figcap="f1"]').blur(); time.sleep(0.4)
        assert pg.locator("#cappv-f1 .katex").count() >= 1 and "$" not in pg.inner_text("#cappv-f1"), "the caption is shown typeset"
        shot(pg, "07b_review_figure_answered")
        pg.click('[data-act="rvtab"][data-v="todo"]'); time.sleep(0.3)
        p5 = '.qcard.flag[data-page="5"]'
        assert pg.locator(p5).count() == 2, "a doubtful reading and a possible misreading on page 5"
        flags_text = " ".join(pg.locator(p5).all_inner_texts())
        assert "\\log" not in flags_text and "$" not in flags_text and "suggested reading:" not in flags_text, flags_text   # no LaTeX source
        assert pg.locator(f"{p5} .katex").count() >= 4 and pg.locator(f"{p5} code").count() == 0
        shot(pg, "08_review_page5")
        pg.locator(f'.qcard.flag.red[data-page="5"] [data-act="dropflag"]').click(); time.sleep(0.5)       # 'correct as read' is remembered
        assert pg.evaluate("S.review.n") != 5 or pg.locator(p5).count() == 1
        pg.reload(); pg.wait_for_selector(".review"); time.sleep(0.4)
        assert pg.locator(p5).count() == 1 and pg.locator('.qcard.flag.red[data-page="5"]').count() == 0
        assert pg.evaluate("settledItems().filter((x) => x.type === 'flag').length") >= 1, "the answer is listed under Settled after a reload"
        # a reading that occurs in two places: both ticked, one unticked, nothing changed yet
        p2 = '.qcard.flag[data-page="2"]'
        assert pg.locator(f"{p2} .occ-row").count() == 2 and "in all 2 places" in pg.inner_text(p2)
        pg.locator(f"{p2} .occ-row input >> nth=1").uncheck(); time.sleep(0.3)
        assert not pg.locator(f"{p2} .occ-row input >> nth=1").is_checked() and pg.locator(f"{p2} .occ-row input >> nth=0").is_checked()
        assert "in the 1 ticked place" in pg.inner_text(p2)
        shot(pg, "08a_two_places")
        pg.locator(f'{p2} [data-act="dropflag"]').click(); time.sleep(0.4)
        assert pg.locator(p2).count() == 0
        # the author's own reading, typed right in the card, and Enter to use it
        p4 = '.qcard.flag[data-page="4"]'
        assert pg.locator(p4).count() == 1
        pg.locator(f'{p4} [data-act="ownread"]').click(); time.sleep(0.2)
        pg.locator(f"{p4} [data-own]").fill("s>1"); time.sleep(0.2)
        assert pg.locator(".ownprev .katex").count() == 1, "what was typed is shown typeset"
        shot(pg, "08b_own_reading")
        pg.locator(f"{p4} [data-own]").press("Enter"); time.sleep(0.6)
        assert pg.locator(p4).count() == 0 and "s>1" in pg.evaluate("S.b.transcript.pages[4].text")
        pg.reload(); pg.wait_for_selector(".review"); time.sleep(0.4)
        assert pg.locator(p4).count() == 0 and "s>1" in pg.evaluate("S.b.transcript.pages[4].text"), "the answer was not kept"
        assert pg.locator('.qcard.flag[data-page="6"]').count() == 0, "the doubt about a plural must not be shown"
        for _ in range(20):                                           # whatever is left: kept as read
            left = pg.evaluate("(() => { const x = openItems()[0]; return x ? x.type + ':' + x.f.id : '' })()")
            if not left:
                break
            t, i = left.split(":")
            pg.locator(f'#fig-{i} .choice[data-v="orig"]' if t == "fig" else f'#flag-{i} [data-act="dropflag"]').click(); time.sleep(0.5)
        assert pg.evaluate("S.review.tab") == "approve" and "Everything is checked" in pg.inner_text(".rv-body"), "with nothing left, Approve opens"
        shot(pg, "08d_approve")
        pg.click('[data-act="approve"]')

        # ---- the assessment of the notes; the author's plan for the paper
        pg.wait_for_selector(".kinds", timeout=240000); time.sleep(0.5)
        shot(pg, "08c_plan"); shot(pg, "08c_plan_full", full=True)
        txt = pg.inner_text("#main")
        assert "The proof has a gap" in txt and "Already in the literature" in txt and "suggested" in pg.inner_text(".kinds"), txt[:800]
        assert pg.locator('[data-act="scopepick"][data-v="credit"].on').count() == 1, "a known result is credited by default"
        pg.locator('[data-act="scopekind"][data-v="not_ready"]').click(); time.sleep(0.2)
        assert "Stop here" in pg.inner_text(".gatebar")
        pg.locator('[data-act="scopekind"][data-v="article"]').click(); time.sleep(0.2)
        pg.fill("[data-scopenote]", "Keep the lower bound short.")
        pg.click('[data-act="scopesubmit"]')

        # ---- the first draft and the three readings; third stop: decisions
        pg.wait_for_selector("#deccount", timeout=240000); time.sleep(0.6)
        shot(pg, "09_decide"); shot(pg, "09_decide_full", full=True)
        # one item at a time, the queue on the left; each with its own passage of the draft and page of the notes
        assert pg.locator(".dq-items .dq-item").count() == 5 and pg.inner_text("#deccount").startswith("0 of 2")
        assert pg.locator(".dq-detail .dcard").count() == 1 and "Shorter proof" in pg.inner_text(".dq-title")
        assert "Item 1 of 5" in pg.inner_text(".dq-top")
        assert pg.locator(".dq-ref .dp-img img").count() == 1, pg.inner_text(".dq-ref")[:300]           # the notes first
        pg.locator('.dq-ref [data-act="dptab"][data-v="draft"]').click(); time.sleep(0.3)
        assert pg.locator(".dq-ref .tex-block").count() >= 2
        pg.locator('.dq-ref [data-act="dptab"][data-v="notes"]').click(); time.sleep(0.3)
        assert "res:" not in pg.inner_text("#main")
        pg.locator('.dq-ref [data-act="dpmode"][data-v="text"]').click(); time.sleep(0.3)
        assert pg.locator(".dq-ref .rv-rendered").count() == 1
        shot(pg, "09b_check_against_notes")
        pg.locator('.dq-ref [data-act="dpmode"][data-v="image"]').click(); time.sleep(0.2)
        pg.click('[data-act="decide"]'); time.sleep(0.3)
        assert "still need your decision" in pg.inner_text("#toast")
        pg.locator('[data-act="decsim"][data-v="accept"]').click(); time.sleep(0.3)
        assert "Shorter proof" not in pg.inner_text(".dq-title") and "1" in pg.inner_text('.dq-tab[data-v="done"]'), "the next item is shown and the settled one moved"
        pg.locator('[data-act="decissue"][data-v="instruct"]').click(); time.sleep(0.2)
        assert pg.inner_text("#deccount").startswith("1 of 2")
        pg.locator("[data-decnote]").fill("Replace the last value by $\\log(n+1)$.")
        assert pg.inner_text("#deccount").startswith("2 of 2")
        pg.locator('[data-act="decissue"][data-v="fix"]').click(); time.sleep(0.3)
        assert "Remark" in pg.inner_text(".dq-title"), pg.inner_text(".dq-title")
        rid = pg.evaluate("S.b.revision.items.remarks[0].id")
        pg.locator(f'[data-remark="{rid}"]').fill("Say in the theorem what $\\zeta(s)$ is.")
        pg.locator('[data-act="decread"]').click(); time.sleep(0.3)
        shot(pg, "09c_queue")
        pg.reload(); pg.wait_for_selector("#deccount"); time.sleep(0.5)
        assert pg.inner_text("#deccount").startswith("2 of 2"), "decisions lost on reload"
        pg.click('.dq-tab[data-v="done"]'); time.sleep(0.2)
        assert pg.locator(".dq-items .dq-item.ok").count() == 3, pg.inner_text(".dq-items")
        pg.locator(f'.dq-item[data-id="{rid}"]').click(); time.sleep(0.3)
        assert pg.locator(f'[data-remark="{rid}"]').input_value().startswith("Say in the theorem") and "Settled" in pg.inner_text(".dq-detail")
        pg.click(".dq-draft"); time.sleep(0.4)
        assert pg.locator("#draftHost .paper").count() == 1
        pg.locator("#draftHost a.ref").first.click(); time.sleep(0.4)
        assert pg.locator("#deccount").count() == 1, "a link in the draft must not leave the page"
        shot(pg, "10_decide_draft")
        pg.click('[data-act="decide"]')

        # ---- the last stop: the author chooses the title, approves the introduction and chooses its citations
        pg.wait_for_selector(".tlist", timeout=240000); time.sleep(0.6)
        shot(pg, "10b_finish"); shot(pg, "10b_finish_full", full=True)
        assert pg.locator(".tlist .trow").count() == 4 and pg.locator(".plist .prow").count() >= 2, pg.inner_text("#main")[:600]
        assert "Google Scholar" in pg.inner_text(".plist")
        pg.locator('[data-ftitle="1"]').check(); time.sleep(0.3)
        assert pg.locator(".trow.on").count() == 1
        pg.click('[data-act="ftitles"]'); pg.wait_for_function("/Five more titles/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.3)
        assert pg.locator(".tlist .trow").count() == 6
        first = pg.locator(".plist .prow input").first
        first.uncheck() if first.is_checked() else first.check()
        time.sleep(0.3)
        assert "differs from what the introduction cites" in pg.inner_text("#main")
        pg.locator('[data-act="fcite"]').last.click(); pg.wait_for_function("/introduction was rewritten/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.3)
        assert "differs from what the introduction cites" not in pg.inner_text("#main")
        pg.fill("[data-fown]", "A title of my own for $H_n^{(s)}$"); time.sleep(0.2)
        pg.click('[data-act="ffinish"]')

        # ---- the final version
        pg.wait_for_selector("#paperHost", timeout=240000); time.sleep(1.0)
        assert "A title of my own" in pg.inner_text("#paperHost .p-title"), pg.inner_text("#paperHost")[:300]
        shot(pg, "11_paper"); shot(pg, "11_paper_full", full=True)
        assert pg.locator("#paperHost .p-fig img").count() == 1 and pg.locator("#paperHost .p-refs li").count() >= 1
        assert pg.evaluate("document.querySelector('#paperHost .p-fig img').naturalWidth") > 50, "the figure image did not load"
        assert "For: Simulated Journal of Analysis" in pg.inner_text("#topbar")
        pg.locator('#paperHost a.cite').first.click(); time.sleep(0.5)
        assert pg.locator("#paperHost").count() == 1, "a citation link must not leave the paper"
        pg.locator('#paperHost [data-part="intro"]').click(); time.sleep(0.4); shot(pg, "12_edit")
        pg.click('[data-act="closeedit"]')
        for t in ("revision", "checks", "refs", "journal", "latex", "activity"):
            pg.click(f'[data-tab="{t}"]'); time.sleep(0.7)
            shot(pg, f"13_{t}", full=True)
            assert pg.evaluate("document.querySelectorAll('.katex-error').length") == 0, f"KaTeX error on tab {t}"
        # journal: statements, the journal's guide, own settings
        pg.click('[data-tab="journal"]'); time.sleep(0.4)
        assert "typical for the publisher" in pg.inner_text("#main") and "Statements on funding" in pg.inner_text("#main")
        pg.fill('[data-stm="funding"]', "No funding was received."); pg.fill('[data-stm="competing"]', "The author declares none.")
        pg.fill("#guidesrc", "Guide for authors. " + "Abstract, keywords, references, manuscript, submission, LaTeX and figures are described here. " * 8)
        pg.click('[data-act="jrguide"]'); pg.wait_for_selector(".guide-out", timeout=30000)
        assert pg.input_value('[data-stm="funding"]') == "No funding was received.", "typed statements must survive reading the guide"
        shot(pg, "14_journal_guide", full=True)
        pg.click('[data-act="jruse"]'); pg.wait_for_function("document.querySelector('#main').innerText.includes(\"from the journal's guide\")", timeout=60000)
        pg.click('[data-act="jrstatements"]'); pg.wait_for_function("/Statements saved/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.5)
        txt = pg.inner_text("#main")
        assert "150 to 200 words" in txt and "line numbers" in txt and "Present." in txt, txt[:1500]
        pg.locator("details.more summary").last.click()
        pg.select_option('[data-cu="template"]', "article"); pg.fill('[data-cu="keywords_max"]', "4")
        pg.click('[data-act="jrcustom"]'); pg.wait_for_function("/Your requirements are in force/.test(document.querySelector('#toast').textContent)", timeout=60000); time.sleep(0.5)
        assert "set by you" in pg.inner_text("#main")
        shot(pg, "15_journal_final", full=True)
        pg.click('[data-tab="paper"]'); time.sleep(0.6)
        assert pg.inner_text("#paperHost").count("Keywords.") == 1 and "Competing interests" in pg.inner_text("#paperHost")
        state = json.loads(pg.evaluate("JSON.stringify({c: S.b.paper.compile, k: S.b.paper.keywords.length, a: [S.b.paper.audit.preserved, S.b.paper.audit.total, S.b.paper.audit.agreed.length], open: S.b.paper.checklist.filter(x => !x.ok).map(x => x.label)})"))
        print("paper state:", state)
        assert state["k"] == 4 and state["c"]["template"] == "article"
        # every step's screen after the run
        for sid in ("scout", "search1", "survey", "confirm", "reading", "transcribe", "figures", "review", "understand", "assess", "scope", "intro", "structure", "simplify", "verify", "referee", "decide", "finish"):
            pg.click(f'.step[data-view="{sid}"]'); time.sleep(0.45)
            shot(pg, f"20_{sid}", full=sid in ("understand", "assess", "scope", "structure", "simplify", "verify", "referee", "decide", "figures", "intro", "finish"))
            assert pg.evaluate("document.querySelectorAll('.katex-error').length") == 0, f"KaTeX error on {sid}"
        pg.click('[data-act="settings"]'); time.sleep(0.4); shot(pg, "30_settings", full=True)
        assert "Only the strongest model is used" in pg.inner_text(".dialog") and "used only if you allow it" in pg.inner_text(".dialog")
        pg.keyboard.press("Escape")
        pg.click('.step[data-view="scout"]'); time.sleep(0.2)
        pg.click('[data-act="journal"]'); pg.wait_for_selector("#jrdialog"); time.sleep(0.3); shot(pg, "31_journal_dialog")
        pg.keyboard.press("Escape")
        b.close()
finally:
    srv.terminate()
print("console errors:", json.dumps(errors[:10], indent=1))
print("shots in", OUT)
assert not errors, "the browser reported errors"
print("ui walk ok")
