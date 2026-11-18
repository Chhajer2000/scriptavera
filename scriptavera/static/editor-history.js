/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* The history, the way Overleaf shows it. The versions are listed on the right, newest first, grouped by day, each
   with the files it changed and its labels. Choosing one shows what changed in it; "Compare from" and "Compare up to"
   choose any two. The files of that range are on the left, marked added, edited or deleted, and the chosen file is in
   the middle with the changes in place: added text in green, removed text struck through in red. From here a file or
   the whole project goes back to a version, a version gets labels, and any version downloads as a .zip.
   Uses the editor's own helpers (api, esc, $, menu, modal, toast, saveAll, refreshTree, reloadFile, openFile). */

const H = { data: null, from: null, to: null, path: null, filter: "all", cm: null, files: [], marks: [], at: -1, busy: 0 };
const HK = { auto: ["You", ""], manual: ["You", "manual"], ai: ["AI", "ai"], restore: ["Restored", "restore"], start: ["Created", "start"],
  named: ["Saved", "manual"] };

function hTime(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function hDay(t) {
  const d = new Date(t * 1000), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}
function hCounts(f, words) {
  const parts = [f.add ? `<i class="p">+${f.add}</i>` : "", f.del ? `<i class="m">−${f.del}</i>` : ""].filter(Boolean);
  if (!parts.length) return "";
  return `<span class="h-n" title="${f.add} line${f.add === 1 ? "" : "s"} added, ${f.del} removed">${parts.join(" ")}${words ? ` line${f.add + f.del === 1 ? "" : "s"}` : ""}</span>`;
}
const hOrder = (ref) => (ref === "now" ? Infinity : Number(ref) || 0);
function hVersion(id) { return (H.data.versions || []).find((v) => v.id === Number(id)); }
function hBefore(id) {                 // the version just before this one (0: before the first)
  const older = (H.data.versions || []).filter((v) => v.id < Number(id));
  return older.length ? older[0].id : 0;
}
function hName(ref) {
  if (ref === "now") return "now";
  if (!ref) return "the start";
  const v = hVersion(ref);
  if (!v) return `version ${ref}`;
  const d = hDay(v.at);
  return `${d === "Today" || d === "Yesterday" ? d.toLowerCase() : d} at ${hTime(v.at)}`;
}

async function histOpen() {
  try { await saveAll(); } catch (e) { /* saving is retried by the editor itself */ }
  const el = $("#hist");
  el.style.top = `${$("#top").offsetHeight}px`;
  el.hidden = false;
  document.body.classList.add("in-history");
  if (!H.cm) {
    H.cm = CodeMirror($("#histDoc"), { value: "", mode: "latex", readOnly: true, lineNumbers: true, lineWrapping: true,
      cursorBlinkRate: -1, gutters: ["h-gut", "CodeMirror-linenumbers"] });
  }
  await histLoad();
  if (!H.data) return;
  if (H.data.pending.length) histPick("now");
  else if (H.data.versions.length) histPick(H.data.versions[0].id);
}
function histClose() {
  $("#hist").hidden = true;
  document.body.classList.remove("in-history");
  closeMenu();
  if (cm) { cm.refresh(); cm.focus(); }
}
async function histLoad() {
  try { H.data = await api(`${API}/history`); }
  catch (e) { toast(e.message, true); H.data = { versions: [], pending: [] }; }
  drawHistList();
}

/* choosing: one version shows its own changes; "now" shows what changed since the newest version */
function histPick(ref) {
  if (ref === "now") { H.from = H.data.versions.length ? H.data.versions[0].id : 0; H.to = "now"; }
  else if (H.filter === "labels") {
    const labelled = H.data.versions.filter((v) => v.labels.length && v.id < Number(ref));
    H.from = labelled.length ? labelled[0].id : 0; H.to = Number(ref);
  } else { H.from = hBefore(ref); H.to = Number(ref); }
  histShow();
}
function histCompareFrom(id) {
  H.from = Number(id);
  if (hOrder(H.to) <= H.from) H.to = "now";
  histShow();
}
function histCompareTo(id) {
  H.to = Number(id);
  if (hOrder(H.from) >= H.to) H.from = hBefore(id);
  histShow();
}

async function histShow() {
  drawHistList();
  const n = ++H.busy;
  $("#histRange").textContent = H.to === "now"
    ? (H.from ? `Changes since ${hName(H.from)}, up to now` : "Everything up to now")
    : H.from === hBefore(H.to) ? `Changes saved ${hName(H.to)}` : `Changes from ${hName(H.from)} to ${hName(H.to)}`;
  let r;
  try { r = await api(`${API}/history`, { method: "POST", body: { action: "compare", from: H.from, to: H.to } }); }
  catch (e) { return toast(e.message, true); }
  if (n !== H.busy) return;
  H.files = r.files;
  const changed = H.files.filter((f) => f.status !== "same");
  if (!H.path || !H.files.some((f) => f.path === H.path) || (changed.length && !changed.some((f) => f.path === H.path)))
    H.path = (changed.find((f) => /\.tex$/.test(f.path)) || changed[0] || H.files.find((f) => f.path === "main.tex") || H.files[0] || {}).path || null;
  drawHistFiles();
  histFile(H.path);
}

function drawHistFiles() {
  const box = $("#histFiles");
  const changed = H.files.filter((f) => f.status !== "same").length;
  const rows = [], seen = new Set();
  for (const f of H.files) {
    const parts = f.path.split("/");
    for (let i = 1; i < parts.length; i++) {
      const dir = parts.slice(0, i).join("/");
      if (!seen.has(dir)) { seen.add(dir); rows.push(`<div class="h-dir" style="padding-left:${8 + (i - 1) * 14}px">${ICON.folder}<span>${esc(parts[i - 1])}</span></div>`); }
    }
    const what = { added: "added", deleted: "deleted", edited: "edited", same: "" }[f.status];
    const counts = f.kind === "text" && f.status !== "same" ? hCounts(f) : "";
    rows.push(`<button class="h-file ${f.status} ${f.path === H.path ? "on" : ""}" data-act="h-file" data-path="${esc(f.path)}" style="padding-left:${8 + (parts.length - 1) * 14}px"
      title="${esc(f.path)}${what ? " — " + what : ""}"><span class="h-st">${{ added: "+", deleted: "−", edited: "•", same: "" }[f.status]}</span>
      <span class="nm">${esc(parts[parts.length - 1])}</span>${counts}</button>`);
  }
  box.innerHTML = `<div class="h-files-head">${changed ? `${changed} file${changed === 1 ? "" : "s"} changed` : "No file changed"}</div>${rows.join("") || `<div class="h-none">No files.</div>`}`;
}

async function histFile(path) {
  H.path = path;
  $$("#histFiles .h-file").forEach((b) => b.classList.toggle("on", b.dataset.path === path));
  const head = $("#histDocHead"), empty = $("#histEmpty");
  if (!path) { head.innerHTML = ""; empty.hidden = false; empty.textContent = "Nothing to show."; $("#histDoc").hidden = true; return; }
  const n = ++H.busy;
  let r;
  try { r = await api(`${API}/history`, { method: "POST", body: { action: "compare", from: H.from, to: H.to, path } }); }
  catch (e) { return toast(e.message, true); }
  if (n !== H.busy) return;
  const canRestore = H.to !== "now" || r.status === "deleted";
  const fromOf = r.status === "deleted" ? H.from : H.to;
  const say = { added: "added in this range", deleted: "deleted in this range", edited: "edited in this range", same: "not changed in this range" }[r.status];
  head.innerHTML = `<span class="h-path">${esc(path)}</span><span class="h-say ${r.status}">${say}</span>
    ${r.kind === "text" && r.status !== "same" ? hCounts(r, true) : ""}
    <span class="sp"></span>
    ${r.kind === "text" && r.status !== "same" ? `<button class="btn small" data-act="h-prev" title="Previous change">↑ Previous</button><button class="btn small" data-act="h-next" title="Next change">↓ Next change</button>` : ""}
    ${canRestore && fromOf ? `<button class="btn small primary" data-act="h-restorefile" data-id="${fromOf}" data-path="${esc(path)}">Restore this file</button>` : ""}`;
  if (r.kind !== "text") {
    $("#histDoc").hidden = true; empty.hidden = false;
    empty.textContent = `${path} is an image or another file that is not text: it was ${r.status === "same" ? "not changed" : r.status} here. ` +
      (canRestore && fromOf ? "Restore it to get this version back in the project." : "");
    return;
  }
  empty.hidden = true; $("#histDoc").hidden = false;
  let text = "";
  const marks = [];
  for (const [k, s] of r.segments) { const a = text.length; text += s; if (k !== "=") marks.push([k, a, text.length]); }
  const c = H.cm;
  c.setValue(text);
  H.marks = [];
  c.operation(() => {
    for (const [k, a, b] of marks) {
      const from = c.posFromIndex(a), to = c.posFromIndex(b);
      c.markText(from, to, { className: k === "+" ? "h-add" : "h-del" });
      const last = to.ch === 0 && to.line > from.line ? to.line - 1 : to.line;
      for (let l = from.line; l <= last; l++) c.addLineClass(l, "gutter", k === "+" ? "h-g-add" : "h-g-del");
      const prev = H.marks[H.marks.length - 1];
      if (prev && prev.end >= a - 1 && prev.line >= from.line - 1) prev.end = b;      // a word replaced: one change
      else H.marks.push({ start: a, end: b, line: from.line });
    }
  });
  H.at = -1;
  c.refresh();
  if (H.marks.length) histStep(1); else c.scrollTo(0, 0);
}
function histStep(d) {
  if (!H.marks.length) return;
  H.at = (H.at + d + H.marks.length) % H.marks.length;
  const m = H.marks[H.at], c = H.cm, pos = c.posFromIndex(m.start);
  c.scrollIntoView({ from: pos, to: c.posFromIndex(m.end) }, Math.round(c.getScrollInfo().clientHeight / 3));
  c.setCursor(pos);
}

function drawHistList() {
  const box = $("#histList");
  if (!H.data) { box.innerHTML = ""; return; }
  $$("#hist .h-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.f === H.filter));
  const vs = H.filter === "labels" ? H.data.versions.filter((v) => v.labels.length) : H.data.versions;
  const lo = hOrder(H.from), hi = hOrder(H.to);
  const out = [];
  if (H.filter === "all") {
    const p = H.data.pending;
    out.push(`<div class="h-ver now ${H.to === "now" ? "to in" : ""}" data-act="h-pick" data-id="now" tabindex="0">
      <div class="h-top"><b>Current state</b><span class="sp"></span><button class="h-more" data-act="h-menu" data-id="now" aria-label="More">⋯</button></div>
      <div class="h-what">${p.length ? `Changed since the newest version: ${p.slice(0, 3).map((c) => esc(c.path.split("/").pop())).join(", ")}${p.length > 3 ? ` and ${p.length - 3} more` : ""}` : "The same as the newest version below."}</div></div>`);
  }
  let day = "";
  for (const v of vs) {
    const d = hDay(v.at);
    if (d !== day) { day = d; out.push(`<div class="h-day">${esc(d)}</div>`); }
    const inside = v.id > lo && v.id <= hi, k = HK[v.kind] || [v.kind, ""];
    const changes = v.changes.length
      ? v.changes.slice(0, 3).map((c) => `<span class="h-ch ${c.status}">${{ added: "Created", deleted: "Deleted", edited: "Edited" }[c.status]} ${esc(c.path)}</span>`).join("") +
        (v.changes.length > 3 ? `<span class="h-ch">and ${v.changes.length - 3} more file${v.changes.length - 3 === 1 ? "" : "s"}</span>` : "")
      : `<span class="h-ch">No file changed</span>`;
    out.push(`<div class="h-ver ${inside ? "in" : ""} ${v.id === hi ? "to" : ""} ${v.id === lo ? "from" : ""}" data-act="h-pick" data-id="${v.id}" tabindex="0">
      <div class="h-top"><span class="h-time">${hTime(v.at)}</span><span class="kind ${k[1]}">${esc(k[0])}</span><span class="sp"></span>
        <button class="h-more" data-act="h-menu" data-id="${v.id}" aria-label="More for this version">⋯</button></div>
      ${v.labels.map((l) => `<span class="h-label">${esc(l)}<button data-act="h-unlabel" data-id="${v.id}" data-label="${esc(l)}" aria-label="Remove the label ${esc(l)}" title="Remove this label">×</button></span>`).join("")}
      ${v.kind !== "auto" && !v.labels.includes(v.label) ? `<div class="h-what">${esc(v.label)}</div>` : ""}
      <div class="h-chs">${changes}</div></div>`);
  }
  if (!vs.length) out.push(`<div class="h-none">${H.filter === "labels" ? "No labelled version yet. Open a version's ⋯ menu and choose “Label this version”." : "No version yet."}</div>`);
  box.innerHTML = out.join("");
}

function histMenu(anchor, id) {
  const now = id === "now";
  menu(anchor, `${now ? "" : `<button data-act="h-label" data-id="${id}">Label this version</button>`}
    ${now ? `<button data-act="h-label" data-id="now">Label the current state</button>` : ""}
    ${now ? "" : `<button data-act="h-cfrom" data-id="${id}">Compare from this version<span class="sub">Show what changed after it</span></button>
    <button data-act="h-cto" data-id="${id}">Compare up to this version<span class="sub">Show what changed until it</span></button><hr>
    <a href="${API}/history/zip?id=${id}" data-act="none" download>Download this version<span class="sub">The project's files as they were then, as a .zip</span></a>
    <button data-act="h-restoreall" data-id="${id}">Restore the project to this version<span class="sub">The files as they are now stay in the history</span></button>`}`);
}
function histLabelDialog(id) {
  closeMenu();
  modal(`<header><h2>Label this version</h2><button class="btn quiet" data-act="close">Close</button></header><div class="body">
    <p>A label marks a version you may want to come back to, for example “sent to my co-author” or “submitted”. Labelled versions are always kept.</p>
    <form class="field" data-act="h-labelsave" data-id="${id}"><input id="hLabel" maxlength="120" placeholder="For example: submitted to the journal" required>
    <button class="btn primary" type="submit">Add label</button></form></div>`);
}
async function histLabelSave(id, text) {
  try {
    await api(`${API}/history`, { method: "POST", body: { action: "label", id, label: text } });
    closeModal(); toast("Label added.");
    await histLoad();
    if (id === "now") histPick(H.data.versions[0].id); else histShow();
  } catch (e) { toast(e.message, true); }
}
async function histUnlabel(id, text) {
  try { await api(`${API}/history`, { method: "POST", body: { action: "unlabel", id, label: text } }); await histLoad(); }
  catch (e) { toast(e.message, true); }
}
async function histRestoreFile(id, path) {
  const v = hVersion(id);
  if (!confirm(`Restore ${path} as it was ${v ? hName(id) : "then"}? The file as it is now stays in the history.`)) return;
  try {
    await saveAll();
    await api(`${API}/history`, { method: "POST", body: { action: "restore_file", id: Number(id), path } });
    await refreshTree();
    if (E.docs.has(path)) await reloadFile(path);
    histClose();
    await openFile(path);
    toast(`${path} is back as it was ${v ? hName(id) : "then"}.`);
    compile();
  } catch (e) { toast(e.message, true); }
}
async function histRestoreAll(id) {
  closeMenu();
  if (!confirm(`Restore the whole project as it was ${hName(id)}? The files as they are now stay in the history, so you can come back to them.`)) return;
  try {
    await saveAll();
    await api(`${API}/history`, { method: "POST", body: { action: "restore", id: Number(id) } });
    await refreshTree();
    for (const p of [...E.docs.keys()]) {
      if (E.tree.some((x) => x.path === p)) await reloadFile(p);
      else { E.docs.delete(p); E.openTabs = E.openTabs.filter((x) => x !== p); }
    }
    histClose();
    if (!E.tree.some((x) => x.path === E.current)) openFile("main.tex"); else drawTabs();
    toast(`The project is back as it was ${hName(id)}.`);
    compile();
  } catch (e) { toast(e.message, true); }
}

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-act]");
  if (!t || t.tagName === "FORM" || !t.dataset.act.startsWith("h-")) return;     // a form's button submits it
  const a = t.dataset.act, id = t.dataset.id === "now" ? "now" : t.dataset.id;
  ev.preventDefault();
  switch (a) {
    case "h-close": histClose(); break;
    case "h-filter": H.filter = t.dataset.f; drawHistList(); break;
    case "h-pick": if (!ev.target.closest(".h-more, .h-label button")) histPick(id); break;
    case "h-menu": ev.stopPropagation(); histMenu(t, id); break;
    case "h-file": histFile(t.dataset.path); break;
    case "h-prev": histStep(-1); break;
    case "h-next": histStep(1); break;
    case "h-label": histLabelDialog(id); break;
    case "h-unlabel": ev.stopPropagation(); histUnlabel(Number(id), t.dataset.label); break;
    case "h-cfrom": closeMenu(); histCompareFrom(id); break;
    case "h-cto": closeMenu(); histCompareTo(id); break;
    case "h-restorefile": histRestoreFile(id, t.dataset.path); break;
    case "h-restoreall": histRestoreAll(id); break;
    default: break;
  }
});
document.addEventListener("submit", (ev) => {
  const f = ev.target;
  if (f.dataset.act !== "h-labelsave") return;
  ev.preventDefault();
  const text = $("#hLabel").value.trim();
  if (text) histLabelSave(f.dataset.id === "now" ? "now" : Number(f.dataset.id), text);
});
document.addEventListener("keydown", (ev) => {       // before the editor's own keys: Escape closes a menu first
  if ($("#hist").hidden) return;
  if (ev.key === "Escape" && $("#modal").hidden && $("#menu").hidden) { ev.preventDefault(); histClose(); }
  if ((ev.key === "Enter" || ev.key === " ") && ev.target.classList && ev.target.classList.contains("h-ver")) { ev.preventDefault(); ev.target.click(); }
}, true);
window.addEventListener("resize", () => { if (!$("#hist").hidden) $("#hist").style.top = `${$("#top").offsetHeight}px`; });
