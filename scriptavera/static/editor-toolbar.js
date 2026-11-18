/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* The toolbar above the code, as in Overleaf's code editor: undo and redo, the heading of the line, bold and
   italic, inline and displayed formulas, the symbol palette, a link, a reference and a citation (with their
   suggestions), a figure (an image of the project or a new one), a table of any size, lists, indenting, and search.
   Uses the editor's helpers (cm, E, $, esc, modal, closeModal, menu, closeMenu, upload, insertSnippet, wrap, inMath,
   maybeComplete, openFind). */

const TBI = (() => {
  const s = (d, extra = "") => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" ${extra}>${d}</svg>`;
  return {
    undo: s('<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'),
    redo: s('<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>'),
    link: s('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
    ref: s('<path d="M12 2H2v10l9.3 9.3a1 1 0 0 0 1.4 0l8.6-8.6a1 1 0 0 0 0-1.4z"/><circle cx="7" cy="7" r="1.5"/>'),
    cite: s('<path d="M3 21c3 0 7-1 7-8V5H3v8h4c0 4-2 6-4 6z"/><path d="M14 21c3 0 7-1 7-8V5h-7v8h4c0 4-2 6-4 6z"/>'),
    figure: s('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>'),
    table: s('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'),
    bullets: s('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>'),
    numbers: s('<path d="M10 6h10M10 12h10M10 18h10"/><path d="M4 4h1v4M4 8h2M4 11.5c0-.8.7-1.5 1.5-1.5S7 10.7 7 11.5c0 1-3 2-3 3.5h3M4 17h2.5a1 1 0 0 1 0 2H5h1.5a1 1 0 0 1 0 2H4"/>'),
    outdent: s('<path d="M21 5H11M21 12H11M21 19H11"/><path d="m7 8-4 4 4 4"/>'),
    indent: s('<path d="M21 5H11M21 12H11M21 19H11"/><path d="m3 8 4 4-4 4"/>'),
    search: s('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'),
    chevron: s('<path d="m6 9 6 6 6-6"/>'),
  };
})();

const HEADINGS = [["", "Normal text"], ["section", "Section"], ["subsection", "Subsection"], ["subsubsection", "Subsubsection"], ["paragraph", "Paragraph"]];
function drawToolbar() {
  const b = (act, html, title, cls = "") => `<button type="button" class="tb-b ${cls}" data-act="${act}" title="${esc(title)}" aria-label="${esc(title)}">${html}</button>`;
  $("#tb").innerHTML = [
    b("tb-undo", TBI.undo, `Undo (${MOD}+Z)`), b("tb-redo", TBI.redo, `Redo (${MOD}+Y)`), `<span class="tb-sep"></span>`,
    `<button type="button" class="tb-b tb-head" data-act="tb-head" title="The heading of this line" aria-haspopup="menu"><span id="tbHead">Normal text</span>${TBI.chevron}</button>`,
    `<span class="tb-sep"></span>`,
    b("tb-bold", "<b>B</b>", `Bold (${MOD}+B)`, "tb-txt"), b("tb-italic", "<i>I</i>", `Italic (${MOD}+I)`, "tb-txt"), `<span class="tb-sep"></span>`,
    b("tb-math", '<span class="tb-math">$x$</span>', "Formula in the text"), b("tb-display", '<span class="tb-math">[ x ]</span>', "Displayed formula"),
    b("tb-symbols", '<span class="tb-sym">Ω</span>', "Symbol palette"), `<span class="tb-sep"></span>`,
    b("tb-link", TBI.link, "Link (\\href)"), b("tb-ref", TBI.ref, "Reference to a label (\\ref)"), b("tb-cite", TBI.cite, "Citation (\\cite)"),
    `<span class="tb-sep"></span>`,
    b("tb-figure", TBI.figure, "Insert a figure"), b("tb-table", TBI.table, "Insert a table"), `<span class="tb-sep"></span>`,
    b("tb-bullets", TBI.bullets, "Bulleted list"), b("tb-numbers", TBI.numbers, "Numbered list"),
    b("tb-outdent", TBI.outdent, `Indent less (${MOD}+[)`), b("tb-indent", TBI.indent, `Indent more (${MOD}+])`), `<span class="tb-sep"></span>`,
    b("tb-search", TBI.search, `Search (${MOD}+F)`),
    `<button type="button" class="tb-b tb-more" id="tbMore" data-act="tb-more" title="More" aria-label="More tools" hidden>⋯</button>`,
  ].join("");
  fitToolbar();
}
/* what does not fit in the width of the code goes into a ⋯ menu, as in Overleaf */
function fitToolbar() {
  const bar = $("#tb");
  if (!bar) return;
  const items = [...bar.children].filter((x) => x.id !== "tbMore");
  items.forEach((x) => { x.hidden = false; });
  const more = $("#tbMore");
  more.hidden = true;
  if (bar.scrollWidth <= bar.clientWidth + 1) return;
  more.hidden = false;
  const room = bar.clientWidth - more.offsetWidth - 22;
  let used = 0;
  for (const x of items) {
    used += x.offsetWidth + 2;
    if (used > room) x.hidden = true;
  }
  while (items.length && items.filter((x) => !x.hidden).slice(-1)[0] && items.filter((x) => !x.hidden).slice(-1)[0].classList.contains("tb-sep"))
    items.filter((x) => !x.hidden).slice(-1)[0].hidden = true;
}
function moreMenu(anchor) {
  const hidden = [...$("#tb").children].filter((x) => x.hidden && x.tagName === "BUTTON" && x.id !== "tbMore");
  menu(anchor, hidden.map((x) => `<button type="button" data-act="${x.dataset.act}">${esc(x.title || x.textContent)}</button>`).join(""));
}
if (window.ResizeObserver) new ResizeObserver(() => fitToolbar()).observe($("#codePane"));
function lineHeading() {
  const t = cm.getLine(cm.getCursor().line) || "";
  const m = t.match(/^\s*\\(section|subsection|subsubsection|paragraph)\*?\s*\{/);
  return m ? m[1] : "";
}
function setHeading(kind) {
  closeMenu();
  const from = cm.getCursor("from").line, to = cm.getCursor("to").line;
  cm.operation(() => {
    for (let l = from; l <= to; l++) {
      const t = cm.getLine(l);
      if (!t.trim() && from !== to) continue;
      const m = t.match(/^(\s*)\\(section|subsection|subsubsection|paragraph)(\*?)\s*\{(.*)\}(.*)$/);
      const indent = (m ? m[1] : t.match(/^\s*/)[0]), inner = m ? m[4] : t.trim(), rest = m ? m[5] : "", star = m ? m[3] : "";
      const out = kind ? `${indent}\\${kind}${star}{${inner}}${rest}` : `${indent}${inner}${rest}`;
      cm.replaceRange(out, { line: l, ch: 0 }, { line: l, ch: t.length });
      if (l === from && !inner) cm.setCursor({ line: l, ch: out.length - rest.length - 1 });
    }
  });
  cm.focus();
}
function insertMath(display) {
  const sel = cm.getSelection();
  if (display) {
    const cur = cm.getCursor("from"), line = cm.getLine(cur.line), start = cur.ch === 0 || !line.slice(0, cur.ch).trim();
    insertSnippet(cm.getCursor("from"), cm.getCursor("to"), `${start ? "" : "\n"}\\[\n  ${sel ? sel.replace(/\$/g, "") : "$1"}\n\\]\n`);
  } else insertSnippet(cm.getCursor("from"), cm.getCursor("to"), `$${sel || "$1"}$`);
  cm.focus();
}
function insertList(env) {
  const from = cm.getCursor("from"), to = cm.getCursor("to");
  if (cm.somethingSelected() && from.line !== to.line) {
    const lines = []; for (let l = from.line; l <= to.line; l++) lines.push(cm.getLine(l));
    const indent = lines[0].match(/^\s*/)[0];
    const body = lines.filter((x) => x.trim()).map((x) => `${indent}  \\item ${x.trim().replace(/^\\item\s*/, "")}`).join("\n");
    cm.replaceRange(`${indent}\\begin{${env}}\n${body}\n${indent}\\end{${env}}`, { line: from.line, ch: 0 }, { line: to.line, ch: cm.getLine(to.line).length });
  } else {
    const cur = cm.getCursor(), before = cm.getLine(cur.line).slice(0, cur.ch);
    insertSnippet(cm.getCursor("from"), cm.getCursor("to"), `${before.trim() ? "\n" : ""}\\begin{${env}}\n  \\item ${cm.getSelection() || "$1"}\n\\end{${env}}\n`);
  }
  cm.focus();
}
function insertRef(cmd) {
  const from = cm.getCursor("from"), to = cm.getCursor("to");
  cm.replaceRange(`${cmd === "ref" && !/~$/.test(cm.getLine(from.line).slice(0, from.ch)) && /\w$/.test(cm.getLine(from.line).slice(0, from.ch)) ? "~" : ""}\\${cmd}{}`, from, to);
  const c = cm.getCursor(); cm.setCursor({ line: c.line, ch: c.ch - 1 });
  cm.focus();
  maybeComplete(null, true);
}
function insertLink() {
  const sel = cm.getSelection();
  insertSnippet(cm.getCursor("from"), cm.getCursor("to"), sel ? `\\href{$1}{${sel}}` : "\\href{${1:https://}}{$2}");
  cm.focus();
}
function newBlockAt() {                   // the place for a new block: its own line after the current one
  const cur = cm.getCursor(), line = cm.getLine(cur.line);
  if (!line.trim()) return { from: { line: cur.line, ch: 0 }, to: { line: cur.line, ch: line.length }, pre: "" };
  return { from: { line: cur.line, ch: line.length }, to: { line: cur.line, ch: line.length }, pre: "\n" };
}

/* figure: an image of the project (or one uploaded now), with its caption, label and width */
function figureDialog() {
  const imgs = E.tree.filter((x) => x.kind === "binary").map((x) => x.path);
  modal(`<header><h2>Insert a figure</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <form class="fig-form" data-act="tb-figsave">
      <label>Image<select id="figPath">${imgs.map((p) => `<option>${esc(p)}</option>`).join("") || `<option value="">(no image in the project yet)</option>`}</select></label>
      <div class="fig-up"><button type="button" class="btn small" data-act="tb-figupload">Upload an image…</button><span class="muted">PNG, JPG, PDF or EPS</span></div>
      <label>Caption<input id="figCaption" placeholder="What the figure shows"></label>
      <label>Label<input id="figLabel" placeholder="fig:name" spellcheck="false"></label>
      <label>Width<select id="figWidth"><option value="0.5\\linewidth">Half the line</option><option value="0.8\\linewidth" selected>Most of the line</option>
        <option value="\\linewidth">The whole line</option></select></label>
      <div class="fig-buttons"><button class="btn primary" type="submit">Insert figure</button></div>
    </form></div>`);
  const syncLabel = () => { const p = $("#figPath").value; if (p && !$("#figLabel").dataset.touched) $("#figLabel").value = "fig:" + p.split("/").pop().replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9]+/g, "-").toLowerCase(); };
  $("#figPath").addEventListener("change", syncLabel);
  $("#figLabel").addEventListener("input", () => { $("#figLabel").dataset.touched = "1"; });
  syncLabel();
}
function figureInsert() {
  const path = $("#figPath").value, cap = $("#figCaption").value.trim(), lab = $("#figLabel").value.trim(), w = $("#figWidth").value;
  closeModal();
  const at = newBlockAt();
  const img = path.replace(/\.(png|jpe?g|pdf|eps)$/i, "");
  insertSnippet(at.from, at.to, `${at.pre}\\begin{figure}[htbp]\n  \\centering\n  \\includegraphics[width=${w}]{${img || "$1"}}\n  \\caption{${cap || "$2"}}\n  \\label{${lab || "fig:$3"}}\n\\end{figure}\n`);
  cm.focus();
}
document.addEventListener("change", (ev) => {
  if (ev.target.id !== "figUpload") return;
  const files = ev.target.files;
  if (!files || !files.length) return;
  (async () => {
    await upload(files);
    const name = files[0].name.replace(/[^A-Za-z0-9._\-]+/g, "-");
    const sel = $("#figPath");
    if (!sel) return;
    const opts = E.tree.filter((x) => x.kind === "binary").map((x) => x.path);
    sel.innerHTML = opts.map((p) => `<option ${p.endsWith(name) ? "selected" : ""}>${esc(p)}</option>`).join("");
    sel.dispatchEvent(new Event("change"));
  })();
});

