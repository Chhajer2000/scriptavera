# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The JSON API over real HTTP, with simulated models: every route, the three stops for the author, waiting for
the strongest model, the author's permission to use the next best one, automatic restart, redoing a step."""
import json, os, socket, threading, time
from helpers import ROOT, isolate, pages_pdf
tmp = isolate("scriptavera_srv_")
import requests, uvicorn
from scriptavera import config, pipeline, store
from scriptavera.server import create_app

sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
B = f"http://127.0.0.1:{port}"


def call(method, path, expect=200, **kw):
    r = requests.request(method, B + path, timeout=120, **kw)
    assert r.status_code == expect, (method, path, r.status_code, r.text[:400])
    if "json" in r.headers.get("content-type", ""):
        return r.json()
    return r if r.ok else True                      # an error without a body: reaching here means it was the expected one


def post(path, body=None, expect=200):
    return call("POST", path, expect, json=body if body is not None else {})


def project(pid):
    return call("GET", f"/api/projects/{pid}")


def wait(pid, stage, statuses=("waiting", "done"), seconds=120, allow=()):
    t0 = time.time()
    while time.time() - t0 < seconds:
        b = project(pid)
        s = b["state"]["stages"][stage]
        if s.get("status") in statuses and not b["state"]["running"]:
            return b
        stopped = [k for k, v in b["state"]["stages"].items() if v.get("status") in ("error", "paused")]
        if stopped and not b["state"]["running"] and not set(stopped) <= set(allow) and b["state"]["stages"][stopped[0]]["status"] not in statuses:
            raise SystemExit(f"stopped at {stopped}: {b['state']['stages'][stopped[0]].get('error')}")
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting for {stage}: " + str({k: v.get("status") for k, v in project(pid)["state"]["stages"].items()}))


def answers(b):
    g = b["glossary"]
    return {"glossary": [{"id": e["id"], "meaning": e["meaning"], "confirmed": True} for e in g["entries"]],
            "topic": {"problem": b["topic"]["problem"]}, "answers": [], "extra": [{"symbol": "\\int", "meaning": "the Riemann integral"}],
            "instructions": "Write log for the natural logarithm."}


# ------------------------------------------------------------------ meta, journals, creation
m = call("GET", "/api/meta")
assert m["mock"] and [x["id"] for x in m["phases"]] == ["read", "write", "revise", "final"] and len(m["stages"]) == 20
assert {s["phase"] for s in m["stages"]} == {"read", "write", "revise", "final"}
assert [s["id"] for s in m["stages"] if s.get("gate")] == ["topiccheck", "confirm", "review", "scope", "decide", "finish"]
assert m["plan"]["writer"]["chain"] == ["mock:sim-text"] and m["plan"]["writer"]["weaker"] == ["mock:sim-small", "mock:sim-vision"], m["plan"]["writer"]
assert m["plan"]["fast"]["weaker"] == [] and len(m["plan"]["fast"]["chain"]) == 3
assert set(m["templates"]) == {"amsart", "article", "elsarticle"} and m["templates"]["elsarticle"]["tested"] is False
assert [s["id"] for s in m["statements"]] == ["ack", "funding", "competing", "data"] and any(f["id"] == "springer" for f in m["families"])
j = call("GET", "/api/journals", params={"q": "annals inequalities"})
assert j["journals"][0]["family"] == "elsevier" and j["message"] == "", j
assert call("GET", "/api/journals", params={"q": "ab"})["journals"] == []
assert post("/api/projects/nope/run", expect=404)["error"]

meta = {"name": "Server test", "domains": ["math.CA", "not.a.domain"], "subarea": "series", "title": "",
        "authors": [{"name": "A. Tester", "affiliation": "Somewhere", "email": "", "orcid": "https://orcid.org/0000-0002-1825-0097"}, {"name": "  "}],
        "declaration": {"agreed": True, "author": 0},
        "known_refs": [], "journal": j["journals"][0], "problem": "Bounds for partial sums of series of reciprocal powers.",
        "keywords": "partial sums, reciprocal powers, explicit bounds", "key_papers": []}
