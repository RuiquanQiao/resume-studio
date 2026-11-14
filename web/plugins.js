// Plugins (docs/PLUGINS.md): each plugin that is on is a workspace of its own in the top bar, after Plan (2026-11-12);
// its collections are tabs there (when it has more than one), each a table (web/dbview.js) whose records open as a
// page. Settings → Plugins turns them on and off; the plugin market installs more. A plugin is data only: everything
// here is drawn from its manifest.
"use strict";

const PL = { list: [], records: {}, loaded: false, order: (() => {   // order: the top bar's workspaces, as dragged
  try { const v = JSON.parse(localStorage.getItem("rs:ws-order") || "[]"); return Array.isArray(v) ? v : []; } catch (e) { return []; }
})() };
// a plugin's {lang: text} label in the window's language, else English, else any
const L = (x) => !x ? "" : typeof x === "string" ? x : x[I18N.lang] || x.en || Object.values(x)[0] || "";
const PCHIP = { gray: "s-plain", blue: "pc-blue", green: "pc-green", yellow: "pc-yellow", orange: "pc-orange", red: "pc-red", purple: "pc-purple" };
let pendingPlugins = {};   // Settings: switches flipped, applied on Done

async function loadPlugins() {
  try {
    const r = await api("GET", "/api/plugins");
    PL.list = r.plugins || []; PL.records = r.records || {};
    if (Array.isArray(r.ws_order)) { PL.order = r.ws_order; store("ws-order", JSON.stringify(r.ws_order)); }
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
// the plugins in the order the top bar shows them (Ctrl+5… follow it)
const wsRank = (ws, i) => { const k = PL.order.indexOf(ws); return k < 0 ? 1000 + i : k; };
const pluginsInBar = () => pluginsOn().map((p, i) => [p, wsRank("p." + p.id, i)]).sort((a, b) => a[1] - b[1]).map(([p]) => p);

function paintPluginButtons() {
  const sw = $("#ws-switch");
  if (!sw) return;
  for (const b of sw.querySelectorAll(".plugin-ws, .ws-more")) b.remove();
  pluginsInBar().forEach((p, i) => {
    const name = L(p.manifest.name), ws = "p." + p.id;
    sw.append(h("button", { type: "button", class: "ws-btn plugin-ws" + (W.ws === ws ? " on" : ""), role: "tab", "data-ws": ws,
      "aria-selected": String(W.ws === ws), title: i < 5 ? `${name} (Ctrl+${i + 5})` : name, onclick: () => setWorkspace(ws) },
      h("span", { class: "ws-mono", "aria-hidden": "true" }, [...name][0] || "?"), h("span", { class: "lbl" }, name)));
  });
  sw.append(h("button", { type: "button", class: "ws-btn ws-more hidden", "aria-haspopup": "menu", title: t("More workspaces"),
    "aria-label": t("More workspaces"), onclick: (e) => openMoreWorkspaces(e.currentTarget) }, icon("more")));
  orderWsSwitch(sw);
  fitWsSwitch();
}

// ---- the top bar's workspaces are dragged into any order, like a browser's tabs; the order is this computer's
// (prefs.json ws_order), so a new port or a restart keeps it. A workspace never placed keeps its default spot
function orderWsSwitch(sw = $("#ws-switch")) {
  if (!sw) return;
  const btns = [...sw.querySelectorAll(".ws-btn:not(.ws-more)")];
  const more = sw.querySelector(".ws-more");
  btns.map((b, i) => [b, wsRank(b.dataset.ws, i)]).sort((a, b) => a[1] - b[1]).forEach(([b]) => sw.insertBefore(b, more));
  document.documentElement.classList.remove("ws-ordering");
}

function initWsDrag(sw = $("#ws-switch")) {
  if (!sw || sw.dataset.drag) return;
  sw.dataset.drag = "1";
  sw.addEventListener("pointerdown", (e) => {
    const b = e.target.closest(".ws-btn");
    if (!b || b.classList.contains("ws-more") || e.button !== 0) return;
    const x0 = e.clientX;
    let grab = 0, moved = false;
    const shown = () => [...sw.querySelectorAll(".ws-btn:not(.ws-more)")].filter((x) => x !== b && x.getClientRects().length);
    const move = (ev) => {
      if (!moved) {
        if (Math.abs(ev.clientX - x0) < 5) return;   // a click, not a drag
        moved = true;
        grab = x0 - b.getBoundingClientRect().left;
        b.setPointerCapture(ev.pointerId);
        sw.classList.add("ws-sorting"); b.classList.add("ws-dragging");
        closeFloat();
      }
      // the button follows the pointer; the one it passes the middle of trades places with it, sliding over
      const box = sw.getBoundingClientRect();
      const left = Math.max(box.left, Math.min(box.right - b.offsetWidth, ev.clientX - grab));
      const mid = left + b.offsetWidth / 2;
      const others = shown();
      const before = others.find((x) => { const r = x.getBoundingClientRect(); return mid < r.left + r.width / 2; }) || sw.querySelector(".ws-more");
      if (b.nextElementSibling !== before) {
        const was = new Map(others.map((x) => [x, x.getBoundingClientRect().left]));
        sw.insertBefore(b, before);
        for (const [x, l] of was) {
          const dx = l - x.getBoundingClientRect().left;
          if (!dx) continue;
          x.style.transition = "none"; x.style.transform = `translateX(${dx}px)`;
          requestAnimationFrame(() => { x.style.transition = ""; x.style.transform = ""; });
        }
      }
      b.style.transform = "";
      b.style.transform = `translateX(${left - b.getBoundingClientRect().left}px)`;
    };
    const up = () => {
      sw.removeEventListener("pointermove", move);
      sw.removeEventListener("pointerup", up);
      sw.removeEventListener("pointercancel", up);
      if (!moved) return;
      b.classList.remove("ws-dragging"); sw.classList.remove("ws-sorting");
      b.style.transform = "";
      // the click that ends a drag doesn't switch the workspace (it comes right after pointerup, if at all)
      const eat = (c) => { c.stopPropagation(); c.preventDefault(); };
      sw.addEventListener("click", eat, { capture: true, once: true });
      setTimeout(() => sw.removeEventListener("click", eat, { capture: true }), 0);
      PL.order = [...sw.querySelectorAll(".ws-btn:not(.ws-more)")].map((x) => x.dataset.ws);
      store("ws-order", JSON.stringify(PL.order));
      api("PUT", "/api/workspace-order", { order: PL.order }).catch((err) => toast(err.message));
      paintPluginButtons();   // Ctrl+5… and their tips follow the new order
    };
    sw.addEventListener("pointermove", move);
    sw.addEventListener("pointerup", up);
    sw.addEventListener("pointercancel", up);
  });
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

// ---- relations: a record linked to records of another collection of the same plugin (a programme to its university)
const relTarget = (p, prop) => p.manifest.collections.find((x) => x.id === prop.to);
const relIds = (v) => Array.isArray(v) ? v : v ? [v] : [];
const relTitle = (p, prop, id) => {
  const tc = relTarget(p, prop), r = tc && pluginRows(p.id, tc.id).find((x) => x.id === id);
  return r ? r[titleProp(tc).key] || t("Untitled") : "";
};
const relText = (p, prop, v) => relIds(v).map((id) => relTitle(p, prop, id)).filter(Boolean).join(", ");
// a linked record as a chip: clicking it opens that record
function relChip(p, prop, id) {
  const name = relTitle(p, prop, id);
  if (!name) return null;
  return h("button", { type: "button", class: "plugin-rel", "data-rel": id, title: name,
    onclick: (e) => { e.stopPropagation(); openPluginRecord(p.id, prop.to, id); } }, name);
}

// pick the linked record(s): typing finds one by its title, or adds a new one to the other collection; several
// (multiple) are ticked on and off, the list staying open
function relPicker(anchor, p, prop, row, save) {
  const tc = relTarget(p, prop), tkey = titleProp(tc).key;
  let cur = relIds(row[prop.key]);
  const input = h("input", { type: "text", class: "dbv-optinput", spellcheck: "false", "aria-label": L(prop.label),
    placeholder: t("Find or add a {item}", { item: L(tc.item) }) });
  const list = h("div", { class: "listbox dbv-pop dbv-options", role: "listbox", "aria-label": L(prop.label), "aria-multiselectable": String(!!prop.multiple) });
  const put = async (ids) => {
    cur = ids;
    try { await save(prop.multiple ? ids : ids[0] || ""); } catch (e) { toast(e.message); }
  };
  const fill = () => {
    const q = input.value.trim(), qq = q.toLowerCase();
    const rows = pluginRows(p.id, tc.id);
    const shown = rows.filter((r) => !qq || String(r[tkey] || "").toLowerCase().includes(qq));
    const opt = (r) => {
      const on = cur.includes(r.id);
      return h("button", { type: "button", role: "option", class: "lb-opt" + (on ? " on" : ""), "aria-selected": String(on), "data-value": r.id,
        onclick: () => {
          if (!prop.multiple) { (on ? Promise.resolve() : put([r.id])).then(closeFloat); return; }
          put(on ? cur.filter((x) => x !== r.id) : [...cur, r.id]).then(fill);
        } },
      h("span", { class: "lb-check" }, on ? icon("check") : null), h("span", { class: "lb-text" }, r[tkey] || t("Untitled")));
    };
    list.replaceChildren(...shown.map(opt),
      ...(q && !rows.some((r) => String(r[tkey] || "").toLowerCase() === qq) ? [h("button", { type: "button", role: "option", class: "lb-opt dbv-newopt",
        onclick: async () => {
          let r;
          try { r = await api("POST", `/api/plugins/${p.id}/records/${tc.id}`, { [tkey]: q }); }
          catch (e) { toast(e.message); return; }
          (PL.records[p.id] = PL.records[p.id] || {})[tc.id] = [...pluginRows(p.id, tc.id), r];
          input.value = "";
          await put(prop.multiple ? [...cur, r.id] : [r.id]);
          if (prop.multiple) fill(); else closeFloat();
        } }, h("span", { class: "lb-check" }, icon("plus")), t("New {item} “{name}”", { item: L(tc.item), name: q }))] : []),
      ...(cur.length && !q ? [h("button", { type: "button", role: "option", class: "lb-opt dbv-clearopt", onclick: () => put([]).then(closeFloat) },
        h("span", { class: "lb-check" }, icon("x")), t("Clear"))] : []),
      ...(!rows.length && !q ? [h("div", { class: "muted lb-empty" }, t("No {items} yet: type a name to add one.", { items: L(tc.name) }))] : []));
  };
  input.addEventListener("input", fill);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); const first = list.querySelector(".lb-opt"); if (first) first.focus(); }
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); const first = list.querySelector(".lb-opt"); if (first) first.click(); }
  });
  list.addEventListener("keydown", (e) => menuKeys(e, list, ".lb-opt"));
  fill();
  openFloat(anchor, h("div", { class: "menu dbv-pop dbv-free plugin-relpick" }, input, list),
    { minWidth: Math.max(240, anchor.offsetWidth), onClose: () => { if (typeof dbvAfterClose === "function") dbvAfterClose(); paintPluginspace(); }, focus: input });
}

