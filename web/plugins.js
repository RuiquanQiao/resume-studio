// Plugins (docs/PLUGINS.md): each plugin that is on is a workspace of its own in the top bar, after Plan (2026-11-12);
// its collections are tabs there (when it has more than one), each a table (web/dbview.js) whose records open as a
// page. Settings → Plugins turns them on and off; the plugin market installs more. A plugin is data only: everything
// here is drawn from its manifest.
"use strict";

const PL = { list: [], records: {}, loaded: false };
// a plugin's {lang: text} label in the window's language, else English, else any
const L = (x) => !x ? "" : typeof x === "string" ? x : x[I18N.lang] || x.en || Object.values(x)[0] || "";
const PCHIP = { gray: "s-plain", blue: "pc-blue", green: "pc-green", yellow: "pc-yellow", orange: "pc-orange", red: "pc-red", purple: "pc-purple" };
let pendingPlugins = {};   // Settings: switches flipped, applied on Done

async function loadPlugins() {
  try {
    const r = await api("GET", "/api/plugins");
    PL.list = r.plugins || []; PL.records = r.records || {};
  } catch (e) { /* kept as they were */ }
  PL.loaded = true;
}

// after the plugins changed (an event, Settings, the market): the top bar's buttons, and the plugin on screen
function pluginsChanged() {
  paintPluginButtons();
  if (!isPluginWs()) return;
  if (typeof dbvBusy === "function" && dbvBusy()) DBV.pending = true;   // an open cell editor: redrawn when it closes
  else paintPluginspace();
}

function listenPlugins(es) {
  es.addEventListener("plugins", async () => { await loadPlugins(); pluginsChanged(); });
}

const pluginsOn = () => PL.list.filter((p) => p.on && p.manifest);
const pluginRows = (pid, cid) => ((PL.records[pid] || {})[cid]) || [];
const titleProp = (c) => c.properties.find((x) => x.type === "title");
const optionOf = (prop, v) => (prop.options || []).find((o) => o.value === v);
const optionLabel = (prop, v) => { const o = optionOf(prop, v); return o ? L(o.label) : v || ""; };
const pluginChip = (prop, v) => ({ value: v, label: optionLabel(prop, v), cls: PCHIP[(optionOf(prop, v) || {}).color || "gray"] });

// ================================================================ the workspace
// W.ws is "p.<plugin id>"; body[data-ws] is "plugin" for all of them (style.css)
const PW = { col: {}, pages: {}, q: "" };   // per plugin: the collection open, the record open ({cid, rid})
function restorePw() {   // at start (initWorkspaces): store() is app.js's, loaded after this file
  try { const s = JSON.parse(store("plugin-ws") || "{}"); PW.col = s.col || {}; PW.pages = s.pages || {}; } catch (e) { /* none */ }
}
const storePw = () => store("plugin-ws", JSON.stringify({ col: PW.col, pages: PW.pages }));
const PW_SCROLL = {};   // a table's scroll, kept while one of its records is open
const isPluginWs = (ws = W.ws) => typeof ws === "string" && ws.startsWith("p.");
const wsPlugin = (ws = W.ws) => isPluginWs(ws) ? pluginsOn().find((p) => "p." + p.id === ws) || null : null;
const pluginCollection = (p) => p.manifest.collections.find((c) => c.id === PW.col[p.id]) || p.manifest.collections[0];
const tabOf = (p, c) => ({ id: `p.${p.id}.${c.id}`, p, c });

function pluginRecordOnScreen() {
  const p = wsPlugin();
  const pg = p && PW.pages[p.id];
  const c = pg && p.manifest.collections.find((x) => x.id === pg.cid);
  const row = c && pluginRows(p.id, c.id).find((r) => r.id === pg.rid);
  return row ? { ...tabOf(p, c), row } : null;
}

