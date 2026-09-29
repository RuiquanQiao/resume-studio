// Resume Studio front end. No build step: plain DOM + fetch + EventSource.
// The server (and resume.yaml on disk) is the source of truth; this page only
// keeps a draft of the item being edited and autosaves it.
"use strict";

const $ = (s) => document.querySelector(s);
const clone = (x) => JSON.parse(JSON.stringify(x ?? null));
const LANG_NAMES = { en: "EN", zh: "中文" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
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

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

function store(key, val) {
  try {
    if (val === undefined) return localStorage.getItem("rs:" + key);
    localStorage.setItem("rs:" + key, val);
  } catch (_) { return null; }
}

// ---------------------------------------------------------------- state
let S = null; // last server state
const ui = {
  versionId: null,
  sel: { kind: "profile", id: null }, // what the middle pane edits
  draft: null, formBase: null, dirty: false, editSeq: 0,
  saving: false, refreshAfterSave: false, saveTimer: null, conflict: null,
  editLang: null,
  render: { timer: null, busy: false, again: false, front: "pdfA" },
  ai: { job: null, snapshot: null },
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

// ---------------------------------------------------------------- i18n text
// Any text value is a plain string (all languages) or a map {en: "...", zh: "..."}.
function getT(v, lang) {
  if (v && typeof v === "object") return v[lang] ?? "";
  return v ?? "";
}
function isNeutral(v) { return !(v && typeof v === "object"); }
function setT(old, lang, val) {
  const ls = langs();
  if (ls.length <= 1 && isNeutral(old)) return val;
  let obj;
  if (old && typeof old === "object") obj = { ...old };
  else obj = old ? Object.fromEntries(ls.map((l) => [l, old])) : {};
  obj[lang] = val;
  return obj;
}
function display(v) {
  const L = (version() && version().lang) || langs()[0];
  return getT(v, L) || (v && typeof v === "object" ? Object.values(v).find(Boolean) : "") || "";
}

// ---------------------------------------------------------------- loading
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
  if (!versions().some((v) => v.id === ui.versionId)) {
    const saved = store("version");
    ui.versionId = versions().some((v) => v.id === saved) ? saved : (versions()[0] || {}).id || null;
  }
  if (!ui.editLang || !langs().includes(ui.editLang)) {
    ui.editLang = (version() && langs().includes(version().lang) && version().lang) || langs()[0];
  }
  renderLeft();
  renderControls();
  renderClaudeAvailability();
}

// Called after state changed by someone else: rebuild the form if it is stale.
function syncEditor() {
  const { kind, id } = ui.sel;
  if ((kind === "entry" || kind === "version") && !currentItem(kind, id)) {
    select("profile");
    return;
  }
  const hNow = itemHash(kind, id);
  if (hNow === ui.formBase) return;
  if (ui.dirty) {
    ui.conflict = { current: clone(currentItem(kind, id)) };
    buildEditor();
    return;
  }
  buildEditor({ flash: true });
}

// ---------------------------------------------------------------- saving
function touch() {
  ui.dirty = true;
  ui.editSeq++;
  setSaveStatus("未保存…");
  clearTimeout(ui.saveTimer);
  ui.saveTimer = setTimeout(saveDraft, 600);
}

function setSaveStatus(msg, bad) {
  const el = $("#save-status");
  el.textContent = msg;
  el.style.color = bad ? "var(--bad)" : "";
}

async function saveDraft(force = false) {
  clearTimeout(ui.saveTimer);
  if (!ui.dirty || ui.saving || ui.conflict && !force) return;
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
    ui.conflict = null;
    if (ui.editSeq === seq) { ui.dirty = false; setSaveStatus("已保存"); }
    else ui.saveTimer = setTimeout(saveDraft, 400);
    scheduleRender();
  } catch (e) {
    if (e.status === 409) {
      ui.conflict = { current: e.data && e.data.current };
      buildEditor();
      setSaveStatus("有冲突", true);
    } else setSaveStatus("保存失败：" + e.message, true);
  } finally {
    ui.saving = false;
    if (ui.refreshAfterSave) { ui.refreshAfterSave = false; refresh(); }
  }
}

// Version changes from the sidebar / layout panel: re-read, apply, retry once on conflict.
async function mutateVersion(fn) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const v = clone(version());
    if (!v) return;
    fn(v);
    try {
      const s = await api("PUT", "/api/item", { kind: "version", id: ui.versionId, data: v, base: itemHash("version", ui.versionId) });
      applyState(s);
      syncEditor();
      scheduleRender();
      return;
    } catch (e) {
      if (e.status !== 409 || attempt) { toast("保存失败：" + e.message); return; }
      applyState(await api("GET", "/api/state"));
    }
  }
}