bad = requests.post(B + "/api/projects", data={"meta": json.dumps({**meta, "domains": []})}, files={"files": ("notes.pdf", pages_pdf(), "application/pdf")})
assert bad.status_code == 400 and "domain" in bad.json()["error"]
for missing, word in (({"problem": "short"}, "problem"), ({"keywords": "one"}, "keywords"), ({"authors": [{"name": " "}]}, "author"),
                      ({"declaration": {"agreed": False, "author": 0}}, "declaration"), ({"declaration": {"agreed": True}}, "making the record"),
                      ({"authors": [{"name": "A. Tester", "orcid": "0000-0002-1825-0098"}]}, "ORCID")):
    bad = requests.post(B + "/api/projects", data={"meta": json.dumps({**meta, **missing})}, files={"files": ("notes.pdf", pages_pdf(), "application/pdf")})
    assert bad.status_code == 400 and word in bad.json()["error"], bad.text
r = requests.post(B + "/api/projects", data={"meta": json.dumps(meta)}, files={"files": ("notes.pdf", pages_pdf(), "application/pdf")})
assert r.status_code == 200, r.text
pid = r.json()["id"]
b = project(pid)
assert len(b["state"]["pages"]) == 6 and b["state"]["meta"]["domains"] == ["math.CA"] and len(b["state"]["meta"]["authors"]) == 1
assert b["state"]["meta"]["authors"][0]["orcid"] == "0000-0002-1825-0097", "an ORCID iD is kept in its standard form"
pr = b["proof"]                                   # the record made at upload names the authors and carries the declaration
assert pr["authors"][0]["name"] == "A. Tester" and pr["recorded_by"] == {"name": "A. Tester", "is_author": True}
assert pr["declaration"].startswith("I, A. Tester, am the author of these notes.") and pr["subjects"][0]["code"] == "math.CA"
assert pr["description"].startswith("Bounds for partial sums") and pr["keywords"][0] == "partial sums"
import shutil                                     # a paper uploaded before records existed: recorded later, with a declaration
shutil.rmtree(config.PROJECTS_DIR / pid / "proof"); shutil.rmtree(config.PROJECTS_DIR / pid / "originals")
assert project(pid)["proof"]["missing"]
assert "declaration" in post(f"/api/projects/{pid}/proof", {"action": "record_now"}, expect=400)["error"]
late = post(f"/api/projects/{pid}/proof", {"action": "record_now", "declaration": {"agreed": True, "author": 0}})["proof"]
assert late["declaration"].startswith("I, A. Tester, am the author") and "after the upload" in late["note"] and len(late["files"]) == 6
assert b["journal"]["name"] == "Simulated Annals of Inequalities" and b["profile"]["template"] == "elsarticle" and b["profile"]["origin"]["template"] == "typical"
assert call("GET", f"/api/projects/{pid}/page/1").headers["content-type"] == "image/jpeg" and call("GET", f"/api/projects/{pid}/page/99", 404)
assert any(x["id"] == pid for x in call("GET", "/api/projects")["projects"])
print("created", pid)

# ------------------------------------------------------------------ first stop
post(f"/api/projects/{pid}/run")
assert "already running" in post(f"/api/projects/{pid}/run", expect=400)["error"]
b = wait(pid, "topiccheck")
assert b["state"]["meta"]["keywords"] == ["partial sums", "reciprocal powers", "explicit bounds"] and b["topic"]["keywords"][0] == "partial sums"
assert post(f"/api/projects/{pid}/topic", {"action": "refine"}, 400)["error"]
post(f"/api/projects/{pid}/topic", {"action": "refine", "comment": "Explicit inequalities for every exponent.", "keywords": ["partial sums", "integral test"]})
b = wait(pid, "topiccheck")
assert b["topic"]["refined"] == 1 and b["state"]["meta"]["keywords"] == ["partial sums", "integral test"]
post(f"/api/projects/{pid}/topic", {"action": "confirm"})
b = wait(pid, "confirm")
assert post(f"/api/projects/{pid}/locate", {"symbol": "\\log", "page": 3})["found"] is True
assert post(f"/api/projects/{pid}/locate", {"symbol": "tail", "page": 6})["found"] is False
assert post(f"/api/projects/{pid}/locate", {"symbol": "x", "page": 77}, 400)
post(f"/api/projects/{pid}/library", {"action": "state", "key": "Demo2011Simulated", "state": "excluded"})
post(f"/api/projects/{pid}/reglossary")
assert "still need your confirmation" in post(f"/api/projects/{pid}/confirm", {"glossary": []}, 400)["error"]
assert "not at the review step" in post(f"/api/projects/{pid}/review", expect=400)["error"]