function paintPluginspace() {
  const el = $("#pluginspace");
  if (el && isPluginWs()) repaintKeeping(el, () => paintPluginspaceNow(el));
}
function paintPluginspaceNow(el) {
  const p = wsPlugin();
  if (!p) {
    if (!PL.loaded) { el.replaceChildren(h("div", { class: "hunt-wrap" }, h("div", { class: "muted" }, t("Loading…")))); return; }
    setWorkspace("resume");   // its plugin was turned off or uninstalled
    return;
  }
  const c = pluginCollection(p);
  const rec = pluginRecordOnScreen();
  if (PW.pages[p.id] && !rec) { delete PW.pages[p.id]; storePw(); }   // deleted, here or by the AI
  const key = rec ? `rec:${p.id}.${rec.c.id}.${rec.row.id}` : `table:p.${p.id}.${c.id}`;
  const old = el.querySelector(".hunt-scroll");
  if (old && old.dataset.page !== key && old.dataset.page.startsWith("table:")) PW_SCROLL[old.dataset.page] = old.scrollTop;
  const scroll = old && old.dataset.page === key ? old.scrollTop : PW_SCROLL[key] || 0;
  const cs = p.manifest.collections;
  const bar = cs.length > 1 && !rec ? h("div", { class: "hunt-bar" },
    h("div", { class: "hunt-tabs", role: "tablist", "aria-label": L(p.manifest.name) }, cs.map((x) =>
      h("button", { type: "button", role: "tab", class: "hunt-tab" + (x.id === c.id ? " on" : ""), "data-tab": x.id, "aria-selected": String(x.id === c.id),
        onclick: () => { PW.col[p.id] = x.id; storePw(); paintPluginspace(); welcomeAgain(); } },
        L(x.name), h("span", { class: "n" }, pluginRows(p.id, x.id).length))))) : null;
  const sc = h("div", { class: "hunt-scroll", "data-page": key },
    h("div", { class: "hunt-wrap" + (rec ? " is-page" : "") }, rec ? pluginRecordPage(rec) : pluginTable(tabOf(p, c))));
  el.replaceChildren(...[bar, sc].filter(Boolean));
  sc.scrollTop = scroll;
  if (key.startsWith("table:")) sc.addEventListener("scroll", () => { if (sc.isConnected) PW_SCROLL[key] = sc.scrollTop; }, { passive: true });
  dbvKeepX(sc);
  for (const ta of el.querySelectorAll("textarea")) autosizeFull(ta);
}

// ---- the top bar: one button per plugin after Plan. The bar is the window's title bar, so it always keeps room
// to drag the window by: buttons that don't fit go into a "more" menu, the one on screen never (the priority+
// pattern, as browsers' bookmark bars and VS Code's activity bar do)
const DRAG_ROOM = 120;   // px of the top bar left empty, at the least
function paintPluginButtons() {
  const sw = $("#ws-switch");
  if (!sw) return;
  for (const b of sw.querySelectorAll(".plugin-ws, .ws-more")) b.remove();
  pluginsOn().forEach((p, i) => {
    const name = L(p.manifest.name), ws = "p." + p.id;
    sw.append(h("button", { type: "button", class: "ws-btn plugin-ws" + (W.ws === ws ? " on" : ""), role: "tab", "data-ws": ws,
      "aria-selected": String(W.ws === ws), title: i < 5 ? `${name} (Ctrl+${i + 5})` : name, onclick: () => setWorkspace(ws) },
      h("span", { class: "ws-mono", "aria-hidden": "true" }, [...name][0] || "?"), h("span", { class: "lbl" }, name)));
  });
  sw.append(h("button", { type: "button", class: "ws-btn ws-more hidden", "aria-haspopup": "menu", title: t("More workspaces"),
    "aria-label": t("More workspaces"), onclick: (e) => openMoreWorkspaces(e.currentTarget) }, icon("more")));
  fitWsSwitch();
}

// the empty width of the top bar: what its buttons and texts don't take
function topbarRoom(bar) {
  const cs = getComputedStyle(bar);
  const kids = [...bar.children].filter((k) => k.getClientRects().length);
  let used = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + (parseFloat(cs.columnGap) || 0) * Math.max(0, kids.length - 1);
  for (const k of kids) {
    if (k.classList.contains("spacer")) continue;
    if (k.id === "ws-tools") {   // it stretches (flex: 1): count what is in it
      const g = parseFloat(getComputedStyle(k).columnGap) || 0;
      const inner = [...k.children].filter((x) => x.getClientRects().length && !x.classList.contains("spacer"));
      used += parseFloat(getComputedStyle(k).paddingLeft) + inner.reduce((s, x) => s + x.getBoundingClientRect().width, 0) + g * Math.max(0, inner.length - 1);
    } else used += k.getBoundingClientRect().width;
  }
  return bar.clientWidth - used;
}

