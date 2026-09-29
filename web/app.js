// Resume Studio front end. No build step: plain DOM + fetch + EventSource.
// resume.yaml on disk is the source of truth; this page keeps a draft of the
// item being edited, autosaves it, and follows changes made by Claude.
"use strict";

// ================================================================ helpers
const $ = (s, root = document) => root.querySelector(s);
const clone = (x) => JSON.parse(JSON.stringify(x ?? null));
const LANG_NAMES = { en: "EN", zh: "中文" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === true) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3.2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  sparkle: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  zoomIn: '<circle cx="11" cy="11" r="7"/><path d="M11 8v6M8 11h6M20 20l-4-4"/>',
  zoomOut: '<circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-4-4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i");
  s.innerHTML = ICONS[name] || "";
  return s;
}

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
  const t = $("#toast");
  t.replaceChildren(h("span", null, msg));
  if (action) t.append(h("button", { class: "btn sm", onclick: () => { t.classList.remove("show"); action.fn(); } }, action.label));
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
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
function modal({ title, message = "", input = null, ok = "确定", danger = false }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const field = input !== null ? h("input", { value: input, spellcheck: "false" }) : null;
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
          h("button", { class: "btn", onclick: () => close(field ? null : false) }, "取消"),
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
  versionId: null,
  sel: { kind: "profile", id: null },
  draft: null, formBase: null, dirty: false, editSeq: 0,
  saving: false, refreshAfterSave: false, saveTimer: null, conflict: null,
  editLang: null,
  render: { timer: null, busy: false, again: false, last: null },
  zoom: "fit",
  layoutOpen: false,
  polish: null, // {chat, entryId, snapshot, done}
  chat: { list: [], id: null, meta: null, messages: [], running: false, live: null, liveEl: null, available: false },
};

const doc = () => S.doc;
const langs = () => (doc().settings && doc().settings.languages) || ["en"];
const sections = () => doc().sections || [];
const entries = () => doc().entries || [];
const versions = () => doc().versions || [];
const version = () => versions().find((v) => v.id === ui.versionId) || null;
const entryById = (id) => entries().find((e) => e.id === id) || null;
const sectionById = (id) => sections().find((s) => s.id === id) || null;

function itemHash(kind, id) {
  if (!S) return null;
  if (kind === "entry" || kind === "version") return (S.hashes[kind] || {})[id] || null;
  return S.hashes[kind] || null;
}
function currentItem(kind, id) {
  if (kind === "entry") return entryById(id);
  if (kind === "version") return versions().find((v) => v.id === id) || null;
  return doc()[kind];
}

// Any text value is a plain string (all languages) or a map {en: "...", zh: "..."}.
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
  const L = (version() && version().lang) || langs()[0];
  return getT(v, L) || (v && typeof v === "object" ? Object.values(v).find(Boolean) : "") || "";
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function shortDate(v) {
  const s = display(v).trim();
  if (!s) return "";
  if (/^(present|now|至今)$/i.test(s)) return "至今";
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
  $("#datapath").textContent = s.data_path;
  $("#datapath").title = "数据文件：" + s.data_path;
  if (!versions().some((v) => v.id === ui.versionId)) {
    const saved = store("version");
    ui.versionId = versions().some((v) => v.id === saved) ? saved : (versions()[0] || {}).id || null;
  }
  if (!ui.editLang || !langs().includes(ui.editLang)) {
    ui.editLang = (version() && langs().includes(version().lang) && version().lang) || langs()[0];
  }
  ui.chat.available = !!(s.claude && s.claude.available);
  renderTopbar();
  renderLibrary();
  renderPreviewToolbar();
  renderComposerState();
}

// state changed elsewhere (Claude, another window): rebuild the form if it is stale
function syncEditor() {
  const { kind, id } = ui.sel;
  if ((kind === "entry" || kind === "version") && !currentItem(kind, id)) { select("profile"); return; }
  if (itemHash(kind, id) === ui.formBase) return;
  if (ui.dirty) { ui.conflict = { current: clone(currentItem(kind, id)) }; buildEditor(); return; }
  buildEditor({ flash: true });
}

function touch() {
  ui.dirty = true;
  ui.editSeq++;
  setSaveStatus("未保存");
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
  setSaveStatus("保存中…");
  try {
    const s = await api("PUT", "/api/item", { kind, id, data: ui.draft, base: force ? null : ui.formBase });
    if (kind === "entry" || kind === "version") ui.sel.id = ui.draft.id;
    if (kind === "version") { ui.versionId = ui.draft.id; store("version", ui.versionId); }
    applyState(s);
    ui.formBase = itemHash(ui.sel.kind, ui.sel.id);
    if (ui.conflict) { ui.conflict = null; buildEditorKeepDraft(); }
    if (ui.editSeq === seq) { ui.dirty = false; setSaveStatus("已保存"); }
    else ui.saveTimer = setTimeout(saveDraft, 300);
    scheduleRender();
  } catch (e) {
    if (e.status === 409) {
      ui.conflict = { current: e.data && e.data.current };
      buildEditorKeepDraft();
      setSaveStatus("有冲突", true);
    } else setSaveStatus("保存失败：" + e.message, true);
  } finally {
    ui.saving = false;
    if (ui.refreshAfterSave) { ui.refreshAfterSave = false; refresh(); }
  }
}

// version tweaks from the sidebar / preview toolbar: re-read, apply, retry once on conflict
async function mutateVersion(fn) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const v = clone(version());
    if (!v) return;
    fn(v);
    try {
      const s = await api("PUT", "/api/item", { kind: "version", id: ui.versionId, data: v, base: itemHash("version", ui.versionId) });
      applyState(s);
      syncEditor();
      scheduleRender(200);
      return;
    } catch (e) {
      if (e.status !== 409 || attempt) { toast("保存失败：" + e.message); return; }
      applyState(await api("GET", "/api/state"));
    }
  }
}

// ================================================================ top bar
function renderTopbar() {
  const sel = $("#version-select");
  sel.replaceChildren(...versions().map((v) => h("option", { value: v.id, selected: v.id === ui.versionId }, v.label ? `${v.label} · ${v.id}` : v.id)));
  if (!versions().length) sel.append(h("option", { value: "" }, "还没有版本"));
  sel.disabled = !versions().length;
}

