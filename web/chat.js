// Chat sidebar: a Claude Code session with the parts of Claude Code's own UI that matter here —
// model / effort / permission-mode pickers, permission cards, Claude's questions and plan
// approval, slash commands and skills, attachments, a message queue, the task list,
// context use and plan limits, interrupt and fork.
// Loaded after ui.js and before app.js; it only defines functions (app.js's state is
// read when they run), so the load order stays: ui.js, chat.js, app.js.
"use strict";

// (i18n.js is loaded before this file, so t() works in these tables)
const TOOL_NAMES = {
  Read: t("Read"), Edit: t("Edit"), MultiEdit: t("Edit"), Write: t("Write"), NotebookEdit: t("Edit notebook"), Glob: t("Find files"), Grep: t("Search content"),
  Bash: t("Run command"), PowerShell: t("Run command"), Skill: t("Use skill"), WebFetch: t("Open web page"), WebSearch: t("Search the web"),
  TodoWrite: t("Plan"), TaskCreate: t("New task"), TaskUpdate: t("Update task"), TaskList: t("View tasks"), TaskGet: t("View tasks"),
  Task: t("Subtask"), Agent: t("Subtask"), AskUserQuestion: t("Ask you"), ExitPlanMode: t("Submit plan"), EnterPlanMode: t("Enter plan mode"),
  ToolSearch: t("Load tools"), Monitor: t("Monitor"), TaskOutput: t("Subtask output"), TaskStop: t("Stop subtask"),
};
const TOOL_ACTIONS = {
  Write: t("write a file"), Edit: t("edit a file"), MultiEdit: t("edit a file"), NotebookEdit: t("edit a notebook"), Bash: t("run a command"), PowerShell: t("run a command"),
  WebFetch: t("open a web page"), WebSearch: t("search the web"), Read: t("read a file"), Glob: t("find files"), Grep: t("search file contents"), Skill: t("use a skill"),
};
const toolAction = (tool) => TOOL_ACTIONS[tool] || t("use {tool}", { tool });
const MODE_INFO = {
  default: { label: t("Ask every time"), short: t("Ask"), icon: "hand", desc: t("Asks you before editing files or running commands") },
  acceptEdits: { label: t("Accept edits"), short: t("Auto edit"), icon: "pen", desc: t("Edits files directly; still asks before risky commands") },
  plan: { label: t("Plan mode"), short: t("Plan"), icon: "plan", desc: t("Read-only; proposes a plan and acts once you approve") },
  auto: { label: t("Auto"), short: t("Auto"), icon: "bolt", desc: t("Claude Code's safety checks decide; asks only when something is risky") },
};
const MODE_CYCLE = ["default", "acceptEdits", "plan"];   // Shift+Tab, like the terminal
const EFFORT_INFO = {
  "": [t("Auto"), t("Claude Code decides per task")],
  low: ["Low", t("Fastest, uses the least quota")], medium: ["Medium", t("Balance of speed and quality")], high: ["High", t("Thinks more carefully")],
  xhigh: ["XHigh", t("Very careful, slower")], max: ["Max", t("Deepest, uses the most quota")],
};
// slash commands this window handles itself (the rest go to Claude Code as typed)
const LOCAL_COMMANDS = [
  { name: "model", desc: t("Switch model"), hint: t("[model]") },
  { name: "effort", desc: t("Set the effort level"), hint: "[low|medium|high|xhigh|max|auto]" },
  { name: "permissions", desc: t("Switch permission mode (or press Shift+Tab)"), hint: "" },
  { name: "clear", desc: t("Start a new chat"), hint: "" },
  { name: "rename", desc: t("Rename this chat"), hint: t("<name>") },
];
// Claude Code's most used built-ins, for languages other than English (the rest keep Claude Code's own description)
const BUILTIN_DESC = {
  compact: "Compact the context: clear the history, keep a summary", context: "Show context usage", usage: "Show this session's cost and plan usage", cost: "Show this session's cost",
  init: "Create a CLAUDE.md for this folder", memory: "Edit memory files", review: "Review code changes", "code-review": "Review code changes",
  "security-review": "Security review", agents: "Manage subagents", mcp: "Manage MCP servers", help: "Help", recap: "Recap this chat",
  insights: "Usage insights", "output-style": "Switch the reply style", autocompact: "Set up auto-compact", "update-config": "Change Claude Code settings",
};
const commandDesc = (c) => (I18N.lang !== "en" && BUILTIN_DESC[c.name] ? t(BUILTIN_DESC[c.name]) : c.desc);
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
// dates and times in the interface language's own format
const LOCALE = I18N.lang === "zh" ? "zh-CN" : I18N.lang;
function resetText(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000), now = new Date();
  const hm = d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return t("today {time}", { time: hm });
  return `${d.toLocaleDateString(LOCALE, { weekday: "short" })} ${hm}`;
}
function relTime(epoch) {
  const d = (Date.now() / 1000 - epoch) / 60;
  if (d < 1) return t("just now");
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto", style: "short" });
  if (d < 60) return rtf.format(-Math.floor(d), "minute");
  if (d < 60 * 24) return rtf.format(-Math.floor(d / 60), "hour");
  return new Date(epoch * 1000).toLocaleDateString(LOCALE);
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; }
// the chat is on screen: its tab in Resume, or the left pane of Job search (where it has no tab)
const chatShown = () => ui.tab === "chat" || (W.ws === "jobs" && W.chat === "on");
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
  return { name: modelName(resolved) || (i ? t(i.default_model.name) : t("Default model")), efforts: m ? m.efforts : i ? i.efforts : [], auto: m ? m.auto_mode : true, follows: true };
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
      if (ui.chat.running && field === "effort") toast(t("The effort level applies from the next message"));
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
  setTip(mb, em.follows ? t("Model: {name} (following Claude Code's setting)", { name: em.name }) : t("Model: {name}", { name: em.name }));
  const noEffort = !em.efforts.length;
  eb.querySelector(".lbl").textContent = noEffort ? "—" : EFFORT_INFO[s.effort || ""][0];
  eb.disabled = noEffort;
  setTip(eb, noEffort ? t("{name} has no effort levels", { name: em.name }) : t("Effort: {level}", { level: EFFORT_INFO[s.effort || ""][0] }));
  const mi = MODE_INFO[mode] || MODE_INFO.default;
  pb.replaceChildren(icon(mi.icon), h("span", { class: "lbl" }, mi.short));
  pb.dataset.mode = mode;
  setTip(pb, `${s.mode ? t("Permission mode: {mode}", { mode: mi.label }) : t("Permission mode: {mode} (following Claude Code's setting)", { mode: mi.label })} · ${t("Shift+Tab to switch")}`);
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
  // the next key press must land in the panel: the chosen item if it is visible (not inside a collapsed "More models")
  const visible = [...panel.querySelectorAll(".pk-item:not(:disabled)")].filter((b) => b.offsetParent);
  (visible.find((b) => b.getAttribute("aria-checked") === "true") || visible[0] || panel).focus({ preventScroll: true });
}

