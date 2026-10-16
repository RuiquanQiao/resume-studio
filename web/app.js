// Resume Studio front end. No build step: plain DOM + fetch + EventSource.
// resume.yaml on disk is the source of truth; this page keeps a draft of the
// item being edited, autosaves it, and follows changes made by Claude.
"use strict";

// ================================================================ helpers
const clone = (x) => JSON.parse(JSON.stringify(x ?? null));
// resume languages (S.resume_langs from resume/langs.py): a code's own name, and a short form for toggles
const SHORT_LANG = { en: "EN", zh: "中文", "zh-TW": "繁中", ja: "日本語", ko: "한국어" };
const langInfo = (code) => ((S && S.resume_langs) || []).find((l) => l.code === code) || { code, name: code, cjk: false };
const langName = (code) => langInfo(code).name;
const langShort = (code) => SHORT_LANG[code] || String(code).toUpperCase();

// h(), icon(), $ and the custom controls live in ui.js

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(data.error || r.statusText);
    e.status = r.status;
    e.data = data.data;
    throw e;
  }
  return data;
}

function toast(msg, { ms = 2800, action = null } = {}) {
  const el = $("#toast");
  el.replaceChildren(h("span", null, msg));
  if (action) el.append(h("button", { class: "btn sm", onclick: () => { el.classList.remove("show"); action.fn(); } }, action.label));
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), ms);
}

function store(key, val) {
  try {
    if (val === undefined) return localStorage.getItem("rs:" + key);
    if (val === null) localStorage.removeItem("rs:" + key);
    else localStorage.setItem("rs:" + key, val);
  } catch (_) { return null; }
  return null;
}

// promise-based dialogs (prettier than prompt/confirm and they work in every webview)
function modal({ title, message = "", input = null, placeholder = "", ok = t("OK"), danger = false }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const field = input !== null ? h("input", { value: input, placeholder, spellcheck: "false" }) : null;
    const close = (val) => { root.replaceChildren(); document.removeEventListener("keydown", onKey, true); resolve(val); };
    const done = () => close(field ? field.value.trim() || null : true);
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(field ? null : false); }
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); done(); }
    };
    document.addEventListener("keydown", onKey, true);
    root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(field ? null : false); } },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true" },
        h("h3", null, title), message && h("p", null, message), field,
        h("div", { class: "acts" },
          h("button", { class: "btn", onclick: () => close(field ? null : false) }, t("Cancel")),
          h("button", { class: "btn " + (danger ? "danger-soft" : "primary"), onclick: done }, ok)))));
    setTimeout(() => (field || $(".modal .btn:last-child", root)).focus(), 0);
    if (field) field.select();
  });
}

// ---- tiny, safe markdown for chat replies ----
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function mdInline(s) {
  const codes = [];
  s = esc(s).replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" data-ext>$1</a>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}
function mdBlocks(text) {
  const lines = text.split("\n");
  const out = [];
  let i = 0;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => mdInline(c.trim()));
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (l.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]/.test(l);
      const items = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, "");
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(`<li>${mdInline(item)}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    if (l.startsWith(">")) {
      const q = [];
      while (i < lines.length && lines[i].startsWith(">")) q.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${mdInline(q.join(" "))}</blockquote>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|>|\s*([-*•]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${para.map(mdInline).join("<br>")}</p>`);
  }
  return out.join("");
}
function md(src) {
  const parts = String(src || "").split(/^```/m);
  return parts.map((p, i) => {
    if (i % 2 === 0) return mdBlocks(p);
    const nl = p.indexOf("\n");
    return `<pre><code>${esc((nl >= 0 ? p.slice(nl + 1) : p).replace(/\n$/, ""))}</code></pre>`;
  }).join("");
}

// ================================================================ state
let S = null; // last server state
const ui = {
  tab: "library",
  subsetId: null,
  sel: { kind: "profile", id: null },
  draft: null, formBase: null, dirty: false, editSeq: 0,
  saving: false, refreshAfterSave: false, saveTimer: null, conflict: null,
  editLang: null,
  render: { timer: null, busy: false, again: false, last: null },
  zoom: "fit",
  layoutOpen: false,
  ai: {},        // entryId -> {chat, action, snapshot, done}: what Claude was asked to change, for the diff + undo
  lines: {},     // lang -> {entryId: {bulletIndex: {n, fill}}}: measured by the last render in that language
  linesFor: {},  // lang -> subset id the measurement belongs to
  missing: [],   // entries lacking text in the previewed language (from the last render)
  evidence: {},  // entryId -> {state, paths: [...]}: have the project folders changed since Claude read them
  chat: { list: [], id: null, meta: null, messages: [], running: false, live: null, liveEl: null, available: false,
          queue: [], files: [], waiting: new Set(), thinking: 0 },
  claude: { info: null, limits: {}, modes: [] },   // what the user's Claude Code offers (GET /api/claude)
  w: {},         // custom controls created once: subset, chat, tpl
};

const doc = () => S.doc;
const langs = () => (doc().settings && doc().settings.languages) || ["en"];
const sections = () => doc().sections || [];
const entries = () => doc().entries || [];
const subsets = () => doc().subsets || [];
const subset = () => subsets().find((v) => v.id === ui.subsetId) || null;
const ALL = "ALL";
const isAll = () => ui.subsetId === ALL;
const subsetName = (v) => (v && v.id === ALL ? t("All entries") : (v && (v.label || v.id)) || "");
const entryById = (id) => entries().find((e) => e.id === id) || null;
// what people see: the entry's number inside its own section (library order); ids stay internal
function localNo(e) { return entries().filter((x) => x.section === e.section).findIndex((x) => x.id === e.id) + 1; }
function entryLabel(e) {
  if (!e) return "";
  const sec = sectionById(e.section);
  return `${(sec && display(sec.title)) || e.section || t("Unsorted")} #${localNo(e)}`;
}
const sectionById = (id) => sections().find((s) => s.id === id) || null;
// a copy of `list` with the item at `from` taken out and put back at `to`
const moved = (list, from, to) => { const o = [...list]; o.splice(to, 0, ...o.splice(from, 1)); return o; };
// every section id in this subset's order (same rule as model.section_order: unlisted ones go last)
function sectionOrder(v) {
  const all = sections().map((s) => s.id);
  return [...new Set([...((v && v.sections) || []).filter((id) => all.includes(id)), ...all])];
}

function itemHash(kind, id) {
  if (!S) return null;
  if (kind === "entry" || kind === "subset") return (S.hashes[kind] || {})[id] || null;
  return S.hashes[kind] || null;
}
function currentItem(kind, id) {
  if (kind === "entry") return entryById(id);
  if (kind === "subset") return subsets().find((v) => v.id === id) || null;
  return doc()[kind];
}

// Any text value is a plain string (all languages) or a map {en: "...", de: "..."}.
function getT(v, lang) { return v && typeof v === "object" ? v[lang] ?? "" : v ?? ""; }
function isNeutral(v) { return !(v && typeof v === "object"); }
function setT(old, lang, val) {
  const ls = langs();
  if (ls.length <= 1 && isNeutral(old)) return val;
  const obj = old && typeof old === "object" ? { ...old } : old ? Object.fromEntries(ls.map((l) => [l, old])) : {};
  obj[lang] = val;
  return obj;
}
function display(v) {
  const L = (subset() && subset().lang) || langs()[0];
  return getT(v, L) || (v && typeof v === "object" ? Object.values(v).find(Boolean) : "") || "";
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function shortDate(v) {
  const s = display(v).trim();
  if (!s) return "";
  if (/^(present|now|current|至今)$/i.test(s)) return t("now");
  const m = s.match(/^(\d{4})-(\d{1,2})/);
  return m ? `${m[1]}.${String(m[2]).padStart(2, "0")}` : s;
}
function entryMeta(e) {
  if (e.date) return display(e.date);
  const a = shortDate(e.start), b = shortDate(e.end);
  return a && b ? `${a} – ${b}` : a || b || "";
}

// ================================================================ data sync
async function refresh() {
  if (ui.saving) { ui.refreshAfterSave = true; return; }
  const s = await api("GET", "/api/state");
  applyState(s);
  syncEditor();
  scheduleRender();
}

function applyState(s) {
  S = s;
  if (!subsets().some((v) => v.id === ui.subsetId)) {
    const saved = store("subset") || store("version");   // "version": remembered before subsets were named
    ui.subsetId = subsets().some((v) => v.id === saved) ? saved : ALL;
  }
  if (!ui.editLang || !langs().includes(ui.editLang)) {
    ui.editLang = (subset() && langs().includes(subset().lang) && subset().lang) || langs()[0];
  }
  ui.chat.available = !!(s.claude && s.claude.available);
  // evidence paths changed (picked here, typed, or edited by Claude): look at the folders again
  const evSig = JSON.stringify(entries().map((e) => [e.id, e.evidence || []]));
  if (ui.evSig !== undefined && evSig !== ui.evSig) checkEvidence(true);
  ui.evSig = evSig;
  renderTopbar();
  renderLibrary();
  renderPreviewToolbar();
  renderComposerState();
}

// state changed elsewhere (Claude, another window): rebuild the form if it is stale
function syncEditor() {
  const { kind, id } = ui.sel;
  if (kind === "posting") return;   // not in resume.yaml; jobs.js refreshes it
  if ((kind === "entry" || kind === "subset") && !currentItem(kind, id)) { select("profile"); return; }
  if (itemHash(kind, id) === ui.formBase) return;
  if (ui.dirty) { ui.conflict = { current: clone(currentItem(kind, id)) }; buildEditor(); return; }
  buildEditor({ flash: true });
}

function touch() {
  ui.dirty = true;
  ui.editSeq++;
  setSaveStatus(t("Unsaved"));
  clearTimeout(ui.saveTimer);
  ui.saveTimer = setTimeout(saveDraft, 550);
}

function setSaveStatus(msg, bad = false) {
  const el = $("#save-status");
  el.textContent = msg;
  el.classList.toggle("bad", bad);
}

async function saveDraft(force = false) {
  clearTimeout(ui.saveTimer);
  if (!ui.dirty || ui.saving || (ui.conflict && !force)) return;
  const { kind, id } = ui.sel;
  const seq = ui.editSeq;
  ui.saving = true;
  setSaveStatus(t("Saving…"));
  try {
    const s = await api("PUT", "/api/item", { kind, id, data: ui.draft, base: force ? null : ui.formBase });
    if (kind === "entry" || kind === "subset") ui.sel.id = ui.draft.id;
    if (kind === "subset") { ui.subsetId = ui.draft.id; store("subset", ui.subsetId); }
    applyState(s);
    ui.formBase = itemHash(ui.sel.kind, ui.sel.id);
    if (ui.conflict) { ui.conflict = null; buildEditorKeepDraft(); }
    if (ui.editSeq === seq) { ui.dirty = false; setSaveStatus(t("Saved")); }
    else ui.saveTimer = setTimeout(saveDraft, 300);
    scheduleRender();
  } catch (e) {
    if (e.status === 409) {
      ui.conflict = { current: e.data && e.data.current };
      buildEditorKeepDraft();
      setSaveStatus(t("Conflict"), true);
    } else setSaveStatus(t("Save failed: {error}", { error: e.message }), true);
  } finally {
    ui.saving = false;
    if (ui.refreshAfterSave) { ui.refreshAfterSave = false; refresh(); }
  }
}

// subset tweaks from the sidebar / preview toolbar: re-read, apply, retry once on conflict
async function mutateSubset(fn) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const v = clone(subset() || (isAll() ? { id: ALL, label: "All entries", lang: langs()[0], template: "classic", headline: "", layout: {} } : null));
    if (!v) return;   // ALL may not be in the file yet; the server adds it on first save
    fn(v);
    try {
      const s = await api("PUT", "/api/item", { kind: "subset", id: ui.subsetId, data: v, base: itemHash("subset", ui.subsetId) });
      applyState(s);
      syncEditor();
      scheduleRender(200);
      return;
    } catch (e) {
      if (e.status !== 409 || attempt) { toast(t("Save failed: {error}", { error: e.message })); return; }
      applyState(await api("GET", "/api/state"));
    }
  }
}

// ================================================================ top bar
const TEMPLATE_NAMES = (id) => ((S.templates || []).find((t) => t.id === id) || {}).name || id;