// ---------------------------------------------------------------- left pane
function renderLeft() {
  const left = $("#left");
  left.replaceChildren();
  const v = version();

  left.append(
    h("div", { class: "vbar" },
      h("select", { onchange: (e) => switchVersion(e.target.value), title: "简历版本" },
        versions().map((x) => h("option", { value: x.id, selected: x.id === ui.versionId }, `${x.label || x.id}  (${x.id})`)))),
    h("div", { class: "vbtns" },
      h("button", { onclick: () => select("version", ui.versionId), disabled: !v }, "版本设置"),
      h("button", { onclick: () => newVersion(null) }, "新建"),
      h("button", { onclick: () => newVersion(ui.versionId), disabled: !v }, "复制"),
      h("button", { class: "danger", onclick: deleteVersion, disabled: !v }, "删除")),
    h("button", { class: "navrow" + (ui.sel.kind === "profile" ? " active" : ""), onclick: () => select("profile") }, "个人信息"),
    h("button", { class: "navrow" + (ui.sel.kind === "sections" ? " active" : ""), onclick: () => select("sections") }, "板块与设置"),
  );

  const included = new Set(v ? v.entries || [] : []);
  const order = v ? v.entries || [] : [];
  for (const sec of sections()) {
    const mine = entries().filter((e) => e.section === sec.id);
    const inV = order.map(entryById).filter((e) => e && e.section === sec.id);
    const outV = mine.filter((e) => !included.has(e.id));
    left.append(h("div", { class: "sec" },
      h("div", { class: "sec-head" },
        h("span", null, display(sec.title) || sec.id),
        h("button", { class: "icon", title: "在这个板块新增一条经历", onclick: () => newEntry(sec.id) }, "+ 新增")),
      [...inV, ...outV].map((e, i) => entryRow(e, included.has(e.id), i, inV.length))));
  }
  const orphans = entries().filter((e) => !sectionById(e.section));
  if (orphans.length) {
    left.append(h("div", { class: "sec" }, h("div", { class: "sec-head" }, "未归类"),
      orphans.map((e) => entryRow(e, included.has(e.id), 0, 0))));
  }
  if (S.warnings && S.warnings.length) {
    left.append(h("div", { class: "warnings" }, S.warnings.map((w) => h("div", null, "⚠ " + w))));
  }
}

