/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* Scriptavera: interface. Plain JavaScript, no build step.
   State lives in S; render() redraws the rail, the top bar and the main area. */

const S = {
  meta: null, projects: [], pid: null, b: null, view: "home", follow: true,
  logs: [], es: null, form: null, gate: null, review: null, decide: null, jr: null, tab: "paper", edit: null,
  nowPage: null, busy: "", domQuery: "", domOpen: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
const inner = (html) => html.replace(/^<p>|<\/p>$/g, "");
/* Line icons, drawn for this app (stroke = currentColor). */
const ICON = {
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9 14l3 3 3-3"/></svg>',
  more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5.5" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.7" fill="currentColor" stroke="none"/></svg>',
  home: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 11.5 12 4.5l8.5 7"/><path d="M6 10v9.5h12V10"/><path d="M10 19.5v-5h4v5"/></svg>',
  back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 5.5 8 12l6.5 6.5"/></svg>',
  sliders: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9M18 7h2M4 17h3M12 17h8"/><circle cx="15.5" cy="7" r="2.3"/><circle cx="9.5" cy="17" r="2.3"/></svg>',
  pages: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3.5h9.5a1.5 1.5 0 0 1 1.5 1.5v12.5"/><rect x="5" y="6.5" width="11" height="14" rx="1.5"/><path d="M10.5 10.5v6M7.5 13.5h6"/></svg>',
};

function newForm() {
  return { domains: [], subarea: "", hints: "", problem: "", keywords: "", keypapers: "", title: "", refs: "", files: [],
    authors: [{ name: "", affiliation: "", email: "", orcid: "" }], recorder: "0", recorderName: "", declare: false,
    journal: null, jq: "", jresults: null, jmsg: "" };
}
function newJr() { return { q: "", results: null, message: "", searching: false, guide: null, guideBusy: false, saving: "" }; }

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, init); }
  catch (e) { throw new Error("The app is not responding. Check that the Scriptavera window is still open."); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok || !data || data.ok === false) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = bad ? "bad" : ""; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, bad ? 8000 : 3400);
}

/* ------------------------------------------------------------ mathematics */
/* A shorthand is given to KaTeX only if KaTeX does not know the command itself. (A paper built by an earlier version
   could carry \cancel or \phi declared as plain words, which the preview then printed as "cancel" and "phi".) */
const KATEX_KNOWS = {};
function katexKnows(name) {
  if (!(name in KATEX_KNOWS)) {
    try { katex.renderToString(name + "{x}{y}", { throwOnError: true, strict: "ignore" }); KATEX_KNOWS[name] = true; }
    catch (e) { KATEX_KNOWS[name] = !/Undefined control sequence/i.test(String(e && e.message)); }
  }
  return KATEX_KNOWS[name];
}
function macros() {
  const all = { ...(S.meta ? S.meta.macros : {}), ...((S.b && S.b.katex_macros) || {}), ...((S.b && S.b.paper && S.b.paper.macros) || {}) };
  const out = {};
  for (const [k, v] of Object.entries(all)) if (!(/^\\operatorname\{/.test(v) && katexKnows(k))) out[k] = v;
  return out;
}
/* A displayed formula wider than its column is scaled down to fit, never given a scrollbar: the PDF's text is
   wider than the preview's column, so a formula that fits on the page is shown whole here too. */
function fitMath(root) {
  $$(".math.display", root).forEach((el) => {
    el.style.fontSize = "";
    const w = el.clientWidth;
    if (!w) return;                                   // hidden: fitted when shown
    const k = el.firstElementChild;
    const need = k ? k.scrollWidth : el.scrollWidth;
    if (need > w + 1) el.style.fontSize = Math.max(0.45, (w - 2) / need).toFixed(3) + "em";
  });
}
let fitTimer = null;
const refit = (ms) => { clearTimeout(fitTimer); fitTimer = setTimeout(() => fitMath(document), ms); };
window.addEventListener("resize", () => refit(150));
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => refit(0));       // widths change once KaTeX's fonts load
new MutationObserver((ms) => { if (ms.some((m) => m.addedNodes.length)) refit(60); }).observe(document.documentElement, { childList: true, subtree: true });
function tex(src, display) {
  try { return katex.renderToString(String(src || ""), { displayMode: !!display, throwOnError: false, strict: "ignore", output: "html", macros: macros() }); }
  catch (e) { return `<code>${esc(src)}</code>`; }
}
function renderMath(root) {
  $$(".math[data-tex]", root).forEach((el) => {
    if (el.dataset.done) return;
    try { katex.render(el.dataset.tex, el, { displayMode: el.classList.contains("display"), throwOnError: false, strict: "ignore", output: "html", macros: macros() }); }
    catch (e) { el.textContent = el.dataset.tex; }
    el.dataset.done = "1";
  });
  fitMath(root);
}
const squash = (s) => String(s || "").replace(/\$/g, "").replace(/\s+/g, "");

/* Text with $...$ and $$...$$ (a transcript, a definition) to HTML. */
function mixed(text, marks) {
  marks = (marks || []).filter(Boolean);
  const sq = marks.map(squash).filter((m) => m.length > 2);
  const parts = String(text || "").split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g);
  let out = "";
  for (const part of parts) {
    if (!part) continue;
    const disp = part.startsWith("$$") && part.endsWith("$$") && part.length > 4;
    const inl = !disp && part.startsWith("$") && part.endsWith("$") && part.length > 2;
    if (disp || inl) {
      const body = disp ? part.slice(2, -2) : part.slice(1, -1);
      const hit = sq.some((m) => squash(body).includes(m) || (m.includes(squash(body)) && squash(body).length > 5));
      out += `<span class="math${disp ? " display" : ""}${hit ? " unc" : ""}">${tex(body, disp)}</span>`;
    } else {
      let h = esc(part).replace(/`([^`\n]{1,60})`/g, (m0, t) => `<span class="math">${tex(t.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"))}</span>`);
      for (const m of marks) {
        const em = esc(m);
        if (em.length > 2 && h.includes(em)) h = h.split(em).join(`<mark>${em}</mark>`);
      }
      h = h.replace(/\[FIGURE(?:\s+f\d+)?:\s*([\s\S]*?)\]/g, '<span class="fig">Figure: $1</span>')
        .replace(/\[MARGIN:\s*([\s\S]*?)\]/g, '<span class="fig">Margin note: $1</span>')
        .replace(/\[\?([^\]|]*)\|([^\]]*)\?\]/g, "<mark>$1</mark>")
        .replace(/\[\?\]/g, "<mark>[unreadable]</mark>")
        .replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
      out += h;
    }
  }
  return `<p>${out}</p>`;
}

/* A LaTeX passage as the writer produces it (a statement, a proof, the introduction) to HTML. */
function labelName(label, inPaper) {
  /* The name a reader sees for a label: "Lemma 3.2". In the first draft the numbering is the draft's;
     with inPaper it is the numbering of the final paper, which a new lemma may have shifted. */
  const v = (((S.b || {}).revision || {}).verify || {}).results || {};
  const blk = (((S.b || {}).paper || {}).blocks || []).find((x) => x.label === label);
  const final = blk && blk.num ? `${(blk.env || "").replace(/^./, (c) => c.toUpperCase())} ${blk.num}` : "";
  if (inPaper) return final || (v[label] ? v[label].name : "");
  return v[label] ? v[label].name : final;
}
function labelNumber(label) {
  if (label.startsWith("fig:")) { const i = ((S.b || {}).figures || []).findIndex((f) => "fig:" + f.id === label); return i >= 0 ? String(i + 1) : "?"; }
  const n = labelName(label);
  return n ? n.split(" ").pop() : (/^res:S\d+$/.test(label) ? "(the new one)" : "?");
}
function named(text, inPaper) {
  /* Labels such as res:R3 are for the machine; a reader is shown "Theorem 3.3". */
  return String(text || "").replace(/\b(?:res|def):[A-Za-z0-9_.\-]*[A-Za-z0-9]/g, (l) => labelName(l, inPaper) || (/^res:S\d+$/.test(l) ? "the new lemma" : l));
}
function citeRef(key) {
  /* How a reader sees a citation before the paper is set: the authors and the year, linked to the paper. */
  const p = ((S.b || {}).library || []).find((x) => x.key === key);
  if (!p) return { text: key, href: "", title: "" };
  const fam = (p.authors || []).map((a) => (typeof a === "string" ? a : a.family || a.given)).filter(Boolean);
  const who = !fam.length ? (p.title || key).split(/\s+/).slice(0, 4).join(" ") + "…"
    : fam.length === 1 ? fam[0] : fam.length === 2 ? `${fam[0]} and ${fam[1]}`
    : fam.length === 3 ? `${fam[0]}, ${fam[1]} and ${fam[2]}` : `${fam[0]} et al.`;
  const href = p.doi ? `https://doi.org/${p.doi}` : p.arxiv ? `https://arxiv.org/abs/${p.arxiv}`
    : p.url && /^https?:\/\//i.test(p.url) ? p.url : scholar(p.title || key);
  return { text: who + (p.year ? ", " + p.year : ""), href, title: p.title || "" };
}
function citeHtml(keys, opt) {
  const parts = keys.split(",").map((k) => k.trim()).filter(Boolean).map((k) => {
    const c = citeRef(k);
    return c.href ? `<a class="cite-link" href="${esc(c.href)}" target="_blank" rel="noopener" title="${esc(c.title)}">${esc(c.text)}</a>` : esc(c.text);
  });
  return `<span class="cite-chip">[${parts.join("; ")}${opt ? ", " + esc(opt) : ""}]</span>`;
}
const PROSE_CMDS = /^(?:cite[tp]?|eqref|ref|label|insertfigure|emph|textit|textbf|textrm|text|begin|end|item|qedhere|noindent|medskip|smallskip|bigskip|par|url|href)$/;
function strayMath(t) {
  /* Mathematics a model wrote without dollar signs ("the set \{2, 3\}", "\pi(x)", "p^\alpha"): typeset it
     anyway, so that no LaTeX source reaches the reader. */
  const keep = [];
  const put = (src) => { keep.push(tex(src)); return `\u0000${keep.length - 1}\u0000`; };
  t = t.replace(/\\\{[^{}$\n]*?\\\}/g, put);
  t = t.replace(/(?:\\[A-Za-z]+|[A-Za-z0-9)\]|])(?:[_^](?:\{[^{}$\n]*\}|\\[A-Za-z]+|[A-Za-z0-9]))+/g,
    (m) => (/^\\([A-Za-z]+)/.test(m) && PROSE_CMDS.test(m.slice(1).match(/^[A-Za-z]+/)[0])) ? m : put(m));
  t = t.replace(/\\([A-Za-z]+)((?:\{[^{}$\n]*\})*)/g, (m, name) => PROSE_CMDS.test(name) ? m : put(m));
  return [t, keep];
}
function proseLite(t) {
  const [t2, keep] = strayMath(String(t || ""));
  let h = esc(t2);
  h = h.replace(/\\cite[tp]?\s*(?:\[([^\]]*)\])?\s*\{([^{}]+)\}/g, (m0, opt, keys) => citeHtml(keys, opt));
  h = h.replace(/\\(?:eq)?ref\s*\{([^{}]+)\}/g, (m0, l) => esc(labelNumber(l)));
  h = h.replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\insertfigure\s*\{([^{}]+)\}/g, '<span class="fig">The figure is placed here.</span>');
  for (let i = 0; i < 2; i++) h = h.replace(/\\(?:emph|textit)\{([^{}]*)\}/g, "<em>$1</em>").replace(/\\textbf\{([^{}]*)\}/g, "<strong>$1</strong>").replace(/\\(?:textrm|text)\{([^{}]*)\}/g, "$1");
  h = h.replace(/\\(?:begin|end)\{(?:itemize|enumerate|center|quote)\}/g, "").replace(/\\item\b/g, "<br>• ");
  h = h.replace(/\\(?:qedhere|noindent|medskip|smallskip|bigskip|par)\b/g, "").replace(/~/g, "&nbsp;");
  h = h.replace(/``/g, "“").replace(/&#39;&#39;/g, "”").replace(/---/g, "—").replace(/--/g, "–").replace(/\\([%&#_$])/g, "$1").replace(/\\ /g, " ");
  h = h.replace(/\u0000(\d+)\u0000/g, (m, i) => `<span class="math">${keep[+i]}</span>`);
  return h.replace(/\n\s*\n/g, "</p><p>");
}
/* Text written by a model (a summary, a remark, a title): shown with its mathematics typeset. */
const rich = (s) => inner(latexLite(String(s || "")));
function latexLite(src) {
  const s = String(src || "");
  const re = /\\\[[\s\S]+?\\\]|\$\$[\s\S]+?\$\$|\\begin\{(align\*?|equation\*?|gather\*?|multline\*?|eqnarray\*?|flalign\*?|alignat\*?)\}[\s\S]+?\\end\{\1\}|\\\([\s\S]+?\\\)|\$[^$\n]+?\$/g;
  let out = "", pos = 0, m;
  while ((m = re.exec(s))) {
    out += proseLite(s.slice(pos, m.index));
    const t = m[0];
    if (t.startsWith("\\[") || t.startsWith("$$")) out += `<span class="math display">${tex(t.slice(2, -2).replace(/\\label\{[^{}]*\}/g, ""), true)}</span>`;
    else if (t.startsWith("\\begin")) out += `<span class="math display">${tex(t.replace(/\\label\{[^{}]*\}/g, "").replace(/eqnarray/g, "align").replace(/\{(flalign|multline)(\*?)\}/g, "{gather$2}"), true)}</span>`;
    else if (t.startsWith("\\(")) out += `<span class="math">${tex(t.slice(2, -2))}</span>`;
    else out += `<span class="math">${tex(t.slice(1, -1))}</span>`;
    pos = m.index + t.length;
  }
  return `<p>${out + proseLite(s.slice(pos))}</p>`;
}

function quoted(src) {
  /* A passage quoted from a proof. When it is a bare formula (taken from inside a display, so without its
     delimiters) it is shown as mathematics; otherwise as text. */
  const t = String(src || "").trim();
  const bare = !/\$|\\\[|\\begin\{/.test(t) && /\\[A-Za-z]+|[_^=<>]/.test(t);
  const words = (t.replace(/\\[A-Za-z]+/g, " ").match(/[A-Za-z]{4,}/g) || []).length;
  return bare && words <= 4 ? `<p><span class="math display">${tex(t.replace(/[.,;]\s*$/, ""), true)}</span></p>` : latexLite(t);
}

/* ------------------------------------------------------------- data flow */
function clock(t) {
  if (!t) return "";
  const d = new Date(t * 1000), days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(new Date().setHours(0, 0, 0, 0))) / 86400000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) + (days === 1 ? " tomorrow" : days > 1 ? " on " + d.toLocaleDateString([], { weekday: "long" }) : "");
}
function until(t) {
  const s = Math.max(0, Math.round(t - Date.now() / 1000));
  if (s < 90) return `(in ${s} seconds)`;
  if (s < 5400) return `(in ${Math.round(s / 60)} minutes)`;
  return `(in ${(s / 3600).toFixed(1)} hours)`;
}
setInterval(() => { document.querySelectorAll("[data-until]").forEach((el) => { el.textContent = until(+el.dataset.until); }); }, 5000);
function stageInfo(id) { return S.meta.stages.find((s) => s.id === id); }
function st(id) { return (S.b && S.b.state.stages[id]) || { status: "pending" }; }
function running() { return !!(S.b && S.b.state.running); }
function domainNames(ids) { return (ids || []).map((i) => (S.meta.domains.find((d) => d.id === i) || {}).name).filter(Boolean); }
function libPaper(key) { return ((S.b && S.b.library) || []).find((p) => p.key === key); }

function autoView() {
  const ids = S.meta.stages.map((s) => s.id);
  if (S.b && S.b.adding && st("review").status !== "done") return "review";     // pages added after the first check
  const waiting = ids.find((i) => st(i).status === "waiting");
  if (waiting) return waiting;
  const run = ids.find((i) => st(i).status === "running");
  if (run) return run;
  const err = ids.find((i) => st(i).status === "paused" || st(i).status === "error");
  if (err) return err;
  if (ids.every((i) => st(i).status === "done")) return "assemble";
  return ids.find((i) => st(i).status !== "done") || "assemble";
}

let refreshTimer = null;
function refreshSoon(ms = 250) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, ms); }
async function refresh() {
  if (!S.pid) return;
  const pid = S.pid;
  try {
    const data = await api(`/api/projects/${pid}`);
    if (S.pid !== pid) return;
    const before = S.view;
    const live = !!S.b && S.view === autoView();     // looking at the step in progress, even if it was clicked by hand
    S.b = data;
    if (S.follow || live) {                            // ... then the screen moves on with the work
      S.view = autoView();
      if (!S.follow && S.view !== before) { S.follow = true; history.replaceState(null, "", `#p/${S.pid}`); }
    }
    if (S.view !== before) { S.edit = null; }
    render();
  } catch (e) { toast(e.message, true); }
}

function connect(pid) {
  if (S.es) { S.es.close(); S.es = null; }
  S.logs = [];
  const es = new EventSource(`/api/projects/${pid}/events`);
  S.es = es;
  es.onmessage = (m) => {
    if (S.pid !== pid) return;
    let e; try { e = JSON.parse(m.data); } catch (x) { return; }
    if (e.type === "log") {
      S.logs.push(e); if (S.logs.length > 400) S.logs.shift();
      drawLog();
      if (e.level === "error" || e.level === "gate" || e.level === "done") refreshSoon(150);
    } else if (e.type === "progress") {
      if (S.b && S.b.state.stages[e.stage]) S.b.state.stages[e.stage].progress = { done: e.done, total: e.total, note: e.note };
      const pm = /page (\d+)/.exec(e.note || "");
      if (pm) markPage(parseInt(pm[1], 10));
      drawProgress(e.stage);
    } else if (e.type === "state") {
      refreshSoon(120);
    } else if (e.type === "call") {
      if (S.b) { S.b.calls.push(e); }
      const pm = /page (\d+)/.exec(e.label || "");
      S.nowPage = null;
      if (pm) markPage(parseInt(pm[1], 10));
      drawCalls();
    }
  };
}
function markPage(n) { const el = $(`.strip .pg[data-n="${n}"]`); if (el) el.classList.add("read"); }

async function openProject(pid, view) {
  S.pid = pid; S.b = null; S.gate = null; S.review = null; S.decide = null; S.jr = null; S.edit = null; S.tab = "paper";
  S.follow = !view; S.view = view || "loading";
  render();
  connect(pid);
  await refresh();
  if (view) { S.view = view; render(); }
}

const APP_VERSION = "1.4";         /* must equal scriptavera/__init__.py; checked against the running program */
async function loadMeta() {
  S.meta = await api("/api/meta");
  /* The page is read from disk on every load, the program only when it starts. After an update the page can be
     new while an older program is still running: say so, plainly, instead of behaving oddly. */
  const el = $("#stale");
  if (el && S.meta.version !== APP_VERSION) {
    el.innerHTML = `<b>Scriptavera has been updated, but the old version is still running.</b> Close the black Scriptavera window
      (or every one, if there are several), then start <code>run_app.bat</code> again. Your work is saved.`;
    el.hidden = false;
  }
}
async function loadProjects() { S.projects = (await api("/api/projects")).projects; }

function route() {
  const m = /^#p\/([\w-]+)(?:\/(\w+))?/.exec(location.hash);
  if (m) {
    if (S.pid !== m[1]) openProject(m[1], m[2]);
    else if (m[2]) { S.view = m[2]; S.follow = false; render(); }
    else { S.follow = true; S.view = S.b ? autoView() : "loading"; render(); }
  } else {
    if (S.es) { S.es.close(); S.es = null; }
    S.pid = null; S.b = null; S.menu = null; S.view = location.hash === "#new" ? "new" : "home";
    loadProjects().then(() => { S.boardSig = JSON.stringify(S.projects); render(); });
    render();
    clearInterval(S.boardTimer);
    S.boardTimer = setInterval(() => {               // the board shows papers working in the background as they move on
      if (S.view !== "home" || S.pid) { clearInterval(S.boardTimer); return; }   // (the new-paper form is never redrawn under you)
      const quiet = () => !S.menu && $("#overlay").hidden;                        // nor an open menu or dialog
      if (!quiet()) return;
      loadProjects().then(() => {
        if (!(S.view === "home" && !S.pid && quiet())) return;
        const sig = JSON.stringify(S.projects);
        if (sig === S.boardSig) return;                 // nothing moved: the page, and the keyboard focus, stay as they are
        S.boardSig = sig;
        const f = document.activeElement, href = f && f.matches && f.matches(".pc-link") ? f.getAttribute("href") : null;
        render();
        if (href) { const again = $(`.pc-link[href="${CSS.escape(href)}"]`); if (again) again.focus(); }
      }).catch(() => {});
    }, 5000);
  }
}

/* ------------------------------------------------------------------ rail */
function drawRail() {
  /* Only inside a paper: the way home, the steps of this paper, and the settings. Starting or importing a paper is
     done from the home page. */
  const rail = $("#rail");
  if (!S.pid) { rail.innerHTML = ""; return; }
  let h = `<div class="rail-home"><button class="home-btn" data-act="home" title="All your papers, and where to start a new one">${ICON.home}<span>Home</span></button></div>`;
  if (S.b && !((S.b.state || {}).meta || {}).imported) {
    h += `<div class="rail-sec">`;
    for (const ph of S.meta.phases) {
      const mine = S.meta.stages.filter((s) => s.phase === ph.id);
      const done = mine.filter((s) => st(s.id).status === "done").length;
      h += `<div class="phase"><span>${esc(ph.name)}</span><span class="phase-n">${done} of ${mine.length}</span></div><ol class="steps">`;
      for (const s of mine) {
        const x = st(s.id);
        let note = "";
        if (x.status === "waiting") note = "Waiting for you";
        else if (x.status === "running") note = (x.progress && x.progress.note) || "Working";
        else if (x.status === "error") note = "Stopped; progress saved";
        else if (x.status === "paused") note = "Paused until " + clock(x.resume_at);
        else if (x.status === "done" && x.skipped) note = "Nothing to decide";
        else if (x.status === "done" && x.model) note = x.model.split(":").pop();
        h += `<li><button class="step ${x.status} ${s.gate ? "gate" : ""} ${S.view === s.id ? "on" : ""}" data-act="view" data-view="${s.id}">
          <span class="mark"></span><span><span class="step-name">${esc(s.name)}</span>
          <span class="step-note">${esc(note)}</span></span></button></li>`;
      }
      h += `</ol>`;
    }
    h += `</div>`;
  }
  const okKeys = S.meta.providers.filter((p) => p.ok).length;
  h += `<div class="rail-foot"><button data-act="settings"><span>Settings and API keys</span>
    <span class="key-dot ${S.meta.has_vision ? "ok" : ""}" title="${okKeys ? okKeys + " provider(s) connected" : "No key connected"}"></span></button></div>`;
  rail.innerHTML = h;
}

/* A "More" button and its menu. One menu is open at a time (S.menu holds its key); items are [act, label, data, tone]. */
function moreMenu(key, items, label) {
  const open = S.menu === key;
  const list = items.filter(Boolean);
  const data = (d) => Object.entries(d || {}).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
  return `<span class="menu-wrap"><button class="btn small icon-btn" data-act="menu" data-menu="${esc(key)}" aria-haspopup="menu" aria-expanded="${open}"
      aria-label="${esc(label)}" title="${esc(label)}">${ICON.more}</button>${open ? `<div class="menu" role="menu">${list.map(([act, text, d, tone]) =>
      `<button role="menuitem" class="menu-item ${tone || ""}" data-act="${act}" ${data(d)}>${esc(text)}</button>`).join("")}</div>` : ""}</span>`;
}
function redrawMenus() { if (S.pid) drawTop(); else render(); }

function homeBar() {
  const okKeys = S.meta.providers.filter((p) => p.ok).length;
  return `<button class="brand" data-act="home" aria-label="Scriptavera: your papers"><img class="brand-mark" src="/static/mark-rail.svg" alt=""><span class="brand-name">Scripta<i>vera</i></span></button>
    <span class="tb-space"></span>
    <button class="hb-btn ${testOn() ? "on" : ""}" data-act="testmode" aria-pressed="${testOn()}" title="Test Scriptavera on papers that are already published: their own paper, and anything after it, is never searched for">${testOn() ? "Test mode is on" : "Test mode"}</button>
    <button class="hb-btn" data-act="settings" title="${okKeys ? plural(okKeys, "AI provider") + " connected" : "No AI key connected yet"}">${ICON.sliders}<span class="hb-label">Settings</span><i class="key-dot ${S.meta.has_vision ? "ok" : ""}"></i></button>`;
}

function drawTop() {
  const el = $("#topbar");
  if (!S.pid) { el.className = "homebar"; el.innerHTML = homeBar(); return; }
  el.className = "";
  if (!S.b) { el.innerHTML = ""; return; }
  const m = S.b.state.meta;
  const ids = S.meta.stages.map((s) => s.id);
  const gateOpen = ids.some((i) => st(i).status === "waiting");
  const unfinished = ids.some((i) => st(i).status !== "done");
  const started = ids.some((i) => st(i).status !== "pending");
  const rec = proofSummary(S.b.proof);
  let h = `<span class="tb-title">${inner(latexLite((S.b.paper && S.b.paper.title) || m.title || m.name))}</span>
    <span class="tb-dom">${esc(domainNames(m.domains).join(", "))}</span>
    ${m.imported ? "" : `<button class="chip ${S.b.journal ? "blue" : ""} tb-journal" data-act="journal" title="The journal the final paper is prepared for">${S.b.journal ? "For: " + esc(S.b.journal.name) : "Choose a target journal"}</button>
    <button class="chip ${rec.cls} tb-proof" data-act="proof" title="The signed, timestamped record of your notes: evidence of when they existed">${esc(rec.chip)}</button>`}
    ${S.b.usage && S.b.usage.total.calls ? `<button class="chip" data-act="costs" title="What this paper has cost in AI calls so far, and the estimate for the whole paper">${money(S.b.usage.total.cost)} so far</button>` : ""}
    <span class="tb-space"></span>`;
  if (S.b.paper && !running()) h += `<button class="btn small" data-act="addpagesdlg" title="Add new pages to these notes: only they are read, and every part of the paper that does not change is kept">${ICON.pages}Add pages</button>`;
  if (S.b.editor && S.b.editor.can && !running() && !(S.view === "assemble" && S.b.paper)) {     // the paper view has its own
    h += `<a class="btn small ${unfinished ? "" : "primary"}" href="/editor/${encodeURIComponent(S.pid)}" title="Work on the LaTeX files directly, with the PDF beside them, the history, and commands to the AI">Go to editor</a>`;
  }
  if (running()) h += `<span class="muted small"><span class="spinner"></span> Working</span><button class="btn small" data-act="stop">Stop</button>`;
  else if (unfinished && !gateOpen && !m.imported) h += `<button class="btn small primary" data-act="resume" title="Carry on from where it stopped. Finished work is kept.">Continue</button>`;
  h += moreMenu("top", [
    ["rename", "Rename…", { id: S.pid, name: m.name || "" }],
    started && !running() && !m.imported && ["startover", "Start over…"],
    ["delete", "Delete paper…", { id: S.pid }, "danger"],
  ], "More actions for this paper");
  el.innerHTML = h;
}

