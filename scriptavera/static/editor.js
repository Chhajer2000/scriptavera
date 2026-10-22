/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* Scriptavera: the LaTeX editor. Plain JavaScript, no build step.
   CodeMirror 5 edits the source (one document per open file, each with its own undo history); PDF.js shows the
   PDF compiled by the LaTeX installed on this computer. Every AI change arrives as a proposal to review. */

const PID = (location.pathname.match(/^\/editor\/([\w-]+)/) || [])[1];
const API = `/api/projects/${PID}/editor`;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = { get(k, d) { try { const v = localStorage.getItem("sv-ed-" + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("sv-ed-" + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } } };

const E = {
  info: null, tree: [], docs: new Map(), current: null, openTabs: [], build: null, outline: [], complete: null,
  numbersFrom: "", proposal: null, busy: false, compiling: false, compileAgain: false, autoTimer: null,
  folded: new Set(store.get("folded-" + PID, [])), logsOpen: store.get("logs-open", true), lastSel: "",
};

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, init); } catch (e) { throw new Error("Scriptavera is not responding. Check that its window is still open."); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok || !data || data.ok === false) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.data = data; err.status = res.status;
    throw err;
  }
  return data;
}
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, bad ? 7000 : 3200);
}
function when(t) {
  const d = new Date(t * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today ${hm}`;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `yesterday ${hm}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" }) + " " + hm;
}
function mathText(t) {             /* a title with $...$: the mathematics typeset by KaTeX, the rest as text */
  return String(t || "").split(/(\$[^$]+\$)/).map((part) => {
    if (/^\$[^$]+\$$/.test(part) && window.katex) {
      try { return katex.renderToString(part.slice(1, -1), { throwOnError: false, strict: "ignore", output: "html" }); } catch (e) { /* as text */ }
    }
    return esc(part);
  }).join("");
}
const isText = (p) => /\.(tex|bib|sty|cls|bst|bbx|cbx|cfg|def|clo|ltx|txt|md|csv|dat|tikz)$/i.test(p);

/* ------------------------------------------------------------- LaTeX mode */
const MATH_ENVS = /^(equation|align|gather|multline|flalign|alignat|eqnarray|displaymath|math)\*?$/;
CodeMirror.defineMode("latex", function () {
  return {
    startState: () => ({ math: null, argOf: null, arg: null, depth: 0 }),
    copyState: (s) => ({ ...s }),
    lineComment: "%",
    token(stream, st) {
      if (st.math) {
        if (stream.match("%")) { stream.skipToEnd(); return "comment"; }
        if (st.math === "$" && stream.match("$")) { st.math = null; return "math-delim"; }
        if (st.math === "$$" && stream.match("$$")) { st.math = null; return "math-delim"; }
        if (st.math === "\\]" && stream.match("\\]")) { st.math = null; return "math-delim"; }
        if (st.math === "\\)" && stream.match("\\)")) { st.math = null; return "math-delim"; }
        if (st.math.startsWith("env:")) {
          const m = stream.match(/^\\end\{([A-Za-z*]+)\}/, false);
          if (m && m[1] === st.math.slice(4)) { stream.match(/^\\end\{[A-Za-z*]+\}/); st.math = null; return "tag"; }
        }
        if (stream.match(/^\\[A-Za-z@]+/)) return "math-cmd";
        if (stream.match(/^\\./)) return "math";
        stream.next();
        return "math";
      }
      if (stream.match("%")) { stream.skipToEnd(); return "comment"; }
      let m = stream.match(/^\\(begin|end)\{([A-Za-z*@]+)\}/);
      if (m) { if (m[1] === "begin" && MATH_ENVS.test(m[2])) st.math = "env:" + m[2]; return "tag"; }
      if (stream.match(/^\\(?:label|ref|eqref|cref|Cref|autoref|pageref|nameref|cite[tpa]?|input|include|includegraphics|bibliography|bibitem|usepackage|documentclass)\b/)) { st.argOf = "ref"; return "keyword"; }
      if (stream.match(/^\\(?:part|chapter|section|subsection|subsubsection|paragraph|title)\*?/)) { st.argOf = "head"; return "keyword"; }
      if (stream.match("$$")) { st.math = "$$"; return "math-delim"; }
      if (stream.match("$")) { st.math = "$"; return "math-delim"; }
      if (stream.match("\\[")) { st.math = "\\]"; return "math-delim"; }
      if (stream.match("\\(")) { st.math = "\\)"; return "math-delim"; }
      if (stream.match(/^\\[A-Za-z@]+\*?/)) { st.argOf = null; return "keyword"; }
      if (stream.match(/^\\./)) return "keyword";
      const c = stream.peek();
      if (c === "[" && st.argOf) { stream.next(); while (!stream.eol() && stream.peek() !== "]") stream.next(); stream.eat("]"); return "string"; }
      if (c === "{") { stream.next(); if (st.argOf) { st.arg = st.argOf; st.argOf = null; st.depth = 0; } else if (st.arg) st.depth++; return "bracket"; }
      if (c === "}") { stream.next(); if (st.arg) { if (st.depth > 0) st.depth--; else st.arg = null; } return "bracket"; }
      if (st.arg) { stream.eatWhile(/[^{}%\\$]/); return st.arg === "ref" ? "atom" : "header"; }
      st.argOf = null;
      if (!stream.eatWhile(/[^\\%${}[\]]/)) stream.next();
      return null;
    },
  };
});

/* ------------------------------------------------------------------ editor */
let cm = null;
function makeEditor() {
  cm = CodeMirror($("#cmHost"), {
    value: "", mode: "latex", lineNumbers: true, lineWrapping: true, indentUnit: 2, tabSize: 2, indentWithTabs: false,
    gutters: ["ed-marks", "CodeMirror-linenumbers"], spellcheck: false, autofocus: false,
    extraKeys: {
      "Ctrl-S": () => saveAndCompile(), "Cmd-S": () => saveAndCompile(), "Ctrl-Enter": () => saveAndCompile(), "Cmd-Enter": () => saveAndCompile(),
      "Ctrl-F": () => openFind(false), "Cmd-F": () => openFind(false), "Ctrl-H": () => openFind(true), "Cmd-Alt-F": () => openFind(true),
      "Ctrl-/": () => toggleComment(), "Cmd-/": () => toggleComment(), "Ctrl-K": () => $("#askText").focus(), "Cmd-K": () => $("#askText").focus(),
      "Tab": (c) => { if (E.complete) return pickComplete(); if (c.somethingSelected()) c.indentSelection("add"); else c.replaceSelection("  "); },
      "Shift-Tab": (c) => c.indentSelection("subtract"),
      "Esc": () => { if (E.complete) closeComplete(); else if (!$("#find").hidden) closeFind(); },
    },
  });
  cm.on("inputRead", (c, ch) => maybeComplete(ch));
  cm.on("cursorActivity", () => { if (E.complete) maybeComplete(); markActiveLine(); });
  cm.on("blur", () => setTimeout(() => { if (!document.activeElement || !$("#complete").contains(document.activeElement)) closeComplete(); }, 150));
  cm.on("gutterClick", (c, line, gutter) => { if (gutter === "ed-marks") showLineIssues(line + 1); });
}
let activeLine = null;
function markActiveLine() {
  if (activeLine !== null) cm.removeLineClass(activeLine, "background", "CodeMirror-activeline-background");
  activeLine = cm.getCursor().line;
  cm.addLineClass(activeLine, "background", "CodeMirror-activeline-background");
}

/* ---------------------------------------------------------------- files */
async function loadInfo() {
  let data;
  try { data = await api(API); }
  catch (e) { return fatal(e.message); }
  if (!data.exists) {
    if (!data.finished) return fatal("The editor opens once the paper is written. Go back to Scriptavera and finish the steps first.");
    try { data = await api(API, { method: "POST", body: {} }); }
    catch (e) { return fatal(e.message); }
  }
  E.info = data;
  E.tree = data.tree;
  E.build = data.build;
  document.title = `${data.name} – LaTeX editor – Scriptavera`;
  $("#name").innerHTML = `<b>${mathText(data.title || data.name)}</b>`;
  $("#back").href = `/#p/${PID}/assemble`;
  banner();
  return true;
}
function fatal(msg) {
  $("#cmHost").hidden = true;
  const el = $("#empty"); el.hidden = false;
  el.innerHTML = `<div><p>${esc(msg)}</p><p><a href="/#p/${PID}">Back to Scriptavera</a></p></div>`;
  return false;
}
function banner() {
  const b = $("#banner");
  const msgs = [];
  if (E.info && !E.info.engine) {
    msgs.push(`<span>No LaTeX is installed on this computer, so the PDF cannot be made here. Install <a href="https://miktex.org/download" target="_blank" rel="noopener">MiKTeX</a>, then restart Scriptavera; or download the project and upload it to Overleaf. Editing and the AI work without it.</span>`);
  }
  if (E.info && E.info.source && E.info.source.changed && store.get("dismissed-" + PID, 0) < (E.info.source.built || 1)) {
    msgs.push(`<span>Scriptavera rebuilt the paper after this copy was made (for example, you changed the title in the app). Your files here are unchanged.</span>
      <button class="btn small" data-act="reset">Start again from the new version</button><button class="btn small quiet" data-act="dismiss">Keep editing this copy</button>`);
  }
  b.innerHTML = msgs.join("");
  b.hidden = !msgs.length;
  b.classList.toggle("bad", !!(E.info && !E.info.engine));
}
async function refreshTree() {
  const d = await api(API);
  E.info = d; E.tree = d.tree; drawTree();
}
function drawTree() {
  const rows = [];
  const dirs = E.tree.filter((x) => x.kind === "dir").map((x) => x.path);
  const hidden = (p) => dirs.some((d) => E.folded.has(d) && p.startsWith(d + "/"));
  const depth = (p) => p.split("/").length - 1;
  const items = [...E.tree].sort((a, b) => {
    const pa = a.path.split("/"), pb = b.path.split("/");
    for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
      if (pa[i] !== pb[i]) {
        const aDir = i < pa.length - 1 || a.kind === "dir", bDir = i < pb.length - 1 || b.kind === "dir";
        if (pa[i] === "main.tex" && i === 0) return -1;
        if (pb[i] === "main.tex" && i === 0) return 1;
        if (aDir !== bDir) return aDir ? -1 : 1;
        return pa[i].localeCompare(pb[i]);
      }
    }
    return pa.length - pb.length;
  });
  for (const it of items) {
    if (hidden(it.path)) continue;
    const name = it.path.split("/").pop();
    const pad = 8 + depth(it.path) * 14;
    const dirty = E.docs.get(it.path) && E.docs.get(it.path).dirty;
    if (it.kind === "dir") {
      const shut = E.folded.has(it.path);
      rows.push(`<div class="row dir" role="treeitem" aria-expanded="${!shut}" tabindex="0" data-act="fold" data-path="${esc(it.path)}" style="padding-left:${pad}px">
        <span class="ic">${shut ? "▸" : "▾"}</span><span class="nm">${esc(name)}</span>
        <span class="acts"><span data-act="rename" data-path="${esc(it.path)}" title="Rename">rename</span><span data-act="delete" data-path="${esc(it.path)}" title="Delete">delete</span></span></div>`);
    } else {
      rows.push(`<div class="row ${E.current === it.path ? "on" : ""}" role="treeitem" tabindex="0" data-act="open" data-path="${esc(it.path)}" style="padding-left:${pad}px">
        <span class="ic">${it.kind === "binary" ? "▫" : "·"}</span><span class="nm">${esc(name)}</span>${dirty ? '<span class="dot" title="Not saved yet"></span>' : ""}
        <span class="acts">${it.path === "main.tex" ? "" : `<span data-act="rename" data-path="${esc(it.path)}" title="Rename">rename</span><span data-act="delete" data-path="${esc(it.path)}" title="Delete">delete</span>`}</span></div>`);
    }
  }
  $("#tree").innerHTML = rows.join("") || `<div class="empty-o">No files.</div>`;
}

