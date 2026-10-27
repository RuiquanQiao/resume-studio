// A table that works like a Notion database (docs/JOB_HUNT.md, 2026-10-19): one component for the Applying
// workspace's three tables. A table is described by its properties, each with a type:
//   title     the row's name: clicking it opens the row's page
//   text      typed in place (a box over the cell: Enter or clicking away saves, Esc cancels)
//   select    one option, picked from a list under the cell (status, how it goes in…)
//   date      a date field under the cell
//   relation  chips that open other rows (read-only here)
//   custom    drawn by the table, read-only: clicking it opens the row
// {key, label, type, get(row) -> value, render(row)?, options()? [{value, label, cls}], set(row, value)?,
//  width?, hidden?, group? (can group by it), free? (a select whose options are the user's own: typing makes one),
//  edit(td, row)? (its own editor over the cell)}
// Each table has several views (tabs), each with its own layout (table | board | calendar | gantt), columns
// (order, width, hidden, wrapped), filters, sorts and group. They are saved with the data (.studio/hunt/views.json).
// A table whose rows have dates (o.dated(row) -> {start, end, cls, items: [{at, label, sub, kind}]}) can also be
// a calendar (the items, by day) or a Gantt chart (a bar per row from start to end, its items on it). One whose
// rows have places (o.located) can be a world map.
// Loaded after ui.js and before hunt.js: only definitions.
"use strict";

const DBV = {
  tables: null,        // GET /api/hunt/views: {table: {active, views: [...]}}
  timers: {},          // table -> pending save
  scrollX: {},         // "table:view" -> how far its table is scrolled sideways, kept over repaints
  pending: false,      // a repaint waits for an editor to close
  drag: null,          // {table, key} while a column heading is dragged
  saved: 0,            // when this window last saved: the "views" event that follows is its own echo
  month: {},           // table -> "YYYY-MM" the calendar shows (this window's, not saved)
};
const LAYOUT_ICONS = { table: "table", board: "columns", calendar: "calendar", gantt: "gantt", map: "map" };
const LAYOUT_NAMES = { table: "Table", board: "Board", calendar: "Calendar", gantt: "Gantt", map: "Map" };
const LAYOUT_AS = { table: "Show as a table", board: "Show as a board", calendar: "Show as a calendar", gantt: "Show as a Gantt chart", map: "Show on a world map" };
const dbvLayouts = (o) => ["table", "board", ...(o.dated ? ["calendar", "gantt"] : []), ...(o.located ? ["map"] : [])];

const FILTER_OPS = {
  is: "is", is_not: "is not", contains: "contains", not_contains: "does not contain", before: "is before",
  after: "is after", empty: "is empty", not_empty: "is not empty",
};
const OPS_FOR = {
  title: ["contains", "not_contains", "empty", "not_empty"], text: ["contains", "not_contains", "empty", "not_empty"],
  custom: ["contains", "not_contains", "empty", "not_empty"], relation: ["contains", "not_contains", "empty", "not_empty"],
  select: ["is", "is_not", "empty", "not_empty"], date: ["is", "before", "after", "empty", "not_empty"],
};
const TYPE_ICONS = { title: "text", text: "text", select: "option", date: "calendar", relation: "link", custom: "list" };

async function loadViews() {
  try { DBV.tables = await api("GET", "/api/hunt/views"); } catch (e) { DBV.tables = DBV.tables || {}; }
}

// an editor or a view panel is open: a repaint would close it, so it waits
const dbvBusy = () => !!(Float.cur && Float.cur.panel.classList.contains("dbv-pop"));
function dbvAfterClose() { if (DBV.pending) { DBV.pending = false; if (typeof paintFill === "function" && W.ws === "fill") paintFill(); } }

// ---------------------------------------------------------------- views: reading and saving
function dbvTable(table, defaults) {
  DBV.tables = DBV.tables || {};
  if (!DBV.tables[table] || !(DBV.tables[table].views || []).length) {
    DBV.tables[table] = { active: defaults[0].id, views: defaults.map((v) => ({ columns: [], filters: [], sorts: [], group: "", folded: [], layout: "table", ...v })) };
  }
  return DBV.tables[table];
}
const dbvView = (tb) => tb.views.find((v) => v.id === tb.active) || tb.views[0];

function dbvSave(table, repaint) {
  clearTimeout(DBV.timers[table]);
  DBV.timers[table] = setTimeout(async () => {
    delete DBV.timers[table];
    DBV.saved = Date.now();
    try { await api("PUT", "/api/hunt/views/" + table, DBV.tables[table]); } catch (e) { toast(e.message); }
    DBV.saved = Date.now();
  }, 250);
  if (repaint) repaint();
}

// the view's columns: its own order first, then properties it doesn't know yet
function dbvColumns(view, props) {
  const byKey = Object.fromEntries(props.map((p) => [p.key, p]));
  const seen = new Set();
  const cols = [];
  for (const c of view.columns || []) {
    if (!byKey[c.key] || seen.has(c.key)) continue;
    seen.add(c.key);
    cols.push({ ...c, prop: byKey[c.key] });
  }
  // long text wraps until the user says otherwise; a chip or a date never needs to
  for (const p of props) if (!seen.has(p.key)) cols.push({ key: p.key, width: null, hidden: !!p.hidden, wrap: p.wrap ?? !["select", "date"].includes(p.type), prop: p });
  // the title always shows, first
  const ti = cols.findIndex((c) => c.prop.type === "title");
  if (ti > 0) cols.unshift(cols.splice(ti, 1)[0]);
  if (ti >= 0) cols[0].hidden = false;
  return cols;
}
function dbvSetColumns(view, cols) { view.columns = cols.map(({ key, width, hidden, wrap }) => ({ key, width, hidden, wrap })); }

// ---------------------------------------------------------------- values: text, filters, sorts, groups
function dbvText(p, row) {
  const v = p.get(row);
  if (p.type === "select") { const o = (p.options() || []).find((x) => x.value === v); return o ? o.label : ""; }
  return Array.isArray(v) ? v.join(" ") : String(v ?? "");
}

function dbvPass(f, p, row) {
  const v = p.get(row);
  const empty = v == null || v === "" || (Array.isArray(v) && !v.length);
  if (f.op === "empty") return empty;
  if (f.op === "not_empty") return !empty;
  if (p.type === "select") {
    const vals = Array.isArray(f.value) ? f.value : [f.value].filter(Boolean);
    if (!vals.length) return true;
    return f.op === "is_not" ? !vals.includes(v) : vals.includes(v);
  }
  if (p.type === "date") {
    if (!f.value) return true;
    const d = String(v || "").slice(0, 10);
    if (!d) return false;
    return f.op === "before" ? d < f.value : f.op === "after" ? d > f.value : d === f.value;
  }
  const q = String(f.value || "").trim().toLowerCase();
  if (!q) return true;
  const has = dbvText(p, row).toLowerCase().includes(q);
  return f.op === "not_contains" ? !has : has;
}

function dbvSortKey(p, row) {
  const v = p.get(row);
  if (p.type === "select") { const i = (p.options() || []).findIndex((x) => x.value === v); return i < 0 ? null : i; }
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return null;
  return Array.isArray(v) ? v.join(" ").toLowerCase() : typeof v === "number" ? v : String(v).toLowerCase();
}

function dbvRows(view, props, rows) {
  const byKey = Object.fromEntries(props.map((p) => [p.key, p]));
  let out = rows.filter((r) => (view.filters || []).every((f) => !byKey[f.key] || dbvPass(f, byKey[f.key], r)));
  const sorts = (view.sorts || []).filter((s) => byKey[s.key]);
  if (sorts.length) {
    const idx = new Map(out.map((r, i) => [r, i]));
    out = [...out].sort((a, b) => {
      for (const s of sorts) {
        const x = dbvSortKey(byKey[s.key], a), y = dbvSortKey(byKey[s.key], b);
        if (x === y) continue;
        if (x == null) return 1;           // empty last, either way
        if (y == null) return -1;
        const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), I18N.lang);
        if (c) return s.dir === "desc" ? -c : c;
      }
      return idx.get(a) - idx.get(b);
    });
  }
  return out;
}

