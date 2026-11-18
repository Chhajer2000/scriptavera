/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* The code check, as you type, the way Overleaf's works, and a little more:
   - a command only allowed in a formula used outside one (\alpha, \frac, ^, _ ...): LaTeX would stop with
     "Missing $ inserted"; a command only allowed in text used inside a formula (\section, \item ...);
   - a command LaTeX does not know here, with the one probably meant ("\textbff: did you mean \textbf?") or the
     package it comes from ("\mathbb comes from amssymb, which this paper does not load"). What LaTeX knows is read by
     the server from LaTeX itself, the class and every package the paper loads (texdb.py);
   - an environment LaTeX does not know; a \ref to a label that does not exist; a \cite of a key no reference has.
   %%novalidate at the top of a file turns the check off for it, %%begin novalidate ... %%end novalidate for a part,
   as in Overleaf. Each problem is marked in the gutter and underlined where it is; hovering shows what to do.
   Compile errors get a plain explanation too (errorHint), and an unknown command is underlined at its place. */

E.cmds = null;
const KNOWN = { cmds: new Set(), envs: new Set(), list: [] };
async function loadCommands() {
  try {
    const r = await api(`${API}/commands`);
    E.cmds = r;
    /* when LaTeX's own files could not all be read, the usual commands are taken as known, so that only what looks
       like a misspelling of one of them is pointed out */
    const usual = r.complete ? [] : [...POPULAR.keys()];
    KNOWN.cmds = new Set([...r.known, ...usual]); KNOWN.envs = new Set([...r.envs, ...(r.complete ? [] : Object.keys(TEX_ENVS))]);
    KNOWN.list = [...KNOWN.cmds];
    for (const p of E.docs.keys()) lintNow(p);
  } catch (e) { /* the check works without it, only less */ }
}

const POPULAR = new Map();
(typeof TEX_COMMANDS !== "undefined" ? TEX_COMMANDS : []).forEach((x, i) => { const n = cmdName(x[0]); if (n && !POPULAR.has(n)) POPULAR.set(n, i); });
const MATH_ONLY = new Set((typeof TEX_COMMANDS !== "undefined" ? TEX_COMMANDS : []).filter((x) => x[2] === "m").map((x) => cmdName(x[0])).filter(Boolean));
for (const n of ["nonumber", "notag", "tag", "intertext", "shortintertext", "limits", "nolimits"]) MATH_ONLY.delete(n);   // checked by LaTeX in its own way
const TEXT_ONLY = new Set(["section", "subsection", "subsubsection", "chapter", "part", "paragraph", "subparagraph", "item", "maketitle", "caption",
  "usepackage", "documentclass", "newpage", "clearpage", "par", "appendix", "tableofcontents", "bibliography", "printbibliography"]);
