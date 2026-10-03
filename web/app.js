// Resume Studio front end. No build step: plain DOM + fetch + EventSource.
// resume.yaml on disk is the source of truth; this page keeps a draft of the
// item being edited, autosaves it, and follows changes made by Claude.
"use strict";

// ================================================================ helpers
const clone = (x) => JSON.parse(JSON.stringify(x ?? null));
const LANG_NAMES = { en: "EN", zh: "中文" };

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
function modal({ title, message = "", input = null, placeholder = "", ok = "确定", danger = false }) {
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
  ai: {},        // entryId -> {chat, action, snapshot, done}: what Claude was asked to change, for the diff + undo
  lines: {},     // lang -> {entryId: {bulletIndex: {n, fill}}}: measured by the last render in that language
  linesFor: {},  // lang -> version id the measurement belongs to
  missing: [],   // entries lacking text in the previewed language (from the last render)
  evidence: {},  // entryId -> {state, paths: [...]}: have the project folders changed since Claude read them
  chat: { list: [], id: null, meta: null, messages: [], running: false, live: null, liveEl: null, available: false,
          queue: [], files: [], waiting: new Set(), thinking: 0 },
  claude: { info: null, limits: {}, modes: [] },   // what the user's Claude Code offers (GET /api/claude)
  w: {},         // custom controls created once: version, chat, tpl
};

const doc = () => S.doc;
const langs = () => (doc().settings && doc().settings.languages) || ["en"];
const sections = () => doc().sections || [];
const entries = () => doc().entries || [];
const versions = () => doc().versions || [];
const version = () => versions().find((v) => v.id === ui.versionId) || null;
const ALL = "ALL";
const isAll = () => ui.versionId === ALL;
const jobName = (v) => (v && v.id === ALL ? "全部经历" : (v && (v.label || v.id)) || "");
const entryById = (id) => entries().find((e) => e.id === id) || null;
// what people see: the entry's number inside its own section (library order); ids stay internal
function localNo(e) { return entries().filter((x) => x.section === e.section).findIndex((x) => x.id === e.id) + 1; }
function entryLabel(e) {
  if (!e) return "";
  const sec = sectionById(e.section);
  return `${(sec && display(sec.title)) || e.section || "未归类"} #${localNo(e)}`;
}
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
  setTip($("#datapath"), "数据文件：" + s.data_path);
  if (!versions().some((v) => v.id === ui.versionId)) {
    const saved = store("version");
    ui.versionId = versions().some((v) => v.id === saved) ? saved : ALL;
  }
  if (!ui.editLang || !langs().includes(ui.editLang)) {
    ui.editLang = (version() && langs().includes(version().lang) && version().lang) || langs()[0];
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
const TEMPLATE_NAMES = (id) => ((S.templates || []).find((t) => t.id === id) || {}).name || id;

function renderTopbar() {
  const jobs = versions().filter((v) => v.id !== ALL);
  const opts = [{ value: ALL, label: "ALL · 全部经历", short: "ALL · 全部经历", desc: "经历库全集，不限一页" }];
  for (const v of jobs) opts.push({ value: v.id, label: v.label || v.id, group: "岗位", hint: `${LANG_NAMES[v.lang] || v.lang} · ${TEMPLATE_NAMES(v.template)}` });
  if (!jobs.length) opts.push({ value: "__none", label: "还没有岗位", desc: "点右边的 ··· 新建一个", group: "岗位", disabled: true });
  ui.w.version.setOptions(opts, ui.versionId);
}

function openVersionMenu() {
  const v = version();
  const job = v && v.id !== ALL;
  const bilingual = langs().length > 1;
  const inV = job ? (v.entries || []).map(entryById).filter(Boolean) : entries();
  const lacking = (l) => inV.filter((e) => missingIn(e, l)).length;
  const long = longBullets().length;
  openMenu($("#version-menu-btn"), [
    v && { label: job ? "岗位设置" : "ALL 设置", hint: job ? "名称、求职方向、板块" : "求职方向、板块顺序", icon: "settings", onClick: () => select("version", ui.versionId) },
    { label: "新建岗位", hint: "空白，自己勾选", icon: "plus", onClick: () => newVersion(null) },
    v && { label: job ? "复制当前岗位" : "新建岗位（先勾上全部经历）", icon: "copy", onClick: () => newVersion(ui.versionId) },
    "sep",
    { header: `让 Claude 处理${job ? "这个岗位" : "全部经历"}` },
    bilingual && { label: "补齐中文版", hint: lacking("zh") ? `${lacking("zh")} 条缺中文` : "都有了", icon: "translate", disabled: !ui.chat.available || !lacking("zh"),
      onClick: () => aiBatch("batch_translate", { target: "zh" }) },
    bilingual && { label: "补齐英文版", hint: lacking("en") ? `${lacking("en")} 条缺英文` : "都有了", icon: "translate", disabled: !ui.chat.available || !lacking("en"),
      onClick: () => aiBatch("batch_translate", { target: "en" }) },
    { label: "把超过一行的要点压成一行", hint: long ? `${long} 条` : "都是一行", icon: "compress", disabled: !ui.chat.available || !long,
      onClick: () => aiBatch("batch_fit") },
    "sep",
    { label: "打开导出文件夹", icon: "folder", onClick: revealExports },
    job && "sep",
    job && { label: "删除当前岗位", icon: "trash", danger: true, onClick: deleteVersion },
  ], { align: "start", focusFirst: false });
}

async function switchVersion(id) {
  await saveDraft();
  ui.versionId = id;
  store("version", id);
  const v = version();
  if (v && langs().includes(v.lang)) ui.editLang = v.lang;
  ui.missing = [];
  renderTopbar();
  renderLibrary();
  renderPreviewToolbar();
  renderComposerState();
  if (ui.sel.kind === "version") select("version", id); else buildEditorKeepDraft();
  clearPages();
  scheduleRender(0);
}

async function newVersion(copyFrom) {
  const fromAll = copyFrom === ALL;
  const label = await modal({
    title: copyFrom && !fromAll ? "复制岗位" : "新建岗位",
    message: fromAll ? "先勾上全部经历，再去掉这个岗位用不上的。" : copyFrom ? "复制当前岗位的经历选择、求职方向和版面。" : "从空白开始，在左侧勾选要放进这个岗位的经历。",
    input: copyFrom && !fromAll ? `${jobName(version())}（副本）` : "", placeholder: "岗位名称，比如：AI Agent 工程师", ok: "创建",
  });
  if (!label || !label.trim()) return;
  try {
    const r = await api("POST", "/api/versions", { label: label.trim(), copy_from: copyFrom });
    applyState(r);
    switchVersion(r.id);
    toast(`已创建岗位「${label.trim()}」${fromAll || copyFrom ? "" : "，在左侧勾选要放进去的经历"}`);
  } catch (e) { toast(e.message); }
}

async function deleteVersion() {
  if (!(await modal({ title: `删除岗位「${jobName(version())}」？`, message: "只删除这个岗位的配置（选了哪些经历、求职方向、模板和版面），经历库本身不受影响。", ok: "删除", danger: true }))) return;
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
  const job = v && v.id !== ALL;
  lib.append(h("div", { class: "lib-hint" + (job ? " job" : "") }, job
    ? [h("b", null, jobName(v)), "：勾选 = 放进这个岗位的简历"]
    : [h("b", null, "ALL"), "：全部经历都在这里。新建岗位后，从中勾选一部分"]));
  const changed = entries().filter((e) => (ui.evidence[e.id] || {}).state === "changed");
  if (changed.length) {
    lib.append(h("div", { class: "lib-update" },
      icon("refresh"),
      h("span", { class: "lu-text" }, changed.length === 1
        ? ["「", display(changed[0].title) || entryLabel(changed[0]), "」的项目有新进展"]
        : `${changed.length} 个项目自上次同步后有新进展`),
      changed.length === 1
        ? h("button", { class: "btn sm", onclick: () => select("entry", changed[0].id) }, "查看")
        : h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => aiBatch("batch_sync") }, icon("sparkle"), "逐个更新")));
  }

  const included = new Set(job ? v.entries || [] : entries().map((e) => e.id));
  const order = job ? v.entries || [] : entries().map((e) => e.id);
  for (const sec of sections()) {
    const mine = entries().filter((e) => e.section === sec.id);
    const inV = order.map(entryById).filter((e) => e && e.section === sec.id);
    const outV = mine.filter((e) => !included.has(e.id));
    lib.append(h("div", { class: "lib-section" },
      h("div", { class: "lib-head" },
        h("span", null, h("span", { class: "t" }, display(sec.title) || sec.id), mine.length ? h("span", { class: "n" }, job ? `${inV.length}/${mine.length}` : mine.length) : null),
        h("button", { class: "btn icon sm ghost", title: `在「${display(sec.title) || sec.id}」新增一条`, onclick: () => newEntry(sec.id) }, icon("plus"))),
      [...inV, ...outV].map((e, i) => entryRow(e, included.has(e.id), i, inV.length, job))));
  }
  const orphans = entries().filter((e) => !sectionById(e.section));
  if (orphans.length) lib.append(h("div", { class: "lib-section" }, h("div", { class: "lib-head" }, h("span", { class: "t" }, "未归类")), orphans.map((e) => entryRow(e, included.has(e.id), 0, 0, job))));

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
  const meta = entryMeta(e);
  const ev = (ui.evidence[e.id] || {}).state;
  const gaps = langs().length > 1 ? langs().filter((l) => missingIn(e, l)) : [];
  return h("div", { class: "entry" + (isIn ? "" : " out") + (active ? " on" : ""), "data-id": e.id, onclick: () => select("entry", e.id) },
    hasVersion && h("button", {
      class: "check" + (isIn ? " on" : ""), role: "checkbox", "aria-checked": String(isIn),
      title: isIn ? "从这个岗位移除" : "放进这个岗位",
      onclick: (ev) => { ev.stopPropagation(); toggleInclude(e.id, !isIn); },
    }),
    h("span", { class: "e-no" }, localNo(e)),
    h("span", { class: "e-main" },
      h("span", { class: "e-title" }, display(e.title) || "（未命名）",
        ev === "changed" && h("span", { class: "e-dot", title: "项目文件夹自上次同步后有更新" }),
        ev === "missing" && h("span", { class: "e-dot bad", title: "找不到证据文件夹" })),
      (meta || gaps.length > 0) && h("span", { class: "e-meta" }, meta,
        gaps.map((l) => h("span", { class: "e-gap", title: `有些文字还没有${l === "zh" ? "中文" : "英文"}，${l === "zh" ? "中文" : "英文"}简历里会暂时显示另一种语言` }, `缺${l === "zh" ? "中文" : "英文"}`)))),
    isIn && h("span", { class: "e-tools" },
      h("button", { class: "btn icon sm ghost", title: "上移", disabled: idx === 0, onclick: (ev) => { ev.stopPropagation(); hasVersion ? moveInVersion(e.id, -1) : moveInLibrary(e.id, -1); } }, icon("up")),
      h("button", { class: "btn icon sm ghost", title: "下移", disabled: idx >= nIn - 1, onclick: (ev) => { ev.stopPropagation(); hasVersion ? moveInVersion(e.id, 1) : moveInLibrary(e.id, 1); } }, icon("down"))));
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