function openModelMenu(anchor = $("#chat-model-btn")) {
  const i = info(), s = chatSettings();
  if (!i) { toast(ui.claude && ui.claude.error ? ui.claude.error : t("Reading Claude Code's model list…")); loadClaudeInfo(true); return; }
  const em = effectiveModel();
  const panel = h("div", { class: "picker", role: "menu", "aria-label": t("Model") }, h("div", { class: "pk-head" }, t("Model")));
  i.models.forEach((m, n) => {
    const on = s.model ? s.model === m.value : em.follows && m.resolved === (ui.chat.meta && ui.chat.meta.model || i.default_model.resolved);
    const item = pickerItem({ label: m.name, desc: m.desc && t(m.desc), tag: m.tag && t(m.tag), on, hint: String(n + 1), onClick: () => setChatSetting("model", m.value) });
    item.dataset.num = String(n + 1);
    panel.append(item);
  });
  const more = h("div", { class: "pk-more-body hidden" });
  const moreBtn = h("button", { type: "button", class: "pk-more", "aria-expanded": "false",
    onclick: () => { const open = more.classList.toggle("hidden") === false; moreBtn.setAttribute("aria-expanded", String(open)); placeFloat(Float.cur); } },
    h("span", null, t("More models")), icon("chevRight"));
  more.append(pickerItem({ label: t("Follow Claude Code's setting"), desc: t("currently {name}", { name: t(i.default_model.name) }), on: !s.model, onClick: () => setChatSetting("model", null) }));
  for (const m of i.more_models) more.append(pickerItem({ label: m.name, desc: m.desc && t(m.desc), tag: m.tag && t(m.tag), on: s.model === m.value, onClick: () => setChatSetting("model", m.value) }));
  const custom = h("input", { placeholder: t("Model ID, e.g. claude-opus-4-1"), spellcheck: "false", "aria-label": t("Model ID"),
    value: s.model && !modelEntry(s.model) ? s.model : "" });
  const useCustom = () => {
    const v = custom.value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._\-[\]]{0,80}$/.test(v)) { custom.classList.add("bad"); return; }
    closeFloat(); setChatSetting("model", v);
  };
  custom.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); useCustom(); } e.stopPropagation(); });
  custom.addEventListener("input", () => custom.classList.remove("bad"));
  more.append(h("div", { class: "pk-custom" }, custom, h("button", { type: "button", class: "btn sm", onclick: useCustom }, t("Use"))));
  if (s.model && !i.models.some((m) => m.value === s.model)) { more.classList.remove("hidden"); moreBtn.setAttribute("aria-expanded", "true"); }
  panel.append(h("div", { class: "menu-sep" }), moreBtn, more);
  openPicker(anchor, panel);
}