# the strongest reader is resting when page 3 is transcribed: the step waits, and says which model could step in
os.environ.update(SCRIPTAVERA_MOCK_FAIL="busy:Transcribe page 3", SCRIPTAVERA_MOCK_RESET="4")
post(f"/api/projects/{pid}/confirm", answers(b))
b = wait(pid, "transcribe", ("paused",))
s = b["state"]["stages"]["transcribe"]
assert s["reason"] == "limits" and s["weaker"] == "sim-small" and 3 < s["resume_at"] - time.time() < 12, s
assert "strongest models for the 'Handwriting transcriber' job are not free" in s["error"] and "sim-vision (daily limit reached" in s["error"], s["error"]
assert list(b["transcript"]["pages"]) == ["1", "2"], "the pages finished before the wait are kept"
assert next(x for x in call("GET", "/api/projects")["projects"] if x["id"] == pid)["paused"]
post(f"/api/projects/{pid}/run", {"relax": True})                       # the author allows the next best model for this step
b = wait(pid, "review", seconds=60, allow=("figures",))                 # the next step waits by itself until the strong model is back
s = b["state"]["stages"]
assert s["transcribe"]["relax"] and s["transcribe"]["stand_in"] == {"wanted": ["sim-vision", "sim-text"], "used": {"sim-small": 5}}, s["transcribe"]
assert b["transcript"]["pages"]["2"]["model"] == "sim-vision" and b["transcript"]["pages"]["3"]["model"] == "sim-small"
assert "stand_in" not in s["figures"] and s["figures"]["model"] == "mock:sim-vision", s["figures"]
print("waited for the strongest model; the author let the next best one finish one step; the following step restarted by itself")

# ------------------------------------------------------------------ second stop: transcript and figures
# doubts about language saved by an earlier version of the app are not shown either, and nothing on disk changes
p_ = store.Project(pid)
tr_ = p_.load("transcript"); fl_ = p_.load("flags") or []
text1 = tr_["pages"]["1"]["text"]; tr_["pages"]["1"]["text"] = text1 + "\nSo $f(4)$ many term , therefore, holds."
tr_["pages"]["1"]["uncertain"] = tr_["pages"]["1"]["uncertain"] + [
    {"snippet": "term , therefore,", "issue": 'singular "term" written instead of plural', "alt": "terms"},
    {"snippet": "f(4)", "issue": "a typo for f(n)", "alt": "f(n)"}]
p_.save("transcript", tr_); p_.save("flags", fl_ + [{"page": 1, "snippet": "many term , therefore,", "issue": "should be plural",
                                                     "suggestion": "suggested reading: \u201c\u2026 many **terms**, therefore \u2026\u201d"}])
b = project(pid)
shown = [u["snippet"] for u in b["transcript"]["pages"]["1"]["uncertain"]]
assert "f(4)" in shown and "term , therefore," not in shown, shown
assert not any("many term" in f["snippet"] for f in b["flags"]) and b["language_settled"] >= 2
assert len(p_.load("transcript")["pages"]["1"]["uncertain"]) == len(tr_["pages"]["1"]["uncertain"])
# 'it is correct as read' is remembered
post(f"/api/projects/{pid}/doubt", {"page": 1, "snippet": "f(4)", "alt": "f(n)"})
assert "f(4)" not in [u["snippet"] for u in project(pid)["transcript"]["pages"]["1"]["uncertain"]]
p_.save("dismissed", [])
tr_["pages"]["1"]["uncertain"] = tr_["pages"]["1"]["uncertain"][:-2]; tr_["pages"]["1"]["text"] = text1
p_.save("transcript", tr_); p_.save("flags", fl_)
b = project(pid)
fig = b["figures"][0]
assert fig["id"] == "f1" and fig["choice"] == "" and fig["tikz_ok"] in (True, None)
assert call("GET", f"/api/projects/{pid}/figure/f1").headers["content-type"] == "image/jpeg"
if fig["tikz_ok"]:
    assert call("GET", f"/api/projects/{pid}/figure/f1", params={"kind": "tikz"}).headers["content-type"] == "image/png"
