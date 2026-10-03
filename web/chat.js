// Chat sidebar: a Claude Code session with the parts of Claude Code's own UI that matter here —
// model / effort / permission-mode pickers, permission cards, Claude's questions and plan
// approval, slash commands and skills, attachments, a message queue, the task list,
// context use and plan limits, interrupt and fork.
// Loaded after ui.js and before app.js; it only defines functions (app.js's state is
// read when they run), so the load order stays: ui.js, chat.js, app.js.
"use strict";

const TOOL_NAMES = {
  Read: "读取", Edit: "修改", MultiEdit: "修改", Write: "写入", NotebookEdit: "修改笔记本", Glob: "查找文件", Grep: "搜索内容",
  Bash: "运行命令", PowerShell: "运行命令", Skill: "使用技能", WebFetch: "打开网页", WebSearch: "搜索网页",
  TodoWrite: "列计划", TaskCreate: "新建任务", TaskUpdate: "更新任务", TaskList: "查看任务", TaskGet: "查看任务",
  Task: "子任务", Agent: "子任务", AskUserQuestion: "向你提问", ExitPlanMode: "提交计划", EnterPlanMode: "进入计划模式",
  ToolSearch: "加载工具", Monitor: "监视", TaskOutput: "子任务输出", TaskStop: "停止子任务",
};
const TOOL_ACTIONS = {
  Write: "写入文件", Edit: "修改文件", MultiEdit: "修改文件", NotebookEdit: "修改笔记本", Bash: "运行命令", PowerShell: "运行命令",
  WebFetch: "打开网页", WebSearch: "搜索网页", Read: "读取文件", Glob: "查找文件", Grep: "搜索文件内容", Skill: "使用技能",
};
const MODE_INFO = {
  default: { label: "每次询问", short: "询问", icon: "hand", desc: "修改文件、运行命令前都先问你" },
  acceptEdits: { label: "自动接受编辑", short: "自动编辑", icon: "pen", desc: "直接修改文件；有风险的命令仍会先问你" },
  plan: { label: "计划模式", short: "计划", icon: "plan", desc: "只读、只出方案，你批准后才动手" },
  auto: { label: "自动", short: "自动", icon: "bolt", desc: "由 Claude Code 的安全检查判断，只在有风险时问你" },
};
const MODE_CYCLE = ["default", "acceptEdits", "plan"];   // Shift+Tab, like the terminal
const EFFORT_INFO = {
  "": ["自动", "由 Claude Code 按任务决定"],
  low: ["Low", "最快，最省额度"], medium: ["Medium", "速度和质量平衡"], high: ["High", "想得更仔细"],
  xhigh: ["XHigh", "非常仔细，较慢"], max: ["Max", "最深入，最耗额度"],
};
// slash commands this window handles itself (the rest go to Claude Code as typed)
const LOCAL_COMMANDS = [
  { name: "model", desc: "切换模型", hint: "[模型]" },
  { name: "effort", desc: "调整推理强度", hint: "[low|medium|high|xhigh|max|auto]" },
  { name: "permissions", desc: "切换权限模式（也可以按 Shift+Tab）", hint: "" },
  { name: "clear", desc: "开始新对话", hint: "" },
  { name: "rename", desc: "给这段对话改名", hint: "<名字>" },
];
// Chinese for Claude Code's most used built-ins (the rest keep Claude Code's own description)
const BUILTIN_ZH = {
  compact: "压缩上下文：清掉历史，只留一份摘要", context: "查看上下文用量", usage: "查看本次花费和套餐用量", cost: "查看本次花费",
  init: "为当前文件夹生成 CLAUDE.md", memory: "编辑记忆文件", review: "审查代码改动", "code-review": "审查代码改动",
  "security-review": "安全审查", agents: "管理子代理", mcp: "管理 MCP 服务", help: "帮助", recap: "回顾这段对话",
  insights: "使用习惯分析", "output-style": "切换回答风格", autocompact: "设置自动压缩", "update-config": "修改 Claude Code 设置",
};
// commands that only make sense in the terminal's full-screen UI
const TERMINAL_ONLY = new Set(["doctor", "color", "reload-plugins", "config", "theme", "login", "logout", "terminal-setup",
  "vim", "exit", "quit", "ide", "keybindings", "statusline", "hooks", "install-github-app", "heapdump", "upgrade", "resume"]);

const askEls = new Map();   // ask id -> its card, reused while the reply streams so typed answers survive