function entryRow(e, isIn, idx, nIn) {
  const active = ui.sel.kind === "entry" && ui.sel.id === e.id;
  return h("div", { class: "entry" + (isIn ? "" : " out") + (active ? " active" : ""), onclick: () => select("entry", e.id) },
    h("input", {
      type: "checkbox", checked: isIn, title: isIn ? "从当前版本移除" : "加入当前版本",
      onclick: (ev) => { ev.stopPropagation(); toggleInclude(e.id, ev.target.checked); },
    }),
    h("span", { class: "etitle" }, display(e.title) || "（未命名）"),
    h("span", { class: "eid" }, e.id),
    isIn && h("span", { class: "moves" },
      h("button", { class: "icon ghost", title: "上移", disabled: idx === 0, onclick: (ev) => { ev.stopPropagation(); moveInVersion(e.id, -1); } }, "↑"),
      h("button", { class: "icon ghost", title: "下移", disabled: idx >= nIn - 1, onclick: (ev) => { ev.stopPropagation(); moveInVersion(e.id, 1); } }, "↓")));
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

async function switchVersion(id) {
  await saveDraft();
  ui.versionId = id;
  store("version", id);
  const v = version();
  if (v && langs().includes(v.lang)) ui.editLang = v.lang;
  renderLeft();
  renderControls(true);
  if (ui.sel.kind === "version") select("version", id);
  else buildEditor();
  scheduleRender(0);
}

async function newVersion(copyFrom) {
  const id = prompt("新版本的 ID（比如 RES-AI-EN）", copyFrom ? copyFrom + "-2" : "RES-NEW");
  if (!id) return;
  try {
    const r = await api("POST", "/api/versions", { id, copy_from: copyFrom });
    applyState(r);
    switchVersion(r.id);
  } catch (e) { toast(e.message); }
}

async function deleteVersion() {
  if (!confirm(`删除版本 ${ui.versionId}？经历库不受影响。`)) return;
  applyState(await api("DELETE", "/api/versions/" + encodeURIComponent(ui.versionId)));
  ui.versionId = null;
  applyState(S);
  select("profile");
  scheduleRender(0);
}

async function newEntry(section) {
  await saveDraft();
  const r = await api("POST", "/api/entries", { section });
  applyState(r);
  select("entry", r.id);
}

// ---------------------------------------------------------------- middle pane
async function select(kind, id = null) {
  await saveDraft();
  ui.sel = { kind, id };
  ui.conflict = null;
  ui.dirty = false;
  renderLeft();
  buildEditor();
}

function buildEditor(opts = {}) {
  const mid = $("#middle");
  const { kind, id } = ui.sel;
  const item = currentItem(kind, id);
  if (!ui.conflict) {
    ui.draft = clone(item);
    ui.formBase = itemHash(kind, id);
    ui.dirty = false;
  }
  mid.replaceChildren();
  if (ui.conflict) mid.append(conflictBanner());
  const builders = { entry: entryForm, profile: profileForm, sections: sectionsForm, version: versionForm };
  if (item == null && kind !== "sections") { mid.append(h("p", { class: "muted" }, "没有内容。")); return; }
  mid.append(builders[kind]());
  if (opts.flash) { mid.classList.remove("flash"); void mid.offsetWidth; mid.classList.add("flash"); }
}

function conflictBanner() {
  return h("div", { class: "banner warn" },
    "这一项刚被 Claude 或另一个窗口修改过，你这里还有没保存的改动。",
    h("button", { onclick: () => { ui.conflict = null; ui.dirty = false; buildEditor({ flash: true }); } }, "载入最新版本（放弃我的改动）"),
    h("button", { onclick: () => saveDraft(true) }, "用我的版本覆盖"));
}

function langSwitch() {
  if (langs().length <= 1) return null;
  return h("span", { class: "langs" }, langs().map((l) =>
    h("button", { class: l === ui.editLang ? "on" : "", onclick: () => { ui.editLang = l; buildEditorKeepDraft(); } }, LANG_NAMES[l] || l)));
}

function buildEditorKeepDraft() {
  // re-render the form from the current draft (e.g. after switching edit language)
  const keep = { draft: ui.draft, dirty: ui.dirty, base: ui.formBase };
  const mid = $("#middle");
  mid.replaceChildren();
  ui.draft = keep.draft; ui.dirty = keep.dirty; ui.formBase = keep.base;
  if (ui.conflict) mid.append(conflictBanner());
  const builders = { entry: entryForm, profile: profileForm, sections: sectionsForm, version: versionForm };
  mid.append(builders[ui.sel.kind]());
}

// A text field bound to draft[obj][key]; `i18n` stores per-language values.
function tfield(label, obj, key, { i18n = false, multiline = false, placeholder = "", rows = 2, help = "" } = {}) {
  const val = i18n ? getT(obj[key], ui.editLang) : (obj[key] ?? "");
  const onInput = (ev) => {
    obj[key] = i18n ? setT(obj[key], ui.editLang, ev.target.value) : ev.target.value;
    touch();
  };
  const input = multiline
    ? h("textarea", { rows, placeholder, oninput: onInput }, val)
    : h("input", { value: val, placeholder, oninput: onInput });
  const tag = i18n && langs().length > 1
    ? h("span", { class: "tag" + (isNeutral(obj[key]) && obj[key] ? " neutral" : ""), title: isNeutral(obj[key]) ? "目前所有语言共用这一段文字；修改后会拆成分语言保存" : "" },
      isNeutral(obj[key]) && obj[key] ? "通用" : LANG_NAMES[ui.editLang] || ui.editLang)
    : null;
  return h("div", { class: "field" }, h("label", null, label, tag, help && h("span", { class: "muted" }, help)), input);
}

function entryForm() {
  const e = ui.draft;
  const sec = sectionById(e.section);
  const isList = sec && sec.kind === "list";
  e.bullets = e.bullets || [];
  e.tech = e.tech || [];
  e.evidence = e.evidence || [];
  const wrap = h("div");

  if (ui.ai.snapshot && ui.ai.snapshot.id === e.id && ui.ai.done) wrap.append(aiDiff());

  wrap.append(h("div", { class: "ed-head" },
    h("h2", null, display(e.title) || "（未命名）"), h("span", { class: "mono muted" }, e.id), h("span", { class: "spacer" }), langSwitch()));

  wrap.append(h("div", { class: "grid2" },
    h("div", { class: "field" }, h("label", null, "板块"),
      h("select", { onchange: (ev) => { e.section = ev.target.value; touch(); buildEditorKeepDraft(); } },
        sections().map((s) => h("option", { value: s.id, selected: s.id === e.section }, display(s.title) || s.id)))),
    tfield("ID", e, "id", { placeholder: "EXP-001" })));

  wrap.append(tfield(isList ? "类别名" : "名称（公司 / 学校 / 项目）", e, "title", { i18n: true }));
  if (!isList) {
    wrap.append(h("div", { class: "grid2" },
      tfield("副标题（职位 / 学位 / 角色）", e, "subtitle", { i18n: true }),
      tfield("地点", e, "location", { i18n: true })));
    wrap.append(h("div", { class: "grid2" },
      tfield("开始", e, "start", { placeholder: "2025-07" }),
      tfield("结束", e, "end", { placeholder: "2026-02 或 present" })));
    wrap.append(h("div", { class: "grid2" },
      tfield("自定义日期文字（可选，覆盖上面两项）", e, "date", { i18n: true }),
      tfield("链接", e, "link", { placeholder: "https://…" })));
  }
  wrap.append(h("div", { class: "field" },
    h("label", null, isList ? "条目（逗号分隔）" : "技术 / 关键词（逗号分隔）"),
    h("input", {
      value: e.tech.map((t) => display(t)).join(", "),
      oninput: (ev) => { e.tech = ev.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean); touch(); },
    })));

  wrap.append(bulletsEditor(e));

  wrap.append(h("div", { class: "fact" },
    h("h3", null, "事实层（不会出现在简历上）"),
    h("div", { class: "hint" }, "写真实情况：哪些是你做的、哪些是 AI 写的、数字怎么来的、不好意思写进简历的实情。AI 润色时只在这里能支撑的范围内包装。"),
    tfield("实际情况 / 原始想法", e, "notes", { multiline: true, rows: 6 }),
    h("div", { class: "field" },
      h("label", null, "证据路径（每行一个，Claude 可以去读）"),
      h("textarea", {
        rows: 2, placeholder: "E:/Forge/Paper2Exam",
        oninput: (ev) => { e.evidence = ev.target.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean); touch(); },
      }, e.evidence.join("\n")))));

  const instr = h("input", { placeholder: "补充要求（可选），比如：更突出架构设计；第二条太夸张了" });
  const avail = S.claude && S.claude.available;
  wrap.append(h("div", { class: "ai" },
    h("h3", null, "让 Claude 润色这一条"),
    h("div", { class: "row" }, instr,
      h("button", { class: "primary", disabled: !avail || !!ui.ai.job, title: avail ? "" : "没有找到 claude 命令", onclick: () => polish(e.id, instr.value) }, "润色"))));

  wrap.append(h("div", null, h("button", { class: "danger", onclick: () => deleteEntry(e.id) }, "删除这条经历")));
  return wrap;
}