// [{value, label, cls, rows}], in the option order (select) or by first appearance; empty last
function dbvGroups(p, rows) {
  const groups = new Map();
  if (p.type === "select") for (const o of p.options() || []) groups.set(o.value, { value: o.value, label: o.label, cls: o.cls, rows: [] });
  for (const r of rows) {
    const v = p.get(r);
    const k = v == null || v === "" ? "" : String(v);
    if (!groups.has(k)) groups.set(k, { value: k, label: k || t("No {name}", { name: p.label }), rows: [] });
    groups.get(k).rows.push(r);
  }
  const all = [...groups.values()];
  return [...all.filter((g) => g.value !== ""), ...all.filter((g) => g.value === "")];
}

// ---------------------------------------------------------------- cells
function dbvChip(o) {
  return h("span", { class: "state-chip " + (o.cls || "s-plain") }, o.label);
}

function dbvShow(p, row) {
  if (p.render) return p.render(row);
  const v = p.get(row);
  if (p.type === "select") { const o = (p.options() || []).find((x) => x.value === v); return o ? dbvChip(o) : null; }
  return v == null ? "" : String(v);
}

function dbvCell(p, row, col, ctx) {
  const td = h("td", { class: "dbv-td t-" + p.type + (col.wrap ? " wrap" : "") + (p.set || p.edit ? " editable" : ""), "data-key": p.key });
  if (p.type === "title") {
    td.append(h("button", { type: "button", class: "dbv-title", onclick: (e) => { e.stopPropagation(); ctx.open(row); } }, dbvShow(p, row) || t("Untitled")));
    return td;
  }
  const inner = h("div", { class: "dbv-in" }, dbvShow(p, row));
  td.append(inner);
  td.addEventListener("click", (e) => {
    if (e.target.closest("button, a, input")) return;
    e.stopPropagation();
    if (p.edit) { p.edit(td, row); return; }   // a property with its own editor (a role's places)
    if (!p.set) { ctx.open(row); return; }
    dbvEdit(td, p, row);
  });
  return td;
}

// edit a cell where it is
function dbvEdit(td, p, row) {
  const done = async (val) => {
    if (val === undefined) return;
    try { await p.set(row, val); } catch (e) { toast(e.message); }
  };
  if (p.type === "select") {
    const cur = p.get(row);
    const list = h("div", { class: "listbox dbv-pop dbv-options", role: "listbox", "aria-label": p.label });
    const opt = (o) => {
      const on = o.value === cur;
      return h("button", { type: "button", role: "option", class: "lb-opt" + (on ? " on" : ""), "aria-selected": String(on), "data-value": o.value,
        onclick: () => { closeFloat(); if (!on) done(o.value); } },
      h("span", { class: "lb-check" }, on ? icon("check") : null), dbvChip(o));
    };
    list.addEventListener("keydown", (e) => menuKeys(e, list, ".lb-opt"));
    if (!p.free) {
      list.append(...(p.options() || []).map(opt));
      openFloat(td, list, { minWidth: Math.max(180, td.offsetWidth), onClose: dbvAfterClose, focus: list.querySelector(".lb-opt.on") || list.querySelector(".lb-opt") });
      return;
    }
    // the user's own options (p.free): typing finds one, or makes a new one
    const input = h("input", { type: "text", class: "dbv-optinput", spellcheck: "false", "aria-label": p.label, placeholder: t("Find or type a new one") });
    const fill = () => {
      const q = input.value.trim();
      const shown = (p.options() || []).filter((o) => !q || o.label.toLowerCase().includes(q.toLowerCase()));
      list.replaceChildren(...shown.map(opt),
        ...(q && !shown.some((o) => o.value === q) ? [h("button", { type: "button", role: "option", class: "lb-opt dbv-newopt", "data-value": q,
          onclick: () => { closeFloat(); done(q); } }, h("span", { class: "lb-check" }, icon("plus")), t("Add “{name}”", { name: q }))] : []),
        ...(cur && !q ? [h("button", { type: "button", role: "option", class: "lb-opt dbv-clearopt", onclick: () => { closeFloat(); done(""); } },
          h("span", { class: "lb-check" }, icon("x")), t("Clear"))] : []));
    };
    input.addEventListener("input", fill);
    input.addEventListener("keydown", (e) => {
      // Enter: what was typed, as it is (an option of that name, or a new one)
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); const q = input.value.trim(); closeFloat(); if (q && q !== cur) done(q); }
      if (e.key === "ArrowDown") { e.preventDefault(); const first = list.querySelector(".lb-opt"); if (first) first.focus(); }
    });
    fill();
    openFloat(td, h("div", { class: "menu dbv-pop dbv-free" }, input, list), { minWidth: Math.max(220, td.offsetWidth), onClose: dbvAfterClose, focus: input });
    return;
  }
  if (p.type === "date") {
    const input = h("input", { type: "date", class: "dbv-date", value: String(p.get(row) || "").slice(0, 10), "aria-label": p.label });
    const panel = h("div", { class: "menu dbv-pop dbv-datepanel" }, input,
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { type: "button", class: "btn sm ghost", onclick: () => { closeFloat(); done(""); } }, t("Clear"))));
    input.addEventListener("change", () => { const v = input.value; if (v && v !== String(p.get(row) || "").slice(0, 10)) done(v); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); closeFloat(); } });
    openFloat(td, panel, { onClose: dbvAfterClose, focus: input });
    return;
  }
  // text: a box over the cell
  const before = String(p.get(row) ?? "");
  let cancelled = false;
  const input = h("textarea", { class: "dbv-textbox", rows: 1, spellcheck: "false", "aria-label": p.label }, before);
  input.value = before;
  const panel = h("div", { class: "dbv-pop dbv-textpanel" }, input);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); closeFloat(); }
  });
  input.addEventListener("input", () => autosize(input));
  DBV.cancel = () => { cancelled = true; };
  openFloat(td, panel, { place: "over", minWidth: Math.max(td.offsetWidth, 220), focus: input,
    onClose: () => { DBV.cancel = null; const v = input.value.trim(); if (!cancelled && v !== before.trim()) done(v); dbvAfterClose(); } });
  autosize(input);
  input.setSelectionRange(input.value.length, input.value.length);
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
// Esc in a text box cancels (the floating panel's own Esc only closes it)
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && DBV.cancel) DBV.cancel(); }, true);

// a choice inside a floating panel: only one float is open at a time, so its options unfold in the panel itself
function inlinePick(options, value, onChange, { cls = "", label = "" } = {}) {
  const cur = () => (options.find((x) => x.value === value) || {}).label || t("Choose…");
  const lab = h("span", { class: "sel-label" }, cur());
  const btn = h("button", { type: "button", class: "select sm " + cls, "aria-label": label, "aria-expanded": "false", "data-value": value }, lab, icon("chev"));
  const list = h("div", { class: "dbv-inlist", role: "listbox", "aria-label": label, hidden: true });
  const wrap = h("div", { class: "dbv-pick" }, btn, list);
  const fill = () => list.replaceChildren(...options.map((x) => h("button", { type: "button", role: "option", class: "lb-opt" + (x.value === value ? " on" : ""),
    "aria-selected": String(x.value === value), "data-value": x.value,
    onclick: () => { list.hidden = true; btn.setAttribute("aria-expanded", "false"); if (x.value === value) return;
      value = x.value; btn.dataset.value = value; lab.textContent = cur(); onChange(value); } },
    h("span", { class: "lb-check" }, x.value === value ? icon("check") : null), h("span", { class: "lb-label" }, x.label))));
  btn.addEventListener("click", () => { fill(); list.hidden = !list.hidden; btn.setAttribute("aria-expanded", String(!list.hidden)); });
  list.addEventListener("keydown", (e) => menuKeys(e, list, ".lb-opt"));
  return wrap;
}

// ---------------------------------------------------------------- the view's toolbar
function dbvTabs(o, tb, repaint) {
  const view = dbvView(tb);
  const tabs = tb.views.map((v) => {
    const on = v.id === view.id;
    return h("button", { type: "button", class: "dbv-tab" + (on ? " on" : ""), role: "tab", "aria-selected": String(on), "data-view": v.id,
      onclick: (e) => {
        if (!on) { tb.active = v.id; dbvSave(o.table, repaint); return; }
        dbvViewMenu(e.currentTarget, o, tb, v, repaint);
      } }, icon(LAYOUT_ICONS[v.layout] || "table"), h("span", null, v.name), on && icon("chev"));
  });
  const add = h("button", { type: "button", class: "btn icon sm ghost dbv-addview", title: t("Add a view"), "aria-label": t("Add a view"),
    onclick: (e) => openMenu(e.currentTarget, dbvLayouts(o).map((l) => ({ label: t(LAYOUT_NAMES[l]), icon: LAYOUT_ICONS[l],
      onClick: () => dbvAddView(o, tb, l, repaint) }))) }, icon("plus"));
  return h("div", { class: "dbv-tabs", role: "tablist", "aria-label": t("Views") }, tabs, add);
}