function renderTopbar() {
  const jobs = subsets().filter((v) => v.id !== ALL);
  const all = `ALL · ${t("All entries")}`;
  const opts = [{ value: ALL, label: all, short: all, desc: t("The whole library, not limited to one page") }];
  for (const v of jobs) opts.push({ value: v.id, label: v.label || v.id, group: t("Subsets"), hint: `${langShort(v.lang)} · ${TEMPLATE_NAMES(v.template)}` });
  if (!jobs.length) opts.push({ value: "__none", label: t("No subsets yet"), desc: t("Click ··· on the right to create one"), group: t("Subsets"), disabled: true });
  ui.w.subset.setOptions(opts, ui.subsetId);
}

function openSubsetMenu() {
  const v = subset();
  const job = v && v.id !== ALL;
  const bilingual = langs().length > 1;
  const inV = job ? (v.entries || []).map(entryById).filter(Boolean) : entries();
  const lacking = (l) => inV.filter((e) => missingIn(e, l)).length;
  const long = longBullets().length;
  openMenu($("#subset-menu-btn"), [
    job && { label: t("Subset settings"), hint: t("Name, ID, headline"), icon: "settings", onClick: () => select("subset", ui.subsetId) },
    { label: t("New subset"), hint: t("Blank, tick entries yourself"), icon: "plus", onClick: () => newSubset(null) },
    v && { label: job ? t("Duplicate this subset") : t("New subset (with every entry ticked)"), icon: "copy", onClick: () => newSubset(ui.subsetId) },
    "sep",
    { header: job ? t("Ask Claude about this subset") : t("Ask Claude about all entries") },
    ...(bilingual ? langs().map((l) => ({ label: t("Fill in the {lang} version", { lang: langName(l) }),
      hint: lacking(l) ? t("{n} entry lacks it", { n: lacking(l) }, "{n} entries lack it") : t("All done"), icon: "translate", disabled: !ui.chat.available || !lacking(l),
      onClick: () => aiBatch("batch_translate", { target: l }) })) : []),
    { label: t("Fit bullets that run past one line"), hint: long ? t("{n} bullet", { n: long }, "{n} bullets") : t("All on one line"), icon: "compress", disabled: !ui.chat.available || !long,
      onClick: () => aiBatch("batch_fit") },
    "sep",
    { label: t("Open the export folder"), icon: "folder", onClick: revealExports },
    job && "sep",
    job && { label: t("Delete this subset"), icon: "trash", danger: true, onClick: deleteSubset },
  ], { align: "start", focusFirst: false });
}

async function switchSubset(id) {
  await saveDraft();
  if (ui.headPending) await ui.headPending();   // a headline being typed belongs to the subset it was typed in
  ui.subsetId = id;
  store("subset", id);
  const v = subset();
  if (v && langs().includes(v.lang)) ui.editLang = v.lang;
  ui.missing = [];
  renderTopbar();
  renderLibrary();
  renderPreviewToolbar();
  renderComposerState();
  if (ui.sel.kind === "subset") select(id === ALL ? "profile" : "subset", id === ALL ? null : id);   // ALL has no job settings
  else buildEditorKeepDraft();
  clearPages();
  scheduleRender(0);
}

async function newSubset(copyFrom) {
  const fromAll = copyFrom === ALL;
  const label = await modal({
    title: copyFrom && !fromAll ? t("Duplicate subset") : t("New subset"),
    message: fromAll ? t("Every entry starts ticked; untick the ones this subset doesn't need.") : copyFrom ? t("Copies this subset's entries, headline and layout.") : t("Starts blank; tick the entries for this subset in the library on the left."),
    input: copyFrom && !fromAll ? t("{name} (copy)", { name: subsetName(subset()) }) : "", placeholder: t("Subset name, e.g. AI Agent Engineer"), ok: t("Create"),
  });
  if (!label || !label.trim()) return;
  try {
    const r = await api("POST", "/api/subsets", { label: label.trim(), copy_from: copyFrom });
    applyState(r);
    switchSubset(r.id);
    toast(fromAll || copyFrom ? t("Created the subset “{name}”", { name: label.trim() }) : t("Created the subset “{name}”; tick its entries on the left", { name: label.trim() }));
  } catch (e) { toast(e.message); }
}

async function deleteSubset() {
  if (!(await modal({ title: t("Delete the subset “{name}”?", { name: subsetName(subset()) }), message: t("Only this subset's setup goes (which entries, headline, template and layout); the library itself is untouched."), ok: t("Delete"), danger: true }))) return;
  applyState(await api("DELETE", "/api/subsets/" + encodeURIComponent(ui.subsetId)));
  ui.subsetId = null;
  applyState(S);
  select("profile");
  clearPages();
  scheduleRender(0);
}

async function revealExports() {
  const dir = S.data_path.replace(/[\\/][^\\/]+$/, "") + "/" + ((doc().settings || {}).export_dir || "exports");
  const r = await api("POST", "/api/reveal", { path: dir }).catch(() => ({}));
  if (!r.ok) toast(t("Nothing exported yet; export once and this folder opens"));
}

// ================================================================ left: library
function setTab(tab) {
  ui.tab = tab;
  store("tab", tab);
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
  $("#library").classList.toggle("hidden", tab !== "library");
  $("#jobs").classList.toggle("hidden", tab !== "jobs");
  $("#chat").classList.toggle("hidden", tab !== "chat");
  if (tab === "chat") { renderChat(); setTimeout(() => $("#chat-input").focus(), 0); }
  if (tab === "jobs") { renderJobs(); loadJobs({ quiet: true }); }
  if (tab === "chat" && W.ws === "jobs" && W.chat !== "on") setJobsChat(true);   // a chat started in Job search
}

function renderLibrary() {
  const lib = $("#library");
  const scroll = lib.scrollTop;
  lib.replaceChildren();
  const v = subset();
  const nav = (kind, label, ic, id = null) => h("button", { class: "nav-row" + (ui.sel.kind === kind ? " on" : ""), onclick: () => select(kind, id) }, icon(ic), label);
  const job = v && v.id !== ALL;
  lib.append(nav("profile", t("Profile"), "user"), nav("sections", t("Sections & resume settings"), "grid"));
  if (job) lib.append(nav("subset", t("Subset settings"), "settings", v.id));
  lib.append(h("div", { class: "lib-hint" + (job ? " job" : "") }, job
    ? [h("b", null, subsetName(v)), ": " + t("tick = in this subset's resume")]
    : [h("b", null, "ALL"), ": " + t("every entry lives here. Create a subset, then tick the ones it needs")]));
  const changed = entries().filter((e) => (ui.evidence[e.id] || {}).state === "changed");
  if (changed.length) {
    lib.append(h("div", { class: "lib-update" },
      icon("refresh"),
      h("span", { class: "lu-text" }, changed.length === 1
        ? t("“{name}” has new progress", { name: display(changed[0].title) || entryLabel(changed[0]) })
        : t("{n} project has new progress since the last sync", { n: changed.length }, "{n} projects have new progress since the last sync")),
      changed.length === 1
        ? h("button", { class: "btn sm", onclick: () => select("entry", changed[0].id) }, t("View"))
        : h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => aiBatch("batch_sync") }, icon("sparkle"), t("Update one by one"))));
  }

  const included = new Set(job ? v.entries || [] : entries().map((e) => e.id));
  const order = job ? v.entries || [] : entries().map((e) => e.id);
  for (const sec of sectionOrder(v).map(sectionById)) {   // same order as this subset's resume
    const mine = entries().filter((e) => e.section === sec.id);
    const inV = order.map(entryById).filter((e) => e && e.section === sec.id);
    const outV = mine.filter((e) => !included.has(e.id));
    lib.append(h("div", { class: "lib-section" },
      h("div", { class: "lib-head" },
        h("span", null, h("span", { class: "t" }, display(sec.title) || sec.id), mine.length ? h("span", { class: "n" }, job ? `${inV.length}/${mine.length}` : mine.length) : null),
        h("button", { class: "btn icon sm ghost", title: t("Add an entry to “{section}”", { section: display(sec.title) || sec.id }), onclick: () => newEntry(sec.id) }, icon("plus"))),
      [...inV, ...outV].map((e, i) => entryRow(e, included.has(e.id), i, inV.length, job))));
  }
  const orphans = entries().filter((e) => !sectionById(e.section));
  if (orphans.length) lib.append(h("div", { class: "lib-section" }, h("div", { class: "lib-head" }, h("span", { class: "t" }, t("Unsorted"))), orphans.map((e) => entryRow(e, included.has(e.id), 0, 0, job))));

  if (!entries().length) {
    lib.append(h("div", { class: "empty" },
      h("div", { class: "t" }, t("The library is empty")),
      h("div", null, t("Put every real experience here; pick for each resume later.")),
      h("div", { class: "acts" },
        h("button", { class: "btn primary sm", onclick: () => newEntry((sections().find((s) => s.id === "projects") || sections()[0] || {}).id || "projects") }, icon("plus"), t("Add entry")),
        h("button", { class: "btn sm", onclick: () => startChatWith(t("Please import the experience from my old resume into resume.yaml. The old resume is at: ")) }, icon("sparkle"), t("Let Claude import")))));
  }
  if (S.warnings && S.warnings.length) lib.append(h("div", { class: "warnings" }, S.warnings.map((w) => h("div", null, w))));
  lib.scrollTop = scroll;
}

function entryRow(e, isIn, idx, nIn, hasSubset) {
  const active = ui.sel.kind === "entry" && ui.sel.id === e.id;
  const meta = entryMeta(e);
  const ev = (ui.evidence[e.id] || {}).state;
  const gaps = langs().length > 1 ? langs().filter((l) => missingIn(e, l)) : [];
  return h("div", { class: "entry" + (isIn ? "" : " out") + (active ? " on" : ""), "data-id": e.id, onclick: () => select("entry", e.id) },
    hasSubset && h("button", {
      class: "check" + (isIn ? " on" : ""), role: "checkbox", "aria-checked": String(isIn),
      title: isIn ? t("Remove from this subset") : t("Add to this subset"),
      onclick: (ev) => { ev.stopPropagation(); toggleInclude(e.id, !isIn); },
    }),
    h("span", { class: "e-no" }, localNo(e)),
    h("span", { class: "e-main" },
      h("span", { class: "e-title" }, display(e.title) || t("(untitled)"),
        ev === "changed" && h("span", { class: "e-dot", title: t("The project folder changed since the last sync") }),
        ev === "missing" && h("span", { class: "e-dot bad", title: t("The evidence folder can't be found") })),
      (meta || gaps.length > 0) && h("span", { class: "e-meta" }, meta,
        gaps.map((l) => h("span", { class: "e-gap", title: t("Some text has no {lang} yet; the {lang} resume shows another language there for now", { lang: langName(l) }) }, t("No {lang}", { lang: langShort(l) }))))),
    isIn && h("span", { class: "e-tools" },
      h("button", { class: "btn icon sm ghost", title: t("Move up"), disabled: idx === 0, onclick: (ev) => { ev.stopPropagation(); hasSubset ? moveInSubset(e.id, -1) : moveInLibrary(e.id, -1); } }, icon("up")),
      h("button", { class: "btn icon sm ghost", title: t("Move down"), disabled: idx >= nIn - 1, onclick: (ev) => { ev.stopPropagation(); hasSubset ? moveInSubset(e.id, 1) : moveInLibrary(e.id, 1); } }, icon("down"))));
}

function toggleInclude(id, on) {
  mutateSubset((v) => {
    v.entries = (v.entries || []).filter((x) => x !== id);
    if (on) v.entries.push(id);
  });
}

function moveInSubset(id, dir) {
  mutateSubset((v) => {
    const list = v.entries || [];
    const sec = (entryById(id) || {}).section;
    const same = list.map((x, i) => [x, i]).filter(([x]) => (entryById(x) || {}).section === sec);
    const k = same.findIndex(([x]) => x === id);
    const other = same[k + dir];
    if (!other) return;
    const i = same[k][1], j = other[1];
    [list[i], list[j]] = [list[j], list[i]];
  });
}

// in ALL, the arrows reorder the library itself
async function moveInLibrary(id, dir) {
  try { applyState(await api("POST", `/api/entries/${encodeURIComponent(id)}/move`, { dir })); scheduleRender(200); }
  catch (e) { toast(t("Couldn't move it: {error}", { error: e.message })); }
}

async function newEntry(section) {
  await saveDraft();
  const r = await api("POST", "/api/entries", { section });
  applyState(r);
  if (subset() && !isAll()) await mutateSubset((v) => { v.entries = [...(v.entries || []), r.id]; });
  select("entry", r.id);
  const title = () => $("#editor input[data-key=title]");
  if (title()) title().focus(); else setTimeout(() => title() && title().focus(), 30);
}