/* where a command LaTeX does not know usually comes from */
const PACKAGE_FOR = {
  mathbb: "amssymb", mathfrak: "amssymb", varnothing: "amssymb", leqslant: "amssymb", geqslant: "amssymb", lesssim: "amssymb", gtrsim: "amssymb",
  nexists: "amssymb", twoheadrightarrow: "amssymb", subsetneq: "amssymb", nmid: "amssymb", square: "amssymb", blacksquare: "amssymb",
  therefore: "amssymb", because: "amssymb", mathscr: "mathrsfs", mathds: "dsfont", mathbbm: "bbm", llbracket: "stmaryrd", rrbracket: "stmaryrd",
  eqref: "amsmath", text: "amsmath", binom: "amsmath", dfrac: "amsmath", tfrac: "amsmath", operatorname: "amsmath", DeclareMathOperator: "amsmath",
  boldsymbol: "amsmath", xrightarrow: "amsmath", xleftarrow: "amsmath", overset: "amsmath", underset: "amsmath", iint: "amsmath", iiint: "amsmath",
  implies: "amsmath", impliedby: "amsmath", lvert: "amsmath", rvert: "amsmath", lVert: "amsmath", rVert: "amsmath", intertext: "amsmath",
  coloneqq: "mathtools", DeclarePairedDelimiter: "mathtools", shortintertext: "mathtools", mathclap: "mathtools",
  theoremstyle: "amsthm", qedhere: "amsthm", qed: "amsthm", newtheoremstyle: "amsthm",
  includegraphics: "graphicx", graphicspath: "graphicx", rotatebox: "graphicx", scalebox: "graphicx", resizebox: "graphicx",
  url: "url (or hyperref)", href: "hyperref", hypersetup: "hyperref", autoref: "hyperref", nameref: "hyperref", hyperref: "hyperref",
  cref: "cleveref", Cref: "cleveref", crefname: "cleveref", textcolor: "xcolor", color: "xcolor", definecolor: "xcolor", colorbox: "xcolor",
  toprule: "booktabs", midrule: "booktabs", bottomrule: "booktabs", cmidrule: "booktabs", multirow: "multirow",
  SI: "siunitx", si: "siunitx", num: "siunitx", qty: "siunitx", unit: "siunitx", bm: "bm", cancel: "cancel", sout: "ulem", nicefrac: "nicefrac",
  tikz: "tikz", draw: "tikz", node: "tikz", tikzset: "tikz", usetikzlibrary: "tikz", citep: "natbib", citet: "natbib", citeauthor: "natbib",
  printbibliography: "biblatex", addbibresource: "biblatex", parencite: "biblatex", textcite: "biblatex", todo: "todonotes", lipsum: "lipsum",
  geometry: "geometry", setlist: "enumitem", captionof: "caption", subcaption: "subcaption", lstinline: "listings", lstset: "listings",
  doublespacing: "setspace", onehalfspacing: "setspace", fancyhead: "fancyhdr", fancyfoot: "fancyhdr", xspace: "xspace", ket: "braket", bra: "braket",
};
const ENV_PACKAGE = { tikzpicture: "tikz", tikzcd: "tikz-cd", axis: "pgfplots", align: "amsmath", "align*": "amsmath", gather: "amsmath",
  "gather*": "amsmath", multline: "amsmath", "multline*": "amsmath", flalign: "amsmath", alignat: "amsmath", split: "amsmath", aligned: "amsmath",
  gathered: "amsmath", cases: "amsmath", pmatrix: "amsmath", bmatrix: "amsmath", vmatrix: "amsmath", Bmatrix: "amsmath", matrix: "amsmath",
  proof: "amsthm", algorithm: "algorithm", algorithmic: "algpseudocode", lstlisting: "listings", minted: "minted", subfigure: "subcaption",
  longtable: "longtable", tabularx: "tabularx", multicols: "multicol", wrapfigure: "wrapfig", comment: "comment", Verbatim: "fancyvrb",
  dcases: "mathtools", "dcases*": "mathtools", CD: "amscd", landscape: "pdflscape", spacing: "setspace", enumerate: "", itemize: "" };
const LITERAL_ARGS = new Set(["label", "ref", "eqref", "cref", "Cref", "autoref", "pageref", "nameref", "vref", "labelcref", "cite", "citep", "citet",
  "citeauthor", "citeyear", "parencite", "textcite", "autocite", "nocite", "url", "href", "input", "include", "includegraphics", "includepdf",
  "bibliography", "bibliographystyle", "addbibresource", "usepackage", "RequirePackage", "documentclass", "graphicspath", "hypersetup", "hyperref",
  "hyperlink", "hypertarget", "path", "nolinkurl", "lstinputlisting", "inputminted", "newcounter", "setcounter", "addtocounter", "stepcounter",
  "refstepcounter", "value", "arabic", "roman", "Roman", "alph", "Alph", "fnsymbol", "begin", "end", "newtheorem", "theoremstyle", "bibitem",
  "subfile", "externaldocument", "pagestyle", "thispagestyle", "color", "textcolor", "definecolor", "colorbox", "setlist", "geometry",
  "crefname", "Crefname", "usetikzlibrary", "pgfplotsset", "tikzset", "lstset", "captionsetup", "newlength", "settowidth", "numberwithin"]);
/* definitions: what is defined and its definition are not checked (how many groups each takes) */
const DEF_GROUPS = { newcommand: 2, renewcommand: 2, providecommand: 2, DeclareRobustCommand: 2, newrobustcmd: 2, renewrobustcmd: 2,
  providerobustcmd: 2, DeclareMathOperator: 2, DeclarePairedDelimiter: 3, DeclarePairedDelimiterX: 4, NewDocumentCommand: 3,
  RenewDocumentCommand: 3, ProvideDocumentCommand: 3, DeclareDocumentCommand: 3, NewExpandableDocumentCommand: 3, newenvironment: 3,
  renewenvironment: 3, NewDocumentEnvironment: 4, RenewDocumentEnvironment: 4, newtheoremstyle: 9, newfont: 2, DeclareMathSymbol: 4,
  DeclareMathAlphabet: 5, newlength: 1, newsavebox: 1, newcounter: 1, pgfmathsetmacro: 1, pgfmathtruncatemacro: 1, newif: 1 };