function dbvNewId(tb) { let n = tb.views.length + 1; while (tb.views.some((v) => v.id === "v" + n)) n++; return "v" + n; }

function dbvAddView(o, tb, layout, repaint) {
  const groupBy = layout === "board" ? (o.props.find((p) => p.type === "select") || {}).key || "" : "";
  const v = { id: dbvNewId(tb), name: t(LAYOUT_NAMES[layout]), layout, columns: [], filters: [], sorts: [], group: groupBy, folded: [] };
  tb.views.push(v);
  tb.active = v.id;
  dbvSave(o.table, repaint);
}

function dbvViewMenu(anchor, o, tb, v, repaint) {
  openMenu(anchor, [
    { label: t("Rename"), icon: "pen", onClick: async () => {
      const f = await formModal({ title: t("Rename the view"), ok: t("Save"), fields: [{ key: "name", label: t("Name"), value: v.name, required: true }] });
      if (f) { v.name = f.name; dbvSave(o.table, repaint); }
    } },
    ...dbvLayouts(o).filter((l) => l !== v.layout).map((l) => ({ label: t(LAYOUT_AS[l]), icon: LAYOUT_ICONS[l], onClick: () => {
      v.layout = l;
      if (l === "board" && !dbvBoardProp(o.props, v)) v.group = (o.props.find((p) => p.type === "select") || {}).key || "";
      dbvSave(o.table, repaint);
    } })),
    { label: t("Duplicate"), icon: "copy", onClick: () => {
      const c = JSON.parse(JSON.stringify(v));
      c.id = dbvNewId(tb); c.name = t("{name} copy", { name: v.name });
      tb.views.splice(tb.views.indexOf(v) + 1, 0, c);
      tb.active = c.id;
      dbvSave(o.table, repaint);
    } },
    "sep",
    { label: t("Delete this view"), icon: "trash", danger: true, disabled: tb.views.length < 2, onClick: () => {
      tb.views = tb.views.filter((x) => x !== v);
      tb.active = tb.views[0].id;
      dbvSave(o.table, repaint);
    } },
  ], { minWidth: 210 });
}

const dbvBoardProp = (props, v) => props.find((p) => p.key === v.group && p.type === "select");

function dbvTools(o, tb, view, cols, repaint) {
  const byKey = Object.fromEntries(o.props.map((p) => [p.key, p]));
  const n = (k) => (view[k] || []).length;
  const btn = (id, ic, label, count, onClick) => h("button", { type: "button", class: "btn sm ghost dbv-tool" + (count ? " on" : ""), id: `dbv-${o.table}-${id}`, onclick: onClick },
    icon(ic), label, count ? h("span", { class: "n" }, count) : null);
  const groupP = byKey[view.group];
  const dated = ["calendar", "gantt", "map"].includes(view.layout);   // rows by date or by place: no columns, no groups
  return h("div", { class: "dbv-tools" },
    btn("filter", "filter", t("Filter"), n("filters"), (e) => dbvFilterAdd(e.currentTarget, o, view, repaint)),
    !["calendar", "map"].includes(view.layout) && btn("sort", "sort", t("Sort"), n("sorts"), (e) => dbvSortPanel(e.currentTarget, o, view, repaint)),
    !dated && btn("group", "layers", groupP ? t("Group: {name}", { name: groupP.label }) : t("Group"), 0, (e) => dbvGroupMenu(e.currentTarget, o, view, repaint)),
    !dated && btn("props", "eye", t("Properties"), 0, (e) => dbvPropsPanel(e.currentTarget, o, view, repaint)));
}

// the filters as chips above the table; each opens to be changed or removed
function dbvFilterChips(o, view, repaint) {
  const byKey = Object.fromEntries(o.props.map((p) => [p.key, p]));
  const fs = (view.filters || []).filter((f) => byKey[f.key]);
  if (!fs.length) return null;
  return h("div", { class: "dbv-chips" }, fs.map((f) => {
    const p = byKey[f.key];
    let what = t(FILTER_OPS[f.op]);
    if (!["empty", "not_empty"].includes(f.op)) {
      if (p.type === "select") {
        const labels = (Array.isArray(f.value) ? f.value : [f.value]).map((v) => ((p.options() || []).find((x) => x.value === v) || {}).label).filter(Boolean);
        what += labels.length ? " " + labels.join(", ") : "";
      } else if (f.value) what += " " + f.value;
    }
    return h("button", { type: "button", class: "dbv-chip", "data-key": f.key, onclick: (e) => dbvFilterPanel(e.currentTarget, o, view, f, repaint) },
      h("b", null, p.label), " ", what, icon("chev"));
  }));
}

function dbvFilterAdd(anchor, o, view, repaint) {
  openMenu(anchor, [{ header: t("Filter by") }, ...o.props.map((p) => ({ label: p.label, icon: TYPE_ICONS[p.type], onClick: () => {
    const f = { key: p.key, op: OPS_FOR[p.type][0], value: p.type === "select" ? [] : "" };
    view.filters = [...(view.filters || []), f];
    dbvSave(o.table, repaint);
    requestAnimationFrame(() => { const chip = [...document.querySelectorAll(`.dbv-chip[data-key="${CSS.escape(p.key)}"]`)].pop(); if (chip) dbvFilterPanel(chip, o, view, f, repaint); });
  } }))], { minWidth: 200 });
}

function dbvFilterPanel(anchor, o, view, f, repaint) {
  const p = o.props.find((x) => x.key === f.key);
  const ops = OPS_FOR[p.type];
  const op = inlinePick(ops.map((x) => ({ value: x, label: t(FILTER_OPS[x]) })), f.op, (v) => { f.op = v; dbvSave(o.table, null); DBV.pending = true; paintVal(); }, { cls: "dbv-op", label: t("Condition") });
  const body = h("div", { class: "dbv-fval" });
  const paintVal = () => {
    body.replaceChildren();
    if (["empty", "not_empty"].includes(f.op)) return;
    if (p.type === "select") {
      const vals = new Set(Array.isArray(f.value) ? f.value : []);
      for (const x of p.options() || []) {
        body.append(h("label", { class: "dbv-check" }, h("input", { type: "checkbox", checked: vals.has(x.value), onchange: (e) => {
          if (e.target.checked) vals.add(x.value); else vals.delete(x.value);
          f.value = [...vals]; dbvSave(o.table, null); DBV.pending = true;
        } }), dbvChip(x)));
      }
    } else {
      const input = h("input", { type: p.type === "date" ? "date" : "text", value: f.value || "", class: "dbv-fin", spellcheck: "false",
        placeholder: t("Value"), "aria-label": t("Value"),
        oninput: (e) => { f.value = e.target.value; dbvSave(o.table, null); DBV.pending = true; } });
      body.append(input);
      requestAnimationFrame(() => input.focus());
    }
  };
  paintVal();
  const panel = h("div", { class: "menu dbv-pop dbv-filterpanel" },
    h("div", { class: "row" }, h("b", null, p.label), op, h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Remove this filter"), "aria-label": t("Remove this filter"),
        onclick: () => { view.filters = view.filters.filter((x) => x !== f); closeFloat(); dbvSave(o.table, repaint); } }, icon("trash"))),
    body);
  openFloat(anchor, panel, { minWidth: 260, onClose: () => { DBV.pending = true; dbvAfterClose(); } });
}

