# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The LaTeX editor: the paper split into files and joined back unchanged, compiled with the LaTeX installed here,
every number exactly as printed, SyncTeX both ways, the history, and commands in plain words, by rule and by the
(simulated) AI, each ending as a proposal that is applied in whole or in part."""
import io, json, re, shutil, socket, sys, threading, time, zipfile
from helpers import isolate, new_project
tmp = isolate("scriptavera_editor_")
import requests, uvicorn
import tests.simulator as sim
from scriptavera import assist as A, config, paper as paperlib, store, workspace as W

TEX = r"""\documentclass[11pt,reqno]{amsart}
\usepackage{amsmath,amssymb,amsthm}
\usepackage[colorlinks=true]{hyperref}

\theoremstyle{plain}
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{corollary}[theorem]{Corollary}
\theoremstyle{definition}
\newtheorem{definition}[theorem]{Definition}
\theoremstyle{remark}
\newtheorem{remark}[theorem]{Remark}

\title{Partial sums of reciprocal powers}

\begin{document}

\begin{abstract}
We bound $H_n^{(s)}$ from both sides.
\end{abstract}

\maketitle

\section{Introduction}

We compare sums with integrals~\cite{Euler1735}. Section~2 fixes the notation, and Section~3 proves the bound of
Theorem~\ref{thm:main}, using Lemma~2.3.

\section{Notation}

\begin{definition}\label{def:sum}
For $s > 0$ let $H_n^{(s)} = \sum_{k=1}^{n} k^{-s}$.
\end{definition}

\begin{definition}\label{def:int}
Let $I_n^{(s)} = \int_1^n x^{-s}\,dx$.
\end{definition}

\begin{lemma}\label{lem:cmp}
For every $k \ge 2$ we have $k^{-s} \le \int_{k-1}^{k} x^{-s}\,dx$.
\end{lemma}

\begin{proof}
The function $x^{-s}$ is decreasing, so
\begin{equation}\label{eq:mono}
k^{-s} \le x^{-s} \quad \text{for } k-1 \le x \le k.
\end{equation}
Integrating \eqref{eq:mono} gives the claim.
\end{proof}

\section{The bound}

By Definition~\ref{def:int} and Lemma~\ref{lem:cmp}:

\begin{theorem}\label{thm:main}
For $s > 0$ and $n \ge 1$, $H_n^{(s)} \le 1 + I_n^{(s)}$.
\end{theorem}

\begin{proof}
Sum Lemma~\ref{lem:cmp} over $k = 2, \dots, n$ and use Definition~\ref{def:sum}.
\end{proof}

\begin{corollary}\label{cor:one}
$H_n^{(1)} \le 1 + \log n$.
\end{corollary}

\begin{thebibliography}{9}

\bibitem{Euler1735} L.~Euler, \emph{De summis serierum reciprocarum}, Comment. Acad. Sci. Petrop. 7 (1740), 123--134.

\end{thebibliography}

