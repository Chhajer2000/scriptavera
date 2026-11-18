/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* Typing the way Overleaf's editor lets you type:
   - brackets, braces and $ close by themselves (and \[ \( \{ get their partner), the closing one is typed over;
   - finishing \begin{name} puts \end{name} below it (with an \item for a list); Enter after an \item starts the
     next one, and on an empty \item leaves the list;
   - suggestions carry the places Tab moves through (\frac{|}{ }), see editor.js for the lists;
   - sections and environments fold from the gutter; brackets show their partner;
   - Overleaf's keys: Ctrl+D delete line, Ctrl+Shift+D duplicate, Alt+Up/Down move lines, Alt+Shift+Up/Down copy
     them, Ctrl+Shift+L go to line, Ctrl+U/Ctrl+Shift+U upper/lower case, Ctrl+B bold, Ctrl+I italic,
     Ctrl+Space suggestions, Ctrl+G/Ctrl+Shift+G next/previous match;
   - the formula under the cursor is shown typeset above it.
   Uses the editor's own helpers and state (cm, E, $, esc, toast, modal, closeModal, flashLine, openFind, stepFind). */

const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd" : "Ctrl";
const PREF = Object.assign({ autoClose: true, autoComplete: true, preview: true }, store.get("prefs", {}));

/* ---------------------------------------------------------------- formulas in the text */
/* Every formula of a text: $...$, $$...$$, \(...\), \[...\] and the display environments, with where its content
   starts and ends. Comments and escaped characters (\$, \%) are skipped. An inline $ that is not closed before
   the paragraph ends is returned with `open` set. Used by the preview here and by the code check. */
const MATH_ENV_RE = /^\\begin\{(equation|align|gather|multline|flalign|alignat|eqnarray|displaymath|math|dmath)(\*?)\}/;
function mathRegions(text) {
  const out = [], n = text.length;
  const close = (from, tok, inline) => {
    for (let k = from; k < n; k++) {
      if (text.startsWith(tok, k)) return k;
      const c = text[k];
      if (c === "\\") { k++; continue; }
      if (c === "%") { const e = text.indexOf("\n", k); if (e < 0) return -1; k = e; continue; }
      if (inline && c === "\n" && /^[ \t]*\n/.test(text.slice(k + 1, k + 80))) return -(k + 1);   // a blank line: not closed
    }
    return -1;
  };
  let i = 0;
  while (i < n) {
    const c = text[i];
    if (c === "%") { const e = text.indexOf("\n", i); i = e < 0 ? n : e + 1; continue; }
    if (c === "\\") {
      const d = text[i + 1];
      if (d === "(" || d === "[") {
        const tok = d === "(" ? "\\)" : "\\]", j = close(i + 2, tok, false);
        out.push({ a: i, ia: i + 2, ib: j < 0 ? n : j, b: j < 0 ? n : j + 2, display: d === "[", open: j < 0, kind: d === "[" ? "\\[" : "\\(" });
        i = j < 0 ? n : j + 2; continue;
      }
      if (d === "b" && text.startsWith("\\begin{", i)) {
        const m = MATH_ENV_RE.exec(text.slice(i, i + 40));
        if (m) {
          const end = `\\end{${m[1]}${m[2]}}`, j = close(i + m[0].length, end, false);
          out.push({ a: i, ia: i + m[0].length, ib: j < 0 ? n : j, b: j < 0 ? n : j + end.length, display: true, open: j < 0, kind: m[1] + m[2] });
          i = j < 0 ? n : j + end.length; continue;
        }
      }
      i += 2; continue;
    }
    if (c === "$") {
      const dbl = text[i + 1] === "$", tok = dbl ? "$$" : "$", j = close(i + tok.length, tok, !dbl);
      if (j < 0) {
        const end = j === -1 ? n : -j - 1;
        out.push({ a: i, ia: i + tok.length, ib: end, b: end, display: dbl, open: true, kind: tok });
        i = end; continue;
      }
      out.push({ a: i, ia: i + tok.length, ib: j, b: j + tok.length, display: dbl, open: false, kind: tok });
      i = j + tok.length; continue;
    }
    i++;
  }
  return out;
}
const MR = { gen: -1, n: 0, doc: null, list: [] };
cm.on("change", () => { MR.n++; });
function regionsNow() {
  if (MR.gen !== MR.n || MR.doc !== cm.getDoc()) { MR.gen = MR.n; MR.doc = cm.getDoc(); MR.list = mathRegions(cm.getValue()); }
  return MR.list;
}
function inMath(pos) {
  const i = cm.indexFromPos(pos || cm.getCursor());
  return regionsNow().some((r) => i >= r.ia && i <= r.ib);
}

/* ---------------------------------------------------------------- snippets: the places Tab moves through */
const SNIP = { fields: [], outer: null, i: -1 };
function clearSnippet() { SNIP.fields.forEach((f) => f.mark.clear()); if (SNIP.outer) SNIP.outer.clear(); SNIP.fields = []; SNIP.outer = null; SNIP.i = -1; }
function insertSnippet(from, to, snippet) {
  clearSnippet();
  const indent = cm.getLine(from.line).match(/^\s*/)[0];
  snippet = snippet.replace(/\n/g, "\n" + indent);
  let out = "", last = 0, m;
  const fields = [], re = /\$\{(\d):([^}]*)\}|\$(\d)/g;
  while ((m = re.exec(snippet))) {
    out += snippet.slice(last, m.index);
    const def = m[2] || "";
    fields.push({ n: Number(m[1] || m[3]), at: out.length, len: def.length });
    out += def; last = re.lastIndex;
  }
  out += snippet.slice(last);
  cm.operation(() => {
    cm.replaceRange(out, from, to, "+snippet");
    const base = cm.indexFromPos(from);
    if (!fields.length) return;
    fields.sort((x, y) => (x.n || 99) - (y.n || 99));
    SNIP.outer = cm.markText(from, cm.posFromIndex(base + out.length), { inclusiveLeft: true, inclusiveRight: true, clearWhenEmpty: false });
    SNIP.fields = fields.map((f) => ({ n: f.n, mark: cm.markText(cm.posFromIndex(base + f.at), cm.posFromIndex(base + f.at + f.len),
      { className: "cm-snip", inclusiveLeft: true, inclusiveRight: true, clearWhenEmpty: false }) }));
  });
  if (SNIP.fields.length) nextField(1); else cm.setCursor(cm.posFromIndex(cm.indexFromPos(from) + out.length));
}
function nextField(d) {
  if (!SNIP.fields.length) return false;
  SNIP.i += d;
  if (SNIP.i < 0) SNIP.i = 0;
  if (SNIP.i >= SNIP.fields.length) {                       // past the last place: to the end of the snippet
    const r = SNIP.outer && SNIP.outer.find();
    clearSnippet();
    if (r) cm.setCursor(r.to);
    return true;
  }
  const r = SNIP.fields[SNIP.i].mark.find();
  if (!r) { clearSnippet(); return false; }
  cm.setSelection(r.from, r.to);
  if (SNIP.fields.length === 1 && d > 0) { /* one place: Tab after it leaves the snippet */ }
  return true;
}
cm.on("cursorActivity", () => {
  if (!SNIP.outer) return;
  const r = SNIP.outer.find(), c = cm.getCursor();
  if (!r || CodeMirror.cmpPos(c, r.from) < 0 || CodeMirror.cmpPos(c, r.to) > 0) clearSnippet();
});

/* ---------------------------------------------------------------- environments and lists */
const LISTS = /^(itemize|enumerate|description|compactitem|compactenum|inparaenum)$/;
function envUnclosed(name) {             // more \begin{name} than \end{name} in the file (comments left out)
  const t = cm.getValue().replace(/(^|[^\\])%.*$/gm, "$1");
  const esc_ = name.replace(/[*]/g, "\\*");
  const b = (t.match(new RegExp(`\\\\begin\\{${esc_}\\}`, "g")) || []).length, e = (t.match(new RegExp(`\\\\end\\{${esc_}\\}`, "g")) || []).length;
  return b > e;
}
function closeEnvironment(name) {
  const cur = cm.getCursor(), indent = cm.getLine(cur.line).match(/^\s*/)[0];
  const body = LISTS.test(name) ? (name === "description" ? "\\item[$1] $2" : "\\item $1") : "$1";
  insertSnippet(cur, cur, `\n  ${body}\n\\end{${name}}`);
  void indent;
}
function afterBegin() {                  // the cursor right after \begin{name}: is the environment to be closed?
  const cur = cm.getCursor(), line = cm.getLine(cur.line);
  const m = line.slice(0, cur.ch).match(/\\begin\{([A-Za-z*@]+)\}(?:\[[^\]]*\]|\{[^{}]*\})*$/);
  if (!m || line.slice(cur.ch).trim() || m[1] === "document") return null;
  return envUnclosed(m[1]) ? m[1] : null;
}

/* ---------------------------------------------------------------- the keys */
function escaped() {                     // an odd number of backslashes just before the cursor
  const cur = cm.getCursor(), before = cm.getLine(cur.line).slice(0, cur.ch);
  return (before.match(/\\+$/) || [""])[0].length % 2 === 1;
}
function typeOpen(ch) {
  if (!PREF.autoClose || cm.somethingSelected() && ch !== "$") return CodeMirror.Pass;
  if (escaped()) {
    const pair = { "[": "\\]", "(": "\\)", "{": "\\}" }[ch];
    if (!pair) { cm.replaceSelection(ch); return undefined; }          // \$ is a dollar sign, nothing to close
    const cur = cm.getCursor();
    cm.replaceRange(ch + pair, cur, cur, "+input");
    cm.setCursor({ line: cur.line, ch: cur.ch + 1 });
    return undefined;
  }
  return CodeMirror.Pass;
}
/* typing \} \] \) by hand where the editor already put that partner: step over it instead of doubling it */
function typeEscapedClose(ch) {
  const cur = cm.getCursor(), after = cm.getLine(cur.line).slice(cur.ch);
  if (!PREF.autoClose || cm.somethingSelected() || !escaped() || !after.startsWith("\\" + ch)) return false;
  cm.operation(() => {
    cm.replaceRange("", { line: cur.line, ch: cur.ch - 1 }, cur, "+input");
    cm.setCursor({ line: cur.line, ch: cur.ch + 1 });
  });
  return true;
}
function typeCloseBrace() {
  if (typeEscapedClose("}")) return;
  const cur = cm.getCursor(), next = cm.getLine(cur.line).charAt(cur.ch);
  if (next === "}" && PREF.autoClose && !cm.somethingSelected()) cm.setCursor({ line: cur.line, ch: cur.ch + 1 });
  else cm.replaceSelection("}");
  const env = afterBegin();
  if (env) closeEnvironment(env);
}
function enter() {
  if (E.complete) return CodeMirror.Pass;
  clearSnippet();                         // a new line ends the filling in
  if (cm.somethingSelected() || cm.getSelections().length > 1) return CodeMirror.Pass;
  const cur = cm.getCursor(), line = cm.getLine(cur.line);
  // \begin{name|}: step over the brace the editor added, then close the environment
  const half = line.slice(0, cur.ch).match(/\\begin\{([A-Za-z*@]+)$/);
  if (half && line.charAt(cur.ch) === "}") { cm.setCursor({ line: cur.line, ch: cur.ch + 1 }); }
  const env = afterBegin();
  if (env) { closeEnvironment(env); return undefined; }
  const item = line.match(/^(\s*)\\item\b(\[[^\]]*\])?(.*)$/);
  if (item && cur.ch === line.length) {
    if (!item[2] && !item[3].trim()) {                         // an empty \item: leave the list
      cm.replaceRange(item[1], { line: cur.line, ch: 0 }, { line: cur.line, ch: line.length });
      return undefined;
    }
    cm.replaceSelection(`\n${item[1]}\\item `);
    return undefined;
  }
  return CodeMirror.Pass;
}
function moveLines(dir) {
  cm.operation(() => {
    const ranges = cm.listSelections();
    const from = Math.min(...ranges.map((r) => Math.min(r.anchor.line, r.head.line)));
    let to = Math.max(...ranges.map((r) => Math.max(r.anchor.line, r.head.line)));
    if (ranges.length === 1 && ranges[0].head.line !== ranges[0].anchor.line && Math.max(ranges[0].head.ch, ranges[0].anchor.ch) === 0
        && to > from) to--;                                        // a selection ending at a line start leaves that line alone
    if ((dir < 0 && from === 0) || (dir > 0 && to === cm.lastLine())) return;
    const block = []; for (let l = from; l <= to; l++) block.push(cm.getLine(l));
    if (dir < 0) {
      const above = cm.getLine(from - 1);
      cm.replaceRange(block.join("\n") + "\n" + above, { line: from - 1, ch: 0 }, { line: to, ch: cm.getLine(to).length }, "+move");
    } else {
      const below = cm.getLine(to + 1);
      cm.replaceRange(below + "\n" + block.join("\n"), { line: from, ch: 0 }, { line: to + 1, ch: below.length }, "+move");
    }
    cm.setSelections(ranges.map((r) => ({ anchor: { line: r.anchor.line + dir, ch: r.anchor.ch }, head: { line: r.head.line + dir, ch: r.head.ch } })));
  });
}
function copyLines(dir) {
  cm.operation(() => {
    const r = cm.listSelections()[0];
    const from = Math.min(r.anchor.line, r.head.line), to = Math.max(r.anchor.line, r.head.line);
    const block = []; for (let l = from; l <= to; l++) block.push(cm.getLine(l));
    cm.replaceRange(block.join("\n") + "\n", { line: from, ch: 0 }, { line: from, ch: 0 }, "+copy");
    const shift = dir > 0 ? block.length : 0;
    cm.setSelection({ line: r.anchor.line + shift, ch: r.anchor.ch }, { line: r.head.line + shift, ch: r.head.ch });
  });
}
function duplicate() {
  if (cm.somethingSelected()) {
    const r = cm.listSelections()[0], t = cm.getSelection();
    const end = CodeMirror.cmpPos(r.anchor, r.head) > 0 ? r.anchor : r.head;
    cm.replaceRange(t, end, end, "+copy");
  } else copyLines(1);
}
function changeCase(upper) {
  if (!cm.somethingSelected()) {
    const w = cm.findWordAt(cm.getCursor());
    cm.setSelection(w.anchor, w.head);
  }
  cm.replaceSelections(cm.getSelections().map((s) => (upper ? s.toUpperCase() : s.toLowerCase())), "around");
}
/* Ctrl+B and Ctrl+I: wrap the selection in \textbf{...} / \textit{...}, or unwrap it if it is already wrapped */
function wrap(cmd) {
  cm.operation(() => {
    const sels = cm.listSelections();
    const out = sels.map((r) => {
      const from = CodeMirror.cmpPos(r.anchor, r.head) <= 0 ? r.anchor : r.head, to = from === r.anchor ? r.head : r.anchor;
      const text = cm.getRange(from, to), open = `\\${cmd}{`;
      const before = cm.getRange({ line: from.line, ch: Math.max(0, from.ch - open.length) }, from), after = cm.getRange(to, { line: to.line, ch: to.ch + 1 });
      if (before === open && after === "}") {
        cm.replaceRange(text, { line: from.line, ch: from.ch - open.length }, { line: to.line, ch: to.ch + 1 });
        return { anchor: { line: from.line, ch: from.ch - open.length }, head: { line: to.line, ch: to.ch - (from.line === to.line ? open.length : 0) } };
      }
      const inner = text.match(new RegExp(`^\\\\${cmd}\\{([\\s\\S]*)\\}$`));
      if (inner) { cm.replaceRange(inner[1], from, to); return { anchor: from, head: cm.posFromIndex(cm.indexFromPos(from) + inner[1].length) }; }
      cm.replaceRange(open + text + "}", from, to);
      const s = { line: from.line, ch: from.ch + open.length };
      return { anchor: s, head: text ? cm.posFromIndex(cm.indexFromPos(s) + text.length) : s };
    });
    cm.setSelections(out);
  });
  cm.focus();
}
function goToLine() {
  modal(`<header><h2>Go to line</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <form class="field" data-act="gotoline"><input id="gotoLine" inputmode="numeric" placeholder="Line number (1 to ${cm.lineCount()})" autocomplete="off">
    <button class="btn primary" type="submit">Go</button></form></div>`);
}
document.addEventListener("submit", (ev) => {
  if (ev.target.dataset.act !== "gotoline") return;
  ev.preventDefault();
  const n = parseInt($("#gotoLine").value, 10);
  if (!n) return;
  closeModal();
  const l = Math.max(1, Math.min(n, cm.lineCount())) - 1;
  cm.setCursor({ line: l, ch: 0 });
  cm.scrollIntoView({ line: l, ch: 0 }, 120);
  if (typeof flashLine === "function") flashLine(l + 1);
  cm.focus();
});
function findStep(d) {
  if ($("#find").hidden) openFind(false);
  else stepFind(d);
}
const KEYS = {
  "'{'": () => typeOpen("{"), "'['": () => typeOpen("["), "'('": () => typeOpen("("), "'$'": () => typeOpen("$"),
  "'}'": () => typeCloseBrace(), "']'": () => (typeEscapedClose("]") ? undefined : CodeMirror.Pass),
  "')'": () => (typeEscapedClose(")") ? undefined : CodeMirror.Pass), Enter: () => enter(),
  Tab: () => (E.complete ? CodeMirror.Pass : SNIP.fields.length ? (nextField(1) ? undefined : CodeMirror.Pass) : CodeMirror.Pass),
  "Shift-Tab": () => (SNIP.fields.length && SNIP.i > 0 ? (nextField(-1), undefined) : CodeMirror.Pass),
  Esc: () => { if (SNIP.fields.length) { clearSnippet(); return undefined; } return CodeMirror.Pass; },
  [`${MOD}-D`]: "deleteLine", [`Shift-${MOD}-D`]: () => duplicate(),
  "Alt-Up": () => moveLines(-1), "Alt-Down": () => moveLines(1), "Shift-Alt-Up": () => copyLines(-1), "Shift-Alt-Down": () => copyLines(1),
  [`Shift-${MOD}-L`]: () => goToLine(), [`${MOD}-U`]: () => changeCase(true), [`Shift-${MOD}-U`]: () => changeCase(false),
  [`${MOD}-B`]: () => wrap("textbf"), [`${MOD}-I`]: () => wrap("textit"),
  "Ctrl-Space": () => { if (typeof maybeComplete === "function") maybeComplete(null, true); },
  [`${MOD}-G`]: () => findStep(1), [`Shift-${MOD}-G`]: () => findStep(-1),
  [`${MOD}-[`]: "indentLess", [`${MOD}-]`]: "indentMore",
};
cm.setOption("matchBrackets", true);
cm.setOption("autoCloseBrackets", PREF.autoClose ? { pairs: "()[]{}$$", closeBefore: ")]}$,;:.!? \t\n", explode: "" } : false);
cm.addKeyMap(KEYS);                       // after the brackets addon's keys, so these come first

/* ---------------------------------------------------------------- folding: sections and environments */
const LEVEL = { part: 0, chapter: 1, section: 2, subsection: 3, subsubsection: 4, paragraph: 5, subparagraph: 6 };
const HEAD = /^\s*\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\s*[[{]/;
const uncomment = (t) => t.replace(/(^|[^\\])%.*$/, "$1");
CodeMirror.registerHelper("fold", "latex", (c, start) => {
  const line = c.getLine(start.line), code = uncomment(line);
  const m = code.match(/\\begin\{([^{}]+)\}/);
  if (m && m[1] !== "document") {
    let depth = 0;
    for (let l = start.line, last = Math.min(c.lastLine(), start.line + 4000); l <= last; l++) {
      const t = uncomment(c.getLine(l)), re = /\\(begin|end)\{([^{}]+)\}/g;
      let x;
      while ((x = re.exec(t))) {
        if ((l === start.line && x.index < m.index) || x[2] !== m[1]) continue;
        depth += x[1] === "begin" ? 1 : -1;
        if (depth === 0) return l === start.line ? undefined : { from: CodeMirror.Pos(start.line, line.length), to: CodeMirror.Pos(l, x.index) };
      }
    }
    return undefined;
  }
  const h = code.match(HEAD);
  if (!h) return undefined;
  let end = c.lastLine();
  for (let l = start.line + 1; l <= c.lastLine(); l++) {
    const t = uncomment(c.getLine(l)), n = t.match(HEAD);
    if ((n && LEVEL[n[1]] <= LEVEL[h[1]]) || /\\end\{document\}|\\begin\{thebibliography\}|\\(?:bibliography|printbibliography|appendix)\b/.test(t)) { end = l - 1; break; }
  }
  while (end > start.line && !c.getLine(end).trim()) end--;
  return end > start.line ? { from: CodeMirror.Pos(start.line, line.length), to: CodeMirror.Pos(end, c.getLine(end).length) } : undefined;
});
cm.setOption("foldOptions", { rangeFinder: CodeMirror.fold.latex, widget: "⋯", minFoldSize: 1 });
cm.setOption("gutters", ["ed-marks", "CodeMirror-linenumbers", "CodeMirror-foldgutter"]);
cm.setOption("foldGutter", { rangeFinder: CodeMirror.fold.latex });

/* ---------------------------------------------------------------- the formula under the cursor, typeset */
let macroCache = { at: 0, macros: {} };
function groupAt(t, i) {                  // the {...} group starting at t[i] ("{"): [content, index after it]
  if (t[i] !== "{") return null;
  let d = 0;
  for (let k = i; k < t.length; k++) {
    if (t[k] === "\\") { k++; continue; }
    if (t[k] === "{") d++;
    else if (t[k] === "}" && --d === 0) return [t.slice(i + 1, k), k + 1];
  }
  return null;
}
function macrosFrom(text) {
  const out = {}, t = text.replace(/(^|[^\\])%.*$/gm, "$1");
  const re = /\\(?:re)?newcommand\*?\s*\{?\s*(\\[A-Za-z@]+)\s*\}?\s*(?:\[(\d)\])?\s*(?:\[[^\]]*\])?\s*|\\providecommand\*?\s*\{?\s*(\\[A-Za-z@]+)\s*\}?\s*(?:\[(\d)\])?\s*|\\DeclareMathOperator(\*?)\s*\{?\s*(\\[A-Za-z@]+)\s*\}?\s*|\\def\s*(\\[A-Za-z@]+)\s*/g;
  let m;
  while ((m = re.exec(t))) {
    const g = groupAt(t, re.lastIndex);
    if (!g) continue;
    if (m[6]) out[m[6]] = `\\operatorname${m[5] ? "*" : ""}{${g[0]}}`;
    else out[m[1] || m[3] || m[7]] = g[0];
  }
  return out;
}
async function macros() {
  if (Date.now() - macroCache.at < 15000) return macroCache.macros;
  let text = "";
  for (const [p, d] of E.docs) if (/\.(tex|sty)$/.test(p)) text += "\n" + d.doc.getValue();
  if (!E.docs.has("main.tex")) { try { text += "\n" + (await api(`${API}/file?path=main.tex`)).text; } catch (e) { /* none */ } }
  macroCache = { at: Date.now(), macros: macrosFrom(text) };
  return macroCache.macros;
}
function forKatex(r, tex) {
  tex = tex.replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\(?:nonumber|notag)\b/g, "").replace(/(^|[^\\])%.*$/gm, "$1");
  const k = r.kind.replace("*", "");
  if (/^(align|flalign|eqnarray)$/.test(k)) return `\\begin{aligned}${tex}\\end{aligned}`;
  if (/^(gather|multline)$/.test(k)) return `\\begin{gathered}${tex}\\end{gathered}`;
  if (k === "alignat") { const g = groupAt(tex.trim(), 0); return g ? `\\begin{alignedat}{${g[0]}}${tex.trim().slice(g[1])}\\end{alignedat}` : tex; }
  return tex;
}
const PV = { el: null, t: null, key: "" };
function hidePreview() { if (PV.el) PV.el.hidden = true; PV.key = ""; }
async function showPreview() {
  if (!PREF.preview || !cm.hasFocus() || cm.somethingSelected() || typeof katex === "undefined" || !E.current || !/\.tex$/.test(E.current)) return hidePreview();
  const i = cm.indexFromPos(cm.getCursor());
  const r = regionsNow().find((x) => i >= x.ia && i <= x.ib);
  if (!r) return hidePreview();
  const text = cm.getValue(), tex = text.slice(r.ia, r.ib);
  if (!tex.trim()) return hidePreview();
  if (!PV.el) { PV.el = document.createElement("div"); PV.el.className = "eq-preview"; PV.el.setAttribute("role", "tooltip"); document.body.appendChild(PV.el); }
  const key = `${r.a}:${tex}`;
  if (key !== PV.key) {
    PV.key = key;
    const mac = await macros();
    try {
      PV.el.innerHTML = katex.renderToString(forKatex(r, tex), { displayMode: r.display, throwOnError: true, strict: "ignore", trust: false, macros: { ...mac } });
      PV.el.classList.remove("bad");
    } catch (e) {
      PV.el.innerHTML = `<span class="eq-err">The preview cannot show this formula${e && e.message ? ": " + esc(String(e.message).replace(/^KaTeX parse error:\s*/, "")).slice(0, 140) : ""}. Compile to see it in the PDF.</span>`;
      PV.el.classList.add("bad");
    }
  }
  const top = cm.charCoords(cm.posFromIndex(r.a), "window"), bottom = cm.charCoords(cm.posFromIndex(r.b), "window");
  const host = $("#cmHost").getBoundingClientRect();
  PV.el.hidden = false;
  PV.el.style.maxWidth = `${Math.max(240, host.width - 30)}px`;
  const h = PV.el.offsetHeight, w = PV.el.offsetWidth;
  let y = top.top - h - 8;
  if (y < host.top + 4) y = bottom.bottom + 8;
  PV.el.style.top = `${Math.max(4, y)}px`;
  PV.el.style.left = `${Math.max(host.left + 8, Math.min(top.left - 12, host.right - w - 12))}px`;
}
function previewSoon() { clearTimeout(PV.t); PV.t = setTimeout(showPreview, 140); }
cm.on("cursorActivity", previewSoon);
cm.on("change", () => { if (PV.el && !PV.el.hidden) previewSoon(); });
cm.on("blur", () => setTimeout(() => { if (!cm.hasFocus()) hidePreview(); }, 120));
cm.on("scroll", () => { if (PV.el && !PV.el.hidden) previewSoon(); });
cm.on("swapDoc", () => { clearSnippet(); hidePreview(); });