// in ALL, the arrows reorder the library itself
async function moveInLibrary(id, dir) {
  try { applyState(await api("POST", `/api/entries/${encodeURIComponent(id)}/move`, { dir })); scheduleRender(200); }
  catch (e) { toast("移动失败：" + e.message); }
}

async function newEntry(section) {
  await saveDraft();
  const r = await api("POST", "/api/entries", { section });
  applyState(r);
  if (version() && !isAll()) await mutateVersion((v) => { v.entries = [...(v.entries || []), r.id]; });
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
  return h("div", { class: "seg", title: "编辑哪种语言的文字（右侧预览的语言在预览上方切换）" }, langs().map((l) =>
    h("button", { class: l === ui.editLang ? "on" : "", "aria-pressed": String(l === ui.editLang), onclick: () => {
      if (l === ui.editLang) return;
      ui.editLang = l;
      buildEditorKeepDraft();
      if (ui.linesFor[l] !== ui.versionId && !ui.render.busy) measureEditLang(ui.versionId);
    } }, LANG_NAMES[l] || l)));
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

  const ai = ui.ai[e.id];
  if (ai && ai.done) out.push(aiDiff(e.id));

  out.push(h("div", { class: "ed-head" }, h("h1", null, display(e.title) || "（未命名）"), h("span", { class: "id" }, entryLabel(entryById(e.id) || e)), h("span", { class: "spacer" }), langSwitch()));

  const secSel = uiSelect({
    options: sections().map((s) => ({ value: s.id, label: display(s.title) || s.id, hint: s.kind === "list" ? "列表" : "时间线" })),
    value: e.section, cls: "field-select", label: "板块",
    onChange: (v) => { e.section = v; touch(); buildEditorKeepDraft(); },
  });
  secSel.el.setAttribute("data-key", "section");
  const basics = [
    h("div", { class: "field" }, h("label", null, "板块"), secSel.el),
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
    h("input", { value: e.tech.map((t) => display(t)).join(", "), spellcheck: "false", "data-key": "tech",
      oninput: (ev) => { e.tech = ev.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean); touch(); } })));
  out.push(card("基本信息", null, basics));

  if (!isList) out.push(bulletsCard(e));

  out.push(card("事实层", "只给 Claude 看，永远不会出现在简历上", [
    h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, "写真实情况：哪些是你做的、哪些是 AI 写的、数字怎么来的、不好意思写进简历的实情。Claude 润色时只在这里能支撑的范围内包装。"),
    tfield("实际情况 / 原始想法", e, "notes", { multiline: true, rows: 6 }),
    evidenceEditor(e),
  ], { cls: "fact" }));

  out.push(h("div", { class: "danger-zone" }, h("button", { class: "btn sm ghost danger", onclick: () => deleteEntry(e.id) }, icon("trash"), "删除这条经历")));
  return out;
}