async function openFile(path, line, opts = {}) {
  if (!path) return;
  const node = E.tree.find((x) => x.path === path);
  if (node && node.kind === "binary") return previewBinary(path);
  let d = E.docs.get(path);
  if (!d) {
    try {
      const f = await api(`${API}/file?path=${encodeURIComponent(path)}`);
      d = { doc: CodeMirror.Doc(f.text, "latex"), sha: f.sha, saved: f.text, dirty: false, saving: false, timer: null };
      d.doc.on("change", () => onChange(path));
      E.docs.set(path, d);
    } catch (e) { toast(e.message, true); return; }
  }
  if (!E.openTabs.includes(path)) E.openTabs.push(path);
  E.current = path;
  cm.swapDoc(d.doc);
  $("#cmHost").hidden = false; $("#empty").hidden = true;
  store.set("open-" + PID, { tabs: E.openTabs, current: path });
  drawTabs(); drawTree(); lintNow(path); markIssues();
  if (line) {
    const ln = Math.max(0, Math.min(d.doc.lineCount() - 1, line - 1));
    cm.setCursor({ line: ln, ch: 0 });
    cm.scrollIntoView({ line: ln, ch: 0 }, cm.getScrollInfo().clientHeight / 3);
    if (opts.flash !== false) flashLine(ln);
  }
  if (opts.focus !== false) cm.focus();
  if (window.matchMedia("(max-width: 860px)").matches && opts.show !== false) setView("code");
}
function flashLine(ln) {
  const h = cm.addLineClass(ln, "background", "line-flash");
  setTimeout(() => cm.removeLineClass(h, "background", "line-flash"), 1600);
}
function previewBinary(path) {
  const url = `${API}/file?path=${encodeURIComponent(path)}&raw=1`;
  if (/\.(png|jpe?g)$/i.test(path)) {
    modal(`<header><h2>${esc(path)}</h2><button class="btn quiet" data-act="close">Close</button></header>
      <div class="body"><img src="${url}" alt="${esc(path)}" style="max-width:100%;height:auto;border:1px solid var(--rule)">
      <p style="margin-top:10px">Include it with <code>\\includegraphics[width=0.8\\textwidth]{${esc(path)}}</code> (the preamble needs <code>\\usepackage{graphicx}</code>).</p></div>`);
  } else window.open(url, "_blank", "noopener");
}
function drawTabs() {
  $("#tabs").innerHTML = E.openTabs.map((p) => {
    const d = E.docs.get(p);
    return `<button class="tab ${p === E.current ? "on" : ""}" role="tab" aria-selected="${p === E.current}" data-act="tab" data-path="${esc(p)}" title="${esc(p)}">
      ${d && d.dirty ? '<span class="dirty" title="Not saved yet"></span>' : ""}${esc(p.split("/").pop())}<span class="x" data-act="closetab" data-path="${esc(p)}" aria-label="Close">×</span></button>`;
  }).join("");
}
function closeTab(path) {
  const d = E.docs.get(path);
  if (d && d.dirty) save(path);
  E.openTabs = E.openTabs.filter((p) => p !== path);
  if (E.current === path) {
    const next = E.openTabs[E.openTabs.length - 1];
    if (next) openFile(next, null, { focus: false });
    else { E.current = null; cm.swapDoc(CodeMirror.Doc("", "latex")); $("#cmHost").hidden = true; const el = $("#empty"); el.hidden = false; el.textContent = "Open a file from the list on the left."; drawTabs(); drawTree(); }
  } else drawTabs();
  store.set("open-" + PID, { tabs: E.openTabs, current: E.current });
}


/* ------------------------------------------------- the check as you type */
/* Like Overleaf's code check: mistakes TeX would stop at, found in the browser while typing, before compiling.
   (Compiling then gives TeX's own messages, with their file and line, in the logs.) */
const OPERATOR_TRAP = /\\providecommand\{\\([A-Za-z]+)\}\{\\operatorname\{\1\}\}/;
/* commands that come from a package: declared as an operator name, they print as a word instead */
const PACKAGE_OF = { cancel: "cancel", bcancel: "cancel", xcancel: "cancel", cancelto: "cancel", mathscr: "mathrsfs", bm: "bm",
  mathbbm: "bbm", mathds: "dsfont", llbracket: "stmaryrd", rrbracket: "stmaryrd", sout: "ulem", nicefrac: "nicefrac" };
function lintTex(text) {
  const out = [];
  const lines = text.split("\n");
  const braces = [];                        // [line] of each open {
  const envs = [];                          // [{name, line}]
  let inlineOpen = 0, displayOpen = 0, parenOpen = 0, dollarLine = 0, displayLine = 0, parenLine = 0, leftRight = 0, lrLine = 0;
  for (let n = 0; n < lines.length; n++) {
    const raw = lines[n];
    const ln = n + 1;
    if (/^\s*$/.test(raw) && inlineOpen) { out.push({ line: dollarLine, level: "error", message: "A $ opened here is not closed before the paragraph ends." }); inlineOpen = 0; }
    const trap = raw.match(OPERATOR_TRAP);
    if (trap && PACKAGE_OF[trap[1]]) out.push({ line: ln, level: "error", message: `This line makes \\${trap[1]} print the word “${trap[1]}”. Delete it and load the package instead: \\usepackage{${PACKAGE_OF[trap[1]]}}.` });
    let i = 0;
    while (i < raw.length) {
      const c = raw[i];
      if (c === "%") break;
      if (c === "\\") {
        const m = raw.slice(i).match(/^\\([A-Za-z]+|.)/);
        const name = m ? m[1] : "";
        if (name === "[") { if (displayOpen) out.push({ line: ln, level: "error", message: "\\[ inside a displayed formula that is already open (line " + displayLine + ")." }); displayOpen = 1; displayLine = ln; }
        else if (name === "]") { if (!displayOpen) out.push({ line: ln, level: "error", message: "\\] closes a displayed formula that was never opened." }); displayOpen = 0; }
        else if (name === "(") { parenOpen = 1; parenLine = ln; }
        else if (name === ")") { if (!parenOpen) out.push({ line: ln, level: "error", message: "\\) without an opening \\(." }); parenOpen = 0; }
        else if (name === "left") { leftRight++; lrLine = ln; }
        else if (name === "right") { leftRight--; if (leftRight < 0) { out.push({ line: ln, level: "error", message: "\\right without a matching \\left." }); leftRight = 0; } }
        else if (name === "begin" || name === "end") {
          const g = raw.slice(i + m[0].length).match(/^\s*\{([^{}]*)\}/);
          if (g) {
            const env = g[1].trim();
            if (name === "begin") envs.push({ name: env, line: ln });
            else {
              const top = envs.pop();
              if (!top) { if (env !== "document") out.push({ line: ln, level: "error", message: `\\end{${env}} without a \\begin{${env}}.` }); }
              else if (top.name !== env) { out.push({ line: ln, level: "error", message: `\\end{${env}} closes \\begin{${top.name}} from line ${top.line}.` }); }
            }
            i += m[0].length + g[0].length;
            continue;
          }
        }
        i += m ? m[0].length : 1;
        continue;
      }
      if (c === "{") braces.push(ln);
      else if (c === "}") { if (!braces.length) out.push({ line: ln, level: "error", message: "This } closes a brace that was never opened." }); else braces.pop(); }
      else if (c === "$") {
        if (raw[i + 1] === "$") { displayOpen = displayOpen ? 0 : 1; displayLine = ln; i += 2; continue; }
        inlineOpen = inlineOpen ? 0 : 1; dollarLine = ln;
      }
      i++;
    }
    if (!displayOpen && !inlineOpen && !parenOpen && !envs.some((e) => /^(equation|align|gather|multline|eqnarray|flalign|alignat|split|aligned)\*?$/.test(e.name))) {
      if (leftRight > 0) out.push({ line: lrLine, level: "warning", message: "A \\left here has no matching \\right in its formula." });
      leftRight = 0;
    }
  }
  for (const l of braces.slice(0, 3)) out.push({ line: l, level: "error", message: "A { opened here is never closed." });
  for (const e of envs) if (e.name !== "document") out.push({ line: e.line, level: "error", message: `\\begin{${e.name}} is never closed with \\end{${e.name}}.` });
  if (inlineOpen) out.push({ line: dollarLine, level: "error", message: "A $ opened here is never closed." });
  if (displayOpen) out.push({ line: displayLine, level: "error", message: "A displayed formula opened here is never closed." });
  if (parenOpen) out.push({ line: parenLine, level: "error", message: "\\( opened here is never closed." });
  return out;
}
const LINT = {};                            // path -> issues found as you type
let lintTimer = null;
function lintNow(path) {
  const d = E.docs.get(path);
  if (!d || !/\.tex$/i.test(path)) { delete LINT[path]; return; }
  const text = d.doc.getValue();
  const whole = /\\begin\s*\{document\}/.test(text);     // a section file: \begin{document} is in main.tex
  LINT[path] = lintTex(text).filter((x) => whole || !/document/.test(x.message));
  if (path === E.current) { markIssues(); drawLogs(); }
}
function lintSoon(path) { clearTimeout(lintTimer); lintTimer = setTimeout(() => lintNow(path), 350); }