function pluginProps(p, c) {
  const set = (prop) => (row, v) => savePluginField(p.id, c.id, row, prop.key, v).then(() => paintPluginspace());
  return c.properties.map((prop) => {
    const base = { key: prop.key, label: L(prop.label), width: prop.width, hidden: !!prop.hidden, get: (r) => r[prop.key] ?? "" };
    if (prop.type === "title") return { ...base, type: "title" };
    if (prop.type === "relation") {
      const tc = relTarget(p, prop);
      // saved without a repaint: the picker stays open over its cell, and the table is drawn again when it closes
      const rel = { edit: (td, row) => relPicker(td, p, prop, row, (v) => savePluginField(p.id, c.id, row, prop.key, v)),
        render: (r) => h("span", { class: "plugin-rels" }, relIds(r[prop.key]).map((id) => relChip(p, prop, id))) };
      // one link: a select of the other collection's records, so it groups, filters and sorts like one
      if (!prop.multiple) return { ...base, ...rel, type: "select", group: true,
        options: () => pluginRows(p.id, tc.id).map((r) => ({ value: r.id, label: r[titleProp(tc).key] || t("Untitled"), cls: "s-plain" })) };
      return { ...base, ...rel, type: "text", get: (r) => relText(p, prop, r[prop.key]) };
    }
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
  const texts = (r) => c.properties.map((x) => x.type === "select" ? optionLabel(x, r[x.key]) : x.type === "relation" ? relText(p, x, r[x.key]) : r[x.key]);
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
  if (prop.type === "relation") {
    const tc = relTarget(p, prop);
    const chips = relIds(row[prop.key]).map((id) => relChip(p, prop, id)).filter(Boolean);
    const pick = h("button", { type: "button", class: "btn sm ghost plugin-relbtn", "data-key": prop.key, "aria-haspopup": "listbox",
      onclick: (e) => relPicker(e.currentTarget, p, prop, row, (v) => savePluginField(p.id, c.id, row, prop.key, v)) },
    icon(chips.length ? "pen" : "plus"), chips.length ? t("Change") : t("Link a {item}", { item: L(tc.item) }));
    return h("div", { class: "field" }, h("label", null, L(prop.label), hint && h("span", { class: "hint" }, hint)),
      h("div", { class: "plugin-rels plugin-relfield" }, ...chips, pick));
  }
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
    ...pluginBacklinks(tab),
    h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn sm danger-soft", onclick: del }, icon("trash"), t("Delete"))),
  ];
}

// the records of other collections that link to this one, a card per link (a university's programmes, its
// supervisors), each row opening its record, and a button that adds one already linked here
function pluginBacklinks(tab) {
  const { p, c, row } = tab;
  const out = [];
  for (const oc of p.manifest.collections) {
    for (const prop of oc.properties) {
      if (prop.type !== "relation" || prop.to !== c.id) continue;
      const tk = titleProp(oc).key;
      const st = oc.status && oc.properties.find((x) => x.key === oc.status);
      const mine = pluginRows(p.id, oc.id).filter((r) => relIds(r[prop.key]).includes(row.id));
      const add = async () => {
        const name = await modal({ title: t("New {item}", { item: L(oc.item) }), input: "", placeholder: L(titleProp(oc).label), ok: t("Add") });
        if (!name) return;
        let r;
        try { r = await api("POST", `/api/plugins/${p.id}/records/${oc.id}`, { [tk]: name, [prop.key]: prop.multiple ? [row.id] : row.id }); }
        catch (e) { toast(e.message); return; }
        (PL.records[p.id] = PL.records[p.id] || {})[oc.id] = [...pluginRows(p.id, oc.id), r];
        openPluginRecord(p.id, oc.id, r.id);
      };
      const title = oc.properties.filter((x) => x.type === "relation" && x.to === c.id).length > 1 ? `${L(oc.name)} · ${L(prop.label)}` : L(oc.name);
      const sec = card(title, "", mine.length ? h("div", { class: "plugin-linklist" }, mine.map((r) => h("button", { type: "button", class: "plugin-linkrow", "data-rid": r.id,
          onclick: () => openPluginRecord(p.id, oc.id, r.id) },
          h("span", { class: "plugin-linkname" }, r[tk] || t("Untitled")),
          st && r[oc.status] ? dbvChip(pluginChip(st, r[oc.status])) : null,
          ...(oc.dates || []).filter((k) => r[k]).slice(0, 1).map((k) => h("span", { class: "plugin-linkdate" },
            `${L((oc.properties.find((x) => x.key === k) || {}).label)} ${r[k]}`)))))
          : h("p", { class: "muted" }, t("None linked yet.")),
        { cls: "plugin-backlinks", count: mine.length, actions: h("button", { class: "btn sm ghost", onclick: add }, icon("plus"), t("New {item}", { item: L(oc.item) })) });
      sec.dataset.from = `${oc.id}.${prop.key}`;
      out.push(sec);
    }
  }
  return out;
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
    // each plugin: its switch, and a gear that opens its own page of settings (openPluginPage); career-ops first
    const gear = (id, name) => h("button", { type: "button", class: "btn icon ghost plugin-gear", "data-open": id,
      title: t("{name} settings", { name }), "aria-label": t("{name} settings", { name }), onclick: () => openPluginPage(id) }, icon("settings"));
    const co = h("div", { class: "plugin-row", "data-plugin": "career-ops" },
      coSwitch(paint, jobsTool().name, h("span", { class: "hint" }, t("Evaluates job postings, scores how well they fit your resume and keeps a tracker of your applications, in its own interface (a workspace in the top bar). Turning it off keeps its folder, evaluations and tracker.")),
        h("span", { class: "plugin-meta" }, [t("Comes with Resume Studio"), t("Open source (MIT) by santifer")].join(" · "))),
      gear("career-ops", jobsTool().name));
    const rows = [co, ...PL.list.map((p) => {
      if (!p.manifest) return h("div", { class: "plugin-row bad" }, h("span", { class: "fs-status bad" }, icon("alert"), t("{name} doesn't load: {why}", { name: p.id, why: p.error })));
      const m = p.manifest;
      return h("div", { class: "plugin-row", "data-plugin": p.id },
        pluginSwitch(p, paint, L(m.name), h("span", { class: "hint" }, L(m.description)), h("span", { class: "plugin-meta" }, pluginMeta(p))),
        gear(p.id, L(m.name)));
    })];
    box.replaceChildren(
      h("label", null, t("Plugins"), h("span", { class: "hint" }, t("Optional workspaces, each with its own place in the top bar: career-ops, talent programmes, civil service exams, PhDs… Turning one off keeps its records."))),
      h("div", { class: "plugin-list" }, rows),
      h("div", { class: "row wrap" }, h("button", { class: "btn sm", id: "plugin-market-open", onclick: () => openMarket(paint) }, icon("download"), t("Browse the plugin market"))));
  };
  paint();
  box.repaint = paint;   // a plugin's page changed a switch: the list shows it on the way back
  return box;
}