/* table: choose the size on a grid, as in Overleaf */
function tableMenu(anchor) {
  const R = 8, C = 8;
  let cells = "";
  for (let r = 1; r <= R; r++) for (let c = 1; c <= C; c++) cells += `<span data-r="${r}" data-c="${c}"></span>`;
  menu(anchor, `<div class="tb-grid" id="tbGrid">${cells}</div><div class="tb-grid-size" id="tbGridSize">Choose the size of the table</div>`);
  const g = $("#tbGrid");
  g.addEventListener("mouseover", (ev) => {
    const t = ev.target.closest("span"); if (!t) return;
    const r = +t.dataset.r, c = +t.dataset.c;
    $$("span", g).forEach((s) => s.classList.toggle("on", +s.dataset.r <= r && +s.dataset.c <= c));
    $("#tbGridSize").textContent = `${r} × ${c}`;
  });
  g.addEventListener("click", (ev) => { const t = ev.target.closest("span"); if (t) { closeMenu(); tableInsert(+t.dataset.r, +t.dataset.c); } });
}
function tableInsert(r, c) {
  const at = newBlockAt();
  const row = Array(c).fill("  ").join("&");
  const rows = Array(r).fill(`    ${row} \\\\`).join("\n");
  insertSnippet(at.from, at.to, `${at.pre}\\begin{table}[htbp]\n  \\centering\n  \\caption{$1}\n  \\label{tab:$2}\n  \\begin{tabular}{${"c".repeat(c)}}\n    \\hline\n${rows}\n    \\hline\n  \\end{tabular}\n\\end{table}\n`);
  cm.focus();
}