const DEF_ENV = /^(newenvironment|renewenvironment|NewDocumentEnvironment|RenewDocumentEnvironment)$/;
const DEFS_RE = /\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand|(?:re)?newrobustcmd|DeclareMathOperator|DeclarePairedDelimiterX?|(?:New|Renew|Provide|Declare)DocumentCommand|[gex]?def|let|newlength|newsavebox|pgfmathsetmacro)\*?\s*\{?\s*\\([A-Za-z]+)|\\(?:(?:re)?newenvironment|(?:New|Renew)DocumentEnvironment|newtheorem\*?)\s*\{([A-Za-z*]+)\}/g;
const TEXT_IN_MATH = new Set(["text", "textrm", "textit", "textbf", "textsf", "texttt", "textup", "textnormal", "emph", "mbox", "hbox", "intertext",
  "shortintertext", "textsc", "textsl", "fbox", "parbox", "makebox", "framebox"]);
const MATH_CELLS = /^(tikzcd|CD)$/;
const VERBATIM = /^(verbatim\*?|Verbatim|lstlisting|minted|comment|filecontents\*?|alltt)$/;

function dist(a, b, cap) {                       // edit distance with swaps, given up beyond `cap`
  if (Math.abs(a.length - b.length) > cap) return cap + 1;
  const d = [];
  for (let i = 0; i <= a.length; i++) { d[i] = [i]; }
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    let best = cap + 1;
    for (let j = 1; j <= b.length; j++) {
      const c = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
      best = Math.min(best, d[i][j]);
    }
    if (best > cap) return cap + 1;
  }
  return d[a.length][b.length];
}
const NEAR = new Map();
function closest(name, pool) {
  const key = name + "|" + pool.length;
  if (NEAR.has(key)) return NEAR.get(key);
  const cap = name.length <= 4 ? 1 : 2;
  let best = null, bd = cap + 1, br = 1e9;
  for (const c of pool) {
    if (c === name || Math.abs(c.length - name.length) > cap || c.includes("@")) continue;
    const dd = dist(name, c, cap);
    if (dd > cap) continue;
    const rank = POPULAR.has(c) ? POPULAR.get(c) : 5000;
    if (dd < bd || (dd === bd && rank < br)) { best = c; bd = dd; br = rank; }
  }
  if (NEAR.size > 500) NEAR.clear();
  NEAR.set(key, best);
  return best;
}
/* what the open files define (saved or not): known at once, without waiting for the server */
function localDefs() {
  const cmds = new Set(), envs = new Set();
  for (const [p, d] of E.docs) {
    if (!/\.(tex|sty|cls)$/.test(p)) continue;
    for (const m of d.doc.getValue().matchAll(DEFS_RE)) { if (m[1]) cmds.add(m[1]); if (m[2]) envs.add(m[2]); }
  }
  return { cmds, envs };
}