assert call("GET", f"/api/projects/{pid}/figure/f9", 404)
assert "each figure" in post(f"/api/projects/{pid}/review", expect=400)["error"]
assert post(f"/api/projects/{pid}/figure/f1", {"redraw": "Make the curve steeper."})["figure"]["redrawn"] == 1
assert "Unknown choice" in post(f"/api/projects/{pid}/figure/f1", {"choice": "maybe"}, 400)["error"]
post(f"/api/projects/{pid}/figure/f1", {"choice": "tikz" if fig["tikz_ok"] else "orig", "caption": "Rectangles under a decreasing graph."})
text5 = b["transcript"]["pages"]["5"]["text"]
call("PUT", f"/api/projects/{pid}/transcript/5", json={"text": text5 + "\n\nNote added by the author."})
post(f"/api/projects/{pid}/review")
b = wait(pid, "scope")
# ------------------------------------------------------------------ the assessment, and the author's plan
assert b["assessment"]["results"]["R4"]["proof"] == "gap" and b["scope_default"]["results"]["R2"] == "credit" and b["state"]["stages"]["scope"]["status"] == "waiting"
assert post(f"/api/projects/{pid}/scope", {"kind": "not_ready"})["scope"]["kind"] == "not_ready"
assert project(pid)["state"]["stages"]["scope"]["stopped"] and not project(pid)["state"]["running"]
post(f"/api/projects/{pid}/scope", {"kind": "article", "results": {"R2": "credit", "R4": "keep"}, "note": "Keep the lower bound short."})
b = wait(pid, "decide")
assert b["scope"]["note"] == "Keep the lower bound short." and b["state"]["stages"]["scope"]["status"] == "done"
assert b["transcript"]["pages"]["5"]["edited"] and b["approved"] and b["figures"][0]["caption"] == "Rectangles under a decreasing graph."
assert 'class="paper"' in b["draft"]["html"] and b["draft"]["results"] == 5 and b["draft"]["proofs"] == 5 and b["draft"]["sections"] == 3
assert b["dossier"]["results"][2]["role"] == "main" and b["plan"]["sections"][1]["title"] == "Upper bounds" and b["intro"]["text"]
rev = b["revision"]
assert len(rev["items"]["simplify"]) == 1 and len(rev["items"]["issues"]) == 1 and len(rev["items"]["remarks"]) == 3 and rev["decisions"] is None

# ------------------------------------------------------------------ third stop: the author's decisions
iid = rev["items"]["issues"][0]["id"]
assert "still need your decision" in post(f"/api/projects/{pid}/decisions", {"simplify": {"S1": "accept"}, "issues": {iid: {"choice": "instruct"}}}, 400)["error"]
post(f"/api/projects/{pid}/decisions", {"simplify": {"S1": "accept"}, "issues": {iid: {"choice": "fix"}}, "remarks": {}})
b = wait(pid, "assemble", ("done",))
P = b["paper"]
assert P["title"] and P["compile"]["template"] == "elsarticle" and "res:S1" in [x.get("label") for x in P["blocks"]]
if P["compile"]["engine"]:            # elsarticle is not installed here: the PDF is set in the standard class, and both sources are offered
    assert P["compile"]["ok"] and P["compile"]["fallback"]["template"] == "elsarticle" and "main_standard_class.tex" in b["downloads"], P["compile"]
for name in b["downloads"]:
    assert call("GET", f"/api/projects/{pid}/download/{name}").status_code == 200
tex = call("GET", f"/api/projects/{pid}/download/main.tex").text
assert "{elsarticle}" in tex and "\\begin{frontmatter}" in tex and "\\journal{Simulated Annals of Inequalities}" in tex
assert call("GET", f"/api/projects/{pid}/download/secrets.txt", 404)
print("final version:", P["title"], "|", b["downloads"])

