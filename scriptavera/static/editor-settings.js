/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* What Overleaf's menus offer, here:
   - Settings: the LaTeX program (pdfLaTeX, XeLaTeX, LuaLaTeX), the main document, spell check, suggestions,
     closing brackets, the code check, the formula preview, Vim or Emacs keys, font size, font, line height, light or
     dark editor. The editor's own choices are kept in this browser; the compile ones in the project.
   - The Recompile menu: compile by itself, normal or fast (draft: images left out), stop at the first error or
     go on, recompile from scratch.
   - Layout: code and PDF side by side, the code only, the PDF only.
   - Search in the whole project (Ctrl+Shift+F), and the word count. */

Object.assign(PREF, Object.assign({ codeCheck: true, spell: true, keys: "default", fontSize: 13.5, font: "default", lineHeight: "normal",
  theme: "light" }, store.get("prefs", {})));
const savePrefs = () => store.set("prefs", PREF);
E.settings = null;
const EXTRA = { ...(cm.getOption("extraKeys") || {}) };
const ICONS2 = (() => {
  const s = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${d}</svg>`;
  return {
    gear: s('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    layout: s('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>'),
    search: s('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
  };
})();

/* ---------------------------------------------------------------- how the editor looks and behaves */
const FONTS = { default: "var(--mono)", lucida: '"Lucida Console", "Lucida Sans Typewriter", monospace', courier: '"Courier New", Courier, monospace' };
const HEIGHTS = { compact: 1.35, normal: 1.55, wide: 1.85 };
function applyLook() {
  const root = $("#ed");
  root.style.setProperty("--cm-size", `${PREF.fontSize}px`);
  root.style.setProperty("--cm-font", FONTS[PREF.font] || FONTS.default);
  root.style.setProperty("--cm-height", String(HEIGHTS[PREF.lineHeight] || HEIGHTS.normal));
  root.classList.toggle("dark", PREF.theme === "dark");
  root.dataset.layout = PREF.layout || "both";
  cm.refresh();
}
function applyKeys() {
  const k = PREF.keys || "default";
  cm.setOption("keyMap", k);
  cm.removeKeyMap(KEYS);
  if (k === "default") cm.addKeyMap(KEYS);
  const extra = { ...EXTRA };
  if (k === "emacs") for (const key of ["Ctrl-F", "Ctrl-H", "Ctrl-K", "Ctrl-S"]) delete extra[key];
  cm.setOption("extraKeys", extra);
}
function applyBehaviour() {
  cm.setOption("autoCloseBrackets", PREF.autoClose ? { pairs: "()[]{}$$", closeBefore: ")]}$,;:.!? \t\n", explode: "" } : false);
  if (!PREF.preview) hidePreview();
  for (const p of E.docs.keys()) lintNow(p);
  markIssues();
}
/* spell check: the browser's own, as Overleaf uses it; commands, formulas and comments are left out */
cm.on("renderLine", (c, line, el) => {
  if (!PREF.spell) return;
  for (const sp of el.querySelectorAll(".cm-keyword, .cm-tag, .cm-atom, .cm-math, .cm-math-cmd, .cm-math-delim, .cm-comment, .cm-string, .cm-bracket"))
    sp.spellcheck = false;
});

/* ---------------------------------------------------------------- settings */
async function loadSettings() {
  try { E.settings = await api(`${API}/settings`); } catch (e) { /* the defaults */ }
}
function settingsDialog() {
  const S = E.settings || { compiler: E.info && E.info.engine, engines: [], main: "main.tex", mains: ["main.tex"] };
  const sel = (id, options, value) => `<select id="${id}">${options.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const onoff = (id, v) => sel(id, [["1", "On"], ["0", "Off"]], v ? "1" : "0");
  const row = (label, ctl, note = "") => `<div class="set-row"><label>${label}${note ? `<span class="muted">${note}</span>` : ""}</label>${ctl}</div>`;
  const names = { pdflatex: "pdfLaTeX", xelatex: "XeLaTeX", lualatex: "LuaLaTeX" };
  modal(`<header><h2>Settings</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body settings">
    <h3>Compiling</h3>
    ${row("Compiler", S.engines.length ? sel("setCompiler", S.engines.map((e) => [e, names[e] || e]), S.compiler) : `<span class="muted">${esc(S.compiler || "No LaTeX installed")}</span>`,
      "XeLaTeX and LuaLaTeX read Unicode and use system fonts")}
    ${row("Main document", sel("setMain", S.mains.map((m) => [m, m]), S.main))}
    <h3>Editor</h3>
    ${row("Spell check", onoff("setSpell", PREF.spell), "your browser's dictionary; applies after the page reloads")}
    ${row("Suggestions while typing", onoff("setComplete", PREF.autoComplete))}
    ${row("Close brackets and $ by themselves", onoff("setClose", PREF.autoClose))}
    ${row("Code check", onoff("setCheck", PREF.codeCheck), "warnings while you type")}
    ${row("Formula preview", onoff("setPreview", PREF.preview))}
    ${row("Keys", sel("setKeys", [["default", "Standard"], ["vim", "Vim"], ["emacs", "Emacs"]], PREF.keys))}
    <h3>Appearance</h3>
    ${row("Font size", sel("setSize", [11, 12, 13, 13.5, 14, 15, 16, 18, 20].map((n) => [n, `${n} px`]), PREF.fontSize))}
    ${row("Font", sel("setFont", [["default", "Standard monospace"], ["lucida", "Lucida Console"], ["courier", "Courier New"]], PREF.font))}
    ${row("Line height", sel("setHeight", [["compact", "Compact"], ["normal", "Normal"], ["wide", "Wide"]], PREF.lineHeight))}
    ${row("Editor theme", sel("setTheme", [["light", "Light"], ["dark", "Dark"]], PREF.theme))}
  </div>`);
  $(".settings").addEventListener("change", async (ev) => {
    const id = ev.target.id, v = ev.target.value;
    if (id === "setCompiler" || id === "setMain") {
      try {
        E.settings = await api(`${API}/settings`, { method: "POST", body: id === "setCompiler" ? { compiler: v } : { main: v } });
        toast(id === "setCompiler" ? `Compiling with ${names[v] || v} from now on.` : `${v} is the main document now.`);
        if (E.info) E.info.engine = E.settings.compiler;
        compile();
      } catch (e) { toast(e.message, true); }
      return;
    }
    const map = { setSpell: ["spell", v === "1"], setComplete: ["autoComplete", v === "1"], setClose: ["autoClose", v === "1"], setCheck: ["codeCheck", v === "1"],
      setPreview: ["preview", v === "1"], setKeys: ["keys", v], setSize: ["fontSize", Number(v)], setFont: ["font", v], setHeight: ["lineHeight", v],
      setTheme: ["theme", v] }[id];
    if (!map) return;
    PREF[map[0]] = map[1];
    savePrefs();
    if (id === "setKeys") applyKeys();
    else if (/^set(Size|Font|Height|Theme)$/.test(id)) applyLook();
    else applyBehaviour();
    if (id === "setSpell") toast("Spell check changes after the page reloads.");
  });
}

/* ---------------------------------------------------------------- the Recompile menu */
function compileMenu(anchor) {
  const S = E.settings || {};
  const tick = (on) => `<span class="tick">${on ? "✓" : ""}</span>`;
  menu(anchor, `<div class="menu-h">Auto compile</div>
    <button data-act="cm-auto" data-v="1">${tick($("#autoCompile").checked)}On<span class="sub">Compiles a moment after you stop typing</span></button>
    <button data-act="cm-auto" data-v="0">${tick(!$("#autoCompile").checked)}Off</button><hr>
    <div class="menu-h">Compile mode</div>
    <button data-act="cm-draft" data-v="0">${tick(!S.draft)}Normal</button>
    <button data-act="cm-draft" data-v="1">${tick(S.draft)}Fast [draft]<span class="sub">Images are left out: quicker while writing</span></button><hr>
    <div class="menu-h">When there is an error</div>
    <button data-act="cm-halt" data-v="1">${tick(S.halt)}Stop on the first error</button>
    <button data-act="cm-halt" data-v="0">${tick(!S.halt)}Try to compile anyway</button><hr>
    <button data-act="cm-fresh">Recompile from scratch<span class="sub">Deletes what earlier runs left behind (.aux, .bbl …) and compiles</span></button>`);
}
async function setCompile(body) {
  closeMenu();
  try { E.settings = await api(`${API}/settings`, { method: "POST", body }); compile(); }
  catch (e) { toast(e.message, true); }
}
async function compileFresh() {
  closeMenu();
  if (E.compiling) return;
  await saveAll();
  E.compiling = true;
  $("#compileBtn").disabled = true; $("#compileBtn").textContent = "Compiling…";
  setStatus("Compiling from scratch…");
  try {
    const r = await api(`${API}/compile`, { method: "POST", body: { fresh: true } });
    E.build = r.build; showBuild();
    if (r.build.has_pdf) await loadPdf(r.build.build);
    loadOutline(); loadCommands();
  } catch (e) { setStatus("Compile failed", "bad"); toast(e.message, true); }
  finally { E.compiling = false; $("#compileBtn").disabled = false; $("#compileBtn").textContent = "Recompile"; }
}

/* ---------------------------------------------------------------- layout */
function layoutMenu(anchor) {
  const now = PREF.layout || "both";
  const item = (v, t, sub) => `<button data-act="lay" data-v="${v}">${now === v ? '<span class="tick">✓</span>' : '<span class="tick"></span>'}${t}<span class="sub">${sub}</span></button>`;
  menu(anchor, item("both", "Code and PDF", "Side by side") + item("code", "Code only", "The PDF hidden") + item("pdf", "PDF only", "The code hidden"));
}
function setLayout(v) {
  closeMenu();
  PREF.layout = v; savePrefs(); applyLook();
  setTimeout(() => { cm.refresh(); if (PDF.mode === "fit") setZoom("fit"); }, 60);
}

/* ---------------------------------------------------------------- search in the whole project */
const PS = { t: null, n: 0 };
function projectSearch() {
  $("#side").classList.add("searching");
  $("#psearch").hidden = false;
  const sel = cm.getSelection();
  if (sel && !sel.includes("\n")) $("#psText").value = sel;
  $("#psText").focus(); $("#psText").select();
  runProjectSearch();
}
function closeProjectSearch() { $("#side").classList.remove("searching"); $("#psearch").hidden = true; cm.focus(); }
async function runProjectSearch() {
  const q = $("#psText").value, n = ++PS.n;
  if (!q) { $("#psResults").innerHTML = ""; $("#psCount").textContent = ""; return; }
  await saveAll();
  let r;
  try {
    r = await api(`${API}/search?q=${encodeURIComponent(q)}&case=${$("#psCase").checked ? 1 : 0}&regex=${$("#psRegex").checked ? 1 : 0}&word=${$("#psWord").checked ? 1 : 0}`);
  } catch (e) { if (n === PS.n) { $("#psCount").textContent = e.message; $("#psResults").innerHTML = ""; } return; }
  if (n !== PS.n) return;
  const by = new Map();
  for (const m of r.matches) { if (!by.has(m.path)) by.set(m.path, []); by.get(m.path).push(m); }
  $("#psCount").textContent = r.matches.length ? `${r.matches.length}${r.more ? "+" : ""} result${r.matches.length === 1 ? "" : "s"} in ${by.size} file${by.size === 1 ? "" : "s"}` : "No result";
  $("#psResults").innerHTML = [...by].map(([path, ms]) => `<div class="ps-file"><div class="ps-name">${ICON.file}<span>${esc(path)}</span><i>${ms.length}</i></div>
    ${ms.map((m) => {
      const a = Math.max(0, m.ch - 40), t = m.text;
      return `<button class="ps-hit" data-act="pshit" data-path="${esc(path)}" data-line="${m.line}" data-ch="${m.ch}" data-len="${m.len}"><span class="ln">${m.line}</span>
        <span class="tx">${a ? "…" : ""}${esc(t.slice(a, m.ch))}<mark>${esc(t.slice(m.ch, m.ch + m.len))}</mark>${esc(t.slice(m.ch + m.len, m.ch + m.len + 80))}</span></button>`;
    }).join("")}</div>`).join("");
}
async function openHit(t) {
  await openFile(t.dataset.path, Number(t.dataset.line));
  const l = Number(t.dataset.line) - 1, ch = Number(t.dataset.ch), len = Number(t.dataset.len);
  cm.setSelection({ line: l, ch }, { line: l, ch: ch + len });
  cm.scrollIntoView({ line: l, ch }, 120);
  $$("#psResults .ps-hit").forEach((b) => b.classList.toggle("on", b === t));
}

/* ---------------------------------------------------------------- word count */
async function wordCount() {
  closeMenu();
  await saveAll();
  let r;
  try { r = await api(`${API}/wordcount`); } catch (e) { return toast(e.message, true); }
  const line = (k, v) => `<div class="proof-line"><b>${k}</b><span>${v}</span></div>`;
  modal(`<header><h2>Word count</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    ${line("Words in the text", r.words)}${line("Words in headings", r.headers)}${line("Words in captions", r.captions)}
    ${line("Formulas in the text", r.inline_math)}${line("Displayed formulas", r.display_math)}
    <p class="muted" style="margin-top:12px">Counted in the files LaTeX reads, from \\begin{document} to \\end{document}; comments, labels, references and the bibliography are left out, as in Overleaf.</p></div>`);
}

/* ---------------------------------------------------------------- wiring */
(function addControls() {
  const compile = $(".ed-compile");
  const drop = document.createElement("button");
  drop.className = "btn primary cm-drop"; drop.dataset.act = "cm-menu"; drop.title = "Compile options"; drop.setAttribute("aria-label", "Compile options"); drop.textContent = "▾";
  $("#compileBtn").after(drop);
  compile.classList.add("has-drop");
  const acts = $(".ed-actions");
  const mk = (act, icon, title) => { const b = document.createElement("button"); b.className = "btn quiet icon"; b.dataset.act = act; b.title = title; b.setAttribute("aria-label", title); b.innerHTML = icon; return b; };
  acts.prepend(mk("layout", ICONS2.layout, "Layout: code and PDF, code only, PDF only"));
  acts.append(mk("settings", ICONS2.gear, "Settings"));
  const tools = $(".side-tools");
  const sb = document.createElement("button"); sb.dataset.act = "psearch"; sb.title = "Search the whole project (Ctrl+Shift+F)"; sb.setAttribute("aria-label", sb.title); sb.innerHTML = ICONS2.search;
  tools.prepend(sb);
  const panel = document.createElement("div");
  panel.className = "psearch"; panel.id = "psearch"; panel.hidden = true;
  panel.innerHTML = `<div class="side-head"><span>Search in the project</span><span class="side-tools"><button data-act="psclose" title="Back to the files" aria-label="Back to the files">✕</button></span></div>
    <div class="ps-box"><input id="psText" placeholder="Search all the files" spellcheck="false" autocomplete="off">
    <div class="ps-opts"><label title="Match case"><input type="checkbox" id="psCase"> Aa</label><label title="Regular expression"><input type="checkbox" id="psRegex"> .*</label>
    <label title="Whole word"><input type="checkbox" id="psWord"> Word</label></div><div class="ps-count" id="psCount"></div></div>
    <div class="ps-results" id="psResults"></div>`;
  $("#side").prepend(panel);
  $("#psText").addEventListener("input", () => { clearTimeout(PS.t); PS.t = setTimeout(runProjectSearch, 300); });
  $("#psText").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); runProjectSearch(); } if (ev.key === "Escape") closeProjectSearch(); });
  for (const id of ["psCase", "psRegex", "psWord"]) $("#" + id).addEventListener("change", runProjectSearch);
})();
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-act]");
  if (!t || t.tagName === "FORM") return;
  const a = t.dataset.act;
  if (!/^(settings|cm-menu|cm-auto|cm-draft|cm-halt|cm-fresh|layout|lay|psearch|psclose|pshit|wordcount)$/.test(a)) return;
  ev.preventDefault();
  if (t.closest("#menu") && !/^(cm-menu|layout)$/.test(a)) closeMenu();
  switch (a) {
    case "settings": closeMenu(); settingsDialog(); break;
    case "cm-menu": compileMenu(t); break;
    case "cm-auto": $("#autoCompile").checked = t.dataset.v === "1"; store.set("auto", $("#autoCompile").checked); break;
    case "cm-draft": setCompile({ draft: t.dataset.v === "1" }); break;
    case "cm-halt": setCompile({ halt: t.dataset.v === "1" }); break;
    case "cm-fresh": compileFresh(); break;
    case "layout": layoutMenu(t); break;
    case "lay": setLayout(t.dataset.v); break;
    case "psearch": projectSearch(); break;
    case "psclose": closeProjectSearch(); break;
    case "pshit": openHit(t); break;
    case "wordcount": wordCount(); break;
    default: break;
  }
});
document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && (ev.key === "F" || ev.key === "f")) { ev.preventDefault(); projectSearch(); }
});
applyLook();
applyKeys();
loadSettings();