// ---------------------------------------------------------------- helpers
function shortHint(hint) {
  if (!hint) return "";
  if (/^[a-zA-Z]:[\\/]|^\//.test(hint)) { const parts = hint.split(/[\\/]/); return parts.slice(-2).join("/"); }
  return hint;
}
function modelName(id) {
  if (!id) return "";
  let s = String(id);
  const oneM = s.endsWith("[1m]");
  s = s.replace(/\[1m\]$/, "");
  const m = s.match(/^(?:claude-)?([a-z]+)((?:-\d+)*)$/);
  if (!m) return String(id);
  let nums = m[2].split("-").filter(Boolean);
  if (nums.length && nums[nums.length - 1].length === 8) nums = nums.slice(0, -1);
  return m[1][0].toUpperCase() + m[1].slice(1) + (nums.length ? " " + nums.join(".") : "") + (oneM ? " · 1M" : "");
}
const fmtK = (n) => (n >= 1000 ? `${Math.round(n / 100) / 10}k`.replace(".0k", "k") : String(n));
function resetText(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  return `${["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()]} ${hm}`;
}
function relTime(t) {
  const d = (Date.now() / 1000 - t) / 60;
  if (d < 1) return "刚刚";
  if (d < 60) return `${Math.floor(d)} 分钟前`;
  if (d < 60 * 24) return `${Math.floor(d / 60)} 小时前`;
  return new Date(t * 1000).toLocaleDateString();
}
function autosize(t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; }
const fileUrl = (path) => `/api/chats/${ui.chat.id}/file?path=${encodeURIComponent(path)}`;

// ---------------------------------------------------------------- Claude Code info
async function loadClaudeInfo(refresh = false) {
  try {
    const r = await api("GET", "/api/claude" + (refresh ? "?refresh=1" : ""));
    ui.claude = { ...ui.claude, ...r };
    ui.chat.available = r.available;
  } catch (_) { /* keep what we had */ }
  renderPickers();
}

const info = () => (ui.claude && ui.claude.info) || null;
// what the next message will use: the open chat's choice, or the remembered one for a new chat
function chatSettings() {
  if (ui.chat.meta) return { model: ui.chat.meta.model_choice || null, effort: ui.chat.meta.effort || null, mode: ui.chat.meta.mode || null };
  try { return { model: null, effort: null, mode: null, ...JSON.parse(store("chat-defaults") || "{}") }; } catch (_) { return { model: null, effort: null, mode: null }; }
}
function allModels() { const i = info(); return i ? [...i.models, ...i.more_models] : []; }
function modelEntry(value) { return allModels().find((m) => m.value === value) || null; }
// {name, efforts, auto, tag} of the model the next message will use
function effectiveModel() {
  const { model } = chatSettings();
  const i = info();
  if (model) {
    const m = modelEntry(model);
    return m ? { name: m.name, efforts: m.efforts, auto: m.auto_mode, tag: m.tag } : { name: modelName(model), efforts: i ? i.efforts : [], auto: true };
  }
  const actual = ui.chat.meta && ui.chat.meta.model;
  const resolved = actual || (i && i.default_model.resolved) || "";
  const m = allModels().find((x) => x.resolved === resolved || x.value === resolved);
  return { name: modelName(resolved) || (i ? i.default_model.name : "默认模型"), efforts: m ? m.efforts : i ? i.efforts : [], auto: m ? m.auto_mode : true, follows: true };
}
function effectiveMode() {
  const { mode } = chatSettings();
  if (mode) return mode;
  return (ui.chat.meta && ui.chat.meta.mode_actual) || (info() && info().permission_mode) || "default";
}

async function setChatSetting(field, value) {
  const next = { ...chatSettings(), [field]: value };
  store("chat-defaults", JSON.stringify(next));             // new chats start with the last choice, like Claude Code
  if (field === "model" && value) {                           // an effort the new model can't do falls back to auto
    const m = modelEntry(value);
    if (m && next.effort && !m.efforts.includes(next.effort)) { next.effort = null; store("chat-defaults", JSON.stringify(next)); if (ui.chat.id) await setChatSetting("effort", null); }
  }
  if (ui.chat.id) {
    try {
      ui.chat.meta = await api("PUT", "/api/chats/" + ui.chat.id, { [field]: value });
      const i = ui.chat.list.findIndex((c) => c.id === ui.chat.id);
      if (i >= 0) ui.chat.list[i] = ui.chat.meta;
      if (ui.chat.running && field === "effort") toast("推理强度从下一条消息开始生效");
    } catch (e) { toast(e.message); }
  }
  renderPickers();
}

// ---------------------------------------------------------------- pickers (composer bar)
function renderPickers() {
  const em = effectiveModel(), mode = effectiveMode(), s = chatSettings();
  const mb = $("#chat-model-btn"), eb = $("#chat-effort-btn"), pb = $("#chat-mode-btn");
  if (!mb) return;
  mb.querySelector(".lbl").textContent = em.name;
  setTip(mb, `模型：${em.name}${em.follows ? "（跟随 Claude Code 设置）" : ""}`);
  const noEffort = !em.efforts.length;
  eb.querySelector(".lbl").textContent = noEffort ? "—" : EFFORT_INFO[s.effort || ""][0];
  eb.disabled = noEffort;
  setTip(eb, noEffort ? `${em.name} 不支持调推理强度` : `推理强度：${EFFORT_INFO[s.effort || ""][0]}`);
  const mi = MODE_INFO[mode] || MODE_INFO.default;
  pb.replaceChildren(icon(mi.icon), h("span", { class: "lbl" }, mi.short));
  pb.dataset.mode = mode;
  setTip(pb, `权限模式：${mi.label}${s.mode ? "" : "（跟随 Claude Code 设置）"} · Shift+Tab 切换`);
  renderContextRing();
}

function pickerItem({ label, desc, tag, on, hint, disabled, onClick, cls = "" }) {
  return h("button", { type: "button", role: "menuitemradio", class: "pk-item " + cls, "aria-checked": String(!!on), disabled,
    onclick: () => { closeFloat(); onClick(); } },
    h("span", { class: "pk-main" }, h("span", { class: "pk-label" }, label, tag && h("span", { class: "pk-tag" }, tag)), desc && h("span", { class: "pk-desc" }, desc)),
    h("span", { class: "pk-side" }, on ? icon("check") : hint ? h("span", { class: "pk-num" }, hint) : null));
}
function pickerKeys(panel) {
  panel.addEventListener("keydown", (e) => {
    if (/^[1-9]$/.test(e.key)) {
      const hit = panel.querySelector(`.pk-item[data-num="${e.key}"]:not(:disabled)`);
      if (hit) { e.preventDefault(); hit.click(); }
      return;
    }
    menuKeys(e, panel, ".pk-item:not(:disabled), .pk-more, .pk-custom input, .pk-custom button");
  });
}
function openPicker(anchor, panel) {
  pickerKeys(panel);
  openFloat(anchor, panel, { place: "top", align: "end", minWidth: 250 });
  // the next key press must land in the panel: the chosen item if it is visible (not inside a collapsed "更多模型")
  const visible = [...panel.querySelectorAll(".pk-item:not(:disabled)")].filter((b) => b.offsetParent);
  (visible.find((b) => b.getAttribute("aria-checked") === "true") || visible[0] || panel).focus({ preventScroll: true });
}

function openModelMenu(anchor = $("#chat-model-btn")) {
  const i = info(), s = chatSettings();
  if (!i) { toast(ui.claude && ui.claude.error ? ui.claude.error : "正在读取 Claude Code 的模型列表…"); loadClaudeInfo(true); return; }
  const em = effectiveModel();
  const panel = h("div", { class: "picker", role: "menu", "aria-label": "模型" }, h("div", { class: "pk-head" }, "模型"));
  i.models.forEach((m, n) => {
    const on = s.model ? s.model === m.value : em.follows && m.resolved === (ui.chat.meta && ui.chat.meta.model || i.default_model.resolved);
    const item = pickerItem({ label: m.name, desc: m.desc, tag: m.tag, on, hint: String(n + 1), onClick: () => setChatSetting("model", m.value) });
    item.dataset.num = String(n + 1);
    panel.append(item);
  });
  const more = h("div", { class: "pk-more-body hidden" });
  const moreBtn = h("button", { type: "button", class: "pk-more", "aria-expanded": "false",
    onclick: () => { const open = more.classList.toggle("hidden") === false; moreBtn.setAttribute("aria-expanded", String(open)); placeFloat(Float.cur); } },
    h("span", null, "更多模型"), icon("chevRight"));
  more.append(pickerItem({ label: "跟随 Claude Code 设置", desc: `现在是 ${i.default_model.name}`, on: !s.model, onClick: () => setChatSetting("model", null) }));
  for (const m of i.more_models) more.append(pickerItem({ label: m.name, desc: m.desc, tag: m.tag, on: s.model === m.value, onClick: () => setChatSetting("model", m.value) }));
  const custom = h("input", { placeholder: "输入模型 ID，如 claude-opus-4-1", spellcheck: "false", "aria-label": "模型 ID",
    value: s.model && !modelEntry(s.model) ? s.model : "" });
  const useCustom = () => {
    const v = custom.value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._\-[\]]{0,80}$/.test(v)) { custom.classList.add("bad"); return; }
    closeFloat(); setChatSetting("model", v);
  };
  custom.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); useCustom(); } e.stopPropagation(); });
  custom.addEventListener("input", () => custom.classList.remove("bad"));
  more.append(h("div", { class: "pk-custom" }, custom, h("button", { type: "button", class: "btn sm", onclick: useCustom }, "使用")));
  if (s.model && !i.models.some((m) => m.value === s.model)) { more.classList.remove("hidden"); moreBtn.setAttribute("aria-expanded", "true"); }
  panel.append(h("div", { class: "menu-sep" }), moreBtn, more);
  openPicker(anchor, panel);
}

function openEffortMenu(anchor = $("#chat-effort-btn")) {
  const em = effectiveModel(), s = chatSettings();
  if (!em.efforts.length) { toast(`${em.name} 不支持调推理强度`); return; }
  const panel = h("div", { class: "picker", role: "menu", "aria-label": "推理强度" }, h("div", { class: "pk-head" }, "推理强度"));
  ["", ...em.efforts].forEach((lv, n) => {
    const [label, desc] = EFFORT_INFO[lv] || [lv, ""];
    const item = pickerItem({ label, desc, on: (s.effort || "") === lv, hint: String(n + 1), onClick: () => setChatSetting("effort", lv || null) });
    item.dataset.num = String(n + 1);
    panel.append(item);
  });
  openPicker(anchor, panel);
}