/* ------------------------------------------------------------------ home */
function journalResults(list, message, act) {
  if (message) return `<p class="muted small" style="margin-top:8px">${esc(message)}</p>`;
  if (!list) return "";
  if (!list.length) return `<p class="muted small" style="margin-top:8px">No journal of that name was found in the databases. Check the spelling, or use the name as typed.</p>`;
  return `<div class="jr-list">${list.map((j, i) => `<button class="jr-item" data-act="${act}" data-i="${i}">
      <span class="jr-name">${esc(j.name)}</span>
      <span class="jr-meta">${esc(j.publisher || "publisher not given")}${j.issn ? ", ISSN " + esc(j.issn) : ""}${j.works ? ", about " + Number(j.works).toLocaleString() + " papers" : ""}</span></button>`).join("")}</div>`;
}
function ago(t) {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = new Date(t * 1000);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
/* What each step that waits for the author asks of them, as the line on the paper's card. */
const GATE_ASK = { topiccheck: "Check the topic and the papers found", confirm: "Confirm the notation", review: "Check the transcription",
  scope: "Choose the plan for the paper", decide: "Decide on the proposed changes", finish: "Choose the title, check the citations" };
function paperStatus(p) {
  /* What a paper is doing now, in a few words, and the colour that goes with it. */
  if (p.imported) return ["LaTeX paper", "plain"];
  if (p.completed) return ["Paper ready", "green"];
  if (p.running) return [`Working: ${p.current_name || "next step"}`, "blue"];
  if (p.waiting) return [GATE_ASK[p.waiting] || "Waiting for your answer", "hl"];
  if (p.paused) return [`Paused until ${clock(p.paused)}; continues by itself`, "plain"];
  if (p.stopped) return [`Stopped at ${p.current_name}`, "red"];
  if (!p.done) return ["Not started", "plain"];
  return [`Left at ${p.current_name}`, "plain"];
}
function paperTicks(p) {
  /* One mark per step, grouped by phase, as in the list of steps inside the paper. */
  const marks = S.meta.phases.map((ph) => `<span class="tk-ph">${S.meta.stages.map((s, i) => [s, i]).filter(([s]) => s.phase === ph.id)
    .map(([s, i]) => `<i class="tk ${esc((p.stages || [])[i] || "pending")}"></i>`).join("")}</span>`).join("");
  return `<div class="pc-steps"><span class="pc-ticks" role="img" aria-label="${p.done} of ${p.total} steps done">${marks}</span>
    <span class="pc-count">${p.done} of ${p.total}</span></div>`;
}
function paperCard(p) {
  const [label, tone] = paperStatus(p);
  const name = p.title || p.name;
  const href = p.imported ? `/editor/${encodeURIComponent(p.id)}` : `#p/${encodeURIComponent(p.id)}`;
  const thumb = p.imported ? `<span class="pc-thumb tex" aria-hidden="true">T<span>E</span>X</span>`
    : p.first_page ? `<img class="pc-thumb" loading="lazy" src="/api/projects/${encodeURIComponent(p.id)}/page/${p.first_page}?thumb=1" alt="">`
    : `<span class="pc-thumb" aria-hidden="true"></span>`;
  const day = (s) => { const d = new Date(s + "T00:00:00"); return isNaN(d) ? s : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
  const facts = [domainNames(p.domains)[0], p.pages ? plural(p.pages, "page") : "", p.test ? `test paper (literature before ${day(p.test)})` : ""].filter(Boolean);
  return `<article class="pcard ${tone}">
    <div class="pc-top"><span class="pc-status ${tone}">${p.running ? '<span class="spinner"></span>' : '<i class="pc-dot"></i>'}${esc(label)}</span>
      ${moreMenu("card:" + p.id, [["rename", "Rename…", { id: p.id, name: p.name }], ["delete", "Delete…", { id: p.id }, "danger"]], `More actions for ${name}`)}</div>
    <div class="pc-body">${thumb}<div class="pc-text">
      <h3 class="pc-title"><a class="pc-link" href="${href}">${rich(name)}</a></h3>
      ${p.title && p.name && p.title !== p.name ? `<div class="pc-name">${esc(p.name)}</div>` : ""}
      ${p.authors && p.authors.length ? `<div class="pc-authors">${esc(p.authors.join(", "))}</div>` : ""}
      <div class="pc-meta">${facts.length ? esc(facts.join(", ")) + "<br>" : ""}Updated ${esc(ago(p.updated))}</div></div></div>
    ${p.imported ? `<div class="pc-steps"><span class="pc-count">Opens in the editor</span></div>` : paperTicks(p)}
  </article>`;
}
const HOW = { read: "Every page is read. You confirm the topic, the notation and the transcription.",
  write: "The notes are understood as a whole; you choose the plan, and the paper is written from them.",
  revise: "Arguments are shortened, every proof is checked and a referee reads the draft. You decide on each change to the mathematics.",
  final: "You choose the title and check the citations. You get the LaTeX, the PDF and a package for Overleaf." };
const HOME_ACTS = `<button class="btn primary" data-act="newpaper">${ICON.plus}New paper from notes</button>
  <button class="btn" data-act="importpaper" title="A paper written elsewhere (a .tex file or an Overleaf .zip), opened in the editor to carry out a referee's report or the journal's proof corrections">${ICON.doc}Import a LaTeX paper</button>`;
function papersBoard() {
  const P = (S.projects || []).filter((p) => !p.imported), imported = (S.projects || []).filter((p) => p.imported);
  const groups = [
    ["Waiting for you", "", P.filter((p) => !p.completed && !p.running && p.waiting)],
    ["Working now", "These carry on by themselves, also while you work on another paper. Papers working at the same time share your AI limits, so each goes a little slower.", P.filter((p) => !p.completed && p.running)],
    ["In progress", "", P.filter((p) => !p.completed && !p.running && !p.waiting)],
    ["Completed", "", P.filter((p) => p.completed)],
    ["Imported for review", "Papers written elsewhere, for a referee's report or the journal's proofs.", imported],
  ];
  return groups.filter((g) => g[2].length).map(([h, sub, list]) => `<section class="pgroup" aria-label="${esc(h)}">
      <h2>${esc(h)} <span class="pg-n2">${list.length}</span></h2>${sub ? `<p class="pgroup-sub">${esc(sub)}</p>` : ""}
      <div class="pgrid">${list.map(paperCard).join("")}</div></section>`).join("");
}
function viewHome() {
  const P = S.projects || [];
  const noKey = !S.meta.has_vision;
  const key = noKey ? `<div class="banner"><div class="grow"><b>Connect an AI key first.</b> Reading handwriting needs a model that accepts images; a free Google Gemini key is enough to start.</div>
      <button class="btn" data-act="settings">Open settings</button></div>` : "";
  if (!P.length) {
    return `<div class="home">${key}<div class="home-empty">
      <h1>Your papers</h1>
      <p class="lead">Start a paper from your notes: a scanned PDF, photos of the pages, or typed notes in LaTeX or Markdown. Scriptavera reads them, asks you only what you must decide, and writes the paper with every formula as you wrote it.</p>
      <div class="home-acts">${HOME_ACTS}</div>
      <p class="muted small">Already have the paper in LaTeX? Import it to carry out a referee's report or the journal's proof corrections in the editor.</p>
      </div>
      <ol class="howto" aria-label="How a paper is made">${S.meta.phases.map((ph) => `<li><b>${esc(ph.name)}</b><span>${esc(HOW[ph.id] || "")}</span></li>`).join("")}</ol>
      ${aboutLine()}</div>`;
  }
  return `<div class="home">${key}
    <div class="home-head"><div class="grow"><h1>Your papers</h1>
      <p>Every step is saved as it finishes, so you can leave a paper at any point and come back to it.</p></div>
      <div class="home-acts">${HOME_ACTS}</div></div>
    ${papersBoard()}${aboutLine()}</div>`;
}
function money(x) { return x >= 1 ? "$" + x.toFixed(2) : x > 0 ? "$" + x.toFixed(3) : "$0"; }
function tokens(n) { return n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n); }
function testOn() { const t = ((S.meta || {}).settings || {}).test_mode || {}; return !!(t.on && t.before); }
function testBanner() {
  const t = ((S.meta || {}).settings || {}).test_mode || {};
  const mine = S.b && ((S.b.state || {}).meta || {}).test;
  if (mine) return `<div class="test-banner"><span>Test paper: only literature published before <b>${esc(mine.before)}</b> is searched for and read${mine.exclude && mine.exclude.length ? `; ${mine.exclude.length} paper(s) excluded` : ""}. OEIS is not asked.</span></div>`;
  if (t.on && t.before && !S.pid) return `<div class="test-banner"><span>Test mode is on: new papers are test papers, and search only literature published before <b>${esc(t.before)}</b>.</span> <button class="btn small" data-act="testmode">Change</button></div>`;
  return "";
}
function viewImported() {
  const m = S.b.state.meta;
  return `<div class="wrap"><div class="sheet"><div class="sec"><h2>${inner(latexLite(m.title || m.name))}</h2>
    <p>This paper was imported from LaTeX, exactly as it was written; Scriptavera reads no notes for it. Open it in the editor to work on it with the PDF beside the code.</p>
    <p>For a referee's report or the journal's proofs, use <b>Review &amp; proofs</b> in the editor: language and layout points are made by themselves, every point about the mathematics waits for you, and the letter is written from what was done.</p>
    <a class="btn primary" href="/editor/${encodeURIComponent(S.pid)}">Open in the editor</a></div></div></div>`;
}
function viewNew() {
  const f = S.form;
  const q = S.domQuery.trim().toLowerCase();
  const groups = {};
  for (const d of S.meta.domains) {
    if (q && !(d.name + " " + d.blurb + " " + d.id + " " + d.msc.join(" ")).toLowerCase().includes(q)) continue;
    (groups[d.group] = groups[d.group] || []).push(d);
  }
  let doms = "";
  for (const g of Object.keys(groups)) {
    doms += `<div class="dom-group"><div class="dom-group-h">${esc(g)}</div><div class="dom-grid">` +
      groups[g].map((d) => `<button class="dom ${f.domains.includes(d.id) ? "on" : ""}" data-act="dom" data-id="${d.id}" aria-pressed="${f.domains.includes(d.id)}">
        <span class="dom-name">${esc(d.name)}</span><span class="dom-code">MSC ${esc(d.msc.slice(0, 3).join(", "))}</span>
        <span class="dom-blurb">${esc(d.blurb)}</span></button>`).join("") + `</div></div>`;
  }
  if (!doms) doms = `<p class="muted" style="margin-top:14px">No area matches “${esc(S.domQuery)}”. Pick the closest one and describe the sub-area below.</p>`;
  const collapsed = f.domains.length && !S.domOpen && !q;
  const picked = f.domains.length
    ? `<div class="chips picked">${f.domains.map((id, i) => `<span class="chip blue big">${i === 0 ? "Primary: " : ""}${esc(domainNames([id])[0])}
        <button class="chip" data-act="dom" data-id="${id}" aria-label="Remove ${esc(domainNames([id])[0])}">×</button></span>`).join("")}
        ${collapsed ? `<button class="btn small" data-act="domopen">Add or change area</button>` : `<button class="btn small quiet" data-act="domclose">Done choosing</button>`}</div>`
    : `<p class="muted small picked">Choose one area, or up to three if the work sits between fields. The first is the primary one.</p>`;
  if (collapsed) doms = "";
  const files = f.files.map((x, i) => `<div class="file"><span>${esc(x.name)} <span class="muted small">${(x.size / 1048576).toFixed(1)} MB</span></span>
    <button class="btn small quiet" data-act="rmfile" data-i="${i}">Remove</button></div>`).join("");
  const authors = f.authors.map((a, i) => `<div class="grid-auth">
      <label class="field"><span>${i ? "" : "Author name"}</span><input type="text" data-bind="authors.${i}.name" value="${esc(a.name)}" placeholder="Full name"></label>
      <label class="field"><span>${i ? "" : "Affiliation"}</span><input type="text" data-bind="authors.${i}.affiliation" value="${esc(a.affiliation)}" placeholder="Department, university, city, country, or Independent"></label>
      <label class="field"><span>${i ? "" : "E-mail"}</span><input type="email" data-bind="authors.${i}.email" value="${esc(a.email)}" placeholder="name@university.edu"></label>
      <label class="field"><span>${i ? "" : "ORCID iD"}</span><input type="text" data-bind="authors.${i}.orcid" value="${esc(a.orcid || "")}" placeholder="0000-0000-0000-0000 (optional)"></label>
      ${f.authors.length > 1 ? `<button class="btn small quiet" data-act="rmauthor" data-i="${i}">Remove</button>` : "<span></span>"}</div>`).join("");
  const ready = f.domains.length && f.files.length;
  const noKey = !S.meta.has_vision;
  const journal = f.journal
    ? `<div class="chips"><span class="chip blue big">${esc(f.journal.name)}${f.journal.publisher ? " (" + esc(f.journal.publisher) + ")" : ""}
        <button class="chip" data-act="formjclear" aria-label="Remove the journal">×</button></span></div>`
    : `<div class="row"><input type="text" class="grow" id="formjq" data-bind="jq" value="${esc(f.jq)}" placeholder="Journal name, e.g. Journal of Number Theory">
        <button class="btn" data-act="formjsearch" ${S.busy === "formj" ? "disabled" : ""}>${S.busy === "formj" ? "Searching…" : "Find"}</button></div>
        ${journalResults(f.jresults, f.jmsg, "formjpick")}
        ${f.jresults && f.jq.trim().length > 2 ? `<button class="btn small quiet" data-act="formjtyped" style="margin-top:6px">Use “${esc(f.jq.trim())}” as typed</button>` : ""}`;
  return `<div class="wrap">
    ${noKey ? `<div class="banner"><div class="grow"><b>Connect an AI key first.</b> Reading handwriting needs a model that accepts images.
      A free Google Gemini key is enough to start.</div><button class="btn" data-act="settings">Open settings</button></div>` : ""}
    <div class="crumbs"><button class="btn small" data-act="home">${ICON.back}Home</button><span class="muted small">New paper from notes</span></div>
    <div class="head"><h1>Which area of mathematics are these notes in?</h1>
      <p>Your answer limits the literature search to that area and tells every model which conventions to expect. Nothing is read until you choose.</p></div>
    <div class="sheet">
      <div class="sec">
        ${collapsed ? "" : `<input class="domain-search" type="search" placeholder="Search areas, e.g. graph, PDE, 11" value="${esc(S.domQuery)}" data-bind-q="1" aria-label="Search areas">`}
        ${picked}${doms}
      </div>
      <div class="sec"><h2>What you are working on</h2><p>The literature search and every reading of your notes start from this, so the first two are required.
          The app also shows you what it understood and the papers it found, and asks you to confirm, before it reads further.</p>
        <label class="field"><span>The problem you are working on <b class="reqmark">required</b></span>
          <textarea rows="3" id="fproblem" data-bind="problem" placeholder="In a sentence or two, in your own words: the objects, the question, and what you prove or try to prove">${esc(f.problem)}</textarea></label>
        <label class="field"><span>Keywords <b class="reqmark">required</b></span><input type="text" id="fkeywords" data-bind="keywords" value="${esc(f.keywords)}" placeholder="at least two, separated by commas: the terms a specialist would search for">
          <small>Specific terms work best: the names of the objects, the invariants and the methods you use.</small></label>
        <label class="field"><span>The one or two papers your work relies on most <span class="muted">(optional)</span></span>
          <textarea rows="2" data-bind="keypapers" placeholder="One per line: a DOI, an arXiv number or the exact title">${esc(f.keypapers)}</textarea>
          <small>They are looked up before anything else and read first, so that the whole context is understood from the start.</small></label>
        <div class="grid2">
          <label class="field"><span>Sub-area <span class="muted">(optional)</span></span><input type="text" data-bind="subarea" value="${esc(f.subarea)}" placeholder="a few words naming the topic inside the area"></label>
          <label class="field"><span>Anything else the reader should know <span class="muted">(optional)</span></span><input type="text" data-bind="hints" value="${esc(f.hints)}" placeholder="e.g. continues a problem posed in a paper you named"></label>
        </div></div>
      <div class="sec"><h2>Your notes</h2><p>A scanned PDF, or photos of the pages in order. Blank pages and covers are skipped automatically. Typed notes work too: a .tex file, an Overleaf project downloaded as a .zip, Markdown or plain text. Their text is taken exactly as written, so nothing has to be read from an image.</p>
        <div class="row" style="margin-bottom:8px"><button class="btn small" data-act="phone" data-for="new">Take photos with your phone</button></div>
        <div class="drop" data-act="pick" id="drop" tabindex="0" role="button"><strong>Drop the notes here</strong>
          <span class="muted">or click to choose a PDF, images, or typed notes (.tex, .zip, .md, .txt)</span></div>
        <input type="file" id="fileInput" hidden multiple accept=".pdf,image/*,.tex,.ltx,.zip,.md,.markdown,.txt">
        <div class="files">${files}</div>
        <p class="muted small" style="margin-top:10px">Before anything else is done with them, your files are kept unchanged and a record of them is made: their fingerprints, signed with your key${S.meta && S.meta.settings && S.meta.settings.proof_timestamps === false ? "" : " and timestamped by independent services"}. It is evidence of when your notes existed. Only the fingerprints leave this computer for that, never the notes.</p></div>
      <div class="sec"><h2>Authors, and your declaration</h2><p>As arXiv asks with every submission, the record of your notes says who wrote them, who makes the record, and that the details are correct. All of it is signed and timestamped with the record. The authors also appear on the paper.</p>
        ${authors}<button class="btn small" data-act="addauthor">Add another author</button>
        <div class="declare">${declarationBlock(f)}</div></div>
      <div class="sec"><h2>Target journal</h2><p>Optional, and it can be chosen or changed at any time. The final paper is prepared to the journal's requirements: template, abstract length, keywords, reference style.</p>
        ${journal}</div>
      <div class="sec"><details class="more" ${f.title || f.refs ? "open" : ""}><summary>Paper details (title, papers you already know)</summary>
        <div class="grid2">
          <label class="field"><span>Title</span><input type="text" data-bind="title" value="${esc(f.title)}" placeholder="Leave empty to have titles proposed">
            <small>If you give none, titles are proposed once the paper is finished, and you choose.</small></label>
          <label class="field"><span>Papers you already know are relevant</span><textarea rows="3" data-bind="refs" placeholder="One per line: a DOI, an arXiv number, or a link">${esc(f.refs)}</textarea></label>
        </div></details></div>
      <div class="startbar"><button class="btn primary big" data-act="create" ${ready && !S.busy ? "" : "disabled"}>${S.busy === "create" ? "Preparing pages…" : "Start reading my notes"}</button>
        <span class="muted">${!f.domains.length ? "Choose an area first." : (!f.files.length ? "Add your notes to continue." : "A few pages are read first, without equations; then you confirm the topic and the papers found.")}</span></div>
      ${aboutLine()}
    </div></div>`;
}

/* --------------------------------------------- the declaration with the notes */
function orcidOk(s) {                 /* the check digit of an ORCID iD (ISO 7064 MOD 11-2), as the server checks it */
  const d = String(s || "").replace(/^\s*(https?:\/\/)?(www\.)?orcid\.org\//i, "").replace(/[^0-9Xx]/g, "");
  if (d.length !== 16 || !/^\d{15}/.test(d)) return false;
  let t = 0;
  for (const ch of d.slice(0, 15)) t = (t + Number(ch)) * 2;
  const r = (12 - (t % 11)) % 11;
  return d[15].toUpperCase() === (r === 10 ? "X" : String(r));
}
function declarationText(nAuthors, by) {    /* word for word what the server signs (server.declaration_text) */
  const tail = "The details in this record are correct to the best of my knowledge, and the files are the notes as I uploaded them.";
  if (by.is_author) {
    return nAuthors <= 1 ? `I, ${by.name}, am the author of these notes. ${tail}`
      : `I, ${by.name}, am an author of these notes. The other authors listed agree to this record. ${tail}`;
  }
  return `I, ${by.name}, am not an author of these notes. I record them for the authors listed, with their consent. ${tail}`;
}
/* who makes the record, from a choice among the named authors ("0", "1", …) or "other" with a name */
function declarationMaker(authors, recorder, otherName) {
  const named = authors.map((a, i) => ({ i, name: (a.name || "").trim() })).filter((x) => x.name);
  if (recorder === "other") return (otherName || "").trim() ? { name: otherName.trim(), is_author: false, index: null } : null;
  const pick = named.find((x) => String(x.i) === String(recorder)) || named[0];
  return pick ? { name: pick.name, is_author: true, index: pick.i } : null;
}
function declarationBlock(f) {
  const named = f.authors.map((a, i) => ({ i, name: a.name.trim() })).filter((x) => x.name);
  const by = declarationMaker(f.authors, f.recorder, f.recorderName);
  const opts = named.map((x) => `<option value="${x.i}" ${by && by.is_author && by.index === x.i ? "selected" : ""}>${esc(x.name)}</option>`).join("")
    + `<option value="other" ${f.recorder === "other" ? "selected" : ""}>Someone who is not an author, for the authors, with their consent</option>`;
  return `<div class="grid2" style="margin-top:14px">
      <label class="field"><span>Who is making this record?</span><select id="frecorder" data-frecorder>${named.length ? "" : `<option value="0">The first author named above</option>`}${opts}</select></label>
      ${f.recorder === "other" ? `<label class="field"><span>Your name</span><input type="text" data-bind="recorderName" value="${esc(f.recorderName)}" placeholder="Full name"></label>` : "<span></span>"}</div>
    <label class="check declare-line"><input type="checkbox" id="fdeclare" data-fdeclare ${f.declare ? "checked" : ""}>
      <span id="fdeclareText">${by ? esc(declarationText(named.length, by)) : "Name the author above: the declaration says who makes it."}</span></label>`;
}
function refreshDeclaration(full) {
  const host = $(".declare");
  if (!host || !S.form) return;
  if (full) { host.innerHTML = declarationBlock(S.form); return; }
  const by = declarationMaker(S.form.authors, S.form.recorder, S.form.recorderName), t = $("#fdeclareText");
  if (t) t.textContent = by ? declarationText(S.form.authors.filter((a) => a.name.trim()).length, by) : "Name the author above: the declaration says who makes it.";
}

/* ------------------------------------------------------------ components */
function pageStrip(kind) {
  const pages = S.b.state.pages;
  const done = new Set(kind === "scout" ? S.b.scouted : Object.keys((kind === "transcribe" || kind === "review") ? ((S.b.transcript || {}).pages || {}) : (S.b.survey || {})).map(Number));
  const tr = (S.b.transcript || {}).pages || {};
  return `<div class="strip">` + pages.map((p) => {
    const flag = (kind === "transcribe" || kind === "review") && tr[p.n] && tr[p.n].uncertain.length;
    const cls = p.skip ? "skip" : (flag ? "flag" : (done.has(p.n) ? "read" : ""));
    const title = p.skip ? `Page ${p.n}: skipped (${p.kind || "excluded"})` : `Page ${p.n}`;
    return `<button class="pg ${cls}" data-act="zoom" data-n="${p.n}" title="${esc(title)}">
      <img loading="lazy" src="/api/projects/${S.pid}/page/${p.n}?thumb=1" alt="Page ${p.n}"><span class="pg-mark"></span><span class="pg-n">${p.n}</span></button>`;
  }).join("") + `</div>`;
}

function meter(v) {
  const pct = Math.round((v || 0) * 100);
  return `<span class="meter ${pct >= 70 ? "" : (pct >= 45 ? "mid" : "low")}"><i style="width:${pct}%"></i></span>${pct}%`;
}

function topicCard() {
  const t = S.b.topic; if (!t) return "";
  return `<div class="sec"><h2>What the notes appear to be about</h2><p>From the first reading, with equations skipped.</p>
    <dl class="kv"><dt>Sub-area</dt><dd>${rich(t.subfield) || "<span class='muted'>not yet clear</span>"}</dd>
    <dt>Problem</dt><dd>${explained(t.problem)}</dd>
    <dt>Confidence</dt><dd>${meter(t.confidence)} <span class="muted small">after ${plural((t.pages_read || []).length, "page")}</span></dd>
    <dt>Keywords</dt><dd class="chips">${(t.keywords || []).map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</dd>
    ${t.missing ? `<dt>Still unclear</dt><dd class="muted">${rich(t.missing)}</dd>` : ""}</dl></div>`;
}

function authorsOf(p) {
  const a = (p.authors || []).map((x) => x.family || x.given).filter(Boolean);
  return a.length > 3 ? a.slice(0, 3).join(", ") + " et al." : a.join(", ");
}
function paperLink(p) {
  if (p.doi) return `<a href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">doi:${esc(p.doi)}</a>`;
  if (p.arxiv) return `<a href="https://arxiv.org/abs/${esc(p.arxiv)}" target="_blank" rel="noopener">arXiv:${esc(p.arxiv)}</a>`;
  if (p.url && /^https?:\/\//i.test(p.url)) return `<a href="${esc(p.url)}" target="_blank" rel="noopener">open</a>`;
  return "";
}
function litList(editable, onlyRelevant) {
  let lib = S.b.library || [];
  if (onlyRelevant) lib = lib.filter((p) => p.state !== "candidate" || p.read);
  if (!lib.length) return `<p class="muted">No papers yet.</p>`;
  return `<div class="lit">` + lib.map((p) => {
    const nt = p.notes;
    const notes = nt ? `<div class="lit-notes"><b>${esc(p.verdict || "")}</b> (${esc(p.fulltext || "")}). ${explained(nt.summary || "")}
        ${nt.relation ? `<div style="margin-top:4px"><b>Relation to your notes:</b> ${rich(nt.relation)}</div>` : ""}
        ${(nt.citable || []).length ? `<ul>${nt.citable.slice(0, 4).map((c) => `<li>${rich(c)}</li>`).join("")}</ul>` : ""}</div>` : "";
    const act = editable ? `<div class="lit-actions">${p.state === "excluded"
      ? `<button class="btn small" data-act="lit" data-key="${esc(p.key)}" data-state="selected">Include</button>`
      : `<button class="btn small quiet" data-act="lit" data-key="${esc(p.key)}" data-state="excluded">Exclude</button>
         ${p.state !== "selected" ? `<button class="btn small" data-act="lit" data-key="${esc(p.key)}" data-state="selected">Include</button>` : ""}`}</div>` : "<span></span>";
    return `<div class="lit-item ${p.state === "selected" ? "sel" : ""} ${p.state === "excluded" ? "exc" : ""}">
      <div class="lit-score" title="Relevance to your notes, 0 to 10">${p.score == null ? "–" : Math.round(p.score)}<small>of 10</small></div>
      <div><div class="lit-title">${rich(p.title)}</div>
        <div class="lit-by">${esc(authorsOf(p))}${p.year ? " (" + p.year + ")" : ""}${p.venue ? ", " + esc(p.venue) : ""} ${paperLink(p)}</div>
        <div class="lit-why">${p.pinned ? '<span class="chip blue">added by you</span> ' : ""}${p.read ? '<span class="chip green">read</span> ' : ""}
          ${p.origin === "graph" ? '<span class="chip">via citations</span> ' : ""}<span class="muted">${explained(p.why || "")}</span></div>${notes}</div>${act}</div>`;
  }).join("") + `</div>`;
}

function drawLog() {
  const el = $("#log"); if (!el) return;
  const only = el.dataset.stage;
  const lines = S.logs.filter((l) => !only || l.stage === only).slice(-80).reverse();
  el.innerHTML = lines.length ? lines.map((l) => `<div class="log-line ${l.level}"><time>${new Date(l.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time><span>${esc(l.message)}</span></div>`).join("")
    : `<p class="muted" style="padding:8px 0">Nothing logged in this session yet.</p>`;
}
function callsTable(stage, limit) {
  const calls = (S.b.calls || []).filter((c) => !stage || c.stage === stage).slice(-(limit || 60)).reverse();
  if (!calls.length) return `<p class="muted">No AI calls yet.</p>`;
  return `<table class="calls"><thead><tr><th>Task</th><th>Model</th><th class="num">Tokens in</th><th class="num">out</th><th class="num">Seconds</th></tr></thead><tbody>` +
    calls.map((c) => `<tr class="${c.ok ? "" : "bad"}"><td>${esc(c.label || c.role)}${c.ok ? "" : ` <span class="small">(${esc(c.note || "failed")})</span>`}</td>
      <td>${esc(c.model || "")}</td><td class="num">${c.in ?? ""}</td><td class="num">${c.out ?? ""}</td><td class="num">${c.seconds ?? ""}</td></tr>`).join("") + `</tbody></table>`;
}
function drawCalls() { const el = $("#calls"); if (el) el.innerHTML = callsTable(el.dataset.stage, 12); }
function drawProgress(stage) {
  if (S.view !== stage) { const n = $(`.step.running .step-note`); const p = st(stage).progress; if (n && p) n.textContent = p.note || ""; return; }
  const p = st(stage).progress; const bar = $("#bar > i"); const note = $("#barnote");
  if (p && bar) bar.style.width = (p.total ? Math.round(100 * p.done / p.total) : 0) + "%";
  if (p && note) note.textContent = `${p.note || ""}${p.total ? ` (${p.done} of ${p.total})` : ""}`;
  const n = $(`.step.running .step-note`); if (n && p) n.textContent = p.note || "";
}

/* -------------------------------------------- what the three readings found */
const SIMPLIFY_KIND = { "general-lemma": "A method used several times, stated once as a lemma", "same-argument": "The same argument as in an earlier proof",
  "direct-step": "A step that follows from an earlier result", "repeated-computation": "A computation carried out twice" };
const SEVERITY = { error: ["Error", "red"], gap: ["Gap in the argument", "hl"], slip: ["Slip of the pen", "hl"] };
const POINT_TYPE = { mathematics: "Mathematics", structure: "Structure", citation: "Citations", presentation: "Presentation", notation: "Notation", typography: "Typography" };

function proposalCard(pr, form) {
  const D = S.decide;
  const pick = (v, label) => `<button class="pick ${D && D.simplify[pr.id] === v ? "on" : ""}" data-act="decsim" data-id="${pr.id}" data-v="${v}" aria-pressed="${D && D.simplify[pr.id] === v}">${label}</button>`;
  const names = pr.affects.map((l) => pr.names[l] || l);
  return `<div class="dcard ${form && D.simplify[pr.id] ? "ok" : ""}" id="dec-${pr.id}">
    <div class="dcard-h"><span class="chip blue">${esc(SIMPLIFY_KIND[pr.kind] || pr.kind)}</span><span class="muted small">concerns the ${names.length > 1 ? "proofs" : "proof"} of ${esc(names.join(", "))}${pr.context && pr.context.pages.length ? ", " + pagesPhrase(pr.context) : ""}</span>
</div>
    <p>${rich(named(pr.idea))}</p>
    <p class="muted small">${pr.benefit ? esc(pr.benefit.replace(/^./, (c) => c.toUpperCase()).replace(/\.?$/, ".")) + " " : ""}The paper becomes about ${pr.saved} characters shorter.
      An independent check of the rewritten proofs found them sound${pr.check ? ": " + rich(named(pr.check)) : "."}</p>
    ${pr.new ? `<div class="dnew"><div class="dlabel">New ${esc(pr.new.kind)}, to stand before ${esc(pr.names[pr.new.before] || pr.new.before)}</div>
      <div class="tex-block">${latexLite(pr.new.statement)}</div><div class="dlabel" style="margin-top:8px">Its proof</div><div class="tex-block">${latexLite(pr.new.proof)}</div></div>` : ""}
    ${pr.affects.map((l) => `<div class="dlabel" style="margin-top:12px">Proof of ${esc(pr.names[l] || l)}</div>
      <div class="twocol"><div><div class="col-h">As it is in the draft</div><div class="tex-block">${latexLite(pr.before[l])}</div></div>
      <div><div class="col-h">Proposed</div><div class="tex-block new">${latexLite(pr.after[l])}</div></div></div>`).join("")}
    ${form ? `<div class="picks" role="group" aria-label="Your decision">${pick("accept", "Accept this change")}${pick("reject", "Keep the proofs as they are")}</div>` : ""}</div>`;
}
/* --- checking an item against the draft and against the notes */
function decideAll() {
  const it = (S.b.revision || {}).items || { simplify: [], issues: [], remarks: [] };
  return [...it.simplify, ...it.issues, ...it.remarks];
}
function pagesPhrase(C) {
  if (!C || !C.pages || !C.pages.length) return "";
  return `from your notes, page${C.pages.length > 1 ? "s" : ""} ${C.pages.join(", ")}`;
}
function marked(src, quote) {
  /* A statement or proof from the draft, with the paragraph containing the passage in question marked. */
  const sq = (t) => String(t || "").replace(/\$/g, "").replace(/\s+/g, "");
  const q = sq(quote);
  return String(src || "").split(/\n\s*\n/).map((para) => {
    const hit = q.length >= 6 && (sq(para).includes(q) || (q.length > 40 && sq(para).includes(q.slice(0, 40))));
    return `<div class="${hit ? "hl-para" : ""}">${latexLite(named(para))}</div>`;
  }).join("");
}
function decidePanel(x) {
  if (!x || !x.context) return `<div class="dp-empty">${x && x.source === "first reading of your notes" ? "This remark is about your notes as a whole, not about one result, so there is no single passage to show." : "This item is about the paper as a whole, not about one result, so there is no single passage to show."}</div>`;
  const C = x.context, D = S.decide;
  const pg = C.pages.includes(D.page) ? D.page : (C.quote_pages[0] || C.pages[0]);
  const mode = D.pmode || "image", tab = D.ptab === "draft" || !C.pages.length ? "draft" : "notes";
  const quote = x.quote || "";
  const tr = ((S.b.transcript || {}).pages || {})[pg];
  const title = C.name || labelName(C.label) || "This result";
  const tabs = `<div class="dp-tabs" role="tablist">
      <button role="tab" class="dp-tab ${tab === "notes" ? "on" : ""}" data-act="dptab" data-v="notes" aria-selected="${tab === "notes"}" ${C.pages.length ? "" : "disabled"}>Your handwritten notes</button>
      <button role="tab" class="dp-tab ${tab === "draft" ? "on" : ""}" data-act="dptab" data-v="draft" aria-selected="${tab === "draft"}">${esc(title)} in the draft</button></div>`;
  const draft = `<div class="dp-sec">
      ${(C.others || []).length ? `<p class="muted small">This proposal also concerns ${esc(C.others.map((l) => labelName(l) || l).join(", "))}.</p>` : ""}
      <div class="dlabel">Statement of ${esc(title)}</div><div class="tex-block">${marked(C.statement, C.part === "statement" ? quote : "")}</div>
      <div class="dlabel" style="margin-top:10px">Proof</div>
      ${C.proof ? `<div class="tex-block">${marked(C.proof, C.part !== "statement" ? quote : "")}</div>` : `<p class="muted small">The draft gives no proof.</p>`}
      ${quote && C.part ? `<p class="muted small" style="margin-top:6px">The marked paragraph is the one the problem is about.</p>` : ""}</div>`;
  const notes = `<div class="dp-sec">
      <p class="muted small">“${esc(title)}” is the draft's name; your notes have no such number. It was written from page${C.pages.length > 1 ? "s" : ""} ${C.pages.join(", ")} of your notes${C.quote_pages.length ? `, and the passage in question is on page ${C.quote_pages.join(", ")} (marked)` : ""}.</p>
      <div class="dp-tools">${C.pages.map((n) => `<button class="rv-p ${n === pg ? "on" : ""} ${C.quote_pages.includes(n) ? "flag" : ""}" data-act="dppage" data-n="${n}" title="Page ${n}">${n}</button>`).join("")}
        <span class="tb-space"></span>
        <button class="pick ${mode === "image" ? "on" : ""}" data-act="dpmode" data-v="image">Handwriting</button><button class="pick ${mode === "text" ? "on" : ""}" data-act="dpmode" data-v="text">Typed</button></div>
      ${mode === "image" ? `<a class="dp-img" href="/api/projects/${S.pid}/page/${pg}" target="_blank" rel="noopener" title="Open the page at full size"><img src="/api/projects/${S.pid}/page/${pg}" alt="Page ${pg} of your notes"></a>`
        : `<div class="rv-rendered">${tr ? mixed(tr.text, quote ? [quote] : []) : "<p class=\"muted\">No transcript for this page.</p>"}</div>`}</div>`;
  return tabs + (tab === "notes" ? notes : draft);
}

const COMPUTED = { proved: ["proved", "green"], exhaustive: ["every case", "green"], tested: ["tested", "green"], follows: ["follows", "green"],
  counterexample: ["false", "red"], unsupported: ["not implied", "hl"], logical: ["for the proof check", ""], unchecked: ["not computed", ""] };
function computedRows(r) {
  const c = (r.computed || []).filter((x) => x.status !== "given");
  if (!c.length) return "";
  const n = (ks) => c.filter((x) => ks.includes(x.status)).length;
  const ok = n(["proved", "exhaustive", "tested", "follows"]), bad = n(["counterexample"]), un = n(["unsupported"]), left = n(["logical", "unchecked"]);
  return `<details class="computed"><summary><span class="chip ${bad ? "red" : ok ? "green" : ""}">Computer: ${ok} step${ok === 1 ? "" : "s"} verified${bad ? `, ${bad} false` : ""}${un ? `, ${un} not implied` : ""}</span>
    ${left ? `<span class="muted small">${left} left to the proof check</span>` : ""}</summary>
    ${c.map((x) => `<div class="crow"><span class="chip ${(COMPUTED[x.status] || ["", ""])[1]}">${(COMPUTED[x.status] || [x.status])[0]}</span> ${inner(latexLite("$" + x.tex + "$"))}
      <div class="muted small">${esc(x.detail)}</div></div>`).join("")}</details>`;
}
function issueCard(c, form) {
  const D = S.decide, sev = SEVERITY[c.severity] || SEVERITY.gap;
  const mine = form ? (D.issues[c.id] || {}) : {};
  const pick = (v, label) => `<button class="pick ${mine.choice === v ? "on" : ""}" data-act="decissue" data-id="${c.id}" data-v="${v}" aria-pressed="${mine.choice === v}">${label}</button>`;
  const decided = mine.choice && (mine.choice !== "instruct" || (mine.note || "").trim());
  return `<div class="dcard ${form && decided ? "ok" : ""}" id="dec-${c.id}">
    <div class="dcard-h"><span class="chip ${sev[1]}">${sev[0]}</span><b>${esc(c.name)}</b>${c.context && c.context.pages.length ? `<span class="muted small">${pagesPhrase(c.context)}</span>` : ""}${c.status === "dismissed" ? '<span class="chip">dismissed by the second opinion</span>' : ""}${c.by === "computer" ? '<span class="chip">found by exact computation</span>' : ""}
</div>
    ${c.quote ? `<div class="dlabel">${c.context && c.context.part === "statement" ? "In the statement" : "In the proof"}</div><div class="tex-block">${quoted(c.quote)}</div>` : ""}
    <div class="dlabel" style="margin-top:8px">The problem</div><div class="tex-block plain">${inner(latexLite(named(c.problem)))}</div>
    ${c.ruling ? `<p class="muted small" style="margin-top:6px">Second opinion: ${inner(latexLite(named(c.ruling)))}</p>` : ""}
    ${c.status !== "dismissed" && c.correction ? `<div class="dlabel" style="margin-top:8px">Proposed correction</div><div class="tex-block new">${quoted(c.correction)}</div>` : ""}
    ${form ? `<div class="picks" role="group" aria-label="Your decision">${c.correction ? pick("fix", "Apply this correction") : ""}${pick("instruct", "I will say how to fix it")}${pick("keep", "Leave the proof as it is")}</div>
      ${mine.choice === "instruct" ? `<div class="ownbox"><textarea rows="3" data-decnote="${c.id}" placeholder="What should be changed. For a statement that differs from your notes, write it as your notes have it. You can use $...$ for mathematics." aria-label="Your instruction">${esc(mine.note || "")}</textarea>
        <div class="row" style="margin-top:6px"><button class="btn small primary" data-act="decnext" ${(mine.note || "").trim() ? "" : "disabled"} id="donebtn-${c.id}">Done, next item</button></div></div>` : ""}` : ""}</div>`;
}

/* ---------------------------------------------------------- stage screen */
const STAGE_ROLE = { scout: "vision_fast", survey: "vision_best", transcribe: "vision_best", figures: "vision_best", search1: "fast", reading: "reader",
  understand: "reason", intro: "writer", structure: "writer", simplify: "reason", verify: "reason", referee: "reason", assemble: "writer" };

function figurePair(f) {
  const v = Math.round(f.drawn || 0);
  const orig = `<figure><img src="/api/projects/${S.pid}/figure/${f.id}?v=${v}" alt="Your drawing, cut from page ${f.page}"><figcaption>Your drawing (page ${f.page})</figcaption></figure>`;
  const red = f.tikz_ok ? `<figure><img src="/api/projects/${S.pid}/figure/${f.id}?kind=tikz&v=${v}" alt="The redrawn figure"><figcaption>Redrawn</figcaption></figure>`
    : `<figure class="nofig"><div>${f.tikz_ok === null ? "The redrawn figure cannot be shown: no LaTeX program is installed on this computer." : "The figure could not be redrawn" + (f.error ? ": " + esc(f.error) : ".")}</div><figcaption>Redrawn</figcaption></figure>`;
  return `<div class="figpair">${orig}${red}</div>`;
}

function stageBody(id) {
  let h = "";
  if (id === "scout") {
    h += `<div class="sec"><h2>Pages</h2><p>Only a sample is read at this step. A tick marks a page that has been read.</p>${pageStrip("scout")}</div>` + topicCard();
  } else if (id === "search1" || id === "reading") {
    const ss = S.b.search_status || {};
    const round = id === "search1" ? ss.round1 : ss.round2;
    const queries = id === "search1" ? ss.queries1 : ss.queries2;
    if (round && Object.keys(round).length) h += `<div class="sec"><h2>Databases searched</h2><p>Every reference comes from one of these, never from a model's memory.</p><div class="src-row">` +
      Object.entries(round).map(([k, v]) => `<span class="chip ${v.ok ? "green" : "red"}" title="${esc(v.message || "")}">${esc(k)}: ${v.ok ? v.found + " results" : esc(v.message || "unavailable")}</span>`).join("") +
      `</div>${queries && queries.length ? `<div class="muted small">Queries: ${queries.map(esc).join("; ")}</div>` : ""}</div>`;
    if (id === "reading" && S.b.brief) h += `<div class="sec"><h2>Research context</h2><p>Written from the papers read; every later step works from it.</p><div style="max-width:80ch">${rich(S.b.brief.text).replace(/\n/g, "<br>")}</div></div>`;
    const sel = (S.b.library || []).filter((q) => q.state === "selected").length;
    h += `<div class="sec"><h2>Papers ${id === "reading" ? "read and considered" : "found"}</h2><p>${plural(sel, "paper")} judged relevant. The score is the relevance to your notes.</p>${litList(false, id === "reading")}</div>`;
  } else if (id === "survey") {
    const g = S.b.glossary;
    h += `<div class="sec"><h2>Pages</h2><p>Every page is read here, with the topic known, to draft the text and list all notation.</p>${pageStrip("survey")}</div>`;
    if (g) h += `<div class="sec"><h2>Notation catalogued</h2><p>${plural(g.entries.length, "item")}; ${g.entries.filter((e) => e.status === "undefined" || e.status === "ambiguous").length} need your confirmation at the next step.</p>
      <div class="chips">${g.entries.map((e) => `<span class="chip ${e.status === "undefined" ? "hl" : (e.status === "ambiguous" ? "red" : "")}">${e.kind === "symbol" ? tex(e.symbol) : rich(e.symbol)}</span>`).join("")}</div></div>`;
  } else if (id === "transcribe") {
    const tr = (S.b.transcript || {}).pages || {};
    const unc = Object.values(tr).reduce((a, v) => a + v.uncertain.length, 0);
    h += `<div class="sec"><h2>Pages</h2><p>Each page is read again with your confirmed notation. A yellow mark means a reading that is doubtful as mathematics; you will review it. Doubts about spelling or grammar are settled without asking you.</p>${pageStrip("transcribe")}
      ${Object.keys(tr).length ? `<p class="muted small" style="margin-top:10px">${plural(unc, "doubtful reading")}, ${plural((S.b.flags || []).length, "possible misreading")} so far${S.b.language_settled ? `; ${plural(S.b.language_settled, "doubt")} about wording settled without asking you` : ""}.</p>` : ""}</div>`;
  } else if (id === "figures") {
    const figs = S.b.figures;
    if (figs) {
      const drawn = figs.filter((f) => f.drawn), rest = figs.filter((f) => !f.drawn);
      h += `<div class="sec"><h2>Drawings in your notes</h2><p>${figs.length ? "Each drawing is judged from the text around it. One the paper needs is redrawn; one that only explains your argument is not printed and not redrawn, but the writers still read it. At the next step you check each figure for the paper, and you can print any of the others." : "Your notes contain no drawings."}</p>
        ${drawn.map((f) => `<div class="figcard">${figVerdict(f)}${figurePair(f)}<p class="muted small">${inner(mixed(f.caption || f.desc))}</p></div>`).join("")}
        ${rest.length ? `<h3 class="fig-sub">Not redrawn</h3>${rest.map((f) => `<div class="figcard"><div class="srow-t">Page ${f.page}</div>${figVerdict(f)}<p class="small">${inner(mixed(f.desc))}</p></div>`).join("")}` : ""}</div>`;
    }
  } else if (id === "understand") {
    const d = S.b.dossier;
    if (d) {
      const src = d.source && libPaper(d.source);
      h += `<div class="sec"><h2>The problem</h2><p>What your notes set out to do, as understood from reading all of them together with the papers read.</p>
        <div class="tex-block plain">${latexLite(d.problem)}</div>
        ${src ? `<p class="muted small" style="margin-top:8px">It comes from: ${rich(src.title)}${src.year ? " (" + src.year + ")" : ""}</p>` : ""}
        ${d.setting ? `<div class="dlabel" style="margin-top:12px">Setting and notation</div><div class="tex-block plain">${latexLite(d.setting)}</div>` : ""}</div>
      <div class="sec"><h2>Results, in the order of presentation</h2><p>Each result, what it depends on, and where it is in your notes. Every later step works from this list.</p>
        <table class="grid-table"><thead><tr><th>Result</th><th>Statement</th><th>Pages</th><th>Uses</th></tr></thead><tbody>
        ${d.results.map((r) => `<tr><td><b>${esc(r.id)}</b><br><span class="chip ${r.role === "main" ? "blue" : ""}">${esc(r.kind)}${r.role === "main" ? ", main result" : ""}</span></td>
          <td class="tex-cell">${latexLite(r.statement)}</td><td>${r.pages.join(", ") || "–"}</td><td>${r.depends.join(", ") || "–"}</td></tr>`).join("")}
        ${d.definitions.map((r) => `<tr><td><b>${esc(r.id)}</b><br><span class="chip">definition</span></td>
          <td class="tex-cell">${rich(r.name)}: ${inner(latexLite(r.text))}</td><td>${r.page || "–"}</td><td>–</td></tr>`).join("")}</tbody></table></div>
      ${(d.checked || []).length ? `<div class="sec"><h2>Checked against your notes</h2><p>A second reading compared every result with your notes. What it changed:</p>
        ${d.checked.map((c) => `<div class="flag green">${rich(c)}.</div>`).join("")}</div>` : ""}
      <div class="sec"><h2>How the argument goes</h2><p>The thread from the setting to the main result.</p><div class="tex-block plain">${latexLite(d.storyline)}</div>
        ${d.contribution ? `<div class="dlabel" style="margin-top:12px">What is known, and what your notes add</div><div class="tex-block plain">${latexLite(d.contribution)}</div>` : ""}
        ${d.concerns.length ? `<div class="dlabel" style="margin-top:12px">Noted for your attention</div>${d.concerns.map((c) => `<div class="flag">${inner(latexLite(c))}</div>`).join("")}
          <p class="muted small">Nothing was changed because of these. They are put to you at the decision step.</p>` : ""}</div>`;
    }
  } else if (id === "assess") {
    if (S.b.assessment) h += `<div class="sec"><h2>What the assessment found</h2>${assessSummary()}</div>${noveltyView()}
      <div class="sec"><h2>Each result</h2><p>How far your notes prove it, and whether it already appears in the papers that were read.</p>${assessRows(false)}</div>`;
  } else if (id === "intro") {
    const it = S.b.intro;
    if (it) h += `<div class="sec"><h2>Introduction</h2><p>Written from the understanding of the whole paper. Every statement about earlier work carries a citation of a paper that was read; a claim without a source is left out. It is copy-edited once more in the final version.</p>
      <div class="tex-block serif">${latexLite(it.text)}</div>
      ${(it.notes || []).map((n) => `<div class="flag">${rich(n)}</div>`).join("")}</div>`;
  } else if (id === "structure") {
    const pl = S.b.plan, dr = S.b.draft;
    if (pl) h += `<div class="sec"><h2>Structure of the paper</h2><p>Decided after the whole of the notes was understood: every definition and lemma stands before the place it is used.</p>
      <table class="grid-table"><thead><tr><th>Section</th><th>Presents</th><th>From your pages</th><th>Purpose</th></tr></thead><tbody>
      <tr><td>1. Introduction</td><td class="muted">the problem, earlier work, the main results</td><td>–</td><td class="muted">written first</td></tr>
      ${pl.sections.map((s, i) => `<tr><td>${i + 2}. ${rich(s.title)}</td><td>${esc(s.items.join(", ")) || "–"}</td><td>${s.pages[0]}${s.pages.length > 1 ? "–" + s.pages[s.pages.length - 1] : ""}</td><td>${rich(s.purpose)}</td></tr>`).join("")}</tbody></table></div>`;
    if (dr) {
      const a = dr.audit || { total: 0, preserved: 0, altered: [], missing: [], added: [] };
      const bad = a.altered.length + a.missing.length;
      h += `<div class="sec"><h2>First draft</h2><p>${plural(dr.sections, "section")}, ${plural(dr.results, "result")}, ${plural(dr.proofs, "proof")}. This is the draft that is now re-read three times; it is not yet the final wording.</p>
        <div class="row"><span class="chip ${bad ? "hl" : "green"}">${a.preserved} of ${a.total} formulas identical to your notes</span>
          ${bad ? `<span class="chip red">${bad} to look at in the final version</span>` : ""}</div>
        <details class="more" style="margin-top:14px" open><summary>Read the first draft</summary><div id="draftHost">${dr.html}</div></details></div>`;
    }
  } else if (id === "simplify") {
    const sm = (S.b.revision || {}).simplify;
    if (sm && sm.found) {
      h += `<div class="sec"><h2>Arguments that can be shortened</h2><p>The draft was re-read for arguments carried out twice, methods that can be stated once in general, and proofs that take a long way round. Each proposal was written out in full and then checked by a second, independent reading.
        ${sm.proposals.length ? "Nothing is changed unless you accept it at the decision step." : ""}</p>
        ${sm.proposals.length ? sm.proposals.map((pr) => proposalCard(pr, false)).join("") : `<p class="muted">${sm.found.length ? "No proposal passed the independent check, so none is put to you." : "No argument is carried out twice and no proof takes a detour."}</p>`}</div>`;
      if ((sm.dropped || []).length) h += `<div class="sec"><h2>Set aside</h2><p>Ideas that were tried and not put to you, with the reason.</p>
        ${sm.dropped.map((x) => `<div class="flag"><b>${esc(SIMPLIFY_KIND[x.kind] || x.kind)}.</b> ${esc(named(x.idea))}<div class="small" style="margin-top:4px">Set aside because ${inner(latexLite(named(x.reason)))}</div></div>`).join("")}</div>`;
    }
  } else if (id === "verify") {
    const v = (S.b.revision || {}).verify;
    if (v && Object.keys(v.results || {}).length) {
      const rs = Object.values(v.results);
      const up = rs.reduce((a, r) => a + r.issues.filter((c) => c.status === "upheld").length, 0);
      h += `<div class="sec"><h2>Proofs checked</h2><p>Each proof was examined on its own, step by step. Steps that are pure computation were first checked exactly on this computer; the model was told which ones hold, so it examined the logic. Every suspected problem then went to a second opinion, and only what that upheld is put to you.
        ${up ? `${plural(up, "problem")} stood up.` : "No problem stood up."} A model's check is strong evidence, not a certificate: it does not replace your own reading.</p>
        ${rs.map((r) => {
          const mine = r.issues.filter((c) => c.status === "upheld").length;
          const chip = r.verdict === "unproved" ? '<span class="chip hl">stated without proof</span>' : r.verdict === "cited" ? '<span class="chip">cited, not proved here</span>'
            : mine ? `<span class="chip red">${plural(mine, "problem")}</span>` : '<span class="chip green">no problem found</span>';
          return `<div class="vrow"><div class="vrow-h"><b>${esc(r.name)}</b>${chip}${r.issues.length > mine ? `<span class="chip">${plural(r.issues.length - mine, "suspicion")} dismissed</span>` : ""}</div>
            ${r.summary ? `<p class="muted small">${inner(latexLite(r.summary))}</p>` : ""}${computedRows(r)}${r.issues.map((c) => issueCard({ ...c, name: r.name }, false)).join("")}</div>`;
        }).join("")}</div>`;
      const cp = ((S.b.revision || {}).completion || {}).items || {};
      if (Object.keys(cp).length) h += `<div class="sec"><h2>Sketches completed by their own steps</h2><p>Your notes only sketch these proofs. Each sketch was completed by justifying its own steps, without changing your words, and every addition was checked by computation, by an independent reading for faithfulness, and by the proof check. Only completions that passed are put to you; nothing changes unless you accept one.</p>
        ${Object.values(cp).map((x) => `<div class="vrow"><div class="vrow-h"><b>${esc(x.name)}</b>${x.status === "ready" ? `<span class="chip green">completed, for your decision</span><span class="muted small">${plural(x.steps_completed || 0, "step")} filled in</span>` : '<span class="chip hl">not completed</span>'}</div>
          ${x.status === "ready" ? "" : `<p class="muted small">${inner(latexLite(named(x.reason)))}</p>`}</div>`).join("")}</div>`;
    }
  } else if (id === "referee") {
    const r = (S.b.revision || {}).referee;
    if (r) h += `<div class="sec"><h2>Referee's report</h2><p>The whole paper read the way a journal referee reads a submission${S.b.journal ? " to " + esc(S.b.journal.name) : ""}.</p>
      <div class="tex-block plain">${latexLite(r.summary)}</div>
      ${r.recommendation ? `<p style="margin-top:10px">Recommendation on the first draft: <span class="chip ${r.recommendation === "accept" ? "green" : "hl"}">${esc(r.recommendation)}</span></p>` : ""}</div>
      <div class="sec"><h2>Points raised</h2><p>Points about presentation, notation and typography are carried out in the final copy-edit. Points about the mathematics, the structure or the citations are put to you.</p>
      ${r.points.length ? r.points.map((x) => `<div class="flag ${x.route === "author" ? "" : "green"}"><span class="chip">${esc(POINT_TYPE[x.type] || x.type)}</span>
        ${x.where ? `<span class="muted small">${esc(labelName(x.where) || x.where)}</span>` : ""} <span class="chip ${x.route === "author" ? "hl" : "green"}" style="float:right">${x.route === "author" ? "for you" : "handled in the copy-edit"}</span>
        <div style="margin-top:5px">${inner(latexLite(named(x.comment)))}</div>${x.action ? `<div class="small" style="margin-top:3px"><b>Asked for:</b> ${inner(latexLite(named(x.action)))}</div>` : ""}</div>`).join("") : `<p class="muted">The referee raised no points.</p>`}</div>`;
  }
  return h;
}

function viewStage(id) {
  const info = stageInfo(id), x = st(id);
  const p = x.progress;
  const plan = (S.meta.plan || {})[STAGE_ROLE[id]];
  const status = { pending: "Not started", running: "In progress", done: "Finished", error: "Stopped", waiting: "Waiting for you", paused: "Paused" }[x.status];
  let h = `<div class="wrap"><div class="head head-row"><div class="grow"><h1>${esc(info.name)}</h1><p>${esc(info.desc)}.</p></div>
    <div class="row">${x.status === "done" && !running() ? `<button class="btn small" data-act="redo" data-stage="${id}">Redo from this step</button>` : ""}</div></div>`;
  if (S.b.kept && !S.b.paper) h += `<div class="banner blue"><div class="grow"><b>This paper was started with an earlier version of the app.</b> Your reading, the notation you confirmed and your transcript are kept.
      The paper itself is written again by the new steps; the one built earlier is still in the folder <code>out_before_update</code> of this project.</div></div>`;
  if (x.status === "error") h += `<div class="banner red"><div class="grow"><b>This step stopped.</b> ${esc(x.error || "")}
      <div class="small" style="margin-top:4px">Everything finished before this point is saved. Continue picks up from here, not from the beginning.</div></div>
      <button class="btn primary" data-act="resume">Continue</button></div>`;
  if (x.status === "paused") {
    const head = x.reason === "limits" ? (x.weaker ? "Paused: waiting for the strongest model." : "Paused: free-tier limit reached.")
      : x.reason === "network" ? "Paused: could not reach the AI providers." : "Paused: the model did not give a usable answer.";
    h += `<div class="banner"><div class="grow"><b>${head}</b> ${esc(x.error || "")}
      <div style="margin-top:6px">Progress is saved. This will continue by itself at <b>${clock(x.resume_at)}</b> <span class="muted" data-until="${x.resume_at}">${until(x.resume_at)}</span>, from this point. Keep the Scriptavera window open.</div>
      ${x.weaker ? `<div class="small" style="margin-top:6px">If you would rather not wait, the next best model (<b>${esc(x.weaker)}</b>) can do this one step. It is weaker, so the result may be poorer; the step can be redone later with the strongest model.</div>` : ""}</div>
      <div class="banner-acts"><button class="btn primary" data-act="resume">Try again now</button>
        ${x.weaker ? `<button class="btn" data-act="relax">Use ${esc(x.weaker)} for this step</button>` : ""}
        <button class="btn" data-act="autostop">Do not restart automatically</button></div></div>`;
  }
  if (x.stand_in) {
    const used = Object.entries(x.stand_in.used || {}).map(([m, n]) => `${m} (${plural(n, "call")})`).join(", ");
    h += `<div class="banner blue"><div class="grow"><b>Part of this step was done by a weaker model, with your permission.</b> ${esc(used)} stood in while ${esc((x.stand_in.wanted || []).join(", "))} was not free.
      To have the step done by the strongest model, use “Redo from this step” when it is free again.</div></div>`;
  }
  h += `<div class="sheet"><div class="sec"><div class="row"><span class="chip ${x.status === "done" ? "green" : (x.status === "error" ? "red" : (x.status === "paused" ? "hl" : (x.status === "running" ? "blue" : "")))}">${status}</span>
    ${x.model ? `<span class="muted small">Model: ${esc(x.model)}</span>` : (plan && plan.primary ? `<span class="muted small">Will use ${esc(plan.primary)} (${esc(plan.name)})${plan.strict ? "; no weaker model unless you allow it" : ""}</span>` : "")}</div>
    ${x.status === "running" || p ? `<div class="bar" id="bar"><i style="width:${p && p.total ? Math.round(100 * p.done / p.total) : 0}%"></i></div>
      <div class="muted small" id="barnote">${p ? esc(p.note || "") + (p.total ? ` (${p.done} of ${p.total})` : "") : ""}</div>` : ""}</div>`;
  h += stageBody(id);
  h += `<div class="sec"><h2>Who did what</h2><p>Each AI call in this step, newest first.</p><div id="calls" data-stage="${id}">${callsTable(id, 12)}</div>
    <h3>Log</h3><div class="log" id="log" data-stage="${id}"></div></div></div></div>`;
  return h;
}

/* ---------------------------------------------------------- confirm gate */
function initGate() {
  const g = S.b.glossary || { entries: [], questions: [] };
  const t = S.b.topic || {};
  const prev = S.b.confirmed;
  S.gate = {
    pid: S.pid, problem: (prev && prev.topic.problem) || t.problem || "", subfield: (prev && prev.topic.subfield) || t.subfield || "",
    keywords: ((prev && prev.topic.keywords) || t.keywords || []).join(", "), items: {}, answers: {}, extra: [{ symbol: "", meaning: "" }],
    instructions: (prev && prev.instructions) || "",
  };
  // choice: "" not answered | "yes" the proposal is right | "own" the author's wording | "skip" not notation or a misreading
  for (const e of g.entries) {
    const was = prev && prev.glossary.find((x) => x.symbol === e.symbol);
    const needs = e.status === "undefined" || e.status === "ambiguous";
    const it = { meaning: e.meaning || "", choice: needs && !e.remembered ? "" : "yes", own: "" };
    if (was) { if (was.meaning === (e.meaning || "")) it.choice = "yes"; else { it.choice = "own"; it.own = was.meaning; if (!needs) it.meaning = was.meaning; } }
    else if (prev && (prev.ignored || []).includes(e.symbol)) it.choice = "skip";
    S.gate.items[e.id] = it;
  }
  try {                                       // answers typed earlier in this browser survive a reload or an app restart
    const d = JSON.parse(localStorage.getItem("scriptavera-gate-" + S.pid) || "null");
    if (d && d.sig === gateSig()) {
      for (const k of ["problem", "subfield", "keywords", "instructions"]) if (typeof d[k] === "string") S.gate[k] = d[k];
      for (const [id, it] of Object.entries(d.items || {})) if (S.gate.items[id]) Object.assign(S.gate.items[id], it);
      S.gate.answers = d.answers || {}; if ((d.extra || []).length) S.gate.extra = d.extra;
    }
  } catch (e) { /* no saved draft */ }
}
function gateSig() { return (S.b.glossary.entries || []).map((e) => e.id + e.symbol).join("|"); }
function saveGate() {
  try { localStorage.setItem("scriptavera-gate-" + S.pid, JSON.stringify({ ...S.gate, sig: gateSig() })); } catch (e) { /* storage unavailable */ }
}
function answered(it) { return it.choice === "yes" || it.choice === "skip" || (it.choice === "own" && it.own.trim() !== ""); }
function gateCounts() {
  const need = (S.b.glossary.entries || []).filter((e) => e.status === "undefined" || e.status === "ambiguous");
  return { need: need.length, done: need.filter((e) => answered(S.gate.items[e.id])).length };
}
function answerLine(it) {
  if (it.choice === "yes") return `<span class="ans ok">Answered: the proposed meaning is right.</span>`;
  if (it.choice === "skip") return `<span class="ans ok">Answered: this will be ignored and the page re-read without it.</span>`;
  if (it.choice === "own") return it.own.trim() ? `<span class="ans ok">Answered: your meaning will be used.</span>` : `<span class="ans todo">Write the meaning below. That is all; there is nothing else to tick.</span>`;
  return `<span class="ans todo">Waiting for your answer. Choose one of the three.</span>`;
}
function symbolContext(e, n) {
  const text = ((S.b.survey || {})[n] || {}).text || "";
  const key = squash(e.symbol).replace(/[{}\\]/g, "").toLowerCase();
  if (!key) return [];
  const pieces = text.split(/\n+|(?<=[.;])\s+(?=[A-Z])/).filter((l) => squash(l).replace(/[{}\\]/g, "").toLowerCase().includes(key));
  return pieces.slice(0, 3);
}
function viewConfirm() {
  if (!S.b.glossary) return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens once the notation survey has finished.</div></div>`;
  if (!S.gate || S.gate.pid !== S.pid) initGate();
  const G = S.gate, g = S.b.glossary, t = S.b.topic || {};
  const waiting = st("confirm").status === "waiting";
  const need = g.entries.filter((e) => e.status === "undefined" || e.status === "ambiguous");
  const settled = g.entries.filter((e) => e.status === "defined" || e.status === "standard");
  const misread = g.entries.filter((e) => e.status === "misread");
  const termRow = (e) => {
    const it = G.items[e.id];
    const pick = (v, label) => `<button class="pick ${it.choice === v ? "on" : ""}" data-act="choose" data-id="${e.id}" data-v="${v}" aria-pressed="${it.choice === v}">${label}</button>`;
    return `<div class="term ${answered(it) ? "ok" : ""}" id="term-${e.id}">
      <div class="term-side"><div class="term-sym ${e.kind === "term" ? "word" : ""}">${e.kind === "symbol" ? tex(e.symbol) : rich(e.symbol)}</div>
        <span class="chip ${e.status === "ambiguous" ? "red" : "hl"}">${e.status === "ambiguous" ? "used in two ways" : "not defined in notes"}</span>${e.remembered ? `<span class="chip green" title="You gave this meaning in an earlier paper; it is accepted unless you change it">remembered</span>` : ""}
        <div class="where">${(e.pages || []).length ? `<span class="muted small">Show where it was read:</span>` : ""}
          <div class="chips">${(e.pages || []).slice(0, 8).map((n) => `<button class="chip blue" data-act="where" data-id="${e.id}" data-n="${n}">page ${n}</button>`).join("")}</div></div></div>
      <div><div class="term-q">${inner(mixed(e.question || `What does ${e.symbol} mean in your notes?`))}</div>
        <div class="guess"><span class="guess-h">The AI's guess</span>${it.meaning.trim() ? mixed(it.meaning) : `<p class="muted">No guess could be made.</p>`}
          <div class="term-basis">Based on: ${rich(e.basis || "how the notes use it")}. Confidence ${meter(e.confidence)}
            ${(e.uses || []).length ? `<br>Seen used as: ${e.uses.map(rich).join("; ")}` : ""}</div></div>
        <div class="picks" role="group" aria-label="Your answer for ${esc(e.symbol)}">
          ${it.meaning.trim() ? pick("yes", "Yes, that is right") : ""}${pick("own", "No, I will write what it means")}${pick("skip", "Ignore it: misread, or not notation")}</div>
        ${it.choice === "own" ? `<div class="ownbox"><textarea rows="2" data-gateown="${e.id}" placeholder="What it means in your notes. You can use $...$ for mathematics." aria-label="Your meaning of ${esc(e.symbol)}">${esc(it.own)}</textarea>
          <div class="preview" id="pv-${e.id}">${it.own.trim() ? mixed(it.own) : ""}</div>
          ${(e.alternatives || []).length ? `<div class="term-alts chips"><span class="muted small">Or use one of these:</span>${e.alternatives.map((a, i) => `<button class="chip" data-act="alt" data-id="${e.id}" data-i="${i}">${rich(a)}</button>`).join("")}</div>` : ""}</div>` : ""}
        <div class="ansline" id="ans-${e.id}">${answerLine(it)}</div></div></div>`;
  };
  const c = gateCounts();
  const sel = (S.b.library || []).filter((p) => p.state === "selected").length;
  return `<div class="wrap wide" style="padding-bottom:0"><div class="head"><h1>Confirm what was understood</h1>
      <p>Before any equation is trusted, check the topic and settle every symbol or term your notes use without defining. Nothing continues until you confirm.</p></div>
    ${!waiting && st("confirm").status === "done" ? `<div class="banner blue"><div class="grow">You confirmed this already. Confirming again restarts the pipeline from the reading step.</div></div>` : ""}
    <div class="gate-grid"><div class="sheet">
      <div class="sec"><h2>The topic</h2><p>Correct anything that is off. Topic confidence was ${Math.round((t.confidence || 0) * 100)}%.</p>
        <label class="field"><span>Sub-area</span><input type="text" data-gatef="subfield" value="${esc(G.subfield)}"></label>
        <label class="field" style="margin-top:10px"><span>The problem you are working on</span><textarea rows="4" data-gatef="problem">${esc(G.problem)}</textarea></label>
        <label class="field" style="margin-top:10px"><span>Search keywords, comma separated</span><input type="text" data-gatef="keywords" value="${esc(G.keywords)}"></label></div>
      <div class="sec"><h2>Needs your confirmation</h2><p>${need.length ? `${plural(need.length, "item")} used in your notes without a definition. For each one, press one of the three answers. To see where it was read, press a page button on the left: the place is marked on your page.` : "Every symbol in your notes is either defined there or completely standard."}</p>
        ${need.map(termRow).join("")}
        <div class="row" style="margin-top:14px"><button class="btn small" data-act="reglossary" ${S.busy ? "disabled" : ""}>${S.busy === "reglossary" ? "Re-checking…" : "Re-check this list"}</button>
          <span class="muted small">Builds the list again from the pages already read, asking only about items that matter. Your pages are not read again.</span></div></div>
      ${misread.length ? `<div class="sec"><h2>Probably misread in the first pass</h2><p>No answer needed. These will be read again from the page with the right context.</p>
        <table class="settled">${misread.map((e) => `<tr><td>${e.kind === "symbol" ? tex(e.symbol) : rich(e.symbol)}</td><td>${mixed(e.meaning)}
          <div class="chips">${(e.pages || []).slice(0, 6).map((n) => `<button class="chip blue" data-act="where" data-id="${e.id}" data-n="${n}">page ${n}</button>`).join("")}</div></td></tr>`).join("")}</table></div>` : ""}
      ${g.questions.length ? `<div class="sec"><h2>Other questions</h2><p>Optional. Answer the ones that matter.</p>${g.questions.map((q) => `<label class="field" style="margin-bottom:12px">
        <span>${q.page ? `<button class="chip" data-act="zoom" data-n="${q.page}">p. ${q.page}</button> ` : ""}${inner(mixed(q.question))}</span>
        <input type="text" data-gateq="${q.id}" value="${esc(G.answers[q.id] || "")}" placeholder="Your answer"></label>`).join("")}</div>` : ""}
      <div class="sec"><h2>Something that was missed?</h2><p>Add any symbol or convention the survey did not list.</p>
        ${G.extra.map((x, i) => `<div class="row" style="margin-bottom:8px"><input type="text" style="width:170px" placeholder="Symbol, in LaTeX" data-gatex="${i}.symbol" value="${esc(x.symbol)}">
          <input type="text" class="grow" placeholder="Meaning" data-gatex="${i}.meaning" value="${esc(x.meaning)}"></div>`).join("")}
        <button class="btn small" data-act="addextra">Add another</button>
        <label class="field" style="margin-top:16px"><span>Instructions for the transcription and writing</span>
          <textarea rows="2" data-gatef="instructions" placeholder="Anything the transcriber and the writer should follow, such as a preferred notation">${esc(G.instructions)}</textarea></label></div>
      ${settled.length ? `<div class="sec"><details class="more"><summary>Already settled: ${plural(settled.length, "item")} defined in your notes or standard</summary>
        <table class="settled">${settled.map((e) => `<tr><td>${e.kind === "symbol" ? tex(e.symbol) : rich(e.symbol)}</td>
          <td><input type="text" data-gate="${e.id}" value="${esc(G.items[e.id].meaning)}"><div class="small muted">${e.status === "defined" ? "Defined in your notes" : "Standard"}; ${explained(e.basis || "")}</div></td></tr>`).join("")}</table></details></div>` : ""}
    </div>
    <div class="sheet"><div class="sec"><h2>Papers found</h2><p>${plural(sel, "paper")} selected in your area. Exclude any that are off-topic, and add ones you know.</p>
      <div class="row" style="margin-bottom:12px"><input type="text" class="grow" id="addref" placeholder="DOI, arXiv number, or exact title">
        <button class="btn" data-act="addref">${S.busy === "addref" ? "Looking up…" : "Add"}</button></div>
      ${litList(true)}</div></div></div></div>
    <div class="gatebar"><span id="gatecount">${c.done} of ${c.need} answered</span><span class="tb-space"></span>
      <button class="btn primary big" data-act="confirm" ${S.busy || running() ? "disabled" : ""}>Confirm and continue</button></div>`;
}

/* ----------------------------------------------------------- review gate
   One list for the whole transcript. Every doubt and every figure is an item; an answered item leaves the list
   "To check" for "Settled" (with a tick); when nothing is left, "Approve" opens. The page image on the left
   follows the item being answered. */
function initReview() {
  S.review = { pid: S.pid, n: reviewPages()[0] || 1, zoom: 1, tab: "todo", dropped: {}, skip: {}, own: {}, figBusy: "",
    figMode: "", watch: [], local: [] };
  const first = openItems()[0]; if (first) S.review.n = first.n;
}
function reviewPages() { return Object.keys(((S.b || {}).transcript || {}).pages || {}).map(Number).sort((a, b) => a - b); }
function reviewFlags(n) {
  const tr = S.b.transcript.pages[n] || { uncertain: [] };
  const out = tr.uncertain.map((u, i) => ({ kind: "unc", id: `u${n}-${i}`, snippet: u.snippet, issue: u.issue, alt: u.alt, every: u.every }));
  (S.b.flags || []).forEach((f, i) => { if (f.page === n) out.push({ kind: "check", id: `c${i}`, snippet: f.snippet, issue: f.issue, alt: f.suggestion, every: f.every }); });
  out.forEach((f) => { f.key = `${n}|${f.snippet}|${f.alt}`; f.n = n; });
  /* a doubt about a passage that is no longer on the page (it has been changed meanwhile) has nothing left to decide */
  return out.filter((f) => !S.review.dropped[f.key] && !(f.snippet && !occurrences(tr.text, f.snippet).length));
}
function findFlag(id) {
  for (const n of reviewPages()) { const f = reviewFlags(n).find((x) => x.id === id); if (f) return f; }
  return null;
}
function figsOn(n) { return (S.b.figures || []).filter((f) => f.page === n); }
function figOpen(f) { return !["tikz", "orig", "none", "explain"].includes(f.choice); }
function openItems() {
  const out = [];
  for (const n of reviewPages()) {
    figsOn(n).filter(figOpen).forEach((f) => out.push({ type: "fig", n, f, key: "fig:" + f.id }));
    reviewFlags(n).forEach((f) => out.push({ type: "flag", n, f, key: f.key }));
  }
  return out;
}
function settledItems() {
  const pages = new Set(reviewPages()), out = [], seen = new Set();
  (S.b.figures || []).filter((f) => !figOpen(f) && pages.has(f.page)).forEach((f) => out.push({ type: "fig", n: f.page, f }));
  [...(S.review.local || []), ...(S.b.doubt_answers || [])].forEach((a) => {
    const k = `${a.page}|${a.snippet}|${a.alt}|${a.how}`;
    if (!seen.has(k) && pages.has(a.page)) { seen.add(k); out.push({ type: a.how === "note" ? "note" : "flag", n: a.page, a }); }
  });
  return out.sort((x, y) => x.n - y.n);
}
function occurrences(text, piece) {
  const at = []; if (!piece) return at;
  for (let i = String(text || "").indexOf(piece); i >= 0; i = text.indexOf(piece, i + piece.length)) at.push(i);
  return at;
}
function contextAt(text, pos, len) {
  /* A few words around one place on the page, cut so that no formula is broken. */
  const re = /\$\$[\s\S]+?\$\$|\$[^$\n]+?\$/g; let m, a = pos, b = pos + len;
  while ((m = re.exec(text))) { if (m.index <= pos && pos < m.index + m[0].length) { a = m.index; b = m.index + m[0].length; break; } }
  let a2 = a, b2 = b;
  while (a2 > 0 && a - a2 < 45 && !"$\n".includes(text[a2 - 1])) a2--;
  while (b2 < text.length && b2 - b < 45 && !"$\n".includes(text[b2])) b2++;
  if (a2 > 0 && a2 < a) { const s = text.indexOf(" ", a2); if (s >= 0 && s < a) a2 = s + 1; }
  if (b2 < text.length && b2 > b) { const s = text.lastIndexOf(" ", b2); if (s >= b) b2 = s; }
  return (a2 > 0 ? "… " : "") + text.slice(a2, b2).replace(/^\$\$|\$\$$/g, "$") + (b2 < text.length ? " …" : "");
}
function readable(src) {
  /* A reading quoted from the transcript, shown as the reader would see it in the paper, never as LaTeX source:
     a bare formula as mathematics, a passage as text with its formulas typeset. */
  const t = String(src || "").trim();
  if (!t) return "";
  const words = (t.replace(/\\[A-Za-z]+/g, " ").match(/[A-Za-z]{3,}/g) || []).length;
  const bare = !/\$/.test(t) && /\\[A-Za-z]+|[_^=<>]|[A-Za-z]\(|\d/.test(t) && words <= 2;
  return bare ? `<span class="math">${tex("\\displaystyle " + t.replace(/[.,;]\s*$/, ""))}</span>` : inner(mixed(t));
}
function explained(src) {
  /* A model's explanation: plain words with the mathematics typeset. */
  let t = String(src || "").replace(/^\s*(suggested|possible)?\s*reading\s*:\s*/i, "").trim();
  if (t) t = t[0].toUpperCase() + t.slice(1) + (/[.!?)\]$”"]$/.test(t) ? "" : ".");
  return inner(latexLite(t));
}
function choiceRow(act, id, label, hint, extra = "") {
  return `<button class="choice" data-act="${act}" data-id="${id}" ${extra}><span class="dot" aria-hidden="true"></span><span><b>${label}</b>${hint ? `<small>${hint}</small>` : ""}</span></button>`;
}
function flagCard(f, text) {
  /* One doubt, answered right here: the suggested reading, the reading as it is, or what the author types. */
  const at = occurrences(text, f.snippet), alt = f.alt && f.alt.toLowerCase() !== "none" ? f.alt : "";
  const skip = S.review.skip[f.key] || {}, chosen = at.filter((p, i) => !skip[i]).length;
  const own = S.review.own[f.key];
  const where = at.length > 1 ? `<div class="flag-line"><span class="flag-k">Where</span><div>It appears in ${at.length} places on this page. Untick any place that is already right.
      <div class="occ">${at.map((p, i) => `<label class="occ-row"><input type="checkbox" data-occ="${f.id}" data-i="${i}" ${skip[i] ? "" : "checked"}><span>${inner(mixed(contextAt(text, p, f.snippet.length), [f.snippet]))}</span></label>`).join("")}</div></div></div>` : "";
  const many = at.length > 1 ? (chosen === at.length ? `in all ${at.length} places` : `in the ${chosen} ticked place${chosen === 1 ? "" : "s"}`) : "";
  const choices = at.length
    ? `${alt ? choiceRow("usealt", f.id, "Use the suggested reading", readable(alt) + (many ? ` <span class="muted">(${many})</span>` : ""), chosen ? "" : "disabled") : ""}
       ${choiceRow("dropflag", f.id, "It is correct as read", readable(f.snippet))}
       ${choiceRow("ownread", f.id, "Something else: I will type what is written", "", `aria-expanded="${own !== undefined}"`)}`
    : choiceRow("dropflag", f.id, "Close this remark", "it points at no passage on the page");
  const ownBox = own === undefined || !at.length ? "" : `<div class="ownread">
      <div class="row"><input type="text" class="grow" id="own-${f.id}" data-own="${f.id}" value="${esc(own)}" placeholder="Type what is written, for example ${esc(alt || f.snippet)}" aria-label="The correct reading">
        <button class="btn small primary" data-act="useown" data-id="${f.id}" ${own.trim() && chosen ? "" : "disabled"}>Use this${many ? " " + many : ""}</button></div>
      <div class="ownprev muted small" id="ownpv-${f.id}">${own.trim() ? "It will read: " + readable(own) : "Write it the way you would in LaTeX; what it will look like is shown here. Press Enter to use it."}</div></div>`;
  return `<div class="qcard flag ${f.kind === "check" ? "red" : ""}" id="flag-${f.id}" data-page="${f.n}">
    <div class="qcard-h"><b>${f.kind === "check" ? "Possible misreading" : "Doubtful reading"}</b></div>
    <div class="qcard-issue">${explained(f.issue)}</div>
    ${f.snippet ? `<div class="flag-line"><span class="flag-k">Read as</span><span>${readable(f.snippet)}</span></div>` : ""}
    ${where}<div class="figlbl" style="margin-top:10px">Your answer</div><div class="choices" role="group">${choices}</div>${ownBox}</div>`;
}
function answerFlag(f, value, how) {
  /* Put the author's answer into the page, in the places ticked; the doubt moves to Settled. */
  const n = f.n, rec = S.b.transcript.pages[n];
  const at = occurrences(rec.text, f.snippet), skip = S.review.skip[f.key] || {};
  let v = "";
  if (how !== "kept") {
    if (!at.length) return toast("That passage is not on this page as quoted.", true);
    v = String(value || "").trim();
    if (!/\$/.test(f.snippet)) v = v.replace(/^\$+|\$+$/g, "");      /* typed with dollar signs inside a formula */
    let t = rec.text;
    for (let i = at.length - 1; i >= 0; i--) if (!skip[i]) t = t.slice(0, at[i]) + v + t.slice(at[i] + f.snippet.length);
    rec.text = t; rec.edited = true; saveTranscript(n, t);
  }
  S.review.dropped[f.key] = true; delete S.review.own[f.key];
  const ans = { page: n, snippet: f.snippet, alt: f.alt || "", how, value: v, issue: f.issue || "" };
  S.review.local.push(ans);
  api(`/api/projects/${S.pid}/doubt`, { method: "POST", body: ans }).catch((e) => toast(e.message, true));
  advanceReview(how === "kept" ? "Kept as read." : "Changed on the page.");
}
function advanceReview(said) {
  /* After an answer: the next item's page is shown; with nothing left, the Approve tab opens. */
  const open = openItems(), R = S.review;
  if (!open.length) { R.tab = "approve"; toast(`${said} Everything is checked: approve the transcript when you are ready.`); }
  else { R.n = open[0].n; toast(`${said} Moved to Settled. ${plural(open.length, "item")} left to check.`); }
  render();
  const r = $(".rv-body"); if (r) r.scrollTop = 0;
}
function showPage(n) {
  /* Change the page on the left without redrawing the answers on the right. */
  if (!S.review || S.review.n === n) return;
  S.review.n = n;
  const img = $("#rvimg"); if (img) { img.src = `/api/projects/${S.pid}/page/${n}`; img.alt = `Your handwritten page ${n}`; }
  const lab = $("#rvpgno"); if (lab) lab.textContent = `Page ${n}`;
  document.querySelectorAll(".rv-left .rv-p").forEach((b) => b.classList.toggle("on", +b.dataset.n === n));
}
function leftOutBanner(style) {
  /* One quiet line: pages left out as a cover or a blank page. Pages with anything read on them are put back
     automatically, so this is only for a last look. */
  const out = ((S.b.state || {}).pages || []).filter((p) => p.skip && (p.kind === "cover" || p.kind === "blank"));
  if (!out.length) return "";
  return `<div class="leftline" ${style ? `style="${style}"` : ""}>Left out as ${out.every((p) => p.kind === "blank") ? "blank" : "a cover or blank"}:
    ${out.map((p) => `<button class="linkbtn" data-act="zoom" data-n="${p.n}">page ${p.n}</button>`).join(", ")}. Click one to look at it, and include it if it has your work on it.</div>`;
}
function figVerdict(f) {
  /* What the app made of a drawing, and why, in one line. */
  const j = f.judged; if (!j) return "";
  const head = f.asked_print ? "You asked for it to be printed" : j.use === "paper" ? (j.sure ? "For the paper" : "Probably for the paper")
    : j.use === "explanation" ? (j.sure ? "Only explains your argument" : "Probably only explains your argument") : "Probably your own labels on a formula";
  const doubt = !j.sure && j.doubt && !f.asked_print ? ` <span class="muted">${explained(j.doubt)} So you decide.</span>` : "";
  return `<div class="figverdict v-${f.asked_print ? "paper" : j.use}"><b>${head}.</b> ${f.asked_print ? (j.use === "explanation" ? "The app had taken it for an explanation of your argument." : "") : explained(j.why)}${doubt}</div>`;
}
function figureCard(f) {
  const busy = S.review.figBusy === f.id ? (S.review.figMode || "draw") : false;
  const drawn = !!f.drawn, j = f.judged || {};
  const opt = (v, label, hint, off) => `<button class="choice ${f.choice === v ? "on" : ""}" role="radio" data-act="figchoice" data-id="${f.id}" data-v="${v}" aria-checked="${f.choice === v}" ${off || busy ? "disabled" : ""}><span class="dot" aria-hidden="true"></span><span><b>${label}</b>${hint ? `<small>${hint}</small>` : ""}</span></button>`;
  const asText = `<button class="choice" data-act="figtext" data-id="${f.id}" ${busy ? "disabled" : ""}><span class="dot" aria-hidden="true"></span><span><b>${busy === "text" ? "Writing it into the text…" : "It is not a figure: write it into the text"}</b><small>for your own labels or notes on a formula; labels become braces under the formula</small></span></button>`;
  const print = `<button class="choice" data-act="figprint" data-id="${f.id}" ${busy ? "disabled" : ""}><span class="dot" aria-hidden="true"></span><span><b>${busy === "print" ? "Drawing it for the paper…" : "Print it in the paper"}</b><small>it is redrawn from your page, and then you check the drawing</small></span></button>`;
  const printed = drawn ? opt("tikz", "Use the redrawn figure", f.tikz_ok ? "" : "it could not be redrawn", !f.tikz_ok) + opt("orig", "Use my own drawing", "the drawing cut from your page") : print;
  const explain = opt("explain", "Do not print it: it only explains my argument", "the writers still read it, to understand the proof");
  const order = j.use === "annotation" ? [asText, printed, explain] : j.use === "explanation" && !f.asked_print ? [explain, printed, asText] : [printed, explain, asText];
  return `<div class="qcard figcard" id="fig-${f.id}" data-page="${f.page}">
    <div class="qcard-h"><b>${drawn ? "Figure" : "Drawing"}${f.of > 1 ? ` ${f.k + 1} of ${f.of}` : ""}</b><span class="muted small">${drawn ? "Is it for the paper, and is the redrawn version what you drew?" : "Is it for the paper, or does it only explain your argument?"}</span></div>
    ${figVerdict(f)}
    ${drawn ? figurePair(f) : `<div class="figdesc">${inner(mixed(f.desc))}<div class="muted small">Your drawing is on page ${f.page}, shown on the left.</div></div>`}
    ${drawn && f.weak ? `<div class="figweak">Redrawn by ${esc(f.model || "a weaker model")}, a weaker model you allowed for this step. Compare it closely with your drawing: your own drawing may be the safer choice, or draw it again once the strongest model is back.</div>` : ""}
    <div class="figlbl" style="margin-top:4px">Your answer</div>
    <div class="choices" role="radiogroup" aria-label="Your answer for this drawing">
      ${order.join("")}${opt("none", "Leave it out completely", "for a doodle or an abandoned attempt; the writers do not see it")}
    </div>
    ${drawn ? `<div class="figfoot"><input type="text" class="grow figwhy" id="figwhy-${f.id}" placeholder="Optional: what it is, or what is wrong with the drawing" ${busy ? "disabled" : ""}>
      <button class="btn small" data-act="figredraw" data-id="${f.id}" ${busy ? "disabled" : ""}>${busy === "draw" ? "Drawing…" : "Draw it again"}</button></div>
    ${captionBox(f)}` : ""}</div>`;
}
function captionBox(f, compact) {
  /* The caption as it will be printed, with its source to edit below it (shown only on request). */
  return `<div class="capwrap"><div class="figlbl">Caption, printed under the figure if you keep it</div>
    <div class="cappv" id="cappv-${f.id}">${f.caption ? inner(latexLite(f.caption)) : '<span class="muted">No caption yet.</span>'}</div>
    <details class="more"><summary>Change the caption</summary>
      <textarea class="capbox ${compact ? "small" : ""}" rows="2" data-figcap="${f.id}" placeholder="One sentence saying what the figure shows">${esc(f.caption || "")}</textarea>
      <div class="muted small">Mathematics between $…$. Saved when you click elsewhere.</div></details></div>`;
}
function liveReading() {
  /* Pages added after the first check, and figures being drawn: what is happening now, step by step. */
  const a = S.b.adding; if (!a) return "";
  const tr = st("transcribe"), fg = st("figures"), note = (tr.progress || {}).note || "", run = running();
  const checking = tr.status === "running" && /checking/.test(note);
  const tS = !a.pages.length ? "done" : tr.status === "running" && !checking ? "now" : checking ? "done" : "wait";
  const cS = !a.pages.length ? "done" : checking ? "now" : "wait";
  const fS = !a.figures ? "done" : fg.status === "running" ? "now" : "wait";
  const step = (label, state) => `<li class="${state}"><span class="dq-mark ${state === "done" ? "ok" : ""}">${state === "done" ? "✓" : ""}</span><span>${label}</span>${state === "now" ? '<span class="spinner"></span>' : ""}</li>`;
  const who = a.pages.length ? `page${a.pages.length > 1 ? "s" : ""} ${a.pages.join(", ")}` : "the figures";
  const stopped = !run && (tr.status === "error" || tr.status === "paused" || fg.status === "error" || fg.status === "paused");
  return `<div class="live ${stopped ? "stopped" : ""}"><div class="live-h">${stopped ? "Reading stopped" : `<span class="spinner"></span> Reading ${who} now`}</div>
    <ol class="live-steps">${a.pages.length ? step("Reading the page, with the notation you confirmed (about a minute)", tS) + step("Checking it for misreadings", cS) : ""}${step("Judging its drawings, and redrawing those for the paper", fS)}</ol>
    ${stopped ? `<div class="row"><span class="muted small grow">${esc((tr.error || fg.error || "").slice(0, 200))}</span><button class="btn small primary" data-act="resume">Continue</button></div>`
      : `<div class="muted small">You can go on answering meanwhile. What is read appears in this list as soon as it is ready.</div>`}</div>`;
}
function settledRow(x) {
  const tick = `<span class="dq-mark ok">✓</span>`;
  if (x.type === "fig") {
    const f = x.f, used = f.choice === "tikz" || f.choice === "orig", j = f.judged || {}, expl = f.choice === "explain";
    const what = { tikz: "The redrawn figure goes into the paper.", orig: "Your own drawing goes into the paper.", none: "Left out completely.",
      explain: "Not printed: it only explains your argument. The writers still read it, to understand the proof." }[f.choice];
    const why = expl && f.auto ? `<div class="muted small">Decided by the app: ${explained(j.why)}${j.illustrates ? ` It helps to see ${inner(latexLite(String(j.illustrates).replace(/\.\s*$/, "")))}.` : ""}</div>` : "";
    return `<div class="srow">${tick}<div class="grow"><div class="srow-t">Page ${x.n} · ${expl ? "Drawing" : "Figure"}${f.of > 1 ? ` ${f.k + 1}` : ""}</div><div>${what}</div>
      ${expl ? `<div class="small figdesc-s">${inner(mixed(f.desc))}</div>` : ""}${why}
      ${used ? captionBox(f, true) : ""}</div>
      <button class="btn small quiet" data-act="figreopen" data-id="${f.id}">Change</button></div>`;
  }
  const a = x.a;
  if (x.type === "note") return `<div class="srow">${tick}<div class="grow"><div class="srow-t">Page ${x.n} · Your note on a formula</div><div>Written into the text instead of a figure.</div></div>
      <button class="btn small quiet" data-act="rvtext" data-n="${x.n}">See the page</button></div>`;
  const what = a.how === "kept" ? `Kept as read: ${readable(a.snippet)}` : a.how === "suggested" ? `Changed to the suggested reading: ${readable(a.value)}` : `Changed to your reading: ${readable(a.value)}`;
  return `<div class="srow">${tick}<div class="grow"><div class="srow-t">Page ${x.n} · Doubtful reading</div><div>${what}</div></div>
      <button class="btn small quiet" data-act="rvtext" data-n="${x.n}">Edit the page</button></div>`;
}
function approveTab(open, done) {
  const P = reviewPages(), edited = P.filter((n) => S.b.transcript.pages[n].edited), figs = S.b.figures || [];
  const kept = figs.filter((f) => f.choice === "tikz" || f.choice === "orig").length, out = figs.filter((f) => f.choice === "none").length;
  const expl = figs.filter((f) => f.choice === "explain").length;
  if (open.length) return `<div class="rv-empty"><b>${plural(open.length, "item")} still to check.</b><p class="muted">The paper is written from the approved transcript, so answer them first.</p>
      <button class="btn primary" data-act="rvtab" data-v="todo">Go to To check</button></div>`;
  const adding = S.b.adding;
  return `<div class="approve-card"><div class="big-tick">✓</div><h2>Everything is checked</h2>
    <ul class="sumlist"><li>${plural(P.length, "page")} read${adding ? ", and more still being read" : ""}</li>
      <li>${plural(done.filter((x) => x.type === "flag").length, "doubtful reading")} settled</li>
      <li>${plural(kept, "figure")} printed${expl ? `, ${expl} only explaining your argument (not printed)` : ""}, ${out} left out${done.some((x) => x.type === "note") ? `, ${plural(done.filter((x) => x.type === "note").length, "note")} written into the text` : ""}</li>
      <li>${edited.length ? `Pages you changed: ${edited.join(", ")}` : "No page changed by hand"}</li></ul>
    ${leftOutBanner("margin:0 auto 12px;max-width:460px")}
    <p class="muted small">Approving starts the writing: the whole of your notes is read together. The transcript cannot be changed after that without redoing the later steps.</p>
    <button class="btn primary big" data-act="approve" ${S.busy || running() ? "disabled" : ""}>${running() ? "Wait: a page is still being read" : "Approve the transcript and continue"}</button></div>`;
}
function viewReview() {
  if (!S.b.transcript || !Object.keys(S.b.transcript.pages).length) return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens once the final transcription has finished.</div></div>`;
  if (!S.review || S.review.pid !== S.pid) initReview();
  const R = S.review, pages = reviewPages();
  /* a page that was being read has arrived: show it */
  const a = S.b.adding;
  if (a && a.pages.length) R.watch = a.pages.slice();
  else if (R.watch.length && (!a || !a.pages.length) && pages.includes(R.watch[0])) {
    const n = R.watch[0]; R.watch = []; R.n = n; R.tab = "todo";
    setTimeout(() => toast(`Page ${n} is read. ${figsOn(n).length ? "Its drawings are being judged and drawn; any that need you appear in To check." : "Compare it with the image."}`), 0);
  }
  if (!pages.includes(R.n)) R.n = pages[0];
  const open = openItems(), done = settledItems(), total = open.length + done.length;
  const tab = (id, label, n) => `<button role="tab" class="dq-tab ${R.tab === id ? "on" : ""}" data-act="rvtab" data-v="${id}" aria-selected="${R.tab === id}">${label}${n !== null ? `<span class="dq-n">${n}</span>` : ""}</button>`;
  let body;
  if (R.tab === "settled") {
    body = done.length ? done.map(settledRow).join("") : `<div class="rv-empty"><p class="muted">Nothing settled yet. What you answer in To check moves here, with a tick.</p></div>`;
  } else if (R.tab === "text") {
    const rec = S.b.transcript.pages[R.n];
    body = `<div class="rv-pages">${pages.map((n) => `<button class="rv-p ${n === R.n ? "on" : ""} ${S.b.transcript.pages[n].edited ? "edited" : ""}" data-act="rvpage" data-n="${n}" title="Page ${n}">${n}</button>`).join("")}</div>
      <p class="muted small">Page ${R.n}, as it will go into the paper. Compare it with the image on the left.</p>${leftOutBanner("")}
      <div class="rv-rendered" id="rvout">${mixed(rec.text, reviewFlags(R.n).map((f) => f.snippet))}</div>
      <details class="more" style="margin-top:12px" ${R.srcOpen ? "open" : ""}><summary>Change something on this page by hand (the page's LaTeX source)</summary>
        <textarea class="rv-src" id="rvsrc" spellcheck="false" aria-label="Transcript source for page ${R.n}">${esc(rec.text)}</textarea>
        <div class="muted small">Mathematics goes between $…$ (inline) or $$…$$ (displayed). Changes save as you type. Only the mathematics needs your eye: spelling and grammar are corrected when the paper is written.</div></details>`;
  } else if (R.tab === "approve") {
    body = approveTab(open, done);
  } else {
    const groups = {};
    open.forEach((x) => { (groups[x.n] = groups[x.n] || []).push(x); });
    const auto = (S.b.figures || []).filter((f) => f.choice === "explain" && f.auto && pages.includes(f.page));
    const autoLine = auto.length ? `<div class="autoline"><b>${auto.length === 1 ? "One drawing only explains" : plural(auto.length, "drawing") + " only explain"} your argument</b>
      (page${auto.length > 1 ? "s" : ""} ${[...new Set(auto.map((f) => f.page))].join(", ")}), so ${auto.length === 1 ? "it is" : "they are"} not printed and not redrawn. The writers still read ${auto.length === 1 ? "it" : "them"}, to understand the proof.
      <button class="linkbtn" data-act="rvtab" data-v="settled">See why</button></div>` : "";
    body = liveReading() + autoLine + (open.length
      ? Object.keys(groups).map(Number).sort((x, y) => x - y).map((n) => `<div class="qgroup"><div class="qgroup-h"><span>Page ${n}</span>
          <button class="btn small quiet" data-act="rvshow" data-n="${n}">Show this page</button></div>
          ${groups[n].map((x) => x.type === "fig" ? figureCard(x.f) : flagCard(x.f, S.b.transcript.pages[n].text)).join("")}</div>`).join("")
      : `<div class="rv-empty"><div class="big-tick">✓</div><b>Nothing left to check.</b><p class="muted">${total ? "Every doubt and figure is settled." : "Nothing on the pages was doubtful."} Still, glance at each page under Page text before approving.</p>
          <button class="btn primary" data-act="rvtab" data-v="approve">Go to Approve</button></div>`);
  }
  const idx = pages.indexOf(R.n);
  return `<div class="review"><div class="rv-left"><div class="rv-tools"><button class="btn small" data-act="rvprev" ${idx <= 0 ? "disabled" : ""} aria-label="Previous page">‹</button>
      <span id="rvpgno">Page ${R.n}</span><button class="btn small" data-act="rvnext" ${idx >= pages.length - 1 ? "disabled" : ""} aria-label="Next page">›</button><span class="tb-space"></span>
      <button class="btn small" data-act="zoomout" aria-label="Zoom out">−</button><button class="btn small" data-act="zoomin" aria-label="Zoom in">+</button></div>
      <div class="rv-pages dark">${pages.map((n) => `<button class="rv-p ${n === R.n ? "on" : ""} ${reviewFlags(n).length || figsOn(n).some(figOpen) ? "flag" : ""}" data-act="rvpage" data-n="${n}" title="Page ${n}">${n}</button>`).join("")}</div>
      <div class="rv-img"><img id="rvimg" src="/api/projects/${S.pid}/page/${R.n}" alt="Your handwritten page ${R.n}" style="width:${R.zoom * 100}%"></div></div>
    <div class="rv-right"><div class="rv-head"><div class="grow"><h1>Check what was read</h1>
        <div class="dq-prog"><div style="width:${total ? Math.round((done.length / total) * 100) : 100}%"></div></div>
        <div class="muted small">${total ? `${done.length} of ${total} settled` : "Nothing was flagged."}${S.b.adding ? " · reading more now" : ""}</div></div></div>
      ${st("review").status === "done" ? `<div class="banner blue" style="margin:10px 20px 0"><div class="grow">You approved this already. Approving again writes the paper again from the transcript.</div></div>` : ""}
      <div class="dq-tabs rv-tabs" role="tablist">${tab("todo", "To check", open.length)}${tab("settled", "Settled", done.length)}${tab("text", "Page text", null)}${tab("approve", "Approve", null)}</div>
      <div class="rv-body">${body}</div>
      <div class="rv-foot"><span class="muted small" id="rvsaved"></span><span class="tb-space"></span>
        ${R.tab === "approve" ? "" : `<span class="muted small">${open.length ? `${plural(open.length, "item")} to check` : "All checked"}</span>
        <button class="btn ${open.length ? "" : "primary"}" data-act="rvtab" data-v="approve">Approve…</button>`}</div></div></div>`;
}

/* ------------------------------------------------- the topic, confirmed after the first search */
async function topicSubmit(action) {
  const T = topicState();
  S.busy = action === "refine" ? "topicrefine" : "topicok"; render();
  try {
    await api(`/api/projects/${S.pid}/topic`, { method: "POST", body: { action, comment: T.comment, problem: T.problem, keywords: T.keywords.split(/[,;]/).map((s) => s.trim()).filter(Boolean) } });
    S.follow = true; S.tg = null; history.replaceState(null, "", `#p/${S.pid}`);
    toast(action === "refine" ? "Your comment went to the model. Searching again." : "Confirmed. Every page is read now, with the topic known.");
    await refresh();
  } catch (e) { toast(e.message, true); }
  S.busy = ""; render();
}
function topicState() {
  const sig = S.pid + "|" + (st("search1").finished || 0);
  if (!S.tg || S.tg.sig !== sig) {
    const m = (S.b.state || {}).meta || {};
    S.tg = { sig, comment: "", problem: m.problem || "", keywords: (m.keywords || []).join(", ") };
  }
  return S.tg;
}
function viewTopic() {
  const t = S.b.topic, x = st("topiccheck");
  if (!t || st("search1").status !== "done") return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens after the first literature search.</div></div>`;
  const T = topicState(), low = (t.confidence || 0) < 0.7;
  const kp = (S.b.key_papers || []);
  const named = (S.b.library || []).filter((p) => p.key_paper);
  return `<div class="wrap" style="padding-bottom:0"><div class="head"><h1>Is this what your notes are about?</h1>
      <p>From your description and a first reading of a few pages (formulas skipped), this is what the app understood, and these are the papers it found.
        Nothing more is read until you confirm. If it is not quite right, say so below: your comment goes to the model, the topic is rewritten and the search is done again.</p></div>
    ${low ? `<div class="banner"><div class="grow"><b>The app is not sure about the topic</b> (confidence ${Math.round((t.confidence || 0) * 100)}%). Please say more precisely what your notes are about, or correct the problem and the keywords.</div></div>` : ""}
    ${t.refined ? `<div class="banner blue"><div class="grow">Searched again with your comment${t.refined > 1 ? ` (${t.refined} times)` : ""}. Read the summary and the papers once more.</div></div>` : ""}
    ${leftOutBanner("margin:10px 0 0")}
    ${kp.some((k) => k.missing) ? `<div class="banner red"><div class="grow">Not found: ${kp.filter((k) => k.missing).map((k) => esc(k.ident)).join(", ")}. Add it below by its DOI or arXiv number.</div></div>` : ""}
    <div class="sheet">
      ${topicCard()}
      <div class="sec"><h2>What you said</h2><p>Correct it here if you like; it is used for every later step.</p>
        <label class="field"><span>The problem you are working on</span><textarea rows="3" data-tgf="problem">${esc(T.problem)}</textarea></label>
        <label class="field"><span>Keywords</span><input type="text" data-tgf="keywords" value="${esc(T.keywords)}"></label>
        ${named.length ? `<p class="small">Papers you named, read first: ${named.map((p) => `<b>${rich(p.title)}</b>`).join("; ")}</p>` : ""}</div>
      <div class="sec"><h2>Papers found</h2><p>Exclude any that are off-topic and add ones you know. Excluded papers also tell the next search what to avoid.</p>
        <div class="row" style="margin-bottom:12px"><input type="text" class="grow" id="addref" placeholder="DOI, arXiv number, or exact title">
          <button class="btn" data-act="addref">${S.busy === "addref" ? "Looking up…" : "Add"}</button></div>
        ${litList(true)}</div>
      <div class="sec"><h2>More precisely</h2>
        <label class="field"><span>What your notes are about, in more detail (optional)</span>
          <textarea rows="3" data-tgf="comment" placeholder="For example: the sub-area is …; the papers on … are not related; the question comes from … ; the key objects are …">${esc(T.comment)}</textarea></label></div>
    </div></div>
    <div class="gatebar"><span class="muted small">${x.status === "done" ? "You confirmed this already." : ""}</span><span class="tb-space"></span>
      <button class="btn big" data-act="topicrefine" ${S.busy || running() ? "disabled" : ""}>${S.busy === "topicrefine" ? "Searching…" : "Search again with my comment"}</button>
      <button class="btn primary big" data-act="topicok" ${S.busy || running() ? "disabled" : ""}>This is right, continue</button></div>`;
}

/* ------------------------------------------------- closest work: a list per result and a map of the papers */
function noveltyView() {
  const N = S.b.novelty;
  if (!N || !N.closest || !Object.keys(N.closest).length) return "";
  const pct = (x) => Math.round(100 * x) + "%";
  const rows = Object.entries(N.closest).map(([rid, list]) => `<div class="nv-r"><b>${esc(rid)}</b>${list.map((x) =>
    `<div class="nv-p"><span class="nv-t">${esc(x.title)}${x.year ? ` (${x.year})` : ""}</span>
      <span class="chip">similar ${pct(x.sim)}</span>${x.coupling ? `<span class="chip">shared references ${pct(Math.min(1, x.coupling))}</span>` : ""}${x.cocited ? `<span class="chip">cited by ${x.cocited} key paper${x.cocited === 1 ? "" : "s"}</span>` : ""}</div>`).join("")}</div>`).join("");
  const extra = (N.threads || []).slice(0, 6).map((t) => `<div class="nv-p"><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.title)}</a> <span class="chip">${esc(t.site)}</span></div>`).join("")
    + (N.oeis || []).map((o) => `<div class="nv-p"><a href="${esc(o.url)}" target="_blank" rel="noopener">OEIS ${esc(o.id)}</a> ${esc(o.name)} <span class="muted small">(your sequence ${esc(o.sequence)})</span></div>`).join("");
  return `<div class="sec"><h2>Closest work found for each result</h2><p>Found by searching for each main result by its statement, and ranked by
    ${esc(N.how || "similarity")}, by the references a paper shares with your key papers, and by how often your key papers cite it. A close paper is a lead, not proof that a result is known: the assessment above weighed them.</p>
    ${noveltyMap(N.graph)}${rows}${extra ? `<h3 style="margin-top:12px">Questions and sequences</h3>${extra}` : ""}</div>`;
}
function noveltyMap(g) {
  if (!g || !g.nodes || g.nodes.length < 3) return "";
  const W = 640, H = 360, n = g.nodes.length, idx = Object.fromEntries(g.nodes.map((x, i) => [x.id, i]));
  const P = g.nodes.map((x, i) => ({ x: W / 2 + 140 * Math.cos(2 * Math.PI * i / n), y: H / 2 + 120 * Math.sin(2 * Math.PI * i / n), vx: 0, vy: 0 }));
  const E = (g.edges || []).filter((e) => e[0] in idx && e[1] in idx).map((e) => [idx[e[0]], idx[e[1]], e[2]]);
  for (let it = 0; it < 220; it++) {                       /* a small force layout: similar papers pull together */
    for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) {
      const dx = P[a].x - P[b].x, dy = P[a].y - P[b].y, d2 = Math.max(60, dx * dx + dy * dy), f = 900 / d2;
      P[a].vx += f * dx; P[a].vy += f * dy; P[b].vx -= f * dx; P[b].vy -= f * dy;
    }
    for (const [a, b, w] of E) { const dx = P[b].x - P[a].x, dy = P[b].y - P[a].y; P[a].vx += dx * w * 0.02; P[a].vy += dy * w * 0.02; P[b].vx -= dx * w * 0.02; P[b].vy -= dy * w * 0.02; }
    for (const p of P) { p.vx += (W / 2 - p.x) * 0.004; p.vy += (H / 2 - p.y) * 0.004; p.x = Math.min(W - 20, Math.max(20, p.x + p.vx)); p.y = Math.min(H - 20, Math.max(20, p.y + p.vy)); p.vx *= 0.5; p.vy *= 0.5; }
  }
  const years = g.nodes.map((x) => x.year || 0).filter(Boolean), y0 = Math.min(...years), y1 = Math.max(...years);
  const shade = (y) => !y || y1 === y0 ? 0.55 : 0.25 + 0.65 * (y - y0) / (y1 - y0);
  return `<figure class="nv-map"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of the papers found: lines join papers that share references">
    ${E.map(([a, b, w]) => `<line x1="${P[a].x.toFixed(1)}" y1="${P[a].y.toFixed(1)}" x2="${P[b].x.toFixed(1)}" y2="${P[b].y.toFixed(1)}" stroke-width="${(0.6 + 3 * w).toFixed(2)}"/>`).join("")}
    ${g.nodes.map((x, i) => `<circle cx="${P[i].x.toFixed(1)}" cy="${P[i].y.toFixed(1)}" r="${(4 + 2.2 * Math.log2(2 + (x.cited_by || 0))).toFixed(1)}"
      class="${x.seed ? "seed" : x.score >= 0.35 ? "close" : ""}" style="fill-opacity:${shade(x.year).toFixed(2)}"><title>${esc(x.title)}${x.year ? " (" + x.year + ")" : ""}</title></circle>`).join("")}
  </svg><figcaption class="muted small">Each circle is a paper (larger: more cited; darker: more recent). Lines join papers that cite the same earlier work, as on Connected Papers. Outlined: your key papers; blue: closest to one of your results. Hover a circle for its title.</figcaption></figure>`;
}
/* ------------------------------------------------- the plan for the paper (after the assessment) */
const KIND_INFO = {
  article: ["A research article", "Your main result is proved and is not in the papers that were read. The paper says exactly what is new and credits what is known."],
  partial: ["A paper on what is proved", "What your notes prove is presented as results. What they do not prove is stated as an open question or a conjecture, never as proved."],
  expository: ["An expository note", "The results are already known. The paper presents them clearly, credits each one to its source and claims nothing as new."],
  not_ready: ["Not yet: I will complete my notes", "Stop here. Nothing more is written. Finish the proofs or add what is missing to your notes, and start a new paper with them; they are assessed again."],
};
const PROOF_INFO = { complete: ["Proved in your notes", "green"], gap: ["The proof has a gap", "hl"], sketch: ["Only a sketch of a proof", "hl"],
  "breaks off": ["The proof breaks off", "red"], none: ["No proof in your notes", "red"], unknown: ["Not assessed: the answer was unclear", "hl"] };
const MAIN_INFO = { established: ["The main result stands on what your notes prove.", "green"], partly: ["The main result is only partly established by your notes.", "hl"],
  "not established": ["Your notes do not establish the main result.", "red"], unknown: ["The assessment gave no clear answer about the main result.", "hl"] };
function scopeState() {
  const sig = S.pid + "|" + (st("assess").finished || 0);           /* a new assessment: the choices start again */
  if (!S.scope || S.scope.sig !== sig) {
    const base = (S.b.scope && !S.b.scope.auto ? S.b.scope : S.b.scope_default) || { kind: "article", results: {}, note: "" };
    S.scope = { sig, pid: S.pid, kind: base.kind, results: { ...(base.results || {}) }, note: base.note || "" };
  }
  return S.scope;
}
function paperRef(key) {
  const x = libPaper(key);
  if (!x) return esc(key);
  const link = x.doi ? `https://doi.org/${x.doi}` : x.arxiv ? `https://arxiv.org/abs/${x.arxiv}` : x.url || "";
  return `${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">` : ""}${esc(x.title)}${link ? "</a>" : ""}${x.year ? ` (${x.year})` : ""}`;
}
function assessRows(editable) {
  const A = S.b.assessment, d = S.b.dossier || { results: [] }, D = editable ? scopeState() : null;
  return d.results.map((r) => {
    const a = (A.results || {})[r.id] || { proof: "complete", known: "new" };
    const pi = PROOF_INFO[a.proof] || PROOF_INFO.complete;
    const known = a.known !== "new";
    const pick = (v, label) => `<button class="pick ${D && D.results[r.id] === v ? "on" : ""}" data-act="scopepick" data-id="${esc(r.id)}" data-v="${v}" aria-pressed="${D && D.results[r.id] === v}">${label}</button>`;
    const choices = !editable ? "" : known
      ? `${pick("credit", "Present it as known, credited to that paper")}${pick("new", "It is not the same result: it is mine")}${pick("omit", "Leave it out")}`
      : a.proof !== "complete" ? `${pick("keep", a.proof === "none" ? "Keep it as a result; I will add the proof" : "Keep it as a result; I will complete the proof")}${pick("conjecture", "State it as a conjecture")}${pick("omit", "Leave it out")}` : "";
    return `<div class="dcard ${editable && !choices ? "ok" : ""}">
      <div class="dcard-h"><b>${esc(r.id)}</b><span class="chip ${r.role === "main" ? "blue" : ""}">${esc(r.kind)}${r.role === "main" ? ", main result" : ""}</span>
        <span class="muted small">notes p. ${r.pages.join(", ") || "?"}</span></div>
      <div class="tex-block">${latexLite(r.statement)}</div>
      <div class="row" style="margin-top:8px;gap:6px"><span class="chip ${pi[1]}">${pi[0]}</span>${known ? `<span class="chip blue">${a.known === "special" ? "A special case of a known result" : "Already in the literature"}</span>` : `<span class="chip">Not found in the papers read</span>`}</div>
      ${a.where ? `<p class="small" style="margin-top:6px">${inner(latexLite(a.where))}</p>` : ""}
      ${known ? `<p class="small" style="margin-top:6px">In ${paperRef(a.key)}${a.evidence ? ": " + inner(latexLite(a.evidence)) : ""}</p>` : ""}
      ${choices ? `<div class="picks" role="group" aria-label="How to present ${esc(r.id)}">${choices}</div>` : ""}</div>`;
  }).join("");
}
function assessSummary() {
  const A = S.b.assessment, m = MAIN_INFO[A.main] || MAIN_INFO.established, k = KIND_INFO[A.recommend] || KIND_INFO.article;
  return `<div class="banner ${m[1] === "green" ? "blue" : m[1] === "red" ? "red" : ""}"><div class="grow"><b>${m[0]}</b> ${A.main_note ? inner(latexLite(A.main_note)) : ""}
      <div style="margin-top:6px">Suggested: <b>${k[0].toLowerCase()}</b>.${A.recommend_note ? " " + inner(latexLite(A.recommend_note)) : ""}</div></div></div>
    ${(A.unfinished || []).length ? `<div class="dlabel" style="margin-top:12px">Left open in your notes</div>${A.unfinished.map((u) => `<div class="flag">${inner(latexLite(u))}</div>`).join("")}` : ""}`;
}
function viewScope() {
  const A = S.b.assessment, x = st("scope");
  if (!A) return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens once your notes have been assessed.</div></div>`;
  const D = scopeState();
  const kinds = Object.entries(KIND_INFO).map(([k, v]) => `<button class="kind ${D.kind === k ? "on" : ""}" data-act="scopekind" data-v="${k}" aria-pressed="${D.kind === k}">
      <b>${esc(v[0])}${A.recommend === k ? ' <span class="chip blue">suggested</span>' : ""}</b><span>${esc(v[1])}</span></button>`).join("");
  return `<div class="wrap" style="padding-bottom:0"><div class="head"><h1>Your plan for the paper</h1>
      <p>Before anything is written, your notes were assessed: how far each result is proved, and whether it already appears in the papers that were read.
        You decide what kind of paper this becomes. Nothing here changes your mathematics; it decides how it is presented.</p></div>
    ${x.stopped ? `<div class="banner"><div class="grow"><b>Stopped here, as you chose.</b> Nothing more is written for this paper. When your notes are complete, start a new paper with them.
      You can also choose another kind of paper below and continue.</div></div>` : ""}
    ${x.status === "done" ? `<div class="banner blue"><div class="grow">You decided this already. Deciding again writes the paper again.</div></div>` : ""}
    <div class="sheet"><div class="sec"><h2>What the assessment found</h2>${assessSummary()}
      <p class="muted small" style="margin-top:8px">“Not found in the papers read” is not proof of novelty: only the papers found and read were compared. Whether each proof is correct is checked later, step by step.</p></div>
      <div class="sec"><h2>Your results</h2><p>Where a proof is incomplete or a result is already known, say how it should appear.</p>${assessRows(true)}</div>
      <div class="sec"><h2>What kind of paper</h2><div class="kinds">${kinds}</div>
        <label class="field" style="margin-top:14px"><span>Anything the writing should know (optional)</span>
          <textarea rows="2" data-scopenote placeholder="For example: the lower bound is a remark, not a main result">${esc(D.note)}</textarea></label></div></div></div>
    <div class="gatebar"><span class="muted small">${esc(KIND_INFO[D.kind][0])}</span><span class="tb-space"></span>
      <button class="btn primary big" data-act="scopesubmit" ${S.busy || running() ? "disabled" : ""}>${S.busy === "scope" ? "Saving…" : D.kind === "not_ready" ? "Stop here" : "Write the paper this way"}</button></div>`;
}