const pluginMeta = (p) => [`v${p.manifest.version}`, p.source === "bundled" ? t("Comes with Resume Studio") : p.manifest.author,
  p.source === "market" && t("From the plugin market")].filter(Boolean).join(" · ");

// a plugin's switch (saved on Done, like the rest of Settings), as a row with its name and `extra` under it
function pluginSwitch(p, repaint, label, ...extra) {
  const on = pendingPlugins[p.id] ?? p.on;
  return h("button", { type: "button", class: "check-row", role: "checkbox", "aria-checked": String(on),
    onclick: () => { pendingPlugins[p.id] = !on; repaint(); } },
  h("span", { class: "check" + (on ? " on" : "") }), h("span", null, label, ...extra));
}
function coSwitch(repaint, label, ...extra) {
  const on = jobsPending.on ?? jobsOn();
  return h("button", { type: "button", class: "check-row", role: "checkbox", "aria-checked": String(on),
    onclick: () => {
      if (on === jobsOn()) jobsPending.on = !on; else delete jobsPending.on;
      repaint(); paintJobsSettings();
      const tb = $("#tracker-settings"); if (tb && tb.repaint) tb.repaint();
    } },
  h("span", { class: "check" + (on ? " on" : "") }), h("span", null, label, ...extra));
}

// ---- a plugin's own page, inside the Settings dialog: the list steps aside, Back brings it back where it was
// (as macOS System Settings and VS Code's extension pages do). What is changed here is saved on Done too.
function openPluginPage(id) {
  const body = $(".modal.settings .settings-body");
  const main = body && body.querySelector(".settings-main");
  if (!main) return;
  const old = body.querySelector(".settings-sub");
  if (old) old.remove();
  const scroll = body.scrollTop;
  const sub = h("div", { class: "settings-sub", "data-plugin": id });
  const back = () => {
    sub.remove();
    main.classList.remove("hidden");
    const list = $("#plugins-settings");
    if (list && list.repaint) list.repaint();
    const tb = $("#tracker-settings"); if (tb && tb.repaint) tb.repaint();
    body.scrollTop = scroll;
    const g = list && list.querySelector(`.plugin-gear[data-open="${CSS.escape(id)}"]`);
    if (g) g.focus();
  };
  const head = () => h("button", { type: "button", class: "btn sm ghost settings-back", onclick: back }, icon("chevLeft"), t("Settings"));
  sub.back = back;
  // replaceChildren writes false and null as text: they are dropped first
  const draw = () => { sub.replaceChildren(head(), ...pluginPage(id, draw).filter(Boolean)); };
  draw();
  main.classList.add("hidden");
  body.append(sub);
  body.scrollTop = 0;
  const first = sub.querySelector(".check-row");
  if (first) first.focus();
}