function openModeMenu(anchor = $("#chat-mode-btn")) {
  const s = chatSettings(), em = effectiveModel();
  const base = (ui.chat.meta && !ui.chat.meta.mode && ui.chat.meta.mode_actual) || (info() && info().permission_mode) || "default";
  const panel = h("div", { class: "picker", role: "menu", "aria-label": "权限模式" }, h("div", { class: "pk-head" }, "权限模式", h("span", { class: "pk-head-hint" }, "Shift+Tab 切换")));
  (ui.claude && ui.claude.modes || Object.keys(MODE_INFO)).forEach((m, n) => {
    const mi = MODE_INFO[m];
    if (!mi) return;
    const item = pickerItem({ label: mi.label, desc: m === "auto" && !em.auto ? `${em.name} 不支持自动模式` : mi.desc, on: s.mode === m,
      hint: String(n + 1), disabled: m === "auto" && !em.auto, onClick: () => setChatSetting("mode", m) });
    item.dataset.num = String(n + 1);
    item.prepend(h("span", { class: "pk-icon" }, icon(mi.icon)));
    panel.append(item);
  });
  panel.append(h("div", { class: "menu-sep" }), pickerItem({ label: "跟随 Claude Code 设置", desc: `现在是「${(MODE_INFO[base] || MODE_INFO.default).label}」`, on: !s.mode, onClick: () => setChatSetting("mode", null) }));
  openPicker(anchor, panel);
}

function cycleMode() {
  const cur = effectiveMode();
  const next = MODE_CYCLE[(MODE_CYCLE.indexOf(cur) + 1) % MODE_CYCLE.length];
  setChatSetting("mode", next);
  toast(`权限模式：${MODE_INFO[next].label}`, { ms: 1400 });
}

// ---------------------------------------------------------------- context ring + limits
function renderContextRing() {
  const btn = $("#chat-ctx-btn");
  if (!btn) return;
  const c = ui.chat.meta && ui.chat.meta.context;
  const p = c && c.window ? Math.min(1, c.used / c.window) : 0;
  const r = 6.5, len = 2 * Math.PI * r;
  btn.innerHTML = `<svg viewBox="0 0 18 18" class="ring" aria-hidden="true"><circle cx="9" cy="9" r="${r}" class="bg"/>` +
    `<circle cx="9" cy="9" r="${r}" class="fg" stroke-dasharray="${(len * p).toFixed(2)} ${len.toFixed(2)}" transform="rotate(-90 9 9)"/></svg>`;
  btn.classList.toggle("warn", p >= 0.7);
  btn.classList.toggle("bad", p >= 0.9);
  setTip(btn, c && c.window ? `上下文已用 ${fmtK(c.used)} / ${fmtK(c.window)}（${Math.round(p * 100)}%）· 点开看额度` : "上下文与额度");
}

function openContextPanel(anchor = $("#chat-ctx-btn")) {
  const c = ui.chat.meta && ui.chat.meta.context;
  const lim = (ui.claude && ui.claude.limits) || {};
  const acct = info() && info().account;
  const bar = (p, cls = "") => h("div", { class: "cx-bar " + cls }, h("i", { style: `width:${Math.round(Math.min(1, p) * 100)}%` }));
  const p = c && c.window ? c.used / c.window : 0;
  const win = (key, label) => {
    const w = (lim.windows || {})[key];
    if (!w) return null;
    return h("div", { class: "cx-row" }, h("div", { class: "cx-line" }, h("span", null, label), h("span", { class: "cx-val" }, `${Math.round(w.utilization * 100)}%`)),
      bar(w.utilization, w.utilization >= 0.9 ? "bad" : w.utilization >= 0.7 ? "warn" : ""), w.resetsAt && h("div", { class: "cx-sub" }, `${resetText(w.resetsAt)} 重置`));
  };
  const send = (cmd) => { closeFloat(); sendChat(cmd); };
  const panel = h("div", { class: "cx-pop", role: "dialog", "aria-label": "上下文与额度" },
    h("div", { class: "pk-head" }, "上下文"),
    c && c.window
      ? h("div", { class: "cx-row" }, h("div", { class: "cx-line" }, h("span", null, `${fmtK(c.used)} / ${fmtK(c.window)} tokens`), h("span", { class: "cx-val" }, `${Math.round(p * 100)}%`)),
        bar(p, p >= 0.9 ? "bad" : p >= 0.7 ? "warn" : ""), h("div", { class: "cx-sub" }, p >= 0.7 ? "快满了：压缩一下能腾出空间，Claude 会保留要点" : "满了以后 Claude Code 会自动压缩"))
      : h("div", { class: "cx-sub" }, "发一条消息后显示这段对话用了多少上下文。"),
    h("div", { class: "cx-acts" },
      h("button", { type: "button", class: "btn sm", disabled: !ui.chat.id || !ui.chat.meta || !ui.chat.meta.session_id, onclick: () => send("/compact") }, icon("compress"), "压缩上下文"),
      h("button", { type: "button", class: "btn sm ghost", disabled: !ui.chat.id || !ui.chat.meta || !ui.chat.meta.session_id, onclick: () => send("/context") }, "详细用量")),
    (lim.windows && (lim.windows.five_hour || lim.windows.seven_day)) && h("div", { class: "pk-head" }, "套餐额度"),
    win("five_hour", "5 小时内"), win("seven_day", "本周"),
    lim.windows ? null : h("div", { class: "cx-sub" }, "收到第一条回复后显示套餐额度。"),
    acct && acct.plan && h("div", { class: "cx-foot" }, [acct.plan, acct.email].filter(Boolean).join(" · ")));
  openFloat(anchor, panel, { place: "top", align: "end", minWidth: 270, focus: panel.querySelector("button:not(:disabled)") });
}

// ---------------------------------------------------------------- chats list
async function loadChats() {
  const r = await api("GET", "/api/chats");
  ui.chat.available = r.available;
  ui.chat.waiting = new Set(r.waiting || []);
  if (ui.chat.id) { // the user already started a chat while the list was loading: keep it
    ui.chat.list = [...ui.chat.list, ...r.chats.filter((c) => !ui.chat.list.some((x) => x.id === c.id))];
    renderChatSelect();
    return;
  }
  ui.chat.list = r.chats;
  const saved = store("chat");
  const id = ui.chat.list.some((c) => c.id === saved) ? saved : (ui.chat.list[0] || {}).id || null;
  if (id) await openChat(id, { quiet: true });
  else { ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; renderChatSelect(); renderChat(); renderPickers(); }
  paintChatDot(r.running);
}

function paintChatDot(running = ui.chat.running ? [ui.chat.id] : []) {
  const dot = $("#chat-dot");
  dot.classList.toggle("hidden", !running.length && !ui.chat.waiting.size);
  dot.classList.toggle("wait", ui.chat.waiting.size > 0);
}