/* ----------------------------------------------------------- saving */
function onChange(path) {
  const d = E.docs.get(path);
  if (!d) return;
  const was = d.dirty;
  d.dirty = d.doc.getValue() !== d.saved;
  if (was !== d.dirty) { drawTabs(); drawTree(); }
  clearTimeout(d.timer);
  d.timer = setTimeout(() => save(path), 900);
  setStatus("Editing…");
  clearTimeout(E.autoTimer);
  lintSoon(path);
}
async function save(path) {
  const d = E.docs.get(path);
  if (!d || !d.dirty || d.saving) return true;
  d.saving = true;
  const text = d.doc.getValue();
  try {
    const r = await api(`${API}/file`, { method: "PUT", body: { path, text, base: d.sha } });
    d.sha = r.sha; d.saved = text; d.dirty = d.doc.getValue() !== text;
    drawTabs(); drawTree();
    if (!d.dirty) { setStatus("Saved"); scheduleAuto(); }
    return true;
  } catch (e) {
    if (e.status === 409) {
      modal(`<header><h2>This file changed elsewhere</h2></header><div class="body"><p>${esc(path)} was changed after you opened it here (in another window, or by an accepted AI change). Your typing here was not saved.</p>
        <div class="btnrow" style="display:flex;gap:8px"><button class="btn primary" data-act="reloadfile" data-path="${esc(path)}">Load the latest version</button>
        <button class="btn" data-act="forcesave" data-path="${esc(path)}">Keep mine and overwrite</button></div></div>`);
    } else toast(`Not saved: ${e.message}`, true);
    return false;
  } finally { d.saving = false; if (d.dirty) { clearTimeout(d.timer); d.timer = setTimeout(() => save(path), 1500); } }
}
async function saveAll() {
  const ok = await Promise.all([...E.docs.keys()].map((p) => save(p)));
  return ok.every(Boolean);
}
async function reloadFile(path, text, sha) {
  const d = E.docs.get(path);
  if (!d) return;
  if (text === undefined) {
    const f = await api(`${API}/file?path=${encodeURIComponent(path)}`);
    text = f.text; sha = f.sha;
  }
  const cur = d.doc.getCursor();
  d.saved = text; d.sha = sha;
  if (d.doc.getValue() !== text) d.doc.replaceRange(text, { line: 0, ch: 0 }, { line: d.doc.lastLine(), ch: d.doc.getLine(d.doc.lastLine()).length }, "+reload");
  d.dirty = false;
  d.doc.setCursor(cur);
  drawTabs(); drawTree();
}
function scheduleAuto() {
  clearTimeout(E.autoTimer);
  if (!$("#autoCompile").checked || !E.info || !E.info.engine) return;
  E.autoTimer = setTimeout(() => compile(), 1600);
}
async function saveAndCompile() { await saveAll(); compile(); return false; }

/* ---------------------------------------------------------------- compile */
function setStatus(text, cls) { const s = $("#status"); s.textContent = text; s.className = "ed-status" + (cls ? " " + cls : ""); }
async function compile() {
  if (!E.info || !E.info.engine) { setStatus("No LaTeX here", "bad"); return; }
  if (E.compiling) { E.compileAgain = true; return; }
  clearTimeout(E.autoTimer);
  await saveAll();
  E.compiling = true;
  $("#compileBtn").disabled = true; $("#compileBtn").textContent = "Compiling…";
  setStatus("Compiling…");
  $("#pages").classList.add("stale");
  try {
    const r = await api(`${API}/compile`, { method: "POST", body: {} });
    E.build = r.build;
    showBuild();
    if (r.build.has_pdf) await loadPdf(r.build.build);
    loadOutline();
  } catch (e) { setStatus("Compile failed", "bad"); toast(e.message, true); }
  finally {
    E.compiling = false; $("#compileBtn").disabled = false; $("#compileBtn").textContent = "Recompile";
    $("#pages").classList.remove("stale");
    if (E.compileAgain) { E.compileAgain = false; compile(); }
  }
}
function showBuild() {
  const b = E.build;
  if (!b) { setStatus(E.info && E.info.engine ? "Not compiled yet" : "No LaTeX here", E.info && E.info.engine ? "" : "bad"); drawLogs(); return; }
  const errs = (b.errors || []).length;
  if (errs) setStatus(`${errs} error${errs === 1 ? "" : "s"}${b.has_pdf ? ", PDF made anyway" : ""}`, "bad");
  else if (b.has_pdf) setStatus(`Compiled in ${b.seconds} s${b.pages ? `, ${b.pages} pages` : ""}`, "good");
  else setStatus("No PDF", "bad");
  drawLogs(); markIssues();
}
function issuesFor(path) {
  const b = E.build || {};
  return [...(b.errors || []).map((x) => ({ ...x, level: "error" })), ...(b.warnings || []).map((x) => ({ ...x, level: x.kind || "warning" }))]
    .filter((x) => x.path === path && x.line > 0)
    .concat((LINT[path] || []).map((x) => ({ ...x, path, message: "Code check: " + x.message })));
}
let marked = [];
function markIssues() {
  if (!cm) return;
  for (const h of marked) cm.removeLineClass(h, "background", "line-err");
  marked = [];
  cm.clearGutter("ed-marks");
  if (!E.current) return;
  const by = {};
  for (const x of issuesFor(E.current)) { if (x.level === "box") continue; (by[x.line] = by[x.line] || []).push(x); }
  for (const [ln, xs] of Object.entries(by)) {
    const line = Number(ln) - 1;
    if (line >= cm.lineCount()) continue;
    const el = document.createElement("span");
    const bad = xs.some((x) => x.level === "error");
    el.className = bad ? "mark-err" : "mark-warn";
    el.title = xs.map((x) => x.message).join("\n");
    cm.setGutterMarker(line, "ed-marks", el);
    if (bad) marked.push(cm.addLineClass(line, "background", "line-err"));
  }
}
function showLineIssues(line) {
  const xs = issuesFor(E.current).filter((x) => x.line === line);
  if (xs.length) toast(xs.map((x) => x.message).join(" · "), xs.some((x) => x.level === "error"));
}
function drawLogs() {
  const b = E.build || {};
  const errs = b.errors || [], warns = (b.warnings || []).filter((w) => w.kind !== "box"), boxes = (b.warnings || []).filter((w) => w.kind === "box");
  const where = (x) => x.path ? `<span class="where" data-act="goto" data-path="${esc(x.path)}" data-line="${x.line}">${esc(x.path)}${x.line ? ":" + x.line : ""}</span>` : `<span></span>`;
  const row = (x, cls) => `<div class="log ${cls}">${where(x)}${cls === "error" && x.path && x.inside !== false ? `<button class="btn small fix" data-act="fixerror" data-i="${errs.indexOf(x)}">Ask the AI to fix it</button>` : "<span></span>"}
    <div class="msg">${esc(x.message)}</div>${x.context ? `<pre>${esc(x.context)}</pre>` : ""}</div>`;
  const something = errs.length || warns.length;
  $("#logs").className = "logs" + ((E.logsOpen && something) || E.logsForced ? "" : " closed");
  $("#logs").innerHTML = `<div class="logs-head" data-act="logs"><span class="t">Logs and messages</span>
      <span class="c ${errs.length ? "err" : ""}">${errs.length} error${errs.length === 1 ? "" : "s"}</span>
      <span class="c ${warns.length ? "warn" : ""}">${warns.length} warning${warns.length === 1 ? "" : "s"}</span>
      ${(LINT[E.current] || []).length ? `<span class="c err" title="Found while you type, before compiling">${LINT[E.current].length} in code check</span>` : ""}
      <a href="${API}/log" target="_blank" rel="noopener" data-act="none">Raw log</a></div>
    <div class="logs-list">${errs.map((x) => row(x, "error")).join("")}${warns.map((x) => row(x, x.kind || "warning")).join("")}
      ${boxes.length ? `<div class="log box"><span>${boxes.length} overfull line${boxes.length === 1 ? "" : "s"} (text sticking into the margin)</span><span></span>
        <div class="msg">${boxes.slice(0, 8).map((x) => `${where(x)} ${esc(x.message)}`).join("<br>")}</div></div>` : ""}
      ${!errs.length && !warns.length && b.has_pdf ? `<div class="log"><span>No errors and no warnings.</span><span></span></div>` : ""}</div>`;
}