function openEffortMenu(anchor = $("#chat-effort-btn")) {
  const em = effectiveModel(), s = chatSettings();
  if (!em.efforts.length) { toast(t("{name} has no effort levels", { name: em.name })); return; }
  const panel = h("div", { class: "picker", role: "menu", "aria-label": t("Effort") }, h("div", { class: "pk-head" }, t("Effort")));
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
  const panel = h("div", { class: "picker", role: "menu", "aria-label": t("Permission mode") }, h("div", { class: "pk-head" }, t("Permission mode"), h("span", { class: "pk-head-hint" }, t("Shift+Tab to switch"))));
  (ui.claude && ui.claude.modes || Object.keys(MODE_INFO)).forEach((m, n) => {
    const mi = MODE_INFO[m];
    if (!mi) return;
    const item = pickerItem({ label: mi.label, desc: m === "auto" && !em.auto ? t("{name} has no auto mode", { name: em.name }) : mi.desc, on: s.mode === m,
      hint: String(n + 1), disabled: m === "auto" && !em.auto, onClick: () => setChatSetting("mode", m) });
    item.dataset.num = String(n + 1);
    item.prepend(h("span", { class: "pk-icon" }, icon(mi.icon)));
    panel.append(item);
  });
  panel.append(h("div", { class: "menu-sep" }), pickerItem({ label: t("Follow Claude Code's setting"), desc: t("currently “{mode}”", { mode: (MODE_INFO[base] || MODE_INFO.default).label }), on: !s.mode, onClick: () => setChatSetting("mode", null) }));
  openPicker(anchor, panel);
}

function cycleMode() {
  const cur = effectiveMode();
  const next = MODE_CYCLE[(MODE_CYCLE.indexOf(cur) + 1) % MODE_CYCLE.length];
  setChatSetting("mode", next);
  toast(t("Permission mode: {mode}", { mode: MODE_INFO[next].label }), { ms: 1400 });
}

// ---------------------------------------------------------------- context ring + limits
// {c, p, known}: known only when both numbers are real; right after /compact the new size is not reported yet
function contextUse() {
  const c = ui.chat.meta && ui.chat.meta.context;
  const known = !!(c && c.window > 0 && Number.isFinite(c.used));
  return { c, known, p: known ? Math.min(1, c.used / c.window) : 0 };
}

function renderContextRing() {
  const btn = $("#chat-ctx-btn");
  if (!btn) return;
  const { c, p, known } = contextUse();
  const r = 6.5, len = 2 * Math.PI * r;
  btn.innerHTML = `<svg viewBox="0 0 18 18" class="ring" aria-hidden="true"><circle cx="9" cy="9" r="${r}" class="bg"/>` +
    `<circle cx="9" cy="9" r="${r}" class="fg" stroke-dasharray="${(len * p).toFixed(2)} ${len.toFixed(2)}" transform="rotate(-90 9 9)"/></svg>`;
  btn.classList.toggle("warn", p >= 0.7);
  btn.classList.toggle("bad", p >= 0.9);
  setTip(btn, known ? t("Context used: {used} / {window} ({pct}%) · click for plan usage", { used: fmtK(c.used), window: fmtK(c.window), pct: Math.round(p * 100) })
    : c && c.compacted ? t("Context compacted · usage updates after the next reply") : t("Context and usage"));
}