function dbvSortPanel(anchor, o, view, repaint) {
  const panel = h("div", { class: "menu dbv-pop dbv-sortpanel" });
  const paint = () => {
    panel.replaceChildren();
    const sorts = view.sorts || [];
    if (!sorts.length) panel.append(h("p", { class: "muted dbv-p" }, t("No sort: rows keep their own order.")));
    sorts.forEach((s, i) => {
      panel.append(h("div", { class: "row dbv-sortrow" },
        inlinePick(o.props.map((p) => ({ value: p.key, label: p.label })), s.key, (v) => { s.key = v; dbvSave(o.table, null); DBV.pending = true; paint(); },
          { cls: "dbv-sortkey", label: t("Property") }),
        uiSeg([{ value: "asc", label: t("Ascending") }, { value: "desc", label: t("Descending") }], s.dir === "desc" ? "desc" : "asc",
          (v) => { s.dir = v; dbvSave(o.table, null); DBV.pending = true; paint(); }, { cls: "sm", label: t("Direction") }),
        h("button", { type: "button", class: "btn icon sm ghost", title: t("Remove this sort"), "aria-label": t("Remove this sort"),
          onclick: () => { view.sorts = sorts.filter((_, j) => j !== i); dbvSave(o.table, null); DBV.pending = true; paint(); } }, icon("x"))));
    });
    panel.append(h("div", { class: "row" }, h("button", { type: "button", class: "btn sm ghost", onclick: () => {
      const used = new Set(sorts.map((s) => s.key));
      const p = o.props.find((x) => !used.has(x.key)) || o.props[0];
      view.sorts = [...sorts, { key: p.key, dir: "asc" }]; dbvSave(o.table, null); DBV.pending = true; paint();
    } }, icon("plus"), t("Add a sort"))));
  };
  paint();
  openFloat(anchor, panel, { minWidth: 300, onClose: () => { DBV.pending = true; dbvAfterClose(); } });
}

function dbvGroupMenu(anchor, o, view, repaint) {
  const can = o.props.filter((p) => p.group || p.type === "select");
  openMenu(anchor, [{ header: t("Group by") },
    ...can.map((p) => ({ label: p.label, icon: view.group === p.key ? "check" : TYPE_ICONS[p.type], onClick: () => { view.group = p.key; view.folded = []; dbvSave(o.table, repaint); } })),
    view.layout !== "board" && "sep",
    view.layout !== "board" && { label: t("No grouping"), icon: view.group ? "x" : "check", onClick: () => { view.group = ""; dbvSave(o.table, repaint); } }], { minWidth: 200 });
}

function dbvPropsPanel(anchor, o, view, repaint) {
  const panel = h("div", { class: "menu dbv-pop dbv-propspanel" });
  const paint = () => {
    const cols = dbvColumns(view, o.props);
    panel.replaceChildren(h("div", { class: "menu-head" }, t("Properties in this view")));
    const list = h("div", { class: "dbv-proplist" });
    cols.forEach((c) => {
      const title = c.prop.type === "title";
      list.append(h("div", { class: "dbv-proprow" + (title ? "" : " sort-row"), "data-key": c.key },
        title ? h("span", { class: "i-pad" }) : dragHandle((from, to) => {
          const movable = cols.slice(1);
          const [m] = movable.splice(from, 1);
          movable.splice(to, 0, m);
          dbvSetColumns(view, [cols[0], ...movable]);
          dbvSave(o.table, null); DBV.pending = true; paint();
        }),
        icon(TYPE_ICONS[c.prop.type]), h("span", { class: "mi-label" }, c.prop.label), h("span", { class: "spacer" }),
        h("button", { type: "button", class: "btn icon sm ghost", disabled: title, "aria-pressed": String(!c.hidden),
          title: c.hidden ? t("Show this column") : t("Hide this column"), "aria-label": (c.hidden ? t("Show this column") : t("Hide this column")) + " " + c.prop.label,
          onclick: () => { c.hidden = !c.hidden; dbvSetColumns(view, cols); dbvSave(o.table, null); DBV.pending = true; paint(); } },
        icon(c.hidden ? "eyeOff" : "eye"))));
    });
    panel.append(list);
  };
  paint();
  openFloat(anchor, panel, { minWidth: 260, align: "end", onClose: () => { DBV.pending = true; dbvAfterClose(); } });
}

// a heading's own menu, as in Notion
function dbvHeadMenu(th, o, view, col, repaint) {
  const p = col.prop;
  const sortBy = (dir) => { view.sorts = [{ key: p.key, dir }, ...(view.sorts || []).filter((s) => s.key !== p.key)]; dbvSave(o.table, repaint); };
  const setCol = (fn) => { const cols = dbvColumns(view, o.props); fn(cols.find((c) => c.key === p.key)); dbvSetColumns(view, cols); dbvSave(o.table, repaint); };
  openMenu(th, [
    { label: t("Sort ascending"), icon: "sortUp", onClick: () => sortBy("asc") },
    { label: t("Sort descending"), icon: "sortDown", onClick: () => sortBy("desc") },
    { label: t("Filter"), icon: "filter", onClick: () => {
      const f = { key: p.key, op: OPS_FOR[p.type][0], value: p.type === "select" ? [] : "" };
      view.filters = [...(view.filters || []), f];
      dbvSave(o.table, repaint);
      requestAnimationFrame(() => { const chip = [...document.querySelectorAll(`.dbv-chip[data-key="${CSS.escape(p.key)}"]`)].pop(); if (chip) dbvFilterPanel(chip, o, view, f, repaint); });
    } },
    (p.group || p.type === "select") && { label: view.group === p.key ? t("Stop grouping") : t("Group by this"), icon: "layers",
      onClick: () => { view.group = view.group === p.key ? "" : p.key; view.folded = []; dbvSave(o.table, repaint); } },
    "sep",
    { label: col.wrap ? t("Don't wrap text") : t("Wrap text"), icon: "wrap", onClick: () => setCol((c) => { c.wrap = !c.wrap; }) },
    // frozen columns stay on screen while the table runs sideways; this column and those before it
    (() => {
      const at = dbvColumns(view, o.props).filter((c) => !c.hidden).findIndex((c) => c.key === p.key) + 1;
      return dbvFrozen(view) === at ? { label: t("Unfreeze columns"), icon: "columns", onClick: () => { view.freeze = 0; dbvSave(o.table, repaint); } }
        : { label: at === 1 ? t("Freeze this column") : t("Freeze up to this column"), icon: "columns", onClick: () => { view.freeze = at; dbvSave(o.table, repaint); } };
    })(),
    p.type !== "title" && { label: t("Hide this column"), icon: "eyeOff", onClick: () => setCol((c) => { c.hidden = true; }) },
  ], { minWidth: 200 });
}

// ---------------------------------------------------------------- the table layout
const DEF_WIDTH = { title: 220, text: 180, select: 150, date: 130, relation: 220, custom: 170 };
const MIN_WIDTH = { title: 160, text: 110, select: 90, date: 106, relation: 100, custom: 110 };   // wrapped text stays over 6em wide

// the width a table has to fit: the page's content box (measured on the page as it is, before the repaint)
function dbvPane() {
  const el = document.querySelector("#fillspace .hunt-wrap");
  if (!el) { DBV.refit = true; return 0; }   // the first paint: measured right after it
  const s = getComputedStyle(el);
  const w = Math.floor(el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight)) - 2;   // the frame's border
  if (w <= 0) DBV.refit = true;   // drawn while its workspace is hidden: measured once it shows
  return w;
}
let dbvLastPane = 0;
function dbvRefit(tries = 0) {
  DBV.refit = false;
  const w = dbvPane();
  if (w <= 0) { DBV.refit = false; if (tries < 60) setTimeout(() => dbvRefit(tries + 1), 100); return; }   // still hidden: look again shortly
  if (w !== dbvLastPane && document.querySelector(".dbv-wrap") && typeof paintHunt === "function" && !dbvBusy()) { dbvLastPane = w; paintHunt(); }
}
window.addEventListener("resize", () => { clearTimeout(DBV.resizeTimer); DBV.resizeTimer = setTimeout(dbvRefit, 120); });

// the widths that fit `avail` pixels: each column scaled from its own width, none below its type's minimum
function dbvFit(cols, width, avail) {
  const out = new Map();
  let free = cols.slice();
  for (let guard = 0; guard < cols.length + 1; guard++) {
    const left = avail - [...out.values()].reduce((s, w) => s + w, 0);
    const share = free.reduce((s, c) => s + width(c), 0);
    const scale = share ? left / share : 1;
    const small = free.filter((c) => width(c) * scale < (c.prop.min || MIN_WIDTH[c.prop.type] || 90));
    if (!small.length) { for (const c of free) out.set(c.key, Math.floor(width(c) * scale)); break; }
    for (const c of small) out.set(c.key, c.prop.min || MIN_WIDTH[c.prop.type] || 90);
    free = free.filter((c) => !small.includes(c));
    if (!free.length) break;
  }
  return out;
}

// how many columns from the left stay put while the table scrolls sideways: the title alone until the user picks
const dbvFrozen = (view) => (Number.isInteger(view.freeze) ? view.freeze : 1);