// ================================================================ middle: editor
async function select(kind, id = null) {
  await saveDraft();
  ui.sel = { kind, id };
  ui.conflict = null;
  ui.dirty = false;
  renderLibrary();
  if (kind !== "posting" && typeof renderJobs === "function") renderJobs();   // the open posting is no longer highlighted
  renderComposerState();
  buildEditor();
}

// "posting" is not part of resume.yaml: an evaluated job posting from the Applications tab (jobs.js)
const BUILDERS = () => ({ entry: entryForm, profile: profileForm, sections: sectionsForm, subset: subsetForm, posting: postingView });

function buildEditor(opts = {}) {
  const { kind, id } = ui.sel;
  if (kind === "posting") { ui.draft = null; ui.formBase = null; ui.dirty = false; paintEditor(opts); return; }
  const item = currentItem(kind, id);
  if (!ui.conflict) {
    ui.draft = clone(item);
    ui.formBase = itemHash(kind, id);
    ui.dirty = false;
  }
  paintEditor(opts);
}

function buildEditorKeepDraft() { paintEditor({ keepScroll: true }); }

function paintEditor({ flash = false, keepScroll = true } = {}) {
  const mid = $("#editor");
  const scroll = mid.scrollTop;
  const wrap = h("div", { class: "editor" });
  if (ui.conflict) wrap.append(conflictBanner());
  if (ui.draft == null && ui.sel.kind !== "sections" && ui.sel.kind !== "posting") wrap.append(h("p", { class: "muted" }, t("Nothing here.")));
  else wrap.append(...[BUILDERS()[ui.sel.kind]()].flat(Infinity).filter(Boolean));
  mid.replaceChildren(wrap);
  if (keepScroll) mid.scrollTop = scroll;
  if (flash) { wrap.classList.add("flash"); }
}

function conflictBanner() {
  return h("div", { class: "banner warn" }, icon("alert"),
    h("span", { style: "flex:1" }, t("Claude or another window just changed this item, and you have unsaved edits here.")),
    h("button", { class: "btn sm", onclick: () => { ui.conflict = null; ui.dirty = false; buildEditor({ flash: true }); } }, t("Load the latest")),
    h("button", { class: "btn sm danger-soft", onclick: () => saveDraft(true) }, t("Keep mine")));
}

function langSwitch() {
  if (langs().length <= 1) return null;
  return h("div", { class: "seg", title: t("Which language you are editing (the preview's language is switched above the preview)") }, langs().map((l) =>
    h("button", { class: l === ui.editLang ? "on" : "", "aria-pressed": String(l === ui.editLang), onclick: () => {
      if (l === ui.editLang) return;
      ui.editLang = l;
      buildEditorKeepDraft();
      if (ui.linesFor[l] !== ui.subsetId && !ui.render.busy) measureEditLang(ui.subsetId);
    } }, langShort(l))));
}

function card(title, sub, body, { cls = "", actions = null } = {}) {
  return h("section", { class: "card " + cls },
    h("div", { class: "card-h" }, h("h3", null, title), sub && h("span", { class: "sub" }, sub), h("span", { class: "spacer" }), actions),
    h("div", { class: "card-b" }, body));
}

// a text field bound to obj[key]; i18n fields store per-language values
function tfield(label, obj, key, { i18n = false, multiline = false, placeholder = "", rows = 2, hint = "" } = {}) {
  const val = i18n ? getT(obj[key], ui.editLang) : (obj[key] ?? "");
  const onInput = (ev) => { obj[key] = i18n ? setT(obj[key], ui.editLang, ev.target.value) : ev.target.value; touch(); };
  const attrs = { placeholder, oninput: onInput, "data-key": key, spellcheck: "false" };
  const input = multiline ? h("textarea", { ...attrs, rows }, val) : h("input", { ...attrs, value: val });
  const neutral = i18n && langs().length > 1 && isNeutral(obj[key]) && obj[key];
  const tag = i18n && langs().length > 1
    ? h("span", { class: "lang-tag" + (neutral ? " neutral" : ""), title: neutral ? t("Every language shares this text for now; once edited, each language keeps its own") : "" }, neutral ? t("Shared") : langShort(ui.editLang))
    : null;
  return h("div", { class: "field" }, h("label", null, label, tag, hint && h("span", { class: "hint" }, hint)), input);
}

function entryForm() {
  const e = ui.draft;
  const sec = sectionById(e.section);
  const isList = sec && sec.kind === "list";
  e.bullets = e.bullets || [];
  e.tech = e.tech || [];
  e.evidence = e.evidence || [];
  const out = [];

  const ai = ui.ai[e.id];
  if (ai && ai.done) out.push(aiDiff(e.id));

  out.push(h("div", { class: "ed-head" }, h("h1", null, display(e.title) || t("(untitled)")), h("span", { class: "id" }, entryLabel(entryById(e.id) || e)), h("span", { class: "spacer" }), langSwitch()));

  const secSel = uiSelect({
    options: sections().map((s) => ({ value: s.id, label: display(s.title) || s.id, hint: s.kind === "list" ? t("List") : t("Timeline") })),
    value: e.section, cls: "field-select", label: t("Section"),
    onChange: (v) => { e.section = v; touch(); buildEditorKeepDraft(); },
  });
  secSel.el.setAttribute("data-key", "section");
  const basics = [
    h("div", { class: "field" }, h("label", null, t("Section")), secSel.el),
    tfield(isList ? t("Category") : t("Name"), e, "title", { i18n: true, placeholder: isList ? t("e.g. Programming languages") : t("Company / school / project") }),
  ];
  if (!isList) {
    basics.push(
      h("div", { class: "grid2" }, tfield(t("Subtitle"), e, "subtitle", { i18n: true, placeholder: t("Position / degree / role") }), tfield(t("Location"), e, "location", { i18n: true })),
      h("div", { class: "grid3" }, tfield(t("Start"), e, "start", { placeholder: "2025-07" }), tfield(t("End"), e, "end", { placeholder: t("2026-02 or present") }),
        tfield(t("Custom date"), e, "date", { i18n: true, hint: t("optional, overrides start and end") })),
      tfield(t("Link"), e, "link", { placeholder: "https://…" }));
  }
  basics.push(h("div", { class: "field" }, h("label", null, isList ? t("Items") : t("Tech / keywords"), h("span", { class: "hint" }, t("comma-separated"))),
    h("input", { value: e.tech.map((t) => display(t)).join(", "), spellcheck: "false", "data-key": "tech",
      oninput: (ev) => { e.tech = ev.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean); touch(); } })));
  out.push(card(t("Basics"), null, basics));

  if (!isList) out.push(bulletsCard(e));

  out.push(card(t("Fact layer"), t("Only Claude sees this; it never appears on the resume"), [
    h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, t("Write what really happened: what you did, what AI wrote, where the numbers come from, the details you wouldn't put on a resume. When Claude polishes, it stays within what this supports.")),
    tfield(t("What really happened / raw notes"), e, "notes", { multiline: true, rows: 6 }),
    evidenceEditor(e),
  ], { cls: "fact" }));

  out.push(h("div", { class: "danger-zone" }, h("button", { class: "btn sm ghost danger", onclick: () => deleteEntry(e.id) }, icon("trash"), t("Delete this entry"))));
  return out;
}

// ---------------------------------------------------------------- bullets
const otherLang = (l) => langs().find((x) => x !== l) || l;

function bulletsCard(e) {
  const L = ui.editLang;
  const aiBtn = h("button", { class: "btn sm ghost ai-btn", "aria-haspopup": "menu", disabled: !ui.chat.available,
    title: ui.chat.available ? "" : t("The claude command was not found"), onclick: (ev) => openAiMenu(ev.currentTarget, e) }, icon("sparkle"), t("Ask Claude…"), icon("chev"));
  return card(t("Bullets"), langs().length > 1 ? t("Editing {lang}", { lang: langName(L) }) : null,
    [h("div", { id: "bullet-notes" }, bulletNotes(e)), bulletsEditor(e)], { actions: aiBtn, cls: "bullets-card" });
}

// what the line measurement and the bilingual check say about this entry, with one-click fixes
function bulletNotes(e) {
  const L = ui.editLang;
  const out = [];
  if (langs().length > 1 && missingIn(e, L)) {
    out.push(noteBar("translate", t("Some text has no {lang} yet", { lang: langName(L) }),
      ui.chat.available && h("button", { class: "btn sm", onclick: () => aiEntry("translate", e.id, { target: L }) }, icon("sparkle"), t("Let Claude write the {lang} version", { lang: langName(L) }))));
  }
  const m = measuredFor(e.id, L);
  // only bullets written in this language: an untranslated one is flagged as missing instead
  const long = m ? Object.entries(m).filter(([i, x]) => x.n > 1 && bulletState(e.bullets[i], L) === "ok").length : 0;
  if (long) {
    out.push(noteBar("compress", t("{n} bullet runs past one line", { n: long }, "{n} bullets run past one line"),
      ui.chat.available && h("button", { class: "btn sm", onclick: () => aiEntry("fit", e.id, { lang: L }) }, icon("sparkle"), t("Let Claude fit them on one line")), "warn"));
  } else if (m === null && e.bullets.some((b) => getT(b, L))) {
    out.push(h("div", { class: "note-line" }, icon("alert"), isAll() ? t("This entry's section is hidden, so it isn't in the preview and its lines can't be measured.") : t("This entry isn't ticked in “{job}”, so it isn't in the preview and its lines can't be measured.", { job: subsetName(subset()) })));
  }
  return out;
}

const NOTE_TIPS = {
  translate: "Missing parts show another language on this language's resume for now; once written, each bullet shows how many lines it takes",
  compress: "Measured with the preview's template, font size and margins; measured again after every edit or layout change",
};
function noteBar(ic, text, action, tone = "") {
  return h("div", { class: "note-bar " + tone }, icon(ic), h("span", { class: "nb-text", title: t(NOTE_TIPS[ic]) }, text), action);
}

function bulletState(b, L) {
  if (langs().length < 2 || !(b && typeof b === "object")) return "ok";
  const others = Object.entries(b).some(([k, x]) => k !== L && x);
  if (!(L in b)) return others ? "missing" : "ok";
  return b[L] === "" && others ? "hidden" : "ok";
}

function lineChip(i, m, st) {
  if (st === "missing") return h("span", { class: "line-chip gap", "data-line": i, title: t("No {lang} yet; the resume shows another language for now", { lang: langName(ui.editLang) }) }, t("No {lang}", { lang: langShort(ui.editLang) }));
  if (st === "hidden") return h("span", { class: "line-chip off", "data-line": i, title: t("This bullet only appears on the other languages' resumes") }, t("Hidden"));
  const x = m && m[i];
  if (!x) return h("span", { class: "line-chip none", "data-line": i });
  const pct = Math.round(x.fill * 100);
  if (x.n > 1) return h("span", { class: "line-chip long", "data-line": i, title: t("Takes {n} lines: the text is {pct}% of a line, cut about {cut}%", { n: x.n, pct, cut: Math.max(1, pct - 98) }) }, t("{n} line", { n: x.n }, "{n} lines"));
  return h("span", { class: "line-chip ok" + (x.fill > 0.93 ? " tight" : ""), "data-line": i, title: t("One line, {pct}% of it used", { pct }) }, x.fill > 0.93 ? t("Full") : t("{n} line", { n: 1 }, "{n} lines"));
}