// ---------------------------------------------------------------- bullets
const LANG_LONG = { en: "英文", zh: "中文" };
const otherLang = (l) => langs().find((x) => x !== l) || (l === "zh" ? "en" : "zh");

function bulletsCard(e) {
  const L = ui.editLang;
  const aiBtn = h("button", { class: "btn sm ghost ai-btn", "aria-haspopup": "menu", disabled: !ui.chat.available,
    title: ui.chat.available ? "" : "没有找到 claude 命令", onclick: (ev) => openAiMenu(ev.currentTarget, e) }, icon("sparkle"), "让 Claude…", icon("chev"));
  return card("要点", langs().length > 1 ? `正在编辑${LANG_LONG[L] || L}` : null,
    [h("div", { id: "bullet-notes" }, bulletNotes(e)), bulletsEditor(e)], { actions: aiBtn, cls: "bullets-card" });
}

// what the line measurement and the bilingual check say about this entry, with one-click fixes
function bulletNotes(e) {
  const L = ui.editLang;
  const out = [];
  if (langs().length > 1 && missingIn(e, L)) {
    out.push(noteBar("translate", `还有内容没有${LANG_LONG[L]}`,
      ui.chat.available && h("button", { class: "btn sm", onclick: () => aiEntry("translate", e.id, { target: L }) }, icon("sparkle"), `让 Claude 写${LANG_LONG[L]}版`)));
  }
  const m = measuredFor(e.id, L);
  // only bullets written in this language: an untranslated one is flagged as 缺 instead
  const long = m ? Object.entries(m).filter(([i, x]) => x.n > 1 && bulletState(e.bullets[i], L) === "ok").length : 0;
  if (long) {
    out.push(noteBar("compress", `${long} 条要点超过一行`,
      ui.chat.available && h("button", { class: "btn sm", onclick: () => aiEntry("fit", e.id, { lang: L }) }, icon("sparkle"), "让 Claude 压成一行"), "warn"));
  } else if (m === null && e.bullets.some((b) => getT(b, L))) {
    out.push(h("div", { class: "note-line" }, icon("alert"), isAll() ? "这一条所在的板块被隐藏了，预览里没有它，量不出行数。" : `这一条没有勾进「${jobName(version())}」，预览里没有它，量不出行数。`));
  }
  return out;
}

const NOTE_TIPS = {
  translate: "缺的部分在这个语言的简历里会暂时显示另一种语言；写好后每条要点后面会显示它占几行",
  compress: "按右侧预览的模板、字号和边距实测；改字或调版面后会自动重新测",
};
function noteBar(ic, text, action, tone = "") {
  return h("div", { class: "note-bar " + tone }, icon(ic), h("span", { class: "nb-text", title: NOTE_TIPS[ic] }, text), action);
}

function bulletState(b, L) {
  if (langs().length < 2 || !(b && typeof b === "object")) return "ok";
  const others = Object.entries(b).some(([k, x]) => k !== L && x);
  if (!(L in b)) return others ? "missing" : "ok";
  return b[L] === "" && others ? "hidden" : "ok";
}

function lineChip(i, m, st) {
  if (st === "missing") return h("span", { class: "line-chip gap", "data-line": i, title: `还没有${LANG_LONG[ui.editLang]}，简历里暂时显示${LANG_LONG[otherLang(ui.editLang)]}` }, `缺${LANG_LONG[ui.editLang]}`);
  if (st === "hidden") return h("span", { class: "line-chip off", "data-line": i, title: `这条只出现在${LANG_LONG[otherLang(ui.editLang)]}简历里` }, "不显示");
  const x = m && m[i];
  if (!x) return h("span", { class: "line-chip none", "data-line": i });
  const pct = Math.round(x.fill * 100);
  if (x.n > 1) return h("span", { class: "line-chip long", "data-line": i, title: `占了 ${x.n} 行：文字是一行的 ${pct}%，大约要删掉 ${Math.max(1, pct - 98)}%` }, `${x.n} 行`);
  return h("span", { class: "line-chip ok" + (x.fill > 0.93 ? " tight" : ""), "data-line": i, title: `一行，用了这一行的 ${pct}%` }, x.fill > 0.93 ? "满行" : "1 行");
}