function bulletsEditor(e) {
  const box = h("div", { class: "field bullets" }, h("label", null, "要点", langs().length > 1 && h("span", { class: "tag" }, LANG_NAMES[ui.editLang])));
  const redraw = () => { box.replaceWith(bulletsEditor(e)); };
  e.bullets.forEach((b, i) => {
    box.append(h("div", { class: "brow" },
      h("textarea", { rows: 2, oninput: (ev) => { e.bullets[i] = setT(e.bullets[i], ui.editLang, ev.target.value); touch(); } }, getT(b, ui.editLang)),
      h("div", { class: "bb" },
        h("button", { class: "icon", title: "上移", disabled: i === 0, onclick: () => { [e.bullets[i - 1], e.bullets[i]] = [e.bullets[i], e.bullets[i - 1]]; touch(); redraw(); } }, "↑"),
        h("button", { class: "icon", title: "下移", disabled: i === e.bullets.length - 1, onclick: () => { [e.bullets[i + 1], e.bullets[i]] = [e.bullets[i], e.bullets[i + 1]]; touch(); redraw(); } }, "↓"),
        h("button", { class: "icon danger", title: "删除这条要点（所有语言）", onclick: () => { e.bullets.splice(i, 1); touch(); redraw(); } }, "✕"))));
  });
  box.append(h("div", null,
    h("button", { onclick: () => { e.bullets.push(""); touch(); redraw(); } }, "+ 添加要点"),
    h("span", { class: "muted", style: "margin-left:8px;font-size:12px" }, "支持 **加粗**、*斜体*、[文字](链接)")));
  return box;
}

async function deleteEntry(id) {
  if (!confirm(`删除 ${id}？它会从所有版本里移除。需要时可以从 git 或 .studio/history 找回。`)) return;
  ui.dirty = false;
  applyState(await api("DELETE", "/api/entries/" + encodeURIComponent(id)));
  select("profile");
  scheduleRender();
}