function openVersionMenu() {
  const menu = $("#version-menu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  const v = version();
  const item = (label, ic, fn, cls = "") => h("button", { class: cls, role: "menuitem", onclick: () => { menu.classList.add("hidden"); fn(); } }, icon(ic), label);
  menu.replaceChildren(
    v && item("版本设置（名称、板块顺序）", "settings", () => select("version", ui.versionId)),
    item("新建空白版本", "plus", () => newVersion(null)),
    v && item("复制当前版本", "copy", () => newVersion(ui.versionId)),
    h("hr"),
    item("打开导出文件夹", "folder", revealExports),
    v && h("hr"),
    v && item("删除当前版本", "trash", deleteVersion, "danger"),
  );
  menu.classList.remove("hidden");
}

async function switchVersion(id) {
  await saveDraft();
  ui.versionId = id;
  store("version", id);
  const v = version();
  if (v && langs().includes(v.lang)) ui.editLang = v.lang;
  renderTopbar();
  renderLibrary();
  renderPreviewToolbar();
  renderComposerState();
  if (ui.sel.kind === "version") select("version", id); else buildEditorKeepDraft();
  clearPages();
  scheduleRender(0);
}

async function newVersion(copyFrom) {
  const id = await modal({ title: copyFrom ? "复制版本" : "新建版本", message: "版本 ID 会用作导出文件名，比如 RES-AI-EN。", input: copyFrom ? copyFrom + "-2" : "RES-NEW", ok: "创建" });
  if (!id) return;
  try {
    const r = await api("POST", "/api/versions", { id, copy_from: copyFrom });
    applyState(r);
    switchVersion(r.id);
    toast(`已创建版本 ${r.id}${copyFrom ? "" : "，在左侧勾选要放进去的经历"}`);
  } catch (e) { toast(e.message); }
}

async function deleteVersion() {
  if (!(await modal({ title: `删除版本 ${ui.versionId}？`, message: "只删除这个版本的配置（选了哪些经历、模板和版面），经历库本身不受影响。", ok: "删除", danger: true }))) return;
  applyState(await api("DELETE", "/api/versions/" + encodeURIComponent(ui.versionId)));
  ui.versionId = null;
  applyState(S);
  select("profile");
  clearPages();
  scheduleRender(0);
}

async function revealExports() {
  const dir = S.data_path.replace(/[\\/][^\\/]+$/, "") + "/" + ((doc().settings || {}).export_dir || "exports");
  const r = await api("POST", "/api/reveal", { path: dir }).catch(() => ({}));
  if (!r.ok) toast("还没有导出过，导出一次后就能打开这个文件夹");
}

// ================================================================ left: library
function setTab(tab) {
  ui.tab = tab;
  store("tab", tab);
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
  $("#library").classList.toggle("hidden", tab !== "library");
  $("#chat").classList.toggle("hidden", tab !== "chat");
  if (tab === "chat") { renderChat(); setTimeout(() => $("#chat-input").focus(), 0); }
}

function renderLibrary() {
  const lib = $("#library");
  const scroll = lib.scrollTop;
  lib.replaceChildren();
  const v = version();
  const nav = (kind, label, ic) => h("button", { class: "nav-row" + (ui.sel.kind === kind ? " on" : ""), onclick: () => select(kind) }, icon(ic), label);
  lib.append(nav("profile", "个人信息", "user"), nav("sections", "板块与设置", "grid"));

  const included = new Set(v ? v.entries || [] : []);
  const order = v ? v.entries || [] : [];
  for (const sec of sections()) {
    const mine = entries().filter((e) => e.section === sec.id);
    const inV = order.map(entryById).filter((e) => e && e.section === sec.id);
    const outV = mine.filter((e) => !included.has(e.id));
    lib.append(h("div", { class: "lib-section" },
      h("div", { class: "lib-head" },
        h("span", null, h("span", { class: "t" }, display(sec.title) || sec.id), mine.length ? h("span", { class: "n" }, v ? `${inV.length}/${mine.length}` : mine.length) : null),
        h("button", { class: "btn icon sm ghost", title: `在「${display(sec.title) || sec.id}」新增一条`, onclick: () => newEntry(sec.id) }, icon("plus"))),
      [...inV, ...outV].map((e, i) => entryRow(e, included.has(e.id), i, inV.length, !!v))));
  }
  const orphans = entries().filter((e) => !sectionById(e.section));
  if (orphans.length) lib.append(h("div", { class: "lib-section" }, h("div", { class: "lib-head" }, h("span", { class: "t" }, "未归类")), orphans.map((e) => entryRow(e, included.has(e.id), 0, 0, !!v))));

  if (!entries().length) {
    lib.append(h("div", { class: "empty" },
      h("div", { class: "t" }, "经历库还是空的"),
      h("div", null, "把全部真实经历都放进来，导出时再挑。"),
      h("div", { class: "acts" },
        h("button", { class: "btn primary sm", onclick: () => newEntry((sections().find((s) => s.id === "projects") || sections()[0] || {}).id || "projects") }, icon("plus"), "新增经历"),
        h("button", { class: "btn sm", onclick: () => startChatWith("请帮我把旧简历里的经历导入到 resume.yaml。旧简历在：") }, icon("sparkle"), "让 Claude 导入"))));
  }
  if (S.warnings && S.warnings.length) lib.append(h("div", { class: "warnings" }, S.warnings.map((w) => h("div", null, w))));
  lib.scrollTop = scroll;
}

function entryRow(e, isIn, idx, nIn, hasVersion) {
  const active = ui.sel.kind === "entry" && ui.sel.id === e.id;
  const meta = [entryMeta(e), e.id].filter(Boolean).join(" · ");
  return h("div", { class: "entry" + (isIn ? "" : " out") + (active ? " on" : ""), "data-id": e.id, onclick: () => select("entry", e.id) },
    hasVersion && h("button", {
      class: "check" + (isIn ? " on" : ""), role: "checkbox", "aria-checked": String(isIn),
      title: isIn ? "从当前版本移除" : "加入当前版本",
      onclick: (ev) => { ev.stopPropagation(); toggleInclude(e.id, !isIn); },
    }),
    h("span", { class: "e-main" }, h("span", { class: "e-title" }, display(e.title) || "（未命名）"), h("span", { class: "e-meta" }, meta)),
    isIn && h("span", { class: "e-tools" },
      h("button", { class: "btn icon sm ghost", title: "上移", disabled: idx === 0, onclick: (ev) => { ev.stopPropagation(); moveInVersion(e.id, -1); } }, icon("up")),
      h("button", { class: "btn icon sm ghost", title: "下移", disabled: idx >= nIn - 1, onclick: (ev) => { ev.stopPropagation(); moveInVersion(e.id, 1); } }, icon("down"))));
}

function toggleInclude(id, on) {
  mutateVersion((v) => {
    v.entries = (v.entries || []).filter((x) => x !== id);
    if (on) v.entries.push(id);
  });
}

function moveInVersion(id, dir) {
  mutateVersion((v) => {
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

async function newEntry(section) {
  await saveDraft();
  const r = await api("POST", "/api/entries", { section });
  applyState(r);
  if (version()) await mutateVersion((v) => { v.entries = [...(v.entries || []), r.id]; });
  select("entry", r.id);
  setTimeout(() => { const f = $("#editor input[data-key=title]"); if (f) f.focus(); }, 30);
}

// ================================================================ middle: editor
async function select(kind, id = null) {
  await saveDraft();
  ui.sel = { kind, id };
  ui.conflict = null;
  ui.dirty = false;
  renderLibrary();
  renderComposerState();
  buildEditor();
}

const BUILDERS = () => ({ entry: entryForm, profile: profileForm, sections: sectionsForm, version: versionForm });

function buildEditor(opts = {}) {
  const { kind, id } = ui.sel;
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
  if (ui.draft == null && ui.sel.kind !== "sections") wrap.append(h("p", { class: "muted" }, "没有内容。"));
  else wrap.append(...[BUILDERS()[ui.sel.kind]()].flat(Infinity).filter(Boolean));
  mid.replaceChildren(wrap);
  if (keepScroll) mid.scrollTop = scroll;
  if (flash) { wrap.classList.add("flash"); }
}

function conflictBanner() {
  return h("div", { class: "banner warn" }, icon("alert"),
    h("span", { style: "flex:1" }, "这一项刚被 Claude 或另一个窗口改过，而你这里还有没保存的修改。"),
    h("button", { class: "btn sm", onclick: () => { ui.conflict = null; ui.dirty = false; buildEditor({ flash: true }); } }, "载入最新版本"),
    h("button", { class: "btn sm danger-soft", onclick: () => saveDraft(true) }, "用我的覆盖"));
}

function langSwitch() {
  if (langs().length <= 1) return null;
  return h("div", { class: "seg", title: "编辑哪种语言的文字" }, langs().map((l) =>
    h("button", { class: l === ui.editLang ? "on" : "", onclick: () => { ui.editLang = l; buildEditorKeepDraft(); } }, LANG_NAMES[l] || l)));
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
    ? h("span", { class: "lang-tag" + (neutral ? " neutral" : ""), title: neutral ? "目前各语言共用这段文字；修改后会按语言分开保存" : "" }, neutral ? "通用" : LANG_NAMES[ui.editLang])
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

  if (ui.polish && ui.polish.entryId === e.id && ui.polish.done) out.push(aiDiff());

  out.push(h("div", { class: "ed-head" }, h("h1", null, display(e.title) || "（未命名）"), h("span", { class: "id" }, e.id), h("span", { class: "spacer" }), langSwitch()));

  const basics = [
    h("div", { class: "grid2" },
      h("div", { class: "field" }, h("label", null, "板块"),
        h("select", { onchange: (ev) => { e.section = ev.target.value; touch(); buildEditorKeepDraft(); } },
          sections().map((s) => h("option", { value: s.id, selected: s.id === e.section }, display(s.title) || s.id)))),
      tfield("ID", e, "id", { placeholder: "EXP-001", hint: "版本靠它引用" })),
    tfield(isList ? "类别名" : "名称", e, "title", { i18n: true, placeholder: isList ? "比如：编程语言" : "公司 / 学校 / 项目名" }),
  ];
  if (!isList) {
    basics.push(
      h("div", { class: "grid2" }, tfield("副标题", e, "subtitle", { i18n: true, placeholder: "职位 / 学位 / 角色" }), tfield("地点", e, "location", { i18n: true })),
      h("div", { class: "grid3" }, tfield("开始", e, "start", { placeholder: "2025-07" }), tfield("结束", e, "end", { placeholder: "2026-02 或 present" }),
        tfield("自定义日期", e, "date", { i18n: true, hint: "可选，覆盖左边" })),
      tfield("链接", e, "link", { placeholder: "https://…" }));
  }
  basics.push(h("div", { class: "field" }, h("label", null, isList ? "条目" : "技术 / 关键词", h("span", { class: "hint" }, "用逗号分隔")),
    h("input", { value: e.tech.map((t) => display(t)).join(", "), spellcheck: "false",
      oninput: (ev) => { e.tech = ev.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean); touch(); } })));
  out.push(card("基本信息", null, basics));

  if (!isList) out.push(card("要点", langs().length > 1 ? `正在编辑 ${LANG_NAMES[ui.editLang]}` : null, bulletsEditor(e),
    { actions: h("button", { class: "btn sm ghost", onclick: () => $("#polish-box").classList.toggle("hidden"), disabled: !ui.chat.available, title: ui.chat.available ? "" : "没有找到 claude 命令" }, icon("sparkle"), "让 Claude 润色") }));

  out.push(card("事实层", "只给 Claude 看，永远不会出现在简历上", [
    h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, "写真实情况：哪些是你做的、哪些是 AI 写的、数字怎么来的、不好意思写进简历的实情。Claude 润色时只在这里能支撑的范围内包装。"),
    tfield("实际情况 / 原始想法", e, "notes", { multiline: true, rows: 6 }),
    h("div", { class: "field" }, h("label", null, "证据路径", h("span", { class: "hint" }, "每行一个文件夹或文件，Claude 需要时会去读")),
      h("textarea", { rows: 2, placeholder: "E:/Forge/Paper2Exam", spellcheck: "false",
        oninput: (ev) => { e.evidence = ev.target.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean); touch(); } }, e.evidence.join("\n"))),
  ], { cls: "fact" }));

  out.push(h("div", { class: "danger-zone" }, h("button", { class: "btn sm ghost danger", onclick: () => deleteEntry(e.id) }, icon("trash"), "删除这条经历")));
  return out;
}

function bulletsEditor(e) {
  const box = h("div", { id: "bullets" });
  const redraw = () => { const nb = bulletsEditor(e); box.replaceWith(nb); };
  e.bullets.forEach((b, i) => {
    box.append(h("div", { class: "bullet" }, h("span", { class: "dot" }),
      h("textarea", { rows: 2, spellcheck: "false", "data-bullet": i, oninput: (ev) => { e.bullets[i] = setT(e.bullets[i], ui.editLang, ev.target.value); touch(); } }, getT(b, ui.editLang)),
      h("div", { class: "b-tools" },
        h("button", { class: "btn icon sm ghost", title: "上移", disabled: i === 0, onclick: () => { [e.bullets[i - 1], e.bullets[i]] = [e.bullets[i], e.bullets[i - 1]]; touch(); redraw(); } }, icon("up")),
        h("button", { class: "btn icon sm ghost", title: "下移", disabled: i === e.bullets.length - 1, onclick: () => { [e.bullets[i + 1], e.bullets[i]] = [e.bullets[i], e.bullets[i + 1]]; touch(); redraw(); } }, icon("down")),
        h("button", { class: "btn icon sm ghost danger", title: "删除这条要点（所有语言）", onclick: () => { e.bullets.splice(i, 1); touch(); redraw(); } }, icon("x")))));
  });
  if (!e.bullets.length) box.append(h("p", { class: "muted", style: "margin:0 0 8px" }, "还没有要点。可以自己写，也可以先在事实层写清楚真实情况，再让 Claude 起草。"));
  const instr = h("input", { placeholder: "补充要求（可选），比如：更突出架构设计；第二条太夸张了", spellcheck: "false",
    onkeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) polish(e.id, instr.value); } });
  box.append(
    h("div", { class: "row wrap" },
      h("button", { class: "btn sm", onclick: () => { e.bullets.push(""); touch(); redraw(); setTimeout(() => { const t = document.querySelectorAll("#bullets textarea"); if (t.length) t[t.length - 1].focus(); }, 0); } }, icon("plus"), "添加要点"),
      h("span", { class: "muted", style: "font-size:12px" }, "支持 **加粗**、*斜体*、[文字](链接)")),
    h("div", { id: "polish-box", class: "polish-box hidden" }, instr,
      h("button", { class: "btn primary sm", onclick: () => polish(e.id, instr.value) }, icon("sparkle"), "开始润色")));
  return box;
}