function openContextPanel(anchor = $("#chat-ctx-btn")) {
  const { c, p, known } = contextUse();
  const lim = (ui.claude && ui.claude.limits) || {};
  const acct = info() && info().account;
  const bar = (p, cls = "") => h("div", { class: "cx-bar " + cls }, h("i", { style: `width:${Math.round(Math.min(1, p) * 100)}%` }));
  const win =(key, label) => {
    const w = (lim.windows || {})[key];
    if (!w) return null;
    return h("div", { class: "cx-row" }, h("div", { class: "cx-line" }, h("span", null, label), h("span", { class: "cx-val" }, `${Math.round(w.utilization * 100)}%`)),
      bar(w.utilization, w.utilization >= 0.9 ? "bad" : w.utilization >= 0.7 ? "warn" : ""), w.resetsAt && h("div", { class: "cx-sub" }, t("resets {when}", { when: resetText(w.resetsAt) })));
  };
  const send = (cmd) => { closeFloat(); sendChat(cmd); };
  const panel = h("div", { class: "cx-pop", role: "dialog", "aria-label": t("Context and usage") },
    h("div", { class: "pk-head" }, t("Context")),
    known
      ? h("div", { class: "cx-row" }, h("div", { class: "cx-line" }, h("span", null, `${fmtK(c.used)} / ${fmtK(c.window)} tokens`), h("span", { class: "cx-val" }, `${Math.round(p * 100)}%`)),
        bar(p, p >= 0.9 ? "bad" : p >= 0.7 ? "warn" : ""), h("div", { class: "cx-sub" }, p >= 0.7 ? t("Nearly full: compacting frees room, and Claude keeps the key points") : t("When it's full, Claude Code compacts it automatically")))
      : c && c.compacted
        ? h("div", { class: "cx-row" }, h("div", { class: "cx-line" }, h("span", null, t("Compacted")), c.window > 0 ? h("span", { class: "cx-val" }, t("window {size}", { size: fmtK(c.window) })) : null),
          bar(0), h("div", { class: "cx-sub" }, t("Usage updates after the next reply.")))
        : h("div", { class: "cx-sub" }, t("Send a message to see how much context this chat uses.")),
    h("div", { class: "cx-acts" },
      h("button", { type: "button", class: "btn sm", disabled: !ui.chat.id || !ui.chat.meta || !ui.chat.meta.session_id, onclick: () => send("/compact") }, icon("compress"), t("Compact context")),
      h("button", { type: "button", class: "btn sm ghost", disabled: !ui.chat.id || !ui.chat.meta || !ui.chat.meta.session_id, onclick: () => send("/context") }, t("Details"))),
    (lim.windows && (lim.windows.five_hour || lim.windows.seven_day)) && h("div", { class: "pk-head" }, t("Plan usage")),
    win("five_hour", t("Last 5 hours")), win("seven_day", t("This week")),
    lim.windows ? null : h("div", { class: "cx-sub" }, t("Plan usage shows after the first reply.")),
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
  const saved = store(chatKey());
  const list = visibleChats();
  const id = list.some((c) => c.id === saved) ? saved : (list[0] || {}).id || null;
  if (id) await openChat(id, { quiet: true });
  else { ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; renderChatSelect(); renderChat(); renderPickers(); }
  paintChatDot(r.running);
}

function paintChatDot(running = ui.chat.running ? [ui.chat.id] : []) {
  const dot = $("#chat-dot");
  dot.classList.toggle("hidden", !running.length && !ui.chat.waiting.size);
  dot.classList.toggle("wait", ui.chat.waiting.size > 0);
  renderWsTools();   // the dot on Job search's Claude button
}

// the latest call wins: an older one answering late (a chat that just ended, while the user switched
// workspaces) must not bring its chat back
let openSeq = 0;
async function openChat(id, { quiet = false } = {}) {
  const seq = ++openSeq;
  let r;
  try { r = await api("GET", "/api/chats/" + id); }
  catch (e) { // deleted elsewhere: fall back to whatever is left
    store(chatKey(), null);
    const list = await api("GET", "/api/chats");
    ui.chat.list = list.chats;
    if (list.chats.length && list.chats[0].id !== id) return openChat(list.chats[0].id, { quiet });
    ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; ui.chat.running = false;
    renderChatSelect(); renderChat(); renderComposerState(); renderPickers();
    return;
  }
  if (seq !== openSeq) return;
  if (ui.chat.id !== id) askEls.clear();
  ui.chat.id = id;
  ui.chat.meta = r.chat;
  ui.chat.messages = r.messages;
  ui.chat.running = r.running;
  ui.chat.live = r.running ? { blocks: (r.live && r.live.blocks) || [] } : null;
  ui.chat.queue = (r.live && r.live.queue) || [];
  store(chatKey(), id);
  renderChatSelect();
  if (!quiet || chatShown()) renderChat();
  renderComposerState();
  renderPickers();
  renderTasks();
  renderQueue();
}

let reloadTimer = 0;
function reloadChat() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => ui.chat.id && openChat(ui.chat.id, { quiet: !chatShown() }), 30);
}

async function createChat() {
  // in Job search a new chat works in the tool's folder
  const c = W.ws === "jobs" && jobsReady()
    ? (await api("POST", "/api/jobs/chat", { ...chatSettings(), title: jobsTool().name })).chat
    : await api("POST", "/api/chats", chatSettings());
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
  if (!(await modal({ title: t("Delete this chat?"), message: t("Only this window's record goes; your resume data is untouched."), ok: t("Delete"), danger: true }))) return;
  try {
    const r = await api("DELETE", "/api/chats/" + ui.chat.id);
    ui.chat.list = r.chats;
    const next = visibleChats()[0];
    if (next) await openChat(next.id);
    else { ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; store(chatKey(), null); renderChatSelect(); renderChat(); renderPickers(); renderTasks(); }
  } catch (e) { toast(e.message); }
}

async function renameChat(title) {
  if (!ui.chat.id) return;
  const name = title || await modal({ title: t("Rename this chat"), input: ui.chat.meta.title || "", ok: t("Save") });
  if (!name) return;
  try {
    ui.chat.meta = await api("PUT", "/api/chats/" + ui.chat.id, { title: name });
    const i = ui.chat.list.findIndex((c) => c.id === ui.chat.id);
    if (i >= 0) ui.chat.list[i] = ui.chat.meta;
    renderChatSelect();
  } catch (e) { toast(e.message); }
}

// Job search shows the chats that run in the tool's folder; Resume shows every chat
function visibleChats() {
  if (W.ws !== "jobs" || !jobsReady()) return ui.chat.list;
  const id = (J.data.status.tool || {}).id;
  return ui.chat.list.filter((c) => c.workspace === id);
}
const chatKey = () => (W.ws === "jobs" ? "chat-jobs" : "chat");

// each workspace keeps its own open chat
async function chatForWorkspace() {
  const list = visibleChats();
  const saved = store(chatKey());
  const id = list.some((c) => c.id === saved) ? saved : list.some((c) => c.id === ui.chat.id) ? ui.chat.id : (list[0] || {}).id || null;
  if (id && id !== ui.chat.id) { await openChat(id, { quiet: true }); return; }
  if (!id) {
    ui.chat.id = null; ui.chat.meta = null; ui.chat.messages = []; ui.chat.running = false; ui.chat.live = null;
    renderComposerState(); renderPickers();
  }
  renderChatSelect();
  renderChat();
}