function pluginPage(id, repaint) {
  if (id === "career-ops") {
    return [
      h("h3", null, jobsTool().name),
      h("p", null, t("Evaluates job postings, scores how well they fit your resume and keeps a tracker of your applications, in its own interface (a workspace in the top bar). Turning it off keeps its folder, evaluations and tracker.")),
      h("div", { class: "field" }, coSwitch(repaint, t("Turn on"), h("span", { class: "hint" }, [t("Comes with Resume Studio"), t("Open source (MIT) by santifer")].join(" · ")))),
      jobsOn() && jobsPending.on !== false ? jobsSettingsSection()
        : h("p", { class: "settings-note", id: "jobs-off-note" }, t("Turn it on and press Done: its version, checks and the CV it reads then show here.")),
    ];
  }
  const p = PL.list.find((x) => x.id === id);
  if (!p || !p.manifest) return [h("p", { class: "muted" }, t("This plugin is no longer installed."))];
  const m = p.manifest;
  const recs = PL.records[p.id] || {};
  return [
    h("h3", null, L(m.name)),
    h("p", null, L(m.description)),
    h("div", { class: "field" }, pluginSwitch(p, repaint, t("Turn on"), h("span", { class: "hint" }, t("A workspace of its own in the top bar. Turning it off keeps its records.")))),
    h("div", { class: "field" }, h("label", null, t("Its tables"), h("span", { class: "hint" }, t("Kept in your data folder, with the rest of your data"))),
      h("div", { class: "plugin-tables" }, m.collections.map((c) => h("div", { class: "plugin-table", "data-collection": c.id },
        h("span", { class: "plugin-table-name" }, L(c.name)),
        // an off plugin's records aren't read (they stay in the data folder): no count rather than a false 0
        p.on && h("span", { class: "plugin-table-n" }, t("{n} record", { n: (recs[c.id] || []).length }, "{n} records")))))),
    h("div", { class: "field" }, h("label", null, t("About this plugin")),
      h("p", { class: "plugin-meta" }, pluginMeta(p)),
      m.homepage && h("a", { href: m.homepage, "data-ext": "", class: "c-link plugin-home" }, siteName(m.homepage))),
    p.source === "market" && h("div", { class: "row" }, h("button", { class: "btn sm danger-soft", onclick: () => uninstallPlugin(p, () => { const s = $(".settings-sub"); if (s && s.back) s.back(); }) },
      icon("trash"), t("Uninstall"))),
  ];
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