# ------------------------------------------------------------------ the last stop: title, introduction, citations
b = wait(pid, "finish", ("waiting",))         # the stop opens a moment after the final version is done
assert b["state"]["stages"]["finish"]["status"] == "waiting" and b["finish"]["titles"] and b["finish"]["related"]
assert post(f"/api/projects/{pid}/finish", {"action": "finish", "title": ""}, 400)["error"].startswith("Choose a title")
F = post(f"/api/projects/{pid}/finish", {"action": "titles"})["finish"]
assert len(F["titles"]) == 5
F = post(f"/api/projects/{pid}/finish", {"action": "cite", "keys": ["Sample2018Simulated"], "instruction": "Keep it short."})["finish"]
assert [r["key"] for r in F["related"] if r["in_intro"]] == ["Sample2018Simulated"], F["related"]
F = post(f"/api/projects/{pid}/finish", {"action": "finish", "title": F["titles"][1]})["finish"]
assert F["finished"] and project(pid)["state"]["stages"]["finish"]["status"] == "done" and project(pid)["paper"]["title"] == F["titles"][1]
print("finish: titles suggested and chosen, citations chosen, files rebuilt")

# ------------------------------------------------------------------ the author edits the finished paper
P = call("PUT", f"/api/projects/{pid}/paper", json={"title": "Bounds for $H_n^{(s)}$ & more", "keywords": ["sums", "integrals"], "msc": ["26D15"]})["paper"]
assert P["title_source"] == "author" and P["keywords"] == ["sums", "integrals"] and P["compile"]["ok"] == bool(P["compile"]["engine"])
bid = next(x["id"] for x in P["blocks"] if x["type"] == "para")
P = call("PUT", f"/api/projects/{pid}/paper", json={"block": {"id": bid, "text": "An edited paragraph with $a \\le b$."}})["paper"]
assert any(x.get("text") == "An edited paragraph with $a \\le b$." for x in P["blocks"])
assert call("PUT", f"/api/projects/{pid}/paper", 400, json={"block": {"id": "b999", "text": "x"}})
P = post(f"/api/projects/{pid}/revise", {"target": "intro", "instruction": "shorter"})["paper"]
assert P["intro"].endswith("(Revised as requested.)") and post(f"/api/projects/{pid}/revise", {"target": "intro"}, 400)
g = post(f"/api/projects/{pid}/journal/guide", {"source": "Guide for authors. Abstract, keywords, references, manuscript, submission, LaTeX and figures. " * 8})
assert g["values"]["abstract_max_words"] == 200 and g["url"] == ""
assert post(f"/api/projects/{pid}/journal/guide", {"source": "too short"}, 400) and post(f"/api/projects/{pid}/journal/guide", {}, 400)
jr = post(f"/api/projects/{pid}/journal", {"journal": {**b["journal"], "guide": g["values"], "custom": {"template": "amsart", "page_limit": 2}},
                                             "statements": {"funding": "None.", "competing": "None.", "data": "No data."}})
assert jr["profile"]["template"] == "amsart" and jr["profile"]["origin"]["template"] == "author" and jr["profile"]["line_numbers"]
open_items = [x["label"] for x in jr["paper"]["checklist"] if not x["ok"]]
assert jr["paper"]["compile"]["fallback"] is None and (not jr["paper"]["compile"]["engine"] or open_items == ["Keywords: 3–5", "At most 2 pages"]), jr["paper"]["checklist"]
assert post(f"/api/projects/{pid}/journal", {"statements": {"ack": "Thanks."}})["journal"]["name"], "statements alone keep the journal"
assert post(f"/api/projects/{pid}/journal", {"journal": None})["profile"]["journal"] is None
print("edits, rewrite on request, journal guide, requirements and statements ok")

# ------------------------------------------------------------------ deciding again makes the final version again
post(f"/api/projects/{pid}/decisions", {"simplify": {"S1": "reject"}, "issues": {iid: {"choice": "keep"}}, "remarks": {"P4": "Say in the introduction what zeta is.", "N1": "Leave it."}})
b = wait(pid, "assemble", ("done",))
P = b["paper"]
assert "res:S1" not in [x.get("label") for x in P["blocks"]] and not P["revision"]["simplified"]
assert P["title_source"] == "author" and P["title"].startswith("Bounds for"), "a title the author wrote stays when the final version is made again"
assert [x["id"] for x in P["revision"]["remarks"]] == ["P4"] and "denotes the limit" in P["intro"], P["revision"]    # a remark on the introduction is applied there
assert [x["id"] for x in P["revision"]["unapplied"]] == ["N1"], P["revision"]["unapplied"]                # nothing to anchor it to: reported, not guessed
assert P["audit"]["preserved"] == P["audit"]["total"] and not P["audit"]["added"]

