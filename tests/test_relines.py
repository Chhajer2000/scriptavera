# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Step 9, the author's own lines and notes: a model's answer gives a page read as running text its line breaks, gaps,
indents and headings, and puts each note with the words it belongs to; nothing else of it is taken. Only space changes
and where a note stands, never inside a formula, a drawing or a note; a note moves whole, with its own words; a line
break the page has is never taken away. Asked once per page, in the background; put into the page once (an edit of
the author's afterwards stays as it is); a page read again is put into shape from the same answer; a failed request is
asked again soon; a paper can turn it off. The pages here are made up, of every shape a page of notes can have."""
import os
import re
import time
from helpers import isolate, new_project
isolate("scriptavera_relines_")
from scriptavera import pipeline, relines
relines.WAITS = (0.2,)                     # asked again soon after a failure: at once here

flat = lambda s: re.sub(r"\s+", "", s)
pieces = lambda s: sorted(re.findall(r"\S+", s))

# ---- 1. the answer's lines put into the page, word for word
page = ("Background 1990 (Smith) $\\exists$ a finite group of order $n$ 2001 (Jones) $G = G_2$ "
        "is simple [MARGIN: see the survey [Lee '05]] 2010 (Smith) $G_d$")
ans = ("<h>Background\n\n1990 (Smith) $\\exists$ a finite group of order $n$\n2001 (Jones) $G = G_2$\n"
       "    is simple [MARGIN: see the survey [Lee '05]]\n2010 (Smith) $G_d$")
new, fmt, share = relines.transfer(page, ans)
assert share == 1.0 and flat(new) == flat(page), (share, new)
assert new.split("\n") == ["Background", "", "1990 (Smith) $\\exists$ a finite group of order $n$", "2001 (Jones) $G = G_2$",
                           "    is simple [MARGIN: see the survey [Lee '05]]", "2010 (Smith) $G_d$"], new
assert fmt == {"heads": ["Background"], "leads": {}}, fmt
new, fmt, share = relines.transfer("Lemma [Smith, 1999] $H$ is normal in $G$ Remark $H$ is abelian",
                                   "<h>Lemma</h> [Smith, 1999]\n$H$ is normal in $G$\n<h>Remark</h>  $H$ is abelian")
assert new == "Lemma [Smith, 1999]\n$H$ is normal in $G$\nRemark $H$ is abelian", new
assert fmt == {"heads": [], "leads": {"Lemma[Smith,1999]": "Lemma", "RemarkHisabelian": "Remark"}}, fmt
print("1. the page gets the answer's lines, its gap, its indent and its headings (a whole line, or its first words); its words stay")

# ---- 2. never inside a formula, a drawing, a note or braces; a misread word in the answer does not matter
page = "We get $a + b = c$ and so [FIGURE: two \\emph{circles} side by side] holds, see \\underline{the lemma} here."
ans = "We get $a +\nb = c$ and so [FIGURE: two \\emph{circles}\nside by side] holds,\nsee \\underline{the\nlemma} hare."
new, fmt, share = relines.transfer(page, ans)
assert new == "We get $a + b = c$ and so [FIGURE: two \\emph{circles} side by side] holds,\nsee \\underline{the lemma} here.", new
assert share > 0.8 and fmt == {"heads": [], "leads": {}}, share
print("2. no line begins inside a formula, a drawing or braces; a word the answer misread is passed over")

# ---- 3. a line break the page has is kept, even where the answer runs two lines together; a poor answer is not used
page = "First line\nsecond line and more"
assert relines.transfer(page, "First line second line\nand more")[0] == "First line\nsecond line\nand more"
assert relines.transfer(page, "Something else entirely, about another page")[2] < relines.GATE
assert relines.transfer("", "x")[2] == 0.0
print("3. no line break is taken away; an answer about something else is not used")

# ---- 4. in the background, once per page; put into the page once; the author's own edit afterwards stays
p = new_project(pages=2)
joined = ("Theorem. Every bounded sequence has a convergent subsequence. Proof. Take a nested sequence of "
          "intervals halving at each step. Each one holds infinitely many terms.")
p.save("transcript", {"pages": {"1": {"text": joined, "uncertain": [], "edited": False},
                                "2": {"text": "Short page. With $x$ only.", "uncertain": []}}})


def settle(want, seconds=20):
    t0 = time.time()
    while True:
        got = relines.settle(p, want)
        if all(v["state"] != "busy" for v in got.values()) or time.time() - t0 > seconds:
            return got
        time.sleep(0.1)


first = relines.settle(p, {1: joined})
assert first[1]["state"] == "busy", first
got = settle({1: joined})
assert got[1]["state"] == "done", got
text = got[1]["text"]
assert text.split("\n") == ["Theorem.", "Every bounded sequence has a convergent subsequence.", "Proof.",
                            "Take a nested sequence of intervals halving at each step.", "Each one holds infinitely many terms."], text
assert got[1]["format"] == {"heads": ["Theorem.", "Proof."], "leads": {}}, got[1]["format"]
rec = p.load("transcript")["pages"]["1"]
assert rec["text"] == text and rec["format"]["heads"] == ["Theorem.", "Proof."] and rec.get("lined") and not rec.get("edited")
assert relines.settle(p, {1: joined})[1]["state"] == "stale", "the page has changed on the server: the screen is told"
again = relines.settle(p, {1: text})
assert again[1] == {"state": "done", "text": text, "format": rec["format"]}, again
pipeline.save_transcript_page(p, 1, joined + " Done.")            # the author runs the lines together again
assert relines.settle(p, {1: joined + " Done."})[1]["text"] == joined + " Done.", "an edit of the author's afterwards stays"
print("4. asked once in the background; the page is put into its lines once; the author's own edit afterwards stays")

# ---- 5. a page read again is put into shape from the same answer, without asking again
asked = []
real = relines.ask
relines.ask = lambda *a, **k: asked.append(1)
tr = p.load("transcript")
tr["pages"]["1"] = {"text": joined.replace("terms.", "terms of it."), "uncertain": []}
p.save("transcript", tr)
got = relines.settle(p, {1: tr["pages"]["1"]["text"]})
assert got[1]["state"] == "done" and got[1]["text"].count("\n") == 4 and not asked, (got, asked)
relines.ask = real
print("5. a page read again gets its lines from the answer already in, without a new request")

# ---- 6. a failed request is asked again soon; turned off for the paper, nothing is asked
os.environ["SCRIPTAVERA_MOCK_FAIL"] = "Find the lines"
got = settle({2: "Short page. With $x$ only."})
assert got[2]["state"] == "done" and got[2]["text"] == "Short page.\nWith $x$ only.", got
os.environ.pop("SCRIPTAVERA_MOCK_FAIL", None)
q = new_project(pages=1)
q.save("transcript", {"pages": {"1": {"text": "One. Two.", "uncertain": []}}})
q.update_state(lambda st: st.update(layout_ai=False))
assert relines.settle(q, {1: "One. Two."})[1]["state"] == "none" and relines.answer(q, 1) is None
print("6. a failed request is asked again soon; a paper that turned it off is not asked about")

# ---- 7. an answer to a doubt saved from a copy of the page made before it was put into its lines keeps the lines
lined = "Short page.\nWith $x$ only."
assert p.load("transcript")["pages"]["2"]["text"] == lined
assert pipeline.save_transcript_page(p, 2, "Short page. With $y$ only.", keep_lines=True) == "Short page.\nWith $y$ only."
assert pipeline.save_transcript_page(p, 2, "Short page. With $y$ only.") == "Short page. With $y$ only.", "the author's own edit of the lines is kept"
print("7. a doubt answered from an older copy of the page keeps the page's lines; the author's own edit of them stays")

# ---- 8. each note goes with the words it belongs to, wherever the reading put it; whole, with its own words
cases = [
    # a note gathered at the end of the page goes after the last of the lines it is about
    ("Step one gives $a = b$.\nStep two gives $b = c$.\nHence $a = c$.\n[MARGIN: on the two lines above: both by the lemma]",
     "Step one gives $a = b$.\nStep two gives $b = c$. [MARGIN: on the two lines above: both by the lemma]\nHence $a = c$.",
     "Step one gives $a = b$.\nStep two gives $b = c$. [MARGIN: on the two lines above: both by the lemma]\nHence $a = c$."),
    # a note read before the words its arrow points to goes after them
    ("[MARGIN: compact] Take $x$ in $U$. Then $f(x) = 0$.",
     "Take $x$ in $U$. [MARGIN: compact]\nThen $f(x) = 0$.",
     "Take $x$ in $U$. [MARGIN: compact]\nThen $f(x) = 0$."),
    # a note written below a line, kept on a line of its own as the answer has it
    ("[MARGIN: see below] First line here.\nSecond line here.",
     "First line here.\n[MARGIN: see below]\nSecond line here.",
     "First line here.\n[MARGIN: see below]\nSecond line here."),
    # the answer misread a word of the note: the note moves with the page's own words
    ("A line. B line.\n[MARGIN: by Lemma 2]", "A line. [MARGIN: by Lema 2]\nB line.", "A line. [MARGIN: by Lemma 2]\nB line."),
    # a note the answer puts inside a formula stays where it was
    ("We have $a + b$ here. [MARGIN: note]", "We have $a + [MARGIN: note] b$ here.", "We have $a + b$ here. [MARGIN: note]"),
    # a note the answer leaves out stays where it was
    ("One two. [MARGIN: x y] Three four.", "One two.\nThree four.", "One two. [MARGIN: x y]\nThree four."),
    # two notes after one word, in the order of the answer
    ("Alpha beta. [MARGIN: first] Gamma. [MARGIN: second]", "Alpha beta. [MARGIN: second] [MARGIN: first]\nGamma.",
     "Alpha beta. [MARGIN: second] [MARGIN: first]\nGamma."),
]
for page, ans, want in cases:
    new, fmt, share = relines.transfer(page, ans)
    assert new == want, (page, ans, new)
    assert pieces(new) == pieces(page), "nothing the page says is changed, added or lost"
print("8. a note goes after the words the answer puts it with, whole; never into a formula; one the answer leaves out stays")
print("relines ok")