function dbvTableEl(o, view, cols, rows, repaint) {
  const shown = cols.filter((c) => !c.hidden);
  const width = (c) => c.width || c.prop.width || DEF_WIDTH[c.prop.type];
  // until the user sizes a column, the table fits its pane (each column scaled from its own width, none below a
  // readable minimum); once one is sized, every width is the view's own and the table may run wider, as in Notion
  const sized = shown.some((c) => c.width);
  const pane = dbvPane();
  const fit = sized || pane <= 0 ? null : dbvFit(shown, width, pane);
  if (pane > 0) dbvLastPane = pane;
  const px = (c) => (fit ? fit.get(c.key) : width(c));
  const colgroup = () => h("colgroup", null, shown.map((c) => h("col", { "data-key": c.key, style: `width:${px(c)}px` })), sized && h("col", { class: "dbv-padcol" }));
  // the heading row and the rows are two tables with the same columns: the heading row's box sticks to the top of the
  // page while it scrolls, the rows' box runs sideways, and the two (and the bar at the window's foot) move together
  let tables = [];
  const ths = shown.map((c) => {
    const th = h("th", { class: "dbv-th t-" + c.prop.type, "data-key": c.key, draggable: c.prop.type !== "title" ? "true" : null, scope: "col" },
      h("button", { type: "button", class: "dbv-thbtn", onclick: (e) => dbvHeadMenu(e.currentTarget.closest("th"), o, view, c, repaint) },
        icon(TYPE_ICONS[c.prop.type]), h("span", { class: "dbv-thlabel" }, c.prop.label),
        (view.sorts || []).some((s) => s.key === c.key) && icon((view.sorts.find((s) => s.key === c.key).dir === "desc") ? "sortDown" : "sortUp")),
      h("span", { class: "dbv-resize", "aria-hidden": "true" }));
    // drag a heading to move the column
    th.addEventListener("dragstart", (e) => {
      if (DBV.resizing) { e.preventDefault(); return; }   // the edge was grabbed: that resizes, it doesn't move
      DBV.drag = { table: o.table, key: c.key };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", c.key);
      th.classList.add("dragging");
    });
    th.addEventListener("dragend", () => { DBV.drag = null; th.classList.remove("dragging"); document.querySelectorAll(".dbv-th.drop-l, .dbv-th.drop-r").forEach((x) => x.classList.remove("drop-l", "drop-r")); });
    th.addEventListener("dragover", (e) => {
      if (!DBV.drag || DBV.drag.table !== o.table || DBV.drag.key === c.key || c.prop.type === "title") return;
      e.preventDefault();
      const r = th.getBoundingClientRect(), right = e.clientX > r.left + r.width / 2;
      th.classList.toggle("drop-r", right); th.classList.toggle("drop-l", !right);
    });
    th.addEventListener("dragleave", () => th.classList.remove("drop-l", "drop-r"));
    th.addEventListener("drop", (e) => {
      if (!DBV.drag || DBV.drag.table !== o.table) return;
      e.preventDefault();
      const right = th.classList.contains("drop-r");
      th.classList.remove("drop-l", "drop-r");
      const all = dbvColumns(view, o.props);
      const from = all.findIndex((x) => x.key === DBV.drag.key);
      const [m] = all.splice(from, 1);
      let to = all.findIndex((x) => x.key === c.key) + (right ? 1 : 0);
      all.splice(Math.max(1, to), 0, m);
      dbvSetColumns(view, all);
      DBV.drag = null;
      dbvSave(o.table, repaint);
    });
    // drag its right edge to resize it
    const edge = th.querySelector(".dbv-resize");
    edge.addEventListener("mousedown", (e) => e.preventDefault());   // no native drag of the heading from its edge
    edge.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      DBV.resizing = true;
      const colEls = tables.map((x) => x.querySelector(`col[data-key="${CSS.escape(c.key)}"]`));
      const start = e.clientX, w0 = th.offsetWidth;
      // the widths as they are on screen become the view's own, in pixels
      const now = Object.fromEntries([...th.closest("table").querySelectorAll("thead th.dbv-th")].map((x) => [x.dataset.key, x.offsetWidth]));
      const t0 = th.closest("table").offsetWidth;
      const setWidth = (w) => { for (const x of tables) { x.style.width = w + "px"; x.style.minWidth = "0"; } if (spacer) spacer.style.width = w + "px"; };
      if (!sized) {
        for (const x of tables.flatMap((tb) => [...tb.querySelectorAll("col[data-key]")])) x.style.width = now[x.dataset.key] + "px";
        setWidth(t0);
      }
      document.body.classList.add("col-resizing");
      const move = (ev) => { const w = Math.max(60, Math.min(900, w0 + ev.clientX - start)); for (const x of colEls) x.style.width = w + "px"; setWidth(t0 + w - w0); };
      const end = (ev) => {
        window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end);
        document.body.classList.remove("col-resizing");
        DBV.resizing = false;
        const w = Math.max(60, Math.min(900, Math.round(w0 + ev.clientX - start)));
        if (w === w0) return;
        const all = dbvColumns(view, o.props);
        for (const x of all) if (now[x.key] && !x.width) x.width = now[x.key];
        all.find((x) => x.key === c.key).width = w;
        dbvSetColumns(view, all);
        dbvSave(o.table, repaint);
      };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", end);
    });
    return th;
  });
  const total = shown.reduce((s, c) => s + px(c), 0) + (sized ? 44 : 0);
  // frozen columns: each sticks at the width of those before it (the heading row sticks to the top, see style.css)
  const nFrz = Math.min(dbvFrozen(view), shown.length);
  const lefts = shown.slice(0, nFrz).map((c, i) => shown.slice(0, i).reduce((s, x) => s + px(x), 0));
  const frz = (cell, i) => {
    if (i < nFrz) { cell.classList.add("frz"); cell.style.left = lefts[i] + "px"; if (i === nFrz - 1) cell.classList.add("frz-last"); }
    return cell;
  };
  ths.forEach(frz);
  const scrolls = !fit || total > pane;   // sized by the user, or more columns than room: it runs sideways on purpose
  const ctx = { open: o.open };
  const tr = (row) => h("tr", { tabindex: "0", "data-id": o.rowId(row),
    onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) o.open(row); } },
    shown.map((c, i) => frz(dbvCell(c.prop, row, c, ctx), i)), sized && h("td", { class: "dbv-pad" }));
  let bodies;
  const gp = o.props.find((p) => p.key === view.group);
  if (gp) {
    const folded = new Set(view.folded || []);
    bodies = dbvGroups(gp, rows).filter((g) => g.rows.length).map((g) => h("tbody", { class: "dbv-group", "data-group": g.value },
      h("tr", { class: "dbv-grouphead" }, h("td", { colspan: String(shown.length + (sized ? 1 : 0)) },
        h("button", { type: "button", class: "dbv-fold", "aria-expanded": String(!folded.has(g.value)), onclick: () => {
          if (folded.has(g.value)) folded.delete(g.value); else folded.add(g.value);
          view.folded = [...folded]; dbvSave(o.table, repaint);
        } }, icon(folded.has(g.value) ? "chevRight" : "chev"), gp.type === "select" ? dbvChip(g) : h("b", null, g.label), h("span", { class: "n" }, g.rows.length)))),
      folded.has(g.value) ? null : g.rows.map(tr)));
  } else bodies = [h("tbody", null, rows.map(tr))];
  const pad = (tag) => sized && h(tag, { class: "dbv-pad" });
  const table = (...kids) => h("table", { class: "htable dbv" + (sized ? " sized" : ""), style: `width:${total}px` }, colgroup(), ...kids);
  tables = [table(h("thead", null, h("tr", null, ths, pad("th")))), table(...bodies)];
  const wide = scrolls ? "" : null;
  const head = h("div", { class: "dbv-head", "data-scrolls": wide }, tables[0]);
  const body = h("div", { class: "dbv-body", "data-scrolls": wide }, tables[1]);
  // the sideways scrollbar: kept at the foot of the window while the table runs past it, so it is always in reach
  const spacer = h("div", { class: "dbv-hspace", style: `width:${total}px` });
  const bar = h("div", { class: "dbv-hbar", "aria-hidden": "true", "data-scrolls": wide }, spacer);
  const boxes = [head, body, bar], key = o.table + ":" + view.id;
  for (const b of boxes) b.addEventListener("scroll", () => {
    const x = b.scrollLeft;
    DBV.scrollX[key] = x;
    for (const o2 of boxes) if (o2 !== b && o2.scrollLeft !== x) o2.scrollLeft = x;
  }, { passive: true });
  return h("div", { class: "htable-wrap dbv-wrap", "data-scrolls": wide, "data-x": key }, head, body, bar);
}
// after a repaint the table is where it was sideways (hunt.js calls this once the new view is on the page)
function dbvKeepX(root) {
  for (const w of root.querySelectorAll(".dbv-wrap[data-x]")) {
    const x = DBV.scrollX[w.dataset.x] || 0;
    for (const b of w.querySelectorAll(":scope > .dbv-head, :scope > .dbv-body, :scope > .dbv-hbar")) b.scrollLeft = x;
  }
}