/* the symbol palette */
const SYMBOLS = {
  Greek: "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega",
  Relations: "le ge leq geq neq approx sim simeq cong equiv propto ll gg leqslant geqslant lesssim gtrsim in notin ni subset subseteq supset supseteq subsetneq mid parallel perp prec succ preceq succeq models vdash",
  Operators: "pm mp times div cdot ast star circ bullet oplus ominus otimes odot cup cap setminus wedge vee sum prod coprod int iint oint bigcup bigcap bigoplus bigotimes nabla partial sqrt{x} frac{a}{b}",
  Arrows: "to rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow implies iff mapsto longmapsto longrightarrow longleftarrow hookrightarrow uparrow downarrow updownarrow nearrow searrow nwarrow swarrow rightharpoonup leftharpoonup",
  Misc: "infty emptyset varnothing forall exists nexists neg aleph hbar ell Re Im wp angle triangle prime cdots ldots vdots ddots langle rangle lfloor rfloor lceil rceil | dagger S P",
  Accents: "hat{a} bar{a} tilde{a} vec{a} dot{a} ddot{a} check{a} breve{a} overline{ab} widehat{ab} widetilde{ab} underline{ab} mathbb{R} mathcal{A} mathfrak{g} mathbf{v} mathrm{d}",
};
let symTab = "Greek";
function symbolMenu(anchor) {
  const tabs = Object.keys(SYMBOLS).map((k) => `<button type="button" class="${k === symTab ? "on" : ""}" data-act="tb-symtab" data-k="${k}">${k}</button>`).join("");
  menu(anchor, `<div class="sym"><div class="sym-head"><input id="symFind" placeholder="Search symbols, e.g. arrow" spellcheck="false"></div>
    <div class="sym-tabs">${tabs}</div><div class="sym-grid" id="symGrid"></div></div>`);
  drawSymbols("");
  $("#symFind").addEventListener("input", (ev) => drawSymbols(ev.target.value.trim()));
  $("#symFind").focus();
}
function drawSymbols(q) {
  const all = q ? Object.values(SYMBOLS).join(" ").split(" ") : SYMBOLS[symTab].split(" ");
  const list = [...new Set(all)].filter((s) => !q || s.toLowerCase().includes(q.toLowerCase()));
  $("#symGrid").innerHTML = list.map((s) => {
    const tex = `\\${s}`;
    let html = esc(tex);
    try { html = katex.renderToString(tex, { throwOnError: true, strict: "ignore" }); } catch (e) { /* shown as its command */ }
    return `<button type="button" data-act="tb-sym" data-tex="${esc(tex)}" title="${esc(tex.replace(/\{([^}]*)\}/g, "{…}"))}">${html}</button>`;
  }).join("") || `<div class="muted">No symbol matches.</div>`;
}
function insertSymbol(tex) {
  closeMenu();
  let k = 0;
  const field = tex.replace(/\{[^}]*\}/g, () => `{$${++k}}`);
  const math = inMath();
  insertSnippet(cm.getCursor("from"), cm.getCursor("to"), math ? field : `$${field}$`);
  cm.focus();
}

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-act]");
  if (!t || t.tagName === "FORM" || !t.dataset.act.startsWith("tb-")) return;
  ev.preventDefault();
  if (!cm) return;
  const anchor = t.closest("#menu") ? $("#tbMore") : t;
  if (t.closest("#menu") && !/^tb-(head|symbols|table|symtab|sym|sethead|more)$/.test(t.dataset.act)) closeMenu();
  switch (t.dataset.act) {
    case "tb-more": moreMenu(t); break;
    case "tb-undo": closeMenu(); cm.undo(); cm.focus(); break;
    case "tb-redo": cm.redo(); cm.focus(); break;
    case "tb-head": {
      const now = lineHeading();
      menu(anchor, HEADINGS.map(([k, name]) => `<button type="button" data-act="tb-sethead" data-k="${k}" class="${k === now ? "on" : ""} h-${k || "text"}">${name}</button>`).join(""));
      break;
    }
    case "tb-sethead": setHeading(t.dataset.k); break;
    case "tb-bold": wrap("textbf"); break;
    case "tb-italic": wrap("textit"); break;
    case "tb-math": insertMath(false); break;
    case "tb-display": insertMath(true); break;
    case "tb-symbols": symbolMenu(anchor); break;
    case "tb-symtab": symTab = t.dataset.k; $$(".sym-tabs button").forEach((b) => b.classList.toggle("on", b === t)); $("#symFind").value = ""; drawSymbols(""); break;
    case "tb-sym": insertSymbol(t.dataset.tex); break;
    case "tb-link": insertLink(); break;
    case "tb-ref": insertRef("ref"); break;
    case "tb-cite": insertRef("cite"); break;
    case "tb-figure": figureDialog(); break;
    case "tb-figupload": { let i = $("#figUpload"); if (!i) { i = document.createElement("input"); i.type = "file"; i.id = "figUpload"; i.hidden = true; i.accept = ".png,.jpg,.jpeg,.pdf,.eps"; document.body.appendChild(i); } i.click(); break; }
    case "tb-table": tableMenu(anchor); break;
    case "tb-bullets": insertList("itemize"); break;
    case "tb-numbers": insertList("enumerate"); break;
    case "tb-outdent": cm.execCommand("indentLess"); cm.focus(); break;
    case "tb-indent": cm.execCommand("indentMore"); cm.focus(); break;
    case "tb-search": openFind(); break;
    default: break;
  }
});
document.addEventListener("submit", (ev) => { if (ev.target.dataset.act === "tb-figsave") { ev.preventDefault(); figureInsert(); } });
cm.on("cursorActivity", () => { const h = lineHeading(), el = $("#tbHead"); if (el) el.textContent = (HEADINGS.find((x) => x[0] === h) || HEADINGS[0])[1]; });
drawToolbar();