/* ------------------------------------------------- the last step: title, introduction, citations */
function finishState() {
  const F = S.b.finish, P = S.b.paper, sig = S.pid + "|" + (st("assemble").finished || 0);   /* a new final version: choose again */
  if (!S.fin || S.fin.sig !== sig) {
    S.fin = { sig, pid: S.pid, title: P.title, own: F.titles.includes(P.title) ? "" : P.title, editing: false, intro: P.intro, asking: false, instr: "",
      keys: F.related.filter((r) => r.in_intro).map((r) => r.key), addref: "" };
  }
  if (!S.fin.editing && !S.fin.dirty) S.fin.intro = P.intro;
  return S.fin;
}
function scholar(q) { return `https://scholar.google.com/scholar?q=${encodeURIComponent(q)}`; }
function viewFinish() {
  const F = S.b.finish, P = S.b.paper, x = st("finish");
  if (!F || !P) return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens once the final version has been written.</div></div>`;
  const R = finishState();
  const errors = F.open.filter((o) => o.level === "error"), notes = F.open.filter((o) => o.level !== "error");
  const ownOn = !F.titles.includes(R.title);
  const titleRow = (t, i) => `<label class="trow ${R.title === t ? "on" : ""}"><input type="radio" name="ftitle" data-ftitle="${i}" ${R.title === t ? "checked" : ""}><span>${inner(latexLite(t))}</span></label>`;
  const now = new Set(F.related.filter((r) => r.in_intro).map((r) => r.key)), want = new Set(R.keys);
  const changed = now.size !== want.size || [...want].some((k) => !now.has(k));
  const topic = (S.b.topic && (S.b.topic.keywords || []).slice(0, 4).join(" ")) || P.title;
  const paperRow = (r) => {
    const link = r.doi ? `https://doi.org/${r.doi}` : r.arxiv ? `https://arxiv.org/abs/${r.arxiv}` : r.url;
    return `<label class="prow ${want.has(r.key) ? "on" : ""}"><input type="checkbox" data-fkey="${esc(r.key)}" ${want.has(r.key) ? "checked" : ""}>
      <span class="grow"><b>${rich(r.title)}</b><span class="muted small"> ${esc(r.authors)}${r.year ? ", " + r.year : ""}${r.venue ? ", " + esc(r.venue) : ""}</span>
        ${r.summary || r.why ? `<span class="small prow-s">${rich(r.summary || r.why)}</span>` : ""}
        <span class="small prow-l">${r.read ? '<span class="chip green">read</span>' : '<span class="chip">abstract only</span>'}${r.cited && !r.in_intro ? '<span class="chip blue">cited in the body</span>' : ""}
          ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${r.doi ? "DOI" : r.arxiv ? "arXiv" : "link"}</a>` : ""}
          <a href="${scholar(r.title)}" target="_blank" rel="noopener">Google Scholar</a></span></span></label>`;
  };
  return `<div class="wrap" style="padding-bottom:0"><div class="head head-row"><div class="grow"><h1>Title, introduction and citations</h1>
      <p>The paper is written. The last three decisions are yours: its title, its introduction, and which papers the introduction cites.</p></div>
      <div class="row"><button class="btn small" data-act="view" data-view="assemble">See the whole paper</button></div></div>
    ${x.status === "done" ? `<div class="banner blue"><div class="grow"><b>Finished.</b> The files carry your choices. You can still change anything here and press “Finish the paper” again.</div></div>` : ""}
    ${errors.length ? `<div class="banner red"><div class="grow"><b>Not ready for submission yet.</b> ${plural(errors.length, "point")} to settle first:
        <ul class="plain-list">${errors.map((o) => `<li>${rich(o.text)}</li>`).join("")}</ul></div></div>`
      : `<div class="banner blue"><div class="grow"><b>No open problems were found.</b> Read the paper once more yourself before you submit it: the checks are strong evidence, not a guarantee.</div></div>`}
    ${notes.length ? `<div class="sheet" style="margin-bottom:14px"><div class="sec"><div class="dlabel">Worth a look</div><ul class="plain-list">${notes.map((o) => `<li>${rich(o.text)}</li>`).join("")}</ul></div></div>` : ""}
    <div class="sheet">
      <div class="sec"><h2>1. Title</h2><p>Suggested from the finished paper. Choose one, or write your own.</p>
        <div class="tlist">${F.titles.map(titleRow).join("")}
          <label class="trow ${ownOn ? "on" : ""}"><input type="radio" name="ftitle" data-ftitle="own" ${ownOn ? "checked" : ""}><span class="grow">
            <input type="text" id="fown" data-fown value="${esc(R.own)}" placeholder="Your own title (you can use $...$)"></span></label></div>
        <button class="btn small" data-act="ftitles" ${S.busy ? "disabled" : ""} style="margin-top:8px">${S.busy === "ftitles" ? "Thinking…" : "Suggest five more"}</button></div>
      <div class="sec"><h2>2. Introduction</h2><p>Suggested from the whole of your notes and the papers found. Every statement about earlier work cites a paper and rests on what was read
          of it: the full text where it was available, otherwise the abstract. Accept it, change it yourself, or say what should change.</p>
        ${R.editing ? `<textarea rows="16" class="mono" data-fintro aria-label="The introduction">${esc(R.intro)}</textarea>
            <div class="row" style="margin-top:6px"><button class="btn small" data-act="fintroview">Done editing</button><span class="muted small">Mathematics between $…$; citations as \\cite{KEY}.</span></div>`
          : `<div class="tex-block serif">${latexLite(named(R.intro, true))}</div>`}
        <div class="row" style="margin-top:8px">${R.editing ? "" : `<button class="btn small" data-act="fintroedit">Edit it myself</button>`}
          <button class="btn small" data-act="fask">${R.asking ? "Never mind" : "Ask for changes"}</button></div>
        ${R.asking ? `<div class="ownbox"><textarea rows="2" data-finstr placeholder="For example: say more about what is known for small exponents; shorten the first paragraph">${esc(R.instr)}</textarea>
          <button class="btn small primary" data-act="fcite" style="margin-top:6px" ${S.busy ? "disabled" : ""}>${S.busy === "fcite" ? "Rewriting…" : "Rewrite the introduction"}</button></div>` : ""}</div>
      <div class="sec"><h2>3. Papers the introduction cites</h2><p>The papers closest to your notes, found in OpenAlex, Semantic Scholar, arXiv, zbMATH and Crossref, most relevant first.
          Tick the ones to cite. Each has a link to look it up on Google Scholar; <a href="${scholar(topic)}" target="_blank" rel="noopener">search Google Scholar for the topic</a> to find more.</p>
        <div class="plist">${F.related.map(paperRow).join("") || '<p class="muted">No related papers were found.</p>'}</div>
        <div class="row" style="margin-top:10px"><input type="text" class="grow" id="faddref" data-faddref value="${esc(R.addref)}" placeholder="Add a paper you know: DOI, arXiv number or exact title">
          <button class="btn small" data-act="faddref" ${S.busy ? "disabled" : ""}>${S.busy === "faddref" ? "Looking up…" : "Add"}</button></div>
        ${changed ? `<div class="banner" style="margin-top:12px"><div class="grow">Your choice differs from what the introduction cites now.</div>
          <button class="btn primary" data-act="fcite" ${S.busy ? "disabled" : ""}>${S.busy === "fcite" ? "Rewriting…" : "Rewrite the introduction to cite exactly these"}</button></div>` : ""}</div>
    </div></div>
    <div class="gatebar"><span class="muted small">Title: ${R.title ? inner(latexLite(R.title)) : "not chosen"}</span><span class="tb-space"></span>
      <button class="btn primary big" data-act="ffinish" ${S.busy || running() ? "disabled" : ""}>${S.busy === "ffinish" ? "Building the files…" : "Finish the paper"}</button></div>`;
}

/* ----------------------------------------------------------- decide gate */
function decideSig() {
  const it = (S.b.revision || {}).items || { simplify: [], issues: [], remarks: [] };
  return [...it.simplify, ...(it.complete || []), ...it.issues, ...it.remarks].map((x) => x.id).join("|") + "|" + ((S.b.revision.simplify || {}).sig || "");
}
function initDecide() {
  const it = S.b.revision.items, prev = S.b.revision.decisions;
  S.decide = { pid: S.pid, sig: decideSig(), simplify: {}, complete: {}, issues: {}, remarks: {} };
  if (prev && !prev.auto) {
    Object.assign(S.decide.simplify, prev.simplify || {});
    for (const [k, v] of Object.entries(prev.complete || {})) S.decide.complete[k] = { choice: v.choice, note: v.note || "", text: v.text || "" };
    for (const [k, v] of Object.entries(prev.issues || {})) S.decide.issues[k] = { choice: v.choice, note: v.note || "" };
    Object.assign(S.decide.remarks, prev.remarks || {});
  }
  try {
    const d = JSON.parse(localStorage.getItem("scriptavera-decide-" + S.pid) || "null");
    if (d && d.sig === S.decide.sig) { Object.assign(S.decide.simplify, d.simplify || {}); Object.assign(S.decide.complete, d.complete || {}); Object.assign(S.decide.issues, d.issues || {}); Object.assign(S.decide.remarks, d.remarks || {}); S.decide.read = d.read || {}; }
  } catch (e) { /* no saved draft */ }
  for (const pr of it.simplify) if (!(pr.id in S.decide.simplify)) S.decide.simplify[pr.id] = "";
}
function saveDecide() { try { localStorage.setItem("scriptavera-decide-" + S.pid, JSON.stringify(S.decide)); } catch (e) { /* storage unavailable */ } }
function decideCounts() {
  const it = S.b.revision.items, D = S.decide;
  const sim = it.simplify.filter((p) => ["accept", "reject"].includes(D.simplify[p.id])).length;
  const cmp = (it.complete || []).filter((c) => compSettled(D.complete[c.id])).length;
  const iss = it.issues.filter((c) => { const m = D.issues[c.id] || {}; return m.choice === "keep" || (m.choice === "fix" && c.correction) || (m.choice === "instruct" && (m.note || "").trim()); }).length;
  return { need: it.simplify.length + (it.complete || []).length + it.issues.length, done: sim + cmp + iss };
}
function compSettled(m) {
  m = m || {};
  return m.choice === "accept" || m.choice === "keep" || (m.choice === "edit" && (m.text || "").trim()) || (m.choice === "instruct" && (m.note || "").trim());
}
function decideQueue() {
  /* Every item in the order it is decided: proposals and problems first (each needs an answer), remarks last (optional). */
  const it = S.b.revision.items;
  return [...it.simplify.map((x) => ({ x, kind: "simplify", need: true })), ...(it.complete || []).map((x) => ({ x, kind: "complete", need: true })),
    ...it.issues.map((x) => ({ x, kind: "issue", need: true })),
    ...it.remarks.map((x) => ({ x, kind: "remark", need: false }))];
}
function settledAs(q) {
  /* What the author decided for an item, in words, or "" while it is open. */
  const D = S.decide, x = q.x;
  if (q.kind === "simplify") return { accept: "Accepted", reject: "Rejected" }[D.simplify[x.id]] || "";
  if (q.kind === "complete") {
    const m = D.complete[x.id] || {};
    if (!compSettled(m)) return "";
    return { accept: "Completed proof accepted", edit: "Your edited proof", instruct: "To be done again as you asked", keep: "Sketch kept" }[m.choice];
  }
  if (q.kind === "issue") {
    const m = D.issues[x.id] || {};
    if (m.choice === "keep") return "Left as it is";
    if (m.choice === "fix" && x.correction) return "Correction applied";
    if (m.choice === "instruct" && (m.note || "").trim()) return "Your instruction";
    return "";
  }
  if ((D.remarks[x.id] || "").trim()) return "Your instruction";
  return (D.read || {})[x.id] ? "Read, nothing to do" : "";
}
function queueTitle(q) {
  const x = q.x;
  if (q.kind === "issue") return `${x.name}: ${(SEVERITY[x.severity] || SEVERITY.gap)[0].toLowerCase()}`;
  if (q.kind === "simplify") return `Shorter proof of ${x.affects.map((l) => x.names[l] || l).join(", ")}`;
  if (q.kind === "complete") return `Completed proof of ${x.name}`;
  const where = x.where ? (labelName(x.where) || named(x.where)) : "";
  return `Remark${where ? " on " + where : x.source === "first reading of your notes" ? " on your notes" : ""}`;
}
function queueText(q) {
  const x = q.x, t = q.kind === "issue" ? x.problem : q.kind === "simplify" ? x.idea : q.kind === "complete"
    ? `Your sketch, completed by its own steps (${plural((x.parts || []).filter((p) => p.added).length, "step")} filled in)` : x.comment;
  return inner(latexLite(named(String(t || "").slice(0, 160) + (String(t || "").length > 160 ? " …" : ""))));
}
function currentItem() {
  const Q = decideQueue(), D = S.decide;
  return Q.find((q) => q.x.id === D.focus) || Q.find((q) => q.need && !settledAs(q)) || Q.find((q) => !settledAs(q)) || Q[0];
}
function advanceDecide(fromId) {
  /* After an answer: the next open item, so the author never has to look for it. */
  const Q = decideQueue(), i = Q.findIndex((q) => q.x.id === fromId);
  const next = [...Q.slice(i + 1), ...Q.slice(0, i)].find((q) => !settledAs(q) && (q.need || !Q.some((r) => r.need && !settledAs(r))));
  S.decide.focus = next ? next.x.id : fromId; S.decide.page = 0;
  if (next) S.decide.tab = "open";
}
function completeCard(c) {
  /* A sketched proof, completed by justifying its own steps: the author's words unchanged, every addition marked. */
  const D = S.decide, m = D.complete[c.id] || {};
  const pick = (v, label) => `<button class="pick ${m.choice === v ? "on" : ""}" data-act="deccomp" data-id="${c.id}" data-v="${v}" aria-pressed="${m.choice === v}">${label}</button>`;
  const parts = (c.parts || []).map((p) => p.added
    ? `<div class="addition"><span class="add-tag">Added to step ${p.step}</span>${latexLite(named(p.text))}</div>`
    : latexLite(named(p.text))).join("");
  const steps = (c.steps || []).map((s) => `<li>${inner(latexLite(named(s.claim)))}</li>`).join("");
  return `<div class="dcard ${compSettled(m) ? "ok" : ""}" id="dec-${c.id}">
    <div class="dcard-h"><span class="chip blue">Sketch completed by its own steps</span><b>${esc(c.name)}</b>${c.context && c.context.pages.length ? `<span class="muted small">${pagesPhrase(c.context)}</span>` : ""}</div>
    <p class="muted small">Your proof is a sketch. It was completed by justifying each of its steps, in your order and by your ideas: your words are unchanged, and every added passage is marked. Each addition passed exact computation where it computes, an independent reading for faithfulness to its step, and the proof check with a second opinion. Nothing changes unless you choose it.</p>
    <div class="dlabel">Statement (not changed)</div><div class="tex-block">${latexLite(named(c.statement))}</div>
    ${steps ? `<details class="more" style="margin-top:8px"><summary>The steps of your sketch (${(c.steps || []).length})</summary><ol class="plain-list">${steps}</ol></details>` : ""}
    <div class="twocol" style="margin-top:10px"><div><div class="col-h">Your sketch</div><div class="tex-block">${latexLite(named(c.sketch))}</div></div>
      <div><div class="col-h">Completed proof, additions marked</div><div class="tex-block new">${parts}</div></div></div>
    <div class="picks" role="group" aria-label="Your decision">${pick("accept", "Replace my sketch with this proof")}${pick("edit", "Edit it, then replace")}${pick("instruct", "Ask for changes")}${pick("keep", "Keep my sketch")}</div>
    ${m.choice === "edit" ? `<div class="ownbox"><textarea class="mono" rows="12" data-compedit="${c.id}" aria-label="The completed proof, to edit">${esc(m.text || c.proof)}</textarea>
      <div class="row" style="margin-top:6px"><span class="muted small">This text replaces the sketch as you leave it. It is your own wording, so it is not checked again.</span><span class="tb-space"></span>
      <button class="btn small primary" data-act="decnext" data-id="${c.id}">Done, next item</button></div></div>` : ""}
    ${m.choice === "instruct" ? `<div class="ownbox"><textarea rows="3" data-compnote="${c.id}" placeholder="What should be added or changed, e.g. 'show why the map is injective in step 2'. You can use $...$ for mathematics." aria-label="Your instruction">${esc(m.note || "")}</textarea>
      <div class="row" style="margin-top:6px"><span class="muted small">It is done again with your instruction, by the same rules and checks, when the final version is written.</span><span class="tb-space"></span>
      <button class="btn small primary" data-act="decnext" data-id="${c.id}" ${(m.note || "").trim() ? "" : "disabled"} id="cnext-${c.id}">Done, next item</button></div></div>` : ""}</div>`;
}
function remarkCard(r) {
  const D = S.decide;
  return `<div class="dcard" id="dec-${r.id}"><div class="dcard-h"><span class="chip">${esc(r.source.replace(/^./, (ch) => ch.toUpperCase()))}</span>
      ${r.where ? `<span class="muted small">${esc(named(labelName(r.where) || r.where))}${r.context && r.context.pages.length ? ", " + pagesPhrase(r.context) : ""}</span>` : ""}</div>
    <p>${inner(latexLite(named(r.comment)))}</p>${r.action ? `<p class="small"><b>Asked for:</b> ${inner(latexLite(named(r.action)))}</p>` : ""}
    <p class="muted small">No answer is needed. If something should be done, write it here and it is carried out on the passage concerned.</p>
    <textarea rows="3" data-remark="${r.id}" placeholder="Optional: what should be done about it" aria-label="Your instruction for this remark">${esc(D.remarks[r.id] || "")}</textarea>
    <div class="row" style="margin-top:8px"><button class="btn small" data-act="decread" data-id="${r.id}">${(D.remarks[r.id] || "").trim() ? "Done, next item" : "Nothing to do, next item"}</button></div></div>`;
}
function decideDetail(q) {
  const Q = decideQueue(), k = Q.findIndex((r) => r.x.id === q.x.id), done = settledAs(q);
  const card = q.kind === "simplify" ? proposalCard(q.x, true) : q.kind === "complete" ? completeCard(q.x) : q.kind === "issue" ? issueCard(q.x, true) : remarkCard(q.x);
  return `<div class="dq-top"><span class="muted small">Item ${k + 1} of ${Q.length}</span>
      <span class="tb-space"></span>
      <button class="btn small" data-act="decstep" data-d="-1" ${k <= 0 ? "disabled" : ""}>Previous</button>
      <button class="btn small" data-act="decstep" data-d="1" ${k >= Q.length - 1 ? "disabled" : ""}>Next</button></div>
    <h2 class="dq-title">${esc(queueTitle(q))}</h2>
    ${done ? `<div class="dq-done"><span class="dq-tick" aria-hidden="true">✓</span> Settled: ${esc(done)}. You can still change it below.</div>` : ""}
    <div class="dq-cols"><div class="dq-card">${card}</div>
      <div class="dq-ref"><div class="dq-ref-h">For checking: ${esc(queueTitle(q))}</div>${decidePanel(q.x)}</div></div>`;
}
function viewDecide() {
  const R = S.b.revision || {}, x = st("decide");
  if (!R.referee && x.status !== "done") return `<div class="wrap"><div class="empty"><strong>Not reached yet</strong>This step opens once the first draft has been re-read: for shorter arguments, for the correctness of every proof, and as a referee would read it.</div></div>`;
  const it = R.items;
  if (!it.simplify.length && !(it.complete || []).length && !it.issues.length && !it.remarks.length) {
    return `<div class="wrap"><div class="head"><h1>Your decisions</h1><p>The three readings of the first draft found nothing mathematical that needs your decision, so this step was passed. Language, tone and typography are corrected in the final version without asking you.</p></div>
      <div class="sheet"><div class="sec"><div class="row"><button class="btn" data-act="view" data-view="simplify">Shorter arguments</button><button class="btn" data-act="view" data-view="verify">Proof check</button>
        <button class="btn" data-act="view" data-view="referee">Referee's report</button></div></div></div></div>`;
  }
  if (!S.decide || S.decide.pid !== S.pid || S.decide.sig !== decideSig()) initDecide();
  const D = S.decide, c = decideCounts(), Q = decideQueue();
  const cur = D.focus === "__draft" ? null : currentItem();
  const open = Q.filter((q) => !settledAs(q)), done = Q.filter((q) => settledAs(q));
  const tab = D.tab === "done" ? "done" : "open";
  const entry = (q) => {
    const s = settledAs(q), on = cur && cur.x.id === q.x.id;
    const pages = q.x.context && q.x.context.pages.length ? `notes p. ${q.x.context.pages.join(", ")}` : "";
    return `<button class="dq-item ${on ? "on" : ""} ${s ? "ok" : ""}" data-act="decfocus" data-id="${q.x.id}" aria-current="${on}">
      <span class="dq-mark ${s ? "ok" : q.need ? "need" : ""}" aria-hidden="true">${s ? "✓" : ""}</span>
      <span class="dq-body"><span class="dq-t">${esc(queueTitle(q))}</span>
        <span class="dq-s">${s ? esc(s) : queueText(q)}</span>${pages ? `<span class="dq-p">${pages}</span>` : ""}</span></button>`;
  };
  const needOpen = open.filter((q) => q.need), optOpen = open.filter((q) => !q.need);
  const list = tab === "open"
    ? (open.length ? `${needOpen.length ? `<div class="dq-g">Needs your decision</div>${needOpen.map(entry).join("")}` : ""}
        ${optOpen.length ? `<div class="dq-g">Remarks, no answer needed</div>${optOpen.map(entry).join("")}` : ""}`
      : `<p class="muted small" style="padding:12px">Everything is settled. Press “Write the final version”.</p>`)
    : (done.length ? done.map(entry).join("") : `<p class="muted small" style="padding:12px">Nothing settled yet.</p>`);
  return `<div class="dq">
    <aside class="dq-list" aria-label="Items to decide">
      <div class="dq-head"><h1>Your decisions</h1><p class="muted small">Only what touches the mathematics is asked. Language and typography are corrected for you.</p>
        <div class="dq-prog"><div style="width:${Q.length ? Math.round(100 * done.length / Q.length) : 100}%"></div></div>
        <div class="muted small">${done.length} of ${Q.length} settled</div></div>
      <div class="dq-tabs" role="tablist">
        <button role="tab" class="dq-tab ${tab === "open" ? "on" : ""}" data-act="dectab" data-v="open" aria-selected="${tab === "open"}">To do <span class="dq-n">${open.length}</span></button>
        <button role="tab" class="dq-tab ${tab === "done" ? "on" : ""}" data-act="dectab" data-v="done" aria-selected="${tab === "done"}">Settled <span class="dq-n">${done.length}</span></button></div>
      <div class="dq-items">${list}</div>
      ${S.b.draft ? `<button class="dq-item dq-draft ${D.focus === "__draft" ? "on" : ""}" data-act="decfocus" data-id="__draft"><span class="dq-mark" aria-hidden="true">≡</span><span class="dq-body"><span class="dq-t">The whole first draft</span><span class="dq-s">What all of these items are about</span></span></button>` : ""}
    </aside>
    <section class="dq-detail" id="dqdetail">
      ${x.status === "done" ? `<div class="banner blue" style="margin-bottom:12px"><div class="grow">You decided this already. Deciding again makes the final version again; edits made in the paper view since then are lost.</div></div>` : ""}
      ${cur ? decideDetail(cur) : `<h2 class="dq-title">The first draft</h2><div class="sheet"><div class="sec"><div id="draftHost">${S.b.draft ? S.b.draft.html : ""}</div></div></div>`}
    </section></div>
    <div class="gatebar"><span id="deccount">${c.done} of ${c.need} decided</span><span class="muted small" style="margin-left:10px">${c.need - c.done ? "Remarks are optional." : ""}</span><span class="tb-space"></span>
      <button class="btn primary big" data-act="decide" ${S.busy || running() ? "disabled" : ""}>${S.busy === "decide" ? "Saving…" : "Write the final version"}</button></div>`;
}