async function openChat(id, { quiet = false } = {}) {
  let r;
  try { r = await api("GET", "/api/chats/" + id); }
  catch (e) { // deleted elsewhere: fall back to whatever is left
    store("chat", null);
    const list = await api("GET", "/api/chats");
    ui.chat.list = list.chats;
    if (list.chats.length && list.chats[0].id !== id) return openChat(list.chats[0].id, { quiet });
    ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; ui.chat.running = false;
    renderChatSelect(); renderChat(); renderComposerState(); renderPickers();
    return;
  }
  if (ui.chat.id !== id) askEls.clear();
  ui.chat.id = id;
  ui.chat.meta = r.chat;
  ui.chat.messages = r.messages;
  ui.chat.running = r.running;
  ui.chat.live = r.running ? { blocks: (r.live && r.live.blocks) || [] } : null;
  ui.chat.queue = (r.live && r.live.queue) || [];
  store("chat", id);
  renderChatSelect();
  if (!quiet || ui.tab === "chat") renderChat();
  renderComposerState();
  renderPickers();
  renderTasks();
  renderQueue();
}

let reloadTimer = 0;
function reloadChat() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => ui.chat.id && openChat(ui.chat.id, { quiet: ui.tab !== "chat" }), 30);
}

async function createChat() {
  const c = await api("POST", "/api/chats", chatSettings());
  ui.chat.list = [c, ...ui.chat.list];
  return c;
}

async function newChat() {
  const c = await createChat();
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
    if (next) await openChat(next.id);
    else { ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; store("chat", null); renderChatSelect(); renderChat(); renderPickers(); renderTasks(); }
  } catch (e) { toast(e.message); }
}

async function renameChat(title) {
  if (!ui.chat.id) return;
  const t = title || await modal({ title: "给这段对话改名", input: ui.chat.meta.title || "", ok: "保存" });
  if (!t) return;
  try {
    ui.chat.meta = await api("PUT", "/api/chats/" + ui.chat.id, { title: t });
    const i = ui.chat.list.findIndex((c) => c.id === ui.chat.id);
    if (i >= 0) ui.chat.list[i] = ui.chat.meta;
    renderChatSelect();
  } catch (e) { toast(e.message); }
}

function renderChatSelect() {
  const sel = ui.w.chat;
  sel.disabled = !ui.chat.list.length;
  sel.setOptions(ui.chat.list.map((c) => ({ value: c.id, label: c.title || "新对话",
    hint: ui.chat.waiting.has(c.id) ? "等你确认" : relTime(c.updated) })), ui.chat.id);
}

// ---------------------------------------------------------------- transcript
function renderChat() {
  const log = $("#chat-log");
  log.replaceChildren();
  ui.chat.liveEl = null;
  if (!ui.chat.messages.length && !ui.chat.running) {
    const roots = (doc().settings || {}).project_roots || [];
    const suggest = [
      roots.length ? `看看我的项目文件夹（${roots.join("、")}），挑出最值得写进简历的项目，先别改，等我确认` : "帮我录入一段新经历，一次只问我一个问题",
      langs().length > 1 ? "检查中英文两个版本：哪些经历缺中文、哪些要点超过一行" : "检查当前岗位里有没有超出事实层的说法",
      "把当前岗位压到一页，先调版面再精简措辞",
      "检查当前岗位里有没有超出事实层的说法",
    ].filter((s, i, a) => a.indexOf(s) === i);
    log.append(h("div", { class: "chat-welcome" },
      h("div", { class: "t" }, "和 Claude 一起改简历"),
      h("div", null, ui.chat.available ? "这里就是 Claude Code：它能读写你的简历数据、使用你装的技能，改完右边会自动刷新。输入 / 可以调用命令和技能。" : "没有找到 claude 命令。请先安装并登录 Claude Code。"),
      ui.chat.available && h("div", { class: "suggest" }, suggest.map((s) => h("button", { onclick: () => sendChat(s) }, s)))));
    return;
  }
  ui.chat.messages.forEach((m, i) => log.append(messageEl(m, i)));
  if (ui.chat.running) { ui.chat.liveEl = messageEl({ role: "assistant", blocks: (ui.chat.live || {}).blocks || [], live: true }); log.append(ui.chat.liveEl); }
  log.scrollTop = log.scrollHeight;
}

function filesEl(files) {
  if (!files || !files.length) return null;
  return h("div", { class: "msg-files" }, files.map((f) => f.image
    ? h("a", { class: "mf-img", href: f.preview || fileUrl(f.path), target: "_blank", title: f.name, onclick: (e) => { e.preventDefault(); openImage(f.preview || fileUrl(f.path), f.name); } },
      h("img", { src: f.preview || fileUrl(f.path), alt: f.name, loading: "lazy" }))
    : h("span", { class: "mf-file", title: f.path }, icon("file"), h("span", null, f.name))));
}

function openImage(src, name) {
  const root = $("#modal-root");
  const close = () => { root.replaceChildren(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  root.replaceChildren(h("div", { class: "modal-back img-view", onmousedown: close }, h("img", { src, alt: name })));
}

function messageEl(m, index) {
  if (m.role === "user") {
    const action = m.meta && (m.meta.action || (m.meta.polish && "polish"));
    const tools = index != null && !action && h("div", { class: "msg-tools" },
      h("button", { type: "button", class: "btn icon sm ghost", title: "复制", onclick: () => copyText(m.text) }, icon("copy")),
      h("button", { type: "button", class: "btn icon sm ghost", title: "从这里分叉：开一个新对话，停在这条消息之前，可以改写后重发", onclick: () => forkAt(index) }, icon("fork")));
    return h("div", { class: "msg-user-wrap" + (action ? " is-action" : "") },
      h("div", { class: "msg-user" + (action ? " is-action" : "") }, action ? h("div", { class: "msg-action" }, icon("sparkle"), AI_LABELS[action] || action) : null,
        filesEl(m.files), m.text ? h("div", { class: "msg-text" }, m.text) : null),
      tools);
  }
  const el = h("div", { class: "msg-ai" });
  for (const b of m.blocks || []) {
    if (b.type === "text") el.append(h("div", { class: "md", html: md(b.text) }));
    else if (b.type === "thinking") el.append(h("details", { class: "thought" }, h("summary", null, "思考过程"), h("div", { class: "md", html: md(b.text) })));
    else if (b.type === "tool") el.append(toolEl(b));
    else if (b.type === "ask") el.append(askEl(b));
    else if (b.type === "note") el.append(h("div", { class: "msg-divider" }, h("span", null, b.text)));
  }
  const blocks = m.blocks || [];
  const waitingOnUser = blocks.some((b) => b.type === "ask" && b.status === "pending");
  if (m.live && !waitingOnUser && (!blocks.length || blocks[blocks.length - 1].type !== "text")) {
    el.append(h("div", { class: "thinking", "aria-label": "Claude 正在思考" }, h("i"), h("i"), h("i"),
      ui.chat.thinking ? h("span", { class: "th-n" }, `思考中 · ${fmtK(ui.chat.thinking)} tokens`) : null));
  }
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

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("已复制", { ms: 1200 }); }
  catch (_) { toast("复制失败"); }
}

async function forkAt(index) {
  if (!ui.chat.id) return;
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/fork`, { index });
    ui.chat.list = [r.chat, ...ui.chat.list];
    await openChat(r.chat.id);
    startChatWith(r.text);
    toast("已分叉：改写这条消息后发送，原对话保持不变");
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- cards: permission / question / plan
function askEl(a) {
  const cached = askEls.get(a.id);
  if (cached && cached._status === a.status) return cached;
  const el = a.kind === "question" ? questionCard(a) : a.kind === "plan" ? planCard(a) : permissionCard(a);
  el._status = a.status;
  askEls.set(a.id, el);
  return el;
}

async function answerAsk(a, decision, card) {
  for (const b of card.querySelectorAll("button, input, textarea")) b.disabled = true;
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/answer`, { request_id: a.id, ...decision });
    Object.assign(a, r.ask);
    updateWaiting(ui.chat.id);
    renderLive(true);
  } catch (e) {
    toast(e.message);
    for (const b of card.querySelectorAll("button, input, textarea")) b.disabled = false;
  }
}