function renderChatSelect() {
  const sel = ui.w.chat;
  const list = visibleChats();
  sel.disabled = !list.length;
  sel.setOptions(list.map((c) => ({ value: c.id, label: c.title || t("New chat"), desc: c.workspace || null,
    hint: ui.chat.waiting.has(c.id) ? t("waiting for you") : relTime(c.updated) })), ui.chat.id);
}

// ---------------------------------------------------------------- transcript
function renderChat() {
  const log = $("#chat-log");
  log.replaceChildren();
  ui.chat.liveEl = null;
  if (!ui.chat.messages.length && !ui.chat.running && W.ws === "jobs" && jobsReady()) {
    const name = jobsTool().name;
    const suggest = [
      t("Scan job boards for new postings that fit me"), t("Where do my applications stand, and what should I do next?"),
      t("Which follow-ups are due this week?"), t("Compare the postings I'm most interested in"),
    ];
    log.append(h("div", { class: "chat-welcome" },
      h("div", { class: "t" }, t("Work with {name} and Claude", { name })),
      h("div", null, ui.chat.available ? t("These chats run in {name}'s folder: Claude follows its modes and scripts, and still edits your resume when you ask. “All of {name}” above lists every mode.", { name }) : t("The claude command was not found. Install Claude Code and sign in first.")),
      ui.chat.available && h("div", { class: "suggest" }, suggest.map((s) => h("button", { onclick: () => sendChat(s) }, s)))));
    return;
  }
  if (!ui.chat.messages.length && !ui.chat.running) {
    const roots = (doc().settings || {}).project_roots || [];
    const suggest = [
      roots.length ? t("Look at my project folders ({folders}) and pick the projects most worth putting on my resume; don't change anything until I confirm", { folders: roots.join(", ") }) : t("Help me add a new experience, one question at a time"),
      langs().length > 1 ? t("Check every language version: which entries lack a language, which bullets run past one line") : t("Check whether the current job claims anything the fact layer doesn't support"),
      t("Get the current job onto one page: adjust the layout first, then tighten the wording"),
      t("Check whether the current job claims anything the fact layer doesn't support"),
    ].filter((s, i, a) => a.indexOf(s) === i);
    log.append(h("div", { class: "chat-welcome" },
      h("div", { class: "t" }, t("Work on your resume with Claude")),
      h("div", null, ui.chat.available ? t("This is Claude Code: it can read and edit your resume data and use your installed skills, and the preview refreshes after each change. Type / for commands and skills.") : t("The claude command was not found. Install Claude Code and sign in first.")),
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
    // under the message, like Claude Code: time · copy · rewind · fork
    const tools = index != null && h("div", { class: "msg-tools" },
      m.at && h("span", { class: "mt-time", title: new Date(m.at * 1000).toLocaleString() }, relTime(m.at)),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Copy"), onclick: () => copyText(m.text) }, icon("copy")),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Go back to before this message (rewind): removes it and everything after, so you can edit and resend"), onclick: () => rewindAt(index) }, icon("rewind")),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Fork: a new chat that stops just before this message; this chat stays as it is"), onclick: () => forkAt(index) }, icon("fork")));
    return h("div", { class: "msg-user-wrap" + (action ? " is-action" : "") },
      h("div", { class: "msg-user" + (action ? " is-action" : "") }, action ? h("div", { class: "msg-action" }, icon("sparkle"), action in AI_LABELS ? t(AI_LABELS[action]) : action) : null,
        filesEl(m.files), m.text ? h("div", { class: "msg-text" }, m.text) : null),
      tools);
  }
  const el = h("div", { class: "msg-ai" });
  for (const b of m.blocks || []) {
    if (b.type === "text") el.append(h("div", { class: "md", html: md(b.text) }));
    else if (b.type === "thinking") el.append(h("details", { class: "thought" }, h("summary", null, t("Thinking")), h("div", { class: "md", html: md(b.text) })));
    else if (b.type === "tool") el.append(toolEl(b));
    else if (b.type === "ask") el.append(askEl(b));
    else if (b.type === "note") el.append(h("div", { class: "msg-divider" }, h("span", null, b.text)));
  }
  const blocks = m.blocks || [];
  const waitingOnUser = blocks.some((b) => b.type === "ask" && b.status === "pending");
  if (m.live && !waitingOnUser && (!blocks.length || blocks[blocks.length - 1].type !== "text")) {
    el.append(h("div", { class: "thinking", "aria-label": t("Claude is thinking") }, h("i"), h("i"), h("i"),
      ui.chat.thinking ? h("span", { class: "th-n" }, t("Thinking · {n} tokens", { n: fmtK(ui.chat.thinking) })) : null));
  }
  if (m.error) el.append(h("div", { class: "msg-error" }, m.error));
  if (m.error && isLoginError(m.error)) el.append(loginHelpEl());
  if (m.cancelled) el.append(h("div", { class: "msg-note" }, t("Stopped")));
  return el;
}

// Errors that mean the `claude` command itself isn't signed in. A fixed check, not Claude:
// Claude can't answer while it is signed out, so the window says what is wrong and how to fix it.
const LOGIN_ERROR = /failed to authenticate|oauth|not (logged|signed) in|run \/login|invalid api key|authentication_error|api error: 401/i;
const isLoginError = (text) => LOGIN_ERROR.test(String(text || ""));