/* ---------------------------------------------------------------- PDF */
const PDF = { lib: null, doc: null, scale: 1, mode: store.get("zoom", "fit"), pages: [], build: 0, observer: null, failed: false };
async function pdfLib() {
  if (PDF.lib || PDF.failed) return PDF.lib;
  try {
    PDF.lib = await import("/static/vendor/pdfjs/pdf.min.mjs");
    PDF.lib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
  } catch (e) { PDF.failed = true; }
  return PDF.lib;
}
function pdfUrl(build) { return `${API}/pdf?b=${build || 0}`; }
async function loadPdf(build) {
  $("#openPdf").href = pdfUrl(build);
  const lib = await pdfLib();
  const host = $("#pages");
  if (!lib) {             // the browser could not load PDF.js: show the browser's own viewer instead
    host.innerHTML = `<iframe title="PDF" src="${pdfUrl(build)}" style="width:100%;height:100%;border:0"></iframe>`;
    return;
  }
  const keep = pdfPosition();
  let doc;
  const task = lib.getDocument({ url: pdfUrl(build), isEvalSupported: false, enableXfa: false });
  try { doc = await task.promise; }
  catch (e) { toast("The PDF could not be shown: " + e.message, true); return; }
  const old = PDF.task;
  PDF.doc = doc; PDF.task = task; PDF.build = build;
  const sizes = [];
  for (let i = 1; i <= doc.numPages; i++) { const p = await doc.getPage(i); const v = p.getViewport({ scale: 1 }); sizes.push([v.width, v.height]); }
  PDF.sizes = sizes;
  PDF.scale = zoomScale();
  if (PDF.observer) PDF.observer.disconnect();
  const frag = document.createDocumentFragment();
  PDF.pages = sizes.map(([w, h], i) => {
    const div = document.createElement("div");
    div.className = "pg"; div.dataset.page = i + 1;
    div.style.width = `${Math.floor(w * PDF.scale)}px`; div.style.height = `${Math.floor(h * PDF.scale)}px`;
    const c = document.createElement("canvas");
    div.appendChild(c);
    frag.appendChild(div);
    return { div, canvas: c, rendered: 0, task: null };
  });
  host.innerHTML = ""; host.appendChild(frag);
  PDF.observer = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) renderPage(Number(en.target.dataset.page) - 1);
  }, { root: host, rootMargin: "600px 0px" });
  PDF.pages.forEach((p) => PDF.observer.observe(p.div));
  restorePdf(keep);
  updatePageNo();
  if (old) setTimeout(() => { try { old.destroy(); } catch (e) { /* already gone */ } }, 0);
}
function zoomScale() {
  if (PDF.mode === "fit" && PDF.sizes && PDF.sizes.length) {
    const w = $("#pages").clientWidth - 34;
    return Math.max(0.3, Math.min(4, w / PDF.sizes[0][0]));
  }
  return Number(PDF.mode) || 1;
}
async function renderPage(i) {
  const pg = PDF.pages[i];
  if (!pg || pg.rendered === PDF.scale || !PDF.doc) return;
  const scale = PDF.scale, doc = PDF.doc;
  pg.rendered = scale;
  try {
    const page = await doc.getPage(i + 1);
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const vp = page.getViewport({ scale: scale * dpr });
    const c = pg.canvas;
    c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
    c.style.width = `${Math.floor(vp.width / dpr)}px`; c.style.height = `${Math.floor(vp.height / dpr)}px`;
    if (pg.task) { try { pg.task.cancel(); } catch (e) { /* finished */ } }
    pg.task = page.render({ canvasContext: c.getContext("2d"), viewport: vp });
    await pg.task.promise;
  } catch (e) { if (!/cancel/i.test(String(e && e.name))) pg.rendered = 0; }
}
function pdfPosition() {
  const host = $("#pages");
  if (!PDF.pages.length) return null;
  const top = host.scrollTop;
  for (let i = 0; i < PDF.pages.length; i++) {
    const d = PDF.pages[i].div;
    if (d.offsetTop + d.offsetHeight > top) return { page: i, frac: (top - d.offsetTop) / d.offsetHeight, left: host.scrollLeft };
  }
  return null;
}
function restorePdf(pos) {
  if (!pos) return;
  const pg = PDF.pages[Math.min(pos.page, PDF.pages.length - 1)];
  if (!pg) return;
  const host = $("#pages");
  host.scrollTop = pg.div.offsetTop + pos.frac * pg.div.offsetHeight;
  host.scrollLeft = pos.left || 0;
}
function setZoom(mode) {
  const pos = pdfPosition();
  PDF.mode = mode; store.set("zoom", mode);
  $("#zoom").value = ["fit", "0.75", "1", "1.25", "1.5", "2"].includes(String(mode)) ? String(mode) : "1";
  if (!PDF.sizes) return;
  PDF.scale = zoomScale();
  PDF.pages.forEach((p, i) => {
    p.div.style.width = `${Math.floor(PDF.sizes[i][0] * PDF.scale)}px`; p.div.style.height = `${Math.floor(PDF.sizes[i][1] * PDF.scale)}px`;
    p.rendered = 0;
  });
  restorePdf(pos);
  PDF.pages.forEach((p, i) => { const r = p.div.getBoundingClientRect(), h = $("#pages").getBoundingClientRect(); if (r.bottom > h.top - 600 && r.top < h.bottom + 600) renderPage(i); });
}
function stepZoom(dir) {
  const steps = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  const cur = PDF.scale;
  const next = dir > 0 ? steps.find((s) => s > cur + 0.01) || 3 : [...steps].reverse().find((s) => s < cur - 0.01) || 0.5;
  setZoom(String(next));
}
function updatePageNo() {
  const pos = pdfPosition();
  $("#pageNo").textContent = PDF.pages.length ? `page ${pos ? pos.page + 1 : 1} of ${PDF.pages.length}` : "";
}