function bulletsEditor(e) {
  const L = ui.editLang, O = otherLang(L);
  const box = h("div", { id: "bullets" });
  const redraw = () => { box.replaceWith(bulletsEditor(e)); };
  const m = measuredFor(e.id, L);
  e.bullets.forEach((b, i) => {
    const st = bulletState(b, L);
    const otherText = b && typeof b === "object" ? b[O] || "" : "";
    const ph = st === "missing" ? `${LANG_NAMES[O]}：${otherText}` : st === "hidden" ? `${LANG_LONG[L]}简历里不显示这一条（${LANG_NAMES[O]}：${otherText}）` : "";
    const ta = h("textarea", { rows: 1, spellcheck: "false", "data-bullet": i, placeholder: ph, class: st !== "ok" ? "is-" + st : null,
      oninput: (ev) => { e.bullets[i] = setT(e.bullets[i], L, ev.target.value); touch(); } }, getT(b, L));
    const bilingual = langs().length > 1 && b && typeof b === "object";
    box.append(h("div", { class: "bullet" + (st !== "ok" ? " " + st : "") }, h("span", { class: "dot" }), ta, lineChip(i, m, st),
      h("div", { class: "b-tools" },
        bilingual && (st === "hidden"
          ? h("button", { class: "btn icon sm ghost", title: `放回${LANG_LONG[L]}简历`, onclick: () => { delete e.bullets[i][L]; touch(); redraw(); } }, icon("undo"))
          : h("button", { class: "btn icon sm ghost", title: `${LANG_LONG[L]}简历不要这一条（${LANG_LONG[O]}版照常显示）`, onclick: () => { e.bullets[i] = { ...e.bullets[i], [L]: "" }; touch(); redraw(); } }, icon("x"))),
        h("button", { class: "btn icon sm ghost", title: "上移", disabled: i === 0, onclick: () => { [e.bullets[i - 1], e.bullets[i]] = [e.bullets[i], e.bullets[i - 1]]; touch(); redraw(); } }, icon("up")),
        h("button", { class: "btn icon sm ghost", title: "下移", disabled: i === e.bullets.length - 1, onclick: () => { [e.bullets[i + 1], e.bullets[i]] = [e.bullets[i], e.bullets[i + 1]]; touch(); redraw(); } }, icon("down")),
        h("button", { class: "btn icon sm ghost danger", title: langs().length > 1 ? "删除这条要点（中英文都删）" : "删除这条要点", onclick: () => { e.bullets.splice(i, 1); touch(); redraw(); } }, icon("trash")))));
  });
  if (!e.bullets.length) {
    const hasEvidence = (e.evidence || []).length;
    box.append(h("p", { class: "muted", style: "margin:0 0 8px" }, hasEvidence
      ? "还没有要点。已经关联了项目文件夹，可以让 Claude 先读项目再起草。"
      : "还没有要点。可以自己写；也可以在下面的事实层写清真实情况、关联项目文件夹，再让 Claude 起草。"));
  }
  const instr = h("input", { placeholder: "补充要求（可选），比如：更突出架构设计；第二条太夸张了", spellcheck: "false",
    onkeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) aiEntry("polish", e.id, { text: instr.value, lang: L }); } });
  box.append(
    h("div", { class: "row wrap" },
      h("button", { class: "btn sm", onclick: () => { e.bullets.push(langs().length > 1 ? { [L]: "" } : ""); /* the other language shows as 缺 until written */ touch(); redraw(); setTimeout(() => { const t = document.querySelectorAll("#bullets textarea"); if (t.length) t[t.length - 1].focus(); }, 0); } }, icon("plus"), "添加要点"),
      h("span", { class: "muted", style: "font-size:12px" }, "支持 **加粗**、*斜体*、[文字](链接)")),
    h("div", { id: "polish-box", class: "polish-box hidden" }, instr,
      h("button", { class: "btn primary sm", onclick: () => aiEntry("polish", e.id, { text: instr.value, lang: L }) }, icon("sparkle"), "开始润色")));
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
    { label: empty ? "起草要点" : `润色${bilingual ? LANG_LONG[L] : ""}要点`, hint: "可加补充要求", icon: "sparkle",
      onClick: () => { const box = $("#polish-box"); box.classList.remove("hidden"); $("input", box).focus(); } },
    bilingual && { label: "写中文版", hint: "按中文习惯重写", icon: "translate", onClick: () => aiEntry("translate", e.id, { target: "zh" }) },
    bilingual && { label: "写英文版", hint: "按英文习惯重写", icon: "translate", onClick: () => aiEntry("translate", e.id, { target: "en" }) },
    { label: "把超过一行的要点压成一行", hint: long ? `${long} 条` : "都是一行", icon: "compress", disabled: !long, onClick: () => aiEntry("fit", e.id, { lang: L }) },
    "sep",
    { label: "按项目新进展更新", hint: ev.state === "changed" ? "有更新" : ev.state === "new" ? "还没读过" : (e.evidence || []).length ? "没有变化" : "没关联文件夹",
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
  const p = await modal({ title: "输入路径", message: "文件夹或文件的完整路径。一般直接用「选择项目文件夹」更省事。", input: "", placeholder: "E:/Forge/Paper2Exam", ok: "添加" });
  if (!p) return;
  const clean = p.trim().replace(/^["']|["']$/g, "");
  if (!e.evidence.includes(clean)) e.evidence.push(clean);
  touch();
  buildEditorKeepDraft();
  await saveDraft();
  checkEvidence(true);
}

function changeSummary(c) {
  if (!c) return "有改动";
  const parts = [];
  if (c.commits) parts.push(`${c.commits} 个新提交`);
  if (c.rewritten) parts.push("git 历史有变化");
  if (c.uncommitted) parts.push(`${c.uncommitted} 个文件未提交`);
  if (c.counts) { const n = c.counts.reduce((a, b) => a + b, 0); if (n) parts.push(`${n} 个文件有改动`); }
  return parts.join("，") || "有改动";
}

function evidenceRow(e, p, i, row) {
  const name = p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
  const st = row ? row.state : "checking";
  const kind = row && row.kind;
  const chip = {
    checking: h("span", { class: "ev-chip" }, "检查中…"),
    same: h("span", { class: "ev-chip ok", title: row && row.synced_at ? `Claude 上次读它：${relTime(row.synced_at)}` : "" }, icon("check"), "已同步"),
    new: h("span", { class: "ev-chip", title: "加进来之后 Claude 还没读过它" }, "Claude 还没读过"),
    changed: h("span", { class: "ev-chip new" }, icon("refresh"), changeSummary(row && row.changes)),
    missing: h("span", { class: "ev-chip bad" }, icon("alert"), "找不到这个路径"),
  }[st];
  const detail = st === "changed" && row.changes ? changeDetail(row.changes) : null;
  const toggle = detail && h("button", { class: "ev-more", "aria-expanded": "false", onclick: (ev) => {
    const open = detail.classList.toggle("hidden") === false;
    ev.currentTarget.setAttribute("aria-expanded", String(open));
    ev.currentTarget.lastChild.textContent = open ? "收起" : "看看改了什么";
  } }, icon("chev"), "看看改了什么");
  return h("div", { class: "ev-row " + st, "data-path": p },
    h("span", { class: "ev-ic" }, icon(kind === "git" ? "git" : kind === "file" ? "file" : "folder")),
    h("div", { class: "ev-main" },
      h("div", { class: "ev-name" }, h("b", null, name), chip),
      h("div", { class: "ev-path", title: p }, p),
      h("div", { class: "ev-meta" }, row && row.mtime ? `最近改动 ${relTime(row.mtime)}` : null, toggle),
      detail),
    h("div", { class: "ev-tools" },
      h("button", { class: "btn icon sm ghost", title: "在资源管理器里打开", disabled: st === "missing", onclick: () => api("POST", "/api/reveal-any", { path: p }) }, icon("external")),
      h("button", { class: "btn icon sm ghost", title: st === "missing" ? "重新选择这个文件夹" : "换成别的文件夹", onclick: () => addEvidence(e, kind === "file" ? "files" : "folder", i) }, icon("folderOpen")),
      h("button", { class: "btn icon sm ghost danger", title: "移除（不会删除文件）", onclick: () => { e.evidence.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
}

function changeDetail(c) {
  const lines = [];
  if (c.log && c.log.length) lines.push(h("div", { class: "cd-head" }, "新提交"), ...c.log.slice(0, 12).map((l) => h("div", { class: "cd-line" }, l)));
  if (c.log && c.log.length > 12) lines.push(h("div", { class: "cd-line muted" }, `…还有 ${c.log.length - 12} 个`));
  for (const [k, label] of [["added", "新增"], ["modified", "修改"], ["removed", "删除"]]) {
    if (c[k] && c[k].length) lines.push(h("div", { class: "cd-head" }, label), ...c[k].slice(0, 8).map((l) => h("div", { class: "cd-line" }, l)));
  }
  if (c.uncommitted) lines.push(h("div", { class: "cd-line muted" }, `另有 ${c.uncommitted} 个文件有未提交的改动`));
  return h("div", { class: "change-detail hidden" }, lines);
}

function evidenceEditor(e) {
  const st = ui.evidence[e.id] || { paths: [] };
  const rows = Object.fromEntries((st.paths || []).map((r) => [r.path, r]));
  const box = h("div", { class: "evidence", id: "evidence" });
  box.append(h("div", { class: "ev-head" }, h("span", { class: "ev-title" }, "项目文件夹与证据"),
    h("span", { class: "hint" }, "Claude 会读里面的代码、README 和提交记录")));
  const pending = e.evidence.map((p) => rows[p]).filter((r) => r && (r.state === "changed" || r.state === "new"));
  if (pending.length) {
    const changed = pending.some((r) => r.state === "changed");
    const empty = !e.bullets.some((b) => getT(b, ui.editLang));
    box.append(h("div", { class: "sync-bar" + (changed ? " changed" : "") }, icon(changed ? "refresh" : "folderOpen"),
      h("span", { class: "nb-text" }, changed ? "项目有新进展" : "Claude 还没读过这个项目"),
      ui.chat.available && h("button", { class: "btn sm primary", onclick: () => aiEntry(empty && !changed ? "polish" : "sync", e.id, { lang: ui.editLang }) }, icon("sparkle"),
        changed ? "让 Claude 按新进展更新" : empty ? "让 Claude 读项目写要点" : "让 Claude 读一遍"),
      h("button", { class: "btn sm ghost", title: "现在的要点已经反映了项目的样子，不用 Claude 再读；项目以后再有改动才会提醒", onclick: () => markSynced(e.id) }, "标记为已同步")));
  }
  const list = h("div", { class: "ev-list" });
  e.evidence.forEach((p, i) => list.append(evidenceRow(e, p, i, rows[p])));
  if (!e.evidence.length) list.append(h("div", { class: "ev-empty" }, "还没有关联项目。选一个文件夹，Claude 写要点时会去读；项目以后有更新，这里和左边的经历库都会提醒你。"));
  box.append(list, h("div", { class: "ev-actions" },
    h("button", { class: "btn sm", onclick: () => addEvidence(e, "folder") }, icon("folder"), "选择项目文件夹"),
    h("button", { class: "btn sm ghost", onclick: () => addEvidence(e, "files") }, icon("file"), "添加文件"),
    h("button", { class: "btn sm ghost", onclick: () => typeEvidence(e) }, icon("pen"), "输入路径"),
    h("span", { class: "spacer" }),
    e.evidence.length ? h("button", { class: "btn sm ghost", title: "现在就检查这些文件夹有没有变化（平时切回窗口时会自动检查）", onclick: () => checkEvidence(true).then(() => toast("检查完了")) }, icon("refresh"), "检查更新") : null));
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
  if (!(await modal({ title: `删除「${display((entryById(id) || {}).title) || entryLabel(entryById(id))}」？`, message: "它会从经历库和所有岗位里移除。需要时可以从 .studio/history 找回。", ok: "删除", danger: true }))) return;
  ui.dirty = false;
  applyState(await api("DELETE", "/api/entries/" + encodeURIComponent(id)));
  select("profile");
  scheduleRender();
}

function profileForm() {
  const p = ui.draft;
  p.contacts = p.contacts || [];
  const rows = h("div", { class: "list-rows" });
  const v = version();
  const job = v && v.id !== ALL;
  const hiddenC = new Set(job ? v.hide_contacts || [] : []);
  p.contacts.forEach((c, i) => {
    const shown = !hiddenC.has(c.label || "");
    rows.append(h("div", { class: "lr", style: `grid-template-columns: ${job ? "22px " : ""}96px 1fr 1fr 30px` },
      job && h("button", { class: "check" + (shown ? " on" : ""), role: "checkbox", "aria-checked": String(shown),
        title: shown ? `在「${jobName(v)}」里隐藏` : `在「${jobName(v)}」里显示`,
        onclick: () => mutateVersion((x) => {
          const set = new Set(x.hide_contacts || []);
          if (shown) set.add(c.label || ""); else set.delete(c.label || "");
          x.hide_contacts = [...set];
          if (!x.hide_contacts.length) delete x.hide_contacts;
        }).then(buildEditorKeepDraft) }),
      h("input", { value: c.label ?? "", placeholder: "email", spellcheck: "false", oninput: (ev) => { c.label = ev.target.value; touch(); } }),
      h("input", { value: getT(c.value, ui.editLang), placeholder: "显示的文字", spellcheck: "false", oninput: (ev) => { c.value = ev.target.value; touch(); } }),
      h("input", { value: c.url ?? "", placeholder: "链接（可选）mailto: / https://", spellcheck: "false", oninput: (ev) => { c.url = ev.target.value; touch(); } }),
      h("button", { class: "btn icon sm ghost danger", title: "删除", onclick: () => { p.contacts.splice(i, 1); touch(); buildEditorKeepDraft(); } }, icon("x"))));
  });
  rows.append(h("button", { class: "btn sm", onclick: () => { p.contacts.push({ label: "", value: "", url: "" }); touch(); buildEditorKeepDraft(); } }, icon("plus"), "添加联系方式"));
  return [
    h("div", { class: "ed-head" }, h("h1", null, "个人信息"), h("span", { class: "spacer" }), langSwitch()),
    card("基本信息", null, [
      tfield("姓名", p, "name", { i18n: true }),
      h("div", { class: "field" }, h("label", null, "一句话介绍 / 求职方向"),
        h("div", { class: "row wrap" },
          h("span", { class: "muted", style: "font-size:12.5px;flex:1;min-width:200px" }, "每个岗位写自己的一句，在 ALL 和各岗位的设置里填。"),
          h("button", { class: "btn sm", onclick: () => select("version", ui.versionId) }, icon("settings"), `编辑「${job ? jobName(v) : "ALL"}」的这一句`))),
    ]),
    card("联系方式", job ? `按顺序显示在姓名下面；左边的勾 = 在「${jobName(v)}」里显示` : "按顺序显示在姓名下面；切到某个岗位后可以隐藏其中几项", rows),
    card("事实层", "只给 Claude 看", tfield("求职背景、限制、偏好", p, "notes", { multiline: true, rows: 5 }), { cls: "fact" }),
  ];
}

function sectionsForm() {
  if (!Array.isArray(ui.draft)) ui.draft = [];
  const list = ui.draft;
  const rows = h("div", { class: "list-rows" });
  list.forEach((s, i) => {
    const used = entries().some((e) => e.section === s.id);
    rows.append(h("div", { class: "lr", style: "grid-template-columns: 120px 1fr auto 26px 26px 26px" },
      h("input", { value: s.id, title: "板块 ID（经历里的 section 字段）", spellcheck: "false", oninput: (ev) => { s.id = ev.target.value; touch(); } }),
      h("input", { value: getT(s.title, ui.editLang), placeholder: "显示标题", oninput: (ev) => { s.title = setT(s.title, ui.editLang, ev.target.value); touch(); } }),
      uiSeg([{ value: "timeline", label: "时间线", tip: "标题 + 日期 + 要点" }, { value: "list", label: "列表", tip: "「类别：条目」一行一个，适合技能" }],
        s.kind || "timeline", (k) => { s.kind = k; touch(); buildEditorKeepDraft(); }, { cls: "sm", label: "板块类型" }),
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
  const langToggle = h("div", { class: "seg", role: "group", "aria-label": "内容语言" }, ["en", "zh"].map((l) => h("button", {
    class: (st.languages || []).includes(l) ? "on" : "", "aria-pressed": String((st.languages || []).includes(l)),
    onclick: () => {
      const set = new Set(st.languages || []);
      if (set.has(l)) set.delete(l); else set.add(l);
      st.languages = ["en", "zh"].filter((x) => set.has(x));
      if (!st.languages.length) st.languages = ["en"];
      saveSettings();
    } }, LANG_NAMES[l])));
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
      h("button", { class: "btn icon sm ghost", title: "在资源管理器里打开", onclick: () => api("POST", "/api/reveal-any", { path: p }) }, icon("external")),
      h("button", { class: "btn icon sm ghost danger", title: "移除（不会删除文件）", onclick: () => { st.project_roots = roots.filter((_, j) => j !== i); if (!st.project_roots.length) delete st.project_roots; saveSettings(); } }, icon("x"))))));
  return [
    h("div", { class: "ed-head" }, h("h1", null, "板块与设置"), h("span", { class: "spacer" }), langSwitch()),
    card("板块", "默认顺序；每个岗位还能单独调整", [
      h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, "时间线 = 标题 + 日期 + 要点；列表 = 「类别：条目」一行一个，适合技能。"), rows]),
    card("设置", null, [
      h("div", { class: "field" }, h("label", null, "内容语言", h("span", { class: "hint" }, "选两种时，文字按语言分开保存，编辑区会出现语言切换")), h("div", null, langToggle)),
      h("div", { class: "field" }, h("label", null, "导出目录", h("span", { class: "hint" }, "相对于 resume.yaml")),
        h("input", { value: st.export_dir || "exports", spellcheck: "false", onchange: (ev) => { st.export_dir = ev.target.value.trim() || "exports"; saveSettings(); } })),
    ]),
    card("我的项目文件夹", "放个人项目的地方，比如 E:/Forge", [
      h("p", { class: "muted", style: "margin:0 0 10px;font-size:12.5px" }, "Claude 会知道你的项目都在这里：可以让它在对话里帮你挑出值得写进简历的项目；给经历选文件夹时也会从这里开始找。"),
      rootRows,
      h("div", { class: "ev-actions" }, h("button", { class: "btn sm", onclick: addRoot }, icon("folder"), "选择文件夹"),
        roots.length && ui.chat.available ? h("button", { class: "btn sm ghost", onclick: () => sendChat(`看看我的项目文件夹（${roots.join("、")}），挑出最值得写进简历的几个项目，说说理由。先别改 resume.yaml，等我确认后再一个一个录入。`) }, icon("sparkle"), "让 Claude 挑项目") : null),
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
      h("button", { class: "check" + (hidden.has(sid) ? "" : " on"), title: "在这里显示", onclick: () => {
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
    v.id === ALL
      ? h("div", { class: "ed-head" }, h("h1", null, "ALL 设置"), h("span", { class: "id" }, "全部经历"), h("span", { class: "spacer" }), langSwitch())
      : h("div", { class: "ed-head" }, h("h1", null, "岗位设置"), h("span", { class: "id" }, v.id), h("span", { class: "spacer" }), langSwitch()),
    v.id === ALL
      ? card("ALL", "全集预览：全部经历、全部联系方式，不受一页限制；模板、语言和版面在右侧预览上方调整", tfield("一句话介绍 / 求职方向", v, "headline", { i18n: true, placeholder: "可以留空" }))
      : card("岗位信息", "模板、语言和版面在右侧预览上方调整；选哪些经历在左侧勾选", [
        h("div", { class: "grid2" }, tfield("名称", v, "label", { placeholder: "AI Agent 工程师" }), tfield("ID", v, "id", { hint: "也是导出文件名" })),
        tfield("一句话介绍 / 求职方向", v, "headline", { i18n: true, placeholder: "比如：AI Agent Engineer · LLM tooling & full-stack" }),
      ]),
    card("板块顺序与显示", v.id === ALL ? "只影响 ALL" : "只影响这个岗位", rows),
  ];
}

// ================================================================ AI polish (runs in the chat)
const AI_LABELS = { polish: "润色", translate: "写另一种语言", fit: "压成一行", sync: "按项目更新", batch_translate: "补齐另一种语言", batch_fit: "压成一行", batch_sync: "按项目更新" };

function aiDiff(id) {
  const a = ui.ai[id];
  const before = a.snapshot;
  const after = entryById(id) || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null));
  const fmt = (v) => {
    const one = (x) => (x && typeof x === "object" ? Object.entries(x).map(([l, t]) => `[${l}] ${t === "" ? "（不显示）" : t}`).join("\n   ") : String(x ?? ""));
    if (Array.isArray(v)) return v.map((x) => "• " + one(x)).join("\n");
    return one(v);
  };
  const names = { title: "名称", subtitle: "副标题", bullets: "要点", tech: "技术", location: "地点", start: "开始", end: "结束", date: "日期", link: "链接", notes: "事实层", evidence: "证据路径" };
  const close = () => { delete ui.ai[id]; buildEditor(); };
  return card(keys.length ? "Claude 改了这一条" : "Claude 没有改动这一条", `${AI_LABELS[a.action] || ""} · 对话里有它的说明`, [
    keys.map((k) => [h("div", { class: "k" }, names[k] || k), h("div", { class: "before" }, fmt(before[k])), h("div", { class: "after" }, fmt(after[k]))]),
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "btn sm", onclick: close }, icon("check"), keys.length ? "保留" : "知道了"),
      keys.length && h("button", { class: "btn sm danger-soft", onclick: () => undoAi(id) }, icon("undo"), "撤销，恢复到改动前"),
      h("button", { class: "btn sm ghost", onclick: () => setTab("chat") }, icon("chat"), "看对话")),
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
    toast("已恢复到改动前");
  } catch (e) { toast("撤销失败：" + e.message); }
}

// one entry: snapshot it first so the result can be compared and undone
async function aiEntry(action, entryId, extra = {}) {
  await saveDraft();
  const snapshot = clone(entryById(entryId));
  delete ui.ai[entryId];
  const ok = await sendChat(extra.text || "", { action, entryId, target: extra.target, lang: extra.lang });
  if (ok) ui.ai[entryId] = { chat: ui.chat.id, action, snapshot, done: false };
}

// a whole version: snapshot every entry; each one Claude touches gets its own diff + undo
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
    if (!touched.length) { toast("Claude 这次没有改动任何经历，详见对话"); return; }
  }
  if (!touched.length) return;
  const open = ui.sel.kind === "entry" && touched.includes(ui.sel.id);
  if (open && !ui.dirty) buildEditor();
  const first = touched.find((id) => id !== ui.sel.id) || touched[0];
  if (!open || touched.length > 1) {
    const name = display((entryById(first) || {}).title) || entryLabel(entryById(first));
    toast(touched.length > 1 ? `Claude 改了 ${touched.length} 条经历，打开每一条都能看到改了什么、可以撤销` : `Claude 改完了「${name}」`,
      { ms: 6000, action: { label: "查看", fn: () => select("entry", first) } });
  }
}

// ---- bilingual checks (mirror resume/model.py missing_text) ----
const CJK = /[㐀-鿿]/;
function lacks(v, lang, bullet = false) {
  if (v && typeof v === "object") {
    const others = Object.entries(v).some(([k, x]) => k !== lang && x);
    return others && (bullet ? !(lang in v) : !v[lang]);
  }
  if (bullet && v) return (lang === "zh") !== CJK.test(String(v));
  return false;
}
function missingIn(e, lang) {
  return ["title", "subtitle", "location", "date"].some((k) => lacks(e[k], lang)) || (e.bullets || []).some((b) => lacks(b, lang, true));
}

// ---- line measurement ----
function inPreview(id) {
  const v = version();
  if (!v) return false;
  const e = entryById(id);
  if (!e || (v.hide_sections || []).includes(e.section)) return false;
  return v.id === ALL || (v.entries || []).includes(id);
}
// {bulletIndex: {n, fill}}; undefined = not measured yet; null = not in the preview, cannot be measured
function measuredFor(id, lang) {
  if (ui.linesFor[lang] !== ui.versionId) return undefined;
  if (!inPreview(id)) return null;
  return (ui.lines[lang] || {})[id] || {};
}
function longBullets(lang = (version() || {}).lang) {
  if (ui.linesFor[lang] !== ui.versionId) return [];
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
  ui.w.tpl.setOptions((S.templates || []).map((t) => ({ value: t.id, label: t.name, desc: t.description })), v ? v.template : null);
  ui.w.tpl.disabled = !v;
  $("#lang-seg").replaceChildren(...["en", "zh"].map((l) => h("button", { class: v && v.lang === l ? "on" : "", disabled: !v,
    title: v ? `预览和导出用${LANG_LONG[l]}` : "", onclick: () => mutateVersion((x) => { x.lang = l; }) }, LANG_NAMES[l])));
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
  layoutBuiltFor = ui.versionId;
  const reset = (key) => h("button", { class: "btn icon sm ghost", title: "恢复默认", "data-reset": key, onclick: () => queueLayout(key, undefined, 0) }, icon("undo"));
  ui.w.accent = colorField({
    value: layoutValue("accent"), onChange: (c) => queueLayout("accent", c, 0), onReset: () => queueLayout("accent", undefined, 0),
    isDefault: () => !((version() || {}).layout || {}).accent,
  });
  $("#layout-panel").replaceChildren(
    ...SLIDERS.map(([key, label, min, max, step, unit]) => {
      const val = h("span", { class: "val" });
      const input = h("input", { type: "range", min, max, step, "data-key": key, "aria-label": label,
        oninput: (ev) => { val.textContent = ev.target.value + unit; queueLayout(key, parseFloat(ev.target.value)); } });
      return h("div", { class: "ctl" }, h("span", null, label), input, val, reset(key));
    }),
    h("div", { class: "ctl" }, h("span", null, "强调色"), ui.w.accent.el, h("span"), reset("accent")),
    h("div", { class: "ctl" }, h("span", null, "纸张"), h("div", { id: "ctl-paper" }), h("span"), reset("paper")),
    h("div", { class: "ctl wide" }, h("span", null, "正文字体"),
      h("input", { type: "text", id: "ctl-font", placeholder: "留空用模板默认，比如 TeX Gyre Heros / Times New Roman / Arial", spellcheck: "false",
        onchange: (ev) => queueLayout("font", ev.target.value.trim() || undefined, 0) })));
}

function updateLayoutPanel() {
  const v = version();
  if (!v) return;
  const active = document.activeElement;
  const set = (el, value) => { if (el && el !== active) el.value = value; };
  const paper = layoutValue("paper");
  $("#ctl-paper").replaceChildren(uiSeg([{ value: "a4", label: "A4", tip: "21 × 29.7 cm，澳洲和中国常用" }, { value: "letter", label: "Letter", tip: "8.5 × 11 in，北美常用" }],
    paper, (p) => queueLayout("paper", p, 0), { label: "纸张" }));
  set($("#ctl-font"), (v.layout || {}).font || "");
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
    if (vid === ui.versionId) {
      r.last = res;
      if (res.ok) { ui.lines[res.lang] = res.lines || {}; ui.linesFor[res.lang] = vid; ui.missing = res.missing || []; }
      await showPages(res);
      setBadge(res);
      paintLines();
    }
    await measureEditLang(vid);
  } catch (e) {
    setBadge({ ok: false, errors: [e.message] });
  } finally {
    r.busy = false;
    if (r.again) { r.again = false; doRender(); }
  }
}

// The preview shows one language; when the form edits the other one, measure that too
// (same template and layout, no images), so every bullet's line count matches what is typed.
async function measureEditLang(vid) {
  const v = version();
  const L = ui.editLang;
  if (!v || v.id !== vid || langs().length < 2 || L === v.lang) return;
  try {
    const res = await api("POST", `/api/versions/${encodeURIComponent(vid)}/measure`, { lang: L });
    if (vid === ui.versionId && res.ok) { ui.lines[L] = res.lines || {}; ui.linesFor[L] = vid; paintLines(); }
  } catch (_) { /* the preview's own errors are shown already */ }
}

// The strip under the toolbar: what still needs attention in this version, each with a fix.
function renderIssues() {
  const box = $("#issues");
  const v = version();
  if (!v || !S) { box.classList.add("hidden"); return; }
  const lang = v.lang;
  const long = longBullets(lang);
  const missing = (ui.missing || []).filter((m) => entryById(m.id));
  const items = [];
  if (long.length) {
    items.push(h("button", { class: "issue warn", "aria-haspopup": "menu", onclick: (ev) => openMenu(ev.currentTarget, [
      { header: `${LANG_LONG[lang]}要点超过一行` },
      ...long.map((b) => { const e = entryById(b.id); return { label: `${display(e.title) || entryLabel(e)} · 第 ${b.index + 1} 条`, hint: `${b.n} 行 · ${Math.round(b.fill * 100)}%`, icon: "compress", onClick: () => jumpToBullet(b.id, b.index, lang) }; }),
      "sep",
      { label: "让 Claude 全部压成一行", icon: "sparkle", disabled: !ui.chat.available, onClick: () => aiBatch("batch_fit") },
    ], { minWidth: 280 }) }, icon("compress"), `${long.length} 条要点超过一行`));
  }
  if (missing.length && langs().length > 1) {
    items.push(h("button", { class: "issue", "aria-haspopup": "menu", onclick: (ev) => openMenu(ev.currentTarget, [
      { header: `这些经历还缺${LANG_LONG[lang]}，暂时显示另一种语言` },
      ...missing.map((m) => { const e = entryById(m.id); return { label: display(e.title) || entryLabel(e), hint: m.bullets && m.bullets.length ? `${m.bullets.length} 条要点` : "标题等", icon: "translate", onClick: () => { ui.editLang = lang; select("entry", m.id); } }; }),
      "sep",
      { label: `让 Claude 补齐${LANG_LONG[lang]}版`, icon: "sparkle", disabled: !ui.chat.available, onClick: () => aiBatch("batch_translate", { target: lang }) },
    ], { minWidth: 280 }) }, icon("translate"), `${missing.length} 条经历缺${LANG_LONG[lang]}`));
  }
  box.replaceChildren(...items);
  box.classList.toggle("hidden", !items.length);
}

async function jumpToBullet(id, index, lang) {
  if (langs().includes(lang)) ui.editLang = lang;
  await select("entry", id);
  const t = document.querySelector(`#bullets textarea[data-bullet="${index}"]`);
  if (t) { t.scrollIntoView({ block: "center", behavior: "smooth" }); t.focus(); t.closest(".bullet").classList.add("pulse"); }
}

function setBadge(res) {
  const b = $("#page-badge");
  const errs = $("#render-errors");
  errs.classList.add("hidden");
  if (!res) { b.className = "badge"; b.textContent = ""; return; }
  if (res.busy) { b.className = "badge busy"; b.textContent = "渲染中"; return; }
  if (res.ok && res.version === ALL) {
    b.className = "badge info";
    b.replaceChildren(`全部经历 · ${res.pages} 页`);
    setTip(b, "ALL 不受一页限制；投递用的简历请在岗位里做");
  } else if (res.ok) {
    const one = res.pages === 1;
    b.className = "badge " + (one ? "ok" : "bad");
    b.replaceChildren(icon(one ? "check" : "alert"), one ? "1 页" : `${res.pages} 页 · 超出一页`);
    setTip(b, one ? `渲染用时 ${res.seconds}s` : "试试在「版面」里减小字号、边距、间距，或在这个岗位里少选一条经历");
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
  const all = res.version === ALL;
  box.replaceChildren(...imgs.map((img, i) => h("div", { class: "page" + (i > 0 && !all ? " overflow" : ""), style: `width:${w}px` },
    res.images > 1 ? h("span", { class: "plabel" }, i === 0 || all ? `第 ${i + 1} 页` : `第 ${i + 1} 页 · 超出一页`) : null, img)));
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
  es.onopen = () => { conn.className = "conn ok"; setTip(conn, "已连接"); if (S) { refresh(); checkEvidence(); } };
  es.onerror = () => { conn.className = "conn bad"; setTip(conn, "连接断开，正在重连…"); };
  es.addEventListener("file", (m) => { const d = JSON.parse(m.data); if (S && d.digest === S.digest) return; refresh(); });
  es.addEventListener("evidence", () => checkEvidence(true));
  listenChat(es);
}

function wire() {
  initTips();
  initContextMenu();
  for (const b of document.querySelectorAll(".tab")) b.onclick = () => setTab(b.dataset.tab);

  ui.w.version = uiSelect({ id: "version-select", cls: "version", label: "ALL 或岗位", menuWidth: 300,
    title: "ALL = 全部经历；其余是各个岗位的简历", onChange: (v) => switchVersion(v) });
  $("#version-slot").replaceWith(ui.w.version.el);
  $("#version-menu-btn").append(icon("more"));
  $("#version-menu-btn").onclick = openVersionMenu;

  ui.w.tpl = uiSelect({ id: "tpl-select", label: "模板", menuWidth: 300, onChange: (t) => mutateVersion((x) => { x.template = t; }) });
  $("#tpl-slot").replaceWith(ui.w.tpl.el);
  $("#layout-toggle").prepend(icon("settings"));
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
    if (e.key === "Escape" && !Float.cur && ui.layoutOpen) { ui.layoutOpen = false; renderPreviewToolbar(); }
  });
  window.addEventListener("beforeunload", () => { if (ui.dirty) saveDraft(); });
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
  select("profile");
  setTab(store("tab") === "chat" ? "chat" : "library");
  connect();
  loadChats().catch(() => {});
  loadClaudeInfo();
  checkEvidence();
  scheduleRender(0);
})();