function loginHelpEl() {
  return h("div", { class: "msg-help" },
    h("div", { class: "mh-title" }, icon("alert"), t("Claude Code (the command line) isn't signed in")),
    h("div", { class: "md", html: md(t("This chat runs the `claude` command, which signs in on its own, separately from the Claude app. Its sign-in has expired (for example after switching accounts or not using it for a while), so Claude can't answer until it signs in again.")) }),
    h("div", { class: "md", html: md(t("To fix it: open a terminal, run `claude`, type `/login` and finish signing in in the browser. Then send your message here again.")) }));
}

function toolEl(tool) {
  const st = tool.status === "running" ? h("span", { class: "st running" }) : tool.status === "error" ? h("span", { class: "st error" }, icon("x")) : h("span", { class: "st done" }, icon("check"));
  return h("details", { class: "tool" },
    h("summary", null, st, h("span", { class: "tn" }, TOOL_NAMES[tool.name] || tool.name), h("span", { class: "th", title: tool.hint || "" }, shortHint(tool.hint))),
    tool.result && h("pre", null, tool.result));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(t("Copied"), { ms: 1200 }); }
  catch (_) { toast(t("Copy failed")); }
}

// the message comes back into the composer (text and attachments) so it can be rewritten
function restoreDraft(r) {
  ui.chat.files = (r.files || []).map((f) => ({ name: f.name, path: f.path, preview: null }));
  renderPendingFiles();
  startChatWith(r.text);
}