function profileForm() {
  const p = ui.draft;
  p.contacts = p.contacts || [];
  const wrap = h("div");
  wrap.append(h("div", { class: "ed-head" }, h("h2", null, "个人信息"), h("span", { class: "spacer" }), langSwitch()));
  wrap.append(tfield("姓名", p, "name", { i18n: true }));
  wrap.append(tfield("一句话介绍 / 求职方向", p, "headline", { i18n: true }));
  const list = h("div", { class: "field rowlist" }, h("label", null, "联系方式（显示文字 + 可选链接）"));
  const redraw = () => { list.replaceWith(profileForm().querySelector(".rowlist")); };
  p.contacts.forEach((c, i) => {
    list.append(h("div", { class: "r", style: "grid-template-columns: 90px 1fr 1fr auto" },
      h("input", { value: c.label ?? "", placeholder: "email", oninput: (ev) => { c.label = ev.target.value; touch(); } }),
      h("input", { value: getT(c.value, ui.editLang), placeholder: "显示文字", oninput: (ev) => { c.value = ev.target.value; touch(); } }),
      h("input", { value: c.url ?? "", placeholder: "mailto: / https://", oninput: (ev) => { c.url = ev.target.value; touch(); } }),
      h("button", { class: "icon danger", onclick: () => { p.contacts.splice(i, 1); touch(); buildEditorKeepDraft(); } }, "✕")));
  });
  list.append(h("button", { onclick: () => { p.contacts.push({ label: "", value: "", url: "" }); touch(); buildEditorKeepDraft(); } }, "+ 添加联系方式"));
  void redraw;
  wrap.append(list);
  wrap.append(h("div", { class: "fact" }, h("h3", null, "事实层（不会出现在简历上）"),
    tfield("求职背景、限制、偏好", p, "notes", { multiline: true, rows: 5 })));
  return wrap;
}

function sectionsForm() {
  // this page edits two items: `sections` (draft) and `settings` (saved directly)
  if (!Array.isArray(ui.draft)) ui.draft = [];
  const list = ui.draft;
  const wrap = h("div");
  wrap.append(h("div", { class: "ed-head" }, h("h2", null, "板块"), h("span", { class: "spacer" }), langSwitch()));
  wrap.append(h("p", { class: "muted" }, "板块的默认顺序。timeline = 标题 + 日期 + 要点；list = 「类别：条目」一行一个（适合技能）。每个版本还可以在「版本设置」里单独调整顺序。"));
  const rows = h("div", { class: "rowlist" });
  list.forEach((s, i) => {
    const used = entries().some((e) => e.section === s.id);
    rows.append(h("div", { class: "r", style: "grid-template-columns: 110px 1fr 100px auto auto auto" },
      h("input", { value: s.id, title: "板块 ID（经历里的 section 字段）", oninput: (ev) => { s.id = ev.target.value; touch(); } }),
      h("input", { value: getT(s.title, ui.editLang), placeholder: "显示标题", oninput: (ev) => { s.title = setT(s.title, ui.editLang, ev.target.value); touch(); } }),
      h("select", { onchange: (ev) => { s.kind = ev.target.value; touch(); } },
        ["timeline", "list"].map((k) => h("option", { value: k, selected: (s.kind || "timeline") === k }, k))),
      h("button", { class: "icon", disabled: i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; touch(); buildEditorKeepDraft(); } }, "↑"),
      h("button", { class: "icon", disabled: i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; touch(); buildEditorKeepDraft(); } }, "↓"),
      h("button", { class: "icon danger", disabled: used, title: used ? "还有经历在这个板块里" : "删除板块", onclick: () => { list.splice(i, 1); touch(); buildEditorKeepDraft(); } }, "✕")));
  });
  rows.append(h("button", { onclick: () => { list.push({ id: "new-section", title: "New section", kind: "timeline" }); touch(); buildEditorKeepDraft(); } }, "+ 添加板块"));
  wrap.append(rows);

  const st = clone(doc().settings || {});
  const saveSettings = async () => {
    try { applyState(await api("PUT", "/api/item", { kind: "settings", data: st, base: itemHash("settings") })); scheduleRender(); }
    catch (e) { toast("保存失败：" + e.message); }
  };
  wrap.append(h("h2", { style: "font-size:16px;margin-top:28px" }, "设置"));
  wrap.append(h("div", { class: "field" }, h("label", null, "内容语言（多于一种时，编辑区会出现语言切换）"),
    h("div", null, ["en", "zh"].map((l) => h("label", { style: "margin-right:14px" },
      h("input", { type: "checkbox", checked: (st.languages || []).includes(l), onchange: (ev) => {
        const set = new Set(st.languages || []);
        ev.target.checked ? set.add(l) : set.delete(l);
        st.languages = ["en", "zh"].filter((x) => set.has(x));
        if (!st.languages.length) st.languages = ["en"];
        saveSettings();
      } }), " ", LANG_NAMES[l])))));
  wrap.append(h("div", { class: "field" }, h("label", null, "导出目录（相对 resume.yaml）"),
    h("input", { value: st.export_dir || "exports", onchange: (ev) => { st.export_dir = ev.target.value.trim() || "exports"; saveSettings(); } })));
  return wrap;
}