const STATUS_TEXT = { allowed: "已允许", denied: "已拒绝", cancelled: "已取消（回复被停止）", answered: "已回答", approved: "已批准", revise: "已请 Claude 继续修改" };

function suggestionLabel(s) {
  if (s.type === "setMode") return MODE_INFO[s.mode] ? `并切到「${MODE_INFO[s.mode].label}」` : `并切换模式`;
  const where = { session: "本对话", localSettings: "这个文件夹", projectSettings: "这个项目", userSettings: "所有项目" }[s.destination] || "";
  if (s.type === "addRules" || s.type === "replaceRules") {
    const rules = (s.rules || []).map((r) => r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName).join("、");
    return `以后不再询问 ${rules}${where ? `（${where}）` : ""}`;
  }
  if (s.type === "addDirectories") return `并允许访问 ${(s.directories || []).join("、")}`;
  return "并记住";
}

function permissionDetail(a) {
  const inp = a.input || {};
  const path = inp.file_path || inp.notebook_path || inp.path;
  if (/^(Bash|PowerShell)$/.test(a.tool)) {
    return [h("pre", { class: "ask-cmd" }, inp.command || ""), inp.description && h("div", { class: "ask-sub" }, inp.description)];
  }
  if (a.tool === "Edit" || a.tool === "MultiEdit") {
    const edits = a.tool === "MultiEdit" ? inp.edits || [] : [inp];
    return [h("div", { class: "ask-path", title: path }, path),
      edits.slice(0, 4).map((e) => h("pre", { class: "ask-diff" },
        String(e.old_string || "").split("\n").slice(0, 12).map((l) => h("span", { class: "del" }, "− " + l + "\n")),
        String(e.new_string || "").split("\n").slice(0, 12).map((l) => h("span", { class: "add" }, "+ " + l + "\n"))))];
  }
  if (a.tool === "Write") {
    const lines = String(inp.content || "").split("\n");
    return [h("div", { class: "ask-path", title: path }, path),
      h("pre", { class: "ask-diff" }, lines.slice(0, 14).map((l) => h("span", { class: "add" }, "+ " + l + "\n")), lines.length > 14 ? h("span", { class: "more" }, `… 还有 ${lines.length - 14} 行`) : null)];
  }
  if (inp.url) return h("div", { class: "ask-path" }, inp.url);
  if (path) return h("div", { class: "ask-path", title: path }, path);
  return h("pre", { class: "ask-cmd" }, JSON.stringify(inp, null, 2).slice(0, 600));
}

function permissionCard(a) {
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-perm ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  card.append(h("div", { class: "ask-h" }, icon("shield"), h("span", { class: "ask-t" }, `Claude 想要${TOOL_ACTIONS[a.tool] || "使用 " + a.tool}`),
    !pending && h("span", { class: "ask-st" }, STATUS_TEXT[a.status] || a.status)));
  if (pending || a.status !== "cancelled") card.append(h("div", { class: "ask-body" }, permissionDetail(a)));
  if (!pending) return card;
  const reason = h("input", { class: "ask-input", placeholder: "告诉 Claude 应该怎么做（可选）", "aria-label": "拒绝的理由" });
  const denyRow = h("div", { class: "ask-row hidden" }, reason, h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { allow: false, message: reason.value }, card) }, "拒绝并发送"));
  reason.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); answerAsk(a, { allow: false, message: reason.value }, card); } });
  card.append(h("div", { class: "ask-acts" },
    h("button", { type: "button", class: "btn sm primary", onclick: () => answerAsk(a, { allow: true }, card) }, "允许"),
    (a.suggestions || []).map((s, i) => h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { allow: true, suggestion: i }, card) }, "允许，" + suggestionLabel(s))),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => answerAsk(a, { allow: false }, card) }, "拒绝"),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => { denyRow.classList.toggle("hidden"); reason.focus(); } }, "拒绝并说明…")), denyRow);
  return card;
}

function questionCard(a) {
  const qs = (a.input && a.input.questions) || [];
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-q ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  card.append(h("div", { class: "ask-h" }, icon("chat"), h("span", { class: "ask-t" }, qs.length > 1 ? `Claude 有 ${qs.length} 个问题` : "Claude 想问你"),
    !pending && h("span", { class: "ask-st" }, STATUS_TEXT[a.status] || a.status)));
  if (!pending) {
    if (a.answers) card.append(h("div", { class: "ask-body" }, qs.map((q) => h("div", { class: "q-done" }, h("span", { class: "q-q" }, q.question), h("span", { class: "q-a" }, a.answers[q.question] || "（没回答）")))));
    return card;
  }
  const picked = qs.map(() => new Set());
  const others = [];
  const submit = h("button", { type: "button", class: "btn sm primary", disabled: true, onclick: () => send() }, "提交回答");
  const valid = () => qs.every((q, i) => picked[i].size || others[i].value.trim());
  const refresh = () => { submit.disabled = !valid(); };
  const send = () => {
    if (!valid()) return;
    const answers = {};
    qs.forEach((q, i) => { answers[q.question] = [...picked[i], others[i].value.trim()].filter(Boolean).join(", "); });
    answerAsk(a, { answers }, card);
  };
  const body = h("div", { class: "ask-body" });
  qs.forEach((q, i) => {
    const other = h("input", { class: "ask-input", placeholder: "其他（自己写）", "aria-label": "其他答案" });
    others.push(other);
    const opts = (q.options || []).map((o) => {
      const b = h("button", { type: "button", class: "q-opt", "aria-pressed": "false", onclick: () => {
        if (q.multiSelect) { picked[i].has(o.label) ? picked[i].delete(o.label) : picked[i].add(o.label); }
        else { picked[i].clear(); picked[i].add(o.label); other.value = ""; }
        for (const x of b.parentElement.children) x.setAttribute("aria-pressed", String(picked[i].has(x.dataset.label)));
        refresh();
      } }, h("span", { class: "q-l" }, o.label), o.description && h("span", { class: "q-d" }, o.description));
      b.dataset.label = o.label;
      return b;
    });
    other.addEventListener("input", () => {
      if (!q.multiSelect && other.value.trim()) { picked[i].clear(); for (const x of opts) x.setAttribute("aria-pressed", "false"); }
      refresh();
    });
    other.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); send(); } });
    body.append(h("div", { class: "q" }, h("div", { class: "q-head" }, q.header && h("span", { class: "q-tag" }, q.header), h("span", null, q.question),
      q.multiSelect && h("span", { class: "q-multi" }, "可多选")), h("div", { class: "q-opts" }, opts), other));
  });
  card.append(body, h("div", { class: "ask-acts" }, submit, h("button", { type: "button", class: "btn sm ghost", onclick: () => answerAsk(a, { skip: true }, card) }, "跳过")));
  return card;
}