function fitWsSwitch() {
  const bar = $(".topbar"), sw = $("#ws-switch");
  if (!bar || !sw) return;
  const btns = [...sw.querySelectorAll(".plugin-ws")];
  const more = sw.querySelector(".ws-more");
  for (const b of btns) b.classList.remove("hidden");
  if (more) more.classList.add("hidden");
  for (let i = btns.length - 1; i >= 0 && topbarRoom(bar) < DRAG_ROOM; i--) {
    if (btns[i].classList.contains("on")) continue;
    btns[i].classList.add("hidden");
    if (more) more.classList.remove("hidden");
  }
}
window.addEventListener("resize", () => fitWsSwitch());

function openMoreWorkspaces(anchor) {
  const hidden = [...$("#ws-switch").querySelectorAll(".plugin-ws.hidden")];
  openMenu(anchor, [
    ...hidden.map((b) => ({ label: b.querySelector(".lbl").textContent, onClick: () => setWorkspace(b.dataset.ws) })),
    { label: t("Manage plugins…"), icon: "settings", onClick: () => { openSettings(); setTimeout(() => { const s = $("#plugins-settings"); if (s) s.scrollIntoView({ block: "start" }); }, 0); } },
  ], { focusFirst: true });
}

// ================================================================ the table
async function savePluginField(pid, cid, row, key, value) {
  const r = await api("PUT", `/api/plugins/${pid}/records/${cid}/${row.id}`, { [key]: value });
  const list = pluginRows(pid, cid);
  const i = list.findIndex((x) => x.id === row.id);
  if (i >= 0) list[i] = r;
  return r;
}

function pluginProps(p, c) {
  const set = (prop) => (row, v) => savePluginField(p.id, c.id, row, prop.key, v).then(() => paintPluginspace());
  return c.properties.map((prop) => {
    const base = { key: prop.key, label: L(prop.label), width: prop.width, hidden: !!prop.hidden, get: (r) => r[prop.key] ?? "" };
    if (prop.type === "title") return { ...base, type: "title" };
    if (prop.type === "select") return { ...base, type: "select", group: true, free: !!prop.free, set: set(prop),
      options: () => [...(prop.options || []).map((o) => pluginChip(prop, o.value)),
        ...(prop.free ? [...new Set(pluginRows(p.id, c.id).map((r) => r[prop.key]).filter((v) => v && !optionOf(prop, v)))].map((v) => pluginChip(prop, v)) : [])] };
    if (prop.type === "date") return { ...base, type: "date", set: set(prop) };
    if (prop.type === "url") return { ...base, type: "text", set: set(prop),
      render: (r) => r[prop.key] ? h("a", { href: r[prop.key], "data-ext": "", title: r[prop.key], class: "c-link" }, siteName(r[prop.key])) : "" };
    if (prop.type === "number") return { ...base, type: "text", set: set(prop), get: (r) => (r[prop.key] ?? "") };
    return { ...base, type: "text", set: set(prop) };
  });
}

function pluginDated(c) {
  if (!(c.dates || []).length) return null;
  const st = c.status && c.properties.find((x) => x.key === c.status);
  return (r) => {
    const items = c.dates.filter((k) => r[k]).map((k) => ({ at: r[k], label: r[titleProp(c).key] || "",
      sub: L((c.properties.find((x) => x.key === k) || {}).label), kind: r[k] < today() ? "past" : "deadline" }));
    const days = items.map((x) => x.at).sort();
    return { start: days[0] || null, end: days[days.length - 1] || null, cls: st ? pluginChip(st, r[c.status]).cls : "", items };
  };
}