function versionForm() {
  const v = ui.draft;
  const wrap = h("div");
  wrap.append(h("div", { class: "ed-head" }, h("h2", null, "版本设置"), h("span", { class: "mono muted" }, v.id)));
  wrap.append(h("div", { class: "grid2" }, tfield("版本 ID", v, "id"), tfield("名称", v, "label")));
  wrap.append(h("p", { class: "muted" }, "模板、语言和版面参数在右侧预览上方调整；要选哪些经历，在左侧勾选。"));
  const all = sections().map((s) => s.id);
  const custom = (v.sections || []).filter((id) => all.includes(id));
  const uniq = [...new Set([...custom, ...all])];
  const hidden = new Set(v.hide_sections || []);
  const rows = h("div", { class: "field rowlist" }, h("label", null, "这个版本的板块顺序和显示"));
  uniq.forEach((sid, i) => {
    rows.append(h("div", { class: "r", style: "grid-template-columns: 24px 1fr auto auto" },
      h("input", { type: "checkbox", checked: !hidden.has(sid), title: "显示这个板块", onchange: (ev) => {
        const set = new Set(v.hide_sections || []);
        ev.target.checked ? set.delete(sid) : set.add(sid);
        v.hide_sections = [...set];
        if (!v.hide_sections.length) delete v.hide_sections;
        touch();
      } }),
      h("span", null, display((sectionById(sid) || {}).title) || sid),
      h("button", { class: "icon", disabled: i === 0, onclick: () => { const o = [...uniq]; [o[i - 1], o[i]] = [o[i], o[i - 1]]; v.sections = o; touch(); buildEditorKeepDraft(); } }, "↑"),
      h("button", { class: "icon", disabled: i === uniq.length - 1, onclick: () => { const o = [...uniq]; [o[i + 1], o[i]] = [o[i], o[i + 1]]; v.sections = o; touch(); buildEditorKeepDraft(); } }, "↓")));
  });
  if (v.sections && v.sections.length) {
    rows.append(h("button", { onclick: () => { delete v.sections; touch(); buildEditorKeepDraft(); } }, "恢复为默认板块顺序"));
  }
  wrap.append(rows);
  return wrap;
}

// ---------------------------------------------------------------- AI
function aiDiff() {
  const before = ui.ai.snapshot;
  const after = entryById(before.id) || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(after[k] ?? null));
  const fmt = (v) => {
    if (Array.isArray(v)) return v.map((x) => "• " + (typeof x === "object" && x ? Object.entries(x).map(([l, t]) => `[${l}] ${t}`).join("\n  ") : x)).join("\n");
    if (v && typeof v === "object") return Object.entries(v).map(([l, t]) => `[${l}] ${t}`).join("\n");
    return v ?? "";
  };
  return h("div", { class: "diff" },
    h("strong", null, keys.length ? "Claude 修改了这一条" : "Claude 没有改动这一条"),
    keys.map((k) => [h("div", { class: "k" }, k), h("div", { class: "before" }, fmt(before[k])), h("div", { class: "after" }, fmt(after[k]))]),
    h("div", { style: "margin-top:8px;display:flex;gap:6px" },
      h("button", { onclick: () => { ui.ai.snapshot = null; buildEditor(); } }, "保留"),
      keys.length && h("button", { class: "danger", onclick: undoAi }, "撤销（恢复到润色前）")));
}

async function undoAi() {
  const snap = ui.ai.snapshot;
  try {
    const s = await api("PUT", "/api/item", { kind: "entry", id: snap.id, data: snap, base: itemHash("entry", snap.id) });
    ui.ai.snapshot = null;
    applyState(s);
    buildEditor({ flash: true });
    scheduleRender();
  } catch (e) { toast("撤销失败：" + e.message); }
}

async function polish(id, instruction) {
  await saveDraft();
  ui.ai.snapshot = clone(entryById(id));
  ui.ai.done = false;
  startClaude({ mode: "polish", entry_id: id, instruction }, `润色 ${id}`);
}

async function startClaude(body, title) {
  try {
    const r = await api("POST", "/api/claude", body);
    ui.ai.job = r.job;
    openClaude();
    logLine("sep", `${new Date().toLocaleTimeString()}  ${title}`);
    renderClaudeAvailability();
    if (ui.sel.kind === "entry") buildEditorKeepDraft();
  } catch (e) { toast(e.message); }
}

function openClaude() { $("#claude").classList.remove("collapsed"); }
function logLine(kind, text) {
  const log = $("#claude-log");
  log.append(h("div", { class: "log-line " + kind }, text));
  log.scrollTop = log.scrollHeight;
}

function renderClaudeAvailability() {
  const avail = S && S.claude && S.claude.available;
  const running = !!ui.ai.job;
  $("#claude-send").disabled = !avail || running;
  $("#claude-input").disabled = !avail;
  $("#claude-cancel").classList.toggle("hidden", !running);
  $("#claude-status").textContent = !avail ? "没有找到 claude 命令" : running ? "运行中…" : "";
}