/* ----------------------------------------------------------- SyncTeX */
async function toPdf() {
  if (!E.current) return;
  const line = cm.getCursor().line + 1;
  try {
    const r = await api(`${API}/sync`, { method: "POST", body: { direction: "forward", path: E.current, line } });
    showInPdf(r);
  } catch (e) { toast(e.message, true); }
}
function showInPdf(r) {
  const pg = PDF.pages[r.page - 1];
  if (!pg) return;
  if (window.matchMedia("(max-width: 860px)").matches) setView("pdf");
  const host = $("#pages");
  const y = r.y * PDF.scale;
  host.scrollTo({ top: pg.div.offsetTop + y - host.clientHeight / 3, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $$(".sync", host).forEach((x) => x.remove());
  const box = document.createElement("div");
  box.className = "sync";
  box.style.left = `${r.x * PDF.scale - 3}px`; box.style.top = `${r.y * PDF.scale - 2}px`;
  box.style.width = `${r.w * PDF.scale + 6}px`; box.style.height = `${r.h * PDF.scale + 4}px`;
  pg.div.appendChild(box);
  requestAnimationFrame(() => requestAnimationFrame(() => box.classList.add("gone")));
  setTimeout(() => box.remove(), 4000);
  E.lastPdfPoint = { page: r.page, x: r.x + 4, y: r.y + r.h / 2 };
}
async function toCode(page, x, y) {
  const pt = page ? { page, x, y } : E.lastPdfPoint;
  if (!pt) { toast("Double-click a place in the PDF to see its source."); return; }
  try {
    const r = await api(`${API}/sync`, { method: "POST", body: { direction: "inverse", ...pt } });
    if (!r.path) { toast("That part of the PDF comes from outside your files (the class or a package)."); return; }
    await openFile(r.path, r.line);
  } catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------------- outline */
async function loadOutline() {
  try {
    const r = await api(`${API}/outline`);
    E.outline = r.items; E.numbersFrom = r.numbers_from; E.comp = r.complete;
    drawOutline();
  } catch (e) { /* the outline is a help, not a necessity */ }
}
const ABBR = { definition: "Def.", lemma: "Lem.", theorem: "Thm.", proposition: "Prop.", corollary: "Cor.", remark: "Rem.", example: "Ex.",
  conjecture: "Conj.", notation: "Not.", observation: "Obs.", assumption: "Ass.", hypothesis: "Hyp.", figure: "Fig.", table: "Tab.", claim: "Claim",
  fact: "Fact", problem: "Prob.", question: "Qu.", convention: "Conv.", property: "Prop.", axiom: "Ax.", construction: "Constr.", exercise: "Exer.", note: "Note" };
function abbrev(name) { const n = String(name).toLowerCase(); return ABBR[n] || (n.length > 5 ? name.slice(0, 4) + "." : name); }
function plain(t) { return String(t).replace(/\$([^$]*)\$/g, "$1").replace(/\\(?:[A-Za-z]+)\s*/g, "").replace(/[{}]/g, ""); }
function drawOutline() {
  const items = E.outline.filter((x) => ["section", "subsection", "subsubsection", "env", "figure"].includes(x.kind));
  $("#outlineNote").textContent = E.numbersFrom === "counted" && items.length ? "numbers counted" : "";
  $("#outlineNote").title = E.numbersFrom === "counted" ? "The files changed since the last compile: numbers are counted until you compile again." : "";
  $("#outline").innerHTML = items.map((x) => {
    const sec = x.kind === "section", sub = x.kind === "subsection" || x.kind === "subsubsection";
    const label = sec || sub ? (x.number || "") : `${esc(abbrev(x.name || x.env || ""))} ${esc(x.number || "")}`;
    const text = sec || sub ? mathText(x.title) : (x.title ? mathText(x.title) : `<em>${esc(plain(x.snippet || "").slice(0, 70))}</em>`);
    return `<button class="o ${sec ? "sec" : sub ? "sub" : ""}" data-act="goto" data-path="${esc(x.path)}" data-line="${x.line}" title="${esc((x.name || x.kind) + " " + (x.number || ""))}${x.label ? " (" + esc(x.label) + ")" : ""}">
      <span class="n">${label}</span><span class="t">${text}</span></button>`;
  }).join("") || `<div class="empty-o">Sections and statements appear here.</div>`;
}

/* ---------------------------------------------------------------- search */
const F = { marks: [], hits: [], now: -1 };
function openFind(replace) {
  const f = $("#find"); f.hidden = false;
  const sel = cm.getSelection();
  if (sel && !sel.includes("\n")) $("#findText").value = sel;
  $("#replaceText").hidden = !replace && !$("#replaceText").value;
  $$("#find [data-act=replace], #find [data-act=replaceall]").forEach((b) => { b.hidden = $("#replaceText").hidden; });
  $(replace && $("#findText").value ? "#replaceText" : "#findText").focus();
  $("#findText").select();
  runFind();
}
function closeFind() { $("#find").hidden = true; clearFind(); cm.focus(); }
function clearFind() { F.marks.forEach((m) => m.clear()); F.marks = []; F.hits = []; F.now = -1; $("#findCount").textContent = ""; }
function runFind(keepNow) {
  clearFind();
  const q = $("#findText").value;
  if (!q) return;
  const text = cm.getValue(), cs = $("#findCase").checked;
  const hay = cs ? text : text.toLowerCase(), needle = cs ? q : q.toLowerCase();
  let i = 0;
  while ((i = hay.indexOf(needle, i)) !== -1 && F.hits.length < 5000) { F.hits.push([i, i + q.length]); i += Math.max(1, q.length); }
  for (const [a, b] of F.hits.slice(0, 1500)) F.marks.push(cm.markText(cm.posFromIndex(a), cm.posFromIndex(b), { className: "cm-found" }));
  const cur = cm.indexFromPos(cm.getCursor("from"));
  F.now = keepNow !== undefined ? Math.min(keepNow, F.hits.length - 1) : F.hits.findIndex(([a]) => a >= cur);
  if (F.now < 0 && F.hits.length) F.now = 0;
  showHit();
}
function showHit() {
  $("#findCount").textContent = F.hits.length ? `${F.now + 1} of ${F.hits.length}` : "No match";
  if (F.now < 0 || !F.hits[F.now]) return;
  const [a, b] = F.hits[F.now];
  if (F.cur) F.cur.clear();
  F.cur = cm.markText(cm.posFromIndex(a), cm.posFromIndex(b), { className: "cm-found-now" });
  cm.setSelection(cm.posFromIndex(a), cm.posFromIndex(b));
  cm.scrollIntoView({ from: cm.posFromIndex(a), to: cm.posFromIndex(b) }, 80);
}
function stepFind(dir) {
  if (!F.hits.length) return runFind();
  F.now = (F.now + dir + F.hits.length) % F.hits.length;
  showHit();
}
function replaceOne() {
  if (F.now < 0 || !F.hits[F.now]) return runFind();
  const [a, b] = F.hits[F.now];
  cm.replaceRange($("#replaceText").value, cm.posFromIndex(a), cm.posFromIndex(b));
  runFind(F.now);
}
function replaceAll() {
  if (!F.hits.length) return;
  const rep = $("#replaceText").value, n = F.hits.length;
  cm.operation(() => { for (const [a, b] of [...F.hits].reverse()) cm.replaceRange(rep, cm.posFromIndex(a), cm.posFromIndex(b)); });
  runFind();
  toast(`Replaced ${n} occurrence${n === 1 ? "" : "s"}.`);
}
function toggleComment() {
  const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
  const lines = []; for (let l = from; l <= to; l++) lines.push(cm.getLine(l));
  const all = lines.every((t) => /^\s*%/.test(t) || !t.trim());
  cm.operation(() => {
    for (let l = from; l <= to; l++) {
      const t = cm.getLine(l);
      if (all) { const m = t.match(/^(\s*)% ?/); if (m) cm.replaceRange(m[1], { line: l, ch: 0 }, { line: l, ch: m[0].length }); }
      else if (t.trim()) cm.replaceRange("% ", { line: l, ch: 0 });
    }
  });
}

/* ---------------------------------------------------------------- completion */
function maybeComplete() {
  if (!E.comp || !E.current) return closeComplete();
  const cur = cm.getCursor();
  const before = cm.getLine(cur.line).slice(0, cur.ch);
  let m, kind = null, part = "";
  if ((m = before.match(/\\(?:ref|eqref|cref|Cref|autoref|pageref|nameref)\{([^{}]*)$/))) { kind = "label"; part = m[1]; }
  else if ((m = before.match(/\\cite[tpa]?\*?(?:\[[^\]]*\])*\{([^{}]*)$/))) { kind = "cite"; part = m[1].split(",").pop().trim(); }
  else if ((m = before.match(/\\begin\{([A-Za-z*]*)$/))) { kind = "env"; part = m[1]; }
  else if ((m = before.match(/\\(?:input|include)\{([^{}]*)$/))) { kind = "tex"; part = m[1]; }
  else if ((m = before.match(/\\includegraphics(?:\[[^\]]*\])?\{([^{}]*)$/))) { kind = "image"; part = m[1]; }
  if (!kind) return closeComplete();
  const p = part.toLowerCase();
  const pool = { label: E.comp.labels.map((x) => [x.label, x.what]), cite: E.comp.cites.map((x) => [x.key, x.what]),
    env: E.comp.envs.map((x) => [x, ""]), tex: E.comp.tex.map((x) => [x, ""]), image: E.comp.images.map((x) => [x, ""]) }[kind];
  const list = pool.filter(([v, w]) => v.toLowerCase().includes(p) || (w || "").toLowerCase().includes(p)).slice(0, 40);
  if (!list.length) return closeComplete();
  E.complete = { kind, part, list, i: 0, line: cur.line, ch: cur.ch - part.length };
  drawComplete();
}
function drawComplete() {
  const C = E.complete, box = $("#complete");
  box.innerHTML = C.list.map(([v, w], i) => `<div class="${i === C.i ? "on" : ""}" role="option" data-i="${i}">${esc(v)}<span>${esc(w || "")}</span></div>`).join("");
  const pos = cm.cursorCoords(true, "window");
  box.style.left = `${Math.min(pos.left, window.innerWidth - 300)}px`;
  box.style.top = `${Math.min(pos.bottom + 4, window.innerHeight - 270)}px`;
  box.hidden = false;
  const on = box.children[C.i]; if (on) on.scrollIntoView({ block: "nearest" });
  if (!E.completeKeys) {
    E.completeKeys = { Up: () => moveComplete(-1), Down: () => moveComplete(1), Enter: () => pickComplete(), Esc: () => closeComplete() };
    cm.addKeyMap(E.completeKeys);
  }
}
function moveComplete(d) { const C = E.complete; if (!C) return; C.i = (C.i + d + C.list.length) % C.list.length; drawComplete(); }
function pickComplete(i) {
  const C = E.complete;
  if (!C) return CodeMirror.Pass;
  const v = C.list[i !== undefined ? i : C.i][0];
  const from = { line: C.line, ch: C.ch }, to = cm.getCursor();
  const after = cm.getLine(to.line).slice(to.ch);
  if (C.kind === "env") {
    const indent = cm.getLine(to.line).match(/^\s*/)[0];
    const close = after.startsWith("}") ? "" : "}";
    cm.replaceRange(`${v}${close}\n${indent}  \n${indent}\\end{${v}}`, from, after.startsWith("}") ? { line: to.line, ch: to.ch + 1 } : to);
    cm.setCursor({ line: from.line + 1, ch: indent.length + 2 });
  } else {
    cm.replaceRange(v + (after.startsWith("}") ? "" : "}"), from, to);
    if (after.startsWith("}")) cm.setCursor({ line: to.line, ch: from.ch + v.length + 1 });
  }
  closeComplete();
  return undefined;
}
function closeComplete() {
  E.complete = null; $("#complete").hidden = true;
  if (E.completeKeys) { cm.removeKeyMap(E.completeKeys); E.completeKeys = null; }
}

/* ---------------------------------------------------------------- the AI */
async function ask(command, opts = {}) {
  command = (command || "").trim();
  if (!command && !opts.error) return;
  await saveAll();
  E.busy = true;
  $("#ask").classList.add("busy"); $("#askBtn").disabled = true; $("#askBtn").textContent = "Working…";
  const rv = $("#review");
  rv.hidden = false;
  rv.innerHTML = `<div class="thinking">${opts.error ? "Looking at the error and your source…" : "Reading the paper and working out the change…"} Nothing is changed until you accept it.</div>`;
  const sel = cm.getSelection();
  const where = { path: E.current, line: cm.getCursor().line + 1, selection: sel && sel.length < 6000 ? sel : "" };
  try {
    const body = opts.error ? { error: opts.error } : { command, where, relax: !!opts.relax };
    const r = await api(`${API}/assist`, { method: "POST", body });
    E.proposal = r.proposal;
    E.lastCommand = command;
    showProposal();
  } catch (e) {
    const limits = /next best model/i.test(e.message);
    rv.innerHTML = `<div class="rv-note error">${esc(e.message)}</div>
      <div class="rv-buttons">${limits ? `<button class="btn primary" data-act="relax">Use the next best model for this command</button>` : ""}
      <span class="spacer"></span><button class="btn" data-act="closereview">Close</button></div>`;
    E.relaxCommand = command; E.relaxError = opts.error;
  } finally {
    E.busy = false; $("#ask").classList.remove("busy"); $("#askBtn").disabled = false; $("#askBtn").textContent = "Propose changes";
  }
}
function showProposal() {
  const P = E.proposal, rv = $("#review");
  rv.hidden = false;
  const who = P.how === "rule" ? "done exactly, by rule" : `proposed by the AI${P.model ? " (" + esc(P.model) + ")" : ""}`;
  const notes = (P.warnings || []).map((w) => `<div class="rv-note ${esc(w.level)}">${w.path ? `<a data-act="goto" data-path="${esc(w.path)}" data-line="${w.line || 1}">${esc(w.path)}${w.line ? ", line " + w.line : ""}</a>: ` : ""}${mathText(w.text)}</div>`).join("");
  const alts = (P.alternatives || []).length ? `<div class="rv-alts">${P.alternatives.map((a, i) => `<button class="btn small" data-act="alt" data-i="${i}">${mathText(a.label)}</button>`).join("")}</div>` : "";
  if (P.kind === "navigate") {
    rv.hidden = true;
    openFile(P.navigate.path, P.navigate.line);
    toast(P.summary);
    return;
  }
  if (P.kind === "answer" || P.kind === "question" || P.kind === "none") {
    rv.innerHTML = `<div class="rv-head"><div class="rv-sum">${P.kind === "question" ? "The AI asks" : P.kind === "answer" ? "Answer" : "No change"}</div><div class="rv-who">${who}</div></div>
      <div class="rv-answer">${esc(P.answer || P.question || P.summary)}</div>${notes}${alts}
      <div class="rv-buttons"><span class="spacer"></span><button class="btn" data-act="closereview">Close</button></div>`;
    if (P.kind === "question") { $("#askText").value = E.lastCommand ? E.lastCommand + " " : ""; $("#askText").focus(); }
    return;
  }
  const byFile = {};
  for (const h of P.hunks) (byFile[h.path] = byFile[h.path] || []).push(h);
  const files = Object.entries(byFile).map(([path, hs]) => `<div class="rv-file">${esc(path)}</div>` + hs.map((h) => {
    const lines = h.old_end >= h.old_start ? `lines ${h.old_start}–${h.old_end}` : `after line ${h.old_start - 1}`;
    const body = [...(h.before || []).map((l) => `<div class="ctx">${esc(l) || " "}</div>`), ...h.old.map((l) => `<div class="del">${esc(l) || " "}</div>`),
      ...h.new.map((l) => `<div class="ins">${esc(l) || " "}</div>`), ...(h.after || []).map((l) => `<div class="ctx">${esc(l) || " "}</div>`)].join("");
    return `<div class="hunk" data-id="${esc(h.id)}"><label class="hunk-head"><input type="checkbox" checked data-hunk="${esc(h.id)}">
      <span class="where">${h.old.length ? (h.new.length ? "Change" : "Remove") : "Add"} ${lines}</span><a data-act="goto" data-path="${esc(path)}" data-line="${h.old_start}">show in file</a></label>
      <div class="hunk-body">${body}</div></div>`;
  }).join("")).join("");
  rv.innerHTML = `<div class="rv-head"><div class="rv-sum">${esc(P.summary)}</div><div class="rv-who">${who}</div></div>${notes}${alts}${files}
    <div class="rv-buttons"><button class="btn primary" data-act="accept">Accept ${P.hunks.length > 1 ? "selected changes" : "this change"}</button>
      <button class="btn" data-act="closereview">Discard</button><span class="spacer"></span><span class="rv-who">${P.hunks.length} change${P.hunks.length === 1 ? "" : "s"} in ${Object.keys(byFile).length} file${Object.keys(byFile).length === 1 ? "" : "s"}</span></div>`;
}
async function accept() {
  const P = E.proposal;
  if (!P) return;
  const keep = $$("#review [data-hunk]").filter((x) => x.checked).map((x) => x.dataset.hunk);
  if (!keep.length) { toast("Tick at least one change, or press Discard."); return; }
  await saveAll();
  try {
    const r = await api(`${API}/apply`, { method: "POST", body: { id: P.id, keep: keep.length === P.hunks.length ? null : keep } });
    for (const [path, f] of Object.entries(r.files)) {
      if (E.docs.has(path)) await reloadFile(path, f.text, f.sha);
    }
    const first = P.hunks.find((h) => keep.includes(h.id));
    $("#review").hidden = true; E.proposal = null; $("#askText").value = "";
    toast(`Done. ${r.changed} file${r.changed === 1 ? "" : "s"} changed; undo it from History if you change your mind.`);
    if (first && E.docs.has(first.path)) openFile(first.path, first.new_start, { focus: false });
    compile();
  } catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------------- dialogs */
function modal(html) { const m = $("#modal"); m.innerHTML = `<div class="dialog" role="dialog" aria-modal="true">${html}</div>`; m.hidden = false; const f = $("input, button.primary, button", m); if (f) f.focus(); }
function closeModal() { $("#modal").hidden = true; $("#modal").innerHTML = ""; }
function menu(anchor, html) {
  const m = $("#menu");
  m.innerHTML = html; m.hidden = false;
  const r = anchor.getBoundingClientRect();
  m.style.top = `${r.bottom + 4}px`;
  m.style.left = `${Math.max(8, Math.min(r.right - m.offsetWidth, window.innerWidth - m.offsetWidth - 8))}px`;
}
function closeMenu() { $("#menu").hidden = true; }
const KIND = { start: ["made from the paper", ""], manual: ["saved by you", "manual"], ai: ["AI change", "ai"], restore: ["restored", ""], auto: ["while editing", ""] };
/* ------------------------------------------------- references and arXiv */
const REF = { verified: ["verified", "a"], check: ["check", "w"], mismatch: ["another paper", "d"], not_found: ["not found", "d"], retracted: ["RETRACTED", "d"], unchecked: ["not checked", ""] };
async function refCheck() {
  modal(`<header><h2>References</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body"><p class="muted" id="refStatus">Looking up every reference…</p><div id="refOut"></div></div>`);
  try {
    await saveAll();
    const r = await api(`/api/projects/${encodeURIComponent(PID)}/refcheck`, { method: "POST", body: {} });
    const c = r.count;
    $("#refStatus").textContent = `${r.entries.length} references: ${c.verified} verified, ${c.check} to check, ${c.mismatch + c.not_found} wrong or not found, ${c.retracted} retracted${c.unchecked ? `, ${c.unchecked} not checked` : ""}. Only identifiers and titles were sent to the databases.`;
    $("#refOut").innerHTML = r.entries.map((x) => `<div class="ref-row"><span class="kind ${(REF[x.status] || ["", ""])[1]}">${(REF[x.status] || [x.status])[0]}</span> <b>${esc(x.key)}</b> ${esc(x.title || "")}<div class="muted small">${esc(x.detail)}</div></div>`).join("");
  } catch (e) { $("#refStatus").textContent = e.message; }
}
async function arxivDialog() {
  modal(`<header><h2>Prepare for arXiv</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body"><p class="muted" id="axStatus">Compiling and checking the package as arXiv will…</p><div id="axOut"></div></div>`);
  try {
    await saveAll();
    const r = await api(`${API}/arxiv`, { method: "POST", body: {} });
    $("#axStatus").innerHTML = r.ok ? "Ready to upload: every check passed." : "Fix the items marked in red, then prepare again.";
    const lv = { ok: ["ok", "a"], warn: ["check", "w"], error: ["fix", "d"] };
    $("#axOut").innerHTML = r.checks.map((c) => `<div class="ref-row"><span class="kind ${lv[c.level][1]}">${lv[c.level][0]}</span> <b>${esc(c.item)}</b> <span class="small">${esc(c.text)}</span></div>`).join("")
      + `<p style="margin-top:12px"><a class="btn primary" href="/api/projects/${encodeURIComponent(PID)}/download/arxiv-submission.zip">Download the package (.zip, ${r.files} files)</a></p>
      <h3 style="margin:14px 0 6px">For arXiv's form</h3>` + Object.entries(r.metadata).filter(([k, v]) => v).map(([k, v]) =>
        `<div class="ax-field"><div class="small muted">${esc(k)}</div><div class="ax-v">${esc(v)}</div><button class="btn small" data-act="copyfield" data-v="${esc(v)}">Copy</button></div>`).join("");
  } catch (e) { $("#axStatus").textContent = e.message; }
}

/* ------------------------------------------------- the referee's report and the proofs */
const RV = { data: null, poll: 0, kind: "referee" };
const RV_TYPE = { mathematics: ["mathematics", "red"], language: ["language", ""], presentation: ["presentation", ""], typesetting: ["typesetting", ""],
  reference: ["reference", "hl"], query: ["query", "hl"], other: ["other", ""] };
const RV_STATUS = { pending: "waiting", applied: "changed", nothing: "nothing to change", needs_author: "for you", declined: "declined",
  done_by_author: "done by you", answered: "answered" };
async function reviewOpen() {
  try { RV.data = await api(`${API}/reply`); } catch (e) { return toast(e.message, true); }
  reviewDraw();
}
function reviewDraw() {
  const d = RV.data || { rounds: [] }, last = d.rounds[d.rounds.length - 1];
  const form = `<form class="rv-start" data-act="rvstart">
      <div class="rv-kinds"><label><input type="radio" name="rvkind" value="referee" ${RV.kind === "referee" ? "checked" : ""}> A referee's report</label>
      <label><input type="radio" name="rvkind" value="proofs" ${RV.kind === "proofs" ? "checked" : ""}> Proof corrections and queries from the journal</label>
      <label><input type="radio" name="rvkind" value="coauthor" ${RV.kind === "coauthor" ? "checked" : ""}> Comments from a co-author (the file from the review pack)</label></div>
      <textarea id="rvText" rows="6" placeholder="Paste the report here, or choose its file below."></textarea>
      <div class="rv-row"><input type="file" id="rvFile" accept=".pdf,.txt,.md,.tex,.json"><button class="btn primary" type="submit">Read it and carry it out</button></div>
      <p class="muted small">Each point is carried out as an exact change in your files. Language, presentation and typesetting points are made by themselves when they touch no formula, label, reference or citation; everything about the mathematics, the references, and every query waits for you. A version is kept before the first change (see History).</p></form>`;
  modal(`<header><h2>Review &amp; proofs</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body rv">
    ${last ? reviewRound(last, d.busy) : ""}${!last || !d.busy ? `<details ${last ? "" : "open"}><summary><b>${last ? "Another report" : "Start"}</b></summary>${form}</details>` : ""}</div>`);
  clearTimeout(RV.poll);
  if (d.busy || (last && ["reading", "working"].includes(last.state))) RV.poll = setTimeout(async () => { try { RV.data = await api(`${API}/reply`); if (!$("#modal").hidden) reviewDraw(); } catch (e) { /* try again */ } }, 2500);
}
function reviewRound(r, busy) {
  const open = r.points.filter((x) => ["pending", "needs_author"].includes(x.status)).length, auto = r.points.filter((x) => x.decision && x.decision.choice === "auto").length;
  const head = `<h3>${esc(r.title)}</h3><p class="muted">${r.state === "reading" ? "Reading the report…" : r.state === "working" || busy ? `Working through the points… (${r.points.filter((x) => x.status !== "pending").length} of ${r.points.length})` :
    r.state === "error" ? "Stopped: " + esc(r.error || "") : `${r.points.length} points. ${auto} made by themselves (language and layout only); ${open} waiting for you.`}</p>`;
  const pts = r.points.map((x) => {
    const t = RV_TYPE[x.type] || RV_TYPE.other, mine = x.status === "needs_author";
    const diff = (x.hunks || []).map((h) => `<div class="rv-diff"><div class="muted small">${esc(h.path)}, line ${h.old_start}</div>${h.old.map((l) => `<div class="d">${esc(l)}</div>`).join("")}${h.new.map((l) => `<div class="a">${esc(l)}</div>`).join("")}</div>`).join("");
    const acts = mine && !busy ? `<div class="rv-acts">${x.proposal && (x.hunks || []).length ? `<button class="btn small primary" data-act="rvdo" data-r="${r.id}" data-p="${x.id}" data-c="apply">Make this change</button>` : ""}
      <textarea class="rv-note" id="rvn-${x.id}" rows="2" placeholder="${x.type === "query" ? "Your answer" : "A different change, your reason for declining, or what you changed yourself"}"></textarea>
      <button class="btn small" data-act="rvdo" data-r="${r.id}" data-p="${x.id}" data-c="redo">Propose this instead</button>
      <button class="btn small" data-act="rvdo" data-r="${r.id}" data-p="${x.id}" data-c="${x.type === "query" ? "answer" : "decline"}">${x.type === "query" ? "Answer" : "Decline, with this reason"}</button>
      <button class="btn small quiet" data-act="rvdo" data-r="${r.id}" data-p="${x.id}" data-c="self">I made it myself</button></div>` : "";
    return `<div class="rv-pt ${mine ? "mine" : ""}"><div class="rv-h"><b>${esc(x.id)}</b><span class="kind ${t[1]}">${t[0]}</span><span class="kind">${RV_STATUS[x.status] || x.status}</span>${x.where ? `<span class="muted small">${esc(x.where)}</span>` : ""}</div>
      <div class="rv-q">${esc(x.quote || x.request)}</div>${x.summary ? `<div class="small">${esc(x.summary)}${(x.where_changed || []).length ? ` <span class="muted">(${esc(x.where_changed.join("; "))})</span>` : ""}</div>` : ""}
      ${x.held ? `<div class="small warn">${esc(x.held)}</div>` : ""}${(x.warnings || []).map((w) => `<div class="small warn">${esc(w)}</div>`).join("")}
      ${x.decision && x.decision.note ? `<div class="small">Your note: ${esc(x.decision.note)}</div>` : ""}${diff}${acts}</div>`;
  }).join("");
  const letterBox = r.letter ? `<h3>The letter</h3><textarea class="rv-letter" rows="16" readonly>${esc(r.letter)}</textarea>
      <div class="rv-row"><button class="btn small" data-act="rvcopy">Copy</button><a class="btn small" download="${r.kind === "referee" ? "response-to-referee" : "proof-corrections"}.txt" href="data:text/plain;charset=utf-8,${encodeURIComponent(r.letter)}">Download</a></div>`
    : r.state === "ready" ? `<button class="btn primary" data-act="rvletter" data-r="${r.id}" ${open ? "disabled title='Decide on every point first'" : ""}>Write the ${r.kind === "referee" ? "response to the referee" : "list of corrections"}</button>` : "";
  return head + pts + letterBox;
}
async function reviewPost(body) {
  try { RV.data = await api(`${API}/reply`, { method: "POST", body }); reviewDraw(); await reloadOpenFiles(); }
  catch (e) { toast(e.message, true); }
}
async function reloadOpenFiles() {
  for (const path of E.docs.keys()) { try { await reloadFile(path); } catch (e) { /* closed or binary */ } }
}

/* ------------------------------------------------- back to the handwriting */
async function inNotes() {
  if (!E.current || !/\.tex$/i.test(E.current)) return toast("Open a .tex file and put the cursor on a line first.");
  const line = cm.getCursor().line + 1;
  modal(`<header><h2>In your notes</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <p class="muted" id="srcStatus">Finding the page this came from…</p><div id="srcOut"></div></div>`);
  try {
    await save(E.current);
    const r = await api(`${API}/source`, { method: "POST", body: { path: E.current, line } });
    const st = $("#srcStatus"), out = $("#srcOut"); if (!st || !out) return;
    if (!r.page) { st.textContent = r.note || "No page of your notes matches this line."; return; }
    const how = r.formulas && r.formulas.length ? `${r.formulas.length} of its formula${r.formulas.length === 1 ? "" : "s"} ${r.formulas.length === 1 ? "is" : "are"} on this page` : "its words match this page";
    st.innerHTML = `Page ${r.page} of your notes (${esc(how)}). ` + (r.found ? "Marked in yellow: the lines it was typeset from. The marks are approximate." :
      esc(r.note || "The page reader could not point at the lines; the whole page is shown."));
    out.innerHTML = `<div class="src-img"><img src="/api/projects/${encodeURIComponent(PID)}/page/${r.page}" alt="Page ${r.page} of the notes">
      ${(r.boxes || []).map((b, i) => `<span class="src-box" style="top:${b[0] / 10}%;left:${b[1] / 10}%;height:${(b[2] - b[0]) / 10}%;width:${(b[3] - b[1]) / 10}%"><i>${i + 1}</i></span>`).join("")}</div>`;
    const first = $(".src-box", out); if (first) setTimeout(() => first.scrollIntoView({ block: "center" }), 60);
  } catch (e) { const st = $("#srcStatus"); if (st) st.textContent = e.message; }
}
async function history() {
  let vs;
  try { vs = (await api(`${API}/history`)).versions; } catch (e) { return toast(e.message, true); }
  modal(`<header><h2>History</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <p>Every change is kept: yours (a version every few minutes while you type), each accepted AI change (with the state just before it), and versions you save with a name. Going back keeps the present state as a version too.</p>
    <form class="field" data-act="savever"><input id="verLabel" placeholder="Name this version, for example: sent to my co-author" maxlength="120"><button class="btn primary" type="submit">Save a version</button></form>
    ${vs.map((v) => `<div class="ver"><div class="lbl">${esc(v.label)}<span class="kind ${(KIND[v.kind] || ["", ""])[1]}">${esc((KIND[v.kind] || [v.kind])[0])}</span></div>
      <div class="meta">${when(v.at)}, ${v.files} file${v.files === 1 ? "" : "s"}</div>
      <div class="acts"><button class="btn small" data-act="diffver" data-id="${v.id}">Compare with now</button><button class="btn small" data-act="restorever" data-id="${v.id}">Go back to this</button></div></div>`).join("")}
    <div id="diffOut"></div></div>`);
}
async function showDiff(id) {
  try {
    const r = await api(`${API}/history`, { method: "POST", body: { action: "diff", id } });
    const out = r.files.length ? r.files.map((f) => `<p><b>${esc(f.path)}</b> ${esc(f.status)}</p>${f.diff ? `<pre class="diff">${f.diff.split("\n").map((l) =>
      `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "a" : l.startsWith("-") && !l.startsWith("---") ? "d" : l.startsWith("@@") ? "h" : ""}">${esc(l)}</span>`).join("\n")}</pre>` : ""}`).join("")
      : "<p>Nothing differs: the files are as they were then.</p>";
    $("#diffOut").innerHTML = `<h3 style="margin:16px 0 6px;font-weight:600">Version ${id} compared with now</h3>${out}`;
    $("#diffOut").scrollIntoView({ block: "start" });
  } catch (e) { toast(e.message, true); }
}
async function restoreVersion(id) {
  if (!confirm("Go back to this version? The files as they are now are kept as a version, so you can return to them.")) return;
  try {
    await saveAll();
    await api(`${API}/history`, { method: "POST", body: { action: "restore", id } });
    await refreshTree();
    for (const p of [...E.docs.keys()]) {
      if (E.tree.some((x) => x.path === p)) await reloadFile(p);
      else { E.docs.delete(p); E.openTabs = E.openTabs.filter((x) => x !== p); }
    }
    if (!E.tree.some((x) => x.path === E.current)) openFile("main.tex"); else drawTabs();
    closeModal(); toast("Back to that version."); compile();
  } catch (e) { toast(e.message, true); }
}
async function proofDialog() {
  let r;
  try { r = await api(`/api/projects/${PID}/proof`); } catch (e) { return toast(e.message, true); }
  const vs = r.versions || [];
  const line = (v) => {
    const t = v.tsa && v.tsa.length ? `timestamped ${new Date(v.tsa[0].time * 1000).toLocaleString()}` : (v.enabled ? "waiting for the timestamp" : "signed (timestamps turned off)");
    const btc = v.ots && v.ots.status === "bitcoin" ? `, in Bitcoin block ${v.ots.height}` : v.ots && v.ots.status === "pending" ? ", Bitcoin confirmation pending" : "";
    return `<div class="proof-line"><b>${esc(v.note || v.title || "Version")}</b><span>${when(v.created)}: ${t}${btc}.
      <a href="/api/projects/${PID}/proof/certificate?rec=${esc(v.id)}" target="_blank" rel="noopener">Certificate</a> ·
      <a href="/api/projects/${PID}/proof/bundle?rec=${esc(v.id)}&files=1">Download the proof</a></span></div>`;
  };
  modal(`<header><h2>Record this version</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <p>Makes a signed, timestamped record of the files exactly as they are now, the way your notes were recorded when you uploaded them. Only their fingerprints leave this computer.</p>
    <form class="field" data-act="recordver"><input id="proofLabel" placeholder="What this version is, for example: submitted to the journal" maxlength="200"><button class="btn primary" type="submit">Record this version</button></form>
    ${vs.length ? vs.slice().reverse().map(line).join("") : "<p class=\"meta\">No version recorded yet.</p>"}
    ${r.proof && !r.proof.missing ? `<p style="margin-top:14px">Your notes: <a href="/api/projects/${PID}/proof/certificate" target="_blank" rel="noopener">certificate of the upload</a>.</p>` : ""}
    ${r.identity && r.identity.fingerprint ? `<p class="meta">Your signing key: <span class="mono">${esc(r.identity.fingerprint)}</span></p>` : ""}</div>`);
}
async function recordVersion(label) {
  try {
    await saveAll();
    await api(`/api/projects/${PID}/proof`, { method: "POST", body: { action: "version", label } });
    toast("Recorded. The timestamps are added in the background.");
    proofDialog();
  } catch (e) { toast(e.message, true); }
}
async function newPath(kind) {
  const base = E.current && E.current.includes("/") ? E.current.split("/").slice(0, -1).join("/") + "/" : "";
  const name = prompt(kind === "new_folder" ? "Name of the new folder" : "Name of the new file (for example sections/05-appendix.tex)", kind === "new_folder" ? "figures" : base + "new.tex");
  if (!name) return;
  try {
    const r = await api(`${API}/files`, { method: "POST", body: { action: kind, path: name.trim() } });
    E.tree = r.tree; drawTree();
    if (kind === "new_file") openFile(name.trim().replace(/^\/+/, ""));
  } catch (e) { toast(e.message, true); }
}
async function renamePath(path) {
  const to = prompt("New name", path);
  if (!to || to === path) return;
  try {
    await saveAll();
    const r = await api(`${API}/files`, { method: "POST", body: { action: "rename", path, to: to.trim() } });
    E.tree = r.tree;
    for (const p of [...E.docs.keys()]) {
      if (p === path || p.startsWith(path + "/")) {
        const np = to.trim() + p.slice(path.length);
        E.docs.set(np, E.docs.get(p)); E.docs.delete(p);
        E.openTabs = E.openTabs.map((x) => (x === p ? np : x));
        if (E.current === p) E.current = np;
      }
    }
    drawTree(); drawTabs();
    toast(/\.tex$/.test(path) ? "Renamed. Update the \\input line that includes it." : "Renamed.");
  } catch (e) { toast(e.message, true); }
}
async function deletePath(path) {
  if (!confirm(`Delete ${path}? It stays in the history, so you can bring it back.`)) return;
  try {
    const r = await api(`${API}/files`, { method: "POST", body: { action: "delete", path } });
    E.tree = r.tree;
    if (E.docs.has(path)) { E.docs.delete(path); if (E.openTabs.includes(path)) closeTab(path); }
    drawTree();
  } catch (e) { toast(e.message, true); }
}
async function upload(files) {
  if (!files.length) return;
  const form = new FormData();
  const folder = E.current && E.current.includes("/") && !/\.tex$/.test(E.current) ? E.current.split("/")[0] : (E.tree.some((x) => x.path === "figures" && x.kind === "dir") ? "figures" : "");
  form.append("folder", [...files].every((f) => /\.(png|jpe?g|pdf|eps)$/i.test(f.name)) ? (folder || "figures") : "");
  for (const f of files) form.append("files", f);
  try {
    const r = await api(`${API}/upload`, { method: "POST", form });
    E.tree = r.tree; drawTree(); loadOutline();
    toast(`Uploaded ${r.saved.join(", ")}.`);
  } catch (e) { toast(e.message, true); }
}
function setView(v) { $("#ed").dataset.view = v; if (v === "pdf" && PDF.mode === "fit") setTimeout(() => setZoom("fit"), 50); if (v === "code" && cm) setTimeout(() => cm.refresh(), 30); }

/* ---------------------------------------------------------------- events */
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-act]");
  if (!$("#menu").hidden && !ev.target.closest("#menu") && !(t && ["download", "more"].includes(t.dataset.act))) closeMenu();
  if (ev.target.closest("#complete div")) { pickComplete(Number(ev.target.closest("#complete div").dataset.i)); cm.focus(); return; }
  if (ev.target === $("#modal")) { closeModal(); return; }
  if (!t || t.tagName === "FORM") return;          // a form's own button submits it (handled below)
  const a = t.dataset.act, path = t.dataset.path;
  if (a === "none") return;
  if (t.tagName === "A" && a !== "goto") { /* plain link */ } else ev.preventDefault();
  switch (a) {
    case "open": case "tab": openFile(path); break;
    case "closetab": ev.stopPropagation(); closeTab(path); break;
    case "fold": E.folded.has(path) ? E.folded.delete(path) : E.folded.add(path); store.set("folded-" + PID, [...E.folded]); drawTree(); break;
    case "goto": closeModal(); await openFile(path, Number(t.dataset.line) || 1); break;
    case "rename": ev.stopPropagation(); renamePath(path); break;
    case "delete": ev.stopPropagation(); deletePath(path); break;
    case "newfile": newPath("new_file"); break;
    case "newfolder": newPath("new_folder"); break;
    case "upload": $("#uploadInput").click(); break;
    case "compile": saveAndCompile(); break;
    case "topdf": toPdf(); break;
    case "tocode": toCode(); break;
    case "zoomin": stepZoom(1); break;
    case "zoomout": stepZoom(-1); break;
    case "logs": {
      if (ev.target.closest("a")) return;
      const shown = !$("#logs").classList.contains("closed");
      E.logsForced = !shown; E.logsOpen = !shown; store.set("logs-open", E.logsOpen); drawLogs(); break;
    }
    case "fixerror": ask("", { error: (E.build.errors || [])[Number(t.dataset.i)] }); break;
    case "accept": accept(); break;
    case "closereview": $("#review").hidden = true; E.proposal = null; cm.focus(); break;
    case "alt": { const al = E.proposal.alternatives[Number(t.dataset.i)]; $("#askText").value = al.command; ask(al.command); break; }
    case "relax": ask(E.relaxCommand, { relax: true, error: E.relaxError }); break;
    case "history": history(); break;
    case "notes": inNotes(); break;
    case "review": reviewOpen(); break;
    case "rvdo": reviewPost({ action: "decide", round: t.dataset.r, point: t.dataset.p, choice: t.dataset.c, note: ($(`#rvn-${t.dataset.p}`) || {}).value || "" }); break;
    case "rvletter": toast("Writing the letter…"); reviewPost({ action: "letter", round: t.dataset.r }); break;
    case "rvcopy": navigator.clipboard.writeText($(".rv-letter").value).then(() => toast("Copied.")).catch(() => toast("Select the text and copy it.", true)); break;
    case "close": closeModal(); break;
    case "diffver": showDiff(Number(t.dataset.id)); break;
    case "restorever": restoreVersion(Number(t.dataset.id)); break;
    case "findnext": stepFind(1); break;
    case "findprev": stepFind(-1); break;
    case "replace": replaceOne(); break;
    case "replaceall": replaceAll(); break;
    case "findclose": closeFind(); break;
    case "view": setView(t.dataset.view); break;
    case "reloadfile": closeModal(); await reloadFile(path); toast("Loaded the latest version."); break;
    case "forcesave": { closeModal(); const d = E.docs.get(path); if (d) { const f = await api(`${API}/file?path=${encodeURIComponent(path)}`); d.sha = f.sha; d.dirty = true; save(path); } break; }
    case "dismiss": store.set("dismissed-" + PID, (E.info.source && E.info.source.built) || Date.now() / 1000); banner(); break;
    case "reset":
      closeMenu();
      if (!confirm("Start again from the paper as Scriptavera last built it? Your current files are kept in the history.")) return;
      try {
        await api(API, { method: "POST", body: { reset: true } });
        E.docs.clear(); E.openTabs = []; await loadInfo(); drawTree(); await openFile("main.tex"); compile(); toast("Started again from Scriptavera's version.");
      } catch (e) { toast(e.message, true); }
      break;
    case "download":
      menu(t, `<a href="${API}/download" data-act="none">Project as a .zip<span class="sub">All the files, for Overleaf or your own LaTeX</span></a>
        <a href="${API}/pdf?download=1" data-act="none">PDF<span class="sub">As last compiled</span></a>`);
      break;
    case "more":
      menu(t, `<button data-act="refcheck">Check the references<span class="sub">Each looked up: does it exist, is it the paper cited, is it retracted</span></button>
        <button data-act="arxiv">Prepare for arXiv<span class="sub">Checked and compiled the way arXiv does; the form filled in</span></button><hr>
        <button data-act="proof">Record this version<span class="sub">A signed, timestamped record of the files as they are</span></button>
        <button data-act="shortcuts">Keyboard shortcuts</button><hr>
        <button data-act="reset">Start again from Scriptavera's version<span class="sub">Your files are kept in the history</span></button>
        <a href="/#p/${PID}/assemble" data-act="none">Back to Scriptavera</a>`);
      break;
    case "proof": closeMenu(); proofDialog(); break;
    case "refcheck": closeMenu(); refCheck(); break;
    case "arxiv": closeMenu(); arxivDialog(); break;
    case "copyfield": navigator.clipboard.writeText(t.dataset.v || "").then(() => toast("Copied.")).catch(() => toast("Select and copy it.", true)); break;
    case "shortcuts":
      closeMenu();
      modal(`<header><h2>Keyboard shortcuts</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
        ${[["Ctrl+S or Ctrl+Enter", "Save and compile"], ["Ctrl+F", "Find in this file"], ["Ctrl+H", "Find and replace"], ["Ctrl+/", "Comment or uncomment the lines"],
          ["Ctrl+K", "Ask the AI to edit"], ["Ctrl+Z, Ctrl+Y", "Undo, redo (each file keeps its own)"], ["Double-click the PDF", "Show the source of that place"],
          ["Tab in a list of suggestions", "Accept the suggestion (labels after \\ref{, keys after \\cite{, environments after \\begin{)"]]
          .map(([k, v]) => `<div class="proof-line"><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join("")}</div>`);
      break;
    default: break;
  }
});
document.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  if (f.id === "ask") ask($("#askText").value);
  else if (f.dataset.act === "savever") {
    api(`${API}/history`, { method: "POST", body: { action: "save", label: $("#verLabel").value.trim() || "Saved by you" } })
      .then(() => { toast("Version saved."); history(); }).catch((e) => toast(e.message, true));
  } else if (f.dataset.act === "recordver") recordVersion($("#proofLabel").value.trim());
  else if (f.dataset.act === "rvstart") {
    const fd = new FormData(), file = $("#rvFile").files[0];
    RV.kind = (f.querySelector("input[name=rvkind]:checked") || {}).value || "referee";
    fd.append("action", "start"); fd.append("kind", RV.kind); fd.append("text", $("#rvText").value);
    if (file) fd.append("file", file);
    api(`${API}/reply`, { method: "POST", form: fd }).then((r) => { RV.data = r; reviewDraw(); }).catch((e) => toast(e.message, true));
  }
});
document.addEventListener("keydown", (ev) => {
  const mod = ev.ctrlKey || ev.metaKey;
  if (ev.key === "Escape") { if (!$("#modal").hidden) closeModal(); else if (!$("#menu").hidden) closeMenu(); else if (!$("#review").hidden && !E.busy && document.activeElement !== cm.getInputField()) { $("#review").hidden = true; } }
  if (mod && (ev.key === "s" || ev.key === "S") && !ev.target.closest(".CodeMirror")) { ev.preventDefault(); saveAndCompile(); }
  if (mod && ev.key === "k" && !ev.target.closest(".CodeMirror")) { ev.preventDefault(); $("#askText").focus(); }
  if (ev.target.id === "findText" || ev.target.id === "replaceText") {
    if (ev.key === "Enter") { ev.preventDefault(); if (ev.target.id === "replaceText") replaceOne(); else stepFind(ev.shiftKey ? -1 : 1); }
    if (ev.key === "Escape") closeFind();
  }
  if (ev.target.closest(".tree .row") && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); ev.target.closest(".row").click(); }
});
$("#complete").addEventListener("mousedown", (ev) => ev.preventDefault());     // keep the cursor in the source
$("#findText").addEventListener("input", () => runFind());
$("#findCase").addEventListener("change", () => runFind());
$("#zoom").addEventListener("change", (ev) => setZoom(ev.target.value));
$("#uploadInput").addEventListener("change", (ev) => { upload(ev.target.files); ev.target.value = ""; });
$("#autoCompile").checked = store.get("auto", true);
$("#autoCompile").addEventListener("change", (ev) => store.set("auto", ev.target.checked));
$("#pages").addEventListener("scroll", () => { clearTimeout(PDF.scrollT); PDF.scrollT = setTimeout(updatePageNo, 80); });
$("#pages").addEventListener("dblclick", (ev) => {
  const pg = ev.target.closest(".pg");
  if (!pg) return;
  const r = pg.getBoundingClientRect();
  const x = (ev.clientX - r.left) / PDF.scale, y = (ev.clientY - r.top) / PDF.scale;
  E.lastPdfPoint = { page: Number(pg.dataset.page), x, y };
  toCode(Number(pg.dataset.page), x, y);
});
$("#pages").addEventListener("wheel", (ev) => { if (ev.ctrlKey) { ev.preventDefault(); stepZoom(ev.deltaY < 0 ? 1 : -1); } }, { passive: false });
window.addEventListener("resize", () => { clearTimeout(PDF.resizeT); PDF.resizeT = setTimeout(() => { if (PDF.mode === "fit") setZoom("fit"); }, 150); });
window.addEventListener("beforeunload", (ev) => { if ([...E.docs.values()].some((d) => d.dirty)) { saveAll(); ev.preventDefault(); ev.returnValue = ""; } });
document.addEventListener("dragover", (ev) => { if (ev.target.closest("#side")) ev.preventDefault(); });
document.addEventListener("drop", (ev) => { if (ev.target.closest("#side")) { ev.preventDefault(); upload(ev.dataTransfer.files); } });

/* resizing the code and the PDF: drag the strip between them */
(function () {
  const g = $("#gutter");
  const w = store.get("pdf-w", null);
  if (w) document.documentElement.style.setProperty("--pdf-w", w);
  let drag = false;
  g.addEventListener("mousedown", (ev) => { if (ev.target.closest("button")) return; drag = true; document.body.style.userSelect = "none"; });
  window.addEventListener("mousemove", (ev) => {
    if (!drag) return;
    const pct = Math.max(22, Math.min(70, ((window.innerWidth - ev.clientX) / window.innerWidth) * 100));
    document.documentElement.style.setProperty("--pdf-w", pct.toFixed(1) + "%");
  });
  window.addEventListener("mouseup", () => {
    if (!drag) return;
    drag = false; document.body.style.userSelect = "";
    store.set("pdf-w", getComputedStyle(document.documentElement).getPropertyValue("--pdf-w").trim());
    cm.refresh(); if (PDF.mode === "fit") setZoom("fit");
  });
})();

/* ---------------------------------------------------------------- start */
(async function start() {
  if (!PID) return fatal("No paper was named in the address.");
  makeEditor();
  $("#zoom").value = String(PDF.mode);
  if (!(await loadInfo())) return;
  drawTree();
  const was = store.get("open-" + PID, null);
  const tabs = was && was.tabs ? was.tabs.filter((p) => E.tree.some((x) => x.path === p)) : [];
  for (const p of tabs) if (p !== (was.current || "main.tex")) { E.openTabs.push(p); }
  await openFile(was && E.tree.some((x) => x.path === was.current) ? was.current : "main.tex", null, { focus: false });
  showBuild();
  loadOutline();
  if (E.build && E.build.has_pdf) loadPdf(E.build.build);
  else if (E.info.engine) compile();
})();