function pluginTable(tab) {
  const { p, c } = tab;
  const m = p.manifest;
  const rows = pluginRows(p.id, c.id);
  const err = (PL.records[p.id] || {}).error;
  const q = PW.q.trim().toLowerCase();
  const texts = (r) => c.properties.map((x) => x.type === "select" ? optionLabel(x, r[x.key]) : r[x.key]);
  const shown = q ? rows.filter((r) => texts(r).some((x) => String(x || "").toLowerCase().includes(q))) : rows;
  const search = h("input", { class: "hunt-search", type: "search", id: "plugin-search", value: PW.q, placeholder: t("Search"), spellcheck: "false",
    "aria-label": t("Search"), oninput: (e) => { PW.q = e.target.value; paintPluginspace(); } });
  return [
    h("div", { class: "hunt-head" }, h("h1", null, L(c.name)), h("p", null, L(m.description))),
    err ? h("p", { class: "fs-status bad" }, icon("alert"), err) : null,
    h("div", { class: "hunt-tools" },
      h("button", { class: "btn sm" + (rows.length ? "" : " primary"), id: "plugin-new", onclick: () => newPluginRecord(tab) },
        icon("plus"), t("New {item}", { item: L(c.item) })),
      h("span", { class: "spacer" }), search),
    dbView({ table: tab.id, props: pluginProps(p, c), rows: shown, rowId: (r) => r.id,
      open: (r) => openPluginRecord(p.id, c.id, r.id), repaint: paintPluginspace, dated: pluginDated(c),
      defaults: (c.views || [{ id: "v1", name: { en: "All" } }]).map((v) => ({ id: v.id, name: L(v.name), layout: v.layout || "table", group: v.group || "" })),
      empty: h("div", { class: "plugin-empty" },
        h("p", null, t("No {items} yet. Add one here, or ask the AI on the left to find them for you.", { items: L(c.name) }))) }),
  ];
}

async function newPluginRecord(tab) {
  const tp = titleProp(tab.c);
  const name = await modal({ title: t("New {item}", { item: L(tab.c.item) }), input: "", placeholder: L(tp.label), ok: t("Add") });
  if (!name) return;
  let r;
  try { r = await api("POST", `/api/plugins/${tab.p.id}/records/${tab.c.id}`, { [tp.key]: name }); }
  catch (e) { toast(e.message); return; }
  (PL.records[tab.p.id] = PL.records[tab.p.id] || {})[tab.c.id] = [...pluginRows(tab.p.id, tab.c.id), r];
  openPluginRecord(tab.p.id, tab.c.id, r.id);
}

// ================================================================ a record's page
async function openPluginRecord(pid, cid, rid) {
  await flushTyping();
  PW.col[pid] = cid; PW.pages[pid] = { cid, rid };
  storePw();
  if (W.ws !== "p." + pid) setWorkspace("p." + pid);
  else paintPluginspace();
  welcomeAgain();
}

async function closePluginRecord(pid) {
  await flushTyping();
  delete PW.pages[pid];
  storePw();
  paintPluginspace();
  welcomeAgain();
}

function pluginField(tab, prop) {
  const { p, c, row } = tab;
  const save = (v) => savePluginField(p.id, c.id, row, prop.key, v).catch((e) => toast(e.message));
  const hint = L(prop.hint);
  if (prop.type === "select") {
    const opts = [{ value: "", label: t("Not set") }, ...(prop.options || []).map((o) => ({ value: o.value, label: L(o.label) })),
      ...(row[prop.key] && !optionOf(prop, row[prop.key]) ? [{ value: row[prop.key], label: row[prop.key] }] : [])];
    return h("div", { class: "field" }, h("label", null, L(prop.label), hint && h("span", { class: "hint" }, hint)),
      uiSelect({ options: opts, value: row[prop.key] || "", label: L(prop.label), onChange: (v) => save(v) }).el);
  }
  if (prop.type === "date") {
    const input = h("input", { type: "date", value: row[prop.key] || "", "aria-label": L(prop.label),
      onchange: (e) => { if (e.target.value !== (row[prop.key] || "")) save(e.target.value); } });
    return h("div", { class: "field" }, h("label", null, L(prop.label), hint && h("span", { class: "hint" }, hint)), input);
  }
  const value = prop.type === "number" && row[prop.key] != null ? String(row[prop.key]) : row[prop.key];
  return hfield(L(prop.label), value, save, { hint, link: prop.type === "url", multiline: prop.type === "longtext" || prop.type === "text" || prop.type === "title",
    rows: prop.type === "longtext" ? 3 : 1, placeholder: prop.type === "url" ? "https://" : "" });
}

