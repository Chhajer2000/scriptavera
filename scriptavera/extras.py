# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""From the finished paper: a plain-language summary, seminar slides (Beamer) and a poster.

The model chooses what to show and writes the words; every formula it uses must be one of the paper's own (compared
by the fidelity audit's normalisation), and anything else is reported. The LaTeX around the content (class,
packages, the paper's macros, the author's own macros for typed notes) is put together by code and compiled here
when LaTeX is installed."""
from __future__ import annotations

import re

from . import audit, paper as paperlib, prompts
from .llm import router
from .pipeline import Run
from .store import Project
from .texutil import check_balance
from .util import fit

FILES = {"summary": ["summary.txt"], "slides": ["slides.tex", "slides.pdf"], "poster": ["poster.tex", "poster.pdf"]}


def _content(P: dict, room: int = 16000) -> str:
    results = [b for b in P.get("blocks", []) if b.get("type") == "env" and b.get("env") in paperlib.THEOREM_ENVS]
    proofs = [b for b in P.get("blocks", []) if b.get("type") == "proof"]
    sections = [b.get("title", "") for b in P.get("blocks", []) if b.get("type") == "section"]
    parts = [f"TITLE: {P.get('title', '')}", f"ABSTRACT: {P.get('abstract', '')}", "SECTIONS: " + "; ".join(sections),
             "INTRODUCTION:\n" + fit(P.get("intro", ""), room // 4, "head"),
             "RESULTS:\n" + "\n".join(f"- {paperlib.THEOREM_ENVS.get(b['env'], b['env'])} {b.get('num', '')}: {fit(b.get('text', ''), 900, 'head')}"
                                      for b in results[:14]),
             "PROOF OPENINGS:\n" + "\n".join(f"- {fit(b.get('text', ''), 500, 'head')}" for b in proofs[:10])]
    return fit("\n\n".join(parts), room, "head")


def _foreign_formulas(text: str, P: dict) -> list[str]:
    """Displayed formulas in the output that are not in the paper (inline ones are too short to judge)."""
    mine = {f["norm"] for f in audit.formulas(paperlib.blocks_to_latex(P.get("blocks", [])) + "\n" + (P.get("intro") or ""))}
    return [f["tex"] for f in audit.formulas(text, inline=False) if f["norm"] not in mine][:8]


def _preamble(p: Project, P: dict) -> list[str]:
    typed = p.load("typed") or {}
    return (list(typed.get("preamble", []))
            + [f"\\providecommand{{{k}}}{{{v}}}" for k, v in {**paperlib.MACROS, **(P.get("macros") or {})}.items()])


def _authors(p: Project) -> str:
    return " \\and ".join(a.get("name", "") for a in p.state.get("meta", {}).get("authors", []) if a.get("name")) or ""


def make(p: Project, kind: str) -> dict:
    P = p.load("paper")
    if not P:
        raise ValueError("The paper is not finished yet.")
    hooks = Run(p)
    hooks.stage = "finish"
    out = p.dir / "out"
    if kind == "summary":
        res = router.chat("writer", prompts.LAY_SUMMARY.format(), _content(P), max_out=1500, effort="medium",
                          label="Write the plain-language summary", hooks=hooks)
        text = res.text.strip()
        (out / "summary.txt").write_text(text, encoding="utf-8")
        return {"kind": kind, "text": text, "files": ["summary.txt"], "foreign": []}
    if kind not in ("slides", "poster"):
        raise ValueError("Unknown kind.")
    system = prompts.SLIDES if kind == "slides" else prompts.POSTER
    res = router.chat("writer", system.format(), _content(P), max_out=6000, effort="medium",
                      label=f"Write the {kind}", hooks=hooks)
    body = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", res.text.strip())
    body = re.sub(r"\\(documentclass|usepackage|begin\{document\}|end\{document\})[^\n]*\n?", "", body)
    if check_balance(body):
        raise ValueError(f"The {kind} came back with unbalanced LaTeX ({check_balance(body)[0]}); try again.")
    pre = _preamble(p, P)
    title, authors = P.get("title", ""), _authors(p)
    plain_authors = authors.replace(" \\and ", ", ")      # outside the f-string: Python 3.11 allows no backslash there
    if kind == "slides":
        tex = "\n".join([r"\documentclass[aspectratio=169]{beamer}", r"\usetheme{default}", r"\setbeamertemplate{navigation symbols}{}",
                         r"\usepackage{amsmath,amssymb,mathtools}", *pre, f"\\title{{{title}}}", f"\\author{{{authors}}}", r"\date{}",
                         r"\begin{document}", r"\begin{frame}\titlepage\end{frame}", body, r"\end{document}", ""])
    else:
        tex = "\n".join([r"\RequirePackage{fix-cm}", r"\documentclass{article}", r"\usepackage[a0paper,landscape,margin=2.5cm]{geometry}",
                         r"\usepackage{amsmath,amssymb,mathtools,multicol,xcolor}", *pre, r"\pagestyle{empty}", r"\setlength{\columnsep}{3cm}",
                         r"\begin{document}", r"\fontsize{26}{33}\selectfont",
                         f"\\begin{{center}}{{\\fontsize{{80}}{{96}}\\selectfont\\bfseries {title}\\par}}\\vspace{{1cm}}{{\\fontsize{{40}}{{48}}\\selectfont {plain_authors}\\par}}\\end{{center}}\\vspace{{2cm}}",
                         r"\begin{multicols}{3}", body, r"\end{multicols}", r"\end{document}", ""])
    name = f"{kind}.tex"
    (out / name).write_text(tex, encoding="utf-8")
    files = [name]
    pdf_ok = None
    if paperlib.find_engine():
        r = paperlib.compile_pdf(tex)
        pdf_ok = bool(r.get("pdf"))
        if r.get("pdf"):
            (out / f"{kind}.pdf").write_bytes(r["pdf"])
            files.append(f"{kind}.pdf")
    return {"kind": kind, "files": files, "compiled": pdf_ok, "foreign": _foreign_formulas(body, P)}


# ------------------------------------------------------------------ a review pack for a co-author

_PACK_JS = r"""
<style>
.rp-bar{position:fixed;top:0;left:0;right:0;background:#21352f;color:#f3f5f1;padding:8px 14px;font:14px system-ui,sans-serif;display:flex;gap:10px;align-items:center;z-index:9}
.rp-bar input{padding:4px 6px}.rp-bar button,.rp-side button{padding:5px 10px;cursor:pointer}
body{padding-top:52px}.p-block,.p-sec{cursor:pointer}.p-block:hover,.p-sec:hover{outline:2px dashed #c99a06;outline-offset:4px}
.rp-has{box-shadow:inset 4px 0 0 #c99a06}
.rp-side{position:fixed;top:44px;right:0;bottom:0;width:min(520px,100%);background:#fff;border-left:1px solid #ccc;overflow:auto;padding:14px;font:14px system-ui,sans-serif;display:none;z-index:8}
.rp-side img{width:100%;border:1px solid #ddd}.rp-side textarea{width:100%;box-sizing:border-box;font:inherit}
.rp-c{border-top:1px solid #eee;padding:6px 0}
</style>
<div class="rp-bar"><b>Review pack</b><span id="rpCount"></span><span style="flex:1"></span>
<label>Your name <input id="rpName"></label><button id="rpSave">Download my comments</button></div>
<div class="rp-side" id="rpSide"></div>
<script>
(function(){
var KEY="rp-"+PACK.id, D={}; try{D=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
D.comments=D.comments||[]; var name=document.getElementById("rpName"); name.value=D.name||"";
function save(){D.name=name.value;try{localStorage.setItem(KEY,JSON.stringify(D))}catch(e){};mark()}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function mark(){document.querySelectorAll("[data-block]").forEach(function(el){el.classList.toggle("rp-has",D.comments.some(function(c){return c.block===el.dataset.block}))});
 document.getElementById("rpCount").textContent=D.comments.length+" comment"+(D.comments.length===1?"":"s")}
function open(el){var bid=el.dataset.block,info=PACK.blocks[bid]||{},side=document.getElementById("rpSide");side.style.display="block";
 var mine=D.comments.filter(function(c){return c.block===bid});
 side.innerHTML="<button id='rpClose'>Close</button> <b>"+esc(info.where||"")+"</b>"+
 "<p><textarea id='rpText' rows='5' placeholder='Your comment on this passage'></textarea><br><button id='rpAdd'>Add comment</button></p>"+
 mine.map(function(c,i){return "<div class='rp-c'>"+esc(c.comment)+"</div>"}).join("")+
 (info.page&&PACK.pages[info.page]?"<p><b>The author's notes, page "+info.page+"</b></p><img alt='Page "+info.page+" of the notes' src='"+PACK.pages[info.page]+"'>":"<p><i>This passage was written for the paper; it has no page of notes.</i></p>");
 document.getElementById("rpClose").onclick=function(){side.style.display="none"};
 document.getElementById("rpAdd").onclick=function(){var t=document.getElementById("rpText").value.trim();if(!t)return;
  D.comments.push({block:bid,where:info.where||"",quote:(el.innerText||"").slice(0,160),comment:t,at:new Date().toISOString()});save();open(el)}}
document.addEventListener("click",function(ev){var el=ev.target.closest("[data-block]");if(el&&!ev.target.closest(".rp-side"))open(el)});
document.getElementById("rpSave").onclick=function(){save();var blob=new Blob([JSON.stringify({pack:PACK.id,paper:PACK.title,reviewer:name.value,comments:D.comments},null,1)],{type:"application/json"});
 var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="comments-"+(name.value||"co-author").replace(/\W+/g,"-")+".json";a.click()};
name.onchange=save;mark();
})();
</script>"""


def review_pack(p: Project) -> bytes:
    """One HTML file: the paper, each passage beside the page of notes it came from, and a box for comments that
    the co-author downloads as a file and sends back (Review & proofs reads it)."""
    import base64
    import io
    import json
    import uuid
    from PIL import Image
    from . import final
    P = p.load("paper")
    if not P:
        raise ValueError("The paper is not finished yet.")
    from .writing import cited_keys
    library = {x["key"]: x for x in p.load("library", [])}
    refs = [library[k] for k in cited_keys([P.get("intro", "")] + [b.get("text", "") for b in P.get("blocks", [])]) if k in library]
    page_html = paperlib.standalone_html(P, refs)
    paperlib.number_blocks(P)
    blocks, section = {}, ""
    last_env = ""
    for b in P.get("blocks", []):
        name = ""
        if b["type"] == "section":
            section = f"Section {b.get('num', '')} ({b.get('title', '')})"
            name = section
        elif b["type"] == "env":
            last_env = f"{paperlib.THEOREM_ENVS.get(b.get('env'), str(b.get('env', '')).capitalize())} {b.get('num', '')}".strip()
            name = last_env
        elif b["type"] == "proof":
            name = f"the proof of {last_env}" if last_env else "a proof"
        blocks[b.get("id", "")] = {"where": name or section or "the paper", "page": b.get("page") or 0}
    pages = {}
    for n in sorted({v["page"] for v in blocks.values() if v["page"]}):
        f = p.dir / "pages" / f"p{int(n):03d}.jpg"
        if f.exists():
            img = Image.open(f)
            img.thumbnail((760, 1400))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=62)
            pages[n] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    pack = {"id": uuid.uuid4().hex[:12], "title": re.sub(r"[\\${}]", "", P.get("title", "")), "blocks": blocks, "pages": pages}
    script = f"<script>var PACK={json.dumps(pack)};</script>" + _PACK_JS
    return page_html.replace("</body>", script + "\n</body>").encode("utf-8")


def comments_to_report(data: bytes) -> str:
    """A co-author's comments file, as a report for Review & proofs."""
    import json
    d = json.loads(data.decode("utf-8"))
    who = d.get("reviewer") or "a co-author"
    lines = [f"Comments from {who} on \"{d.get('paper', '')}\":"]
    for i, c in enumerate(d.get("comments") or [], 1):
        lines.append(f"{i}. On {c.get('where') or 'the paper'} (the passage beginning \"{str(c.get('quote', ''))[:120]}\"): {c.get('comment', '')}")
    return "\n".join(lines)