async function onClaudeEvent(ev) {
  if (ev.job !== ui.ai.job) return;
  if (ev.kind === "text") logLine("", ev.text);
  else if (ev.kind === "tool") logLine("tool", `→ ${ev.name} ${ev.hint || ""}`);
  else if (ev.kind === "error") logLine("err", ev.text);
  else if (ev.kind === "done") {
    logLine(ev.ok ? "done" : "err", ev.ok ? `完成（${ev.seconds ?? "?"} 秒）` : (ev.text || "未完成"));
    ui.ai.job = null;
    ui.ai.done = true;
    renderClaudeAvailability();
    await refresh();
    if (ui.ai.snapshot && ui.sel.kind === "entry" && ui.sel.id === ui.ai.snapshot.id && !ui.dirty) buildEditor();
  }
}

// ---------------------------------------------------------------- right pane
const SLIDERS = [
  ["font_size", "字号", 8, 13, 0.1, "pt"],
  ["line_spread", "行距", 0.8, 1.4, 0.01, "×"],
  ["margin_x", "左右边距", 0.5, 3, 0.05, "cm"],
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

let controlsFor = null;
function renderControls(force = false) {
  const box = $("#controls");
  const v = version();
  if (!v) { box.replaceChildren(h("span", { class: "muted" }, "还没有版本，先在左侧新建一个。")); controlsFor = null; return; }
  if (!force && controlsFor === v.id && box.childElementCount) { updateControls(); return; }
  controlsFor = v.id;
  box.replaceChildren(...[
    h("div", { class: "ctl wide" }, h("span", null, "模板"),
      h("select", { id: "ctl-template", onchange: (ev) => mutateVersion((x) => { x.template = ev.target.value; }) },
        S.templates.map((t) => h("option", { value: t.id, title: t.description }, `${t.name} — ${t.description}`)))),
    h("div", { class: "ctl wide" }, h("span", null, "语言"),
      h("select", { id: "ctl-lang", onchange: (ev) => mutateVersion((x) => { x.lang = ev.target.value; }) },
        ["en", "zh"].map((l) => h("option", { value: l }, LANG_NAMES[l])))),
    SLIDERS.map(([key, label, min, max, step, unit]) => {
      const val = h("span", { class: "val" });
      const input = h("input", { type: "range", min, max, step, "data-key": key,
        oninput: (ev) => { val.textContent = ev.target.value; queueLayout(key, parseFloat(ev.target.value)); } });
      return h("div", { class: "ctl" }, h("span", null, label), input, val,
        h("button", { class: "icon ghost", title: "恢复默认", onclick: () => queueLayout(key, undefined, 0) }, "↺"));
    }),
    h("div", { class: "ctl" }, h("span", null, "强调色"),
      h("input", { type: "color", id: "ctl-accent", onchange: (ev) => queueLayout("accent", ev.target.value.slice(1).toUpperCase(), 0) }),
      h("span"), h("button", { class: "icon ghost", title: "恢复默认", onclick: () => queueLayout("accent", undefined, 0) }, "↺")),
    h("div", { class: "ctl" }, h("span", null, "纸张"),
      h("select", { id: "ctl-paper", onchange: (ev) => queueLayout("paper", ev.target.value, 0) },
        h("option", { value: "a4" }, "A4"), h("option", { value: "letter" }, "Letter")), h("span"), h("span")),
    h("div", { class: "ctl wide" }, h("span", null, "正文字体"),
      h("input", { id: "ctl-font", placeholder: "留空用模板默认，比如 TeX Gyre Heros / Times New Roman / Arial",
        onchange: (ev) => queueLayout("font", ev.target.value.trim() || undefined, 0) })),
  ].flat());
  updateControls();
}

function updateControls() {
  const v = version();
  if (!v) return;
  const active = document.activeElement;
  const set = (el, value) => { if (el && el !== active) el.value = value; };
  set($("#ctl-template"), v.template || "classic");
  set($("#ctl-lang"), v.lang || "en");
  set($("#ctl-paper"), layoutValue("paper"));
  set($("#ctl-font"), (v.layout || {}).font || "");
  set($("#ctl-accent"), "#" + String(layoutValue("accent")).replace("#", ""));
  for (const [key] of SLIDERS) {
    const input = document.querySelector(`#controls input[data-key="${key}"]`);
    if (!input) continue;
    set(input, layoutValue(key));
    input.nextSibling.textContent = input === active ? input.value : layoutValue(key);
  }
}

const pendingLayout = {};
let layoutTimer = null;
function queueLayout(key, value, delay = 350) {
  pendingLayout[key] = value;
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => {
    const patch = { ...pendingLayout };
    for (const k in pendingLayout) delete pendingLayout[k];
    mutateVersion((x) => {
      x.layout = x.layout || {};
      for (const [k, val] of Object.entries(patch)) {
        if (val === undefined) delete x.layout[k]; else x.layout[k] = val;
      }
    }).then(() => updateControls());
  }, delay);
}