function pluginRecordPage(tab) {
  const { p, c, row } = tab;
  const props = c.properties;
  // one-line fields pair up, each starting at its row's top (a neighbour that wraps never pushes it down); a long
  // text has a row of its own (2026-11-11)
  const short = (x) => x.type !== "longtext" && x.type !== "title";
  const body = [];
  let pair = [];
  const flush = () => { if (pair.length) body.push(pair.length === 2 ? h("div", { class: "grid2 plugin-pair" }, ...pair) : pair[0]); pair = []; };
  for (const prop of props) {
    const el = pluginField(tab, prop);
    if (short(prop)) { pair.push(el); if (pair.length === 2) flush(); } else { flush(); body.push(el); }
  }
  flush();
  const del = async () => {
    if (!(await modal({ title: t("Delete “{name}”?", { name: row[titleProp(c).key] }), message: t("This record is removed from {name}.", { name: L(p.manifest.name) }), ok: t("Delete"), danger: true }))) return;
    try { await api("DELETE", `/api/plugins/${p.id}/records/${c.id}/${row.id}`); }
    catch (e) { toast(e.message); return; }
    PL.records[p.id][c.id] = pluginRows(p.id, c.id).filter((r) => r.id !== row.id);
    closePluginRecord(p.id);
  };
  return [
    h("button", { class: "btn sm ghost hunt-back", onclick: () => closePluginRecord(p.id) }, icon("chevLeft"), L(c.name)),
    h("div", { class: "hunt-head" }, h("h1", null, row[titleProp(c).key] || t("Untitled")),
      h("p", null, L(p.manifest.name), " · ", t("added {date}", { date: (row.created || "").slice(0, 10) }), row.by === "claude" ? " · " + t("by the AI") : "")),
    card(L(c.item), "", body),
    h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn sm danger-soft", onclick: del }, icon("trash"), t("Delete"))),
  ];
}

// ================================================================ the chat
// what the chat is told is on screen
function pluginScreenLine() {
  const rec = pluginRecordOnScreen();
  if (rec) return t("the {item} “{name}” ({plugin})", { item: L(rec.c.item), name: rec.row[titleProp(rec.c).key], plugin: L(rec.p.manifest.name) });
  const p = wsPlugin();
  return p ? t("{collection} ({plugin})", { collection: L(pluginCollection(p).name), plugin: L(p.manifest.name) }) : "";
}

// an empty chat in a plugin's workspace: what the AI does here, and the plugin's own suggestions
function pluginWelcome() {
  const p = wsPlugin();
  const agent = agentOf().short;
  return h("div", { class: "chat-welcome" },
    h("div", { class: "t" }, t("{agent}, with {plugin}", { agent, plugin: p ? L(p.manifest.name) : "" })),
    h("div", null, !ui.chat.available ? missingAgentText()
      : t("Tell {agent} what you have heard of, or ask it to look: it fills the table from official sources and says where each fact comes from.", { agent })),
    ui.chat.available && p && h("div", { class: "suggest" }, (p.manifest.suggestions || []).map(L).map((s) => h("button", { onclick: () => sendChat(s) }, s))));
}

// ================================================================ Settings → Plugins
function pluginsSettingsField() {
  pendingPlugins = {};
  const box = h("div", { class: "field", id: "plugins-settings" });
  const paint = () => {
    const rows = PL.list.map((p) => {
      if (!p.manifest) return h("div", { class: "plugin-row bad" }, h("span", { class: "fs-status bad" }, icon("alert"), t("{name} doesn't load: {why}", { name: p.id, why: p.error })));
      const m = p.manifest;
      const on = pendingPlugins[p.id] ?? p.on;
      return h("div", { class: "plugin-row", "data-plugin": p.id },
        h("button", { type: "button", class: "check-row", role: "checkbox", "aria-checked": String(on), onclick: () => { pendingPlugins[p.id] = !on; paint(); } },
          h("span", { class: "check" + (on ? " on" : "") }),
          h("span", null, L(m.name),
            h("span", { class: "hint" }, L(m.description)),
            h("span", { class: "plugin-meta" }, [`v${m.version}`, p.source === "bundled" ? t("Comes with Resume Studio") : m.author, p.source === "market" && t("From the plugin market")].filter(Boolean).join(" · ")))),
        p.source === "market" && h("button", { class: "btn sm ghost", onclick: () => uninstallPlugin(p, paint) }, t("Uninstall")));
    });
    box.replaceChildren(
      h("label", null, t("Plugins"), h("span", { class: "hint" }, t("Other kinds of applying, each a workspace of its own in the top bar: talent programmes, civil service exams, PhDs… Turning one off keeps its records."))),
      h("div", { class: "plugin-list" }, rows),
      h("div", { class: "row wrap" }, h("button", { class: "btn sm", id: "plugin-market-open", onclick: () => openMarket(paint) }, icon("download"), t("Browse the plugin market"))));
  };
  paint();
  return box;
}