/* ---------------------------------------------------------------- journal */
const ORIGIN = { default: ["app default", ""], typical: ["typical for the publisher, not checked for this journal", "hl"], guide: ["from the journal's guide", "green"], author: ["set by you", "blue"] };
function reqRows(prof) {
  const og = prof.origin || {}, T = S.meta.templates;
  const chip = (k) => { const o = ORIGIN[og[k]] || ORIGIN.default; return `<span class="chip ${o[1]}">${o[0]}</span>`; };
  const range = (lo, hi, unit) => (lo && hi ? (lo === hi ? `${lo} ${unit}` : `${lo} to ${hi} ${unit}`) : hi ? `at most ${hi} ${unit}` : lo ? `at least ${lo} ${unit}` : "no limit known");
  const review = [prof.anonymous ? "anonymous (no author names)" : "", prof.line_numbers ? "line numbers" : "", prof.double_spacing ? "double spacing" : ""].filter(Boolean);
  const rows = [
    ["LaTeX template", esc((T[prof.template] || {}).label || prof.template), chip("template")],
    ["Abstract", range(prof.abstract_min_words, prof.abstract_max_words, "words"), chip(prof.abstract_max_words ? "abstract_max_words" : "abstract_min_words")],
    ["Keywords", range(prof.keywords_min, prof.keywords_max, "keywords"), chip(prof.keywords_max ? "keywords_max" : "keywords_min")],
    ["Subject classification (MSC)", { required: "required", optional: "optional", none: "not used" }[prof.msc] || "optional", chip("msc")],
    ["References", `${prof.ref_style === "alpha" ? "alphabetic labels such as [AB20]" : "numbered"}, ${prof.ref_order === "appearance" ? "in order of first citation" : "alphabetical by author"}`, chip(og.ref_style !== "default" ? "ref_style" : "ref_order")],
    ["Manuscript for review", review.join(", ") || "nothing special", chip(prof.anonymous ? "anonymous" : prof.line_numbers ? "line_numbers" : "double_spacing")],
    ["Statements (funding, competing interests)", prof.declarations ? "required in the manuscript" : "not required", chip("declarations")],
    ["Length", prof.page_limit ? `at most ${prof.page_limit} pages` : "no limit known", chip("page_limit")],
  ];
  return `<table class="grid-table req"><tbody>${rows.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join("")}</tbody></table>
    ${(prof.notes || []).length ? `<div class="dlabel" style="margin-top:12px">Also stated in the journal's guide</div><ul class="plain-list">${prof.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
}
function journalSearchBox() {
  const R = S.jr;
  return `<div class="row"><input type="text" class="grow" id="jrq" value="${esc(R.q)}" placeholder="Journal name, e.g. Journal of Number Theory">
      <button class="btn" data-act="jrsearch" ${R.searching ? "disabled" : ""}>${R.searching ? "Searching…" : "Find"}</button></div>
    ${journalResults(R.results, R.message, "jrpick")}
    ${R.results && R.q.trim().length > 2 ? `<button class="btn small quiet" data-act="jrtyped" style="margin-top:6px">Not in the list? Use “${esc(R.q.trim())}” as typed</button>` : ""}
    <p class="muted small" style="margin-top:8px">Journals are looked up by name in OpenAlex and Crossref, which index practically every scholarly journal. Nothing about a journal is assumed from a built-in list.</p>`;
}
function journalPanel() {
  if (!S.jr) S.jr = newJr();
  const R = S.jr, J = S.b.journal, prof = S.b.profile, P = S.b.paper, T = S.meta.templates;
  const tinfo = T[prof.template] || {};
  const fam = S.meta.families.find((f) => f.id === prof.family);
  const house = fam && fam.house && !fam.house.startsWith(prof.template)
    ? `<p class="muted small" style="margin-top:6px">${esc(fam.name)} has its own template, ${esc(fam.house)}, which is not built into this app. The paper is set with the template “${esc(tinfo.label)}” and this journal's content requirements. Many journals accept a standard class for the first submission; check the guide for authors.</p>` : "";
  const fb = P && P.compile && P.compile.fallback;
  let h = `<div class="sheet"><div class="sec"><h2>Target journal</h2>`;
  if (J) {
    h += `<p>The final paper is prepared to this journal's requirements. Changing the journal refits the abstract and keywords and rebuilds every file; the mathematics is not touched.</p>
      <div class="jr-current"><div><div class="jr-name">${esc(J.name)}</div><div class="jr-meta">${esc(J.publisher || "publisher not given")}${J.issn ? ", ISSN " + esc(J.issn) : ""}
        ${J.homepage ? ` · <a href="${esc(J.homepage)}" target="_blank" rel="noopener">journal's site</a>` : ""}</div></div>
        <span class="tb-space"></span><button class="btn small" data-act="jrchange">Change</button><button class="btn small quiet" data-act="jrremove" ${R.saving ? "disabled" : ""}>Remove</button></div>
      ${R.changing ? `<div style="margin-top:14px">${journalSearchBox()}</div>` : ""}`;
  } else {
    h += `<p>No journal is chosen, so the paper is set in the default layout. Choose the journal you intend to submit to and the paper is prepared to its requirements.</p>${journalSearchBox()}`;
  }
  h += `</div><div class="sec"><h2>Requirements in force</h2><p>${J ? "Where each value comes from is shown on the right. Values marked “typical for the publisher” are what that publisher's mathematics journals usually ask; they were not checked for this journal. Reading the journal's own guide below replaces them." : "These are the app's defaults."}</p>
    ${reqRows(prof)}
    <p class="muted small" style="margin-top:10px">${esc(tinfo.note || "")}</p>${house}
    ${fb ? `<div class="banner" style="margin-top:12px"><div class="grow"><b>The PDF is set in the standard article class.</b> The class ${esc(fb.template)} could not be used on this computer${fb.missing ? ` (${esc(fb.missing)} is not installed)` : ""}.
      The download contains both sources: main.tex for the journal's class and main_standard_class.tex.</div></div>` : ""}</div>`;
  if (P) {
    const list = P.checklist || [];
    h += `<div class="sec"><h2>Checklist for this paper</h2><p>Computed from the paper as it stands now.</p>
      ${list.length ? list.map((x) => `<div class="check-row ${x.ok ? "ok" : "open"}"><span class="check-mark">${x.ok ? "✓" : "!"}</span><div><b>${esc(x.label)}</b><div class="small muted">${esc(x.detail)}</div></div></div>`).join("")
        : `<p class="muted">No requirement to check${J ? "" : " until a journal is chosen"}.</p>`}</div>`;
  }
  if (J) {
    const G = R.guide;
    h += `<div class="sec"><h2>Read the journal's guide for authors</h2><p>Give the web address of the journal's instructions for authors, or paste their text. The requirements stated there are extracted and shown to you; nothing is applied until you accept it.</p>
      <textarea rows="3" id="guidesrc" placeholder="https://… or the pasted text of the instructions">${esc(R.guideSrc != null ? R.guideSrc : (J.guide_url || J.homepage || ""))}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn" data-act="jrguide" ${R.guideBusy ? "disabled" : ""}>${R.guideBusy ? "Reading…" : "Read the guide"}</button>
        <span class="muted small">Some publishers do not let programs read their pages. If that happens, open the page in your browser and paste its text here.</span></div>
      ${G ? `<div class="guide-out"><div class="dlabel">Found in the guide${G.values.journal_named ? " of " + esc(G.values.journal_named) : ""}</div>
        ${guideList(G.values)}
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="jruse" ${R.saving ? "disabled" : ""}>${R.saving === "guide" ? "Applying…" : "Use these values"}</button><button class="btn quiet" data-act="jrdiscard">Discard</button>
          <span class="muted small">Compare with the guide before accepting: a model read it for you.</span></div></div>` : ""}</div>
    <div class="sec"><details class="more" ${Object.keys(R.cuDraft || J.custom || {}).length ? "open" : ""}><summary>Set the requirements yourself</summary>
      <p class="muted small" style="margin-bottom:10px">What you set here overrides everything else. Leave a field as “not set” to keep the value shown above.</p>${customForm(R.cuDraft || J.custom || {})}
      <button class="btn" data-act="jrcustom" ${R.saving ? "disabled" : ""}>${R.saving === "custom" ? "Saving…" : "Save these requirements"}</button></details></div>`;
  }
  const stm = R.stmDraft || S.b.statements || {};
  h += `<div class="sec"><h2>Your statements</h2><p>Acknowledgements and declarations are yours alone to make, so they are never written by a model. What you enter is added to the paper as it is.${prof.anonymous ? " In the anonymous version, acknowledgements and funding are left out." : ""}</p>
    ${S.meta.statements.map((x) => `<label class="field" style="margin-bottom:10px"><span>${esc(x.name)}</span><textarea rows="2" data-stm="${x.id}" placeholder="Leave empty to omit">${esc(stm[x.id] || "")}</textarea></label>`).join("")}
    <button class="btn" data-act="jrstatements" ${R.saving ? "disabled" : ""}>${R.saving === "statements" ? "Saving…" : "Save statements"}</button></div></div>`;
  return h;
}
function guideList(v) {
  const rows = [];
  const range = (lo, hi) => (lo && hi ? `${lo} to ${hi}` : hi ? `at most ${hi}` : `at least ${lo}`);
  if (v.abstract_min_words || v.abstract_max_words) rows.push(["Abstract", range(v.abstract_min_words, v.abstract_max_words) + " words"]);
  if (v.keywords_min || v.keywords_max) rows.push(["Keywords", range(v.keywords_min, v.keywords_max)]);
  if (v.msc) rows.push(["Subject classification (MSC)", v.msc]);
  if (v.ref_style) rows.push(["Reference labels", v.ref_style === "alpha" ? "alphabetic" : "numbered"]);
  if (v.ref_order) rows.push(["Reference order", v.ref_order === "appearance" ? "order of first citation" : "alphabetical"]);
  for (const [k, name] of [["anonymous", "Anonymous manuscript"], ["line_numbers", "Line numbers"], ["double_spacing", "Double spacing"], ["declarations", "Statements required"]]) if (k in v) rows.push([name, v[k] ? "yes" : "no"]);
  if (v.page_limit) rows.push(["Page limit", v.page_limit]);
  if (v.latex_class) rows.push(["LaTeX class named", v.latex_class]);
  for (const n of v.notes || []) rows.push(["Note", n]);
  return rows.length ? `<table class="grid-table req"><tbody>${rows.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">The text states none of the requirements this app can apply. Check that it is the journal's instructions for authors.</p>`;
}
function customForm(c) {
  const num = (k, label) => `<label class="field"><span>${label}</span><input type="text" inputmode="numeric" data-cu="${k}" value="${c[k] ?? ""}" placeholder="not set"></label>`;
  const sel = (k, label, opts) => `<label class="field"><span>${label}</span><select data-cu="${k}"><option value="">not set</option>${opts.map(([v, n]) => `<option value="${v}" ${String(c[k]) === v ? "selected" : ""}>${n}</option>`).join("")}</select></label>`;
  const yn = [["true", "yes"], ["false", "no"]];
  return `<div class="grid4">${sel("template", "LaTeX template", Object.entries(S.meta.templates).map(([k, t]) => [k, t.label]))}
    ${num("abstract_min_words", "Abstract, fewest words")}${num("abstract_max_words", "Abstract, most words")}${num("page_limit", "Most pages")}
    ${num("keywords_min", "Fewest keywords")}${num("keywords_max", "Most keywords")}
    ${sel("msc", "Subject classification", [["required", "required"], ["optional", "optional"], ["none", "not used"]])}
    ${sel("ref_style", "Reference labels", [["numeric", "numbered"], ["alpha", "alphabetic"]])}${sel("ref_order", "Reference order", [["alphabetical", "alphabetical"], ["appearance", "order of citation"]])}
    ${sel("anonymous", "Anonymous manuscript", yn)}${sel("line_numbers", "Line numbers", yn)}${sel("double_spacing", "Double spacing", yn)}${sel("declarations", "Statements required", yn)}</div>`;
}
async function postJournal(body, saving, okMsg) {
  captureJournalForm();
  S.jr.saving = saving; redrawJournal();
  try {
    const r = await api(`/api/projects/${S.pid}/journal`, { method: "POST", body });
    S.b.journal = r.journal; S.b.profile = r.profile; if (r.paper) S.b.paper = r.paper;
    S.jr.saving = ""; S.jr.changing = false; S.jr.results = null; S.jr.q = "";
    if (saving === "statements") S.jr.stmDraft = null; else S.jr.cuDraft = null;
    toast(okMsg + (r.paper ? " The paper was rebuilt." : ""));
    await refresh();
  } catch (e) { S.jr.saving = ""; toast(e.message, true); }
  redrawJournal();
}
function captureJournalForm() {
  /* What is typed in the journal forms but not yet saved survives a redraw. */
  if (!S.jr) return;
  const stm = $$("[data-stm]");
  if (stm.length) { S.jr.stmDraft = {}; stm.forEach((el) => { S.jr.stmDraft[el.dataset.stm] = el.value; }); }
  const cu = $$("[data-cu]");
  if (cu.length) { S.jr.cuDraft = {}; cu.forEach((el) => { if (el.value.trim()) S.jr.cuDraft[el.dataset.cu] = el.value.trim(); }); }
}
function redrawJournal() {
  if (!$("#overlay").hidden && $("#jrdialog")) { captureJournalForm(); showDialog(journalDialog()); } else render();
}
function journalDialog() {
  return `<div class="dialog wide" id="jrdialog" role="dialog" aria-modal="true" aria-label="Target journal"><div class="dlg-h"><h2>Target journal</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
    <div class="dlg-b jr-dlg">${journalPanel()}</div></div>`;
}

/* -------------------------------------------------- the record of the notes */
function stamp(t) {
  return t ? new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}
function proofSummary(pr) {
  if (!pr || pr.missing) {
    return { cls: "", chip: "Notes not recorded",
      line: pr && pr.error ? pr.error : "These notes were uploaded before Scriptavera kept records of notes." };
  }
  if (pr.earliest) {
    return { cls: "green", chip: "Notes recorded " + new Date(pr.earliest * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }),
      line: `Your notes existed, exactly as uploaded, no later than ${stamp(pr.earliest)}. An independent time-stamp authority signed this.` };
  }
  if (!pr.enabled) return { cls: "", chip: "Notes signed", line: "The record of your notes is signed on this computer. Independent timestamps are turned off in Settings." };
  return { cls: "hl", chip: "Notes signed, timestamp pending",
    line: "The record of your notes is signed. The independent timestamps are asked for in the background and added by themselves (the computer may have been offline)." };
}
function proofSheet() {
  const pr = S.b.proof || {}, s = proofSummary(pr), o = pr.ots || {};
  const btc = o.status === "bitcoin" ? ` Anchored in Bitcoin block ${esc(String(o.height))}.` : o.status === "pending" ? " The Bitcoin confirmation follows within hours." : "";
  return `<div class="sheet"><h3>Record of your notes</h3><p class="small" style="margin:4px 0 10px">${esc(s.line)}${pr.missing ? "" : btc}</p>
    <div class="dl">${pr.missing ? `<button class="btn" data-act="proof"><span>Record them now</span></button>`
      : `<a class="btn" href="/api/projects/${S.pid}/proof/certificate" target="_blank" rel="noopener"><span>Certificate</span><span class="small">html</span></a>
         <a class="btn" href="/api/projects/${S.pid}/proof/bundle?files=1"><span>Proof, with your notes</span><span class="small">zip</span></a>
         <button class="btn" data-act="proof"><span>Details, and checking a file</span></button>`}</div></div>`;
}
function proofDialog() {
  const r = S.proofData || {}, pr = r.proof || {}, s = proofSummary(pr), C = S.proofCheck, o = pr.ots || {};
  const row = (k, v) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`;
  const tsa = (pr.tsa || []).map((x) => row("Time-stamp authority", `${esc(stamp(x.time))}<div class="muted small" style="font-weight:400">${esc(x.tsa || x.url)}</div>`)).join("");
  const btc = o.status === "bitcoin" ? row("Bitcoin blockchain", `block ${esc(String(o.height))}${o.block_time ? ", mined " + esc(stamp(o.block_time)) : ""}${o.verified ? " (checked against the block)" : ""}`)
    : o.status === "pending" ? row("Bitcoin blockchain", `<span style="font-weight:400">submitted; the confirmation is added by itself within hours</span>`) : "";
  const failed = (pr.tsa_failed || []).length && !(pr.tsa || []).length;
  const files = (pr.files || []).map((f) => `<tr><td>${esc(f.name)}<div class="muted small">${Number(f.bytes || 0).toLocaleString()} bytes</div></td>
    <td style="font:12px/1.5 var(--mono);word-break:break-all">${esc(f.sha256)}</td></tr>`).join("");
  let checked = "";
  if (C && C.busy) checked = `<p class="muted small" style="margin-top:10px"><span class="spinner"></span> Checking…</p>`;
  else if (C && C.error) checked = `<div class="flag red" style="margin-top:10px">${esc(C.error)}</div>`;
  else if (C) {
    checked = `<div style="margin-top:10px">${(C.findings || []).map((f) => `<div class="flag ${f.ok === true ? "green" : f.ok === false ? "red" : ""}">${esc(f.text)}</div>`).join("")}
      <p style="margin-top:6px"><b>${C.ok ? "The record holds" + (C.earliest ? `: these files existed no later than ${esc(stamp(C.earliest))}.` : ".") : "The record does not hold for what was given, or it has no timestamp yet."}</b></p></div>`;
  }
  const mAuthors = ((S.b && S.b.state && S.b.state.meta) || {}).authors || [];
  const R = S.recNow || (S.recNow = { recorder: "0", name: "", declare: false });
  const rby = declarationMaker(mAuthors, R.recorder, R.name);
  const rOpts = mAuthors.map((a, i) => `<option value="${i}" ${rby && rby.is_author && rby.index === i ? "selected" : ""}>${esc(a.name)}</option>`).join("")
    + `<option value="other" ${R.recorder === "other" ? "selected" : ""}>Someone who is not an author, for the authors, with their consent</option>`;
  const authorsList = (pr.authors || []).map((a) => `${esc(a.name)}${a.affiliation ? `<span class="muted" style="font-weight:400">, ${esc(a.affiliation)}</span>` : ""}${a.orcid ? ` <a href="https://orcid.org/${esc(a.orcid)}" target="_blank" rel="noopener" style="font-weight:400">ORCID ${esc(a.orcid)}</a>` : ""}`).join("<br>");
  const by = pr.recorded_by || {};
  const body = pr.missing
    ? `<p>${esc(s.line)} You can record them now: their fingerprints are taken from the page images Scriptavera kept, signed with your key and timestamped. Such a record carries today's date, not the day of the upload, and says so.</p>
      ${mAuthors.length ? `<div class="grid2" style="margin-top:12px"><label class="field"><span>Who is making this record?</span><select data-rrecorder>${rOpts}</select></label>
        ${R.recorder === "other" ? `<label class="field"><span>Your name</span><input type="text" data-rname value="${esc(R.name)}" placeholder="Full name"></label>` : "<span></span>"}</div>
        <label class="check declare-line"><input type="checkbox" data-rdeclare ${R.declare ? "checked" : ""}><span id="rdeclareText">${rby ? esc(declarationText(mAuthors.length, rby)) : "Give your name: the declaration says who makes it."}</span></label>`
        : `<div class="flag" style="margin-top:12px">No author is named for this paper. Name the authors in the paper's details first: the record says who wrote the notes.</div>`}
      <div class="row" style="margin-top:12px"><button class="btn primary" data-act="proofnow" ${S.busy === "proof" || !mAuthors.length ? "disabled" : ""}>${S.busy === "proof" ? "Recording…" : "Record these notes now"}</button></div>`
    : `<p style="font-size:15px;margin:10px 0 12px">${esc(s.line)}</p>
      ${authorsList ? row((pr.authors || []).length > 1 ? "Authors" : "Author", authorsList) : ""}
      ${pr.declaration ? `<div class="stat"><span>Declaration, signed</span></div><blockquote class="decl">${esc(pr.declaration)}</blockquote>` : ""}
      ${row("Recorded by", esc(by.name || (pr.author || {}).name || "the author named for this paper") + (by.name ? `<span class="muted" style="font-weight:400">${by.is_author ? ", an author" : ", for the authors"}</span>` : ""))}
      ${row("Signing key", `<span style="font:12px var(--mono)">${esc(pr.fingerprint || "")}</span>`)}
      ${tsa}${btc}
      ${row("Fingerprint of the record", `<span style="font:12px var(--mono);word-break:break-all">${esc(pr.digest || "")}</span>`)}
      ${failed ? `<div class="flag" style="margin-top:10px">The timestamp services could not be reached yet${pr.tsa_failed[0].error ? ` (${esc(pr.tsa_failed[0].error)})` : ""}. They are asked again by themselves while the app is open.
        <button class="btn small" data-act="proofretry">Ask now</button></div>` : ""}
      ${pr.note ? `<p class="muted small" style="margin-top:8px">${esc(pr.note)}</p>` : ""}
      <h3>The files recorded</h3><table class="grid-table"><tbody>${files}</tbody></table>
      <div class="row" style="margin-top:14px"><a class="btn primary" href="/api/projects/${S.pid}/proof/certificate" target="_blank" rel="noopener">Open the certificate</a>
        <a class="btn" href="/api/projects/${S.pid}/proof/bundle?files=1">Download the proof with your notes</a>
        <a class="btn" href="/api/projects/${S.pid}/proof/bundle">The proof only</a></div>`;
  return `<div class="dialog" role="dialog" aria-modal="true" aria-label="Record of your notes"><div class="dlg-h"><h2>Record of your notes</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
    <div class="dlg-b">${body}
      <h3>What it proves</h3><p>That files with exactly these fingerprints existed no later than the time stamped, and that your key signed the record. Only the fingerprints were sent to the timestamping services; your notes did not leave this computer for this.</p>
      <p>It does not prove by itself who first had the ideas, and copyright protects how something is written, not mathematical ideas as such. It is evidence of priority, the way a dated preprint is, but private. To tie the key to you, publish its fingerprint where people know it is yours: your ORCID record, your university page.</p>
      <h3>Check files against a record</h3><p>Choose the proof (the zip) together with the notes it is about. Anyone can make the same check without Scriptavera; the certificate says how.</p>
      <div class="row"><input type="file" id="proofFiles" multiple aria-label="The proof and the notes"><button class="btn" data-act="proofcheck">Check</button></div>${checked}
    </div></div>`;
}

/* ------------------------------------------------------------ paper view */
function viewPaper() {
  const P = S.b.paper;
  if (!P) return viewStage("assemble");
  const a = P.audit || { total: 0, preserved: 0, altered: [], missing: [], added: [], agreed: [] };
  const agreed = a.agreed || [];
  const auditIssues = a.altered.length + a.missing.length + a.added.length;
  const badCites = (P.cite_checks || []).filter((c) => c.verdict !== "supported").length + (P.cite_notes || []).length;
  const texIssues = (P.compile.errors || []).length + (P.compile.notes || []).length + (P.compile.ok ? 0 : (P.lint || []).length);
  const rv = P.revision || {};
  const openReq = (P.checklist || []).filter((x) => !x.ok).length;
  const tabs = [["paper", "Paper", 0], ["revision", "Revision", (rv.unapplied || []).length], ["checks", "Fidelity", auditIssues], ["refs", "Citations", badCites],
    ["journal", "Journal", openReq], ["latex", "LaTeX", texIssues], ["activity", "Who did what", 0]];
  let body = "";
  if (S.tab === "paper") {
    body = `<div class="pv"><div id="paperHost">${P.html}</div><div class="pv-side">${S.edit ? editorPanel() : sidePanel(P, a, auditIssues, badCites, texIssues, openReq)}</div></div>`;
  } else if (S.tab === "revision") {
    body = revisionTab(P, rv);
  } else if (S.tab === "checks") {
    const pair = (label, t) => `<span class="muted">${label}</span><span>${inner(mixed("$" + t + "$"))}</span>`;
    body = `<div class="sheet"><div class="sec"><h2>Did your mathematics survive unchanged?</h2>
      <p>Each formula in the transcript you approved is compared, symbol by symbol, with the typeset paper. This is a mechanical comparison, not a model's opinion. Formulas that changed because of a decision you made are listed separately.</p>
      <div class="row"><span class="chip ${auditIssues ? "hl" : "green"}">${a.preserved} of ${a.total} formulas identical</span>
        ${agreed.length ? `<span class="chip blue">${agreed.length} changed with your approval</span>` : ""}
        <span class="chip ${a.altered.length ? "red" : ""}">${a.altered.length} changed</span><span class="chip ${a.missing.length ? "red" : ""}">${a.missing.length} missing</span>
        <span class="chip ${a.added.length ? "hl" : ""}">${a.added.length} not in your notes</span></div></div>
      ${auditIssues ? `<div class="sec"><div class="aud">${a.altered.map((x) => `<div class="aud-item"><span class="chip red">changed</span><div class="aud-pair">${pair("Your notes, p. " + x.page, x.source)}${pair("In the paper", x.found)}</div></div>`).join("")}
        ${a.missing.map((x) => `<div class="aud-item"><span class="chip red">missing</span><div class="aud-pair">${pair("Your notes, p. " + x.page, x.source)}<span class="muted">In the paper</span><span class="muted">not found</span></div></div>`).join("")}
        ${a.added.map((x) => `<div class="aud-item"><span class="chip hl">added</span><div class="aud-pair">${pair("In the paper", x.tex)}<span class="muted">Your notes</span><span class="muted">no matching formula</span></div></div>`).join("")}</div>
        <p class="muted small" style="margin-top:12px">Open the Paper tab and click the passage to correct it. A formula written across several lines in your notes can show up here even when it is intact.</p></div>`
        : `<div class="sec"><p>Every formula from your approved transcript is in the paper exactly as you wrote it${agreed.length ? ", apart from the changes you approved" : ""}.</p></div>`}
      ${agreed.length ? `<div class="sec"><h2>Changed with your approval</h2><p>These formulas of your notes are no longer in the paper in their original form, because a proof was shortened or corrected by a decision of yours.</p>
        <div class="aud">${agreed.map((x) => `<div class="aud-item"><span class="chip blue">approved</span><div class="aud-pair">${pair("Your notes, p. " + x.page, x.source)}</div></div>`).join("")}</div></div>` : ""}</div>`;
  } else if (S.tab === "refs") {
    const lib = Object.fromEntries((S.b.library || []).map((p) => [p.key, p]));
    body = `<div class="sheet"><div class="sec"><h2>References</h2><p>Only papers that the text actually cites are listed. Their details come from the scholarly databases.</p>
      ${P.refs.length ? `<ol>${P.refs.map((r) => `<li style="margin-bottom:8px"><b>${esc(r.title)}</b><br><span class="muted">${esc(authorsOf(r))}${r.year ? " (" + r.year + ")" : ""}${r.venue ? ", " + esc(r.venue) : ""}</span> ${paperLink(r)}
        ${(lib[r.key] || {}).fulltext ? `<span class="chip ${lib[r.key].fulltext === "full text" ? "green" : ""}">read: ${esc(lib[r.key].fulltext)}</span>` : '<span class="chip hl">not read in full</span>'}</li>`).join("")}</ol>` : `<p class="muted">The paper cites nothing.</p>`}</div>
      <div class="sec"><h2>Citation check</h2><p>For each sentence that cites a paper: do the reading notes of that paper support it?</p>
        ${(P.cite_notes || []).map((n) => `<div class="flag red">${esc(n)}</div>`).join("")}
        ${(P.cite_checks || []).length ? P.cite_checks.map((c) => `<div class="flag ${c.verdict === "supported" ? "green" : "red"}">
          <b>${esc(c.verdict)}</b> [${esc(c.key)}]: ${explained(c.reason)}<div class="small" style="margin-top:4px">${inner(latexLite(c.sentence))}</div></div>`).join("")
          : `<p class="muted">No cited sentences to check.</p>`}</div>
      <div class="sec"><h2>Read but not cited</h2><p>These were read for context and are deliberately left out of the reference list.</p>
        ${P.uncited.length ? P.uncited.map((u) => `<div>${esc(u.title)}${u.year ? " (" + u.year + ")" : ""} <span class="chip">${esc(u.verdict || "")}</span></div>`).join("") : `<p class="muted">None.</p>`}</div></div>`;
  } else if (S.tab === "journal") {
    body = journalPanel();
  } else if (S.tab === "latex") {
    const fb = P.compile.fallback;
    body = `<div class="sheet"><div class="sec"><div class="row"><h2 class="grow">main.tex</h2><button class="btn small" data-act="copytex">Copy</button>
        <a class="btn small" href="/api/projects/${S.pid}/download/main.tex">Download</a>
        ${(S.b.downloads || []).includes("main_standard_class.tex") ? `<a class="btn small" href="/api/projects/${S.pid}/download/main_standard_class.tex">Standard-class version</a>` : ""}</div>
      <p>The file is generated by code from the passages you see in the Paper tab; no model retypes it. Class: ${esc((S.meta.templates[P.compile.template] || {}).label || P.compile.template)}.
        ${P.compile.engine ? (P.compile.ok ? (fb ? `The class ${esc(fb.template)} is not installed on this computer, so the PDF was compiled in the standard article class with ${esc(P.compile.engine)}.` : `Compiles cleanly with ${esc(P.compile.engine)} on this computer.`) : `Compiled with ${esc(P.compile.engine)}; errors are listed below.`) : "No LaTeX engine is installed here, so the file was checked for balanced braces, dollars and environments only. Upload the package to Overleaf to compile."}</p>
      ${(P.compile.errors || []).map((e) => `<div class="flag red">Line ${e.line}: ${esc(e.message)} ${e.block && e.block !== "refs" ? `<button class="btn small" data-act="editblock" data-id="${esc(e.block)}">Open passage</button>` : (e.block === "refs" ? "(in the reference list)" : "")}</div>`).join("")}
      ${(P.compile.notes || []).map((e) => `<div class="flag">${esc(e.message)} ${e.block && e.block !== "refs" && e.block !== "intro" ? `<button class="btn small" data-act="editblock" data-id="${esc(e.block)}">Open passage</button>` : ""}</div>`).join("")}
      ${!P.compile.ok ? (P.lint || []).map((e) => `<div class="flag">${esc(e.message)} <button class="btn small" data-act="editblock" data-id="${esc(e.block)}">Open passage</button></div>`).join("") : ""}
      <pre class="src" id="texsrc">Loading…</pre></div></div>`;
  } else {
    const calls = S.b.calls || [];
    const byModel = {};
    calls.filter((c) => c.ok).forEach((c) => { const k = c.model; byModel[k] = byModel[k] || { n: 0, i: 0, o: 0 }; byModel[k].n++; byModel[k].i += c.in || 0; byModel[k].o += c.out || 0; });
    body = `<div class="sheet"><div class="sec"><h2>Models used for this paper</h2><p>${plural(calls.filter((c) => c.ok).length, "AI call")} in the latest runs, each with a single job.</p>
      <table class="calls"><thead><tr><th>Model</th><th class="num">Calls</th><th class="num">Tokens in</th><th class="num">Tokens out</th></tr></thead><tbody>
      ${Object.entries(byModel).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v.n}</td><td class="num">${v.i.toLocaleString()}</td><td class="num">${v.o.toLocaleString()}</td></tr>`).join("")}</tbody></table></div>
      <div class="sec"><h2>Every call</h2><p>Newest first.</p>${callsTable(null, 300)}</div></div>`;
  }
  const ed = S.b.editor || {};
  return `<div class="wrap wide"><div class="head head-row"><div class="grow"><h1>Your paper</h1>
      <p>The final version, after the three readings and your decisions. Click any passage to edit it. Every download is rebuilt from what you see here.
        ${ed.can ? (ed.exists ? " You also have a copy in the editor: changes made here do not go into it by themselves, and the editor offers to start again from this version."
          : " To work on the LaTeX files directly, with the PDF beside them, go to the editor.") : ""}</p></div>
      ${!running() ? `${ed.can ? `<a class="btn primary" href="/editor/${encodeURIComponent(S.pid)}">Go to editor</a>` : ""}<button class="btn" data-act="view" data-view="decide">Decide again</button><button class="btn" data-act="redo" data-stage="understand">Write the paper again</button>` : ""}</div>
    <div class="tabs" role="tablist">${tabs.map(([id, name, n]) => `<button class="tab ${S.tab === id ? "on" : ""}" role="tab" aria-selected="${S.tab === id}" data-act="tab" data-tab="${id}">${name}${n ? `<span class="count warn">${n}</span>` : ""}</button>`).join("")}</div>
    <div style="margin-top:18px">${body}</div></div>`;
}
function revisionTab(P, rv) {
  const now = (label, fallback) => labelName(label, true) || fallback;
  const polish = rv.polish || [], typ = rv.typography || {}, ref = rv.referee || {}, pr = rv.proofs || {};
  const typNames = { stops: "full stops and commas after displayed formulas or at the end of a paragraph", ties: "non-breaking spaces before references and citations", spacing: "spacing corrections",
    doubled: "doubled words removed", abbreviations: "spaces after abbreviations", capitals: "capitals before a number (Lemma, Section)", quotes: "quotation marks" };
  const typList = Object.entries(typ).filter(([, n]) => n).map(([k, n]) => `<li>${n} ${esc(typNames[k] || k)}</li>`).join("");
  const done = (rv.simplified || []).length + (rv.completed || []).length + (rv.corrected || []).length + (rv.instructed || []).length + (rv.remarks || []).length;
  return `<div class="sheet">
    <div class="sec"><h2>From the first draft to this version</h2><p>What the revision did, in the order it was done. Nothing mathematical was changed without your decision.</p>
      <div class="row"><span class="chip ${pr.checked === pr.clean ? "green" : "hl"}">${pr.checked || 0} proofs checked step by step, ${pr.clean || 0} without a finding</span>
        <span class="chip blue">${plural(done, "decided change")} made</span>${(rv.unapplied || []).length ? `<span class="chip red">${rv.unapplied.length} left for you</span>` : ""}
        <span class="chip">${polish.filter((x) => x.result === "edited").length} of ${polish.length} parts copy-edited</span></div></div>
    ${(rv.unapplied || []).length ? `<div class="sec"><h2>Left for you</h2><p>Decisions that could not be carried out safely by a model. Make these changes yourself in the Paper tab.</p>
      ${rv.unapplied.map((x) => `<div class="flag red"><b>${esc(x.id)}.</b> Not done because ${esc(named(x.why, true))}</div>`).join("")}</div>` : ""}
    <div class="sec"><h2>Your decisions carried out</h2>
      ${done ? "" : `<p class="muted">No change to the mathematics was decided.</p>`}
      ${(rv.simplified || []).map((x) => `<div class="flag green"><b>Shorter argument.</b> ${esc(named(x.idea, true))} <div class="small">Proofs rewritten: ${esc((x.labels || []).map((l, i) => now(l, (x.names || [])[i] || l)).join(", "))}${x.new ? `; new: ${esc(now(x.new, "a lemma"))}` : ""}.</div></div>`).join("")}
      ${(rv.completed || []).map((x) => `<div class="flag green"><b>The sketch of ${esc(now(x.label, x.name))} completed${x.how === "edit" ? ", as you edited it" : x.how === "instruct" ? ", done again as you asked" : ""}.</b>${x.note ? ` ${inner(mixed(x.note))}` : ""}</div>`).join("")}
      ${(rv.corrected || []).map((x) => `<div class="flag green"><b>Correction in the proof of ${esc(now(x.label, x.name))}.</b> ${inner(latexLite(named(x.problem, true)))}</div>`).join("")}
      ${(rv.instructed || []).map((x) => `<div class="flag green"><b>Your instruction for the proof of ${esc(now(x.label, x.name))}.</b> ${inner(mixed(x.note))}</div>`).join("")}
      ${(rv.remarks || []).map((x) => `<div class="flag green"><b>Your instruction${x.where ? " for " + esc(labelName(x.where, true) || x.where) : ""}.</b> ${inner(mixed(x.note))}</div>`).join("")}</div>
    <div class="sec"><h2>Copy-edit</h2><p>Every part was read once more for grammar, tone and consistency, together with the referee's points on presentation. An edit was kept only if it changed wording and nothing else: the same formulas, labels, citations and arrangement.</p>
      <table class="grid-table"><tbody>${polish.map((x) => `<tr><td>${esc(x.part)}</td><td>${x.result === "edited" ? '<span class="chip green">edited</span>' : esc(x.result)}</td></tr>`).join("")}</tbody></table></div>
    <div class="sec"><h2>Typography</h2><p>Corrected by rule, not by a model, and never inside a formula except for its closing punctuation.</p>
      ${typList ? `<ul class="plain-list">${typList}</ul>` : `<p class="muted">Nothing needed correcting.</p>`}</div>
    <div class="sec"><h2>The referee's reading of the first draft</h2>
      ${ref.summary ? `<div class="tex-block plain">${latexLite(ref.summary)}</div>` : ""}
      ${ref.recommendation ? `<p style="margin-top:8px">Recommendation at that point: <span class="chip">${esc(ref.recommendation)}</span>. The points on presentation below were given to the copy-editor.</p>` : ""}
      ${(ref.handled || []).map((x) => `<div class="flag"><span class="chip">${esc(POINT_TYPE[x.type] || x.type)}</span> ${inner(latexLite(named(x.comment, true)))} <span class="small muted">${inner(latexLite(named(x.action || "", true)))}</span></div>`).join("")}</div></div>`;
}
function sidePanel(P, a, auditIssues, badCites, texIssues, openReq) {
  const dl = (name, label, primary) => (S.b.downloads || []).includes(name) ? `<a class="btn ${primary ? "primary" : ""}" href="/api/projects/${S.pid}/download/${name}"><span>${label}</span><span class="small">${name.split(".").pop()}</span></a>` : "";
  const rv = P.revision || {};
  const agreed = (a.agreed || []).length;
  return `<div class="sheet"><h3>Download</h3><div class="dl">${dl("paper.zip", "Overleaf package", true)}${dl("paper.pdf", "PDF")}${dl("main.tex", "LaTeX source")}${dl("references.bib", "BibTeX file")}${dl("paper.html", "Web page")}</div>
      ${(S.b.downloads || []).includes("paper.pdf") ? "" : `<p class="muted small" style="margin-top:8px">For a PDF, upload the package to Overleaf, or install a LaTeX distribution and rebuild.</p>`}</div>
    <div class="sheet"><h3>From this paper</h3><div class="dl">
      <button class="btn" data-act="extra" data-kind="summary" ${S.busy ? "disabled" : ""}><span>${S.busy === "summary" ? "Writing…" : "Plain-language summary"}</span><span class="small">1 page</span></button>
      <button class="btn" data-act="extra" data-kind="slides" ${S.busy ? "disabled" : ""}><span>${S.busy === "slides" ? "Making…" : "Seminar slides"}</span><span class="small">Beamer</span></button>
      <button class="btn" data-act="extra" data-kind="poster" ${S.busy ? "disabled" : ""}><span>${S.busy === "poster" ? "Making…" : "Poster"}</span><span class="small">A0</span></button>
      <button class="btn" data-act="refcheck" ${S.busy ? "disabled" : ""} title="Each reference is looked up by its DOI, its arXiv number or its title: does it exist, is it the paper the entry says, has it been retracted?"><span>${S.busy === "refcheck" ? "Checking…" : "Check every reference"}</span></button>
      <a class="btn" href="/api/projects/${S.pid}/review_pack" title="One HTML file for a co-author: the paper, each passage beside the page of your notes it came from, and a box for comments they send back as a file. In the editor, Review &amp; proofs reads that file."><span>Review pack for a co-author</span><span class="small">html</span></a></div>
      ${["summary.txt", "slides.pdf", "slides.tex", "poster.pdf", "poster.tex"].filter((n) => (S.b.downloads || []).includes(n)).map((n) => `<a class="small" style="margin-right:10px" href="/api/projects/${S.pid}/download/${n}">${n}</a>`).join("")}</div>
    ${proofSheet()}
    <div class="sheet"><h3>Journal</h3>
      <div class="stat"><span>${S.b.journal ? esc(S.b.journal.name) : "No journal chosen"}</span></div>
      <div class="stat"><span>Template</span><b>${esc((S.meta.templates[P.profile.template] || {}).label || P.profile.template)}</b></div>
      ${(P.checklist || []).length ? `<div class="stat"><span>Requirements met</span><b class="${openReq ? "warn" : "good"}">${P.checklist.length - openReq} of ${P.checklist.length}</b></div>` : ""}
      <button class="btn small" style="margin-top:8px" data-act="tab" data-tab="journal">${S.b.journal ? "Requirements and checklist" : "Choose a journal"}</button></div>
    <div class="sheet"><h3>Checks</h3>
      <div class="stat"><span>Formulas identical to your notes</span><b class="${auditIssues ? "warn" : "good"}">${a.preserved} of ${a.total}</b></div>
      ${agreed ? `<div class="stat"><span>Changed with your approval</span><b>${agreed}</b></div>` : ""}
      <div class="stat"><span>Proofs checked, without a finding</span><b>${(rv.proofs || {}).clean ?? "–"} of ${(rv.proofs || {}).checked ?? "–"}</b></div>
      <div class="stat"><span>References cited</span><b>${P.refs.length}</b></div>
      <div class="stat"><span>Read but left uncited</span><b>${P.uncited.length}</b></div>
      <div class="stat"><span>Citations needing a look</span><b class="${badCites ? "warn" : "good"}">${badCites}</b></div>
      <div class="stat"><span>LaTeX</span><b class="${texIssues ? "bad" : "good"}">${P.compile.engine ? (P.compile.ok ? "compiles" : texIssues + " errors") : (texIssues ? texIssues + " to fix" : "syntax ok")}</b></div></div>
    <div class="sheet"><h3>Written for you</h3>
      <div class="stat"><span>Title</span><b>${{ author: "yours", "chosen by you": "chosen by you", suggested: "suggested; choose it at the last step", generated: "proposed" }[P.title_source] || ""}</b></div>
      <div class="stat"><span>Abstract</span><b>${{ author: "yours", generated: "written" }[P.abstract_source] || ""}</b></div>
      <div class="stat"><span>Introduction</span><b>${{ author: "yours", generated: "suggested", "rewritten for your citations": "rewritten for your citations" }[P.intro_source] || ""}</b></div>
      <p class="muted small" style="margin-top:8px">The body is your mathematics in rewritten, corrected English, arranged as statements and proofs. Click the title to choose another proposal or write your own.</p></div>`;
}
function editorPanel() {
  const E = S.edit, P = S.b.paper;
  const names = { title: "Title", abstract: "Abstract", intro: "Introduction" };
  let label = names[E.target];
  if (!label) { const b = P.blocks.find((x) => x.id === E.target); label = b ? ({ section: "Section heading", subsection: "Subsection heading", env: (b.env || "").replace(/^./, (c) => c.toUpperCase()) + (b.num ? " " + b.num : ""), proof: "Proof", figure: "Figure note", para: "Paragraph" }[b.type]) : "Passage"; }
  const cands = E.target === "title" ? (P.title_candidates || []).filter((t) => t !== P.title) : [];
  return `<div class="sheet editor"><h3><span>${esc(label)}</span><button class="btn small quiet" data-act="closeedit">Close</button></h3>
    <textarea id="edtext" spellcheck="false" aria-label="LaTeX source">${esc(E.text)}</textarea>
    <div class="btnrow"><button class="btn primary small" data-act="saveedit" ${S.busy ? "disabled" : ""}>${S.busy === "save" ? "Saving…" : "Save"}</button>
      ${E.canDelete ? `<button class="btn small danger" data-act="deleteblock">Delete passage</button>` : ""}</div>
    ${cands.length ? `<div class="muted small">Other proposals</div>${cands.map((t, i) => `<button class="btn small" style="white-space:normal;text-align:left;margin-top:5px" data-act="usetitle" data-i="${i}">${inner(mixed(t))}</button>`).join("")}` : ""}
    ${E.target !== "title" ? `<label class="field"><span>Or ask for a change</span><input type="text" id="edinstr" placeholder="e.g. shorten; say 'we show' instead of 'it is shown'"></label>
      <div class="btnrow"><button class="btn small" data-act="revise" ${S.busy ? "disabled" : ""}>${S.busy === "revise" ? "Rewriting…" : "Rewrite with AI"}</button></div>
      <p class="muted small">Formulas and citations are kept unless your instruction says otherwise. The fidelity check runs again after every change.</p>` : ""}</div>`;
}