async function deleteEntry(id) {
  if (!(await modal({ title: `删除 ${id}？`, message: "它会从经历库和所有版本里移除。需要时可以从 git 或 .studio/history 找回。", ok: "删除", danger: true }))) return;
  ui.dirty = false;
  applyState(await api("DELETE", "/api/entries/" + encodeURIComponent(id)));
  select("profile");
  scheduleRender();
}

function profileForm() {
  const p = ui.draft;
  p.contacts = p.contacts || [];
  const rows = h("div", { class: "list-rows" });
  p.contacts.forEach((c, i) => {
    rows.append(h("div", { class: "lr", style: "grid-template-columns: 96px 1fr 1fr 30px" },
      h("input", { value: c.label ?? "", placeholder: "email", spellcheck: "false", oninput: (ev) => { c.label = ev.target.value; touch(); } }),
      h("input", { value: getT(c.value, ui.editLang), placeholder: "显示的文字", spellcheck: "false", oninput: (ev) => { c.value = ev.target.value; touch(); } }),
      h("input", { value: c.url ?? "", placeholder: "链接（可选）mailto: / https://", spellcheck: "false", oninput: (ev) => { c.url = ev.target.value; touch(); } }),
      h("button", { class: "btn icon sm ghost danger", title: "删除", onclick: () => { p.contacts.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
  });
  rows.append(h("button", { class: "btn sm", onclick: () => { p.contacts.push({ label: "", value: "", url: "" }); touch(); buildEditorKeepDraft(); } }, icon("plus"), "添加联系方式"));
  return [
    h("div", { class: "ed-head" }, h("h1", null, "个人信息"), h("span", { class: "spacer" }), langSwitch()),
    card("基本信息", null, [tfield("姓名", p, "name", { i18n: true }), tfield("一句话介绍 / 求职方向", p, "headline", { i18n: true })]),
    card("联系方式", "按顺序显示在姓名下面", rows),
    card("事实层", "只给 Claude 看", tfield("求职背景、限制、偏好", p, "notes", { multiline: true, rows: 5 }), { cls: "fact" }),
  ];
}

function sectionsForm() {
  if (!Array.isArray(ui.draft)) ui.draft = [];
  const list = ui.draft;
  const rows = h("div", { class: "list-rows" });
  list.forEach((s, i) => {
    const used = entries().some((e) => e.section === s.id);
    rows.append(h("div", { class: "lr", style: "grid-template-columns: 120px 1fr 110px 26px 26px 26px" },
      h("input", { value: s.id, title: "板块 ID（经历里的 section 字段）", spellcheck: "false", oninput: (ev) => { s.id = ev.target.value; touch(); } }),
      h("input", { value: getT(s.title, ui.editLang), placeholder: "显示标题", oninput: (ev) => { s.title = setT(s.title, ui.editLang, ev.target.value); touch(); } }),
      h("select", { onchange: (ev) => { s.kind = ev.target.value; touch(); } },
        [["timeline", "时间线"], ["list", "列表"]].map(([k, n]) => h("option", { value: k, selected: (s.kind || "timeline") === k }, n))),
      h("button", { class: "btn icon sm ghost", title: "上移", disabled: i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; touch(); buildEditorKeepDraft(); } }, icon("up")),
      h("button", { class: "btn icon sm ghost", title: "下移", disabled: i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; touch(); buildEditorKeepDraft(); } }, icon("down")),
      h("button", { class: "btn icon sm ghost danger", disabled: used, title: used ? "还有经历在这个板块里" : "删除板块", onclick: () => { list.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
  });
  rows.append(h("button", { class: "btn sm", onclick: () => { list.push({ id: `section-${list.length + 1}`, title: "New section", kind: "timeline" }); touch(); buildEditorKeepDraft(); } }, icon("plus"), "添加板块"));

  const st = clone(doc().settings || {});
  const saveSettings = async () => {
    try { applyState(await api("PUT", "/api/item", { kind: "settings", data: st, base: itemHash("settings") })); buildEditorKeepDraft(); scheduleRender(); }
    catch (e) { toast("保存失败：" + e.message); }
  };
  const langToggle = h("div", { class: "seg" }, ["en", "zh"].map((l) => h("button", {
    class: (st.languages || []).includes(l) ? "on" : "",
    onclick: () => {
      const set = new Set(st.languages || []);
      if (set.has(l)) set.delete(l); else set.add(l);
      st.languages = ["en", "zh"].filter((x) => set.has(x));
      if (!st.languages.length) st.languages = ["en"];
      saveSettings();
    } }, LANG_NAMES[l])));
  return [
    h("div", { class: "ed-head" }, h("h1", null, "板块与设置"), h("span", { class: "spacer" }), langSwitch()),
    card("板块", "默认顺序；每个版本还能单独调整", [
      h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, "时间线 = 标题 + 日期 + 要点；列表 = 「类别：条目」一行一个，适合技能。"), rows]),
    card("设置", null, [
      h("div", { class: "field" }, h("label", null, "内容语言", h("span", { class: "hint" }, "选两种时，文字按语言分开保存，编辑区会出现语言切换")), h("div", null, langToggle)),
      h("div", { class: "field" }, h("label", null, "导出目录", h("span", { class: "hint" }, "相对于 resume.yaml")),
        h("input", { value: st.export_dir || "exports", spellcheck: "false", onchange: (ev) => { st.export_dir = ev.target.value.trim() || "exports"; saveSettings(); } })),
    ]),
  ];
}

function versionForm() {
  const v = ui.draft;
  const all = sections().map((s) => s.id);
  const custom = (v.sections || []).filter((id) => all.includes(id));
  const order = [...new Set([...custom, ...all])];
  const hidden = new Set(v.hide_sections || []);
  const rows = h("div", { class: "list-rows" });
  order.forEach((sid, i) => {
    rows.append(h("div", { class: "lr", style: "grid-template-columns: 22px 1fr 26px 26px" },
      h("button", { class: "check" + (hidden.has(sid) ? "" : " on"), title: "在这个版本里显示", onclick: () => {
        const set = new Set(v.hide_sections || []);
        if (set.has(sid)) set.delete(sid); else set.add(sid);
        v.hide_sections = [...set];
        if (!v.hide_sections.length) delete v.hide_sections;
        touch(); buildEditorKeepDraft();
      } }),
      h("span", null, display((sectionById(sid) || {}).title) || sid),
      h("button", { class: "btn icon sm ghost", disabled: i === 0, onclick: () => { const o = [...order]; [o[i - 1], o[i]] = [o[i], o[i - 1]]; v.sections = o; touch(); buildEditorKeepDraft(); } }, icon("up")),
      h("button", { class: "btn icon sm ghost", disabled: i === order.length - 1, onclick: () => { const o = [...order]; [o[i + 1], o[i]] = [o[i], o[i + 1]]; v.sections = o; touch(); buildEditorKeepDraft(); } }, icon("down"))));
  });
  if (v.sections && v.sections.length) rows.append(h("button", { class: "btn sm", onclick: () => { delete v.sections; touch(); buildEditorKeepDraft(); } }, icon("undo"), "恢复默认顺序"));
  return [
    h("div", { class: "ed-head" }, h("h1", null, "版本设置"), h("span", { class: "id" }, v.id)),
    card("版本信息", "模板、语言和版面在右侧预览上方调整；选哪些经历在左侧勾选", h("div", { class: "grid2" }, tfield("版本 ID", v, "id", { hint: "也是导出文件名" }), tfield("名称", v, "label"))),
    card("板块顺序与显示", "只影响这个版本", rows),
  ];
}

// ================================================================ AI polish (runs in the chat)
function aiDiff() {
  const before = ui.polish.snapshot;
  const after = entryById(before.id) || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null));
  const fmt = (v) => {
    const one = (x) => (x && typeof x === "object" ? Object.entries(x).map(([l, t]) => `[${l}] ${t}`).join("\n   ") : String(x ?? ""));
    if (Array.isArray(v)) return v.map((x) => "• " + one(x)).join("\n");
    return one(v);
  };
  const names = { title: "名称", subtitle: "副标题", bullets: "要点", tech: "技术", location: "地点", start: "开始", end: "结束", date: "日期", link: "链接", notes: "事实层", evidence: "证据路径" };
  return card(keys.length ? "Claude 改了这一条" : "Claude 没有改动这一条", "对话里有它的说明", [
    keys.map((k) => [h("div", { class: "k" }, names[k] || k), h("div", { class: "before" }, fmt(before[k])), h("div", { class: "after" }, fmt(after[k]))]),
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "btn sm", onclick: () => { ui.polish = null; buildEditor(); } }, icon("check"), "保留"),
      keys.length && h("button", { class: "btn sm danger-soft", onclick: undoPolish }, icon("undo"), "撤销，恢复到润色前")),
  ], { cls: "diff" });
}

async function undoPolish() {
  const snap = ui.polish.snapshot;
  try {
    const s = await api("PUT", "/api/item", { kind: "entry", id: snap.id, data: snap, base: itemHash("entry", snap.id) });
    ui.polish = null;
    applyState(s);
    buildEditor({ flash: true });
    scheduleRender();
    toast("已恢复到润色前");
  } catch (e) { toast("撤销失败：" + e.message); }
}

async function polish(id, instruction) {
  await saveDraft();
  const snapshot = clone(entryById(id));
  const ok = await sendChat(instruction || "", { polish: true, entryId: id });
  if (ok) ui.polish = { chat: ui.chat.id, entryId: id, snapshot, done: false };
}

// ================================================================ chat
const TOOL_NAMES = { Read: "读取", Edit: "修改", MultiEdit: "修改", Write: "写入", Glob: "查找文件", Grep: "搜索内容", Bash: "运行命令", Skill: "使用技能", WebFetch: "打开网页", WebSearch: "搜索网页", TodoWrite: "列计划", Task: "子任务", Agent: "子任务" };
function shortHint(hint) {
  if (!hint) return "";
  if (/^[a-zA-Z]:[\\/]|^\//.test(hint)) { const parts = hint.split(/[\\/]/); return parts.slice(-2).join("/"); }
  return hint;
}

async function loadChats() {
  const r = await api("GET", "/api/chats");
  ui.chat.available = r.available;
  if (ui.chat.id) { // the user already started a chat while the list was loading: keep it
    ui.chat.list = [...ui.chat.list, ...r.chats.filter((c) => !ui.chat.list.some((x) => x.id === c.id))];
    renderChatSelect();
    return;
  }
  ui.chat.list = r.chats;
  const saved = store("chat");
  const id = ui.chat.list.some((c) => c.id === saved) ? saved : (ui.chat.list[0] || {}).id || null;
  if (id) await openChat(id, { quiet: true });
  else { ui.chat.id = null; ui.chat.messages = []; renderChatSelect(); renderChat(); }
  if (r.running.length) $("#chat-dot").classList.remove("hidden");
}

async function openChat(id, { quiet = false } = {}) {
  let r;
  try { r = await api("GET", "/api/chats/" + id); }
  catch (e) { // deleted elsewhere: fall back to whatever is left
    store("chat", null);
    const list = await api("GET", "/api/chats");
    ui.chat.list = list.chats;
    if (list.chats.length && list.chats[0].id !== id) return openChat(list.chats[0].id, { quiet });
    ui.chat.id = null; ui.chat.messages = []; ui.chat.running = false;
    renderChatSelect(); renderChat(); renderComposerState();
    return;
  }
  ui.chat.id = id;
  ui.chat.meta = r.chat;
  ui.chat.messages = r.messages;
  ui.chat.running = r.running;
  ui.chat.live = r.running ? { blocks: [] } : null;
  store("chat", id);
  renderChatSelect();
  if (!quiet || ui.tab === "chat") renderChat();
  renderComposerState();
  $("#chat-model").textContent = r.chat.model ? `模型 ${r.chat.model}` : "";
}

async function newChat() {
  const c = await api("POST", "/api/chats");
  ui.chat.list = [c, ...ui.chat.list];
  await openChat(c.id);
  $("#chat-input").focus();
}

async function deleteChat() {
  if (!ui.chat.id) return;
  if (!(await modal({ title: "删除这段对话？", message: "只删除界面里的记录，不影响简历数据。", ok: "删除", danger: true }))) return;
  try {
    const r = await api("DELETE", "/api/chats/" + ui.chat.id);
    ui.chat.list = r.chats;
    const next = ui.chat.list[0];
    if (next) await openChat(next.id); else { ui.chat.id = null; ui.chat.messages = []; store("chat", null); renderChatSelect(); renderChat(); }
  } catch (e) { toast(e.message); }
}

function relTime(t) {
  const d = (Date.now() / 1000 - t) / 60;
  if (d < 1) return "刚刚";
  if (d < 60) return `${Math.floor(d)} 分钟前`;
  if (d < 60 * 24) return `${Math.floor(d / 60)} 小时前`;
  return new Date(t * 1000).toLocaleDateString();
}

function renderChatSelect() {
  const sel = $("#chat-select");
  if (!ui.chat.list.length) { sel.replaceChildren(h("option", null, "还没有对话")); sel.disabled = true; return; }
  sel.disabled = false;
  sel.replaceChildren(...ui.chat.list.map((c) => h("option", { value: c.id, selected: c.id === ui.chat.id }, `${c.title || "新对话"} · ${relTime(c.updated)}`)));
}

function renderChat() {
  const log = $("#chat-log");
  log.replaceChildren();
  ui.chat.liveEl = null;
  if (!ui.chat.messages.length && !ui.chat.running) {
    const suggest = [
      "帮我录入一段新经历，一次只问我一个问题",
      "检查当前版本里有没有超出事实层的说法",
      "把当前版本压到一页，先调版面再精简措辞",
      "根据事实层给当前打开的条目起草要点",
    ];
    log.append(h("div", { class: "chat-welcome" },
      h("div", { class: "t" }, "和 Claude 一起改简历"),
      h("div", null, ui.chat.available ? "这里就是 Claude Code：它能读写你的简历数据、使用你装的技能，改完右边会自动刷新。" : "没有找到 claude 命令。请先安装并登录 Claude Code。"),
      ui.chat.available && h("div", { class: "suggest" }, suggest.map((s) => h("button", { onclick: () => sendChat(s) }, s)))));
    return;
  }
  for (const m of ui.chat.messages) log.append(messageEl(m));
  if (ui.chat.running) { ui.chat.liveEl = messageEl({ role: "assistant", blocks: (ui.chat.live || {}).blocks || [], live: true }); log.append(ui.chat.liveEl); }
  log.scrollTop = log.scrollHeight;
}

function messageEl(m) {
  if (m.role === "user") {
    return h("div", { class: "msg-user" }, m.meta && m.meta.polish ? h("div", { class: "msg-note", style: "margin-bottom:2px" }, "✦ 润色请求") : null, m.text);
  }
  const el = h("div", { class: "msg-ai" });
  for (const b of m.blocks || []) {
    if (b.type === "text") el.append(h("div", { class: "md", html: md(b.text) }));
    else if (b.type === "tool") el.append(toolEl(b));
  }
  if (m.live && !(m.blocks || []).length) el.append(h("div", { class: "thinking", "aria-label": "Claude 正在思考" }, h("i"), h("i"), h("i")));
  if (m.error) el.append(h("div", { class: "msg-error" }, m.error));
  if (m.cancelled) el.append(h("div", { class: "msg-note" }, "已停止"));
  return el;
}

function toolEl(t) {
  const st = t.status === "running" ? h("span", { class: "st running" }) : t.status === "error" ? h("span", { class: "st error" }, icon("x")) : h("span", { class: "st done" }, icon("check"));
  return h("details", { class: "tool" },
    h("summary", null, st, h("span", { class: "tn" }, TOOL_NAMES[t.name] || t.name), h("span", { class: "th", title: t.hint || "" }, shortHint(t.hint))),
    t.result && h("pre", null, t.result));
}

let liveFrame = 0;
function renderLive() {
  if (liveFrame) return;
  liveFrame = requestAnimationFrame(() => {
    liveFrame = 0;
    const log = $("#chat-log");
    if (ui.tab !== "chat" || !ui.chat.running) return;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    const el = messageEl({ role: "assistant", blocks: ui.chat.live.blocks, live: true });
    if (ui.chat.liveEl && ui.chat.liveEl.isConnected) ui.chat.liveEl.replaceWith(el); else log.append(el);
    ui.chat.liveEl = el;
    if (nearBottom) log.scrollTop = log.scrollHeight;
  });
}

function renderComposerState() {
  const running = ui.chat.running;
  $("#chat-send").classList.toggle("hidden", running);
  $("#chat-stop").classList.toggle("hidden", !running);
  $("#chat-send").disabled = !ui.chat.available;
  $("#chat-input").disabled = !ui.chat.available;
  const parts = [];
  if (ui.versionId) parts.push(`版本 ${ui.versionId}`);
  if (ui.sel.kind === "entry" && ui.sel.id) parts.push(`条目 ${ui.sel.id}`);
  $("#chat-context").textContent = parts.length ? `Claude 知道你正在看：${parts.join(" · ")}` : "";
  $("#chat-dot").classList.toggle("hidden", !running);
}

function autosize(t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }

function startChatWith(text) {
  setTab("chat");
  const t = $("#chat-input");
  t.value = text;
  autosize(t);
  t.focus();
  t.setSelectionRange(t.value.length, t.value.length);
}

async function sendChat(text, { polish = false, entryId = null } = {}) {
  if (!ui.chat.available) { toast("没有找到 claude 命令"); return false; }
  if (!polish && !text.trim()) return false;
  if (ui.chat.running) { toast("Claude 还在回复上一条，稍等或先停止"); return false; }
  await saveDraft();
  if (!ui.chat.id) {
    const c = await api("POST", "/api/chats");
    ui.chat.list = [c, ...ui.chat.list];
    ui.chat.id = c.id; ui.chat.meta = c; ui.chat.messages = [];
    store("chat", c.id);
    renderChatSelect();
  }
  setTab("chat");
  const body = { text, version_id: ui.versionId, entry_id: entryId || (ui.sel.kind === "entry" ? ui.sel.id : null), polish };
  ui.chat.running = true;
  ui.chat.live = { blocks: [] };
  ui.chat.messages.push({ role: "user", text: polish ? `润色 ${entryId}${text ? "：" + text : ""}` : text, meta: { polish } });
  renderChat();
  renderComposerState();
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/send`, body);
    ui.chat.messages[ui.chat.messages.length - 1].text = r.text;
    return true;
  } catch (e) {
    ui.chat.running = false;
    ui.chat.live = null;
    ui.chat.messages.push({ role: "assistant", blocks: [], error: e.message });
    renderChat();
    renderComposerState();
    return false;
  }
}

async function onChatEvent(type, ev) {
  if (type === "chat.start") { $("#chat-dot").classList.remove("hidden"); return; }
  if (ev.chat !== ui.chat.id) {
    if (type === "chat.done") { const r = await api("GET", "/api/chats"); ui.chat.list = r.chats; renderChatSelect(); if (!r.running.length) $("#chat-dot").classList.add("hidden"); }
    return;
  }
  const live = ui.chat.live || (ui.chat.live = { blocks: [] });
  if (type === "chat.init") { if (ev.model) $("#chat-model").textContent = `模型 ${ev.model}`; return; }
  if (type === "chat.delta") {
    const last = live.blocks[live.blocks.length - 1];
    if (last && last.type === "text") last.text += ev.text; else live.blocks.push({ type: "text", text: ev.text });
    renderLive();
  } else if (type === "chat.tool") {
    const i = live.blocks.findIndex((b) => b.type === "tool" && b.id === ev.tool.id);
    if (i >= 0) live.blocks[i] = ev.tool; else live.blocks.push(ev.tool);
    renderLive();
  } else if (type === "chat.done") {
    ui.chat.running = false;
    await openChat(ui.chat.id, { quiet: ui.tab !== "chat" });
    const r = await api("GET", "/api/chats");
    ui.chat.list = r.chats;
    renderChatSelect();
    renderComposerState();
    await refresh();
    if (ui.polish && ui.polish.chat === ev.chat) {
      ui.polish.done = true;
      if (ui.sel.kind === "entry" && ui.sel.id === ui.polish.entryId && !ui.dirty) buildEditor();
      else if (ui.sel.kind !== "entry" || ui.sel.id !== ui.polish.entryId) toast(`Claude 改完了 ${ui.polish.entryId}`, { action: { label: "查看", fn: () => select("entry", ui.polish.entryId) } });
    }
    if (ev.error && ui.tab !== "chat") toast("Claude 出错了，详见对话");
  }
}

// ================================================================ right: preview
const SLIDERS = [
  ["font_size", "字号", 8, 13, 0.1, "pt"],
  ["line_spread", "行距", 0.8, 1.4, 0.01, "×"],
  ["margin_x", "左右边距", 0.4, 3, 0.05, "cm"],
  ["margin_y", "上下边距", 0.4, 3, 0.05, "cm"],
  ["section_sep", "板块间距", 0, 20, 0.5, "pt"],
  ["entry_sep", "条目间距", 0, 14, 0.5, "pt"],
  ["item_sep", "要点间距", 0, 8, 0.25, "pt"],
];

function layoutValue(key) {
  const v = version();
  const L = (v && v.layout) || {};
  return L[key] ?? S.layout_defaults[key];
}

let layoutBuiltFor = null;
function renderPreviewToolbar() {
  const v = version();
  const tpl = $("#tpl-select");
  tpl.replaceChildren(...S.templates.map((t) => h("option", { value: t.id, title: t.description, selected: v && t.id === v.template }, t.name)));
  tpl.disabled = !v;
  tpl.title = v ? (S.templates.find((t) => t.id === v.template) || {}).description || "模板" : "模板";
  $("#lang-seg").replaceChildren(...["en", "zh"].map((l) => h("button", { class: v && v.lang === l ? "on" : "", disabled: !v, onclick: () => mutateVersion((x) => { x.lang = l; }) }, LANG_NAMES[l])));
  $("#layout-toggle").classList.toggle("on", ui.layoutOpen);
  $("#layout-toggle").disabled = !v;
  $("#export-btn").disabled = !v;
  $("#layout-panel").classList.toggle("hidden", !ui.layoutOpen || !v);
  if (!v) { layoutBuiltFor = null; return; }
  if (layoutBuiltFor !== v.id) buildLayoutPanel();
  updateLayoutPanel();
}

function buildLayoutPanel() {
  layoutBuiltFor = ui.versionId;
  const reset = (key) => h("button", { class: "btn icon sm ghost", title: "恢复默认", onclick: () => queueLayout(key, undefined, 0) }, icon("undo"));
  $("#layout-panel").replaceChildren(
    ...SLIDERS.map(([key, label, min, max, step, unit]) => {
      const val = h("span", { class: "val" });
      const input = h("input", { type: "range", min, max, step, "data-key": key, "aria-label": label,
        oninput: (ev) => { val.textContent = ev.target.value + unit; queueLayout(key, parseFloat(ev.target.value)); } });
      return h("div", { class: "ctl" }, h("span", null, label), input, val, reset(key));
    }),
    h("div", { class: "ctl" }, h("span", null, "强调色"), h("input", { type: "color", id: "ctl-accent", onchange: (ev) => queueLayout("accent", ev.target.value.slice(1).toUpperCase(), 0) }), h("span"), reset("accent")),
    h("div", { class: "ctl" }, h("span", null, "纸张"),
      h("select", { id: "ctl-paper", onchange: (ev) => queueLayout("paper", ev.target.value, 0) }, h("option", { value: "a4" }, "A4"), h("option", { value: "letter" }, "Letter")), h("span"), reset("paper")),
    h("div", { class: "ctl wide" }, h("span", null, "正文字体"),
      h("input", { type: "text", id: "ctl-font", placeholder: "留空用模板默认，比如 TeX Gyre Heros / Times New Roman / Arial", spellcheck: "false",
        onchange: (ev) => queueLayout("font", ev.target.value.trim() || undefined, 0) })));
}

function updateLayoutPanel() {
  const v = version();
  if (!v) return;
  const active = document.activeElement;
  const set = (el, value) => { if (el && el !== active) el.value = value; };
  set($("#ctl-paper"), layoutValue("paper"));
  set($("#ctl-font"), (v.layout || {}).font || "");
  set($("#ctl-accent"), "#" + String(layoutValue("accent")).replace("#", ""));
  for (const [key, , , , , unit] of SLIDERS) {
    const input = document.querySelector(`#layout-panel input[data-key="${key}"]`);
    if (!input) continue;
    set(input, layoutValue(key));
    input.nextSibling.textContent = (input === active ? input.value : layoutValue(key)) + unit;
  }
}

const pendingLayout = {};
let layoutTimer = null;
function queueLayout(key, value, delay = 300) {
  pendingLayout[key] = value;
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => {
    const patch = { ...pendingLayout };
    for (const k in pendingLayout) delete pendingLayout[k];
    mutateVersion((x) => {
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
  if (!ui.versionId) { clearPages(); setBadge(null); return; }
  if (r.busy) { r.again = true; return; }
  r.busy = true;
  const vid = ui.versionId;
  setBadge({ busy: true });
  try {
    const res = await api("POST", `/api/versions/${encodeURIComponent(vid)}/render`);
    if (vid === ui.versionId) { r.last = res; await showPages(res); setBadge(res); }
  } catch (e) {
    setBadge({ ok: false, errors: [e.message] });
  } finally {
    r.busy = false;
    if (r.again) { r.again = false; doRender(); }
  }
}

function setBadge(res) {
  const b = $("#page-badge");
  const errs = $("#render-errors");
  errs.classList.add("hidden");
  if (!res) { b.className = "badge"; b.textContent = ""; return; }
  if (res.busy) { b.className = "badge busy"; b.textContent = "渲染中"; return; }
  if (res.ok) {
    const one = res.pages === 1;
    b.className = "badge " + (one ? "ok" : "bad");
    b.replaceChildren(icon(one ? "check" : "alert"), one ? "1 页" : `${res.pages} 页 · 超出一页`);
    b.title = one ? `渲染用时 ${res.seconds}s` : "试试在「版面」里减小字号、边距、间距，或少选一条经历";
  } else {
    b.className = "badge bad";
    b.replaceChildren(icon("alert"), "渲染失败");
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
  $("#zoom-fit").textContent = ui.zoom === "fit" ? "适合" : Math.round(ui.zoom * 100) + "%";
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
  const vid = encodeURIComponent(res.version);
  if (!res.images) { // no pdftoppm: fall back to the browser's PDF viewer
    box.replaceChildren(h("iframe", { class: "pdf-frame", src: `/api/versions/${vid}/pdf?t=${res.stamp}#view=FitH`, style: "height:calc(100vh - 150px)" }));
    return;
  }
  const imgs = await Promise.all(Array.from({ length: res.images }, (_, i) => new Promise((resolve) => {
    const img = new Image();
    img.alt = `第 ${i + 1} 页`;
    img.onload = img.onerror = () => resolve(img);
    img.src = `/api/versions/${vid}/page/${i + 1}?t=${res.stamp}`;
  })));
  const w = pageWidth();
  box.replaceChildren(...imgs.map((img, i) => h("div", { class: "page" + (i > 0 ? " overflow" : ""), style: `width:${w}px` },
    res.images > 1 ? h("span", { class: "plabel" }, i === 0 ? "第 1 页" : `第 ${i + 1} 页 · 超出一页`) : null, img)));
}

async function exportPdf() {
  await saveDraft();
  $("#export-btn").disabled = true;
  try {
    const r = await api("POST", `/api/versions/${encodeURIComponent(ui.versionId)}/export`);
    if (!r.ok) { setBadge(r); toast("导出失败，看预览上方的错误信息"); return; }
    setBadge(r);
    await showPages(r);
    toast(`已导出 ${r.exported.pdf.split(/[\\/]/).pop()}${r.pages > 1 ? `（注意：共 ${r.pages} 页）` : ""}`,
      { ms: 6000, action: { label: "打开文件夹", fn: () => api("POST", "/api/reveal", { path: r.exported.pdf }) } });
  } catch (e) { toast("导出失败：" + e.message); }
  finally { $("#export-btn").disabled = !version(); }
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
  const conn = $("#conn");
  es.onopen = () => { conn.className = "conn ok"; conn.title = "已连接"; if (S) refresh(); };
  es.onerror = () => { conn.className = "conn bad"; conn.title = "连接断开，正在重连…"; };
  es.addEventListener("file", (m) => { const d = JSON.parse(m.data); if (S && d.digest === S.digest) return; refresh(); });
  for (const t of ["chat.start", "chat.init", "chat.delta", "chat.tool", "chat.done"]) es.addEventListener(t, (m) => onChatEvent(t, JSON.parse(m.data)));
}

function wire() {
  for (const b of document.querySelectorAll(".tab")) b.onclick = () => setTab(b.dataset.tab);
  $("#version-select").onchange = (e) => switchVersion(e.target.value);
  $("#version-menu-btn").append(icon("more"));
  $("#version-menu-btn").onclick = (e) => { e.stopPropagation(); openVersionMenu(); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#version-menu")) $("#version-menu").classList.add("hidden"); });

  $("#tpl-select").onchange = (e) => mutateVersion((x) => { x.template = e.target.value; });
  $("#layout-toggle").prepend(icon("settings"));
  $("#layout-toggle").onclick = () => { ui.layoutOpen = !ui.layoutOpen; store("layoutOpen", ui.layoutOpen ? "1" : "0"); renderPreviewToolbar(); };
  $("#zoom-in").append(icon("zoomIn"));
  $("#zoom-out").append(icon("zoomOut"));
  $("#zoom-in").onclick = () => setZoom(currentZoomFactor() + 0.1);
  $("#zoom-out").onclick = () => setZoom(currentZoomFactor() - 0.1);
  $("#zoom-fit").onclick = () => setZoom(ui.zoom === "fit" ? 1 : "fit");
  $("#export-btn").onclick = exportPdf;

  $("#chat-new").append(icon("plus"));
  $("#chat-delete").append(icon("trash"));
  $("#chat-send").append(icon("send"));
  $("#chat-stop").append(icon("stop"));
  $("#chat-new").onclick = newChat;
  $("#chat-delete").onclick = deleteChat;
  $("#chat-select").onchange = (e) => openChat(e.target.value);
  const input = $("#chat-input");
  const send = async () => { const text = input.value.trim(); if (!text) return; if (await sendChat(text)) { input.value = ""; autosize(input); } };
  $("#chat-send").onclick = send;
  $("#chat-stop").onclick = () => ui.chat.id && api("POST", `/api/chats/${ui.chat.id}/cancel`);
  input.addEventListener("input", () => autosize(input));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $("#chat-log").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ext]");
    if (a) { e.preventDefault(); api("POST", "/api/open", { url: a.getAttribute("href") }).catch(() => window.open(a.href, "_blank")); }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveDraft(); }
    if (e.key === "Escape") { $("#version-menu").classList.add("hidden"); if (ui.layoutOpen) { ui.layoutOpen = false; renderPreviewToolbar(); } }
  });
  window.addEventListener("beforeunload", () => { if (ui.dirty) saveDraft(); });
}

(async function main() {
  ui.zoom = store("zoom") && store("zoom") !== "fit" ? parseFloat(store("zoom")) : "fit";
  ui.layoutOpen = store("layoutOpen") === "1";
  wire();
  initGutters();
  applyState(await api("GET", "/api/state"));
  select("profile");
  setTab(store("tab") === "chat" ? "chat" : "library");
  connect();
  loadChats().catch(() => {});
  scheduleRender(0);
})();