function planCard(a) {
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-plan ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  const st = a.status === "approved" ? `已批准 · ${a.mode === "acceptEdits" ? "自动接受编辑" : a.mode === "auto" ? "自动" : "编辑前询问"}`
    : a.status === "revise" ? `已请 Claude 继续修改${a.feedback ? "：" + a.feedback : ""}` : STATUS_TEXT[a.status];
  card.append(h("div", { class: "ask-h" }, icon("plan"), h("span", { class: "ask-t" }, "Claude 的计划"), !pending && h("span", { class: "ask-st" }, st)));
  card.append(h("div", { class: "ask-body md plan-md", html: md((a.input && a.input.plan) || "") }));
  if (!pending) return card;
  const fb = h("textarea", { class: "ask-input", rows: "2", placeholder: "哪里要改？比如：先只改措辞，不动结构" });
  const fbRow = h("div", { class: "ask-row hidden" }, fb, h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { approve: false, feedback: fb.value }, card) }, "发给 Claude"));
  card.append(h("div", { class: "ask-acts" },
    h("button", { type: "button", class: "btn sm primary", onclick: () => answerAsk(a, { approve: true, mode: "acceptEdits" }, card) }, "批准，自动接受编辑"),
    h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { approve: true, mode: "default" }, card) }, "批准，编辑前问我"),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => { fbRow.classList.toggle("hidden"); fb.focus(); } }, "继续修改…")), fbRow);
  return card;
}

function updateWaiting(chatId, pending) {
  if (pending === undefined) {
    const live = chatId === ui.chat.id && ui.chat.live ? ui.chat.live.blocks : [];
    pending = live.some((b) => b.type === "ask" && b.status === "pending");
  }
  if (pending) ui.chat.waiting.add(chatId); else ui.chat.waiting.delete(chatId);
  paintChatDot();
  renderChatSelect();
}

// ---------------------------------------------------------------- live updates
let liveFrame = 0;
function renderLive(now = false) {
  if (liveFrame && !now) return;
  const paint = () => {
    liveFrame = 0;
    const log = $("#chat-log");
    if (ui.tab !== "chat" || !ui.chat.running || !ui.chat.live) return;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    const el = messageEl({ role: "assistant", blocks: ui.chat.live.blocks, live: true });
    if (ui.chat.liveEl && ui.chat.liveEl.isConnected) ui.chat.liveEl.replaceWith(el); else log.append(el);
    ui.chat.liveEl = el;
    if (nearBottom) log.scrollTop = log.scrollHeight;
  };
  if (now) { cancelAnimationFrame(liveFrame); paint(); } else liveFrame = requestAnimationFrame(paint);
}

function renderComposerState() {
  const running = ui.chat.running;
  $("#chat-stop").classList.toggle("hidden", !running);
  $("#chat-send").disabled = !ui.chat.available;
  setTip($("#chat-send"), running ? "排队：等这条回复完再发（Enter）" : "发送（Enter）· Shift+Enter 换行");
  $("#chat-input").disabled = !ui.chat.available;
  const parts = [];
  if (ui.versionId) parts.push(isAll() ? "ALL（全部经历）" : `岗位「${jobName(version())}」`);
  if (ui.sel.kind === "entry" && entryById(ui.sel.id)) parts.push(`「${display(entryById(ui.sel.id).title) || entryLabel(entryById(ui.sel.id))}」`);
  $("#chat-context").textContent = parts.length ? `Claude 知道你正在看：${parts.join(" · ")}` : "";
  paintChatDot();
}

function renderTasks() {
  const box = $("#chat-tasks");
  const tasks = (ui.chat.meta && ui.chat.meta.tasks) || [];
  const done = tasks.filter((t) => t.status === "completed").length;
  if (!tasks.length || (done === tasks.length && !ui.chat.running)) { box.classList.add("hidden"); box.replaceChildren(); return; }
  const open = store("tasks-open") !== "0";
  const cur = tasks.find((t) => t.status === "in_progress");
  box.classList.remove("hidden");
  box.replaceChildren(
    h("button", { type: "button", class: "ct-head", "aria-expanded": String(open), onclick: () => { store("tasks-open", open ? "0" : "1"); renderTasks(); } },
      icon("plan"), h("span", { class: "ct-t" }, cur ? cur.active || cur.subject : "任务"), h("span", { class: "ct-n" }, `${done}/${tasks.length}`),
      h("span", { class: "ct-bar" }, h("i", { style: `width:${Math.round((done / tasks.length) * 100)}%` })), icon(open ? "down" : "up")),
    open && h("ul", { class: "ct-list" }, tasks.map((t) => h("li", { class: "ct-" + t.status },
      h("span", { class: "ct-ic" }, t.status === "completed" ? icon("check") : t.status === "in_progress" ? h("span", { class: "st running" }) : null),
      h("span", null, t.subject)))));
}

function renderQueue() {
  const box = $("#chat-queue");
  const q = ui.chat.queue || [];
  box.classList.toggle("hidden", !q.length);
  box.replaceChildren(...q.map((item) => h("div", { class: "cq-item" },
    h("span", { class: "cq-tag" }, "排队中"), h("span", { class: "cq-text" }, item.msg.text || (item.msg.files || []).map((f) => f.name).join("、")),
    h("button", { type: "button", class: "btn icon sm ghost", title: "不发了", onclick: () => api("DELETE", `/api/chats/${ui.chat.id}/queue/${item.id}`).catch((e) => toast(e.message)) }, icon("x")))));
}

// ---------------------------------------------------------------- composer: attachments
function renderPendingFiles() {
  const box = $("#chat-files");
  const files = ui.chat.files;
  box.classList.toggle("hidden", !files.length);
  box.replaceChildren(...files.map((f, i) => h("div", { class: "cf-item" + (f.preview ? " img" : ""), title: f.path || f.name },
    f.preview ? h("img", { src: f.preview, alt: f.name }) : h("span", { class: "cf-ic" }, icon("file")),
    !f.preview && h("span", { class: "cf-name" }, f.name),
    h("button", { type: "button", class: "cf-x", "aria-label": "移除 " + f.name, onclick: () => { ui.chat.files.splice(i, 1); renderPendingFiles(); } }, icon("x")))));
}

function addBrowserFiles(list) {
  for (const file of list) {
    if (file.size > 30 * 1024 * 1024) { toast(`${file.name} 超过 30 MB`); continue; }
    const reader = new FileReader();
    reader.onload = () => {
      const isImg = /^image\/(png|jpeg|gif|webp)$/.test(file.type);
      ui.chat.files.push({ name: file.name || (isImg ? "粘贴的图片.png" : "文件"), data: reader.result, preview: isImg ? reader.result : null });
      renderPendingFiles();
    };
    reader.readAsDataURL(file);
  }
}

