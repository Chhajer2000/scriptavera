/* SPDX-License-Identifier: AGPL-3.0-only
   Copyright (C) 2026 Vaibhav Chhajer */
"use strict";
/* Scriptavera on scriptavera.com: Google sign-in, the free papers, Google Drive, collaborators.
   Loaded before app.js on every page. On a computer (the desktop app) HOSTED.on stays false and every function
   below returns nothing, so the app is exactly as before. Uses app.js's S, api, esc, toast, showDialog, render. */

const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const DRIVE_FOLDER = "Scriptavera Research Papers";
const FREE_KEY = "scriptavera-guest-papers";            // the free papers used in this browser, shown at once
const KEYS_KEY = "scriptavera-ai-keys";                 // this person's AI keys: kept in this browser, nowhere else
const KEYS_WHERE = "scriptavera-ai-keys-where";         // "local": stay after the browser closes; "session": this tab only

const HOSTED = {
  on: false, acct: null, gis: null, token: null, tokenExp: 0, collabs: [], timer: null, busy: "", keyInfo: null,

  async init() {
    if (!(S.meta && S.meta.hosted && S.meta.hosted.public)) return;
    this.on = true;
    Object.assign(ACT, this.acts);
    if (!document.getElementById("sv-collabs")) {
      const dl = document.createElement("datalist"); dl.id = "sv-collabs"; document.body.appendChild(dl);
    }
    await this.refresh();
    this.loadGis();
    if (Object.keys(this.myKeys()).length) {        // the server keeps no keys: the browser hands them over again
      this.sendKeys().then(async () => { S.meta = await api("/api/meta"); render(); }).catch(() => {});
    }
  },

  /* ---------------------------------------------------------- the person's own AI keys (never stored by the server) */
  keysStore() {
    let where = "local";
    try { where = localStorage.getItem(KEYS_WHERE) || "local"; } catch (e) { /* storage unavailable */ }
    try { return where === "session" ? sessionStorage : localStorage; } catch (e) { return null; }
  },
  myKeys() {
    const st = this.keysStore();
    try { const k = JSON.parse((st && st.getItem(KEYS_KEY)) || "{}"); return k && typeof k === "object" ? k : {}; } catch (e) { return {}; }
  },
  saveMyKeys(keys) {
    for (const st of [(() => { try { return localStorage; } catch (e) { return null; } })(), (() => { try { return sessionStorage; } catch (e) { return null; } })()]) {
      try { if (st) st.removeItem(KEYS_KEY); } catch (e) { /* storage unavailable */ }
    }
    const st = this.keysStore();
    try { if (st && Object.keys(keys).length) st.setItem(KEYS_KEY, JSON.stringify(keys)); } catch (e) { /* storage unavailable */ }
  },
  async sendKeys() {
    this.keyInfo = await api("/api/keys", { method: "POST", body: { keys: this.myKeys() } });
    return this.keyInfo;
  },
  keyChip(k) {
    if (!k.ok) return `<span class="chip red" title="${esc(k.message || "")}">${esc(k.message || "not working")}</span>`;
    const tier = k.tier === "paid" ? `<span class="chip green">paid plan</span>` : k.tier === "free" ? `<span class="chip red">free plan</span>` : `<span class="chip">plan not known</span>`;
    return `${tier} <span class="chip">${esc(String(k.count))} models</span>`;
  },
  keysDialog() {
    const info = this.keyInfo || { providers: [] }, mine = this.myKeys();
    let where = "local"; try { where = localStorage.getItem(KEYS_WHERE) || "local"; } catch (e) { /* storage unavailable */ }
    const prov = info.providers.map((p) => {
      const list = (mine[p.id] || []).map((key, i) => {
        const k = (p.keys || []).find((x) => x.n === i + 1) || {};
        return `<div class="sv-key"><span class="sv-key-name">Key ${i + 1} <span class="muted">${esc(key.slice(0, 4) + "…" + key.slice(-4))}</span></span>
          <span>${k.n ? this.keyChip(k) : `<span class="chip">checking…</span>`}</span>
          <button class="btn small quiet" data-act="svkeydel" data-p="${esc(p.id)}" data-i="${i}">Remove</button>
          ${k.privacy ? `<div class="small ${k.leak ? "sv-leak" : "muted"}">${esc(k.privacy)}</div>` : ""}</div>`;
      }).join("");
      return `<div class="sv-prov"><div class="sv-prov-h"><b>${esc(p.label)}</b><span class="small muted">${esc(p.why)}</span></div>
        ${list}
        <div class="row" style="margin-top:6px"><input type="password" class="grow" id="svkey-${esc(p.id)}" placeholder="Paste a ${esc(p.label)} key${(mine[p.id] || []).length ? " (another one)" : ""}" autocomplete="off" spellcheck="false">
          <button class="btn small" data-act="svkeyadd" data-p="${esc(p.id)}" ${this.busy === "keys" ? "disabled" : ""}>${this.busy === "keys" ? "Checking…" : "Add"}</button></div>
        <div class="sv-steps"><b>How to get a ${esc(p.label)} key</b><ol>${p.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
          <a href="${esc(p.link)}" target="_blank" rel="noopener">Open ${esc(p.label)} to create a key ↗</a></div></div>`;
    }).join("");
    showDialog(`<div class="dialog wide" role="dialog" aria-modal="true" aria-label="Your AI keys"><div class="dlg-h"><h2>Your AI keys</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
      <div class="dlg-b">
      <div class="flag blue" style="margin-bottom:12px"><b>Where your keys are kept.</b> Only in this browser. Scriptavera never saves them: not in a file, not in its database, not in a backup.
        While you use Scriptavera, your keys are held in the server's working memory, used only for your own papers, and sent only to the provider they belong to.
        The server forgets them when you sign out, when you remove them here, or when it restarts; your browser hands them over again the next time you open Scriptavera.
        If a paper is working in the background when the server restarts, it waits until you open Scriptavera again.</div>
      ${info.leak ? `<div class="flag red" style="margin-bottom:12px"><b>Data protection:</b> at least one of your keys is on a free plan (or a plan that may train on what you send). Data can leak from free-tier keys: what you send may be used to train the provider's models or be read by people. Use a paid-tier key to keep unpublished notes private.</div>` : ""}
      <p class="small muted">You can add several keys for each provider: when one reaches its limit, the next is used. Scriptavera checks each key and tells you whether it is on a free or a paid plan.</p>
      ${prov}
      <label class="check" style="margin-top:12px"><input type="checkbox" id="svkeyswhere" ${where === "session" ? "" : "checked"}> Keep my keys in this browser after I close it (untick on a shared computer: they are then forgotten when the tab closes)</label>
      <div class="row" style="margin-top:12px"><button class="btn small danger" data-act="svkeysforget">Forget all my keys</button></div></div></div>`);
  },
  async refresh() {
    try { this.acct = await api("/api/account"); } catch (e) { this.acct = null; return; }
    if (!this.acct.signed_in) {                       // the browser's count, kept in step with the server's
      const used = Math.max(this.localUsed(), this.acct.guest_used || 0);
      this.setLocalUsed(used);
    }
  },
  localUsed() { try { return Math.max(0, parseInt(localStorage.getItem(FREE_KEY) || "0", 10) || 0); } catch (e) { return 0; } },
  setLocalUsed(n) { try { localStorage.setItem(FREE_KEY, String(n)); } catch (e) { /* storage unavailable */ } },
  signedIn() { return !!(this.acct && this.acct.signed_in && this.acct.user); },
  admin() { return !this.on || !!(this.acct && this.acct.admin); },
  remaining() {
    const A = this.acct || {}, limit = A.free_limit ?? 3;
    if (this.signedIn()) return A.subscribed ? Infinity : Math.max(0, limit - (A.user_free_used || 0));
    return Math.max(0, Math.min(limit - Math.max(this.localUsed(), A.guest_used || 0), (A.network_limit || 25) - (A.network_used || 0)));
  },
  counted() {                                         // a paper was started: the count moves at once
    if (!this.on) return;
    if (!this.signedIn()) this.setLocalUsed(this.localUsed() + 1);
    this.refresh().then(() => render());
  },

  keysWhere(local) {
    const keys = this.myKeys();
    try { localStorage.setItem(KEYS_WHERE, local ? "local" : "session"); } catch (e) { /* storage unavailable */ }
    this.saveMyKeys(keys);
  },

  /* ---------------------------------------------------------- Google Identity Services */
  loadGis() {
    if (!this.acct || !this.acct.google_client_id || document.getElementById("gis-script")) return;
    const s = document.createElement("script");
    s.id = "gis-script"; s.src = "https://accounts.google.com/gsi/client"; s.async = true; s.defer = true;
    s.onload = () => {
      this.gis = window.google && window.google.accounts;
      if (!this.gis) return;
      this.gis.id.initialize({ client_id: this.acct.google_client_id, callback: (r) => this.signedInWith(r.credential),
        ux_mode: "popup", auto_select: false, cancel_on_tap_outside: true, context: "signin" });
      this.paintButtons();
    };
    document.head.appendChild(s);
  },
  paintButtons() {
    if (!this.gis) return;
    document.querySelectorAll(".sv-gbtn:not([data-done])").forEach((el) => {
      el.dataset.done = "1";
      this.gis.id.renderButton(el, { theme: "outline", size: "large", text: "signin_with", shape: "pill", logo_alignment: "left" });
    });
  },
  async signedInWith(credential) {
    try {
      const r = await api("/api/auth/google", { method: "POST", body: { credential } });
      this.acct = r; this.token = null;
      closeDialog();
      toast(r.adopted ? `Signed in. ${r.adopted === 1 ? "The paper" : "The " + r.adopted + " papers"} you started as a guest ${r.adopted === 1 ? "is" : "are"} now in your account.` : "Signed in with Google.");
      S.meta = await api("/api/meta"); await loadProjects(); render();
    } catch (e) { toast(e.message, true); }
  },

  /* ---------------------------------------------------------- Google Drive (drive.file only) */
  driveToken(prompt) {
    return new Promise((resolve, reject) => {
      if (this.token && Date.now() < this.tokenExp - 60000) return resolve(this.token);
      if (!this.gis || !this.gis.oauth2) return reject(new Error("Google sign-in is still loading. Try again in a moment."));
      const client = this.gis.oauth2.initTokenClient({ client_id: this.acct.google_client_id, scope: DRIVE_SCOPE,
        hint: (this.acct.user || {}).email || "",
        callback: (r) => {
          if (r.error || !r.access_token) return reject(new Error("Google Drive was not connected."));
          if (this.gis.oauth2.hasGrantedAllScopes && !this.gis.oauth2.hasGrantedAllScopes(r, DRIVE_SCOPE)) return reject(new Error("Tick the box that lets Scriptavera save its files in your Drive."));
          this.token = r.access_token; this.tokenExp = Date.now() + (Number(r.expires_in) || 3600) * 1000; resolve(this.token);
        },
        error_callback: () => reject(new Error("The Google window was closed before Drive was connected.")) });
      /* After the first time, Google answers without asking again (an empty prompt): the window opens and closes by itself. */
      client.requestAccessToken({ prompt: prompt ?? ((this.acct.user || {}).drive_connected ? "" : "consent") });
    });
  },
  async driveConnect() {
    const token = await this.driveToken("consent");
    this.acct = await api("/api/account/drive", { method: "POST", body: { access_token: token } });
    return this.acct;
  },
  async gapi(url, opts = {}) {
    const token = await this.driveToken();
    const r = await fetch(url, { ...opts, headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) } });
    if (!r.ok) throw new Error(`Google Drive answered ${r.status}. Try again.`);
    return r;
  },
  async driveFolder() {
    const q = encodeURIComponent(`name='${DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false`);
    const found = await (await this.gapi(`https://www.googleapis.com/drive/v3/files?q=${q}&spaces=drive&fields=files(id)`)).json();
    if (found.files && found.files.length) return found.files[0].id;
    const made = await (await this.gapi("https://www.googleapis.com/drive/v3/files?fields=id", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: DRIVE_FOLDER, mimeType: "application/vnd.google-apps.folder" }) })).json();
    return made.id;
  },
  async driveSave(pid) {
    await this.driveToken();
    if (!(this.acct.user || {}).drive_connected) await api("/api/account/drive", { method: "POST", body: { access_token: this.token } }).then((a) => { this.acct = a; });
    const folder = await this.driveFolder();
    const res = await fetch(`/api/projects/${encodeURIComponent(pid)}/archive`);
    if (!res.ok) throw new Error("The archive could not be prepared.");
    const blob = await res.blob();
    const m = (S.b && S.b.state && S.b.state.meta) || {};
    const title = ((S.b && S.b.paper && S.b.paper.title) || m.title || m.name || "Paper").replace(/[\\/:*?"<>|$]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 90);
    const meta = { name: `${title} (Scriptavera archive ${new Date().toISOString().slice(0, 10)}).zip`, parents: [folder], mimeType: "application/zip" };
    const start = await this.gapi("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,webViewLink", { method: "POST",
      headers: { "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "application/zip", "X-Upload-Content-Length": String(blob.size) },
      body: JSON.stringify(meta) });
    const where = start.headers.get("Location");
    let file;
    if (where) file = await (await fetch(where, { method: "PUT", headers: { "Content-Type": "application/zip" }, body: blob })).json();
    else {                                          // the upload address was not readable: one request with both parts
      const fd = new FormData();
      fd.append("metadata", new Blob([JSON.stringify(meta)], { type: "application/json" }));
      fd.append("file", blob);
      file = await (await this.gapi("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink", { method: "POST", body: fd })).json();
    }
    if (!file || !file.id) throw new Error("Google Drive did not keep the file. Try again.");
    await api(`/api/projects/${encodeURIComponent(pid)}/drive`, { method: "POST", body: { file_id: file.id } });
    return file;
  },

  /* ---------------------------------------------------------- what the pages show */
  bar() {
    if (!this.on) return "";
    const left = this.remaining();
    const chip = !this.acct ? "" : this.signedIn() && this.acct.subscribed ? `<span class="chip green" title="Your subscription is active">Subscribed</span>`
      : `<span class="chip ${left ? "" : "red"}" title="Free papers left${this.signedIn() ? " in your account" : " for this network, as a guest"}">${left} of ${this.acct.free_limit} free papers left</span>`;
    const who = this.signedIn()
      ? `<button class="hb-btn sv-me" data-act="svaccount" title="Your account">${this.acct.user.picture_url ? `<img src="${esc(this.acct.user.picture_url)}" alt="" referrerpolicy="no-referrer">` : ""}<span class="hb-label">${esc(this.acct.user.given_name || this.acct.user.full_name || this.acct.user.email)}</span></button>`
      : `<button class="hb-btn primary-ish" data-act="svsignin">Sign in with Google</button>`;
    const keys = `<button class="hb-btn" data-act="svkeys" title="Your own AI keys, kept in this browser">AI keys<i class="key-dot ${S.meta && S.meta.has_vision ? "ok" : ""}"></i></button>`;
    return chip + keys + who;
  },
  noKeyBanner() {
    if (!this.on) return "";
    return `<div class="banner"><div class="grow"><b>Add your own AI key to start.</b> Scriptavera reads your notes with your own key from Google Gemini (or another provider):
      a free key is enough to try it, a paid one keeps unpublished notes private. Your keys stay in your browser; Scriptavera never stores them.</div>
      <button class="btn primary" data-act="svkeys">Add a key</button></div>`;
  },
  guestNotice(compact) {
    if (!this.on || this.signedIn()) return "";
    return `<div class="banner sv-guest"><div class="grow"><b>You are using Scriptavera as a guest.</b> Your papers are kept only in this server's memory:
      you can lose your data and progress at any time (for example when the server restarts), and a guest's papers are deleted after three days.
      Sign in with Google to keep your progress.${compact ? "" : ` <br><b>Guests get no cryptographic timestamps.</b> To have your unpublished notes signed, timestamped by an independent RFC 3161 authority and preserved,
      register with Google and connect your Google Drive: only then is a signature made.`}</div><span class="sv-gbtn"></span>${this.gis ? "" : `<button class="btn" data-act="svsignin">Sign in</button>`}</div>`;
  },
  formNotices(f) {
    if (!this.on) return "";
    let h = this.guestNotice(false);
    if (this.signedIn() && !this.acct.user.drive_connected) {
      h += `<div class="banner blue"><div class="grow"><b>Connect Google Drive before you upload</b> to have these notes signed and timestamped (RFC 3161) as evidence of when they existed.
        Without it, no signature is made. Scriptavera can then see only the files it saves in your Drive, nothing else.</div>
        <button class="btn" data-act="svdrive">Connect Google Drive</button></div>`;
    }
    const left = this.remaining();
    if (this.acct && left !== Infinity && left <= 0) {
      const guestMsg = (this.acct.network_used || 0) >= (this.acct.network_limit || 25) ? this.acct.guest_limit_message : this.acct.browser_limit_message;
      h += `<div class="banner red"><div class="grow">${this.signedIn() ? "You have used your free papers. A small monthly subscription lets you continue." : esc(guestMsg)}</div>
        ${this.signedIn() ? `<button class="btn primary" data-act="svsubscribe">Subscribe${this.acct.price_label ? " (" + esc(this.acct.price_label) + ")" : ""}</button>` : `<button class="btn primary" data-act="svsignin">Sign in with Google</button>`}</div>`;
    }
    return h;
  },
  optIn(f) {
    if (!this.on || !this.acct) return "";
    return `<div class="sec"><h2>Helping Scriptavera</h2>
      <label class="check"><input type="checkbox" data-optin ${f.optIn ? "checked" : ""}> <span><b>${esc(this.acct.dataset_label)}</b>
      <ul class="small muted sv-consent">${(this.acct.dataset_details || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></span></label></div>`;
  },
  recordLine() {
    if (!this.on) return null;
    if (!this.signedIn()) return "As a guest, no signed record and no timestamp are made for your notes. Sign in with Google and connect Google Drive to have them signed and timestamped.";
    if (!this.acct.user.drive_connected) return "No signed record is made until you connect Google Drive. With it, your files' fingerprints are signed and timestamped by independent services the moment you upload; only the fingerprints are sent, never the notes.";
    return "Before anything else is done with them, your files' fingerprints are signed and timestamped by independent services (RFC 3161, and OpenTimestamps). Only the fingerprints are sent for that, never the notes. A copy of the receipts is kept in Scriptavera's audit ledger.";
  },
  lockedActions() {
    if (!this.on) return "";
    if (!this.signedIn()) return `<button class="btn" data-act="svsignin"><span>Sign in to have notes signed</span></button>`;
    if (!this.acct.user.drive_connected) return `<button class="btn" data-act="svdrive"><span>Connect Google Drive</span></button>`;
    return "";
  },
  driveButton(pid) {
    if (!this.on) return "";
    if (!this.signedIn()) return `<p class="small muted" style="margin-top:10px">Sign in with Google to save the whole archive (paper, signed record, your notes) to your Google Drive.</p>
      <button class="btn" data-act="svsignin" style="margin-top:6px"><span>Sign in to save to Google Drive</span></button>`;
    const saved = S.b && S.b.drive_saved;
    return `<button class="btn ${saved ? "" : "primary"}" data-act="svdrivesave" data-pid="${esc(pid)}" style="margin-top:10px" ${this.busy === "drive" ? "disabled" : ""}>
      <span>${this.busy === "drive" ? "Saving to your Drive…" : saved ? "Saved to your Google Drive. Save again" : "Save to My Google Drive"}</span><span class="small">${esc(DRIVE_FOLDER)}</span></button>`;
  },
  authorAttrs() { return this.on && this.signedIn() ? ` list="sv-collabs" autocomplete="off"` : ""; },
  prefill(f) {
    if (!this.on || !this.signedIn() || f._prefilled) return;
    f._prefilled = true;
    const u = this.acct.user, a = f.authors[0];
    if (a && !a.name.trim() && !a.email.trim()) {
      a.name = u.full_name || ""; a.email = u.email || ""; a.affiliation = u.affiliation || ""; a.orcid = u.orcid || "";
    }
    f.optIn = !!u.dataset_opt_in_default;
  },
  authorInput(i, value) {
    if (!this.on || !this.signedIn()) return;
    const hit = this.collabs.find((c) => c.full_name === value);
    if (hit) {                                         // a collaborator chosen from the list: their details come with them
      const a = S.form.authors[i];
      for (const [k, from] of [["affiliation", "affiliation"], ["email", "email"], ["orcid", "orcid"]]) {
        if (!String(a[k] || "").trim() && hit[from]) {
          a[k] = hit[from];
          const el = document.querySelector(`[data-bind="authors.${i}.${k}"]`); if (el) el.value = hit[from];
        }
      }
      return;
    }
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      try {
        const r = await api(`/api/collaborators?q=${encodeURIComponent(value.trim())}`);
        this.collabs = r.collaborators || [];
        const dl = document.getElementById("sv-collabs");
        if (dl) dl.innerHTML = this.collabs.map((c) => `<option value="${esc(c.full_name)}">${esc([c.affiliation, c.email, c.times_collaborated > 1 ? c.times_collaborated + " papers together" : ""].filter(Boolean).join(" · "))}</option>`).join("");
      } catch (e) { /* suggestions are a convenience */ }
    }, 180);
  },
  refusal(e) {                                         // the server said no to a new paper: say why, and what to do
    if (!this.on) return false;
    if (e.code === "guest_limit") { this.signinDialog(e.message); return true; }
    if (e.code === "paywall") { this.payDialog(e.message); return true; }
    if (e.code === "signin") { this.signinDialog(e.message); return true; }
    if (e.code === "nokeys") { toast(e.message, true); this.acts.svkeys(); return true; }
    return false;
  },
  signinDialog(message) {
    showDialog(`<div class="dialog" role="dialog" aria-modal="true" aria-label="Sign in"><div class="dlg-h"><h2>Sign in with Google</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
      <div class="dlg-b">${message ? `<div class="flag red" style="margin-bottom:12px">${esc(message)}</div>` : ""}
      <p>Signing in keeps your papers and their progress safe, and gives your account ${esc(String((this.acct || {}).free_limit ?? 3))} free papers in all (papers you made as a guest in this browser count, and move into your account).</p>
      <p>With Google Drive connected as well, your notes are signed and timestamped when you upload them, and your finished paper can be saved to your Drive in one click.</p>
      <div class="sv-gbtn" style="margin:16px 0"></div>
      ${this.gis ? "" : `<p class="muted small">Google's sign-in button is loading…</p>`}
      <p class="muted small">Scriptavera receives your name, e-mail address and profile picture from Google. Nothing is posted anywhere.</p></div></div>`);
    this.paintButtons();
  },
  payDialog(message) {
    const A = this.acct || {};
    showDialog(`<div class="dialog" role="dialog" aria-modal="true" aria-label="Subscribe"><div class="dlg-h"><h2>Continue with a subscription</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
      <div class="dlg-b"><p>${esc(message || "You have used your free papers.")}</p>
      <p>The subscription${A.price_label ? ", " + esc(A.price_label) + "," : ""} pays for running Scriptavera: the servers, the signed and timestamped records, and keeping your work safe. You can cancel at any time.</p>
      <div class="row" style="margin-top:14px"><button class="btn primary" data-act="svsubscribe">${A.payments ? "Subscribe" : "Subscriptions open soon"}</button></div></div></div>`);
  },
  accountDialog() {
    const u = this.acct.user, A = this.acct;
    showDialog(`<div class="dialog" role="dialog" aria-modal="true" aria-label="Your account"><div class="dlg-h"><h2>Your account</h2><button class="btn small quiet" data-act="closedlg">Close</button></div>
      <div class="dlg-b"><p><b>${esc(u.full_name || "")}</b><br><span class="muted">${esc(u.email)}</span></p>
      <div class="grid2">
        <label class="field"><span>ORCID iD <span class="muted">(optional)</span></span><input type="text" id="svorcid" value="${esc(u.orcid || "")}" placeholder="0000-0000-0000-0000"></label>
        <label class="field"><span>Affiliation <span class="muted">(optional)</span></span><input type="text" id="svaff" value="${esc(u.affiliation || "")}" placeholder="Department, university, city, country"></label></div>
      <label class="check"><input type="checkbox" id="svoptdef" ${u.dataset_opt_in_default ? "checked" : ""}> Tick “Help improve Scriptavera” for my new papers by default</label>
      <div class="row" style="margin-top:10px"><button class="btn small" data-act="svsaveprofile">Save</button></div>
      <h3>Google Drive</h3><p>${u.drive_connected ? "Connected. Your notes are signed and timestamped when you upload them, and finished papers can be saved to the folder “" + esc(DRIVE_FOLDER) + "”." : "Not connected. Connect it to have your notes signed and timestamped, and to save your papers to your Drive."}</p>
      <div class="row">${u.drive_connected ? `<button class="btn small" data-act="svdriveoff">Disconnect</button>` : `<button class="btn small primary" data-act="svdrive">Connect Google Drive</button>`}</div>
      <h3>What Scriptavera remembers about you</h3><p>${A.memory ? `${A.memory.symbols} symbol meaning(s) and ${A.memory.readings} handwriting correction(s), learned from your own papers and used only for them.` : "Nothing yet."}</p>
      ${A.memory && (A.memory.symbols || A.memory.readings) ? `<div class="row"><button class="btn small" data-act="svforget">Forget all of it</button></div>` : ""}
      ${A.contributions ? `<h3>Notes shared for testing</h3><p>${A.contributions} paper(s) of yours are in Scriptavera's internal test suite, without your name.</p>
        <div class="row"><button class="btn small" data-act="svwithdraw">Withdraw my notes from testing</button></div>` : ""}
      <h3>Plan</h3><p>${A.subscribed ? "Subscribed" + (A.subscription && A.subscription.current_period_end ? ", renewed on " + esc(new Date(A.subscription.current_period_end).toLocaleDateString()) : "") + "."
        : `${Math.max(0, A.free_limit - (A.user_free_used || 0))} of ${A.free_limit} free papers left.`}</p>
      ${A.subscribed ? "" : `<div class="row"><button class="btn small" data-act="svsubscribe">Subscribe${A.price_label ? " (" + esc(A.price_label) + ")" : ""}</button></div>`}
      <div class="row" style="margin-top:18px"><button class="btn small quiet" data-act="svsignout">Sign out</button></div></div></div>`);
  },

  acts: {
    async svkeys() {
      HOSTED.keysDialog();
      try { await HOSTED.sendKeys(); HOSTED.keysDialog(); } catch (e) { toast(e.message, true); }
    },
    async svkeyadd(el) {
      const box = document.getElementById("svkey-" + el.dataset.p), v = ((box && box.value) || "").trim();
      if (!v) return toast("Paste the key first.", true);
      const keys = HOSTED.myKeys(), list = keys[el.dataset.p] || [];
      if (list.includes(v)) return toast("That key is already added.", true);
      if (list.length >= 5) return toast("Five keys per provider at most.", true);
      keys[el.dataset.p] = [...list, v]; HOSTED.saveMyKeys(keys);
      HOSTED.busy = "keys"; HOSTED.keysDialog();
      try {
        await HOSTED.sendKeys();
        const pr = (HOSTED.keyInfo.providers || []).find((x) => x.id === el.dataset.p) || {}, k = (pr.keys || [])[list.length] || {};
        toast(k.ok ? `Key added: ${k.tier === "paid" ? "paid plan" : k.tier === "free" ? "free plan" : "plan not known"}, ${k.count} models.` : "This key did not work: " + (k.message || "check it and try again."), !k.ok);
      } catch (e) { toast(e.message, true); }
      HOSTED.busy = ""; HOSTED.keysDialog(); S.meta = await api("/api/meta"); render();
    },
    async svkeydel(el) {
      const keys = HOSTED.myKeys(), list = keys[el.dataset.p] || [];
      list.splice(+el.dataset.i, 1);
      if (list.length) keys[el.dataset.p] = list; else delete keys[el.dataset.p];
      HOSTED.saveMyKeys(keys);
      try { await HOSTED.sendKeys(); } catch (e) { toast(e.message, true); }
      HOSTED.keysDialog(); S.meta = await api("/api/meta"); render();
    },
    async svkeysforget() {
      HOSTED.saveMyKeys({});
      try { HOSTED.keyInfo = await api("/api/keys", { method: "DELETE" }); } catch (e) { /* forgotten here anyway */ }
      toast("Your keys are forgotten, in this browser and on the server."); HOSTED.keysDialog(); S.meta = await api("/api/meta"); render();
    },
    async svforget() {
      try { await api("/api/memory", { method: "POST", body: { action: "clear" } }); await HOSTED.refresh(); HOSTED.accountDialog(); toast("Forgotten."); }
      catch (e) { toast(e.message, true); }
    },
    async svwithdraw() {
      try { const r = await api("/api/account/withdraw", { method: "POST" }); await HOSTED.refresh(); HOSTED.accountDialog(); toast(`Withdrawn: ${r.withdrawn} paper(s) deleted from the test suite.`); }
      catch (e) { toast(e.message, true); }
    },
    svsignin() { HOSTED.signinDialog(""); },
    svaccount() { HOSTED.accountDialog(); },
    async svsignout() {
      try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* signed out anyway */ }
      if (HOSTED.gis) HOSTED.gis.id.disableAutoSelect();
      HOSTED.token = null; closeDialog(); await HOSTED.refresh(); S.meta = await api("/api/meta"); await loadProjects(); location.hash = ""; render();
    },
    async svdrive() {
      try { await HOSTED.driveConnect(); toast("Google Drive is connected. New notes are signed and timestamped."); closeDialog(); render(); }
      catch (e) { toast(e.message, true); }
    },
    async svdriveoff() {
      try { HOSTED.acct = await api("/api/account/drive", { method: "DELETE" }); if (HOSTED.gis && HOSTED.token) HOSTED.gis.oauth2.revoke(HOSTED.token, () => {}); HOSTED.token = null; HOSTED.accountDialog(); render(); }
      catch (e) { toast(e.message, true); }
    },
    async svsaveprofile() {
      try {
        HOSTED.acct = await api("/api/account/profile", { method: "POST", body: { orcid: $("#svorcid").value, affiliation: $("#svaff").value,
          dataset_opt_in_default: $("#svoptdef").checked } });
        toast("Saved."); HOSTED.accountDialog();
      } catch (e) { toast(e.message, true); }
    },
    async svsubscribe() {
      if (!HOSTED.signedIn()) return HOSTED.signinDialog("Sign in first: the subscription belongs to your account.");
      try { const r = await api("/api/billing/checkout", { method: "POST" }); if (r.url) location.href = r.url; }
      catch (e) { toast(e.message, true); }
    },
    async svdrivesave(el) {
      HOSTED.busy = "drive"; render();
      try {
        const f = await HOSTED.driveSave(el.dataset.pid);
        if (S.b) S.b.drive_saved = true;
        toast(`Saved to your Google Drive, in “${DRIVE_FOLDER}”.`);
        if (f.webViewLink) window.open(f.webViewLink, "_blank", "noopener");
      } catch (e) { toast(e.message, true); }
      HOSTED.busy = ""; render();
    },
  },
};