# ------------------------------------------------------------------ failures: a limit, an unexpected error, stopping, redoing
os.environ.update(SCRIPTAVERA_MOCK_FAIL="limits:Referee", SCRIPTAVERA_MOCK_RESET="600")
post(f"/api/projects/{pid}/run", {"from": "verify"})
b = wait(pid, "referee", ("paused",))
assert b["state"]["stages"]["verify"]["status"] == "done" and b["paper"] is None and b["downloads"] == [] and b["revision"]["verify"]["done"]
post(f"/api/projects/{pid}/autostop")
s = project(pid)["state"]["stages"]["referee"]
assert s["status"] == "error" and s["resume_at"] is None and "free-tier limit" in s["error"]
os.environ["SCRIPTAVERA_MOCK_FAIL"] = "Propose titles"
post(f"/api/projects/{pid}/run")
b = wait(pid, "decide")
post(f"/api/projects/{pid}/decisions", {"simplify": {"S1": "accept"}, "issues": {iid: {"choice": "instruct", "note": "Replace the last value by log(n+1)."}}})
b = wait(pid, "assemble", ("error",))
assert "simulated failure" in b["state"]["stages"]["assemble"]["error"] and b["state"]["error"]["stage"] == "assemble"
n_before = len(b["calls"])
post(f"/api/projects/{pid}/run")
b = wait(pid, "assemble", ("done",))
again = [c["label"] for c in b["calls"][n_before:]]
assert again == ["Propose titles"], again                               # everything before the failure was kept: only the failed call is repeated
assert b["paper"]["revision"]["instructed"] and "\\log(n+1)" in json.dumps(b["paper"]["blocks"])
print("limit pause, cancelled restart, unexpected error and exact resume ok")

# ------------------------------------------------------------------ a run in which the readings find nothing: the third stop is passed
os.environ["SCRIPTAVERA_MOCK_CLEAN"] = "1"
post(f"/api/projects/{pid}/run", {"from": "review"})
b = wait(pid, "review")
assert b["approved"] is False and b["paper"] is None and b["transcript"]["pages"]["5"]["edited"], "redoing from the review keeps the transcript"
post(f"/api/projects/{pid}/review", {"figures": {"f1": "none"}})
b = wait(pid, "assemble", ("done",), seconds=180)
s = b["state"]["stages"]
assert s["decide"]["status"] == "done" and s["decide"]["skipped"] and b["revision"]["decisions"]["auto"], s["decide"]
assert s["scope"]["status"] == "done" and s["scope"]["skipped"] and b["scope"]["auto"] and b["scope"]["kind"] == "article", s["scope"]
assert not b["paper"]["figures"] and "p-fig" not in b["paper"]["html"] and b["paper"]["revision"]["referee"]["recommendation"] == "accept"
os.environ.pop("SCRIPTAVERA_MOCK_CLEAN")
print("a clean run passes the plan and the decision steps by itself; a figure the author declined is left out")

# ------------------------------------------------------------------ stop, start over, delete
os.environ["SCRIPTAVERA_MOCK_DELAY"] = "0.4"
import tests.simulator as sim
sim.DELAY = 0.4
post(f"/api/projects/{pid}/run", {"from": "scout"})
time.sleep(0.6)
post(f"/api/projects/{pid}/stop")
t0 = time.time()
while project(pid)["state"]["running"] and time.time() - t0 < 20:
    time.sleep(0.1)
b = project(pid)
assert not b["state"]["running"] and b["state"]["stages"]["scout"]["status"] == "pending" and b["transcript"] is None and b["glossary"] is None
sim.DELAY = 0.0
call("DELETE", f"/api/projects/{pid}")
assert call("GET", f"/api/projects/{pid}", 404) and not (config.PROJECTS_DIR / pid).exists()
server.should_exit = True
print("server ok")