/* -------------------------------------------------------------- settings */
function settingsDialog() {
  const M = S.meta;
  const ph = (k, empty) => (M.env[k] && M.env[k].set ? esc(M.env[k].shown) + " (saved)" : empty);
  const prov = M.providers.map((p) => {
    let input; const link = p.signup ? `<a href="${esc(p.signup)}" target="_blank" rel="noopener">${p.id === "ollama" ? "Install Ollama" : "Get a free key"}</a>` : "";
    if (p.id === "ollama") input = `<input type="text" data-env="OLLAMA_BASE_URL" placeholder="${ph("OLLAMA_BASE_URL", "http://localhost:11434")}" autocomplete="off">`;
    else if (p.id === "custom") input = `<input type="text" data-env="CUSTOM_BASE_URL" placeholder="${ph("CUSTOM_BASE_URL", "Base URL, e.g. https://example.com/v1")}" autocomplete="off">
      <div class="prov-extra"><input type="password" data-env="CUSTOM_API_KEY" placeholder="${ph("CUSTOM_API_KEY", "Key")}" autocomplete="off">
      <input type="text" data-env="CUSTOM_MODELS" placeholder="${ph("CUSTOM_MODELS", "Model names, comma separated")}" autocomplete="off"></div>`;
    else input = `<input type="password" data-env="${p.env}" placeholder="${ph(p.env, "Paste key")}" autocomplete="off">`;
    const keys = p.keys || [];
    const state = !p.configured ? link : keys.length > 1
      ? keys.map((k) => k.ok ? `<span class="chip green">key ${k.n}: ${plural(k.count, "model")}</span>` : `<span class="chip red" title="${esc(k.message)}">key ${k.n}: ${esc(k.message || "not working")}</span>`).join(" ")
      : (p.ok ? `<span class="chip green">${plural(p.count, "model")}</span>` : `<span class="chip red" title="${esc(p.message)}">${esc(p.message || "not working")}</span>`);
    return `<div class="prov"><div class="prov-name">${esc(p.label)}</div>${p.id === "custom" ? `<div>${input}</div>` : input}
      <div class="prov-st">${state}</div><div class="prov-why">${explained(p.why)}</div></div>`;
  }).join("");
  const short = (ref) => ref.replace(/^[a-z]+:/, "");
  const roles = Object.entries(M.roles).map(([id, r]) => {
    const plan = (M.plan || {})[id] || {};
    const pinned = ((M.settings.routing || {})[id] || [])[0] || "";
    const opts = M.models.filter((m) => (m.scores[id] || 0) > 0 && !m.dead).sort((a, b) => b.scores[id] - a.scores[id]);
    const seen = new Set();
    const chain = plan.chain || [];
    const policy = r.strict
      ? (chain.length ? `Only the strongest model is used: ${esc(chain.map(short).join(", "))}${chain.length > 1 ? " (one key after the other)" : ""}. If none of its keys is free, the work waits and restarts by itself.`
          + ((plan.weaker || []).length ? ` Weaker, used only if you allow it for a step: ${esc(plan.weaker.map(short).join(", "))}.` : "") : "No model is available for this job.")
      : (chain.length > 1 ? `Falls back freely to: ${esc(chain.slice(1, 4).map(short).join(", "))}.` : "");
    return `<div class="role"><div><div class="role-name">${esc(r.name)}</div>${r.strict ? '<span class="chip blue">decides the quality of the paper</span>' : '<span class="chip">small tasks</span>'}</div><div>
      <div class="role-job">${esc(r.job)}</div>
      <select data-role="${id}" style="margin-top:6px"><option value="">Automatic${plan.primary ? ": " + esc(plan.primary) : " (no model available)"}</option>
        ${opts.filter((m) => { const k = m.provider + ":" + m.id; if (seen.has(k)) return false; seen.add(k); return true; }).map((m) => `<option value="${esc(m.provider + ":" + m.id)}" ${pinned === m.provider + ":" + m.id ? "selected" : ""}>${esc(m.provider + ": " + m.id)}</option>`).join("")}</select>
      <div class="small muted" style="margin-top:4px">${policy} ${esc(r.ideal)}</div></div></div>`;
  }).join("");
  const s = M.settings;
  return `<div class="dialog wide" role="dialog" aria-modal="true" aria-label="Settings"><div class="dlg-h"><h2>Settings</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
    <div class="dlg-b">
      <h3>AI providers</h3><p>Keys are stored in the .env file in the app folder on this computer and are sent only to the provider they belong to. Use keys from different providers: several keys from one account share the same limits.</p>
      <div class="flag" style="margin:8px 0 12px">Your notes are sent to the provider whose model reads them. Check its terms for unpublished work. Google's <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noopener">Gemini API terms</a>, for example, let Google use what is sent with a free key to improve its products, and let people review it; with a key from a Google Cloud project that has billing turned on, prompts are not used that way.</div>
      ${prov}
      <div class="row" style="margin-top:12px"><button class="btn primary" data-act="savekeys">${S.busy === "keys" ? "Checking keys…" : "Save and check keys"}</button>
        <span class="muted small">${M.mock ? "Developer mode: simulated models." : ""}</span></div>
      <h3>Which model does which job</h3><p>Chosen automatically from the models your keys can use. Gemini models rank by tier first (4 Argon, then Pro, then Flash, then Flash-Lite), then by version; a model your key cannot use (Pro and Argon are not on Google's free tier) is found out on its first request and skipped on that key. For the jobs that decide the quality of the paper, only the strongest model is used: with several keys, one after the other, so when a key reaches its limit the same model continues on the next key. A lesser model never steps in by itself. A model you pin here is used first.</p>
      ${M.models.length ? roles : `<p class="muted">Connect a provider to see the assignment.</p>`}
      <h3>Literature and layout</h3><p>Optional identifiers raise the limits of the scholarly databases. The layout settings apply when no target journal is chosen for a paper.</p>
      <div class="grid2">
        <label class="field"><span>Contact e-mail for scholarly databases</span><input type="text" data-env="CONTACT_EMAIL" placeholder="${esc(M.env.CONTACT_EMAIL.shown || "you@university.edu")}"><small>Sent to Crossref, OpenAlex and Unpaywall as a courtesy; enables open-access lookup.</small></label>
        <label class="field"><span>OpenAlex key (free)</span><input type="password" data-env="OPENALEX_API_KEY" placeholder="${M.env.OPENALEX_API_KEY.set ? esc(M.env.OPENALEX_API_KEY.shown) + " (saved)" : "optional"}"></label>
        <label class="field"><span>Papers to read in full</span><select data-set="read_top_k">${[4, 6, 8, 12, 16].map((n) => `<option ${s.read_top_k === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <label class="field"><span>Pages surveyed at the same time</span><select data-set="parallel_pages">${[1, 2, 3, 4].map((n) => `<option ${s.parallel_pages === n ? "selected" : ""}>${n}</option>`).join("")}</select><small>Use 1 if you often hit rate limits.</small></label>
        <label class="field"><span>Reference order without a journal</span><select data-set="ref_order"><option value="alphabetical" ${s.ref_order === "alphabetical" ? "selected" : ""}>Alphabetical by author</option><option value="appearance" ${s.ref_order === "appearance" ? "selected" : ""}>Order of first citation</option></select></label>
        <label class="field"><span>Document class without a journal</span><select data-set="document_class"><option value="amsart" ${s.document_class === "amsart" ? "selected" : ""}>amsart (AMS layout)</option><option value="article" ${s.document_class === "article" ? "selected" : ""}>article</option></select></label>
      </div>
      <h3>Private mode</h3>
      <label class="check"><input type="checkbox" data-setbool="private" ${s.private ? "checked" : ""}> <b>Keep unpublished work private.</b> Every job then uses only Gemini models that Google sells on its paid tier, or a model running on this computer (Ollama); free services such as Groq or OpenRouter are never used, and your results are not searched for by their statements, on MathOverflow or in OEIS. Your Gemini key's Google Cloud project must have billing enabled: on the paid tier Google does not use prompts or responses to improve its products and keeps them only for a limited time to detect abuse. Short topic phrases are still sent to the literature databases as search queries, and fingerprints to the timestamp services; nothing else leaves this computer.</label>
      <h3>What Scriptavera remembers about you</h3><p>The meanings you confirm and the readings of your handwriting you correct are remembered on this computer, so later papers ask fewer questions: ${M.memory ? `${M.memory.symbols} symbol(s), ${M.memory.readings} handwriting reading(s)` : "nothing yet"}. ${M.memory && (M.memory.symbols || M.memory.readings) ? `<button class="btn small" data-act="forget">Forget all of it</button>` : ""}</p>
      <h3>Records of your notes</h3><p>When notes are uploaded, the files are kept unchanged and their fingerprints are recorded and signed with your key, which is made on this computer and stays in the folder identity. Keep a copy of that folder somewhere safe.</p>
      <label class="check"><input type="checkbox" data-setbool="proof_timestamps" ${s.proof_timestamps === false ? "" : "checked"}> Have the record timestamped by independent services (an RFC 3161 time-stamp authority, and OpenTimestamps in the Bitcoin blockchain). Only a 32-byte fingerprint is sent, from which nothing of the notes can be read.</label>
      ${M.identity && M.identity.fingerprint ? `<p class="muted small" style="margin-top:8px">Your signing key: <span style="font-family:var(--mono)">${esc(M.identity.fingerprint)}</span>. Publish this fingerprint where people know it is yours (your ORCID record, your university page) to tie your records to you.</p>` : ""}
      <p class="muted small" style="margin-top:14px">LaTeX engine on this computer: ${M.engine ? esc(M.engine) + " (PDFs and figures are compiled locally)" : "none found. The Overleaf package still works; install MiKTeX or TeX Live for local PDFs and redrawn figures."} Version ${esc(M.version)}.</p>
      ${aboutLine()}
    </div></div>`;
}
function aboutLine() {
  /* The AGPL asks that people using the program over a network can get its source: the link serves this very copy. */
  return `<p class="muted small about">Scriptavera ${esc((S.meta || {}).version || "")} · © 2026 Vaibhav Chhajer · free software under the
    <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener">GNU AGPL v3</a> ·
    <a href="/api/source" download>Source code of this copy</a> · no warranty · a commercial licence is available for use without the AGPL's conditions (see LICENSING.md)</p>`;
}
function showDialog(html) { const o = $("#overlay"); const keep = o.scrollTop; o.innerHTML = html; o.hidden = false; o.scrollTop = keep; renderMath(o); }
function closeDialog() {
  const o = $("#overlay"); o.hidden = true; o.innerHTML = "";
  const r = S.confirmResolve; S.confirmResolve = null; if (r) r(false);          // closed without an answer: nothing is done
}
/* Asking before anything that cannot be undone, in the app's own dialog. Resolves true only for the named action;
   Cancel, Escape and a click outside resolve false. Cancel has the focus, so Enter alone never destroys anything. */
function askConfirm({ title, body, yes, danger = true }) {
  const before = S.confirmResolve; S.confirmResolve = null; if (before) before(false);
  return new Promise((resolve) => {
    showDialog(`<div class="dialog narrow" role="alertdialog" aria-modal="true" aria-labelledby="cfh" aria-describedby="cfb">
      <div class="dlg-h"><h2 id="cfh">${title}</h2></div><div class="dlg-b" id="cfb">${body}</div>
      <div class="dlg-f"><button class="btn" data-act="cfno">Cancel</button><button class="btn ${danger ? "danger solid" : "primary"}" data-act="cfyes">${esc(yes)}</button></div></div>`);
    S.confirmResolve = resolve;
    const c = $('[data-act="cfno"]'); if (c) c.focus();
  });
}

/* ---------------------------------------------------------------- render */
function render() {
  if (!S.meta) return;
  if (S.b && S.view !== "home" && !stageInfo(S.view)) S.view = autoView();       // an address from an earlier version of the app
  captureJournalForm();
  $("#app").classList.toggle("bare", !S.pid);              // the home page and the new-paper form have no list of steps
  drawRail(); drawTop();
  const main = $("#main");
  let h;
  if (S.b && S.view !== "home" && ((S.b.state || {}).meta || {}).imported) h = viewImported();
  else if (S.view === "home") h = viewHome();
  else if (S.view === "new") h = viewNew();
  else if (!S.b) h = `<div class="wrap"><div class="empty"><span class="spinner"></span> Opening…</div></div>`;
  else if (S.view === "confirm") h = viewConfirm();
  else if (S.view === "review") h = viewReview();
  else if (S.view === "decide") h = viewDecide();
  else if (S.view === "topiccheck") h = viewTopic();
  else if (S.view === "scope") h = viewScope();
  else if (S.view === "finish") h = viewFinish();
  else if (S.view === "assemble" && S.b.paper) h = viewPaper();
  else h = viewStage(S.view);
  h = testBanner() + h;
  const keep = main.scrollTop, same = main.dataset.view === S.view + "|" + S.pid + "|" + S.tab;
  const inner2 = $(".rv-right", main), keepInner = inner2 ? inner2.scrollTop : 0;
  main.innerHTML = h;
  main.dataset.view = S.view + "|" + S.pid + "|" + S.tab;
  main.scrollTop = same ? keep : 0;
  const again = $(".rv-right", main); if (again && same) again.scrollTop = keepInner;
  renderMath(main);
  drawLog();
  if (S.view === "assemble" && S.tab === "latex" && S.b && S.b.paper) loadTex();
  if (S.edit) { const el = $(`.paper [data-block="${S.edit.target}"], .paper [data-part="${S.edit.target}"]`); if (el) el.classList.add("on"); }
}
async function loadTex() {
  try { const r = await fetch(`/api/projects/${S.pid}/download/main.tex`); const el = $("#texsrc"); if (el) el.textContent = await r.text(); } catch (e) { /* ignore */ }
}

/* --------------------------------------------------------------- actions */
function bindPath(obj, path, value) {
  const keys = path.split("."); let o = obj;
  for (let i = 0; i < keys.length - 1; i++) o = o[keys[i]];
  o[keys[keys.length - 1]] = value;
}
function addFiles(list) {
  for (const f of list) if (/\.(pdf|tex|ltx|zip|md|markdown|txt)$/i.test(f.name) || f.type.startsWith("image/")) S.form.files.push(f);
  S.form.files.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  render();
}
async function saveTranscript(n, text) {
  try { await api(`/api/projects/${S.pid}/transcript/${n}`, { method: "PUT", body: { text } }); const s = $("#rvsaved"); if (s) s.textContent = "Saved"; }
  catch (e) { toast(e.message, true); }
}
function openEditor(target) {
  const P = S.b.paper; let text = "", canDelete = false;
  if (target === "title" || target === "abstract" || target === "intro") text = P[target] || "";
  else { const b = P.blocks.find((x) => x.id === target); if (!b) return; text = (b.type === "section" || b.type === "subsection") ? b.title : b.text; canDelete = true; }
  S.edit = { target, text, canDelete }; S.tab = "paper"; render();
  const t = $("#edtext"); if (t) t.focus();
}
async function savePaper(body) {
  S.busy = "save"; render();
  try { const r = await api(`/api/projects/${S.pid}/paper`, { method: "PUT", body }); S.b.paper = r.paper; S.edit = null; toast("Saved. Downloads rebuilt."); await refresh(); }
  catch (e) { toast(e.message, true); }
  S.busy = ""; render();
}
async function setFigure(id, body, busy) {
  const mode = typeof busy === "string" ? busy : "draw";
  if (busy) { S.review.figBusy = id; S.review.figMode = mode; render(); }
  let ok = false;
  try {
    const r = await api(`/api/projects/${S.pid}/figure/${id}`, { method: "POST", body });
    const i = S.b.figures.findIndex((f) => f.id === id); if (i >= 0) S.b.figures[i] = r.figure;
    if (busy) toast(r.figure.tikz_ok ? (mode === "print" ? "Drawn for the paper. Check it against your page." : "Drawn again. Is it right now?")
      : "It could not be redrawn. You can use your own drawing.", !r.figure.tikz_ok);
    ok = true;
  } catch (e) { toast(e.message, true); }
  if (busy) S.review.figBusy = "";
  if (!ok || busy || !body.choice) render();      // a chosen answer redraws through advanceReview
  return ok;
}
async function journalSearch(q, done) {
  try { const r = await api("/api/journals?q=" + encodeURIComponent(q)); done(r.journals, r.message || ""); }
  catch (e) { done([], e.message); }
}

const ACT = {
  home() {
    if (S.pid && running()) toast("This paper carries on in the background. Its progress is saved.");
    if (location.hash && location.hash !== "#") location.hash = ""; else route();
  },
  papers() { ACT.home(); },
  newpaper() {
    if (S.pid && running()) toast("The paper you left carries on in the background. Its progress is saved.");
    if (location.hash !== "#new") location.hash = "new"; else route();
    const m = $("#main"); if (m) m.scrollTop = 0;
  },
  menu(el) {
    const k = el.dataset.menu;
    S.menu = S.menu === k ? null : k;
    redrawMenus();
    if (S.menu) { const first = $(".menu [role=menuitem]"); if (first) first.focus(); }
    else { const t = $(`[data-act="menu"][data-menu="${CSS.escape(k)}"]`); if (t) t.focus(); }
  },
  cfyes() { const r = S.confirmResolve; S.confirmResolve = null; closeDialog(); if (r) r(true); },
  cfno() { closeDialog(); },
  rename(el) {
    showDialog(`<div class="dialog narrow" role="dialog" aria-modal="true" aria-labelledby="rnh"><div class="dlg-h"><h2 id="rnh">Rename this paper</h2></div>
      <div class="dlg-b"><label class="field"><span>Name in your list of papers</span><input type="text" id="renamebox" value="${esc(el.dataset.name || "")}"></label>
      <p class="muted small" style="margin-top:8px">The title of the paper itself is chosen at the end, from suggestions.</p></div>
      <div class="dlg-f"><button class="btn" data-act="closedlg">Cancel</button>
        <button class="btn primary" data-act="renamesave" data-id="${esc(el.dataset.id)}">Save</button></div></div>`);
    const b = $("#renamebox"); if (b) { b.focus(); b.select(); }
  },
  async renamesave(el) {
    const v = ($("#renamebox") || {}).value || "";
    try { await api(`/api/projects/${el.dataset.id}/name`, { method: "POST", body: { name: v } }); closeDialog(); await loadProjects(); if (S.pid === el.dataset.id) await refresh(); render(); toast("Renamed."); }
    catch (e) { toast(e.message, true); }
  },
  open(el) { location.hash = `p/${el.dataset.id}`; },
  view(el) {
    S.view = el.dataset.view; S.edit = null;
    S.follow = !!S.b && S.view === autoView();         // the step in progress: keep following; an earlier one: stay there
    history.replaceState(null, "", S.follow ? `#p/${S.pid}` : `#p/${S.pid}/${S.view}`); render();
  },
  settings() { showDialog(settingsDialog()); },
  closedlg() { closeDialog(); },
  dom(el) {
    const id = el.dataset.id, d = S.form.domains, i = d.indexOf(id);
    if (i >= 0) d.splice(i, 1); else if (d.length < 3) d.push(id); else return toast("Up to three areas. Remove one first.");
    if (i < 0 && d.length === 1) { S.domOpen = false; S.domQuery = ""; }
    render();
  },
  domopen() { S.domOpen = true; render(); },
  domclose() { S.domOpen = false; S.domQuery = ""; render(); },
  pick() { $("#fileInput").click(); },
  rmfile(el) { S.form.files.splice(+el.dataset.i, 1); render(); },
  addauthor() { S.form.authors.push({ name: "", affiliation: "", email: "", orcid: "" }); render(); },
  rmauthor(el) {
    const i = +el.dataset.i, f = S.form;
    f.authors.splice(i, 1);
    if (f.recorder === String(i)) f.recorder = "0";                       // the one making the record was removed
    else if (f.recorder !== "other" && +f.recorder > i) f.recorder = String(+f.recorder - 1);
    render();
  },
  async formjsearch() {
    const f = S.form; if (f.jq.trim().length < 3) return toast("Type at least three letters of the journal's name.", true);
    S.busy = "formj"; render();
    await journalSearch(f.jq, (list, msg) => { f.jresults = list; f.jmsg = msg; });
    S.busy = ""; render();
  },
  formjpick(el) { S.form.journal = S.form.jresults[+el.dataset.i]; S.form.jresults = null; render(); },
  formjtyped() { S.form.journal = { name: S.form.jq.trim(), publisher: "" }; S.form.jresults = null; render(); },
  formjclear() { S.form.journal = null; S.form.jq = ""; S.form.jresults = null; S.form.jmsg = ""; render(); },
  async create() {
    const f = S.form;
    const kws = f.keywords.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
    if (f.problem.trim().length < 20) { toast("Describe the problem you are working on in a sentence or two. The search and the reading start from it.", true); const e = $("#fproblem"); if (e) e.focus(); return; }
    if (kws.length < 2) { toast("Give at least two keywords, separated by commas.", true); const e = $("#fkeywords"); if (e) e.focus(); return; }
    const named = f.authors.filter((a) => a.name.trim());
    if (!named.length) { toast("Name at least one author. The record of your notes says who wrote them, as arXiv does for every submission.", true); const e = $('[data-bind="authors.0.name"]'); if (e) e.focus(); return; }
    const badId = named.find((a) => (a.orcid || "").trim() && !orcidOk(a.orcid));
    if (badId) { toast(`The ORCID iD given for ${badId.name.trim()} is not valid: check its 16 digits (it looks like 0000-0002-1825-0097).`, true); return; }
    const by = declarationMaker(f.authors, f.recorder, f.recorderName);
    if (!by) { toast("Say who is making the record: choose your name among the authors, or give your name.", true); return; }
    if (!f.declare) { toast("Tick the declaration: the record says, as arXiv does for every submission, who wrote the notes and that the details are correct.", true); const e = $("#fdeclare"); if (e) e.focus(); return; }
    const declaration = { agreed: true, author: by.is_author ? named.indexOf(f.authors[by.index]) : null, name: by.is_author ? "" : by.name };
    S.busy = "create"; render();
    const fd = new FormData();
    f.files.forEach((x) => fd.append("files", x, x.name));
    fd.append("meta", JSON.stringify({ name: f.files[0].name.replace(/\.[^.]+$/, ""), domains: f.domains, subarea: f.subarea, hints: f.hints, title: f.title,
      authors: named, declaration, known_refs: f.refs.split(/\n/).map((s) => s.trim()).filter(Boolean), journal: f.journal,
      problem: f.problem.trim(), keywords: kws, key_papers: f.keypapers.split(/\n/).map((s) => s.trim()).filter(Boolean) }));
    try {
      const r = await api("/api/projects", { method: "POST", form: fd });
      await api(`/api/projects/${r.id}/run`, { method: "POST", body: {} });
      S.form = newForm(); S.busy = ""; location.hash = `p/${r.id}`;
    } catch (e) { S.busy = ""; render(); toast(e.message, true); }
  },
  async stop() { try { await api(`/api/projects/${S.pid}/stop`, { method: "POST" }); toast("Stopping after the current call."); } catch (e) { toast(e.message, true); } },
  async startover() {
    if (!await askConfirm({ title: "Start over from the first step?", yes: "Discard the results and start over",
      body: `<p>Every result for this paper is discarded: the reading, the notation you confirmed, the transcription, the drafts and the paper. It cannot be undone.</p>
        <p>Your uploaded pages and the record of your notes are kept, and the notes are read again from the first step.</p>` })) return;
    try { await api(`/api/projects/${S.pid}/run`, { method: "POST", body: { from: "scout" } }); S.follow = true; S.gate = null; S.review = null; S.decide = null; S.edit = null; history.replaceState(null, "", `#p/${S.pid}`); toast("Starting again from the first step."); await refresh(); }
    catch (e) { toast(e.message, true); }
  },
  async autostop() { try { await api(`/api/projects/${S.pid}/autostop`, { method: "POST" }); toast("Automatic restart cancelled. Press Continue when you want to carry on."); await refresh(); } catch (e) { toast(e.message, true); } },
  async resume() { try { await api(`/api/projects/${S.pid}/run`, { method: "POST", body: {} }); S.follow = true; await refresh(); } catch (e) { toast(e.message, true); } },
  async relax() {
    try { await api(`/api/projects/${S.pid}/run`, { method: "POST", body: { relax: true } }); S.follow = true; toast("Continuing this step with the next best model."); await refresh(); }
    catch (e) { toast(e.message, true); }
  },
  async redo(el) {
    const s = stageInfo(el.dataset.stage);
    if (!await askConfirm({ title: `Redo “${esc(s.name)}”?`, yes: "Redo from this step", danger: false,
      body: `<p>This step and every step after it run again, and their current results are replaced. Everything before it is kept.</p>` })) return;
    try { await api(`/api/projects/${S.pid}/run`, { method: "POST", body: { from: s.id } }); S.follow = true; S.gate = null; S.review = null; S.decide = null; S.edit = null; history.replaceState(null, "", `#p/${S.pid}`); await refresh(); } catch (e) { toast(e.message, true); }
  },
  async delete(el) {
    /* Says exactly what is lost, offers the record of the notes first, and asks. */
    const id = (el && el.dataset.id) || S.pid;
    const here = S.pid === id && S.b;
    const p = (S.projects || []).find((x) => x.id === id) || {};
    const m = here ? S.b.state.meta : {};
    const name = (here ? (S.b.paper && S.b.paper.title) || m.title || m.name : p.title || p.name) || "this paper";
    const done = here ? st("finish").status === "done" : p.completed;
    const working = here ? running() : p.running;
    const imported = here ? !!m.imported : p.imported;
    const record = here ? !(S.b.proof || {}).missing : p.record;
    const lost = imported ? "the LaTeX files you imported, their history and the editor's files"
      : `the notes you uploaded, the transcription, every draft${done ? ", the finished paper" : ""} and the editor's files`;
    const ok = await askConfirm({ title: `Delete “${inner(latexLite(name))}”?`, yes: "Delete permanently", body:
      `<p>This removes the paper from this computer for good: ${lost}. It cannot be undone.</p>
       ${working ? `<p>It is working now; it is stopped first.</p>` : ""}
       ${record ? `<div class="flag">The signed, timestamped record of your notes is deleted with it. It is your evidence of when the notes existed.
         <a href="/api/projects/${encodeURIComponent(id)}/proof/bundle?files=1" download>Download the record with your notes</a> before you delete, if you may need it.</div>` : ""}` });
    if (!ok) return;
    try {
      await api(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast(`Deleted “${String(name).replace(/\$/g, "")}”.`);
      if (S.pid === id) ACT.home(); else { await loadProjects(); render(); }
    } catch (e) { toast(e.message, true); }
  },
  addpagesdlg() {
    showDialog(`<div class="dialog narrow" role="dialog" aria-modal="true" aria-labelledby="aph"><div class="dlg-h"><h2 id="aph">Add pages to these notes</h2></div>
      <div class="dlg-b"><p>Only the new pages are read. Then the paper is put together again, and every part of it that does not change is kept.</p></div>
      <div class="dlg-f"><button class="btn" data-act="closedlg">Cancel</button><button class="btn" data-act="phone" data-for="add">Use your phone</button>
        <button class="btn primary" data-act="addpages">Choose files</button></div></div>`);
  },
  zoom(el) {
    const n = +el.dataset.n, pg = S.b.state.pages.find((x) => x.n === n) || {};
    const can = !running();
    showDialog(`<div class="dialog lightbox"><div class="dlg-h"><h2>Page ${n}${pg.skip ? " (left out)" : ""}</h2><span class="row">
      ${can ? `<button class="btn small" data-act="skippage" data-n="${n}" data-skip="${pg.skip ? "" : "1"}">${pg.skip ? "Include this page" : "Leave this page out"}</button>` : ""}
      <button class="btn small" data-act="closedlg">Close</button></span></div><img src="/api/projects/${S.pid}/page/${n}" alt="Page ${n}"></div>`);
  },
  async skippage(el) {
    const n = +el.dataset.n, skip = !!el.dataset.skip;
    try {
      const r = await api(`/api/projects/${S.pid}/pages`, { method: "POST", body: { n, skip, force: !!el.dataset.force } });
      if (r.confirm) {
        return showDialog(`<div class="dialog"><div class="dlg-h"><h2>Include page ${n}?</h2></div><p>${esc(r.message)}</p>
          <div class="row"><span class="tb-space"></span><button class="btn" data-act="closedlg">Cancel</button>
          <button class="btn primary" data-act="skippage" data-n="${n}" data-force="1">Include it and redo from the review</button></div></div>`);
      }
      closeDialog();
      if (r.reading) { S.follow = true; if (S.review) S.review.tab = "todo"; history.replaceState(null, "", `#p/${S.pid}`); }
      await refresh();
      toast(skip ? "Page left out. Redo a step for this to take effect on finished steps."
        : r.reading ? `Page ${n} is being read now. Only this page is read; everything else is kept.` : `Page ${n} included.`);
    } catch (e) { toast(e.message, true); }
  },
  alt(el) {
    const e = S.b.glossary.entries.find((x) => x.id === el.dataset.id); const it = S.gate.items[e.id];
    it.choice = "own"; it.own = e.alternatives[+el.dataset.i]; saveGate(); render();
  },
  async forget() {
    if (!await askConfirm({ title: "Forget what Scriptavera has learned?", yes: "Forget all of it",
      body: "<p>Every meaning you confirmed and every reading of your handwriting you corrected is forgotten. Later papers will ask about them again.</p>" })) return showDialog(settingsDialog());
    try { S.meta.memory = (await api("/api/memory", { method: "POST", body: { action: "clear" } })); showDialog(settingsDialog()); toast("Forgotten."); } catch (e) { toast(e.message, true); }
  },
  async refcheck() {
    S.busy = "refcheck"; render();
    try {
      const r = await api(`/api/projects/${S.pid}/refcheck`, { method: "POST", body: {} });
      const L = { verified: ["verified", "green"], check: ["check", "hl"], mismatch: ["another paper", "red"], not_found: ["not found", "red"], retracted: ["RETRACTED", "red"], unchecked: ["not checked", ""] };
      showDialog(`<div class="dialog wide"><div class="dlg-h"><h2>References</h2><button class="btn small quiet" data-act="closedlg">Close</button></div><div class="dlg-b">
        <p>${r.entries.length} references: ${r.count.verified} verified, ${r.count.check} to check, ${r.count.mismatch + r.count.not_found} wrong or not found, ${r.count.retracted} retracted.</p>
        ${r.entries.map((x) => `<div class="flag ${x.status === "verified" ? "green" : ["mismatch", "not_found", "retracted"].includes(x.status) ? "red" : ""}"><span class="chip ${(L[x.status] || ["", ""])[1]}">${(L[x.status] || [x.status])[0]}</span> <b>${esc(x.key)}</b> ${esc(x.title)}<div class="small muted">${esc(x.detail)}</div></div>`).join("")}</div></div>`);
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  async extra(el) {
    const kind = el.dataset.kind;
    S.busy = kind; render();
    try {
      const r = await api(`/api/projects/${S.pid}/extra`, { method: "POST", body: { kind } });
      await refresh();
      if (kind === "summary") showDialog(`<div class="dialog wide"><div class="dlg-h"><h2>Plain-language summary</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
        <div class="dlg-b"><div class="tex-block plain">${latexLite(r.text)}</div><p class="muted small">Saved as summary.txt. Read it before you use it: it is written by a model from the paper, and claims only what the paper proves.</p></div></div>`);
      else toast(`${kind === "slides" ? "Slides" : "Poster"} made${r.compiled ? " and compiled" : r.compiled === false ? "; the PDF did not compile, the .tex is there to fix" : " (install LaTeX for the PDF)"}.`
        + (r.foreign.length ? ` ${r.foreign.length} formula(s) in it are not in the paper: check them.` : ""), !!r.foreign.length || r.compiled === false);
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  async phone(el) {
    S.phoneFor = el.dataset.for || "new";
    let st;
    try { st = await api("/api/phone", { method: "POST", body: { action: "start" } }); } catch (e) { return toast(e.message, true); }
    let svg = "";
    try { const q = qrcode(0, "M"); q.addData(st.url); q.make(); svg = q.createSvgTag(6, 8, "QR code with the address for the phone"); } catch (e) { svg = ""; }
    showDialog(`<div class="dialog"><div class="dlg-h"><h2>Photos from your phone</h2><button class="btn small quiet" data-act="phonestop">Close</button></div><div class="dlg-b">
      <p>Scan this with the phone's camera (the phone must be on the same Wi-Fi as this computer), then photograph each page in order.</p>
      <div class="qr">${svg}</div><p class="small" style="word-break:break-all">${esc(st.url)}</p>
      <p class="muted small">Only photos can be sent to this address, which has a secret in it and stops working after 20 minutes or when you close this. If Windows asks whether Python may use private networks, allow it.</p>
      <p id="phoneCount"><b>0</b> photos received.</p><button class="btn primary" data-act="phonedone">Use these photos</button></div></div>`);
    clearInterval(S.phonePoll);
    S.phonePoll = setInterval(async () => { try { const r = await api("/api/phone"); const c = $("#phoneCount"); if (c) c.innerHTML = `<b>${r.files.length}</b> photo${r.files.length === 1 ? "" : "s"} received.`; S.phoneFiles = r.files; } catch (e) { /* keep trying */ } }, 2000);
  },
  async phonestop() { clearInterval(S.phonePoll); try { await api("/api/phone", { method: "POST", body: { action: "stop" } }); } catch (e) { /* already stopped */ } closeDialog(); },
  async phonedone() {
    clearInterval(S.phonePoll);
    const names = (await api("/api/phone")).files;
    const files = [];
    for (const n of names) { const r = await fetch(`/api/phone?file=${encodeURIComponent(n)}`); const b = await r.blob(); files.push(new File([b], `phone-${n}`, { type: b.type })); }
    try { await api("/api/phone", { method: "POST", body: { action: "stop" } }); } catch (e) { /* stopped */ }
    closeDialog();
    if (!files.length) return toast("No photos arrived.", true);
    if (S.phoneFor === "add" && S.pid) {
      const fd = new FormData(); for (const f of files) fd.append("files", f);
      try { const r = await api(`/api/projects/${S.pid}/add_pages`, { method: "POST", form: fd }); toast(`${plural(r.pages.length, "page")} added.`); await api(`/api/projects/${S.pid}/run`, { method: "POST" }); await refresh(); }
      catch (e) { toast(e.message, true); }
    } else { S.form.files.push(...files); render(); toast(`${plural(files.length, "photo")} added to the notes.`); }
  },
  costs() {
    const u = S.b.usage, e = S.b.estimate, t = u.total;
    const rows = Object.entries(u.stages).map(([sid, x]) => `<tr><td>${esc((stageInfo(sid) || { name: sid }).name)}</td><td>${x.calls}</td><td>${tokens(x.in)}</td><td>${tokens(x.out)}</td><td>${tokens(x.think)}</td><td><b>${money(x.cost)}</b></td></tr>`).join("");
    showDialog(`<div class="dialog wide"><div class="dlg-h"><h2>What this paper costs</h2><button class="btn small quiet" data-act="closedlg">Close</button></div><div class="dlg-b">
      <p>So far: <b>${money(t.cost)}</b> for ${t.calls} AI calls (${tokens(t.in)} tokens in, ${tokens(t.out)} out, ${tokens(t.think)} thinking). Thinking is billed as output.
      Estimate for the whole paper (${e.pages} pages on ${esc(e.model)}): <b>${money(e.typical)}</b>, between ${money(e.low)} and ${money(e.high)}.</p>
      <table class="cost-table"><thead><tr><th>Step</th><th>Calls</th><th>In</th><th>Out</th><th>Thinking</th><th>Cost</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small">Prices are Google's paid-tier prices of ${esc(u.table_date)} (Gemini 3.8 Flash doubles on 1 January 2027); free tiers count as nothing.
      ${t.no_think ? `${t.no_think} call(s) were made before thinking was recorded, so their cost is too low.` : ""} ${t.unknown ? `${t.unknown} call(s) used a model whose price is not known; add it under "prices" in settings.json.` : ""}</p></div></div>`);
  },
  testmode() {
    const t = ((S.meta || {}).settings || {}).test_mode || {};
    showDialog(`<div class="dialog"><div class="dlg-h"><h2>Test mode</h2><button class="btn small quiet" data-act="closedlg">Close</button></div><div class="dlg-b">
      <p>To test Scriptavera on a paper that is already published, hand-copy its mathematics as notes and make a new paper while test mode is on. That paper then never meets its own answer:</p>
      <ul><li>every literature search (arXiv, zbMATH, OpenAlex, Crossref, Semantic Scholar, the citation trail, MathOverflow) is limited to work published <b>before</b> the date below;</li>
      <li>the papers you list are excluded outright, however they turn up;</li><li>OEIS is not asked (it cannot be limited by date).</li></ul>
      <p class="muted small">The limits are kept with each test paper, so switching test mode off later changes nothing for it, and papers made with test mode off are never limited. The models themselves have no internet access in Scriptavera; for what a model may remember, use papers that appeared after its training.</p>
      <label class="field"><span>Cutoff date: the day the test paper first appeared (on arXiv or elsewhere)</span><input type="date" id="tmBefore" value="${esc(t.before || "")}"></label>
      <label class="field"><span>Papers to exclude <span class="muted">(one per line: arXiv number, DOI or title; the test paper and its journal version)</span></span><textarea id="tmExclude" rows="3">${esc((t.exclude || []).join("\n"))}</textarea></label>
      <div class="row"><button class="btn primary" data-act="testsave" data-on="1">${t.on ? "Save" : "Turn test mode on"}</button>${t.on ? `<button class="btn" data-act="testsave" data-on="0">Turn test mode off</button>` : ""}</div></div></div>`);
  },
  async testsave(el) {
    const before = $("#tmBefore").value;
    try {
      S.meta = await api("/api/test_mode", { method: "POST", body: { on: el.dataset.on === "1", before, exclude: $("#tmExclude").value } });
      closeDialog(); toast(el.dataset.on === "1" ? `Test mode is on. New papers search only literature published before ${before}.` : "Test mode is off."); render();
    } catch (e) { toast(e.message, true); }
  },
  async importpaper() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = ".tex,.ltx,.zip,.bib,.png,.jpg,.jpeg,.pdf,.eps,.sty,.cls,.bst";
    inp.onchange = async () => {
      if (!inp.files.length) return;
      const fd = new FormData();
      for (const f of inp.files) fd.append("files", f);
      try { const r = await api("/api/import_paper", { method: "POST", form: fd }); location.href = `/editor/${encodeURIComponent(r.id)}`; }
      catch (e) { toast(e.message, true); }
    };
    inp.click();
  },
  async addpages() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = "application/pdf,image/*,.tex,.ltx,.zip,.md,.markdown,.txt";
    inp.onchange = async () => {
      if (!inp.files.length) return;
      const fd = new FormData();
      for (const f of inp.files) fd.append("files", f);
      toast("Adding the pages and recording them…");
      try {
        const r = await api(`/api/projects/${S.pid}/add_pages`, { method: "POST", form: fd });
        toast(`${plural(r.pages.length, "page")} added. Only they are read; then the paper is put together again, keeping every part that has not changed.`);
        await api(`/api/projects/${S.pid}/run`, { method: "POST" });
        await refresh();
      } catch (e) { toast(e.message, true); }
    };
    inp.click();
  },
  async reglossary() {
    S.busy = "reglossary"; render();
    try { await api(`/api/projects/${S.pid}/reglossary`, { method: "POST" }); S.gate = null; toast("The list was rebuilt."); await refresh(); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  choose(el) {
    const it = S.gate.items[el.dataset.id];
    it.choice = it.choice === el.dataset.v ? "" : el.dataset.v; saveGate(); render();
    if (it.choice === "own") { const t = $(`[data-gateown="${el.dataset.id}"]`); if (t) t.focus(); }
  },
  async where(el) {
    const e = S.b.glossary.entries.find((x) => x.id === el.dataset.id), n = +el.dataset.n;
    const ctx = symbolContext(e, n);
    const others = (e.pages || []).filter((x) => x !== n).slice(0, 7);
    const sym = e.kind === "symbol" ? tex(e.symbol) : rich(e.symbol);
    showDialog(`<div class="dialog wide locate"><div class="dlg-h"><h2>Where ${sym} was read on page ${n}</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
      <div class="dlg-b"><div class="loc-status" id="locstatus"><span class="spinner"></span> Looking for it on your page…</div>
        ${ctx.length ? `<div class="loc-ctx"><span class="muted small">On this page the first reading has:</span>${ctx.map((l) => mixed(l, [e.symbol])).join("")}</div>` : `<div class="loc-ctx muted small">The first reading of this page has no line containing it.</div>`}
        ${others.length ? `<div class="chips" style="margin:8px 0"><span class="muted small">Also read on:</span>${others.map((x) => `<button class="chip blue" data-act="where" data-id="${e.id}" data-n="${x}">page ${x}</button>`).join("")}</div>` : ""}
        <div class="loc-img"><img src="/api/projects/${S.pid}/page/${n}" alt="Page ${n}"><div id="locboxes"></div></div></div></div>`);
    try {
      const r = await api(`/api/projects/${S.pid}/locate`, { method: "POST", body: { symbol: e.symbol, page: n, context: ctx.join(" ") } });
      const stEl = $("#locstatus"), host = $("#locboxes"); if (!stEl || !host) return;
      if (r.found) {
        host.innerHTML = r.boxes.map((b, i) => `<span class="loc-box" style="top:${b[0] / 10}%;left:${b[1] / 10}%;height:${(b[2] - b[0]) / 10}%;width:${(b[3] - b[1]) / 10}%"><i>${i + 1}</i></span>`).join("");
        stEl.className = "loc-status"; stEl.innerHTML = `Marked in yellow: ${plural(r.boxes.length, "place")} where it appears to be written. The marks are approximate.`;
        const first = $(".loc-box"); if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
      } else {
        stEl.className = "loc-status warn";
        stEl.innerHTML = `<b>It could not be found on this page.</b> ${explained(r.note || "")} It is probably a misreading of something else. If so, choose “Ignore it: misread, or not notation”.`;
      }
    } catch (err) { const stEl = $("#locstatus"); if (stEl) { stEl.className = "loc-status warn"; stEl.textContent = "Could not mark it: " + err.message; } }
  },
  addextra() { S.gate.extra.push({ symbol: "", meaning: "" }); render(); },
  async lit(el) {
    try { await api(`/api/projects/${S.pid}/library`, { method: "POST", body: { action: "state", key: el.dataset.key, state: el.dataset.state } }); await refresh(); } catch (e) { toast(e.message, true); }
  },
  async addref() {
    const v = $("#addref").value.trim(); if (!v) return;
    S.busy = "addref"; render();
    try { const r = await api(`/api/projects/${S.pid}/library`, { method: "POST", body: { action: "add", identifier: v } }); toast("Added: " + r.title); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; await refresh();
  },
  async confirm() {
    const G = S.gate, c = gateCounts();
    if (c.done < c.need) {
      const first = S.b.glossary.entries.find((e) => (e.status === "undefined" || e.status === "ambiguous") && !answered(S.gate.items[e.id]));
      const el = first && $(`#term-${first.id}`); if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      return toast(`${c.need - c.done} item(s) still need an answer. The first one is shown.`, true);
    }
    S.busy = "confirm"; render();
    const body = {
      topic: { problem: G.problem, subfield: G.subfield, keywords: G.keywords.split(",").map((s) => s.trim()).filter(Boolean) },
      glossary: Object.entries(G.items).map(([id, it]) => ({ id, meaning: it.choice === "own" ? it.own : it.meaning, confirmed: it.choice === "yes" || it.choice === "own", dismiss: it.choice === "skip" })),
      answers: S.b.glossary.questions.map((q) => ({ question: q.question, answer: G.answers[q.id] || "" })),
      extra: G.extra, instructions: G.instructions,
    };
    try { await api(`/api/projects/${S.pid}/confirm`, { method: "POST", body }); try { localStorage.removeItem("scriptavera-gate-" + S.pid); } catch (x) { /* ignore */ } S.follow = true; history.replaceState(null, "", `#p/${S.pid}`); toast("Confirmed. Reading the literature now."); await refresh(); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  rvpage(el) { S.review.n = +el.dataset.n; render(); const r = $(".rv-right"); if (r) r.scrollTop = 0; },
  rvprev() { const p = Object.keys(S.b.transcript.pages).map(Number).sort((a, b) => a - b); S.review.n = p[Math.max(0, p.indexOf(S.review.n) - 1)]; render(); const r = $(".rv-right"); if (r) r.scrollTop = 0; },
  rvnext() { const p = Object.keys(S.b.transcript.pages).map(Number).sort((a, b) => a - b); S.review.n = p[Math.min(p.length - 1, p.indexOf(S.review.n) + 1)]; render(); const r = $(".rv-right"); if (r) r.scrollTop = 0; },
  zoomin() { S.review.zoom = Math.min(3, S.review.zoom + 0.25); $("#rvimg").style.width = S.review.zoom * 100 + "%"; },
  zoomout() { S.review.zoom = Math.max(0.5, S.review.zoom - 0.25); $("#rvimg").style.width = S.review.zoom * 100 + "%"; },
  async topicok() { await topicSubmit("confirm"); },
  async topicrefine() {
    const T = topicState();
    if (!T.comment.trim() && T.problem === (((S.b.state || {}).meta || {}).problem || "") && T.keywords === (((S.b.state || {}).meta || {}).keywords || []).join(", "))
      return toast("Write what your notes are about more precisely, or correct the problem or the keywords, then search again.", true);
    await topicSubmit("refine");
  },
  scopepick(el) { const D = scopeState(); D.results[el.dataset.id] = el.dataset.v; render(); },
  scopekind(el) { scopeState().kind = el.dataset.v; render(); },
  async scopesubmit() {
    const D = scopeState();
    S.busy = "scope"; render();
    try {
      await api(`/api/projects/${S.pid}/scope`, { method: "POST", body: { kind: D.kind, results: D.results, note: D.note } });
      S.follow = D.kind !== "not_ready"; S.scope = null; history.replaceState(null, "", `#p/${S.pid}`);
      toast(D.kind === "not_ready" ? "Stopped here, as you chose." : "Your plan is saved. Writing the introduction now.");
      await refresh();
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  async ftitles() {
    S.busy = "ftitles"; render();
    try { await api(`/api/projects/${S.pid}/finish`, { method: "POST", body: { action: "titles" } }); await refresh(); toast("Five more titles suggested."); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  fintroedit() { const R = finishState(); R.editing = true; render(); const t = $("[data-fintro]"); if (t) t.focus(); },
  fintroview() { finishState().editing = false; render(); },
  fask() { const R = finishState(); R.asking = !R.asking; render(); },
  async fcite() {
    const R = finishState();
    S.busy = "fcite"; render();
    try {
      if (R.dirty) await api(`/api/projects/${S.pid}/paper`, { method: "PUT", body: { intro: R.intro } });
      await api(`/api/projects/${S.pid}/finish`, { method: "POST", body: { action: "cite", keys: R.keys, instruction: R.asking ? R.instr : "" } });
      R.dirty = false; R.editing = false; R.asking = false; R.instr = "";
      await refresh(); toast("The introduction was rewritten. Read it once more.");
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  async faddref() {
    const R = finishState();
    if (!R.addref.trim()) return toast("Enter a DOI, an arXiv number or a title.", true);
    S.busy = "faddref"; render();
    try { const r = await api(`/api/projects/${S.pid}/library`, { method: "POST", body: { action: "add", identifier: R.addref.trim() } });
      R.addref = ""; await refresh(); toast(`Added: ${r.title}. Tick it to cite it.`); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  async ffinish() {
    const R = finishState();
    if (!R.title || !R.title.trim()) return toast("Choose a title, or write your own.", true);
    S.busy = "ffinish"; render();
    try {
      await api(`/api/projects/${S.pid}/finish`, { method: "POST", body: { action: "finish", title: R.title.trim(), intro: R.dirty ? R.intro : undefined } });
      S.fin = null; S.view = "assemble"; S.tab = "paper"; S.follow = false; history.replaceState(null, "", `#p/${S.pid}/assemble`);
      await refresh(); toast("Finished. Every file carries your title and introduction.");
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  decfocus(el) { S.decide.focus = el.dataset.id; S.decide.page = 0; render(); const d = $("#dqdetail"); if (d) d.scrollTop = 0; },
  dectab(el) { S.decide.tab = el.dataset.v; render(); },
  decstep(el) {
    const Q = decideQueue(), cur = currentItem(), i = Q.findIndex((r) => r.x.id === cur.x.id), n = Q[i + +el.dataset.d];
    if (n) { S.decide.focus = n.x.id; S.decide.page = 0; S.decide.tab = settledAs(n) ? "done" : "open"; render(); const d = $("#dqdetail"); if (d) d.scrollTop = 0; }
  },
  decnext() { const cur = currentItem(); if (cur) { advanceDecide(cur.x.id); render(); const d = $("#dqdetail"); if (d) d.scrollTop = 0; } },
  decread(el) {
    const D = S.decide; D.read = D.read || {}; D.read[el.dataset.id] = true; saveDecide();
    advanceDecide(el.dataset.id); render(); const d = $("#dqdetail"); if (d) d.scrollTop = 0;
  },
  dppage(el) { S.decide.page = +el.dataset.n; render(); },
  dpmode(el) { S.decide.pmode = el.dataset.v; render(); },
  dptab(el) { S.decide.ptab = el.dataset.v; render(); },
  dropflag(el) { const f = findFlag(el.dataset.id); if (f) answerFlag(f, "", "kept"); },
  usealt(el) { const f = findFlag(el.dataset.id); if (f) answerFlag(f, f.alt, "suggested"); },
  useown(el) { const f = findFlag(el.dataset.id); if (f && (S.review.own[f.key] || "").trim()) answerFlag(f, S.review.own[f.key], "own"); },
  rvtab(el) { S.review.tab = el.dataset.v; render(); const r = $(".rv-body"); if (r) r.scrollTop = 0; },
  rvshow(el) { showPage(+el.dataset.n); },
  rvtext(el) { S.review.tab = "text"; S.review.n = +el.dataset.n; render(); },
  async figreopen(el) { await setFigure(el.dataset.id, { choice: "" }); S.review.tab = "todo"; const f = (S.b.figures || []).find((x) => x.id === el.dataset.id); if (f) S.review.n = f.page; render(); },
  ownread(el) {
    const f = findFlag(el.dataset.id); if (!f) return;
    if (S.review.own[f.key] === undefined) S.review.own[f.key] = ""; else delete S.review.own[f.key];
    render(); const i = $(`#own-${f.id}`); if (i) i.focus();
  },
  async figchoice(el) {
    const ok = await setFigure(el.dataset.id, { choice: el.dataset.v });
    if (ok) advanceReview({ tikz: "The redrawn figure will be used.", orig: "Your own drawing will be used.", none: "The drawing is left out.",
      explain: "Not printed; the writers still read it." }[el.dataset.v] || "Saved.");
  },
  async figprint(el) {
    /* print a drawing the app took for an explanation (or one left out): it is drawn, then checked like any figure */
    const id = el.dataset.id;
    const ok = await setFigure(id, { print: true }, "print");
    if (ok) {
      const f = (S.b.figures || []).find((x) => x.id === id); S.review.tab = "todo"; if (f) S.review.n = f.page; render();
      const card = $(`#fig-${id}`); if (card) card.scrollIntoView({ block: "start" });
    }
  },
  async figtext(el) {
    const id = el.dataset.id, why = $(`#figwhy-${id}`), note = why ? why.value.trim() : "";
    if (saveTranscript.t) { clearTimeout(saveTranscript.t); saveTranscript.t = null; const n = S.review.n; await saveTranscript(n, S.b.transcript.pages[n].text); }
    S.review.figBusy = id; S.review.figMode = "text"; render();
    try {
      const r = await api(`/api/projects/${S.pid}/figure/${id}`, { method: "POST", body: { astext: note } });
      S.b.figures = (S.b.figures || []).filter((f) => f.id !== id);
      if (S.b.transcript && S.b.transcript.pages[r.page]) { S.b.transcript.pages[r.page].text = r.text; S.b.transcript.pages[r.page].edited = true; }
      S.review.figBusy = ""; await refresh();
      advanceReview("Your note is now part of the text.");
    } catch (e) { toast(e.message, true); S.review.figBusy = ""; render(); }
  },
  figredraw(el) { const why = $(`#figwhy-${el.dataset.id}`); setFigure(el.dataset.id, { redraw: why ? why.value.trim() : "" }, true); },
  async approve() {
    const open = openItems();
    if (open.length) { S.review.tab = "todo"; S.review.n = open[0].n; render(); return toast(`${plural(open.length, "item")} still to check. The first one is shown.`, true); }
    S.busy = "approve"; render();
    try { await api(`/api/projects/${S.pid}/review`, { method: "POST", body: {} }); S.follow = true; history.replaceState(null, "", `#p/${S.pid}`); toast("Approved. Now the whole of the notes is read together."); await refresh(); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  decsim(el) {
    const D = S.decide, id = el.dataset.id; D.simplify[id] = D.simplify[id] === el.dataset.v ? "" : el.dataset.v; saveDecide();
    if (D.simplify[id]) { advanceDecide(id); toast("Settled. The next item is shown."); }
    render(); const d = $("#dqdetail"); if (d) d.scrollTop = 0;
  },
  deccomp(el) {
    const D = S.decide, id = el.dataset.id, cur = D.complete[id] || { choice: "", note: "", text: "" };
    cur.choice = cur.choice === el.dataset.v ? "" : el.dataset.v; D.complete[id] = cur;
    if (cur.choice === "edit" && !cur.text) cur.text = ((S.b.revision.items.complete || []).find((x) => x.id === id) || {}).proof || "";
    saveDecide();
    if (cur.choice === "accept" || cur.choice === "keep") { advanceDecide(id); toast("Settled. The next item is shown."); const d = $("#dqdetail"); if (d) d.scrollTop = 0; }
    render();
    if (cur.choice === "instruct") { const x = $(`[data-compnote="${id}"]`); if (x) x.focus(); }
  },
  decissue(el) {
    const D = S.decide, id = el.dataset.id, cur = D.issues[id] || { choice: "", note: "" };
    cur.choice = cur.choice === el.dataset.v ? "" : el.dataset.v; D.issues[id] = cur; saveDecide();
    if (cur.choice === "keep" || cur.choice === "fix") { advanceDecide(id); toast("Settled. The next item is shown."); const d = $("#dqdetail"); if (d) d.scrollTop = 0; }
    render();
    if (cur.choice === "instruct") { const t = $(`[data-decnote="${id}"]`); if (t) t.focus(); }
  },
  async decide() {
    const D = S.decide, c = decideCounts(), it = S.b.revision.items;
    if (c.done < c.need) {
      const first = it.simplify.find((p) => !["accept", "reject"].includes(D.simplify[p.id]))
        || (it.complete || []).find((x) => !compSettled(D.complete[x.id]))
        || it.issues.find((x) => { const m = D.issues[x.id] || {}; return !(m.choice === "keep" || (m.choice === "fix" && x.correction) || (m.choice === "instruct" && (m.note || "").trim())); });
      if (first) { D.focus = first.id; D.tab = "open"; render(); }
      return toast(`${c.need - c.done} item(s) still need your decision. The first one is shown.`, true);
    }
    S.busy = "decide"; render();
    try {
      await api(`/api/projects/${S.pid}/decisions`, { method: "POST", body: { simplify: D.simplify, complete: D.complete, issues: D.issues, remarks: D.remarks } });
      try { localStorage.removeItem("scriptavera-decide-" + S.pid); } catch (x) { /* ignore */ }
      S.follow = true; S.decide = null; S.tab = "paper"; history.replaceState(null, "", `#p/${S.pid}`); toast("Decisions saved. Writing the final version."); await refresh();
    } catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  journal() { if (S.b.paper && S.view === "assemble") { S.tab = "journal"; S.edit = null; render(); } else { S.jr = S.jr || newJr(); showDialog(journalDialog()); } },
  async proof() {
    S.proofCheck = null; S.recNow = null;
    S.proofData = { proof: S.b.proof || {} };
    showDialog(proofDialog());
    try { S.proofData = await api(`/api/projects/${S.pid}/proof`); showDialog(proofDialog()); }
    catch (e) { toast(e.message, true); }
  },
  async proofnow() {
    const mAuthors = (S.b.state.meta || {}).authors || [], R = S.recNow || {};
    const by = declarationMaker(mAuthors, R.recorder || "0", R.name || "");
    if (!by) return toast("Give your name: the declaration says who makes the record.", true);
    if (!R.declare) return toast("Tick the declaration: the record says who wrote the notes and that the details are correct.", true);
    S.busy = "proof"; showDialog(proofDialog());
    try {
      S.proofData = await api(`/api/projects/${S.pid}/proof`, { method: "POST", body: { action: "record_now",
        declaration: { agreed: true, author: by.is_author ? by.index : null, name: by.is_author ? "" : by.name } } });
      toast("Recorded and signed. The timestamps follow in the background.");
      await refresh();
    } catch (e) { toast(e.message, true); }
    S.busy = ""; showDialog(proofDialog());
  },
  async proofretry() {
    try {
      S.proofData = await api(`/api/projects/${S.pid}/proof`, { method: "POST", body: { action: "retry" } });
      const got = (S.proofData.proof || {}).earliest;
      toast(got ? "Timestamped." : "The services could not be reached yet. They are asked again later by themselves.", !got);
      await refresh();
    } catch (e) { toast(e.message, true); }
    showDialog(proofDialog());
  },
  async proofcheck() {
    const inp = $("#proofFiles");
    if (!inp || !inp.files.length) return toast("Choose the proof (the zip) and the notes to check.", true);
    const fd = new FormData();
    for (const f of inp.files) fd.append("files", f);
    S.proofCheck = { busy: true }; showDialog(proofDialog());
    try { S.proofCheck = (await api("/api/proof/verify", { method: "POST", form: fd })).check; }
    catch (e) { S.proofCheck = { error: e.message }; }
    showDialog(proofDialog());
  },
  jrchange() { S.jr.changing = !S.jr.changing; redrawJournal(); const q = $("#jrq"); if (q) q.focus(); },
  async jrsearch() {
    const R = S.jr, q = ($("#jrq") || {}).value || ""; R.q = q;
    if (q.trim().length < 3) return toast("Type at least three letters of the journal's name.", true);
    R.searching = true; redrawJournal();
    await journalSearch(q, (list, msg) => { R.results = list; R.message = msg; });
    R.searching = false; redrawJournal();
  },
  jrpick(el) { const j = S.jr.results[+el.dataset.i]; S.jr.guide = null; S.jr.guideSrc = null; postJournal({ journal: j }, "journal", `Journal set: ${j.name}.`); },
  jrtyped() { const name = S.jr.q.trim(); S.jr.guide = null; S.jr.guideSrc = null; postJournal({ journal: { name, publisher: "" } }, "journal", `Journal set: ${name}.`); },
  async jrremove() {
    captureJournalForm();
    const ok = await askConfirm({ title: "Remove the target journal?", yes: "Remove the journal", danger: false,
      body: "<p>The paper goes back to the default layout. You can choose a journal again at any time.</p>" });
    showDialog(journalDialog());                     // back to the journal, either way
    if (!ok) return;
    S.jr.guide = null; postJournal({ journal: null }, "journal", "Journal removed.");
  },
  async jrguide() {
    const R = S.jr, src = ($("#guidesrc") || {}).value || ""; R.guideSrc = src;
    if (!src.trim()) return toast("Give the web address of the instructions for authors, or paste their text.", true);
    R.guideBusy = true; redrawJournal();
    try { const r = await api(`/api/projects/${S.pid}/journal/guide`, { method: "POST", body: { source: src } }); R.guide = r; }
    catch (e) { toast(e.message, true); }
    R.guideBusy = false; redrawJournal();
  },
  jruse() { const R = S.jr, J = S.b.journal; postJournal({ journal: { ...J, guide: R.guide.values, guide_url: R.guide.url || J.guide_url || "" } }, "guide", "The guide's requirements are in force.").then(() => { R.guide = null; redrawJournal(); }); },
  jrdiscard() { S.jr.guide = null; redrawJournal(); },
  jrcustom() {
    const custom = {};
    $$("[data-cu]").forEach((el) => {
      const k = el.dataset.cu, v = el.value.trim(); if (!v) return;
      custom[k] = v === "true" ? true : v === "false" ? false : /^\d+$/.test(v) ? +v : v;
    });
    postJournal({ journal: { ...S.b.journal, custom } }, "custom", "Your requirements are in force.");
  },
  jrstatements() {
    const statements = {}; $$("[data-stm]").forEach((el) => { statements[el.dataset.stm] = el.value; });
    postJournal({ statements }, "statements", "Statements saved.");
  },
  tab(el) { S.tab = el.dataset.tab; S.edit = null; render(); },
  closeedit() { S.edit = null; render(); },
  editblock(el) { openEditor(el.dataset.id); },
  saveedit() {
    const text = $("#edtext").value, t = S.edit.target;
    if (t === "title" || t === "abstract" || t === "intro") return savePaper({ [t]: text });
    const b = S.b.paper.blocks.find((x) => x.id === t);
    savePaper({ block: (b.type === "section" || b.type === "subsection") ? { id: t, title: text } : { id: t, text } });
  },
  async deleteblock() {
    if (await askConfirm({ title: "Delete this passage from the paper?", yes: "Delete the passage",
      body: "<p>The passage is removed from the paper and the downloads are rebuilt without it.</p>" })) savePaper({ block: { id: S.edit.target, delete: true } });
  },
  usetitle(el) { const c = (S.b.paper.title_candidates || []).filter((t) => t !== S.b.paper.title); savePaper({ title: c[+el.dataset.i] }); },
  async revise() {
    const instr = $("#edinstr").value.trim(); if (!instr) return toast("Say what should change.", true);
    S.edit.text = $("#edtext").value; S.busy = "revise"; render();
    try { const r = await api(`/api/projects/${S.pid}/revise`, { method: "POST", body: { target: S.edit.target, instruction: instr } }); S.b.paper = r.paper; S.edit = null; toast("Rewritten. Check the passage."); await refresh(); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; render();
  },
  copytex() { const t = $("#texsrc").textContent; navigator.clipboard.writeText(t).then(() => toast("LaTeX copied."), () => toast("Copy failed; use Download.", true)); },
  async savekeys() {
    const env = {}; $$("[data-env]").forEach((i) => { if (i.value.trim()) env[i.dataset.env] = i.value.trim(); });
    const settings = { routing: {} };
    $$("[data-role]").forEach((s) => { if (s.value) settings.routing[s.dataset.role] = [s.value]; });
    $$("[data-set]").forEach((s) => { settings[s.dataset.set] = /^\d+$/.test(s.value) ? +s.value : s.value; });
    $$("[data-setbool]").forEach((s) => { settings[s.dataset.setbool] = !!s.checked; });
    S.busy = "keys"; showDialog(settingsDialog());
    try { S.meta = await api("/api/settings", { method: "POST", body: { env, settings } }); toast(S.meta.has_vision ? "Keys checked. Ready." : "Saved, but no image-reading model is reachable yet.", !S.meta.has_vision); }
    catch (e) { toast(e.message, true); }
    S.busy = ""; showDialog(settingsDialog()); render();
  },
};

document.addEventListener("click", (ev) => {
  const a = ev.target.closest("[data-act]");
  // Any other click closes an open menu: after this click is handled, so that a link clicked still opens.
  if (S.menu && !(a && a.dataset.act === "menu")) { S.menu = null; setTimeout(redrawMenus, 0); }
  if (a) { if (a.tagName !== "A") ev.preventDefault(); const fn = ACT[a.dataset.act]; if (fn) fn(a, ev); return; }
  if (ev.target.id === "overlay") return closeDialog();
  const link = ev.target.closest("a");
  if (link) {                                   // in-page links (citation numbers, theorem references) scroll; they must not change the route
    const href = link.getAttribute("href") || "";
    const host = link.closest("#paperHost, #draftHost");
    if (href.startsWith("#") && host) {
      ev.preventDefault();
      const t = host.querySelector("#" + CSS.escape(href.slice(1)));
      if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.add("hit"); setTimeout(() => t.classList.remove("hit"), 1800); }
    }
    return;
  }
  const host = ev.target.closest("#paperHost");
  if (host && !running()) {
    const el = ev.target.closest("[data-block],[data-part],.p-title");
    if (el) openEditor(el.dataset.block || el.dataset.part || "title");
  }
});
document.addEventListener("input", (ev) => {
  const el = ev.target;
  if (el.dataset.fdeclare !== undefined || el.dataset.frecorder !== undefined) return;     // handled on "change"
  if (el.dataset.bind) {
    bindPath(S.form, el.dataset.bind, el.value);
    if (/^authors\.\d+\.name$/.test(el.dataset.bind)) refreshDeclaration(true);       // the names offered and the text
    else if (el.dataset.bind === "recorderName") refreshDeclaration(false);
  } else if (el.dataset.rname !== undefined) {
    (S.recNow = S.recNow || {}).name = el.value;
    const mA = ((S.b && S.b.state && S.b.state.meta) || {}).authors || [], by = declarationMaker(mA, S.recNow.recorder || "0", el.value), t = $("#rdeclareText");
    if (t) t.textContent = by ? declarationText(mA.length, by) : "Give your name: the declaration says who makes it.";
  }
  else if (el.dataset.bindQ) { S.domQuery = el.value; const pos = el.selectionStart; render(); const n = $(".domain-search"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }
  else if (el.dataset.gateown) {
    const id = el.dataset.gateown, it = S.gate.items[id]; it.own = el.value; saveGate();
    const pv = $(`#pv-${id}`); if (pv) pv.innerHTML = el.value.trim() ? mixed(el.value) : "";
    const a = $(`#ans-${id}`); if (a) a.innerHTML = answerLine(it);
    const row = $(`#term-${id}`); if (row) row.classList.toggle("ok", answered(it));
    const c = gateCounts(); const g = $("#gatecount"); if (g) g.textContent = `${c.done} of ${c.need} answered`;
  } else if (el.dataset.gate) { S.gate.items[el.dataset.gate].meaning = el.value; saveGate(); }
  else if (el.dataset.gatef) { S.gate[el.dataset.gatef] = el.value; saveGate(); }
  else if (el.dataset.gateq) { S.gate.answers[el.dataset.gateq] = el.value; saveGate(); }
  else if (el.dataset.gatex) { bindPath(S.gate.extra, el.dataset.gatex, el.value); saveGate(); }
  else if (el.dataset.decnote) {
    const id = el.dataset.decnote; (S.decide.issues[id] = S.decide.issues[id] || { choice: "instruct", note: "" }).note = el.value; saveDecide();
    const c = decideCounts(); const g = $("#deccount"); if (g) g.textContent = `${c.done} of ${c.need} decided`;
    const card = $(`#dec-${CSS.escape(id)}`); if (card) card.classList.toggle("ok", !!el.value.trim());
    const done = $(`#donebtn-${CSS.escape(id)}`); if (done) done.disabled = !el.value.trim();
  } else if (el.dataset.remark) { S.decide.remarks[el.dataset.remark] = el.value; saveDecide(); }
  else if (el.dataset.compnote || el.dataset.compedit) {
    const id = el.dataset.compnote || el.dataset.compedit, m = (S.decide.complete[id] = S.decide.complete[id] || { choice: "", note: "", text: "" });
    if (el.dataset.compnote) m.note = el.value; else m.text = el.value;
    saveDecide();
    const c = decideCounts(); const g = $("#deccount"); if (g) g.textContent = `${c.done} of ${c.need} decided`;
    const card = $(`#dec-${CSS.escape(id)}`); if (card) card.classList.toggle("ok", compSettled(m));
    const b = $(`#cnext-${CSS.escape(id)}`); if (b) b.disabled = !el.value.trim();
  }
  else if (el.id === "jrq" && S.jr) { S.jr.q = el.value; }
  else if (el.id === "guidesrc" && S.jr) { S.jr.guideSrc = el.value; }
  else if (el.dataset.tgf) { topicState()[el.dataset.tgf] = el.value; }
  else if (el.dataset.scopenote !== undefined) { scopeState().note = el.value; }
  else if (el.dataset.fown !== undefined) {
    const R = finishState(); R.own = el.value;
    const own = $('[data-ftitle="own"]'); if (own && !own.checked) own.checked = true;
    R.title = el.value;
  }
  else if (el.dataset.fintro !== undefined) { const R = finishState(); R.intro = el.value; R.dirty = true; }
  else if (el.dataset.finstr !== undefined) { finishState().instr = el.value; }
  else if (el.dataset.faddref !== undefined) { finishState().addref = el.value; }
  else if (el.dataset.figcap) {
    const pv = $(`#cappv-${el.dataset.figcap}`); if (pv) { pv.innerHTML = el.value.trim() ? inner(latexLite(el.value)) : '<span class="muted">No caption yet.</span>'; renderMath(pv); }
  }
  else if (el.dataset.own) {
    const f = findFlag(el.dataset.own); if (!f) return;
    S.review.own[f.key] = el.value;
    const pv = $(`#ownpv-${f.id}`); if (pv) pv.innerHTML = el.value.trim() ? "It will read: " + readable(el.value) : "";
    const b = $(`#flag-${f.id} [data-act="useown"]`); if (b) b.disabled = !el.value.trim();
  }
  else if (el.id === "rvsrc") {
    const n = S.review.n, rec = S.b.transcript.pages[n];
    rec.text = el.value; rec.edited = true;
    $("#rvout").innerHTML = mixed(rec.text, reviewFlags(n).map((f) => f.snippet));
    const s = $("#rvsaved"); if (s) s.textContent = "Saving…";
    clearTimeout(saveTranscript.t); saveTranscript.t = setTimeout(() => saveTranscript(n, rec.text), 700);
  }
});
document.addEventListener("change", (ev) => {
  const el = ev.target;
  if (el.dataset.fdeclare !== undefined && S.form) { S.form.declare = el.checked; return; }
  if (el.dataset.frecorder !== undefined && S.form) { S.form.recorder = el.value; refreshDeclaration(true); return; }
  if (el.dataset.rdeclare !== undefined) { (S.recNow = S.recNow || {}).declare = el.checked; return; }
  if (el.dataset.rrecorder !== undefined) { (S.recNow = S.recNow || {}).recorder = el.value; showDialog(proofDialog()); return; }
  if (el.id === "fileInput") { addFiles(el.files); el.value = ""; }
  else if (el.dataset.figcap) setFigure(el.dataset.figcap, { caption: el.value });
  else if (el.dataset.ftitle !== undefined) {
    const R = finishState(), F = S.b.finish;
    R.title = el.dataset.ftitle === "own" ? R.own : F.titles[+el.dataset.ftitle];
    const keep = $("#main").scrollTop; render(); $("#main").scrollTop = keep;
  }
  else if (el.dataset.fkey !== undefined) {
    const R = finishState(), k = el.dataset.fkey;
    R.keys = el.checked ? [...new Set([...R.keys, k])] : R.keys.filter((x) => x !== k);
    const keep = $("#main").scrollTop; render(); $("#main").scrollTop = keep;
  }
  else if (el.dataset.occ) {
    const f = findFlag(el.dataset.occ); if (!f) return;
    const s = (S.review.skip[f.key] = S.review.skip[f.key] || {});
    if (el.checked) delete s[el.dataset.i]; else s[el.dataset.i] = true;
    const body = $(".rv-body"), keep = body ? body.scrollTop : 0; render(); const b2 = $(".rv-body"); if (b2) b2.scrollTop = keep;
  }
});
document.addEventListener("toggle", (ev) => {
  if (S.review && ev.target.matches && ev.target.matches(".rv-body details")) S.review.srcOpen = ev.target.open;
}, true);
document.addEventListener("pointerdown", (ev) => {
  const card = ev.target.closest && ev.target.closest(".rv-right [data-page]");
  if (card && S.view === "review") showPage(+card.dataset.page);              // the page image follows the item in hand
});
document.addEventListener("dragover", (ev) => { const d = ev.target.closest && ev.target.closest("#drop"); if (d) { ev.preventDefault(); d.classList.add("over"); } });
document.addEventListener("dragleave", (ev) => { const d = ev.target.closest && ev.target.closest("#drop"); if (d) d.classList.remove("over"); });
document.addEventListener("drop", (ev) => { const d = ev.target.closest && ev.target.closest("#drop"); if (d) { ev.preventDefault(); d.classList.remove("over"); addFiles(ev.dataTransfer.files); } });
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && S.menu && $("#overlay").hidden) {                 // a menu closes, and the focus goes back to its button
    const k = S.menu; S.menu = null; redrawMenus();
    const t = $(`[data-act="menu"][data-menu="${CSS.escape(k)}"]`); if (t) t.focus();
    return;
  }
  const inMenu = ev.target.closest && ev.target.closest(".menu");
  if (inMenu && (ev.key === "ArrowDown" || ev.key === "ArrowUp" || ev.key === "Home" || ev.key === "End")) {
    ev.preventDefault();
    const items = $$("[role=menuitem]", inMenu), i = items.indexOf(ev.target);
    const j = ev.key === "Home" ? 0 : ev.key === "End" ? items.length - 1 : (i + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    if (items[j]) items[j].focus();
    return;
  }
  if (ev.key === "Enter" && ev.target.id === "renamebox") { ev.preventDefault(); const b = $('[data-act="renamesave"]'); if (b) ACT.renamesave(b); }
  if (ev.key === "Escape" && !$("#overlay").hidden) closeDialog();
  if ((ev.key === "Enter" || ev.key === " ") && ev.target.id === "drop") { ev.preventDefault(); $("#fileInput").click(); }
  if (ev.key === "Enter" && ev.target.id === "addref") ACT.addref();
  if (ev.key === "Enter" && ev.target.dataset && ev.target.dataset.own) { ev.preventDefault(); ACT.useown({ dataset: { id: ev.target.dataset.own } }); }
  if (ev.key === "Enter" && ev.target.id === "formjq") { ev.preventDefault(); ACT.formjsearch(); }
  if (ev.key === "Enter" && ev.target.id === "jrq") { ev.preventDefault(); ACT.jrsearch(); }
});
window.addEventListener("hashchange", route);

(async function start() {
  S.form = newForm();
  try { await loadMeta(); await loadProjects(); }
  catch (e) { $("#main").innerHTML = `<div class="wrap"><div class="empty"><strong>The app did not start</strong>${esc(e.message)}</div></div>`; return; }
  route();
  if (!location.hash) render();
})();