// ---------------------------------------------------------------- the board layout
function dbvBoardEl(o, view, cols, rows, repaint) {
  const gp = dbvBoardProp(o.props, view);
  if (!gp) return h("div", { class: "hunt-empty" }, t("Pick a property to group the board by"));
  const shown = cols.filter((c) => !c.hidden && c.prop.type !== "title");
  const title = o.props.find((p) => p.type === "title");
  const card = (row) => {
    const el = h("div", { class: "dbv-card", draggable: gp.set ? "true" : null, tabindex: "0", "data-id": o.rowId(row),
      onclick: (e) => { if (!e.target.closest("button, a")) o.open(row); }, onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) o.open(row); } },
      h("div", { class: "dbv-cardtitle" }, dbvShow(title, row)),
      shown.filter((c) => c.key !== gp.key).map((c) => {
        const v = dbvShow(c.prop, row);
        if (v === "" || v == null) return null;
        // a date says which it is (a deadline, a next step's date…): on a card there is no heading above it
        return h("div", { class: "dbv-cardprop", "data-key": c.key }, c.prop.type === "date" && h("span", { class: "dbv-plabel" }, c.prop.label), v);
      }));
    el.addEventListener("dragstart", (e) => { DBV.card = { table: o.table, row }; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", o.rowId(row)); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => { DBV.card = null; el.classList.remove("dragging"); });
    return el;
  };
  const columns = dbvGroups(gp, rows).filter((g) => g.value !== "" || g.rows.length).map((g) => {
    const col = h("div", { class: "dbv-col", "data-group": g.value },
      h("div", { class: "dbv-colhead" }, dbvChip(g), h("span", { class: "n" }, g.rows.length)),
      h("div", { class: "dbv-cards" }, g.rows.map(card)));
    col.addEventListener("dragover", (e) => { if (DBV.card && DBV.card.table === o.table && g.value !== "") { e.preventDefault(); col.classList.add("drop"); } });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("drop"); });
    col.addEventListener("drop", async (e) => {
      col.classList.remove("drop");
      if (!DBV.card || DBV.card.table !== o.table) return;
      e.preventDefault();
      const { row } = DBV.card;
      DBV.card = null;
      if (gp.get(row) === g.value) return;
      try { await gp.set(row, g.value); } catch (err) { toast(err.message); }
    });
    return col;
  });
  return h("div", { class: "dbv-board", "data-scrolls": "" }, columns);   // a column per option: it runs sideways, as in Notion
}