/* One pass over a file: where each command is, and whether it is in a formula there. */
function scanModes(text) {
  const out = { cmds: [], scripts: [], envs: [], refs: [], cites: [], skip: [], defs: new Set(), defEnvs: new Set() };
  const n = text.length;
  const stack = [];                              // frames: {mode, end: "$", "$$", "\\)", "\\]", env name, or brace depth}
  let depth = 0;
  const mode = () => (stack.length ? stack[stack.length - 1].mode : "text");
  const group = (i) => {                        // the {...} at i (after spaces): [start, end] or null
    let k = i;
    while (k < n && (text[k] === " " || text[k] === "\t")) k++;
    if (text[k] !== "{") return null;
    let d = 0;
    for (let j = k; j < n; j++) {
      if (text[j] === "\\") { j++; continue; }
      if (text[j] === "%") { const e = text.indexOf("\n", j); if (e < 0) return [k, n]; j = e; continue; }
      if (text[j] === "{") d++;
      else if (text[j] === "}" && --d === 0) return [k, j + 1];
    }
    return [k, n];
  };
  const opt = (i) => {                          // an [...] at i (after spaces): its end, or i
    let k = i;
    while (k < n && (text[k] === " " || text[k] === "\t")) k++;
    if (text[k] !== "[") return i;
    let d = 0;
    for (let j = k; j < n; j++) {
      if (text[j] === "\\") { j++; continue; }
      if (text[j] === "{") d++; else if (text[j] === "}") d--;
      else if (text[j] === "]" && d <= 0) return j + 1;
    }
    return n;
  };
  let i = 0;
  while (i < n) {
    const c = text[i];
    if (c === "%") {
      if (text.startsWith("%%begin novalidate", i)) { const e = text.indexOf("%%end novalidate", i); out.skip.push([i, e < 0 ? n : e]); i = e < 0 ? n : e + 16; continue; }
      const e = text.indexOf("\n", i); i = e < 0 ? n : e + 1; continue;
    }
    if (c === "{") { depth++; i++; continue; }
    if (c === "}") {
      depth--;
      while (stack.length && stack[stack.length - 1].depth !== undefined && depth < stack[stack.length - 1].depth) stack.pop();
      i++; continue;
    }
    if (c === "^" || c === "_") { out.scripts.push({ i, ch: c, mode: mode() }); i++; continue; }
    if (c === "$") {
      const dbl = text[i + 1] === "$", tok = dbl ? "$$" : "$", top = stack[stack.length - 1];
      if (top && top.end === tok) stack.pop();
      else if (mode() === "text") stack.push({ mode: "math", end: tok });
      i += tok.length; continue;
    }
    if (c !== "\\") { i++; continue; }
    const d = text[i + 1];
    if (d === "(" || d === "[") { if (mode() === "text") stack.push({ mode: "math", end: d === "(" ? "\\)" : "\\]" }); i += 2; continue; }
    if (d === ")" || d === "]") { const top = stack[stack.length - 1]; if (top && top.end === "\\" + d) stack.pop(); i += 2; continue; }
    const m = /^[A-Za-z@]+\*?/.exec(text.slice(i + 1, i + 60));
    if (!m) { i += 2; continue; }
    const name = m[0].replace(/\*$/, ""), after = i + 1 + m[0].length;
    if (name === "begin" || name === "end") {
      const g = group(after);
      const env = g ? text.slice(g[0] + 1, g[1] - 1).trim() : "";
      if (name === "begin") {
        out.envs.push({ i, name: env, at: g ? g[0] + 1 : after });
        if (VERBATIM.test(env)) { const e = text.indexOf(`\\end{${env}}`, g ? g[1] : after); i = e < 0 ? n : e; continue; }
        if (/^(tikzpicture|pgfpicture|scope|axis|forest|circuitikz)$/.test(env) && mode() !== "math") stack.push({ mode: "free", end: env });
        else if (MATH_ENV_RE.test(`\\begin{${env}}`) || (MATH_CELLS.test(env) && mode() !== "math")) stack.push({ mode: "math", end: env });
        else if (mode() === "math" && /^(tabular|minipage|center)$/.test(env)) stack.push({ mode: "text", end: env });
      } else {
        const top = stack[stack.length - 1];
        if (top && top.end === env) stack.pop();
      }
      i = g ? g[1] : after; continue;
    }
    if (name === "verb" || name === "lstinline") {
      let k = after; if (text[k] === "[") k = opt(k);
      const del = text[k], e = del && del !== "{" ? text.indexOf(del, k + 1) : -1;
      i = e < 0 ? after : e + 1; continue;
    }
    out.cmds.push({ i, name, len: m[0].length + 1, mode: mode() });
    if (name in DEF_GROUPS || /^[gex]?def$/.test(name) || name === "let" || name === "foreach") {
      let k = after;
      const ws = () => { while (k < n && /[ \t\n]/.test(text[k])) k++; };
      const cs = () => { const mm = /^\\(?:[A-Za-z@]+|.)/.exec(text.slice(k, k + 60)); if (mm) { out.defs.add(mm[0].slice(1)); k += mm[0].length; } return !!mm; };
      if (name === "foreach") {                   // \foreach \x/\y in {...}: the loop variables are commands inside
        while (k < n && !text.startsWith(" in", k) && text[k] !== "{" && text[k] !== "\n") { if (text[k] === "\\") cs(); else k++; }
        i = k; continue;
      }
      if (/def$/.test(name)) { ws(); cs(); while (k < n && text[k] !== "{" && text[k] !== "\n") k++; const g = group(k); if (g) k = g[1]; }
      else if (name === "let") { ws(); cs(); ws(); if (text[k] === "=") k++; ws(); if (!cs()) k++; }
      else {
        let got = 0;
        while (got < DEF_GROUPS[name] && k < n) {
          ws();
          if (text[k] === "[") { k = opt(k); continue; }
          if (text[k] === "{") {
            const g = group(k);
            if (!got) { const inner = text.slice(k + 1, g ? g[1] - 1 : k + 1).trim(); if (inner.startsWith("\\")) out.defs.add(inner.slice(1)); else if (DEF_ENV.test(name)) out.defEnvs.add(inner); }
            k = g ? g[1] : k + 1; got++; continue;
          }
          if (text[k] === "\\" && !got) { cs(); got++; continue; }
          break;
        }
      }
      out.skip.push([i, k]);
      i = k; continue;
    }
    if (LITERAL_ARGS.has(name)) {
      let k = opt(after);
      const g = group(k);
      if (g) {
        const arg = text.slice(g[0] + 1, g[1] - 1);
        if (/^(ref|eqref|cref|Cref|autoref|pageref|nameref|vref|labelcref)$/.test(name)) out.refs.push({ i: g[0] + 1, keys: arg });
        if (/^(cite|citep|citet|citeauthor|citeyear|parencite|textcite|autocite|nocite)$/.test(name)) out.cites.push({ i: g[0] + 1, keys: arg });
        k = g[1];
        if (name === "href" || name === "definecolor" || name === "textcolor") { /* the second argument is text: checked */ }
      }
      i = k; continue;
    }
    if (name === "ensuremath") { const g = group(after); if (g) { stack.push({ mode: "math", depth: depth + 1 }); } i = after; continue; }
    if (TEXT_IN_MATH.has(name) && mode() === "math") { const g = group(opt(after)); if (g) { stack.push({ mode: "text", depth: depth + 1 }); i = g[0]; continue; } }
    i = after;
  }
  return out;
}

