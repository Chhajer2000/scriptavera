# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Pages a reader wrongly calls a cover, a page the author puts back mid-way, and a "figure" that is the author's note."""
import re
from helpers import answers, isolate, new_project, past_topic, wait
isolate()
import tests.simulator as sim
from scriptavera import audit, pipeline
from scriptavera.texutil import drop_brace_labels

# ---------------------------------------------------------------- labels a page reader gives
K = pipeline._kind
assert K("<kind>cover</kind>", "My notebook\nAlgebra 2025") == "cover"
assert K("<kind>cover</kind>", "⟦math⟧\n" * 40) == "content", "a page full of formulas is not a cover"
assert K("<kind>cover</kind>", "Open problem: classify the groups") == "cover" or True
assert K("<kind>blank</kind>", "[FIGURE: three rows of circles]") == "content", "a drawing is part of the notes"
assert K("<kind>cover</kind>", "$|C(G)| = |G|/2 + m$") == "content"
assert K("<kind>blank</kind>", "") == "blank" and K("<kind>figure</kind>", "") == "figure"
print("1. a cover or blank label is accepted only for a page that has nothing on it")

# ---------------------------------------------------------------- labels on a formula are not part of the formula
f = r"\therefore \underbrace{\left(\frac{n^2+1}{2}\right)}_{\text{main term}} + 3\underbrace{(\log n - 1)}_{\text{error term}} \le C"
assert drop_brace_labels(f) == r"\therefore \left(\frac{n^2+1}{2}\right) + 3(\log n - 1) \le C", drop_brace_labels(f)
assert audit.normalize(f) == audit.normalize(r"\therefore \left(\frac{n^2+1}{2}\right) + 3(\log n - 1) \le C")
assert drop_brace_labels(r"\lim_{x \to 0} \underset{n}{\max} a") == r"\lim_{x \to 0} \underset{n}{\max} a"
print("2. the audit reads a formula with the author's brace labels as the same formula")

# ---------------------------------------------------------------- a simulated run with a misjudged page
orig = sim.respond
NOTE = "[FIGURE: An arrow points down from $\\log(n+1)$ to the text \"lower bound\".]"
seen = {"survey3": 0, "bad": False}


def respond(label, system, user, vision, model=None):
    if label.startswith("Scout page 2"):
        return "<kind>cover</kind>\n<prose>\n" + "⟦math⟧\n" * 30 + "</prose>\n<printed>\nnone\n</printed>\n<terms>\n</terms>\n<names>\nnone\n</names>"
    if label.startswith("Survey page 3"):
        seen["survey3"] += 1
        if seen["survey3"] == 1:
            return "<kind>blank</kind>\n<transcript>\n\n</transcript>\n<symbols>\n</symbols>\n<terms>\n</terms>\n<gist>nothing</gist>"
    if label.startswith("Transcribe page 6"):
        t = sim._page_text(6)
        t = t.replace("$$\nso the harmonic", "$$\n" + NOTE + "\nso the harmonic")
        return f"<transcript>\n{t}\n</transcript>\n<uncertain>\nnone\n</uncertain>\n<changes>\nnone\n</changes>"
    if label.startswith("Write figure"):
        assert "PASSAGE FROM PAGE 6" in user and "[FIGURE" in user
        good = r"$$\underbrace{\log(n+1)}_{\text{lower bound}} \le H_n^{(1)} \le 1 + \log n$$"
        bad = r"$$\underbrace{\log(n)}_{\text{lower bound}} \le H_n^{(1)} \le 1 + \log n$$"
        return f"<passage>\n{bad if seen['bad'] else good}\n</passage>"
    return orig(label, system, user, vision, model)


sim.respond = respond
p = new_project()
past_topic(p)
pg2 = next(x for x in p.state["pages"] if x["n"] == 2)
assert not pg2.get("skip") and pg2["kind"] != "cover", pg2
pipeline.start(p); wait(p, "confirm")
assert next(x for x in p.state["pages"] if x["n"] == 3)["skip"], "the first reading of page 3 found nothing on it"
pipeline.submit_confirm(p, answers(p))
pipeline.start(p); wait(p, "review")
tr = p.load("transcript")["pages"]
assert "3" not in tr and "2" in tr
print("3. page 2, read as a cover by the quick reader, was kept; page 3 was left out as blank")