function bulletsEditor(e) {
  const L = ui.editLang, O = otherLang(L);
  const box = h("div", { id: "bullets" });
  const redraw = () => { box.replaceWith(bulletsEditor(e)); };
  const m = measuredFor(e.id, L);
  e.bullets.forEach((b, i) => {
    const st = bulletState(b, L);
    const otherText = b && typeof b === "object" ? b[O] || "" : "";
    const ph = st === "missing" ? `${langShort(O)}: ${otherText}` : st === "hidden" ? t("Not shown on the {lang} resume ({other}: {text})", { lang: langName(L), other: langShort(O), text: otherText }) : "";
    const ta = h("textarea", { rows: 1, spellcheck: "false", "data-bullet": i, placeholder: ph, class: st !== "ok" ? "is-" + st : null,
      oninput: (ev) => { e.bullets[i] = setT(e.bullets[i], L, ev.target.value); touch(); } }, getT(b, L));
    const bilingual = langs().length > 1 && b && typeof b === "object";
    box.append(h("div", { class: "bullet" + (st !== "ok" ? " " + st : "") }, h("span", { class: "dot" }), ta, lineChip(i, m, st),
      h("div", { class: "b-tools" },
        bilingual && (st === "hidden"
          ? h("button", { class: "btn icon sm ghost", title: t("Put it back on the {lang} resume", { lang: langName(L) }), onclick: () => { delete e.bullets[i][L]; touch(); redraw(); } }, icon("undo"))
          : h("button", { class: "btn icon sm ghost", title: t("Leave this bullet off the {lang} resume (the other languages keep it)", { lang: langName(L) }), onclick: () => { e.bullets[i] = { ...e.bullets[i], [L]: "" }; touch(); redraw(); } }, icon("x"))),
        h("button", { class: "btn icon sm ghost", title: t("Move up"), disabled: i === 0, onclick: () => { [e.bullets[i - 1], e.bullets[i]] = [e.bullets[i], e.bullets[i - 1]]; touch(); redraw(); } }, icon("up")),
        h("button", { class: "btn icon sm ghost", title: t("Move down"), disabled: i === e.bullets.length - 1, onclick: () => { [e.bullets[i + 1], e.bullets[i]] = [e.bullets[i], e.bullets[i + 1]]; touch(); redraw(); } }, icon("down")),
        h("button", { class: "btn icon sm ghost danger", title: langs().length > 1 ? t("Delete this bullet (in every language)") : t("Delete this bullet"), onclick: () => { e.bullets.splice(i, 1); touch(); redraw(); } }, icon("trash")))));
  });
  if (!e.bullets.length) {
    const hasEvidence = (e.evidence || []).length;
    box.append(h("p", { class: "muted", style: "margin:0 0 8px" }, hasEvidence
      ? t("No bullets yet. A project folder is linked, so Claude can read the project first and draft them.")
      : t("No bullets yet. Write them yourself, or fill in the fact layer below and link a project folder, then let Claude draft them.")));
  }
  const instr = h("input", { placeholder: t("Extra instructions (optional), e.g. stress the architecture; the second bullet overclaims"), spellcheck: "false",
    onkeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) aiEntry("polish", e.id, { text: instr.value, lang: L }); } });
  box.append(
    h("div", { class: "row wrap" },
      h("button", { class: "btn sm", onclick: () => { e.bullets.push(langs().length > 1 ? { [L]: "" } : ""); /* the other languages show as missing until written */ touch(); redraw(); setTimeout(() => { const ta = document.querySelectorAll("#bullets textarea"); if (ta.length) ta[ta.length - 1].focus(); }, 0); } }, icon("plus"), t("Add bullet")),
      h("span", { class: "muted", style: "font-size:12px" }, t("Supports **bold**, *italic*, [text](link)"))),
    h("div", { id: "polish-box", class: "polish-box hidden" }, instr,
      h("button", { class: "btn primary sm", onclick: () => aiEntry("polish", e.id, { text: instr.value, lang: L }) }, icon("sparkle"), t("Polish"))));
  return box;
}

function openAiMenu(anchor, e) {
  const L = ui.editLang, bilingual = langs().length > 1;
  const m = measuredFor(e.id, L);
  const long = m ? Object.entries(m).filter(([i, x]) => x.n > 1 && bulletState(e.bullets[i], L) === "ok").length : 0;
  const ev = ui.evidence[e.id] || {};
  const changed = ev.state === "changed" || ev.state === "new";
  const empty = !e.bullets.some((b) => getT(b, L));
  openMenu(anchor, [
    { label: empty ? t("Draft bullets") : bilingual ? t("Polish the {lang} bullets", { lang: langName(L) }) : t("Polish bullets"), hint: t("extra instructions welcome"), icon: "sparkle",
      onClick: () => { const box = $("#polish-box"); box.classList.remove("hidden"); $("input", box).focus(); } },
    ...(bilingual ? langs().map((l) => ({ label: t("Write the {lang} version", { lang: langName(l) }), hint: t("rewritten the way a {lang} resume reads", { lang: langName(l) }), icon: "translate", onClick: () => aiEntry("translate", e.id, { target: l }) })) : []),
    { label: t("Fit bullets that run past one line"), hint: long ? t("{n} bullet", { n: long }, "{n} bullets") : t("All on one line"), icon: "compress", disabled: !long, onClick: () => aiEntry("fit", e.id, { lang: L }) },
    "sep",
    { label: t("Update from project progress"), hint: ev.state === "changed" ? t("changed") : ev.state === "new" ? t("not read yet") : (e.evidence || []).length ? t("no changes") : t("no folder linked"),
      icon: "refresh", disabled: !changed, onClick: () => aiEntry("sync", e.id) },
  ], { align: "end", focusFirst: true });
}

// ---------------------------------------------------------------- evidence: project folders
function pickStart(e) {
  const last = (e.evidence || []).slice(-1)[0] || entries().flatMap((x) => x.evidence || []).slice(-1)[0];
  if (last) return last.replace(/[\\/][^\\/]*[\\/]?$/, "") || last;
  return ((doc().settings || {}).project_roots || [])[0] || "";
}

async function addEvidence(e, kind, replaceIndex = null) {
  let r;
  try { r = await api("POST", "/api/pick", { kind, initial: replaceIndex != null ? e.evidence[replaceIndex] : pickStart(e) }); }
  catch (err) { toast(err.message); return; }
  if (!r.paths.length) return;
  if (ui.sel.kind !== "entry" || ui.draft !== e) return; // the user moved on while the dialog was open
  if (replaceIndex != null) e.evidence.splice(replaceIndex, 1, r.paths[0]);
  else for (const p of r.paths) if (!e.evidence.includes(p)) e.evidence.push(p);
  touch();
  buildEditorKeepDraft();
  await saveDraft();
  checkEvidence(true);
}