function lintDeep(text, path) {
  if (/^\s*%%novalidate\b/.test(text) || (typeof PREF !== "undefined" && PREF.codeCheck === false)) return [];
  const out = [];
  const starts = [0];
  for (let k = 0; k < text.length; k++) if (text[k] === "\n") starts.push(k + 1);
  const at = (i) => { let lo = 0, hi = starts.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= i) lo = mid; else hi = mid - 1; } return { line: lo + 1, ch: i - starts[lo] }; };
  const s = scanModes(text), local = localDefs();
  const isDefined = (c) => KNOWN.cmds.has(c) || s.defs.has(c) || local.cmds.has(c);
  const isEnv = (e) => KNOWN.envs.has(e) || KNOWN.envs.has(e.replace(/\*$/, "")) || s.defEnvs.has(e) || local.envs.has(e) || local.envs.has(e.replace(/\*$/, ""));
  const skipped = (i) => s.skip.some(([a, b]) => i >= a && i < b);
  const docAt = text.indexOf("\\begin{document}");
  const body = (i) => docAt < 0 || i > docAt;
  const add = (i, len, level, message) => { const p = at(i); out.push({ line: p.line, ch: p.ch, len, level, message }); };
  for (const c of s.cmds) {
    if (skipped(c.i)) continue;
    if (body(c.i) && c.mode === "text" && MATH_ONLY.has(c.name))
      add(c.i, c.len, "error", `\\${c.name} only works inside a formula: put it between $ and $ (LaTeX would stop with “Missing $ inserted”).`);
    else if (c.mode === "math" && TEXT_ONLY.has(c.name))
      add(c.i, c.len, "error", `\\${c.name} cannot be used inside a formula: close the formula first.`);
    else if (E.cmds && !c.name.includes("@") && !isDefined(c.name)) {
      const pkg = PACKAGE_FOR[c.name];
      if (pkg) { add(c.i, c.len, "error", `\\${c.name} comes from the package ${pkg}, which this paper does not load: add \\usepackage{${pkg.split(" ")[0]}} to main.tex.`); continue; }
      const near = closest(c.name, KNOWN.list);
      if (E.cmds.complete) add(c.i, c.len, "error", `\\${c.name} is not a command LaTeX knows here${near ? `. Did you mean \\${near}?` : " (a typo, or a package that is not loaded)."}`);
      else if (near) add(c.i, c.len, "warning", `Did you mean \\${near}? \\${c.name} may not be a command LaTeX knows here.`);
    }
  }
  for (const x of s.scripts) {
    if (skipped(x.i) || !body(x.i) || x.mode !== "text") continue;
    if (x.i > 0 && text[x.i - 1] === "\\") continue;
    add(x.i, 1, "error", `${x.ch} only works inside a formula: write $x${x.ch}2$, or \\${x.ch === "_" ? "_" : "^{}"} for the sign itself.`);
  }
  if (E.cmds) {
    for (const e of s.envs) {
      if (skipped(e.i) || !e.name || isEnv(e.name)) continue;
      const near = closest(e.name, [...KNOWN.envs]), pkg = ENV_PACKAGE[e.name];
      const where = e.at;
      if (pkg) { add(where, e.name.length, "error", `The environment {${e.name}} comes from the package ${pkg}, which this paper does not load: add \\usepackage{${pkg}} to main.tex.`); continue; }
      if (E.cmds.complete) add(where, e.name.length, "error", `LaTeX does not know the environment {${e.name}}${near ? `. Did you mean {${near}}?` : ": define it (for a theorem-like one, with \\newtheorem in main.tex) or load its package."}`);
      else if (near) add(where, e.name.length, "warning", `Did you mean {${near}}? LaTeX may not know the environment {${e.name}}.`);
    }
  }
  for (const c of (E.cmds && E.cmds.clashes) || []) {      // a name LaTeX or a package already has
    const re = c.kind === "command" ? new RegExp(`\\\\(newcommand|DeclareMathOperator)\\*?\\s*\\{?\\s*\\\\${c.name}(?![A-Za-z])`, "g")
      : new RegExp(`\\\\(newtheorem|newenvironment)\\*?\\s*\\{${c.name}\\}`, "g");
    for (const m of text.matchAll(re)) {
      const lineStart = text.lastIndexOf("\n", m.index) + 1, lineEnd = text.indexOf("\n", m.index);
      const line = text.slice(lineStart, lineEnd < 0 ? text.length : lineEnd);
      if (/\\ifcsname|@ifundefined|\\ifdefined/.test(line) || skipped(m.index) || /(^|[^\\])%/.test(text.slice(lineStart, m.index))) continue;
      add(m.index, m[0].length, "error", `${c.kind === "command" ? `\\${c.name}` : `{${c.name}}`} already exists (in LaTeX or a package this paper loads): LaTeX stops here with “Command \\${c.name} already defined”. Choose another name${c.kind === "command" ? ", or use \\renewcommand" : ", or delete this line if it is not needed"}.`);
    }
  }
  if (E.comp) {
    const labels = new Set((E.comp.labels || []).map((x) => x.label)), keys = new Set((E.comp.cites || []).map((x) => x.key));
    for (const r of s.refs) for (const k of r.keys.split(",")) {
      const key = k.trim(), off = r.keys.indexOf(k) + (k.length - k.trimStart().length);
      if (key && labels.size && !labels.has(key) && !skipped(r.i)) add(r.i + off, key.length, "warning", `There is no \\label{${key}} in the paper: this reference will print as “??”.`);
    }
    for (const r of s.cites) for (const k of r.keys.split(",")) {
      const key = k.trim(), off = r.keys.indexOf(k) + (k.length - k.trimStart().length);
      if (key && keys.size && !keys.has(key) && !skipped(r.i)) add(r.i + off, key.length, "warning", `No reference has the key ${key}: the citation will print as “?”.`);
    }
  }
  return out.slice(0, 300);
}