\end{document}
"""

p = new_project()
(p.dir / "out").mkdir()
(p.dir / "out" / "main.tex").write_text(TEX, encoding="utf-8")
(p.dir / "out" / "references.bib").write_text("@article{Euler1735,\n  title = {De summis serierum reciprocarum},\n}\n", encoding="utf-8")
HAVE_TEX = paperlib.find_engine() in ("pdflatex", "xelatex", "lualatex")

# ---------------------------------------------------------------- 1. the split
files = W.split_tex(TEX)
assert list(files) == ["main.tex", "bibliography.tex", "sections/01-introduction.tex", "sections/02-notation.tex",
                       "sections/03-the-bound.tex"], list(files)
main = files["main.tex"]
assert "\\input{sections/02-notation}" in main and "\\input{bibliography}" in main and main.rstrip().endswith("\\end{document}")


def joined(fs: dict) -> str:
    out = fs["main.tex"]
    for _ in range(3):
        out = re.sub(r"\\input\{([^}]+)\}[^\n]*", lambda m: fs.get(m.group(1) + ".tex", ""), out)
    return out


norm = lambda t: re.sub(r"\s+", " ", re.sub(r"(?m)^%.*$", "", t)).strip()      # noqa: E731
assert norm(joined(files)) == norm(TEX), "joining the files back gives the same document"
assert W.split_tex("\\documentclass{article}\\begin{document}Hello\\end{document}")["main.tex"].count("Hello") == 1
print("1. the paper is split into main.tex, one file per section and the bibliography; joined back it is the same document")

# ---------------------------------------------------------------- 2. making the project; safe paths
info = W.create(p)
paths = [t["path"] for t in info["tree"]]
assert {"main.tex", "references.bib", "sections/02-notation.tex", "bibliography.tex"} <= set(paths), paths
assert W.versions(p)[0]["label"] == "Made from the finished paper" and W.create(p)["exists"]
for bad in ("../x.tex", "/etc/passwd.tex", ".hidden.tex", "sections/../../x.tex", "C:/x.tex", "a" * 300 + ".tex", "x.exe"):
    try:
        W.resolve(p, bad, new=True)
        raise SystemExit(f"accepted {bad!r}")
    except W.WorkspaceError:
        pass
for bad in ("my file.tex", "fïle.tex"):
    try:
        W.new_file(p, bad)
        raise SystemExit(f"accepted {bad!r}")
    except W.WorkspaceError:
        pass
W.new_file(p, "sections/04-appendix.tex")
assert W.read_text(p, "sections/04-appendix.tex")["text"] == ""
try:
    W.delete(p, "main.tex")
    raise SystemExit("main.tex was deleted")
except W.WorkspaceError:
    pass
W.delete(p, "sections/04-appendix.tex")
f = W.read_text(p, "sections/01-introduction.tex")
assert W.write_text(p, "sections/01-introduction.tex", f["text"] + "\n% a note\n", base=f["sha"])["ok"]
stale = W.write_text(p, "sections/01-introduction.tex", "overwritten", base=f["sha"])
assert stale.get("conflict") and "overwritten" not in W.read_text(p, "sections/01-introduction.tex")["text"]
print("2. paths outside the project, hidden files and odd names are refused; a stale save never overwrites")

# ---------------------------------------------------------------- 3. compiling and the numbers
expect = {("section", "1"), ("section", "2"), ("section", "3"), ("Definition", "2.1"), ("Definition", "2.2"), ("Lemma", "2.3"),
          ("Theorem", "3.1"), ("Corollary", "3.2"), ("Equation", "2.1") if False else ("Equation", "1")}
counted = W.outline(p, prefer_compile=False)
got = {(it.get("name") if it["kind"] not in ("section",) else "section", it.get("number")) for it in counted["items"]
       if it.get("number") and it["kind"] in ("section", "env", "equation")}
assert got == expect, got
lemma = next(it for it in counted["items"] if it.get("label") == "lem:cmp")
assert lemma["proof"] and lemma["proof"]["line"] > lemma["end"], lemma
if HAVE_TEX:
    res = W.compile_project(p)
    assert res["ok"] and res["pages"] >= 1 and res["probe"] and res["has_pdf"], {k: res[k] for k in ("ok", "errors", "pages", "probe")}
    assert W.fresh_numbers(p)
    compiled = W.outline(p)
    assert compiled["numbers_from"] == "compiled"
    assert [(i["kind"], i.get("number")) for i in compiled["items"]] == [(i["kind"], i.get("number")) for i in counted["items"]], \
        "counting gives exactly the numbers LaTeX printed"
    eq = next(i for i in compiled["items"] if i["kind"] == "equation")
    assert eq["numbers"] == ["1"]
    s = W.sync(p)
    fw = s.forward("sections/02-notation.tex", lemma["line"] + 1)
    assert fw and fw["page"] >= 1 and fw["w"] > 0, fw
    back = s.inverse(fw["page"], fw["x"] + 5, fw["y"] + fw["h"] / 2)
    assert back["path"] == "sections/02-notation.tex" and abs(back["line"] - (lemma["line"] + 1)) <= 1, back
    # an error: reported with its file and line, and the PDF still made
    t = W.read_text(p, "sections/03-the-bound.tex")
    W.write_text(p, "sections/03-the-bound.tex", t["text"].replace("$H_n^{(1)}", "$\\notacommand H_n^{(1)}"))
    bad = W.compile_project(p)
    assert not bad["ok"] and bad["has_pdf"], bad["errors"]
    err = bad["errors"][0]
    assert err["path"] == "sections/03-the-bound.tex" and "Undefined control sequence" in err["message"], err
    assert W.read_text(p, "sections/03-the-bound.tex")["text"].split("\n")[err["line"] - 1].count("\\notacommand") == 1
    fixprop = A.explain_error(p, err)
    assert fixprop["kind"] == "edit" and fixprop["how"] == "ai" and "\\mathbb{Z}" in "".join(h["new"][0] for h in fixprop["hunks"])
    A.apply(p, fixprop["id"])
    again = W.compile_project(p)
    assert again["ok"], again["errors"]
    # a reference to a label that does not exist: a warning with its place
    t = W.read_text(p, "sections/03-the-bound.tex")
    W.write_text(p, "sections/03-the-bound.tex", t["text"].replace("By Definition~\\ref{def:int}", "By Definition~\\ref{def:nowhere}"))
    warn = W.compile_project(p)
    w = [x for x in warn["warnings"] if "nowhere" in x["message"]]
    assert w and w[0]["path"] == "sections/03-the-bound.tex" and w[0]["line"] > 0, warn["warnings"]
    W.write_text(p, "sections/03-the-bound.tex", t["text"])
    assert W.compile_project(p)["ok"]
    print("3. compiled here: every number read from LaTeX itself and equal to the count; SyncTeX both ways; errors and "
          "warnings with their file and line; the AI fixes the error on request")
else:
    print("3. (LaTeX is not installed here: compiling skipped; numbers counted)")

# ---------------------------------------------------------------- 4. history
v0 = W.snapshot(p, "before test edits", "manual", force=True)
t = W.read_text(p, "main.tex")
W.write_text(p, "main.tex", t["text"].replace("Partial sums", "Sums"))
d = W.diff_version(p, v0["id"])
assert d and d[0]["path"] == "main.tex" and "-\\title{Partial sums" in d[0]["diff"] and "+\\title{Sums" in d[0]["diff"]
W.restore(p, v0["id"])
assert "Partial sums" in W.read_text(p, "main.tex")["text"]
assert any(v["label"].startswith("Before going back") for v in W.versions(p))
objs = len(list((p.dir / W.HISTORY / "objects").iterdir()))
W.snapshot(p, "same again", "manual", force=True)
assert len(list((p.dir / W.HISTORY / "objects").iterdir())) == objs, "file contents are stored once"
print("4. history: versions, comparison with now, going back (the present kept as a version), contents stored once")

# ---------------------------------------------------------------- 5. commands done by rule
def run(cmd, **kw):
    return A.run(p, cmd, kw.get("where"))


r = run("remove definition 1")
assert r["kind"] == "edit" and r["how"] == "rule" and r["summary"] == "Remove Definition 2.1.", r["summary"]
assert any("No definition is printed as Definition 1" in w["text"] for w in r["warnings"])
assert any("\\ref{def:sum}" in w["text"] and w["level"] == "error" for w in r["warnings"]), "the \\ref to it is caught"
assert any("rewrite the sentences" in a["command"] for a in r["alternatives"])
assert len(r["hunks"]) == 2 and any(h["path"] == "sections/01-introduction.tex" for h in r["hunks"]), \
    "the hand-typed Lemma~2.3 in the introduction becomes Lemma~2.2"
intro_h = next(h for h in r["hunks"] if h["path"] == "sections/01-introduction.tex")
assert "Lemma~2.2" in intro_h["new"][-1] and "Lemma~2.3" in intro_h["old"][-1]
orphan = [w for w in r["warnings"] if "defines $H_n^{(s)}$" in w["text"]]
assert orphan and orphan[0]["level"] == "warn" and "1 time" in orphan[0]["text"] and "Theorem 3.1" in orphan[0]["text"], \
    "the symbol the definition introduces is named, with its uses after it (the abstract's mention before it does not count)"
assert any("keep the definition of $H_n^{(s)}$" in a["command"] for a in r["alternatives"])
assert A.defined_symbols(r"Let $p$ be a prime and let $i(G)$ be the number of involutions, denoted by $i$.") == ["i(G)", "i"]
assert A.defined_symbols(r"The \emph{$p$-adic valuation} $\nu_p(n)$ is the largest $k$ with $p^k \mid n$.") == ["\\nu_p"]
r2 = run("Remove Definition 2.2.")
assert r2["summary"] == "Remove Definition 2.2." and not any("No definition" in w["text"] for w in r2["warnings"])
try:
    run("remove definition 7")
    raise SystemExit("an item that does not exist was removed")
except A.AssistError as e:
    assert "There is no Definition 7" in str(e) and "Definition 2.1" in str(e), e
nav = run("go to lemma 2.3")
assert nav["kind"] == "navigate" and nav["navigate"]["path"] == "sections/02-notation.tex" and nav["navigate"]["line"] == lemma["line"]
pr = run("delete the proof of Theorem 3.1")
assert pr["summary"] == "Remove the proof of Theorem 3.1." and all("\\begin{theorem}" not in "".join(h["old"]) for h in pr["hunks"])
mv = run("move Lemma 2.3 after Theorem 3.1")
assert mv["kind"] == "edit" and {h["path"] for h in mv["hunks"]} == {"sections/01-introduction.tex", "sections/02-notation.tex",
                                                                     "sections/03-the-bound.tex"}
assert any("Lemma~2.3" in "".join(h["old"]) for h in mv["hunks"]), "the typed Lemma~2.3 follows the lemma to its new number"
ahead = [w["text"] for w in mv["warnings"] if "now comes after it" in w["text"]]
assert any(t.startswith("the proof of Theorem 3.1 uses Lemma 3.2") for t in ahead), ahead
assert not any("Introduction" in t for t in ahead), "a forward reference that was there before the move is not blamed on it"
mv2 = run("move Definition 2.2 before Definition 2.1")
assert not any("defines" in w["text"] or "now comes after it" in w["text"] for w in mv2["warnings"]), mv2["warnings"]
cv = run("make lemma 2.3 a theorem")
assert cv["summary"] == "Make Lemma 2.3 a theorem." and any("\\begin{theorem}" in "".join(h["new"]) for h in cv["hunks"])
assert any("Theorem~2.3" in "".join(h["new"]) for h in cv["hunks"]), "a lemma typed by hand is renamed with it"
sec = run("remove section 2")
assert any("\\input{sections/02-notation}" in "".join(h["old"]) for h in sec["hunks"])
assert any("Section~2" in w["text"] and w["level"] == "warn" for w in sec["warnings"])
assert any("Section~2 proves" in "".join(h["new"]) for h in sec["hunks"]), "Section~3 becomes Section~2"
assert any(w["level"] == "error" and "lem:cmp" in w["text"] for w in sec["warnings"]), "refs into the removed section are caught"
print("5. by rule: remove (by printed number, by counting, with the \\ref and typed numbers checked), go to, remove a proof, "
      "move, change kind, remove a section")

# ---------------------------------------------------------------- 6. applying: all, part, or refused when stale
r = run("remove definition 1")
part = A.apply(p, r["id"], [h["id"] for h in r["hunks"] if h["path"] == "sections/02-notation.tex"])
assert part["changed"] == 1 and "def:sum" not in W.read_text(p, "sections/02-notation.tex")["text"]
assert "Lemma~2.3" in W.read_text(p, "sections/01-introduction.tex")["text"], "the change not ticked is not made"
try:
    A.apply(p, r["id"])
    raise SystemExit("applied twice")
except A.AssistError:
    pass
before = W.versions(p)[-1]
assert before["kind"] == "ai" and before["label"].startswith("AI: remove definition 1")
W.restore(p, W.versions(p)[-2]["id"])           # undo from the history
assert "def:sum" in W.read_text(p, "sections/02-notation.tex")["text"]
r = run("remove definition 1")
t = W.read_text(p, "sections/02-notation.tex")
W.write_text(p, "sections/02-notation.tex", t["text"] + "\n% typed meanwhile\n")
try:
    A.apply(p, r["id"])
    raise SystemExit("a stale proposal was applied")
except A.AssistError as e:
    assert "changed after the proposal" in str(e)
W.write_text(p, "sections/02-notation.tex", t["text"])
print("6. a proposal is applied whole or in part, once, never over newer typing; the history undoes it")

# ---------------------------------------------------------------- 7. commands read by the (simulated) AI
r = run("Make the proof of Lemma 2.3 more detailed")
assert r["how"] == "ai" and r["kind"] == "edit" and r["summary"].startswith("Wrote the proof of Lemma 2.3")
assert any(w.get("formulas") for w in r["warnings"]), "formulas changed inside the proof are reported"
assert not any(w.get("formulas") and w["level"] == "warn" for w in r["warnings"]), "...but not as changes outside it"
r = run("Make the proof of Lemma 2.3 more detailed, bad find first")
assert r["kind"] == "edit", "an answer that cannot be placed is asked for again"
assert sim.EDITOR_LOG[-1].endswith("bad find first")
r = run("remove Definition 2.1, and rewrite the sentences that refer to it so that they still read correctly")
assert r["how"] == "ai" and not any(w["level"] == "error" for w in r["warnings"]), r["warnings"]
assert any("Definition~\\ref{def:sum}" in "".join(h["old"]) for h in r["hunks"])
r = run("change a formula somewhere in the paper")
assert any(w.get("formulas") and w["level"] == "info" for w in r["warnings"])
q = run("Which lemma is used in the proof of the main theorem?")
assert q["kind"] == "answer" and "Lemma 2.3" in q["answer"] and not q["hunks"]
q = run("tidy that up")
assert q["kind"] == "question" and "Which statement" in q["question"]
os_env = __import__("os").environ
os_env["SCRIPTAVERA_MOCK_FAIL"] = "limits:Editor command"
try:
    run("Make the proof of Lemma 2.3 more detailed")
    raise SystemExit("a model at its limit went unnoticed")
except A.AssistError as e:
    assert "next best model" in str(e)
print("7. by the AI: rewriting a proof, retrying an answer that does not fit, fixing the sentences that refer to a removed "
      "item, formulas reported, questions answered, unclear commands asked back, a model at its limit said plainly")

# ---------------------------------------------------------------- 8. over HTTP
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
from scriptavera.server import create_app
server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
B = f"http://127.0.0.1:{port}"
E = f"{B}/api/projects/{p.id}/editor"
page = requests.get(f"{B}/editor/{p.id}")
assert page.status_code == 200 and "editor.js" in page.text and "codemirror.min.js" in page.text
js = requests.get(f"{B}/static/vendor/pdfjs/pdf.min.mjs")
assert js.status_code == 200 and "javascript" in js.headers["content-type"], js.headers["content-type"]
assert requests.get(f"{B}/static/editor.js").status_code == 200
i = requests.get(E).json()
assert i["ok"] and i["exists"] and i["main"] == "main.tex" and not i["source"]["changed"]
f = requests.get(E + "/file", params={"path": "main.tex"}).json()
assert f["text"].startswith("% The paper as a LaTeX project")
ok = requests.put(E + "/file", json={"path": "main.tex", "text": f["text"] + "\n", "base": f["sha"]})
assert ok.json()["ok"]
conflict = requests.put(E + "/file", json={"path": "main.tex", "text": "x", "base": f["sha"]})
assert conflict.status_code == 409 and conflict.json()["conflict"]
assert requests.get(E + "/file", params={"path": "../state.json"}).status_code == 400
assert requests.get(E + "/file", params={"path": "x.tex"}).status_code == 400
nf = requests.post(E + "/files", json={"action": "new_file", "path": "notes.tex"}).json()
assert any(t["path"] == "notes.tex" for t in nf["tree"])
up = requests.post(E + "/upload", data={"folder": "figures"}, files={"files": ("plot one.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")}).json()
assert up["saved"] == ["figures/plot-one.png"]
img = requests.get(E + "/file", params={"path": "figures/plot-one.png", "raw": "1"})
assert img.status_code == 200 and img.headers["content-type"] == "image/png"
rn = requests.post(E + "/files", json={"action": "rename", "path": "notes.tex", "to": "sections/notes.tex"}).json()
assert any(t["path"] == "sections/notes.tex" for t in rn["tree"])
dl = requests.post(E + "/files", json={"action": "delete", "path": "sections/notes.tex"}).json()
assert not any(t["path"] == "sections/notes.tex" for t in dl["tree"])
ol = requests.get(E + "/outline").json()
assert any(x["label"] == "lem:cmp" and x["what"] == "Lemma 2.3" for x in ol["complete"]["labels"]), ol["complete"]["labels"]
assert ol["complete"]["cites"][0]["key"] == "Euler1735" and "lemma" in ol["complete"]["envs"]
if HAVE_TEX:
    c = requests.post(E + "/compile", json={}).json()
    assert c["ok"] and c["build"]["ok"] and "numbers" not in c["build"]
    pdf = requests.get(E + "/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert "Output written" in requests.get(E + "/log").text or "Transcript written" in requests.get(E + "/log").text
    sy = requests.post(E + "/sync", json={"direction": "forward", "path": "sections/03-the-bound.tex", "line": 5}).json()
    assert sy["ok"] and sy["page"] >= 1
    inv = requests.post(E + "/sync", json={"direction": "inverse", "page": sy["page"], "x": sy["x"] + 4, "y": sy["y"] + 3}).json()
    assert inv["ok"] and inv["path"] == "sections/03-the-bound.tex"
pr = requests.post(E + "/assist", json={"command": "remove definition 1"}).json()["proposal"]
assert pr["kind"] == "edit" and "old" not in pr and "new" not in pr
bad = requests.post(E + "/assist", json={"command": "remove definition 9"})
assert bad.status_code == 400 and "There is no Definition 9" in bad.json()["error"]
ap = requests.post(E + "/apply", json={"id": pr["id"], "keep": None}).json()
assert ap["ok"] and ap["changed"] == 2 and "sections/02-notation.tex" in ap["files"]
h = requests.get(E + "/history").json()["versions"]
assert h[0]["kind"] == "ai" and isinstance(h[0]["files"], int)
back = requests.post(E + "/history", json={"action": "restore", "id": h[1]["id"]}).json()
assert back["ok"] and "def:sum" in requests.get(E + "/file", params={"path": "sections/02-notation.tex"}).json()["text"]
sv = requests.post(E + "/history", json={"action": "save", "label": "for my co-author"}).json()
assert sv["version"]["label"] == "for my co-author"
z = zipfile.ZipFile(io.BytesIO(requests.get(E + "/download").content))
assert "main.tex" in z.namelist() and "sections/02-notation.tex" in z.namelist()
# a change made by Scriptavera after the copy was made is said, and starting again keeps the copy in the history
(p.dir / "out" / "main.tex").write_text(TEX.replace("Partial sums", "Explicit partial sums"), encoding="utf-8")
assert requests.get(E).json()["source"]["changed"]
reset = requests.post(E, json={"reset": True}).json()
assert reset["ok"] and "Explicit partial sums" in requests.get(E + "/file", params={"path": "main.tex"}).json()["text"]
assert any(v["label"].startswith("Before starting again") for v in requests.get(E + "/history").json()["versions"])
# a request from another website is refused
assert requests.post(E + "/compile", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
# a paper that is not written yet has no editor
q2 = new_project()
assert requests.post(f"{B}/api/projects/{q2.id}/editor", json={}).status_code == 400
print("8. over HTTP: the page and its libraries, files (conflicts, bad paths, upload, rename, delete), compile, PDF, log, "
      "SyncTeX, completion, commands and their proposals, history, download, starting again; other websites refused")
server.should_exit = True
print("editor ok")