async function applyPlugins() {
  const changes = Object.entries(pendingPlugins).filter(([id, on]) => (PL.list.find((p) => p.id === id) || {}).on !== on);
  pendingPlugins = {};
  for (const [id, on] of changes) await api("PUT", `/api/plugins/${id}`, { on });
  if (changes.length) { await loadPlugins(); pluginsChanged(); }
}

async function uninstallPlugin(p, repaint) {
  if (!(await confirmOver(t("Uninstall {name}?", { name: L(p.manifest.name) }), t("Its records stay in your data folder: installing it again brings them back."), t("Uninstall")))) return;
  try { await api("DELETE", `/api/plugins/${p.id}`); }
  catch (e) { toast(e.message); return; }
  await loadPlugins();
  repaint();
  pluginsChanged();
}

// a small question over an open dialog (Settings): its own layer, Escape closes only it
function confirmOver(title, message, ok) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    let layer;
    const close = (v) => { layer.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(false); } };
    document.addEventListener("keydown", onKey, true);
    const cancel = h("button", { class: "btn", onclick: () => close(false) }, t("Cancel"));
    layer = h("div", { class: "modal-back over" }, h("div", { class: "modal", role: "dialog", "aria-modal": "true" },
      h("h3", null, title), h("p", null, message),
      h("div", { class: "acts" }, cancel, h("button", { class: "btn danger-soft", onclick: () => close(true) }, ok))));
    root.append(layer);
    setTimeout(() => cancel.focus(), 0);
  });
}

// ================================================================ the plugin market
function openMarket(after) {
  const root = $("#modal-root");
  let layer;
  const close = () => { layer.remove(); document.removeEventListener("keydown", onKey, true); if (after) after(); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  const list = h("div", { class: "market-list", "aria-busy": "true" }, h("p", { class: "muted" }, t("Loading…")));
  layer = h("div", { class: "modal-back over", onmousedown: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal market", role: "dialog", "aria-modal": "true", "aria-label": t("Plugin market") },
      h("h3", null, t("Plugin market")),
      h("p", null, t("Plugins add other kinds of applying. They are data only: tables, fields and a guide for the AI, never programs.")),
      list,
      h("p", { class: "settings-note" }, t("Made one? Publish it with a pull request to the market's list: see docs/PLUGINS.md.")),
      h("div", { class: "acts" }, h("button", { class: "btn primary", onclick: close }, t("Done")))));
  root.append(layer);
  const paint = (got) => {
    list.removeAttribute("aria-busy");
    const entries = got.plugins || [];
    list.replaceChildren(
      got.offline ? h("p", { class: "fs-status warn" }, icon("alert"), t("The market can't be reached right now; these come with Resume Studio.")) : null,
      ...entries.map((e) => {
        const busy = h("span");
        const act = e.installed && !e.update && e.on ? h("span", { class: "state-chip pc-green" }, t("Installed"))
          : h("button", { class: "btn sm" + (e.update ? " primary" : ""), "data-install": e.id, onclick: async (ev) => {
            const b = ev.currentTarget;
            b.disabled = true; b.setAttribute("aria-busy", "true");
            try { await api("POST", "/api/plugin-market/install", { id: e.id }); await loadPlugins(); toast(t("{name} is on: it is in the top bar", { name: L(e.name) })); }
            catch (err) { toast(err.message, { ms: 8000 }); b.disabled = false; b.removeAttribute("aria-busy"); return; }
            pluginsChanged();
            load();
          } }, e.update ? t("Update to v{v}", { v: e.version }) : e.installed ? t("Turn on") : t("Install"));
        return h("div", { class: "market-row", "data-plugin": e.id },
          h("div", { class: "market-text" }, h("b", null, L(e.name) || e.id),
            h("span", { class: "hint" }, L(e.description)),
            h("span", { class: "plugin-meta" }, [`v${e.version || "?"}`, e.bundled ? t("Comes with Resume Studio") : e.author].filter(Boolean).join(" · "))),
          busy, act);
      }));
  };
  const load = () => api("GET", "/api/plugin-market").then(paint).catch((e) => paint({ plugins: [], offline: true, error: e.message }));
  load();
}