/* Compile errors in plain words, as Overleaf adds to its logs. */
const HINTS = [
  [/Undefined control sequence/, (x) => {
    const tok = undefinedToken(x);
    if (!tok) return "A command here is not one LaTeX knows: a typo, or a package that is not loaded.";
    const pkg = PACKAGE_FOR[tok], near = closest(tok, KNOWN.list.length ? KNOWN.list : [...POPULAR.keys()]);
    return pkg ? `\\${tok} comes from the package ${pkg}: add \\usepackage{${pkg.split(" ")[0]}} to main.tex.`
      : `LaTeX does not know \\${tok}.${near ? ` Did you mean \\${near}?` : " Check the spelling, or load the package it comes from."}`;
  }],
  [/Missing \$ inserted/, () => "Something that only works in a formula (^, _, \\alpha, \\frac …) is outside $…$, or a formula is not closed."],
  [/Extra \}, or forgotten \$/, () => "A } with no { before it, or a $ that is missing."],
  [/Missing \} inserted/, () => "A { here is never closed."],
  [/Environment (\S+) undefined/, (x, m) => `LaTeX does not know the environment ${m[1]}: a typo, or it needs a package or a \\newtheorem.`],
  [/\\begin\{(\S+)\} on input line \d+ ended by \\end\{(\S+)\}/, (x, m) => `The environments are closed in the wrong order: {${m[1]}} is still open where {${m[2]}} ends.`],
  [/\\begin\{document\} ended by/, () => "An \\end{...} with no \\begin{...} before it."],
  [/File [`'](.+?)' not found/, (x, m) => `LaTeX cannot find the file ${m[1]}: check its name, and that it is uploaded to the project.`],
  [/Missing \\begin\{document\}/, () => "Text before \\begin{document} (often what an error just above left behind), or no \\begin{document} at all."],
  [/Misplaced alignment tab character &/, () => "An & outside a table or an align: write \\& for the sign itself."],
  [/Double (subscript|superscript)/, (x, m) => `Two ${m[1] === "subscript" ? "_" : "^"} in a row: group them, for example x_{i_j}.`],
  [/Paragraph ended before (\S+) was complete/, (x, m) => `A blank line inside the argument of ${m[1]}: a } is probably missing.`],
  [/Missing number, treated as zero/, () => "A number or a length is expected here (for example in \\vspace{} or a width)."],
  [/Illegal unit of measure/, () => "A length needs a unit: 2cm, 10pt, 0.5\\textwidth."],
  [/There's no line here to end/, () => "\\\\ where there is no line to end, for example after a blank line or at the start of a paragraph."],
  [/Can be used only in preamble/, () => "This command belongs before \\begin{document}, in main.tex."],
  [/Option clash for package (\S+)/, (x, m) => `The package ${m[1]} is loaded twice with different options: load it once.`],
  [/Command (\\\S+) already defined/, (x, m) => `${m[1]} already exists: use \\renewcommand to change it, or choose another name.`],
  [/Something's wrong--perhaps a missing \\item/, () => "Text directly inside a list: start each entry with \\item."],
  [/Lonely \\item/, () => "An \\item outside a list: put it inside itemize, enumerate or description."],
  [/Too many \}'s/, () => "A } with no { to close."],
  [/Runaway argument/, () => "The argument of a command is never closed with }."],
  [/Display math should end with \$\$/, () => "A display formula is not closed the way it was opened."],
  [/Missing \\right\. inserted|Extra \\right/, () => "\\left and \\right must come in pairs inside one formula."],
  [/Illegal parameter number in definition/, () => "A # in a definition that has fewer arguments, or a # in the text: write \\# for the sign itself."],
  [/Unicode character (.+?) not set up/, () => "A character LaTeX cannot typeset with this setup: replace it with its LaTeX command, or use XeLaTeX or LuaLaTeX."],
  [/Citation [`'](.+?)' .*undefined/, (x, m) => `No reference has the key ${m[1]}.`],
  [/Reference [`'](.+?)' .*undefined/, (x, m) => `There is no \\label{${m[1]}}: the reference prints as “??”.`],
  [/Emergency stop/, () => "LaTeX stopped: the first error above is the one to fix."],
];
function undefinedToken(x) {
  const first = String(x.context || "").split("\n", 1)[0];
  const m = first.match(/\\([A-Za-z@]+)\s*$/);
  return m ? m[1] : null;
}
function errorHint(x) {
  const msg = String(x.message || "");
  for (const [re, f] of HINTS) { const m = msg.match(re); if (m) return f(x, m); }
  return "";
}