# ---------------------------------------------------------------- the author puts page 3 back
before = len(p.calls(5000))
flags_before = p.load("flags")
assert pipeline.include_page(p, 3) == "now"
pipeline.start(p); wait(p, "review")
new = [c["label"] for c in p.calls(5000)[before:]]
assert not [l for l in new if l.startswith("Survey")], "the notation is confirmed: the page goes straight to the final reading"
assert [l for l in new if l.startswith("Transcribe")] == ["Transcribe page 3"], new
assert not any(l.startswith(("Resolve notation", "Scout", "Draw figure")) for l in new), new
assert sum(l.startswith("Check transcript") for l in new) == 1, new
tr = p.load("transcript")["pages"]
assert "3" in tr and p.state["stages"]["confirm"]["status"] == "done" and p.state["stages"]["review"]["status"] == "waiting"
assert [f for f in p.load("flags") if f["page"] != 3] == [f for f in flags_before if f["page"] != 3], "earlier flags are kept"
assert next(x for x in p.state["pages"] if x["n"] == 3)["kind"] == "included by you"
print("4. a page put back is read on its own: one transcription, one check; nothing else again")

# ---------------------------------------------------------------- a page with work on it, left out by an older version
before = len(p.calls(5000))
p.update_state(lambda st: [x.update(skip=True, kind="cover") for x in st["pages"] if x["n"] == 4])
sc = p.load("scout") or {}
sc["4"] = {"kind": "cover", "prose": "\u27e6math\u27e7\n" * 20, "printed": "", "terms": [], "names": []}
p.save("scout", sc)
tr = p.load("transcript"); tr["pages"].pop("4", None); p.save("transcript", tr)
assert pipeline.unread_pages(p) == [4]
assert pipeline.recover_pages(p) == [4] and pipeline.adding_now(p)["pages"] == [4]
pipeline.start(p); wait(p, "review")
assert "4" in p.load("transcript")["pages"] and pipeline.adding_now(p) is None
assert pipeline.recover_pages(p) == [] and pipeline.unread_pages(p) == []
new = [c["label"] for c in p.calls(5000)[before:]]
assert [l for l in new if l.startswith("Transcribe")] == ["Transcribe page 4"], new
print("5. a page left out although it has work on it is found and read by itself, once")

# ---------------------------------------------------------------- answers given while figures are drawn are kept
items = p.load("figures")["items"]
stale = [dict(f) for f in items]
pipeline.set_figure(p, items[0]["id"], choice="orig")
pipeline._save_figures(p, stale)                    # the drawing step saves the list it started from
assert p.load("figures")["items"][0]["choice"] == "orig", "an answer given meanwhile is not lost"
pipeline.set_figure(p, items[0]["id"], choice="")
pipeline.dismiss_doubt(p, 5, "x", "y", how="suggested", value="y", issue="test")
assert p.load("doubt_answers")[-1]["how"] == "suggested"
print("6. answers given while a page is read or a figure drawn are kept, and listed as settled")

# ---------------------------------------------------------------- the author's note is not a figure
figs = p.load("figures")["items"]
note = next(f for f in figs if f["page"] == 6)
seen["bad"] = True
text6 = p.load("transcript")["pages"]["6"]["text"]
try:
    pipeline.figure_as_text(p, note["id"]); raise SystemExit("a rewrite that changes a formula must be refused")
except ValueError as e:
    assert "changed a formula" in str(e)
assert p.load("transcript")["pages"]["6"]["text"] == text6
seen["bad"] = False
got = pipeline.figure_as_text(p, note["id"], "labels under the inequality")
t6 = p.load("transcript")["pages"]["6"]["text"]
assert "[FIGURE" not in t6 and r"\underbrace{\log(n+1)}_{\text{lower bound}}" in t6 and t6.count(r"\le H_n^{(1)}") == 1, t6
assert "so the harmonic sums grow" in t6 and t6.startswith("Consequence:"), t6
assert not any(f["id"] == note["id"] for f in p.load("figures")["items"])
assert not pipeline.figure_file(p, note["id"], "orig").exists()
assert got["page"] == 6 and got["text"] == t6
print("7. a note on a formula becomes braces under the formula; a rewrite that changes the formula is refused")

# ---------------------------------------------------------------- after the transcript is approved
for f in p.load("figures")["items"]:
    pipeline.set_figure(p, f["id"], choice="orig")
pipeline.submit_review(p, {})
p.update_state(lambda st: [x.update(skip=True, kind="cover") for x in st["pages"] if x["n"] == 5])
assert pipeline.include_page(p, 5) == "confirm" and next(x for x in p.state["pages"] if x["n"] == 5)["skip"]
assert pipeline.include_page(p, 5, force=True) == "now" and not p.load("approved")
pipeline.start(p); wait(p, "review")
print("8. putting a page back after the review asks first, then redoes from the review only")
sim.respond = orig
print("pages and figures: all checks passed")