function scheduleRender(delay = 700) {
  clearTimeout(ui.render.timer);
  ui.render.timer = setTimeout(doRender, delay);
}

async function doRender() {
  const r = ui.render;
  if (!ui.versionId) { $("#preview-empty").classList.remove("hidden"); return; }
  if (r.busy) { r.again = true; return; }
  r.busy = true;
  const vid = ui.versionId;
  setPreviewBar({ busy: true });
  try {
    const res = await api("POST", `/api/versions/${encodeURIComponent(vid)}/render`);
    if (vid === ui.versionId) {
      setPreviewBar(res);
      if (res.pdf) showPdf(vid);
    }
  } catch (e) {
    setPreviewBar({ ok: false, errors: [e.message] });
  } finally {
    r.busy = false;
    if (r.again) { r.again = false; doRender(); }
  }
}

// double-buffered iframes so the preview doesn't flash white on every render
function showPdf(vid) {
  const front = $("#" + ui.render.front);
  const back = $("#" + (ui.render.front === "pdfA" ? "pdfB" : "pdfA"));
  back.onload = () => setTimeout(() => { // give the PDF viewer a moment to paint
    back.classList.remove("hidden");
    front.classList.add("hidden");
    ui.render.front = back.id;
    $("#preview-empty").classList.add("hidden");
  }, 300);
  back.src = `/api/versions/${encodeURIComponent(vid)}/pdf?t=${Date.now()}#view=FitH&toolbar=0&navpanes=0`;
}

function setPreviewBar(res) {
  const bar = $("#preview-bar");
  bar.replaceChildren();
  const old = $("#right .errors");
  if (old) old.remove();
  if (res.busy) {
    bar.append(h("span", { class: "pill" }, "渲染中…"));
  } else if (res.ok) {
    const one = res.pages === 1;
    bar.append(h("span", { class: "pill " + (one ? "ok" : "bad") }, one ? "1 页 ✓" : `${res.pages} 页，超出一页`));
    if (!one) bar.append(h("span", { class: "muted" }, "试试减小字号 / 边距 / 间距，或少选一条经历"));
    bar.append(h("span", { class: "muted" }, `${res.seconds}s`));
  } else {
    bar.append(h("span", { class: "pill bad" }, "渲染失败"));
    $("#preview-bar").after(h("pre", { class: "errors" }, (res.errors || []).join("\n")));
  }
  bar.append(h("span", { class: "spacer" }),
    h("button", { onclick: () => doRender() }, "重新渲染"),
    h("button", { class: "primary", onclick: exportPdf, disabled: !ui.versionId }, "导出 PDF"));
}

async function exportPdf() {
  await saveDraft();
  try {
    const r = await api("POST", `/api/versions/${encodeURIComponent(ui.versionId)}/export`);
    if (!r.ok) { setPreviewBar(r); toast("导出失败"); return; }
    setPreviewBar(r);
    toast(`已导出\n${r.exported.pdf}${r.exported.source ? "\n" + r.exported.source : ""}` +
      (r.pages > 1 ? `\n注意：共 ${r.pages} 页` : ""), 5000);
  } catch (e) { toast("导出失败：" + e.message); }
}

// ---------------------------------------------------------------- events
function connect() {
  const es = new EventSource("/api/events");
  const conn = $("#conn");
  es.onopen = () => { conn.textContent = "已连接"; conn.className = "pill ok"; if (S) refresh(); };
  es.onerror = () => { conn.textContent = "连接断开，重连中…"; conn.className = "pill bad"; };
  es.addEventListener("file", (m) => {
    const d = JSON.parse(m.data);
    if (S && d.digest === S.digest) return;
    refresh();
  });
  es.addEventListener("claude", (m) => onClaudeEvent(JSON.parse(m.data)));
}

function wireClaudeBar() {
  $("#claude-toggle").onclick = () => $("#claude").classList.toggle("collapsed");
  const send = () => {
    const text = $("#claude-input").value.trim();
    if (!text) return;
    $("#claude-input").value = "";
    ui.ai.snapshot = null;
    startClaude({ mode: "free", instruction: text, version_id: ui.versionId,
      entry_id: ui.sel.kind === "entry" ? ui.sel.id : null }, text);
  };
  $("#claude-send").onclick = send;
  $("#claude-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) send(); });
  $("#claude-cancel").onclick = () => ui.ai.job && api("POST", `/api/claude/${ui.ai.job}/cancel`);
}

window.addEventListener("beforeunload", (e) => { if (ui.dirty) { saveDraft(); e.preventDefault(); } });

(async function main() {
  wireClaudeBar();
  applyState(await api("GET", "/api/state"));
  select("profile");
  connect();
  scheduleRender(0);
})();