async function forkAt(index) {
  if (!ui.chat.id) return;
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/fork`, { index });
    ui.chat.list = [r.chat, ...ui.chat.list];
    await openChat(r.chat.id);
    restoreDraft(r);
    toast(t("Forked: edit this message and send it; the original chat stays as it is"));
  } catch (e) { toast(e.message); }
}

async function rewindAt(index) {
  if (!ui.chat.id) return;
  if (ui.chat.running) { toast(t("Claude is still replying; stop it before rewinding")); return; }
  const later = ui.chat.messages.length - index;
  const ok = await modal({ title: t("Go back to before this message?"), ok: t("Rewind"),
    message: t("This message and the {n} after it leave this chat, and Claude forgets them; the message goes back into the input so you can edit and resend it. Resume data is not rolled back (undo Claude's changes in each entry). To keep the original chat, use Fork.", { n: later - 1 }) });
  if (!ok) return;
  try {
    const r = await api("POST", `/api/chats/${ui.chat.id}/rewind`, { index });
    await openChat(ui.chat.id);
    restoreDraft(r);
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

const STATUS_TEXT = { allowed: t("Allowed"), denied: t("Denied"), cancelled: t("Cancelled (the reply was stopped)"), answered: t("Answered"), approved: t("Approved"), revise: t("Asked Claude to revise") };

function suggestionLabel(s) {
  if (s.type === "setMode") return MODE_INFO[s.mode] ? t("and switch to “{mode}”", { mode: MODE_INFO[s.mode].label }) : t("and switch mode");
  const where = { session: t("this chat"), localSettings: t("this folder"), projectSettings: t("this project"), userSettings: t("all projects") }[s.destination] || "";
  if (s.type === "addRules" || s.type === "replaceRules") {
    const rules = (s.rules || []).map((r) => r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName).join(", ");
    return where ? t("don't ask again for {rules} ({where})", { rules, where }) : t("don't ask again for {rules}", { rules });
  }
  if (s.type === "addDirectories") return t("and allow access to {dirs}", { dirs: (s.directories || []).join(", ") });
  return t("and remember");
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
      h("pre", { class: "ask-diff" }, lines.slice(0, 14).map((l) => h("span", { class: "add" }, "+ " + l + "\n")), lines.length > 14 ? h("span", { class: "more" }, t("… {n} more line", { n: lines.length - 14 }, "… {n} more lines")) : null)];
  }
  if (inp.url) return h("div", { class: "ask-path" }, inp.url);
  if (path) return h("div", { class: "ask-path", title: path }, path);
  return h("pre", { class: "ask-cmd" }, JSON.stringify(inp, null, 2).slice(0, 600));
}

function permissionCard(a) {
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-perm ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  card.append(h("div", { class: "ask-h" }, icon("shield"), h("span", { class: "ask-t" }, t("Claude wants to {action}", { action: toolAction(a.tool) })),
    !pending && h("span", { class: "ask-st" }, STATUS_TEXT[a.status] || a.status)));
  if (pending || a.status !== "cancelled") card.append(h("div", { class: "ask-body" }, permissionDetail(a)));
  if (!pending) return card;
  const reason = h("input", { class: "ask-input", placeholder: t("Tell Claude what to do instead (optional)"), "aria-label": t("Why not") });
  const denyRow = h("div", { class: "ask-row hidden" }, reason, h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { allow: false, message: reason.value }, card) }, t("Deny and send")));
  reason.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); answerAsk(a, { allow: false, message: reason.value }, card); } });
  card.append(h("div", { class: "ask-acts" },
    h("button", { type: "button", class: "btn sm primary", onclick: () => answerAsk(a, { allow: true }, card) }, t("Allow")),
    (a.suggestions || []).map((s, i) => h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { allow: true, suggestion: i }, card) }, t("Allow, {what}", { what: suggestionLabel(s) }))),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => answerAsk(a, { allow: false }, card) }, t("Deny")),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => { denyRow.classList.toggle("hidden"); reason.focus(); } }, t("Deny with a reason…"))), denyRow);
  return card;
}

function questionCard(a) {
  const qs = (a.input && a.input.questions) || [];
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-q ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  card.append(h("div", { class: "ask-h" }, icon("chat"), h("span", { class: "ask-t" }, qs.length > 1 ? t("Claude has {n} questions", { n: qs.length }) : t("Claude has a question")),
    !pending && h("span", { class: "ask-st" }, STATUS_TEXT[a.status] || a.status)));
  if (!pending) {
    if (a.answers) card.append(h("div", { class: "ask-body" }, qs.map((q) => h("div", { class: "q-done" }, h("span", { class: "q-q" }, q.question), h("span", { class: "q-a" }, a.answers[q.question] || t("(no answer)"))))));
    return card;
  }
  const picked = qs.map(() => new Set());
  const others = [];
  const submit = h("button", { type: "button", class: "btn sm primary", disabled: true, onclick: () => send() }, t("Submit"));
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
    const other = h("input", { class: "ask-input", placeholder: t("Other (write your own)"), "aria-label": t("Other answer") });
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
      q.multiSelect && h("span", { class: "q-multi" }, t("pick any"))), h("div", { class: "q-opts" }, opts), other));
  });
  card.append(body, h("div", { class: "ask-acts" }, submit, h("button", { type: "button", class: "btn sm ghost", onclick: () => answerAsk(a, { skip: true }, card) }, t("Skip"))));
  return card;
}

function planCard(a) {
  const pending = a.status === "pending";
  const card = h("div", { class: `ask ask-plan ${pending ? "pending" : "done " + a.status}`, "data-ask": a.id });
  const st = a.status === "approved" ? `${t("Approved")} · ${a.mode === "acceptEdits" ? MODE_INFO.acceptEdits.label : a.mode === "auto" ? MODE_INFO.auto.label : t("Ask before edits")}`
    : a.status === "revise" ? (a.feedback ? `${t("Asked Claude to revise")}: ${a.feedback}` : t("Asked Claude to revise")) : STATUS_TEXT[a.status];
  card.append(h("div", { class: "ask-h" }, icon("plan"), h("span", { class: "ask-t" }, t("Claude's plan")), !pending && h("span", { class: "ask-st" }, st)));
  card.append(h("div", { class: "ask-body md plan-md", html: md((a.input && a.input.plan) || "") }));
  if (!pending) return card;
  const fb = h("textarea", { class: "ask-input", rows: "2", placeholder: t("What should change? e.g. only the wording for now, keep the structure") });
  const fbRow = h("div", { class: "ask-row hidden" }, fb, h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { approve: false, feedback: fb.value }, card) }, t("Send to Claude")));
  card.append(h("div", { class: "ask-acts" },
    h("button", { type: "button", class: "btn sm primary", onclick: () => answerAsk(a, { approve: true, mode: "acceptEdits" }, card) }, t("Approve, accept edits")),
    h("button", { type: "button", class: "btn sm", onclick: () => answerAsk(a, { approve: true, mode: "default" }, card) }, t("Approve, ask before edits")),
    h("button", { type: "button", class: "btn sm ghost", onclick: () => { fbRow.classList.toggle("hidden"); fb.focus(); } }, t("Keep refining…"))), fbRow);
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
    if (!chatShown() || !ui.chat.running || !ui.chat.live) return;
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
  setTip($("#chat-send"), running ? t("Queue: sent after this reply (Enter)") : t("Send (Enter) · Shift+Enter for a new line"));
  $("#chat-input").disabled = !ui.chat.available;
  const parts = [];
  if (ui.versionId) parts.push(isAll() ? `ALL (${t("All entries")})` : t("the job “{name}”", { name: jobName(version()) }));
  if (ui.sel.kind === "entry" && entryById(ui.sel.id)) parts.push(`“${display(entryById(ui.sel.id).title) || entryLabel(entryById(ui.sel.id))}”`);
  const ws = (ui.chat.meta && ui.chat.meta.workspace) || (W.ws === "jobs" && jobsReady() ? jobsTool().name : null);
  $("#chat-context").textContent = ws ? t("This chat runs in {name}'s folder; it can still edit your resume", { name: ws })
    : parts.length ? t("Claude knows you're looking at: {what}", { what: parts.join(" · ") }) : "";
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
      icon("plan"), h("span", { class: "ct-t" }, cur ? cur.active || cur.subject : t("Tasks")), h("span", { class: "ct-n" }, `${done}/${tasks.length}`),
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
    h("span", { class: "cq-tag" }, t("Queued")), h("span", { class: "cq-text" }, item.msg.text || (item.msg.files || []).map((f) => f.name).join(", ")),
    h("button", { type: "button", class: "btn icon sm ghost", title: t("Don't send"), onclick: () => api("DELETE", `/api/chats/${ui.chat.id}/queue/${item.id}`).catch((e) => toast(e.message)) }, icon("x")))));
}

// ---------------------------------------------------------------- composer: attachments
function renderPendingFiles() {
  const box = $("#chat-files");
  const files = ui.chat.files;
  box.classList.toggle("hidden", !files.length);
  box.replaceChildren(...files.map((f, i) => h("div", { class: "cf-item" + (f.preview ? " img" : ""), title: f.path || f.name },
    f.preview ? h("img", { src: f.preview, alt: f.name }) : h("span", { class: "cf-ic" }, icon("file")),
    !f.preview && h("span", { class: "cf-name" }, f.name),
    h("button", { type: "button", class: "cf-x", "aria-label": t("Remove {name}", { name: f.name }), onclick: () => { ui.chat.files.splice(i, 1); renderPendingFiles(); } }, icon("x")))));
}

function addBrowserFiles(list) {
  for (const file of list) {
    if (file.size > 30 * 1024 * 1024) { toast(t("{name} is over 30 MB", { name: file.name })); continue; }
    const reader = new FileReader();
    reader.onload = () => {
      const isImg = /^image\/(png|jpeg|gif|webp)$/.test(file.type);
      ui.chat.files.push({ name: file.name || (isImg ? t("pasted image") + ".png" : t("file")), data: reader.result, preview: isImg ? reader.result : null });
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
  const panel = h("div", { class: "slash-menu", role: "listbox", "aria-label": t("Commands and skills") });
  slash.items.forEach((c, i) => panel.append(h("div", { role: "option", class: "sl-item" + (i === 0 ? " on" : ""), "aria-selected": String(i === 0), "data-i": String(i),
    onpointerdown: (e) => e.preventDefault(), onclick: () => applySlash(i) },
    h("span", { class: "sl-name" }, "/" + c.name, c.hint && h("span", { class: "sl-hint" }, " " + c.hint)),
    h("span", { class: "sl-desc", title: c.desc }, commandDesc(c)),
    h("span", { class: "sl-src" }, c.local ? t("this window") : TERMINAL_ONLY.has(c.name) ? t("terminal only") : c.source === "user" || c.source === "project" ? t("skill") : c.source === "plugin" ? t("plugin") : ""))));
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
function clearInput() { const ta = $("#chat-input"); ta.value = ""; autosize(ta); }

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
    else { toast(t("The effort level can only be low / medium / high / xhigh / max / auto")); }
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
  const ta = $("#chat-input");
  ta.value = text;
  autosize(ta);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

async function sendChat(text, { action = null, entryId = null, target = null, lang = null, files = [] } = {}) {
  if (!ui.chat.available) { toast(t("The claude command was not found")); return false; }
  if (!action && !text.trim() && !files.length) return false;
  if (action && ui.chat.running) { toast(t("Claude is still replying; wait or stop it first")); return false; }
  await saveDraft();
  if (!ui.chat.id) {
    const c = await createChat();
    ui.chat.id = c.id; ui.chat.meta = c; ui.chat.messages = [];
    store(chatKey(), c.id);
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
    const who = entryId ? `“${display((entryById(entryId) || {}).title) || entryLabel(entryById(entryId))}”` : "";
    ui.chat.messages.push({ role: "user", text: action ? `${who}${text ? ": " + text : ""}` || "…" : text, meta: { action },
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
    if (ev.ask.status === "pending" && (ev.chat !== ui.chat.id || !chatShown() || document.hidden)) {
      const what = ev.ask.kind === "question" ? t("Claude has a question") : ev.ask.kind === "plan" ? t("Claude's plan is waiting for your approval") : t("Claude wants to {action}", { action: toolAction(ev.ask.tool) });
      toast(what, { ms: 8000, action: { label: t("Show me"), fn: async () => { if (ev.chat !== ui.chat.id) await openChat(ev.chat); setTab("chat"); } } });
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
    await openChat(ui.chat.id, { quiet: !chatShown() });
    const r = await api("GET", "/api/chats");
    ui.chat.list = r.chats;
    renderChatSelect();
    renderComposerState();
    await refresh();
    finishAi(ev.chat);
    if (ev.error && !chatShown()) toast(isLoginError(ev.error) ? t("Claude Code isn't signed in; see the chat") : t("Claude ran into an error; see the chat"));
  }
}

const CHAT_EVENTS = ["chat.start", "chat.init", "chat.meta", "chat.thinking", "chat.delta", "chat.tool", "chat.block", "chat.ask",
  "chat.tasks", "chat.queue", "chat.reply", "chat.next", "chat.done", "claude.info", "claude.limits"];
function listenChat(es) {
  for (const name of CHAT_EVENTS) es.addEventListener(name, (m) => onChatEvent(name, JSON.parse(m.data)));
}

// ---------------------------------------------------------------- wiring
function wireChat() {
  ui.w.chat = uiSelect({ id: "chat-select", cls: "grow", label: t("Chats"), placeholder: t("No chats yet"), menuWidth: 300, onChange: (id) => openChat(id) });
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