// ---------------------------------------------------------------- dates: the calendar and the Gantt chart
const dbvPad = (n) => String(n).padStart(2, "0");
const dbvYmd = (d) => `${d.getFullYear()}-${dbvPad(d.getMonth() + 1)}-${dbvPad(d.getDate())}`;   // local, not UTC
const dbvDay = (s) => { const [y, m, d] = String(s).slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const dbvAddDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const dbvDays = (a, b) => Math.round((dbvDay(dbvYmd(b)) - dbvDay(dbvYmd(a))) / 864e5);
const dbvLocale = () => document.documentElement.lang || undefined;

function dbvItems(o, rows) {
  const out = [];
  for (const row of rows) for (const it of (o.dated(row).items || [])) if (it.at) out.push({ ...it, row });
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

// one dated thing (an interview, a deadline): opens its row
function dbvItemEl(o, it, { day = false } = {}) {
  const time = it.at.length > 10 ? it.at.slice(11, 16) : "";
  return h("button", { type: "button", class: "dbv-item k-" + (it.kind || "event"), title: [day ? it.at : time, it.label, it.sub].filter(Boolean).join(" · "),
    onclick: (e) => { e.stopPropagation(); o.open(it.row); } },
  time && h("span", { class: "dbv-itime" }, time), h("span", { class: "dbv-ilabel" }, it.label), it.sub && h("span", { class: "dbv-isub" }, it.sub));
}

function dbvCalendarEl(o, rows, repaint) {
  const todayS = dbvYmd(new Date());
  const month = DBV.month[o.table] || todayS.slice(0, 7);
  const first = dbvDay(month + "-01");
  const start = dbvAddDays(first, -((first.getDay() + 6) % 7));   // weeks start on Monday
  const byDay = {};
  for (const it of dbvItems(o, rows)) (byDay[it.at.slice(0, 10)] = byDay[it.at.slice(0, 10)] || []).push(it);
  const go = (m) => { DBV.month[o.table] = m; repaint(); };
  const shift = (n) => go(dbvYmd(new Date(first.getFullYear(), first.getMonth() + n, 1)).slice(0, 7));
  const weeks = [];
  let d = start;
  do {
    const days = [];
    for (let i = 0; i < 7; i++, d = dbvAddDays(d, 1)) {
      const k = dbvYmd(d);
      days.push(h("div", { class: "dbv-calday" + (d.getMonth() !== first.getMonth() ? " out" : "") + (k === todayS ? " today" : "") + (i > 4 ? " weekend" : ""), "data-day": k },
        h("span", { class: "dbv-calnum" }, d.getDate()),
        (byDay[k] || []).map((it) => dbvItemEl(o, it))));
    }
    weeks.push(h("div", { class: "dbv-calweek" }, days));
  } while (d.getMonth() === first.getMonth());
  const wd = new Intl.DateTimeFormat(dbvLocale(), { weekday: "short" });
  return h("div", { class: "dbv-cal" },
    h("div", { class: "dbv-calbar" },
      h("b", { class: "dbv-calmonth" }, new Intl.DateTimeFormat(dbvLocale(), { year: "numeric", month: "long" }).format(first)),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Previous month"), "aria-label": t("Previous month"), onclick: () => shift(-1) }, icon("chevLeft")),
      h("button", { type: "button", class: "btn sm ghost", onclick: () => go(todayS.slice(0, 7)) }, t("Today")),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Next month"), "aria-label": t("Next month"), onclick: () => shift(1) }, icon("chevRight"))),
    h("div", { class: "dbv-calhead" }, [0, 1, 2, 3, 4, 5, 6].map((i) => h("span", null, wd.format(new Date(2024, 0, 1 + i))))),
    h("div", { class: "dbv-calweeks" }, weeks));
}

function dbvGanttEl(o, rows) {
  const now = new Date();
  const title = o.props.find((p) => p.type === "title");
  const lines = rows.map((row) => ({ row, ...o.dated(row) })).filter((x) => x.start || (x.items || []).length);
  if (!lines.length) return h("div", { class: "hunt-empty" }, t("Nothing with a date yet"));
  const dates = [dbvYmd(now)];
  for (const x of lines) { if (x.start) dates.push(x.start); if (x.end) dates.push(x.end); for (const it of x.items || []) dates.push(it.at.slice(0, 10)); }
  dates.sort();
  const from = dbvAddDays(dbvDay(dates[0]), -3), to = dbvAddDays(dbvDay(dates[dates.length - 1]), 14);
  const n = dbvDays(from, to) + 1, W = 30;
  const x = (s) => dbvDays(from, dbvDay(s)) * W;
  const months = [], days = [];
  for (let i = 0; i < n; i++) {
    const d = dbvAddDays(from, i);
    if (i === 0 || d.getDate() === 1) months.push(h("span", { class: "dbv-gmonth", style: `left:${i * W}px` }, new Intl.DateTimeFormat(dbvLocale(), { year: "numeric", month: "short" }).format(d)));
    days.push(h("span", { class: "dbv-gday" + ([0, 6].includes(d.getDay()) ? " weekend" : "") + (dbvYmd(d) === dbvYmd(now) ? " today" : ""), style: `left:${i * W}px;width:${W}px` }, d.getDate()));
  }
  const width = n * W;
  const todayX = x(dbvYmd(now)) + W / 2;
  const track = (ln) => h("div", { class: "dbv-gtrack", style: `width:${width}px` },
    h("span", { class: "dbv-gnow", style: `left:${todayX}px`, "aria-hidden": "true" }),
    ln.start && h("button", { type: "button", class: "dbv-gbar " + (ln.cls || ""), title: `${ln.start} → ${ln.end || ln.start}`, "aria-label": `${ln.start} → ${ln.end || ln.start}`,
      style: `left:${x(ln.start)}px;width:${Math.max(W, x(ln.end || ln.start) - x(ln.start) + W)}px`, onclick: () => o.open(ln.row) }),
    (ln.items || []).map((it) => {
      const el = dbvItemEl(o, { ...it, row: ln.row }, { day: true });
      el.classList.add("dbv-gitem");
      el.style.left = (x(it.at) + W / 2) + "px";
      return el;
    }));
  const el = h("div", { class: "dbv-gantt", "data-scrolls": "" },
    h("div", { class: "dbv-grow dbv-ghead" }, h("div", { class: "dbv-gname" }),
      h("div", { class: "dbv-gtrack", style: `width:${width}px` }, h("div", { class: "dbv-gmonths" }, months), h("div", { class: "dbv-gdays" }, days))),
    lines.map((ln) => h("div", { class: "dbv-grow", "data-id": o.rowId(ln.row) },
      h("div", { class: "dbv-gname" }, h("button", { type: "button", class: "dbv-title", onclick: () => o.open(ln.row) }, dbvShow(title, ln.row) || t("Untitled"))),
      track(ln))));
  // opens with today in sight
  requestAnimationFrame(() => { el.scrollLeft = Math.max(0, todayX - (el.clientWidth - 220) / 2); });
  return el;
}

// ---------------------------------------------------------------- places: the world map
// o.located(row) -> [{area, pin, kind, lat?, lon?}]: kind onsite | hybrid | remote | hq; no lat when it isn't on the
// map (remote, or a place the city list doesn't know). area is its name as shown; o.pin(pin, lat, lon) saves where
// the user put one, under its `pin` (a picked place's id, or a written one's words).
// The land (web/geo/land.txt, Natural Earth) is one SVG path in 1/20 degrees, Miller projection, y down.
const dbvProject = (lon, lat) => [lon, -1.25 * (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + 0.4 * (Math.max(-85, Math.min(85, lat)) * Math.PI / 180)))];
const dbvUnproject = (x, y) => [x, (2.5 * (Math.atan(Math.exp((-y / 1.25) * Math.PI / 180)) - Math.PI / 4)) * 180 / Math.PI];
const MAP_Y = [dbvProject(0, 84)[1], dbvProject(0, -58)[1]];   // the world from the Arctic to Cape Horn

function dbvLand(repaint) {
  if (DBV.land || DBV.landLoading) return DBV.land;
  DBV.landLoading = true;
  fetch("geo/land.txt").then((r) => (r.ok ? r.text() : "")).then((d) => { DBV.land = d || " "; repaint(); })
    .catch(() => { DBV.land = " "; });
  return null;
}

function dbvMapEl(o, rows, repaint) {
  // every place on the map: one per spot, with the rows there; the rest by area, beside it
  const spots = new Map(), off = { remote: new Map(), unknown: new Map() }, none = [];
  const pinKey = new Map();   // a place's name as shown -> what pinning it is saved under (p.pin: its id, or its words)
  for (const row of rows) {
    const places = o.located(row) || [];
    if (!places.length) none.push(row);
    for (const p of places) {
      if (!pinKey.has(p.area)) pinKey.set(p.area, p.pin || p.area);
      if (p.lat == null) {
        const m = off[p.kind === "remote" ? "remote" : "unknown"];
        if (!m.has(p.area)) m.set(p.area, new Set());
        m.get(p.area).add(row);
        continue;
      }
      const k = `${p.lat},${p.lon}`;
      if (!spots.has(k)) spots.set(k, { lat: p.lat, lon: p.lon, areas: new Set(), rows: new Map() });
      const s = spots.get(k);
      s.areas.add(p.area);
      const was = s.rows.get(row);
      s.rows.set(row, was && was !== "hq" ? was : p.kind);   // a company that hires where its HQ is: a place, not just the HQ
    }
  }
  const list = [...spots.values()].map((s) => ({ ...s, xy: dbvProject(s.lon, s.lat), label: [...s.areas].sort((a, b) => a.length - b.length)[0] }));

  // the view: its centre and width in projected degrees, kept for this window
  const fit = () => {
    if (!list.length) return { cx: 10, cy: (MAP_Y[0] + MAP_Y[1]) / 2, w: 360 };
    const xs = list.map((s) => s.xy[0]), ys = list.map((s) => s.xy[1]);
    const w = Math.max(24, (Math.max(...xs) - Math.min(...xs)) * 1.5, (Math.max(...ys) - Math.min(...ys)) * 1.5 * 1.8);
    return { cx: (Math.max(...xs) + Math.min(...xs)) / 2, cy: (Math.max(...ys) + Math.min(...ys)) / 2, w: Math.min(360, w) };
  };
  DBV.mapView = DBV.mapView || {};
  const v = DBV.mapView[o.table] = DBV.mapView[o.table] || fit();
  const land = dbvLand(repaint);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "dbv-mapsvg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const g = document.createElementNS("http://www.w3.org/2000/svg", "path");
  g.setAttribute("class", "dbv-land");
  g.setAttribute("transform", "scale(0.05)");
  if (land) g.setAttribute("d", land);
  svg.append(g);
  const pins = h("div", { class: "dbv-mappins" });
  const stage = h("div", { class: "dbv-mapstage" + (DBV.pinning ? " pinning" : ""), tabindex: "-1" }, svg, pins);

  // projected degrees <-> pixels on the stage
  const box = () => { const W = stage.clientWidth || 800, H = stage.clientHeight || 480; return { W, H, k: W / v.w }; };
  const toPx = ([x, y]) => { const { W, H, k } = box(); return [(x - v.cx) * k + W / 2, (y - v.cy) * k + H / 2]; };
  const fromPx = (px, py) => { const { W, H, k } = box(); return [(px - W / 2) / k + v.cx, (py - H / 2) / k + v.cy]; };

  const draw = () => {
    const { W, H, k } = box();
    v.w = Math.max(0.5, Math.min(400, v.w));
    svg.setAttribute("viewBox", `${v.cx - W / k / 2} ${v.cy - H / k / 2} ${W / k} ${H / k}`);
    // close spots join while they would overlap; a join opens by zooming in
    const pts = list.map((s) => ({ s, p: toPx(s.xy) })).filter(({ p }) => p[0] > -40 && p[0] < W + 40 && p[1] > -40 && p[1] < H + 40)
      .sort((a, b) => b.s.rows.size - a.s.rows.size);
    const groups = [];
    for (const x of pts) {
      const near = groups.find((gr) => Math.hypot(gr.p[0] - x.p[0], gr.p[1] - x.p[1]) < 30);
      if (near) near.items.push(x); else groups.push({ p: x.p, items: [x] });
    }
    pins.replaceChildren(...groups.map((gr) => {
      const rowsHere = new Map();
      for (const { s } of gr.items) for (const [r, kind] of s.rows) rowsHere.set(r, rowsHere.get(r) && rowsHere.get(r) !== "hq" ? rowsHere.get(r) : kind);
      const many = gr.items.length > 1;
      const hqOnly = [...rowsHere.values()].every((x) => x === "hq");
      const label = many ? t("{place} +{n}", { place: gr.items[0].s.label, n: gr.items.length - 1 }) : gr.items[0].s.label;
      const b = h("button", { type: "button", class: "dbv-pin" + (many ? " many" : "") + (hqOnly ? " hq" : ""), style: `left:${gr.p[0]}px;top:${gr.p[1]}px`,
        "data-area": gr.items.map(({ s }) => s.label).join("|"), title: gr.items.map(({ s }) => s.label).join(" · "), "aria-label": `${label}: ${rowsHere.size}`,
        onclick: (e) => {
          e.stopPropagation();
          if (many) {   // zoom to see them apart
            const xs = gr.items.map(({ s }) => s.xy[0]), ys = gr.items.map(({ s }) => s.xy[1]);
            v.cx = (Math.max(...xs) + Math.min(...xs)) / 2; v.cy = (Math.max(...ys) + Math.min(...ys)) / 2;
            v.w = Math.max(0.5, Math.min(v.w / 3, (Math.max(...xs) - Math.min(...xs) + Math.max(...ys) - Math.min(...ys)) * 2.5));
            draw();
            return;
          }
          dbvSpotMenu(o, e.currentTarget, gr.items[0].s, repaint, pinKey);
        } },
      h("span", { class: "dbv-pindot" }, rowsHere.size), h("span", { class: "dbv-pinlabel" }, label));
      return b;
    }));
  };

  // wheel zooms where the pointer is; dragging pans; a click in pin mode places the place
  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    const before = fromPx(e.clientX - r.left, e.clientY - r.top);
    v.w *= Math.exp(e.deltaY * 0.0015);
    v.w = Math.max(0.5, Math.min(400, v.w));
    const after = fromPx(e.clientX - r.left, e.clientY - r.top);
    v.cx += before[0] - after[0]; v.cy += before[1] - after[1];
    draw();
  }, { passive: false });
  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest(".dbv-pin, button")) return;
    drag = { x: e.clientX, y: e.clientY, cx: v.cx, cy: v.cy, moved: false };
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const { k } = box();
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
    v.cx = drag.cx - (e.clientX - drag.x) / k; v.cy = drag.cy - (e.clientY - drag.y) / k;
    if (drag.moved) { stage.classList.add("dragging"); draw(); }
  });
  stage.addEventListener("pointerup", (e) => {
    const d = drag;
    drag = null;
    stage.classList.remove("dragging");
    if (!d || d.moved || !DBV.pinning) return;
    const r = stage.getBoundingClientRect();
    const [lon, lat] = dbvUnproject(...fromPx(e.clientX - r.left, e.clientY - r.top));
    const { pin } = DBV.pinning;
    DBV.pinning = null;
    o.pin(pin, Math.round(lat * 1e4) / 1e4, Math.round(lon * 1e4) / 1e4);
  });
  stage.addEventListener("dblclick", (e) => {
    if (e.target.closest(".dbv-pin")) return;
    const r = stage.getBoundingClientRect();
    [v.cx, v.cy] = fromPx(e.clientX - r.left, e.clientY - r.top);
    v.w /= 2;
    draw();
  });
  const zoom = (f) => { v.w *= f; draw(); };
  const ro = new ResizeObserver(() => { if (stage.isConnected) draw(); else ro.disconnect(); });
  ro.observe(stage);

  // what isn't on the map
  const offRow = (area, set, kind) => h("div", { class: "dbv-offrow", "data-area": area },
    h("span", { class: "dbv-offname" }, kind === "remote" ? t("Remote · {area}", { area }) : area),
    h("span", { class: "n" }, set.size),
    kind === "unknown" && h("button", { type: "button", class: "btn sm ghost dbv-place", title: t("Click the map where “{area}” is", { area }),
      onclick: () => { DBV.pinning = { area, pin: pinKey.get(area) || area }; repaint(); } }, icon("pin"), t("Place it")),
    h("div", { class: "dbv-offrows" }, [...set].map((r) => dbvRowButton(o, r))));
  const side = h("div", { class: "dbv-mapside" },
    off.remote.size > 0 && h("div", { class: "dbv-offgroup" }, h("b", null, t("Remote")), [...off.remote].map(([a, s]) => offRow(a, s, "remote"))),
    off.unknown.size > 0 && h("div", { class: "dbv-offgroup" }, h("b", null, t("Not found on the map")),
      h("p", { class: "muted" }, t("Place each one yourself, or ask Claude to.")), [...off.unknown].map(([a, s]) => offRow(a, s, "unknown"))),
    none.length > 0 && h("div", { class: "dbv-offgroup" }, h("b", null, t("No place yet ({n})", { n: none.length })),
      h("div", { class: "dbv-offrows open" }, none.map((r) => dbvRowButton(o, r)))));
  const hasSide = off.remote.size || off.unknown.size || none.length;

  DBV.pinRepaint = repaint;
  const el = h("div", { class: "dbv-map" + (hasSide ? "" : " full") },
    h("div", { class: "dbv-mapwrap" }, stage,
      h("div", { class: "dbv-mapbtns" },
        h("button", { type: "button", class: "btn icon sm", title: t("Zoom in"), "aria-label": t("Zoom in"), onclick: () => zoom(1 / 1.6) }, icon("plus")),
        h("button", { type: "button", class: "btn icon sm", title: t("Zoom out"), "aria-label": t("Zoom out"), onclick: () => zoom(1.6) }, icon("minus")),
        h("button", { type: "button", class: "btn sm", onclick: () => { Object.assign(v, fit()); draw(); } }, t("Show every place"))),
      DBV.pinning && h("div", { class: "dbv-pinbar" }, icon("pin"), t("Click the map where “{area}” is", { area: DBV.pinning.area }),
        h("button", { type: "button", class: "btn sm ghost", onclick: () => { DBV.pinning = null; repaint(); } }, t("Cancel"))),
      !list.length && !DBV.pinning && h("div", { class: "dbv-mapempty" }, t("Nothing to show on the map yet: give the roles their places.")),
      h("span", { class: "dbv-mapcredit" }, "GeoNames · Natural Earth")),
    hasSide ? side : null);
  // as tall as the window has room for below it (the companies' page has more above it)
  requestAnimationFrame(() => {
    if (innerWidth > 760) el.style.height = Math.max(420, innerHeight - el.getBoundingClientRect().top - 20) + "px";
    draw();
  });
  return el;
}