async function typeEvidence(e) {
  const p = await modal({ title: t("Type a path"), message: t("The full path of a folder or file. “Choose project folder” is usually easier."), input: "", placeholder: "E:/Forge/Paper2Exam", ok: t("Add") });
  if (!p) return;
  const clean = p.trim().replace(/^["']|["']$/g, "");
  if (!e.evidence.includes(clean)) e.evidence.push(clean);
  touch();
  buildEditorKeepDraft();
  await saveDraft();
  checkEvidence(true);
}

function changeSummary(c) {
  if (!c) return t("changed");
  const parts = [];
  if (c.commits) parts.push(t("{n} new commit", { n: c.commits }, "{n} new commits"));
  if (c.rewritten) parts.push(t("git history changed"));
  if (c.uncommitted) parts.push(t("{n} uncommitted file", { n: c.uncommitted }, "{n} uncommitted files"));
  if (c.counts) { const n = c.counts.reduce((a, b) => a + b, 0); if (n) parts.push(t("{n} file changed", { n }, "{n} files changed")); }
  return parts.join(t(", ")) || t("changed");
}

function evidenceRow(e, p, i, row) {
  const name = p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
  const st = row ? row.state : "checking";
  const kind = row && row.kind;
  const chip = {
    checking: h("span", { class: "ev-chip" }, t("Checking…")),
    same: h("span", { class: "ev-chip ok", title: row && row.synced_at ? t("Claude last read it {when}", { when: relTime(row.synced_at) }) : "" }, icon("check"), t("In sync")),
    new: h("span", { class: "ev-chip", title: t("Claude hasn't read it since it was added") }, t("Not read by Claude yet")),
    changed: h("span", { class: "ev-chip new" }, icon("refresh"), changeSummary(row && row.changes)),
    missing: h("span", { class: "ev-chip bad" }, icon("alert"), t("This path can't be found")),
  }[st];
  const detail = st === "changed" && row.changes ? changeDetail(row.changes) : null;
  const toggle = detail && h("button", { class: "ev-more", "aria-expanded": "false", onclick: (ev) => {
    const open = detail.classList.toggle("hidden") === false;
    ev.currentTarget.setAttribute("aria-expanded", String(open));
    ev.currentTarget.lastChild.textContent = open ? t("Hide") : t("See what changed");
  } }, icon("chev"), t("See what changed"));
  return h("div", { class: "ev-row " + st, "data-path": p },
    h("span", { class: "ev-ic" }, icon(kind === "git" ? "git" : kind === "file" ? "file" : "folder")),
    h("div", { class: "ev-main" },
      h("div", { class: "ev-name" }, h("b", null, name), chip),
      h("div", { class: "ev-path", title: p }, p),
      h("div", { class: "ev-meta" }, row && row.mtime ? t("last changed {when}", { when: relTime(row.mtime) }) : null, toggle),
      detail),
    h("div", { class: "ev-tools" },
      h("button", { class: "btn icon sm ghost", title: t("Show in the file manager"), disabled: st === "missing", onclick: () => api("POST", "/api/reveal-any", { path: p }) }, icon("external")),
      h("button", { class: "btn icon sm ghost", title: st === "missing" ? t("Choose this folder again") : t("Replace with another folder"), onclick: () => addEvidence(e, kind === "file" ? "files" : "folder", i) }, icon("folderOpen")),
      h("button", { class: "btn icon sm ghost danger", title: t("Remove (doesn't delete any files)"), onclick: () => { e.evidence.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
}

function changeDetail(c) {
  const lines = [];
  if (c.log && c.log.length) lines.push(h("div", { class: "cd-head" }, t("New commits")), ...c.log.slice(0, 12).map((l) => h("div", { class: "cd-line" }, l)));
  if (c.log && c.log.length > 12) lines.push(h("div", { class: "cd-line muted" }, t("…and {n} more", { n: c.log.length - 12 })));
  for (const [k, label] of [["added", t("Added")], ["modified", t("Modified")], ["removed", t("Removed")]]) {
    if (c[k] && c[k].length) lines.push(h("div", { class: "cd-head" }, label), ...c[k].slice(0, 8).map((l) => h("div", { class: "cd-line" }, l)));
  }
  if (c.uncommitted) lines.push(h("div", { class: "cd-line muted" }, t("{n} more file has uncommitted changes", { n: c.uncommitted }, "{n} more files have uncommitted changes")));
  return h("div", { class: "change-detail hidden" }, lines);
}

function evidenceEditor(e) {
  const st = ui.evidence[e.id] || { paths: [] };
  const rows = Object.fromEntries((st.paths || []).map((r) => [r.path, r]));
  const box = h("div", { class: "evidence", id: "evidence" });
  box.append(h("div", { class: "ev-head" }, h("span", { class: "ev-title" }, t("Project folders and evidence")),
    h("span", { class: "hint" }, t("Claude reads the code, README and commit history in them"))));
  const pending = e.evidence.map((p) => rows[p]).filter((r) => r && (r.state === "changed" || r.state === "new"));
  if (pending.length) {
    const changed = pending.some((r) => r.state === "changed");
    const empty = !e.bullets.some((b) => getT(b, ui.editLang));
    box.append(h("div", { class: "sync-bar" + (changed ? " changed" : "") }, icon(changed ? "refresh" : "folderOpen"),
      h("span", { class: "nb-text" }, changed ? t("The project has new progress") : t("Claude hasn't read this project yet")),
      ui.chat.available && h("button", { class: "btn sm primary", onclick: () => aiEntry(empty && !changed ? "polish" : "sync", e.id, { lang: ui.editLang }) }, icon("sparkle"),
        changed ? t("Let Claude update from the progress") : empty ? t("Let Claude read the project and write bullets") : t("Let Claude read it")),
      h("button", { class: "btn sm ghost", title: t("The bullets already reflect the project, so Claude needn't read it; you'll be told when it changes again"), onclick: () => markSynced(e.id) }, t("Mark as in sync"))));
  }
  const list = h("div", { class: "ev-list" });
  e.evidence.forEach((p, i) => list.append(evidenceRow(e, p, i, rows[p])));
  if (!e.evidence.length) list.append(h("div", { class: "ev-empty" }, t("No project linked yet. Choose a folder and Claude reads it when writing bullets; when the project changes later, this place and the library on the left will tell you.")));
  box.append(list, h("div", { class: "ev-actions" },
    h("button", { class: "btn sm", onclick: () => addEvidence(e, "folder") }, icon("folder"), t("Choose project folder")),
    h("button", { class: "btn sm ghost", onclick: () => addEvidence(e, "files") }, icon("file"), t("Add files")),
    h("button", { class: "btn sm ghost", onclick: () => typeEvidence(e) }, icon("pen"), t("Type a path")),
    h("span", { class: "spacer" }),
    e.evidence.length ? h("button", { class: "btn sm ghost", title: t("Check these folders for changes now (it also happens whenever you come back to the window)"), onclick: () => checkEvidence(true).then(() => toast(t("Checked"))) }, icon("refresh"), t("Check for updates")) : null));
  return box;
}

async function markSynced(id) {
  await saveDraft();
  try {
    const r = await api("POST", `/api/evidence/${encodeURIComponent(id)}/synced`);
    Object.assign(ui.evidence, r.status);
    paintEvidence();
    renderLibrary();
  } catch (err) { toast(err.message); }
}

// One check at a time; a request that arrives meanwhile (e.g. right after picking a folder)
// runs once the current one is done, so it always sees the latest data.
let evidenceBusy = null, evidenceAgain = null;
async function checkEvidence(force = false) {
  if (evidenceBusy) {
    evidenceAgain = evidenceAgain === null ? force : evidenceAgain || force;
    return evidenceBusy;
  }
  evidenceBusy = (async () => {
    try {
      const r = await api("GET", "/api/evidence" + (force ? "?force=1" : ""));
      const before = JSON.stringify(ui.evidence);
      ui.evidence = r.status;
      if (JSON.stringify(ui.evidence) !== before) { paintEvidence(); renderLibrary(); }
    } catch (_) { /* offline: keep the last known state */ }
    finally { evidenceBusy = null; }
    if (evidenceAgain !== null) { const f = evidenceAgain; evidenceAgain = null; await checkEvidence(f); }
  })();
  return evidenceBusy;
}

// refresh just the evidence block, so typing elsewhere in the form is not interrupted
function paintEvidence() {
  const old = $("#evidence");
  if (old && ui.sel.kind === "entry" && ui.draft) old.replaceWith(evidenceEditor(ui.draft));
}

async function deleteEntry(id) {
  if (!(await modal({ title: t("Delete “{name}”?", { name: display((entryById(id) || {}).title) || entryLabel(entryById(id)) }), message: t("It is removed from the library and every subset. You can get it back from .studio/history if needed."), ok: t("Delete"), danger: true }))) return;
  ui.dirty = false;
  applyState(await api("DELETE", "/api/entries/" + encodeURIComponent(id)));
  select("profile");
  scheduleRender();
}

function profileForm() {
  const p = ui.draft;
  p.contacts = p.contacts || [];
  // a contact written once serves every language; one already split by language (a phone number
  // per country) changes only in the language being edited
  const editT = (old, val) => isNeutral(old) ? val : { ...old, [ui.editLang]: val };
  const rows = h("div", { class: "list-rows contacts" });
  const v = subset();
  const job = v && v.id !== ALL;
  const hiddenC = new Set(job ? v.hide_contacts || [] : []);
  p.contacts.forEach((c, i) => {
    const shown = !hiddenC.has(c.label || "");
    rows.append(h("div", { class: "lr" + (job ? " job" : "") },
      job && h("button", { class: "check c-check" + (shown ? " on" : ""), role: "checkbox", "aria-checked": String(shown),
        title: shown ? t("Hide in “{job}”", { job: subsetName(v) }) : t("Show in “{job}”", { job: subsetName(v) }),
        onclick: () => mutateSubset((x) => {
          const set = new Set(x.hide_contacts || []);
          if (shown) set.add(c.label || ""); else set.delete(c.label || "");
          x.hide_contacts = [...set];
          if (!x.hide_contacts.length) delete x.hide_contacts;
        }).then(buildEditorKeepDraft) }),
      h("input", { class: "c-label", value: c.label ?? "", placeholder: "email", spellcheck: "false", oninput: (ev) => { c.label = ev.target.value; touch(); } }),
      h("input", { class: "c-value", value: getT(c.value, ui.editLang), placeholder: t("Text shown"), spellcheck: "false", oninput: (ev) => { c.value = editT(c.value, ev.target.value); touch(); } }),
      h("input", { class: "c-url", value: getT(c.url, ui.editLang), placeholder: t("Link (optional) mailto: / https://"), spellcheck: "false", oninput: (ev) => { c.url = editT(c.url, ev.target.value); touch(); } }),
      h("button", { class: "btn icon sm ghost danger c-del", title: t("Delete"), onclick: () => { p.contacts.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
  });
  rows.append(h("button", { class: "btn sm", onclick: () => { p.contacts.push({ label: "", value: "", url: "" }); touch(); buildEditorKeepDraft(); } }, icon("plus"), t("Add contact")));
  return [
    h("div", { class: "ed-head" }, h("h1", null, t("Profile")), h("span", { class: "spacer" }), langSwitch()),
    card(t("Basics"), null, [
      tfield(t("Name"), p, "name", { i18n: true }),
      headlineField(v, job),
    ]),
    card(t("Contacts"), job ? t("Shown in order under the name; ticked = shown in “{job}”", { job: subsetName(v) }) : t("Shown in order under the name; switch to a subset to hide some of them"), rows),
    card(t("Fact layer"), t("Only Claude sees this"), tfield(t("Job search background, constraints, preferences"), p, "notes", { multiline: true, rows: 5 }), { cls: "fact" }),
  ];
}

// The headline under the name belongs to the resume picked at the top (ALL or a job), so it is
// edited right here, in that resume, instead of on a page of its own.
function headlineField(v, job) {
  const cur = (v && v.headline) || "";
  const multi = langs().length > 1;
  const neutral = multi && isNeutral(cur) && cur;
  const tag = multi ? h("span", { class: "lang-tag" + (neutral ? " neutral" : "") }, neutral ? t("Shared") : langShort(ui.editLang)) : null;
  return h("div", { class: "field" },
    h("label", null, t("Headline / job target"), tag, h("span", { class: "hint" }, t("Belongs to “{job}”; each subset has its own", { job: job ? subsetName(v) : "ALL" }))),
    h("input", { value: getT(cur, ui.editLang), "data-key": "headline", spellcheck: "false",
      placeholder: job ? t("e.g. AI Agent Engineer · LLM tooling & full-stack") : t("can be empty"),
      oninput: (ev) => queueHeadline(ui.editLang, ev.target.value) }));
}

function queueHeadline(lang, val) {
  clearTimeout(ui.headTimer);
  setSaveStatus(t("Unsaved"));
  ui.headPending = () => {
    clearTimeout(ui.headTimer);
    ui.headPending = null;
    return mutateSubset((x) => { x.headline = setT(x.headline, lang, val); }).then(() => setSaveStatus(t("Saved")));
  };
  ui.headTimer = setTimeout(() => ui.headPending && ui.headPending(), 500);
}

// Resume languages: any number, from the list the templates set well; the first is the default
// for new jobs. Removing one keeps its text in resume.yaml (adding it back brings the text back).
function resumeLangEditor(st, save) {
  const list = st.languages && st.languages.length ? [...st.languages] : ["en"];
  const commit = (next) => { st.languages = next; save(); };
  const rows = h("div", { class: "list-rows lang-rows" });
  list.forEach((code, i) => {
    rows.append(h("div", { class: "lr sort-row", style: "grid-template-columns: 26px 1fr auto 26px" },
      dragHandle((from, to) => commit(moved(list, from, to))),
      h("span", { class: "lang-name" }, langName(code), h("span", { class: "muted" }, ` · ${code}`)),
      i === 0 ? h("span", { class: "chip" }, t("Default")) : h("span"),
      h("button", { class: "btn icon sm ghost danger", disabled: list.length === 1, title: list.length === 1 ? t("A resume needs at least one language") : t("Remove (its text stays in resume.yaml)"),
        onclick: () => commit(list.filter((x) => x !== code)) }, icon("x"))));
  });
  const add = uiSelect({ cls: "field-select", label: t("Add a language"), placeholder: t("Add a language…"), menuWidth: 260,
    options: ((S && S.resume_langs) || []).filter((l) => !list.includes(l.code)).map((l) => ({ value: l.code, label: l.name, hint: l.code })),
    onChange: (code) => code && commit([...list, code]) });
  return h("div", null, rows, add.el);
}

async function putSettings(st) {
  try { applyState(await api("PUT", "/api/item", { kind: "settings", data: st, base: itemHash("settings") })); buildEditorKeepDraft(); scheduleRender(); }
  catch (e) { toast(t("Save failed: {error}", { error: e.message })); }
}

// The "+" after the language buttons above the preview: where one looks first to add a language.
function openLangMenu(anchor) {
  const have = langs();
  const add = (code) => putSettings({ ...clone(doc().settings || {}), languages: [...have, code] });
  openMenu(anchor, [
    { label: t("Manage languages…"), icon: "settings", onClick: () => select("sections") },
    "sep",
    { header: t("Add a language") },
    ...((S && S.resume_langs) || []).filter((l) => !have.includes(l.code)).map((l) => ({ label: l.name, hint: l.code, onClick: () => add(l.code) })),
  ], { align: "end", focusFirst: true });
}

// App preferences belong to this computer (<skill>/.local/prefs.json), not to the resume, so they
// live behind the gear in the top bar (Ctrl+,) instead of on a resume page.
let closeSettings = () => {};
// a language picked in Settings waits for "Done" (Escape or a click outside drop it)
let pendingUiLang, uiLangSaved = false;
async function applyUiLang() {
  if (pendingUiLang !== undefined) {
    const code = pendingUiLang;
    pendingUiLang = undefined;
    if (code !== (I18N.chosen || null)) { await api("PUT", "/api/prefs", { ui_lang: code }); uiLangSaved = true; }
  }
  return uiLangSaved;   // saved but not shown yet: the page still has to reload
}
function openSettings(section = null) {
  const root = $("#modal-root");
  if (root.firstChild) return;
  pendingUiLang = undefined;
  const done = async () => {
    try { await applyJobsSettings(); } catch (e) { toast(t("Save failed: {error}", { error: e.message })); return; }
    try { if (await applyUiLang()) { await saveDraft(); location.reload(); return; } }
    catch (e) { toast(t("Save failed: {error}", { error: e.message })); return; }
    close();
  };
  const close = () => {
    for (const k of Object.keys(jobsPending)) delete jobsPending[k];
    pendingUiLang = undefined; closeSettings = () => {}; root.replaceChildren(); document.removeEventListener("keydown", onKey); $("#settings-btn").focus(); };
  closeSettings = close;
  // bubble phase: an open dropdown takes Escape first (it stops the event in the capture phase)
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal settings", role: "dialog", "aria-modal": "true", "aria-label": t("Settings") },
      h("h3", null, t("Settings")),
      h("p", null, t("These belong to this computer, not to your resume.")),
      h("div", { class: "field" }, h("label", null, t("App language"), h("span", { class: "hint" }, t("The language of this window; it follows your computer unless you pick one"))), uiLangPicker()),
      h("div", { class: "field" }, h("label", null, t("Data file"), h("span", { class: "hint" }, t("Your whole resume lives in this one file; the window opens it next time too"))),
        h("div", { class: "data-path" }, S.data_path),
        h("div", { class: "row wrap" },
          h("button", { class: "btn sm", onclick: () => api("POST", "/api/reveal", { path: S.data_path }) }, icon("external"), t("Show in the file manager")),
          h("button", { class: "btn sm", onclick: () => changeDataFile("open") }, icon("folderOpen"), t("Open another…")),
          h("button", { class: "btn sm", onclick: () => changeDataFile("new") }, icon("plus"), t("New…")))),
      jobsSettingsSection(),
      h("div", { class: "acts" }, h("button", { class: "btn primary", onclick: done }, t("Done"))))));
  setTimeout(() => {
    if (section === "jobs") { const el = $("#jobs-settings"); el.scrollIntoView({ block: "start" }); el.classList.add("flash"); }
    else $(".modal .select", root).focus();
  }, 0);
}

// Work on another data file in this same window. Chats and history live next to each file, so
// they switch too; a reply Claude is still writing would be cut off, hence the question first.
async function changeDataFile(mode) {
  // the reload below would drop a language picked a moment ago in the same dialog
  try { await applyUiLang(); } catch (e) { toast(t("Save failed: {error}", { error: e.message })); return; }
  if (ui.chat.running) {
    closeSettings();
    if (!(await modal({ title: t("Switch data file?"), message: t("Claude is still working in the chat. Switching files stops it."), ok: t("Switch"), danger: true }))) return;
  }
  await saveDraft();
  let r;
  try { r = await api("POST", "/api/data-file", { mode }); }
  catch (e) { toast(e.message); return; }
  if (!r.path || r.same) return;
  store("dataSwitched", r.created ? "new" : "open");
  location.reload();
}

// The window's own language: the computer's unless the user picks one; "Done" reloads the page to apply it.
function uiLangPicker() {
  const sys = I18N.langs[I18N.system] || I18N.system;
  const sel = uiSelect({ cls: "field-select", label: t("App language"), menuWidth: 260, value: I18N.chosen || null,
    options: [{ value: null, label: t("Follow the computer ({lang})", { lang: sys }) },
      ...Object.entries(I18N.langs).map(([code, name]) => ({ value: code, label: name, hint: code }))],
    onChange: (code) => { pendingUiLang = code; } });
  return sel.el;
}

function sectionsForm() {
  if (!Array.isArray(ui.draft)) ui.draft = [];
  const list = ui.draft;
  const rows = h("div", { class: "list-rows section-rows" });
  // shown and moved in the order of the resume picked at the top (ALL or a job): the order belongs to it
  const ids = list.map((s) => s.id);
  const order = [...new Set([...((subset() || {}).sections || []).filter((id) => ids.includes(id)), ...ids])];
  const move = (from, to) => mutateSubset((v) => { v.sections = moved(order, from, to); }).then(buildEditorKeepDraft);
  const hidden = new Set((subset() || {}).hide_sections || []);
  const where = isAll() ? "ALL" : subsetName(subset());
  order.map((id) => list.find((s) => s.id === id)).forEach((s) => {
    const i = list.indexOf(s);
    const used = entries().some((e) => e.section === s.id);
    const shown = !hidden.has(s.id);
    rows.append(h("div", { class: "lr sort-row" + (shown ? "" : " off") },
      dragHandle(move),
      h("button", { class: "check" + (shown ? " on" : ""), role: "checkbox", "aria-checked": String(shown),
        title: shown ? t("Hide in “{job}”", { job: where }) : t("Show in “{job}”", { job: where }),
        onclick: () => mutateSubset((v) => {
          const set = new Set(v.hide_sections || []);
          if (shown) set.add(s.id); else set.delete(s.id);
          v.hide_sections = [...set];
          if (!v.hide_sections.length) delete v.hide_sections;
        }).then(buildEditorKeepDraft) }),
      h("input", { value: s.id, title: t("Section ID (the section field of entries)"), spellcheck: "false", oninput: (ev) => { s.id = ev.target.value; touch(); } }),
      h("input", { value: getT(s.title, ui.editLang), placeholder: t("Title shown"), oninput: (ev) => { s.title = setT(s.title, ui.editLang, ev.target.value); touch(); } }),
      uiSeg([{ value: "timeline", label: t("Timeline"), tip: t("Title + dates + bullets") }, { value: "list", label: t("List"), tip: t("“Category: items”, one per line, good for skills") }],
        s.kind || "timeline", (kind) => { s.kind = kind; touch(); buildEditorKeepDraft(); }, { cls: "sm", label: t("Section type") }),
      h("button", { class: "btn icon sm ghost danger", disabled: used, title: used ? t("Some entries are still in this section") : t("Delete section"), onclick: () => { list.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
  });
  rows.append(h("button", { class: "btn sm", onclick: () => { list.push({ id: `section-${list.length + 1}`, title: t("New section"), kind: "timeline" }); touch(); buildEditorKeepDraft(); } }, icon("plus"), t("Add section")));

  const st = clone(doc().settings || {});
  const saveSettings = () => putSettings(st);
  const langEditor = resumeLangEditor(st, saveSettings);
  const roots = st.project_roots || [];
  const addRoot = async () => {
    let r;
    try { r = await api("POST", "/api/pick", { kind: "folder", initial: roots[0] || "" }); } catch (err) { toast(err.message); return; }
    const fresh = r.paths.filter((p) => !roots.includes(p));
    if (!fresh.length) return;
    st.project_roots = [...roots, ...fresh];
    saveSettings();
  };
  const rootRows = h("div", { class: "ev-list" }, roots.map((p, i) => h("div", { class: "ev-row same" },
    h("span", { class: "ev-ic" }, icon("folder")),
    h("div", { class: "ev-main" }, h("div", { class: "ev-name" }, h("b", null, p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p)), h("div", { class: "ev-path" }, p)),
    h("div", { class: "ev-tools", style: "opacity:1" },
      h("button", { class: "btn icon sm ghost", title: t("Show in the file manager"), onclick: () => api("POST", "/api/reveal-any", { path: p }) }, icon("external")),
      h("button", { class: "btn icon sm ghost danger", title: t("Remove (doesn't delete any files)"), onclick: () => { st.project_roots = roots.filter((_, j) => j !== i); if (!st.project_roots.length) delete st.project_roots; saveSettings(); } }, icon("x"))))));
  return [
    h("div", { class: "ed-head" }, h("h1", null, t("Sections & resume settings")), h("span", { class: "spacer" }), langSwitch()),
    card(t("Sections"), t("Order and ticks belong to “{job}”; new subsets start from ALL's order", { job: isAll() ? "ALL" : subsetName(subset()) }), [
      h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, t("Timeline = title + dates + bullets; List = “Category: items”, one per line, good for skills.")), rows]),
    card(t("Resume settings"), t("Saved in resume.yaml"), [
      h("div", { class: "field" }, h("label", null, t("Resume languages"), h("span", { class: "hint" }, t("Each language keeps its own text; the first one is the default for new subsets"))), langEditor),
      h("div", { class: "field" }, h("label", null, t("Export folder"), h("span", { class: "hint" }, t("relative to resume.yaml"))),
        h("input", { value: st.export_dir || "exports", spellcheck: "false", onchange: (ev) => { st.export_dir = ev.target.value.trim() || "exports"; saveSettings(); } })),
    ]),
    card(t("My project folders"), t("Where your own projects live, e.g. E:/Forge"), [
      h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, t("Claude will know your projects are here: ask it in the chat which ones are worth a place on your resume; picking a folder for an entry also starts here.")),
      rootRows,
      h("div", { class: "ev-actions" }, h("button", { class: "btn sm", onclick: addRoot }, icon("folder"), t("Choose folder")),
        roots.length && ui.chat.available ? h("button", { class: "btn sm ghost", onclick: () => sendChat(t("Look at my project folders ({folders}) and pick the projects most worth putting on my resume, with your reasons. Don't change resume.yaml yet; once I confirm, add them one at a time.", { folders: roots.join(", ") })) }, icon("sparkle"), t("Let Claude pick projects")) : null),
    ]),
  ];
}

// A job's own name, ID and headline. Which entries: ticks in the library; section order and
// visibility: Sections & resume settings; template, language, layout: above the preview.
function subsetForm() {
  const v = ui.draft;
  return [
    h("div", { class: "ed-head" }, h("h1", null, t("Subset settings")), h("span", { class: "id" }, v.id), h("span", { class: "spacer" }), langSwitch()),
    postingLink(v),
    card(t("Subset"), t("Tick entries in the library on the left; template, language and layout are above the preview"), [
      h("div", { class: "grid2" }, tfield(t("Name"), v, "label", { placeholder: t("AI Agent Engineer") }), tfield("ID", v, "id", { hint: t("also the export file name") })),
      tfield(t("Headline / job target"), v, "headline", { i18n: true, placeholder: t("e.g. AI Agent Engineer · LLM tooling & full-stack") }),
    ]),
  ];
}

// ================================================================ AI polish (runs in the chat)
const AI_LABELS = { polish: "Polish", translate: "Another language", fit: "Fit on one line", sync: "Update from the project", batch_translate: "Fill in a language", batch_fit: "Fit on one line", batch_sync: "Update from the project",
  autofill_read: "Read a profile", autofill_apply: "Update a profile" };

function aiDiff(id) {
  const a = ui.ai[id];
  const before = a.snapshot;
  const after = entryById(id) || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null));
  const fmt = (v) => {
    const one = (x) => (x && typeof x === "object" ? Object.entries(x).map(([l, s]) => `[${l}] ${s === "" ? t("(hidden)") : s}`).join("\n   ") : String(x ?? ""));
    if (Array.isArray(v)) return v.map((x) => "• " + one(x)).join("\n");
    return one(v);
  };
  const names = { title: t("Name"), subtitle: t("Subtitle"), bullets: t("Bullets"), tech: t("Tech"), location: t("Location"), start: t("Start"), end: t("End"), date: t("Date"), link: t("Link"), notes: t("Fact layer"), evidence: t("Evidence paths") };
  const close = () => { delete ui.ai[id]; buildEditor(); };
  return card(keys.length ? t("Claude changed this entry") : t("Claude didn't change this entry"), `${a.action in AI_LABELS ? t(AI_LABELS[a.action]) : ""} · ${t("the chat explains why")}`, [
    keys.map((k) => [h("div", { class: "k" }, names[k] || k), h("div", { class: "before" }, fmt(before[k])), h("div", { class: "after" }, fmt(after[k]))]),
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "btn sm", onclick: close }, icon("check"), keys.length ? t("Keep") : t("Got it")),
      keys.length && h("button", { class: "btn sm danger-soft", onclick: () => undoAi(id) }, icon("undo"), t("Undo, back to before")),
      h("button", { class: "btn sm ghost", onclick: () => setTab("chat") }, icon("chat"), t("Open the chat"))),
  ], { cls: "diff" });
}

async function undoAi(id) {
  const snap = ui.ai[id].snapshot;
  try {
    const s = await api("PUT", "/api/item", { kind: "entry", id: snap.id, data: snap, base: itemHash("entry", snap.id) });
    delete ui.ai[id];
    applyState(s);
    buildEditor({ flash: true });
    scheduleRender();
    toast(t("Restored to before the change"));
  } catch (e) { toast(t("Undo failed: {error}", { error: e.message })); }
}

// one entry: snapshot it first so the result can be compared and undone
async function aiEntry(action, entryId, extra = {}) {
  await saveDraft();
  const snapshot = clone(entryById(entryId));
  delete ui.ai[entryId];
  const ok = await sendChat(extra.text || "", { action, entryId, target: extra.target, lang: extra.lang });
  if (ok) ui.ai[entryId] = { chat: ui.chat.id, action, snapshot, done: false };
}

// a whole subset: snapshot every entry; each one Claude touches gets its own diff + undo
async function aiBatch(action, extra = {}) {
  await saveDraft();
  const snaps = Object.fromEntries(entries().map((e) => [e.id, clone(e)]));
  const ok = await sendChat("", { action, target: extra.target, lang: extra.lang });
  if (ok) ui.batch = { chat: ui.chat.id, action, snaps };
}

function finishAi(chatId) {
  const touched = [];
  for (const [id, a] of Object.entries(ui.ai)) if (a.chat === chatId && !a.done) { a.done = true; touched.push(id); }
  if (ui.batch && ui.batch.chat === chatId) {
    const { snaps, action } = ui.batch;
    ui.batch = null;
    for (const e of entries()) {
      const before = snaps[e.id];
      if (before && JSON.stringify(before) !== JSON.stringify(e)) { ui.ai[e.id] = { chat: chatId, action, snapshot: before, done: true }; touched.push(e.id); }
    }
    if (!touched.length) { toast(t("Claude didn't change any entry this time; see the chat")); return; }
  }
  if (!touched.length) return;
  const open = ui.sel.kind === "entry" && touched.includes(ui.sel.id);
  if (open && !ui.dirty) buildEditor();
  const first = touched.find((id) => id !== ui.sel.id) || touched[0];
  if (!open || touched.length > 1) {
    const name = display((entryById(first) || {}).title) || entryLabel(entryById(first));
    toast(touched.length > 1 ? t("Claude changed {n} entries; open each to see what changed and undo it", { n: touched.length }) : t("Claude finished “{name}”", { name }),
      { ms: 6000, action: { label: t("View"), fn: () => select("entry", first) } });
  }
}

// ---- language checks (mirror resume/model.py missing_text) ----
const CJK = /[぀-ヿ㐀-鿿가-힯]/;
function lacks(v, lang, bullet = false) {
  if (v && typeof v === "object") {
    const others = Object.entries(v).some(([k, x]) => k !== lang && x);
    return others && (bullet ? !(lang in v) : !v[lang]);
  }
  if (bullet && v) return !!langInfo(lang).cjk !== CJK.test(String(v));
  return false;
}
function missingIn(e, lang) {
  return ["title", "subtitle", "location", "date"].some((k) => lacks(e[k], lang)) || (e.bullets || []).some((b) => lacks(b, lang, true));
}

// ---- line measurement ----
function inPreview(id) {
  const v = subset();
  if (!v) return false;
  const e = entryById(id);
  if (!e || (v.hide_sections || []).includes(e.section)) return false;
  return v.id === ALL || (v.entries || []).includes(id);
}
// {bulletIndex: {n, fill}}; undefined = not measured yet; null = not in the preview, cannot be measured
function measuredFor(id, lang) {
  if (ui.linesFor[lang] !== ui.subsetId) return undefined;
  if (!inPreview(id)) return null;
  return (ui.lines[lang] || {})[id] || {};
}
function longBullets(lang = (subset() || {}).lang) {
  if (ui.linesFor[lang] !== ui.subsetId) return [];
  const out = [];
  for (const [id, by] of Object.entries(ui.lines[lang] || {})) {
    for (const [i, x] of Object.entries(by)) if (x.n > 1) out.push({ id, index: +i, ...x });
  }
  return out;
}

// the measurement changed: update chips and notes in place (never rebuild the form under the cursor)
function paintLines() {
  renderIssues();
  if (ui.sel.kind !== "entry" || !ui.draft) return;
  const e = ui.draft, L = ui.editLang, m = measuredFor(e.id, L);
  for (const chip of document.querySelectorAll("#bullets .line-chip")) {
    const i = +chip.dataset.line;
    chip.replaceWith(lineChip(i, m, bulletState(e.bullets[i], L)));
  }
  const notes = $("#bullet-notes");
  if (notes) notes.replaceChildren(...bulletNotes(e));
}

// ================================================================ chat: see chat.js

// ================================================================ right: preview
const SLIDERS = [
  ["font_size", "Font size", 8, 13, 0.1, "pt"],
  ["line_spread", "Line spacing", 0.8, 1.4, 0.01, "×"],
  ["margin_x", "Side margins", 0.4, 3, 0.05, "cm"],
  ["margin_y", "Top & bottom margins", 0.4, 3, 0.05, "cm"],
  ["section_sep", "Section spacing", 0, 20, 0.5, "pt"],
  ["entry_sep", "Entry spacing", 0, 14, 0.5, "pt"],
  ["item_sep", "Bullet spacing", 0, 8, 0.25, "pt"],
];

function layoutValue(key) {
  const v = subset();
  const L = (v && v.layout) || {};
  return L[key] ?? S.layout_defaults[key];
}

let layoutBuiltFor = null;
function renderPreviewToolbar() {
  const v = subset();
  ui.w.tpl.setOptions((S.templates || []).map((tp) => ({ value: tp.id, label: tp.name, desc: t(tp.description) })), v ? v.template : null);
  ui.w.tpl.disabled = !v;
  const shown = [...new Set([...langs(), ...(v && v.lang ? [v.lang] : [])])];
  $("#lang-seg").replaceChildren(...shown.map((l) => h("button", { class: v && v.lang === l ? "on" : "", disabled: !v,
    title: v ? t("Preview and export in {lang}", { lang: langName(l) }) : "", onclick: () => mutateSubset((x) => { x.lang = l; }) }, langShort(l))),
    h("button", { class: "seg-add", title: t("Add or manage resume languages"), "aria-label": t("Add or manage resume languages"), "aria-haspopup": "menu",
      onclick: (e) => openLangMenu(e.currentTarget) }, icon("plus")));
  $("#layout-toggle").classList.toggle("on", ui.layoutOpen);
  $("#layout-toggle").disabled = !v;
  $("#export-btn").disabled = !v;
  $("#layout-panel").classList.toggle("hidden", !ui.layoutOpen || !v);
  if (!v) { layoutBuiltFor = null; return; }
  if (layoutBuiltFor !== v.id) buildLayoutPanel();
  updateLayoutPanel();
  renderIssues();
}

function buildLayoutPanel() {
  layoutBuiltFor = ui.subsetId;
  const reset = (key) => h("button", { class: "btn icon sm ghost", title: t("Reset to default"), "data-reset": key, onclick: () => queueLayout(key, undefined, 0) }, icon("undo"));
  ui.w.accent = colorField({
    value: layoutValue("accent"), onChange: (c) => queueLayout("accent", c, 0), onReset: () => queueLayout("accent", undefined, 0),
    isDefault: () => !((subset() || {}).layout || {}).accent,
  });
  $("#layout-panel").replaceChildren(
    ...SLIDERS.map(([key, label, min, max, step, unit]) => {
      const val = h("span", { class: "val" });
      const input = h("input", { type: "range", min, max, step, "data-key": key, "aria-label": t(label),
        oninput: (ev) => { val.textContent = ev.target.value + unit; queueLayout(key, parseFloat(ev.target.value)); } });
      return h("div", { class: "ctl" }, h("span", null, t(label)), input, val, reset(key));
    }),
    h("div", { class: "ctl" }, h("span", null, t("Accent colour")), ui.w.accent.el, h("span"), reset("accent")),
    h("div", { class: "ctl" }, h("span", null, t("Paper")), h("div", { id: "ctl-paper" }), h("span"), reset("paper")),
    fontControl("font", t("Latin font"), reset),
    fontControl("cjk_font", t("CJK font"), reset));
}

// ---- font pickers: installed fonts from the server, plus "Custom…" for any other font name
const CUSTOM_FONT = "\u0000custom";
const FONT_GROUPS = { serif: "Serif", sans: "Sans serif", kai: "Kai / Fangsong", fang: "Kai / Fangsong" };
const CJK_FONT_LANGS = { zh: "简体中文", "zh-tw": "繁體中文", ja: "日本語", ko: "한국어" };
ui.fontPick = {};
function fontControl(key, label, reset) {
  const input = h("input", { type: "text", class: "hidden", id: `ctl-${key}-custom`, spellcheck: "false", "aria-label": t("{label} (custom)", { label }),
    placeholder: t("Name of an installed font"), onchange: (ev) => { ev.target.classList.add("hidden"); queueLayout(key, ev.target.value.trim() || undefined, 0); } });
  const sel = uiSelect({ id: `ctl-${key}`, label, menuWidth: 320,
    onChange: (v) => {
      if (v === CUSTOM_FONT) { input.classList.remove("hidden"); input.value = ""; input.focus(); return; }
      input.classList.add("hidden");
      queueLayout(key, v || undefined, 0);
    } });
  ui.fontPick[key] = { sel, input };
  return h("div", { class: "ctl" }, h("span", null, label), h("div", { class: "font-pick" }, sel.el, input), reset(key));
}

function updateFontControls(v) {
  const F = S.fonts || { latin: [], cjk: [] };
  const info = langInfo(v.lang);
  const script = info.script || "latin";
  const wide = info.cjk || script === "greek" || script === "cyrillic";
  const cjkDefault = (F.default_cjk_by_script || {})[info.cjk ? script : "zh"] || F.default_cjk;
  const defaults = {
    font: t("Default: {font}", { font: wide ? (F.default_latin_cjk || "Latin Modern Roman") : "Latin Modern Roman" }),
    cjk_font: t("Default: {font}", { font: (F.cjk.find((f) => f.name === cjkDefault) || { label: cjkDefault }).label }),
  };
  const fontGroup = (f) => (f.lang ? `${CJK_FONT_LANGS[f.lang] || f.lang} · ` : "") + t(FONT_GROUPS[f.kind] || "Serif");
  for (const [key, list] of [["font", F.latin], ["cjk_font", F.cjk]]) {
    const pick = ui.fontPick[key];
    if (!pick) continue;
    const cur = (v.layout || {})[key] || null;
    const known = list.some((f) => f.name === cur);
    const mine = key === "cjk_font" && info.cjk ? [...list.filter((f) => f.lang === script), ...list.filter((f) => f.lang !== script)] : list;
    const opts = [{ value: null, label: defaults[key], desc: key === "font" && wide ? t("A serif that covers this language, matched in weight") : t("Follows the template") },
      ...mine.map((f) => ({ value: f.name, label: f.label, hint: f.label !== f.name ? f.name : "", desc: f.desc ? t(f.desc) : "", group: fontGroup(f) }))];
    if (cur && !known) opts.push({ value: cur, label: cur, desc: t("Custom font"), group: t("Custom") });
    opts.push({ value: CUSTOM_FONT, label: t("Custom…"), desc: t("Type the name of any font installed on this computer"), group: t("Custom") });
    pick.sel.setOptions(opts, cur);
    if (document.activeElement !== pick.input) pick.input.classList.add("hidden");
  }
}

function updateLayoutPanel() {
  const v = subset();
  if (!v) return;
  const active = document.activeElement;
  const set = (el, value) => { if (el && el !== active) el.value = value; };
  const paper = layoutValue("paper");
  $("#ctl-paper").replaceChildren(uiSeg([{ value: "a4", label: "A4", tip: t("21 × 29.7 cm, used in most of the world") }, { value: "letter", label: "Letter", tip: t("8.5 × 11 in, used in North America") }],
    paper, (p) => queueLayout("paper", p, 0), { label: t("Paper") }));
  updateFontControls(v);
  if (ui.w.accent) ui.w.accent.value = layoutValue("accent");
  for (const [key, , , , , unit] of SLIDERS) {
    const input = document.querySelector(`#layout-panel input[data-key="${key}"]`);
    if (!input) continue;
    set(input, layoutValue(key));
    syncRange(input);
    input.nextSibling.textContent = (input === active ? input.value : layoutValue(key)) + unit;
  }
  for (const b of document.querySelectorAll("#layout-panel [data-reset]")) b.disabled = !((v.layout || {})[b.dataset.reset] !== undefined);
}

const pendingLayout = {};
let layoutTimer = null;
function queueLayout(key, value, delay = 300) {
  pendingLayout[key] = value;
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => {
    const patch = { ...pendingLayout };
    for (const k in pendingLayout) delete pendingLayout[k];
    mutateSubset((x) => {
      x.layout = x.layout || {};
      for (const [k, val] of Object.entries(patch)) { if (val === undefined) delete x.layout[k]; else x.layout[k] = val; }
    }).then(updateLayoutPanel);
  }, delay);
}

function scheduleRender(delay = 600) {
  clearTimeout(ui.render.timer);
  ui.render.timer = setTimeout(doRender, delay);
}

async function doRender() {
  const r = ui.render;
  if (!ui.subsetId) { clearPages(); setBadge(null); return; }
  if (r.busy) { r.again = true; return; }
  r.busy = true;
  const vid = ui.subsetId;
  // an unchanged resume comes back at once without compiling: don't flash "Rendering" for it
  // (another job's badge is wrong right away, so that one is cleared at once)
  const busyTimer = setTimeout(() => setBadge({ busy: true }), r.last && r.last.subset === vid ? 250 : 0);
  try {
    const res = await api("POST", `/api/subsets/${encodeURIComponent(vid)}/render`);
    clearTimeout(busyTimer);
    if (vid === ui.subsetId) {
      r.last = res;
      if (res.ok) { ui.lines[res.lang] = res.lines || {}; ui.linesFor[res.lang] = vid; ui.missing = res.missing || []; }
      await showPages(res);
      setBadge(res);
      paintLines();
    }
    await measureEditLang(vid);
  } catch (e) {
    clearTimeout(busyTimer);
    setBadge({ ok: false, errors: [e.message] });
  } finally {
    r.busy = false;
    if (r.again) { r.again = false; doRender(); }
  }
}

// The preview shows one language; when the form edits the other one, measure that too
// (same template and layout, no images), so every bullet's line count matches what is typed.
async function measureEditLang(vid) {
  const v = subset();
  const L = ui.editLang;
  if (!v || v.id !== vid || langs().length < 2 || L === v.lang) return;
  try {
    const res = await api("POST", `/api/subsets/${encodeURIComponent(vid)}/measure`, { lang: L });
    if (vid === ui.subsetId && res.ok) { ui.lines[L] = res.lines || {}; ui.linesFor[L] = vid; paintLines(); }
  } catch (_) { /* the preview's own errors are shown already */ }
}

// The strip under the toolbar: what still needs attention in this subset, each with a fix.
function renderIssues() {
  const box = $("#issues");
  const v = subset();
  if (!v || !S) { box.classList.add("hidden"); return; }
  const lang = v.lang;
  const long = longBullets(lang);
  const missing = (ui.missing || []).filter((m) => entryById(m.id));
  const items = [];
  if (long.length) {
    items.push(h("button", { class: "issue warn", "aria-haspopup": "menu", onclick: (ev) => openMenu(ev.currentTarget, [
      { header: t("{lang} bullets that run past one line", { lang: langName(lang) }) },
      ...long.map((b) => { const e = entryById(b.id); return { label: t("{name} · bullet {n}", { name: display(e.title) || entryLabel(e), n: b.index + 1 }), hint: `${t("{n} line", { n: b.n }, "{n} lines")} · ${Math.round(b.fill * 100)}%`, icon: "compress", onClick: () => jumpToBullet(b.id, b.index, lang) }; }),
      "sep",
      { label: t("Let Claude fit them all on one line"), icon: "sparkle", disabled: !ui.chat.available, onClick: () => aiBatch("batch_fit") },
    ], { minWidth: 280 }) }, icon("compress"), t("{n} bullet runs past one line", { n: long.length }, "{n} bullets run past one line")));
  }
  if (missing.length && langs().length > 1) {
    items.push(h("button", { class: "issue", "aria-haspopup": "menu", onclick: (ev) => openMenu(ev.currentTarget, [
      { header: t("These entries lack {lang} and show another language for now", { lang: langName(lang) }) },
      ...missing.map((m) => { const e = entryById(m.id); return { label: display(e.title) || entryLabel(e), hint: m.bullets && m.bullets.length ? t("{n} bullet", { n: m.bullets.length }, "{n} bullets") : t("title etc."), icon: "translate", onClick: () => { ui.editLang = lang; select("entry", m.id); } }; }),
      "sep",
      { label: t("Let Claude fill in the {lang} version", { lang: langName(lang) }), icon: "sparkle", disabled: !ui.chat.available, onClick: () => aiBatch("batch_translate", { target: lang }) },
    ], { minWidth: 280 }) }, icon("translate"), t("{n} entry lacks {lang}", { n: missing.length, lang: langName(lang) }, "{n} entries lack {lang}")));
  }
  box.replaceChildren(...items);
  box.classList.toggle("hidden", !items.length);
}

async function jumpToBullet(id, index, lang) {
  if (langs().includes(lang)) ui.editLang = lang;
  await select("entry", id);
  const ta = document.querySelector(`#bullets textarea[data-bullet="${index}"]`);
  if (ta) { ta.scrollIntoView({ block: "center", behavior: "smooth" }); ta.focus(); ta.closest(".bullet").classList.add("pulse"); }
}

function setBadge(res) {
  const b = $("#page-badge");
  const errs = $("#render-errors");
  errs.classList.add("hidden");
  if (!res) { b.className = "badge"; b.textContent = ""; return; }
  if (res.busy) { b.className = "badge busy"; b.textContent = t("Rendering"); return; }
  if (res.ok && res.subset === ALL) {
    b.className = "badge info";
    b.replaceChildren(`${t("All entries")} · ${t("{n} page", { n: res.pages }, "{n} pages")}`);
    setTip(b, t("ALL isn't limited to one page; make the resumes you send as subsets"));
  } else if (res.ok) {
    const one = res.pages === 1;
    b.className = "badge " + (one ? "ok" : "bad");
    b.replaceChildren(icon(one ? "check" : "alert"), one ? t("{n} page", { n: 1 }, "{n} pages") : `${t("{n} page", { n: res.pages }, "{n} pages")} · ${t("over one page")}`);
    setTip(b, one ? t("Rendered in {s}s", { s: res.seconds }) : t("Try a smaller font size, margins or spacing under Layout, or one entry fewer in this subset"));
  } else {
    b.className = "badge bad";
    b.replaceChildren(icon("alert"), t("Rendering failed"));
    errs.textContent = (res.errors || []).join("\n");
    errs.classList.remove("hidden");
  }
}

function clearPages() { $("#pages").replaceChildren(); }

function paperWidthPx() {
  const inches = layoutValue("paper") === "letter" ? 8.5 : 8.27;
  return inches * 96;
}

function pageWidth() {
  if (ui.zoom === "fit") return Math.max(240, $("#canvas").clientWidth - 48);
  return paperWidthPx() * ui.zoom;
}

function applyZoom() {
  const w = pageWidth();
  for (const p of document.querySelectorAll("#pages .page")) p.style.width = w + "px";
  $("#zoom-fit").textContent = ui.zoom === "fit" ? t("Fit") : Math.round(ui.zoom * 100) + "%";
}

function setZoom(z) {
  if (z === "fit") ui.zoom = "fit";
  else ui.zoom = Math.min(3, Math.max(0.3, Math.round(z * 10) / 10));
  store("zoom", String(ui.zoom));
  applyZoom();
}

function currentZoomFactor() { return ui.zoom === "fit" ? pageWidth() / paperWidthPx() : ui.zoom; }

async function showPages(res) {
  const box = $("#pages");
  if (!res.ok) return; // keep the last good preview on errors
  if (res.cached && box.dataset.subset === res.subset && box.children.length) return; // same pages already shown
  box.dataset.subset = res.subset;
  const vid = encodeURIComponent(res.subset);
  if (!res.images) { // no pdftoppm: fall back to the browser's PDF viewer
    box.replaceChildren(h("iframe", { class: "pdf-frame", src: `/api/subsets/${vid}/pdf?t=${res.stamp}#view=FitH`, style: "height:calc(100vh - 150px)" }));
    return;
  }
  const imgs = await Promise.all(Array.from({ length: res.images }, (_, i) => new Promise((resolve) => {
    const img = new Image();
    img.alt = t("Page {n}", { n: i + 1 });
    img.onload = img.onerror = () => resolve(img);
    img.src = `/api/subsets/${vid}/page/${i + 1}?t=${res.stamp}`;
  })));
  const w = pageWidth();
  const all = res.subset === ALL;
  box.replaceChildren(...imgs.map((img, i) => h("div", { class: "page" + (i > 0 && !all ? " overflow" : ""), style: `width:${w}px` },
    res.images > 1 ? h("span", { class: "plabel" }, i === 0 || all ? t("Page {n}", { n: i + 1 }) : `${t("Page {n}", { n: i + 1 })} · ${t("over one page")}`) : null, img)));
}

async function exportPdf() {
  await saveDraft();
  $("#export-btn").disabled = true;
  try {
    const r = await api("POST", `/api/subsets/${encodeURIComponent(ui.subsetId)}/export`);
    if (!r.ok) { setBadge(r); toast(t("Export failed; see the error above the preview")); return; }
    setBadge(r);
    await showPages(r);
    const file = r.exported.pdf.split(/[\\/]/).pop();
    toast(r.pages > 1 ? t("Exported {file} (note: {n} pages)", { file, n: r.pages }) : t("Exported {file}", { file }),
      { ms: 6000, action: { label: t("Open folder"), fn: () => api("POST", "/api/reveal", { path: r.exported.pdf }) } });
  } catch (e) { toast(t("Export failed: {error}", { error: e.message })); }
  finally { $("#export-btn").disabled = !subset(); }
}

// ================================================================ layout: resizable panes
function initGutters() {
  const ws = $("#workspace");
  const load = (k, d) => { const v = parseFloat(store(k)); return Number.isFinite(v) ? v : d; };
  const setLeft = (px) => { ws.style.setProperty("--w-left", px + "px"); };
  const setRight = (px) => { ws.style.setProperty("--w-right", px + "px"); };
  const lw = load("w-left", 0), rw = load("w-right", 0);
  if (lw) setLeft(lw);
  if (rw) setRight(rw);
  for (const g of document.querySelectorAll(".gutter")) {
    const side = g.dataset.gutter;
    g.addEventListener("dblclick", () => {
      ws.style.removeProperty(side === "left" ? "--w-left" : "--w-right");
      store(side === "left" ? "w-left" : "w-right", null);
      applyZoom();
    });
    g.addEventListener("mousedown", (e) => {
      e.preventDefault();
      g.classList.add("drag");
      document.body.classList.add("resizing");
      const move = (ev) => {
        const W = window.innerWidth;
        if (side === "left") { const px = Math.min(560, Math.max(240, ev.clientX)); setLeft(px); store("w-left", px); }
        else { const px = Math.min(W - 700, Math.max(340, W - ev.clientX)); setRight(px); store("w-right", px); }
        applyZoom();
      };
      const up = () => { g.classList.remove("drag"); document.body.classList.remove("resizing"); window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }
  new ResizeObserver(() => { if (ui.zoom === "fit") applyZoom(); }).observe($("#canvas"));
}

// ================================================================ wiring
function connect() {
  const es = new EventSource("/api/events");
  // nothing to show while connected; a strip only when the background service is gone. The short
  // delay keeps it from flashing when the page reloads or the network hiccups.
  const bar = $("#offline");
  let timer = null;
  es.onopen = () => { clearTimeout(timer); timer = null; bar.classList.add("hidden"); if (S) { refresh(); checkEvidence(); } };
  es.onerror = () => {
    if (timer || !bar.classList.contains("hidden")) return;
    timer = setTimeout(() => {
      timer = null;
      bar.replaceChildren(icon("alert"), h("span", null, t("Lost the connection to Resume Studio's background service; reconnecting… Changes you make now may not be saved.")));
      bar.classList.remove("hidden");
    }, 1500);
  };
  es.addEventListener("file", (m) => {
    jobsAfterResumeChange();   // the window's own saves too: the tool's CV, subset ranking, profile freshness
    fillAfterResumeChange();   // what each online profile should say
    const d = JSON.parse(m.data);
    if (S && d.digest === S.digest) return;
    refresh();
  });
  es.addEventListener("evidence", () => checkEvidence(true));
  listenChat(es);
  listenJobs(es);
  listenFill(es);
}

function wire() {
  initTips();
  initContextMenu();
  for (const b of document.querySelectorAll(".tab")) b.onclick = () => setTab(b.dataset.tab);

  ui.w.subset = uiSelect({ id: "subset-select", cls: "subset", label: t("ALL or a subset"), menuWidth: 300,
    title: t("ALL = every entry; the others are subsets of it"), onChange: (v) => switchSubset(v) });
  $("#subset-slot").replaceWith(ui.w.subset.el);
  $("#subset-menu-btn").append(icon("more"));
  $("#subset-menu-btn").onclick = openSubsetMenu;

  ui.w.tpl = uiSelect({ id: "tpl-select", label: t("Template"), menuWidth: 300, onChange: (tp) => mutateSubset((x) => { x.template = tp; }) });
  $("#tpl-slot").replaceWith(ui.w.tpl.el);
  $("#layout-toggle").prepend(icon("sliders"));
  $("#settings-btn").append(icon("settings"));
  $("#settings-btn").onclick = openSettings;
  $("#layout-toggle").onclick = () => { ui.layoutOpen = !ui.layoutOpen; store("layoutOpen", ui.layoutOpen ? "1" : "0"); renderPreviewToolbar(); };
  $("#zoom-in").append(icon("zoomIn"));
  $("#zoom-out").append(icon("zoomOut"));
  $("#zoom-in").onclick = () => setZoom(currentZoomFactor() + 0.1);
  $("#zoom-out").onclick = () => setZoom(currentZoomFactor() - 0.1);
  $("#zoom-fit").onclick = () => setZoom(ui.zoom === "fit" ? 1 : "fit");
  $("#export-btn").onclick = exportPdf;

  wireChat();

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveDraft(); }
    if ((e.ctrlKey || e.metaKey) && e.key === ",") { e.preventDefault(); openSettings(); }
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && ["1", "2", "3"].includes(e.key)) { e.preventDefault(); setWorkspace({ 1: "resume", 2: "jobs", 3: "fill" }[e.key]); }
    if (e.key === "Escape" && !Float.cur && ui.layoutOpen) { ui.layoutOpen = false; renderPreviewToolbar(); }
  });
  window.addEventListener("beforeunload", () => { if (ui.dirty) saveDraft(); if (ui.headPending) ui.headPending(); });
  // projects change while the window is in the background: look again when the user comes back
  window.addEventListener("focus", () => checkEvidence(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkEvidence(true); });
  setInterval(() => { if (!document.hidden) checkEvidence(); }, 5 * 60 * 1000);
}

(async function main() {
  ui.zoom = store("zoom") && store("zoom") !== "fit" ? parseFloat(store("zoom")) : "fit";
  ui.layoutOpen = store("layoutOpen") === "1";
  wire();
  initGutters();
  applyState(await api("GET", "/api/state"));
  const switched = store("dataSwitched");
  if (switched) {
    store("dataSwitched", null);
    toast(switched === "new" ? t("Started a new data file: {path}", { path: S.data_path }) : t("Opened {path}", { path: S.data_path }), { ms: 5000 });
  }
  select("profile");
  setTab(["chat", "jobs"].includes(store("tab")) ? store("tab") : "library");
  initWorkspaces();
  connect();
  loadChats().catch(() => {});
  loadJobs({ quiet: true });
  loadClaudeInfo();
  checkEvidence();
  scheduleRender(0);
})();