async function pickAttachments() {
  try {
    const r = await api("POST", "/api/pick", { kind: "files", initial: (doc().settings || {}).project_roots?.[0] || null });
    for (const p of r.paths || []) ui.chat.files.push({ name: p.split(/[\\/]/).pop(), path: p, preview: null });
    renderPendingFiles();
    $("#chat-input").focus();
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- composer: slash commands
const slash = { open: false, items: [], active: 0 };

function commandList() {
  const cmds = info() ? info().commands : [];
  const local = LOCAL_COMMANDS.map((c) => ({ ...c, local: true }));
  const names = new Set(local.map((c) => c.name));
  return [...local, ...cmds.filter((c) => !names.has(c.name))];
}

function updateSlash() {
  const input = $("#chat-input");
  const m = input.value.match(/^\/([^\s]*)$/);
  if (!m || input.selectionStart !== input.value.length) { closeSlash(); return; }
  const q = m[1].toLowerCase();
  const all = commandList();
  const score = (c) => (c.name.toLowerCase().startsWith(q) || (c.aliases || []).some((a) => a.startsWith(q)) ? 0 : c.name.toLowerCase().includes(q) ? 1 : 2);
  // built-ins and local commands before plugin skills, then shorter names first
  const rank = (c) => score(c) * 10 + (c.local ? 0 : c.name.includes(":") ? 2 : c.source ? 1 : 0);
  slash.items = all.filter((c) => score(c) < 2).sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length).slice(0, 40);
  slash.active = 0;
  if (!slash.items.length) { closeSlash(); return; }
  const panel = h("div", { class: "slash-menu", role: "listbox", "aria-label": "命令和技能" });
  slash.items.forEach((c, i) => panel.append(h("div", { role: "option", class: "sl-item" + (i === 0 ? " on" : ""), "aria-selected": String(i === 0), "data-i": String(i),
    onpointerdown: (e) => e.preventDefault(), onclick: () => applySlash(i) },
    h("span", { class: "sl-name" }, "/" + c.name, c.hint && h("span", { class: "sl-hint" }, " " + c.hint)),
    h("span", { class: "sl-desc", title: c.desc }, BUILTIN_ZH[c.name] || c.desc),
    h("span", { class: "sl-src" }, c.local ? "本窗口" : TERMINAL_ONLY.has(c.name) ? "终端专用" : c.source === "user" || c.source === "project" ? "技能" : c.source === "plugin" ? "插件" : ""))));
  if (slash.open && Float.cur && Float.cur.panel.classList.contains("slash-menu")) {
    Float.cur.panel.replaceChildren(...panel.childNodes);
    placeFloat(Float.cur);
  } else {
    slash.open = true;
    openFloat($("#composer-box"), panel, { place: "top", align: "start", minWidth: "anchor", onClose: () => { slash.open = false; } });
  }
}
function closeSlash() { if (slash.open && Float.cur && Float.cur.panel.classList.contains("slash-menu")) closeFloat(); slash.open = false; }
function moveSlash(d) {
  const panel = Float.cur && Float.cur.panel;
  if (!panel) return;
  slash.active = (slash.active + d + slash.items.length) % slash.items.length;
  for (const el of panel.querySelectorAll(".sl-item")) { const on = Number(el.dataset.i) === slash.active; el.classList.toggle("on", on); el.setAttribute("aria-selected", String(on)); if (on) el.scrollIntoView({ block: "nearest" }); }
}
function applySlash(i = slash.active) {
  const c = slash.items[i];
  if (!c) return;
  const input = $("#chat-input");
  closeSlash();
  input.value = `/${c.name} `;
  autosize(input);
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  if (c.local && !c.hint.startsWith("<")) runLocal(input.value.trim()) && clearInput();
  else if (!c.local && !c.hint && !TERMINAL_ONLY.has(c.name)) submitComposer();   // nothing to fill in: run it, like Claude Code
}
function clearInput() { const t = $("#chat-input"); t.value = ""; autosize(t); }

// /model, /effort, /permissions, /clear, /rename are this window's own; true = handled
function runLocal(text) {
  const m = text.match(/^\/(\w[\w-]*)(?:\s+(.*))?$/);
  if (!m) return false;
  const [, name, arg = ""] = m;
  const a = arg.trim();
  if (name === "model") {
    if (!a) { openModelMenu(); return true; }
    const hit = allModels().find((x) => x.name.toLowerCase().replace(/\s+/g, "") === a.toLowerCase().replace(/\s+/g, "") || x.value === a);
    setChatSetting("model", hit ? hit.value : a === "default" ? null : a);
    return true;
  }
  if (name === "effort") {
    if (!a) { openEffortMenu(); return true; }
    const lv = a.toLowerCase();
    if (lv === "auto") setChatSetting("effort", null);
    else if (EFFORT_INFO[lv]) setChatSetting("effort", lv);
    else { toast("推理强度只能是 low / medium / high / xhigh / max / auto"); }
    return true;
  }
  if (name === "permissions" || name === "mode") { openModeMenu(); return true; }
  if (name === "clear" || name === "new") { newChat(); return true; }
  if (name === "rename") { renameChat(a); return true; }
  return false;
}

// ---------------------------------------------------------------- sending
function startChatWith(text) {
  setTab("chat");
  const t = $("#chat-input");
  t.value = text;
  autosize(t);
  t.focus();
  t.setSelectionRange(t.value.length, t.value.length);
}

async function sendChat(text, { action = null, entryId = null, target = null, lang = null, files = [] } = {}) {
  if (!ui.chat.available) { toast("没有找到 claude 命令"); return false; }
  if (!action && !text.trim() && !files.length) return false;
  if (action && ui.chat.running) { toast("Claude 还在回复上一条，稍等或先停止"); return false; }
  await saveDraft();
  if (!ui.chat.id) {
    const c = await createChat();
    ui.chat.id = c.id; ui.chat.meta = c; ui.chat.messages = [];
    store("chat", c.id);
    renderChatSelect();
  }
  setTab("chat");
  const batch = action && action.startsWith("batch_");
  const attachments = files.map((f) => (f.data ? { name: f.name, data: f.data } : { name: f.name, path: f.path }));
  const body = { text, version_id: ui.versionId, entry_id: batch ? null : entryId || (ui.sel.kind === "entry" ? ui.sel.id : null), action, target, lang, attachments };
  const queued = ui.chat.running;
  if (!queued) {
    ui.chat.running = true;
    ui.chat.live = { blocks: [] };
    ui.chat.thinking = 0;
    const who = entryId ? `「${display((entryById(entryId) || {}).title) || entryLabel(entryById(entryId))}」` : "";
    ui.chat.messages.push({ role: "user", text: action ? `${who}${text ? "：" + text : ""}` || "…" : text, meta: { action },
      files: files.map((f) => ({ name: f.name, path: f.path || "", image: !!f.preview, preview: f.preview })) });
    renderChat();
    renderComposerState();
  }
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/send`, body);
    if (queued || r.queued) { reloadChat(); return true; }   // queued, or the last reply ended meanwhile: take the server's view
    const last = ui.chat.messages[ui.chat.messages.length - 1];
    last.text = action ? r.text : text;
    last.files = r.files || [];
    api("GET", "/api/chats").then((l) => { ui.chat.list = l.chats; renderChatSelect(); }).catch(() => {});   // the new title
    return true;
  } catch (e) {
    if (queued) { toast(e.message); return false; }
    ui.chat.running = false;
    ui.chat.live = null;
    ui.chat.messages.push({ role: "assistant", blocks: [], error: e.message });
    renderChat();
    renderComposerState();
    return false;
  }
}

async function submitComposer() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text && !ui.chat.files.length) return;
  if (!ui.chat.files.length && runLocal(text)) { clearInput(); return; }
  const files = ui.chat.files.slice();
  if (await sendChat(text, { files })) {
    clearInput();
    ui.chat.files = [];
    renderPendingFiles();
  }
}

// ---------------------------------------------------------------- server events
async function onChatEvent(type, ev) {
  if (type === "claude.info") { loadClaudeInfo(); return; }
  if (type === "claude.limits") { ui.claude = { ...ui.claude, limits: ev }; renderContextRing(); return; }
  if (type === "chat.start") {
    paintChatDot([ev.chat]);
    $("#chat-dot").classList.remove("hidden");
    // a reply this page did not start (another window, or one that began as the last ended): catch up
    if (ev.chat === ui.chat.id && !ui.chat.running) { ui.chat.running = true; ui.chat.live = { blocks: [] }; renderComposerState(); reloadChat(); }
    return;
  }
  if (type === "chat.ask") {
    if (ev.chat === ui.chat.id && !ui.chat.running) { reloadChat(); return; }
    const here = ev.chat === ui.chat.id && ui.chat.running;
    if (here) {
      const live = ui.chat.live || (ui.chat.live = { blocks: [] });
      const i = live.blocks.findIndex((x) => x.type === "ask" && x.id === ev.ask.id);
      if (i >= 0) Object.assign(live.blocks[i], ev.ask); else live.blocks.push(ev.ask);
      renderLive(true);
    }
    updateWaiting(ev.chat, here ? undefined : ev.ask.status === "pending");
    if (ev.ask.status === "pending" && (ev.chat !== ui.chat.id || ui.tab !== "chat" || document.hidden)) {
      const what = ev.ask.kind === "question" ? "Claude 有问题想问你" : ev.ask.kind === "plan" ? "Claude 的计划等你批准" : `Claude 想要${TOOL_ACTIONS[ev.ask.tool] || "使用 " + ev.ask.tool}`;
      toast(what, { ms: 8000, action: { label: "去看看", fn: async () => { if (ev.chat !== ui.chat.id) await openChat(ev.chat); setTab("chat"); } } });
    }
    return;
  }
  if (ev.chat !== ui.chat.id) {
    if (type === "chat.done") {
      const r = await api("GET", "/api/chats");
      ui.chat.list = r.chats;
      ui.chat.waiting = new Set(r.waiting || []);
      renderChatSelect();
      paintChatDot(r.running);
    }
    return;
  }
  const live = ui.chat.live || (ui.chat.live = { blocks: [] });
  if (type === "chat.init") {
    if (ui.chat.meta) { ui.chat.meta.model = ev.model; ui.chat.meta.mode_actual = ev.mode; }
    renderPickers();
  } else if (type === "chat.meta") {
    ui.chat.meta = ev.meta; renderPickers();
  } else if (type === "chat.thinking") {
    ui.chat.thinking = ev.tokens; if (!live.blocks.length) renderLive();
  } else if (type === "chat.delta") {
    const kind = ev.kind || "text";
    const last = live.blocks[live.blocks.length - 1];
    if (last && last.type === kind) last.text += ev.text; else live.blocks.push({ type: kind, text: ev.text });
    renderLive();
  } else if (type === "chat.tool" || type === "chat.block") {
    const b = ev.tool || ev.block;
    const i = b.id ? live.blocks.findIndex((x) => x.id === b.id && x.type === b.type) : -1;
    if (i >= 0) live.blocks[i] = b; else live.blocks.push(b);
    renderLive();
  } else if (type === "chat.tasks") {
    if (ui.chat.meta) ui.chat.meta.tasks = ev.tasks; renderTasks();
  } else if (type === "chat.queue") {
    ui.chat.queue = ev.queue; renderQueue();
  } else if (type === "chat.reply" || type === "chat.next") {
    reloadChat();
  } else if (type === "chat.done") {
    ui.chat.running = false;
    ui.chat.thinking = 0;
    updateWaiting(ev.chat, false);
    await openChat(ui.chat.id, { quiet: ui.tab !== "chat" });
    const r = await api("GET", "/api/chats");
    ui.chat.list = r.chats;
    renderChatSelect();
    renderComposerState();
    await refresh();
    finishAi(ev.chat);
    if (ev.error && ui.tab !== "chat") toast("Claude 出错了，详见对话");
  }
}

const CHAT_EVENTS = ["chat.start", "chat.init", "chat.meta", "chat.thinking", "chat.delta", "chat.tool", "chat.block", "chat.ask",
  "chat.tasks", "chat.queue", "chat.reply", "chat.next", "chat.done", "claude.info", "claude.limits"];
function listenChat(es) {
  for (const t of CHAT_EVENTS) es.addEventListener(t, (m) => onChatEvent(t, JSON.parse(m.data)));
}

// ---------------------------------------------------------------- wiring
function wireChat() {
  ui.w.chat = uiSelect({ id: "chat-select", cls: "grow", label: "历史对话", placeholder: "还没有对话", menuWidth: 300, onChange: (id) => openChat(id) });
  $("#chat-select-slot").replaceWith(ui.w.chat.el);
  $("#chat-new").append(icon("plus"));
  $("#chat-rename").append(icon("pen"));
  $("#chat-delete").append(icon("trash"));
  $("#chat-send").append(icon("send"));
  $("#chat-stop").append(icon("stop"));
  $("#chat-attach").append(icon("clip"));
  for (const id of ["chat-model-btn", "chat-effort-btn"]) $("#" + id).append(h("span", { class: "lbl" }));
  $("#chat-new").onclick = newChat;
  $("#chat-rename").onclick = () => renameChat();
  $("#chat-delete").onclick = deleteChat;
  $("#chat-attach").onclick = pickAttachments;
  $("#chat-model-btn").onclick = (e) => (Float.cur && Float.cur.anchor === e.currentTarget ? closeFloat() : openModelMenu(e.currentTarget));
  $("#chat-effort-btn").onclick = (e) => (Float.cur && Float.cur.anchor === e.currentTarget ? closeFloat() : openEffortMenu(e.currentTarget));
  $("#chat-mode-btn").onclick = (e) => (Float.cur && Float.cur.anchor === e.currentTarget ? closeFloat() : openModeMenu(e.currentTarget));
  $("#chat-ctx-btn").onclick = (e) => (Float.cur && Float.cur.anchor === e.currentTarget ? closeFloat() : openContextPanel(e.currentTarget));
  $("#chat-send").onclick = submitComposer;
  $("#chat-stop").onclick = () => ui.chat.id && api("POST", `/api/chats/${ui.chat.id}/cancel`);
  const input = $("#chat-input");
  input.addEventListener("input", () => { autosize(input); updateSlash(); });
  input.addEventListener("keydown", (e) => {
    if (slash.open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); moveSlash(e.key === "ArrowDown" ? 1 : -1); return; }
      if ((e.key === "Enter" && !e.isComposing) || e.key === "Tab") { e.preventDefault(); applySlash(); return; }
    }
    if (e.key === "Tab" && e.shiftKey) { e.preventDefault(); cycleMode(); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitComposer(); }
  });
  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData && e.clipboardData.files) || []];
    if (files.length) { e.preventDefault(); addBrowserFiles(files); }
  });
  const pane = $("#chat");
  pane.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); pane.classList.add("drop"); } });
  pane.addEventListener("dragleave", (e) => { if (!pane.contains(e.relatedTarget)) pane.classList.remove("drop"); });
  pane.addEventListener("drop", (e) => {
    pane.classList.remove("drop");
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    addBrowserFiles([...e.dataTransfer.files]);
    input.focus();
  });
  $("#chat-log").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ext]");
    if (a) { e.preventDefault(); api("POST", "/api/open", { url: a.getAttribute("href") }).catch(() => window.open(a.href, "_blank")); }
  });
  renderPickers();
}