// a row in a list on the map: its title (and a line under it), opening it
function dbvRowButton(o, row) {
  const title = o.props.find((p) => p.type === "title");
  const sub = o.sub ? o.sub(row) : "";
  return h("button", { type: "button", class: "dbv-maprow", onclick: () => { closeFloat(); o.open(row); } },
    h("span", { class: "dbv-maprow-t" }, dbvText(title, row) || t("Untitled")), sub && h("span", { class: "dbv-maprow-s" }, sub));
}

// what is at one spot: its rows, and moving it if the map put it in the wrong place
function dbvSpotMenu(o, anchor, s, repaint, pinKey) {
  const rows = [...s.rows];
  const panel = h("div", { class: "menu dbv-pop dbv-spot" },
    h("div", { class: "dbv-spothead" }, [...s.areas].map((a) => h("span", { class: "dbv-spotarea" }, h("b", null, a),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("In the wrong place? Put “{area}” where it is", { area: a }),
        "aria-label": t("In the wrong place? Put “{area}” where it is", { area: a }), onclick: () => { closeFloat(); DBV.pinning = { area: a, pin: pinKey.get(a) || a }; repaint(); } }, icon("pin"))))),
    h("div", { class: "dbv-spotrows" }, rows.map(([r, kind]) => {
      const b = dbvRowButton(o, r);
      if (kind === "hq") b.append(h("span", { class: "chip" }, t("Headquarters")));
      if (kind === "hybrid") b.append(h("span", { class: "chip" }, t("Hybrid")));
      return b;
    })));
  openFloat(anchor, panel, { minWidth: 240, focus: panel.querySelector(".dbv-maprow") });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && DBV.pinning && !Float.cur) { DBV.pinning = null; if (DBV.pinRepaint) DBV.pinRepaint(); }
});

// ---------------------------------------------------------------- the whole view
// o: {table, props, rows, rowId(row), open(row), defaults: [views], empty (node when there are no rows at all),
//     repaint()}
function dbView(o) {
  const tb = dbvTable(o.table, o.defaults);
  const view = dbvView(tb);
  const cols = dbvColumns(view, o.props);
  const rows = dbvRows(view, o.props, o.rows);
  const body = !o.rows.length ? h("div", { class: "hunt-empty" }, o.empty)
    : !rows.length ? h("div", { class: "hunt-empty" }, t("Nothing matches"))
    : view.layout === "calendar" && o.dated ? dbvCalendarEl(o, rows, o.repaint)
    : view.layout === "gantt" && o.dated ? dbvGanttEl(o, rows)
    : view.layout === "map" && o.located ? dbvMapEl(o, rows, o.repaint)
    : view.layout === "board" ? dbvBoardEl(o, view, cols, rows, o.repaint) : dbvTableEl(o, view, cols, rows, o.repaint);
  if (DBV.refit) { DBV.refit = false; requestAnimationFrame(() => dbvRefit()); }   // drawn before its pane existed: fit it now
  return h("div", { class: "dbv-view", "data-table": o.table, "data-layout": view.layout },
    h("div", { class: "dbv-bar" }, dbvTabs(o, tb, o.repaint), h("span", { class: "spacer" }), dbvTools(o, tb, view, cols, o.repaint)),
    dbvFilterChips(o, view, o.repaint),
    body);
}
