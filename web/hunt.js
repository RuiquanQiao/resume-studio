// The Applying workspace (Ctrl+3), Resume Studio's own job-hunt tracker (docs/JOB_HUNT.md): three tables
// under tabs — applications (one per company + role, with their progress), companies (what they are, their
// roles in order of preference, the sign-up on their own careers site) and platforms (the online profiles
// autofill.js keeps in step). A row opens its page. Claude works the same data from the chat on the left
// through scripts/hunt.py; every change, from either side, comes back as a "hunt" event.
// Loaded after autofill.js and before app.js: only definitions.
"use strict";

const H = {
  tab: "apps",           // "apps" | "companies" | "platforms"
  data: null,            // GET /api/hunt: {applications, companies, states, platforms, tracker, career_ops}
  page: null,            // null (the table) | {kind: "company" | "app" | "platform", id}
  view: null,            // the open company's or application's page data
  show: "open",          // applications: "open" | "all"
  q: "",                 // the search box
  loading: null,
  editing: null,         // the booked thing being edited on a role's page (its id)
  notes: {},             // a timeline note typed but not added yet, per application: {text, on}; repaints keep it
};

const HUNT_TABS = { apps: "Progress", companies: "Companies", platforms: "Platforms" };
const HSTATES = { saved: "Want to apply", applied: "Applied", assessment: "Tests", interview: "Interviewing", offer: "Offer",
  hired: "Hired", rejected: "Not successful", withdrawn: "Withdrawn" };
const HCHANNELS = { site: "Its own site", platform: "A platform", referral: "Referral", other: "Other" };
const huntState = (id) => t(HSTATES[id] || id || "—");
const hChip = (id) => h("span", { class: "state-chip s-" + (id || "unknown") }, huntState(id));
const huntCompany = (id) => ((H.data && H.data.companies) || []).find((c) => c.id === id) || null;
const platformName = (id) => ((F.sites || []).find((s) => s.id === id) || {}).name || SITE_NAMES[id] || id || "";
const OPEN_STATES = ["saved", "applied", "assessment", "interview"];
const huntOpen = (a) => OPEN_STATES.includes(a.status);
const epochOf = (at) => { const d = new Date(at); return isNaN(d) ? 0 : d.getTime() / 1000; };
const huntWhen = (at) => (at ? relTime(epochOf(at)) : "");

function channelText(a) {
  if (a.channel === "platform") return a.platform ? t("via {platform}", { platform: platformName(a.platform) }) : t("A platform");
  if (a.channel === "site" && a.order) return t("Own site · choice {n}", { n: a.order });
  return t(HCHANNELS[a.channel] || a.channel || "");
}

// a send's note in the ledger is "SUB-002 v3 · where": the subset by its name
function sentNote(note) {
  const m = /^(\S+) v(\d+)(.*)$/s.exec(note || "");
  return m ? sentText({ subset: m[1], n: m[2] }) + m[3] : note || "";
}

// the applications that got version n of a subset, oldest send first (null while the tracker hasn't loaded)
function sentWith(subsetId, n) {
  if (!H.data) { if (!H.loading) loadHunt({ quiet: true }).then(() => { if (ui.sel.kind === "versions") paintEditor({ keepScroll: true }); }); return null; }
  const out = [];
  for (const a of H.data.applications) {
    const s = (a.sent || []).find((x) => x.subset === subsetId && Number(x.n) === Number(n));
    if (s) out.push({ a, at: s.at });
  }
  return out.sort((x, y) => (x.at || "").localeCompare(y.at || ""));
}

// one role a version went to, opened in Applying
function sentRoleChip(a) {
  const c = huntCompany(a.company) || {};
  return h("button", { type: "button", class: "role-chip s-" + a.status, title: huntState(a.status),
    onclick: async () => { if (W.ws !== "fill") setWorkspace("fill"); await openHunt("app", a.id); } },
  h("span", { class: "rc-dot" }), h("span", { class: "rc-name" }, (c.name ? c.name + " / " : "") + a.role));
}

function sentText(s) {
  if (!s) return "";
  const v = subsets().find((x) => x.id === s.subset);
  return `${subsetName(v) || s.subset} · v${s.n}`;
}

// ================================================================ data
async function loadHunt({ quiet = false } = {}) {
  if (H.loading) { H.again = true; return H.loading; }
  H.loading = (async () => {
    try {
      do {
        H.again = false;
        const [data] = await Promise.all([api("GET", "/api/hunt"), DBV.tables && !H.viewsStale ? null : loadViews()]);
        H.data = data; H.viewsStale = false;
        if (H.page && H.page.kind !== "platform") await loadHuntPage({ quiet: true });
      } while (H.again);
      scanDone();
      if (!H.autoScan) { H.autoScan = true; api("POST", "/api/hunt/scan", { auto: true }).catch(() => {}); }
    } catch (e) { if (!quiet) toast(e.message); }
    finally { H.loading = null; }
    if (W.ws === "fill" && dbvBusy()) DBV.pending = true;   // an open cell editor or view panel: after it closes
    else if (W.ws === "fill") paintFill();
  })();
  return H.loading;
}

async function loadHuntPage({ quiet = false } = {}) {
  const p = H.page;
  if (!p || p.kind === "platform") return;
  try {
    H.view = await api("GET", p.kind === "company" ? `/api/hunt/companies/${encodeURIComponent(p.id)}` : `/api/hunt/apps/${encodeURIComponent(p.id)}`);
  } catch (e) {
    if (!quiet) toast(e.message);
    H.page = null; H.view = null;   // it is gone (deleted here or elsewhere): back to the table
    storeHunt();
  }
}

// the server asks how a logo looks before it keeps it (jobhunt/api.py `inspect`): drawn at 128 px on a transparent
// canvas, the share of it that shows on a white page; and the drawing on white, as a png for Claude to look at
async function checkLogo({ token, url }) {
  let stats;
  try {
    const img = new Image();
    await new Promise((ok, no) => { img.onload = ok; img.onerror = no; img.src = url; });
    const w = img.naturalWidth, ht = img.naturalHeight, S = 128;
    const k = w && ht ? Math.min(S / w, S / ht) : 1, dw = w && ht ? w * k : S, dh = w && ht ? ht * k : S;
    const cv = document.createElement("canvas"); cv.width = cv.height = S;
    const g = cv.getContext("2d");
    g.drawImage(img, (S - dw) / 2, (S - dh) / 2, dw, dh);
    const px = g.getImageData(0, 0, S, S).data;
    let shows = 0, top = 0;
    const tones = new Map();   // colours, roughly, of what shows: one colour all over is a block with no mark
    for (let i = 0; i < px.length; i += 4) {
      const a = px[i + 3] / 255;   // the pixel over white
      const r = 255 + (px[i] - 255) * a, gr = 255 + (px[i + 1] - 255) * a, b = 255 + (px[i + 2] - 255) * a;
      if (Math.min(r, gr, b) >= 235) continue;
      shows++;
      const k = (r >> 4) * 256 + (gr >> 4) * 16 + (b >> 4), n = (tones.get(k) || 0) + 1;
      tones.set(k, n); if (n > top) top = n;
    }
    const out = document.createElement("canvas"); out.width = out.height = 256;
    const o = out.getContext("2d");
    o.fillStyle = "#fff"; o.fillRect(0, 0, 256, 256);
    o.drawImage(img, (256 - dw * 2) / 2, (256 - dh * 2) / 2, dw * 2, dh * 2);
    stats = { width: w, height: ht, visible: shows / (S * S), plain: shows > 0.9 * S * S && top > 0.97 * shows, png: out.toDataURL("image/png") };
  } catch (e) { stats = { error: true }; }
  api("POST", url, stats).catch(() => {});
}

function listenHunt(es) {
  es.addEventListener("logo-check", (m) => checkLogo(JSON.parse(m.data)));
  es.addEventListener("hunt", (m) => {
    const d = JSON.parse(m.data || "{}");
    if (d.versions && typeof refresh === "function") refresh();   // a send kept a version of a subset
    // another window arranged a table: not the echo of this one's own save, nor while it has one waiting
    if (d.views && !Object.keys(DBV.timers).length && Date.now() - DBV.saved > 2000) H.viewsStale = true;
    loadHunt({ quiet: true });
  });
}

function storeHunt() {
  store("hunt-tab", H.tab);
  store("hunt-page", H.page ? JSON.stringify(H.page) : null);
}

function restoreHunt() {
  const tab = store("hunt-tab");
  if (tab in HUNT_TABS) H.tab = tab;
  try { const p = JSON.parse(store("hunt-page") || "null"); if (p && p.kind && p.id) H.page = p; } catch (e) { /* none */ }
  if (H.page && H.page.kind === "platform") F.site = H.page.id;
}

async function setHuntTab(tab) {
  await flushTyping();
  H.tab = tab; H.page = null; H.view = null;
  storeHunt();
  paintFill();
  renderWsTools();
  welcomeAgain();
}

// an empty chat's suggestions, and the line above the composer, follow what the page shows
function welcomeAgain() { if (!ui.chat.messages.length && !ui.chat.running) renderChat(); renderComposerState(); }

async function openHunt(kind, id) {
  await flushTyping();
  if (kind === "platform") {
    H.tab = "platforms"; H.page = { kind, id }; H.view = null;
    storeHunt();
    if (F.site !== id) { F.site = id; store("fill-site", id); F.open = {}; }
    paintFill();
    await loadFillSite(id);
    renderWsTools();
    welcomeAgain();
    return;
  }
  H.tab = kind === "company" ? "companies" : "apps";
  H.page = { kind, id }; H.view = null; H.editing = null;
  storeHunt();
  paintFill();
  await loadHuntPage();
  paintFill();
  welcomeAgain();
}

// the workspace: a platform's own page is autofill.js's; everything else is here
function paintFill() {
  repaintKeeping($("#fillspace"), () => {
    if (H.tab === "platforms" && H.page && H.page.kind === "platform" && (F.sites || []).some((s) => s.id === H.page.id)) {
      paintPlatform();
      return;
    }
    if (H.page && H.page.kind === "platform") { H.page = null; storeHunt(); }
    paintHunt();
  });
}

function huntBar() {
  const d = H.data;
  const n = { apps: d ? d.applications.length : null, companies: d ? d.companies.length : null, platforms: F.sites ? F.sites.length : null };
  return h("div", { class: "hunt-bar" + (H.page ? " is-page" : "") },
    h("div", { class: "hunt-tabs", role: "tablist", "aria-label": t("Applying") }, Object.entries(HUNT_TABS).map(([id, label]) =>
      h("button", { type: "button", role: "tab", class: "hunt-tab" + (H.tab === id ? " on" : ""), "data-tab": id, "aria-selected": String(H.tab === id),
        onclick: () => setHuntTab(id) }, t(label), n[id] != null && h("span", { class: "n" }, n[id])))));
}

function paintHunt() {
  const el = $("#fillspace");
  if (el) repaintKeeping(el, () => paintHuntNow(el));
}
function paintHuntNow(el) {
  const old = el.querySelector(".hunt-scroll");
  const scroll = old && old.dataset.page === pageKey() ? old.scrollTop : 0;
  let body;
  if (!H.data) body = h("div", { class: "muted" }, t("Loading…"));
  else if (H.page && H.page.kind === "company") body = H.view && H.view.company ? companyPage(H.view) : h("div", { class: "muted" }, t("Loading…"));
  else if (H.page && H.page.kind === "app") body = H.view && H.view.application ? appPage(H.view) : h("div", { class: "muted" }, t("Loading…"));
  else body = H.tab === "companies" ? companiesTable() : H.tab === "platforms" ? platformsTable() : appsTable();
  const sc = h("div", { class: "hunt-scroll", "data-page": pageKey() }, h("div", { class: "hunt-wrap" + (H.page ? " is-page" : "") }, body));
  el.replaceChildren(huntBar(), sc);
  sc.scrollTop = scroll;
  dbvKeepX(sc);
  for (const ta of el.querySelectorAll("textarea")) autosizeFull(ta);
}

// a search box filters as you type; with an IME (pinyin, kana), once the word is finished (ui.js repaintKeeping)
function searchBox(id, value, placeholder, set) {
  return h("input", { class: "hunt-search", type: "search", id, value, placeholder, spellcheck: "false", "aria-label": placeholder,
    oninput: (e) => { set(e.target.value); paintHunt(); } });
}
const pageKey = () => (H.page ? H.page.kind + ":" + H.page.id : "table:" + H.tab);

// ================================================================ the tables
function tableTools(...right) {
  const search = searchBox("table-search", H.q, t("Search"), (v) => { H.q = v; });
  return h("div", { class: "hunt-tools" }, ...right, h("span", { class: "spacer" }), search);
}

const matches = (...texts) => { const q = H.q.trim().toLowerCase(); return !q || texts.some((x) => String(x || "").toLowerCase().includes(q)); };

function lastText(ev) {
  if (!ev) return "";
  if (ev.kind === "status") return `${huntState(ev.from)} → ${huntState(ev.to)}` + (ev.note ? ` · ${ev.note}` : "");
  if (ev.kind === "sent") return t("Went out: {what}", { what: sentNote(ev.note) });
  if (ev.kind === "created") return t("Added");
  return ev.note || "";
}

// ---- the tables are Notion-style database views (web/dbview.js; docs/JOB_HUNT.md, 2026-10-19) ----

// a change made in a table's cell: shown at once, then saved; the "hunt" event brings the saved row back
async function cellApp(a, fields) {
  Object.assign(a, fields);
  paintHunt();
  try { await api("PUT", `/api/hunt/apps/${encodeURIComponent(a.id)}`, fields); }
  catch (e) { toast(e.message); }
  loadHunt({ quiet: true });
}
async function cellStatus(a, status) {
  a.status = status;
  paintHunt();
  try { await api("POST", `/api/hunt/apps/${encodeURIComponent(a.id)}/status`, { status }); }   // a timeline event, as on the role's page
  catch (e) { toast(e.message); }
  loadHunt({ quiet: true });
}
async function cellCompany(c, fields) {
  Object.assign(c, fields);
  paintHunt();
  try { await api("PUT", `/api/hunt/companies/${encodeURIComponent(c.id)}`, fields); }
  catch (e) { toast(e.message); }
  loadHunt({ quiet: true });
}

function appProps() {
  const d = H.data;
  const states = () => (d.states || []).map((s) => ({ value: s.id, label: huntState(s.id), cls: "s-" + s.id }));
  const channels = () => Object.entries(HCHANNELS).map(([value, label]) => ({ value, label: t(label), cls: "s-plain" }));
  const co = (a) => huntCompany(a.company) || {};
  const batches = () => usedBatches().map((b) => ({ value: b, label: b, cls: "s-plain" }));
  return [
    { key: "role", label: t("Role"), type: "title", width: 220, get: (a) => a.role },
    { key: "company", label: t("Company"), type: "relation", width: 170, group: true, get: (a) => co(a).name || "",
      render: (a) => h("button", { type: "button", class: "role-chip co-chip", onclick: () => openHunt("company", a.company) },
        companyLogo(co(a), "xs"), h("span", { class: "rc-name" }, co(a).name || "")) },
    { key: "status", label: t("Status"), type: "select", width: 140, get: (a) => a.status, options: states, set: (a, v) => cellStatus(a, v) },
    { key: "url", label: t("Link"), type: "text", width: 200, get: (a) => a.url || "", set: (a, v) => cellApp(a, { url: v }),
      render: (a) => !/^https?:\/\//.test(a.url || "") ? h("span", { class: "c-muted" }, a.url || "") : h("a", { href: a.url, class: "c-link", title: a.url },
        a.url.replace(/^https?:\/\//, "")) },
    placesProp("places", t("Location"), (a, v) => cellApp(a, { places: v })),
    { key: "referral", label: t("Referral code"), type: "text", width: 130, get: (a) => a.referral || "", set: (a, v) => cellApp(a, { referral: v }) },
    { key: "channel", label: t("How"), type: "select", width: 150, min: 120, wrap: true, hidden: true, get: (a) => a.channel || "site", options: channels,
      render: (a) => h("span", { class: "c-muted" }, channelText(a)),
      set: (a, v) => cellApp(a, { channel: v, ...(v === "platform" && !a.platform && (F.sites || []).length ? { platform: F.sites[0].id } : {}) }) },
    { key: "batch", label: t("Batch"), type: "select", width: 130, free: true, get: (a) => a.batch || "", options: batches,
      set: (a, v) => cellApp(a, { batch: v }) },
    { key: "sent", label: t("Resume sent"), type: "custom", width: 190, min: 130, get: (a) => (a.sent && a.sent.length ? sentText(a.sent[a.sent.length - 1]) : ""),
      render: (a) => h("span", { class: "c-muted c-resume" }, a.sent && a.sent.length ? sentText(a.sent[a.sent.length - 1]) : "", resumeButton(a)) },
    { key: "last", label: t("Latest"), type: "custom", width: 220, get: (a) => (a.last ? a.last.at : ""),
      render: (a) => a.last ? h("span", { class: "c-last" }, h("span", { class: "c-when" }, huntWhen(a.last.at)), h("span", { class: "c-what" }, lastText(a.last))) : "" },
    // one cell for one thing to do: what and when together, sorted by when (an apply with no date, 招满即止, last)
    { key: "events", label: t("Next step"), type: "custom", width: 220, get: (a) => { const e = nextSteps(a)[0]; return e ? `${e.at || "~"} ${evTitle(e)}` : ""; },
      render: (a) => bookedCell(a), edit: (td, a) => openEvents(a) },
    { key: "note", label: t("Notes"), type: "text", width: 220, hidden: true, get: (a) => a.note || "", set: (a, v) => cellApp(a, { note: v }) },
    { key: "jd", label: t("JD"), type: "text", width: 260, hidden: true, get: (a) => a.jd || "", set: (a, v) => cellApp(a, { jd: v }) },
  ];
}

const APP_VIEWS = () => [
  { id: "v1", name: t("Active"), filters: [{ key: "status", op: "is", value: OPEN_STATES }] },
  { id: "v2", name: t("All") },
  { id: "v3", name: t("Board"), layout: "board", group: "status" },
  { id: "v4", name: t("Calendar"), layout: "calendar" },
  { id: "v5", name: t("Gantt"), layout: "gantt" },
  { id: "v6", name: t("Map"), layout: "map" },
];

// ---- where a role is: cities, hybrid, remote with the region it is open to (docs/JOB_HUNT.md, 2026-10-21) ----
const PLACE_KINDS = { onsite: "On site", hybrid: "Hybrid", remote: "Remote" };
const PLACE_ICONS = { onsite: "pin", hybrid: "home", remote: "globe" };
// a place picked from the list (jobhunt/geo.py) is kept by its id and shown by its name in the interface's language
// (the server sends them: H.data.places); one the list doesn't know is shown as written
const PLACE_SEEN = {};
const placeInfo = (p) => (p && p.id && (((H.data && H.data.places) || {})[p.id] || PLACE_SEEN[p.id])) || null;
const placeName = (p) => (placeInfo(p) || {}).name || (p && p.area) || "";
const placeText = (p) => { const area = placeName(p); return p.kind === "remote" ? t("Remote · {area}", { area }) : p.kind === "hybrid" ? t("{area} · hybrid", { area }) : area; };
const placeKey = (p) => (p.kind || "onsite") + "|" + (p.id || p.area.replace(/\s+/g, "").toLowerCase());
function placeChip(p, remove) {
  const info = placeInfo(p);
  const tip = [t(PLACE_KINDS[p.kind || "onsite"]), info ? info.detail : t("Not in the list of places: kept as written")].filter(Boolean).join(" · ");
  return h("span", { class: "pl-chip k-" + (p.kind || "onsite") + (p.id ? "" : " pl-free"), title: tip }, icon(PLACE_ICONS[p.kind || "onsite"]), h("span", null, placeText(p)),
    remove && h("button", { type: "button", class: "pl-x", title: t("Remove"), "aria-label": t("Remove {what}", { what: placeText(p) }), onclick: remove }, icon("x")));
}

// a table's column of places: chips, edited over the cell and saved when the editor closes
function placesProp(key, label, save, extra = {}) {
  return { key, label, type: "custom", width: 180, wrap: true, get: (x) => (x.places || []).map(placeText),
    render: (x) => h("span", { class: "c-places" }, (x.places || []).map((p) => placeChip(p))),
    edit: (td, x) => {
      let next = null;
      const ed = placesEditor(x.places, (v) => { next = v; });
      openFloat(td, h("div", { class: "menu dbv-pop pl-pop" }, ed), { minWidth: Math.max(td.offsetWidth, 360), focus: ed.querySelector(".pl-input"),
        onClose: () => { if (next) save(x, next); dbvAfterClose(); } });
    }, ...extra };
}

// for the world maps: where each place is (the server looked them up: H.data.geo)
// a picked place is where the list puts it (or where the user pinned it, by its id); a written one, where the
// server found or the user pinned its words. `pin` is what pinning it again is saved under.
const placeAt = (p) => p.id ? placeInfo(p) : ((H.data && H.data.geo) || {})[p.area] || null;
const located = (places, kind = null) => (places || []).map((p) => {
  const at = p.kind === "remote" ? null : placeAt(p);
  return { area: placeName(p), pin: p.id || p.area, kind: kind || p.kind, lat: at && at.lat != null ? at.lat : null, lon: at && at.lon != null ? at.lon : null };
});
const appLocated = (a) => located(a.places);
function companyLocated(c) {
  const out = located(c.places);
  const [hq] = c.hq ? located([c.hq], "hq") : [];
  // its headquarters, when it isn't one of the places it hires in already
  if (hq && hq.lat != null && !out.some((p) => p.lat === hq.lat && p.lon === hq.lon)) out.push(hq);
  return out;
}
async function pinPlace(area, lat, lon) {
  try {
    await api("PUT", "/api/hunt/geo", { area, lat, lon });
    toast(t("“{area}” is on the map now", { area: placeName({ id: area, area }) }));
  } catch (e) { toast(e.message); }
  await loadHunt({ quiet: true });
}

// on a role's page and over a table's cell; change() gets the whole new list each time. Typing finds places in
// the list (jobhunt/geo.py), in the interface's language: picking one keeps it by its id, so it reads the same in
// every table and follows the window's language. What the list doesn't have is added as written (the last option)
// and looked up on OpenStreetMap by the server a moment later: found, it becomes a place like the others.
// `single`: one city, picked to replace the one there (a company's headquarters).
function placesEditor(list, change, { single = false } = {}) {
  let cur = [...(list || [])];
  let kind = "onsite";
  const wrap = h("div", { class: "pl-ed" + (single ? " single" : "") });
  const set = (next) => {
    const seen = new Set();
    cur = next.filter((p) => !seen.has(placeKey(p)) && seen.add(placeKey(p)));
    if (single) cur = cur.slice(-1);
    change(cur);
    paint();
    wrap.querySelector(".pl-input").focus();
  };
  const paint = () => {
    const input = h("input", { type: "text", class: "pl-input", spellcheck: "false", autocomplete: "off", role: "combobox", "aria-expanded": "false",
      "aria-autocomplete": "list", "aria-label": kind === "remote" ? t("Region") : t("City"),
      placeholder: kind === "remote" ? t("Open to which region? 中国大陆, APAC, 不限…") : t("City: 南京, 上海…") });
    const sugg = h("div", { class: "pl-sugg", role: "listbox", hidden: true });
    let found = [], active = 0, asked = 0, timer = 0;
    const pick = (o) => { PLACE_SEEN[o.id] = o; set([...cur, { kind, area: o.name, id: o.id }]); };
    // as written: several at once (南京、上海), each still found in the list by the server when it can be
    const asWritten = () => {
      const v = input.value.trim();
      if (!v) { toast(kind === "remote" ? t("Say which region a remote role is open to (不限 if anywhere)") : t("Say which city")); input.focus(); return; }
      const areas = kind === "remote" || single ? [v] : v.split(/\s*[,，、;；/]\s*/).filter(Boolean);
      set([...cur, ...areas.map((area) => ({ kind, area }))]);
    };
    const show = () => {
      const q = input.value.trim();
      const opts = [...found.map((o, i) => h("button", { type: "button", role: "option", tabindex: "-1", class: "lb-opt pl-opt" + (i === active ? " on" : ""),
        "aria-selected": String(i === active), onmousedown: (e) => e.preventDefault(), onclick: () => pick(o) },
        h("span", { class: "pl-oname" }, o.name), o.detail && h("span", { class: "pl-odetail" }, o.detail))),
        ...(q ? [h("button", { type: "button", role: "option", tabindex: "-1", class: "lb-opt pl-opt pl-asis" + (active === found.length ? " on" : ""),
          "aria-selected": String(active === found.length), onmousedown: (e) => e.preventDefault(), onclick: asWritten },
          icon("plus"), kind === "remote" ? h("span", null, t("Keep “{name}” as written", { name: q }))
            // a city the list doesn't have: the server looks it up on OpenStreetMap (its names in every language)
            : h("span", { class: "pl-asis-text" }, h("span", { class: "pl-oname" }, t("Add “{name}”", { name: q })),
              h("span", { class: "pl-odetail" }, t("Not in the list: looked up on OpenStreetMap"))))] : [])];
      sugg.replaceChildren(...opts);
      sugg.hidden = !opts.length;
      input.setAttribute("aria-expanded", String(!sugg.hidden));
    };
    const ask = async () => {
      timer = 0;
      const q = input.value.trim(), n = ++asked;
      if (!q) { found = []; active = 0; show(); return; }
      try {
        const r = await api("GET", `/api/hunt/places?q=${encodeURIComponent(q)}&kind=${kind}`);
        if (n !== asked) return;   // a later keystroke asked again
        const mine = new Set(cur.map(placeKey));
        found = (r.places || []).filter((o) => !mine.has(placeKey({ kind, id: o.id })));
      } catch (e) { found = []; }
      active = 0;
      show();
    };
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(ask, 120); });
    input.addEventListener("keydown", (e) => {
      const n = sugg.hidden ? 0 : sugg.children.length;
      if (e.key === "ArrowDown" && n) { e.preventDefault(); active = (active + 1) % n; show(); }
      else if (e.key === "ArrowUp" && n) { e.preventDefault(); active = (active - 1 + n) % n; show(); }
      else if (e.key === "Escape" && n) { e.preventDefault(); e.stopPropagation(); sugg.hidden = true; input.setAttribute("aria-expanded", "false"); }
      else if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        if (timer && input.value.trim() && !n) { clearTimeout(timer); ask().then(() => { if (found.length) pick(found[0]); else asWritten(); }); return; }
        if (n && found[active]) pick(found[active]); else asWritten();
      }
    });
    input.addEventListener("blur", () => setTimeout(() => { if (!wrap.contains(document.activeElement)) { sugg.hidden = true; input.setAttribute("aria-expanded", "false"); } }, 120));
    // what the user's other roles and companies say, one click away (cities for on site and hybrid, regions for remote)
    const same = (k) => (kind === "remote") === (k === "remote");
    const mine = new Set(cur.filter((p) => p.kind === kind || single).map((p) => placeKey({ ...p, kind })));
    const rows = [...((H.data && H.data.applications) || []), ...((H.data && H.data.companies) || [])];
    const usedMap = new Map();
    for (const p of rows.flatMap((x) => (x.places || []).filter((q) => same(q.kind)))) {
      const k = placeKey({ ...p, kind });
      if (!mine.has(k) && !usedMap.has(k)) usedMap.set(k, p);
    }
    const used = [...usedMap.values()].slice(0, 14);
    wrap.replaceChildren(
      h("div", { class: "pl-list" }, cur.length ? cur.map((p, i) => placeChip(p, () => set(cur.filter((_, j) => j !== i))))
        : h("span", { class: "muted" }, t("Not said yet"))),
      h("div", { class: "pl-add" },
        !single && uiSeg(Object.entries(PLACE_KINDS).map(([value, label]) => ({ value, label: t(label) })), kind, (k) => { kind = k; paint(); wrap.querySelector(".pl-input").focus(); },
          { cls: "sm", label: t("Kind of place") }),
        h("div", { class: "pl-combo" }, input, sugg)),
      ...(used.length ? [h("div", { class: "bk-chips", role: "group", "aria-label": t("Common ones") }, used.map((p) =>
        h("button", { type: "button", class: "bk-chip", onclick: () => set([...cur, { kind, area: p.area, ...(p.id ? { id: p.id } : {}) }]) }, placeName(p))))] : []));
  };
  paint();
  return wrap;
}

// ---- what is booked for a role: tests, 一面, 二面… (docs/JOB_HUNT.md, 2026-10-21) ----
// the usual ones are built in (jobhunt/store.py BOOKED_KINDS): saved with no title, they read in the interface's
// language; offered as chips, with the user's own titles after them
const BOOKED_KINDS = { apply: "Apply by", test: "Written test", assessment: "Assessment", round1: "1st interview",
  round2: "2nd interview", round3: "3rd interview", hr: "HR interview", final: "Final interview" };
const CHIP_KINDS = ["test", "assessment", "round1", "round2", "round3", "hr", "final"];
const evTitle = (e) => e.title || (BOOKED_KINDS[e.kind] ? t(BOOKED_KINDS[e.kind]) : "");
const nowAt = () => { const d = new Date(); return `${dbvYmd(d)} ${dbvPad(d.getHours())}:${dbvPad(d.getMinutes())}`; };
const bookedPast = (e) => !!e.at && (e.at.length > 10 ? e.at : e.at + " 23:59") < nowAt();
// when a role is applied for by is its apply event (docs/JOB_HUNT.md, 2026-10-26): the date the posting gives, or
// 招满即止 when it says so; a role whose closing date isn't known has none
const applyEvent = (a) => (a.schedule || []).find((e) => e.kind === "apply") || null;
const rollingText = () => t("Rolling: until filled");
const bookedWhenOf = (e) => (e.at ? bookedWhen(e.at) : e.rolling ? rollingText() : t("Closing date not known"));
// what is still ahead for a role, soonest first: its apply event until it is applied to (even when late), then
// every round to come; an apply with no date (招满即止) comes last
function nextSteps(a) {
  const apply = a.status === "saved" ? applyEvent(a) : null;
  const ahead = (a.schedule || []).filter((e) => e.kind !== "apply" && e.at && !bookedPast(e));
  if (apply && apply.at) ahead.push(apply);
  ahead.sort((x, y) => x.at.localeCompare(y.at));
  return apply && !apply.at ? [...ahead, apply] : ahead;
}
function daysTo(at) { return Math.round((dbvDay(at.slice(0, 10)) - dbvDay(today())) / 864e5); }
async function openEvents(a) {   // the role's page, at its events
  await openHunt("app", a.id);
  const box = document.querySelector(".bk-list") || document.querySelector(".bk-new");
  if (box) box.scrollIntoView({ block: "center" });
}
// the rounds the user's roles are in (27秋招, 2027 Graduate Program…), for picking one again
const usedBatches = () => [...new Set(((H.data && H.data.applications) || []).map((a) => a.batch).filter(Boolean))].sort((x, y) => x.localeCompare(y, I18N.lang));

function bookedWhen(at, { weekday = true } = {}) {
  const d = dbvDay(at);
  const day = new Intl.DateTimeFormat(dbvLocale(), { month: "short", day: "numeric", ...(weekday ? { weekday: "short" } : {}),
    ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) }).format(d);
  return at.length > 10 ? `${day} ${at.slice(11, 16)}` : day;
}
function bookedCell(a) {
  const steps = nextSteps(a);
  const e = steps[0];
  if (!e) return "";
  const n = e.kind === "apply" && e.at ? daysTo(e.at) : null;   // how long is left to apply
  const left = n === null ? null : n < 0 ? t("{n} day late", { n: -n }, "{n} days late") : n === 0 ? t("Today") : t("{n} day left", { n }, "{n} days left");
  return h("span", { class: "c-booked" + (n !== null && n < 0 ? " late" : "") }, h("b", null, evTitle(e)), " ",
    h("span", { class: "c-when" }, bookedWhenOf(e)),
    left && h("span", { class: "c-left" }, " · " + left),
    steps.length > 1 && h("span", { class: "c-more" }, t("+{n} more", { n: steps.length - 1 })));
}
// for the calendar and the Gantt chart: from when it went in (or was added) to now, or to its last event once it is over
function appDated(a) {
  const co = (huntCompany(a.company) || {}).name || "";
  const start = a.applied || (a.created || "").slice(0, 10) || null;
  let end = huntOpen(a) ? today() : ((a.last && a.last.at) || a.updated || "").slice(0, 10) || start;
  if (start && end < start) end = start;
  const items = (a.schedule || []).filter((e) => e.at)
    .map((e) => ({ at: e.at, label: evTitle(e), sub: co, kind: e.kind === "apply" ? "deadline" : bookedPast(e) ? "past" : "event" }));
  return { start, end, cls: "s-" + a.status, items };
}

async function exportIcs(what) {
  try {
    const r = await api("POST", "/api/hunt/ics", { ...what, open: true });
    const file = r.path.split(/[\\/]/).pop();
    toast(t("Opened {file} in your calendar app", { file }), { ms: 7000, action: { label: t("Open folder"), fn: () => api("POST", "/api/reveal", { path: r.path }) } });
  } catch (e) { toast(e.message); }
}

async function bookedCall(method, path, body) {
  const before = H.view && H.view.application;
  try {
    H.view = await api(method, path, body);
    H.editing = null;
    paintHunt();
    loadHunt({ quiet: true });
    // booking a round moves the status forward (the server does it): say so, and offer it back
    const a = H.view.application;
    if (before && a && before.id === a.id && before.status !== a.status) {
      const was = before.status;
      toast(t("Status moved to {status}", { status: huntState(a.status) }), { ms: 7000, action: { label: t("Undo"), fn: () => setAppStatus(a, was) } });
    }
    return true;
  } catch (e) { toast(e.message); return false; }
}

// what comes next for a role in interviews (一面, then 二面…) or in tests (笔试): ready in the Add row, never booked by itself
const ROUNDS = ["round1", "round2", "round3", "final"];
function nextRound(a) {
  const had = new Set((a.schedule || []).map((e) => e.kind));
  const k = a.status === "interview" ? ROUNDS.find((x) => !had.has(x)) : a.status === "assessment" ? ["test", "assessment"].find((x) => !had.has(x)) : null;
  return k ? t(BOOKED_KINDS[k]) : "";
}

// the card on a role's page: what is booked, each with its own edit, and an Add row like the timeline's
function bookedCard(a) {
  const base = `/api/hunt/apps/${encodeURIComponent(a.id)}/schedule`;
  const list = a.schedule || [];
  const builtin = CHIP_KINDS.map((k) => t(BOOKED_KINDS[k]));
  const used = [...new Set(((H.data && H.data.applications) || []).flatMap((x) => (x.schedule || []).filter((e) => !e.kind && e.title).map((e) => e.title)))];
  const titles = [...builtin, ...used.filter((x) => !builtin.includes(x))];
  const chips = (input) => h("div", { class: "bk-chips", role: "group", "aria-label": t("Common ones") }, titles.map((x) =>
    h("button", { type: "button", class: "bk-chip" + (input.value === x ? " on" : ""), onclick: (e) => {
      input.value = x;
      for (const c of e.currentTarget.parentNode.children) c.classList.toggle("on", c === e.currentTarget);
      input.focus();
    } }, x)));
  const form = (e) => {
    const title = h("input", { type: "text", class: "bk-title", value: e ? evTitle(e) : nextRound(a), placeholder: t("What: 一面, 笔试…"), spellcheck: "false", "aria-label": t("What") });
    // no date until one is picked: a date filled in for the user reads like a date someone said
    const day = h("input", { type: "date", class: "bk-day", value: e ? e.at.slice(0, 10) : "", "aria-label": t("Date") });
    const time = h("input", { type: "time", class: "bk-time", value: e && e.at.length > 10 ? e.at.slice(11, 16) : "", "aria-label": t("Time") });
    const due = !!e && e.kind === "apply";   // applying: the date the posting gives, or 招满即止 when it says so
    let rolling = due && !!e.rolling;
    const roll = due && h("button", { type: "button", class: "dl-rolling" + (rolling ? " on" : ""), "aria-pressed": String(rolling), title: rollingText(),
      onclick: (ev) => { rolling = !rolling; ev.currentTarget.classList.toggle("on", rolling); ev.currentTarget.setAttribute("aria-pressed", String(rolling)); if (rolling) { day.value = ""; time.value = ""; } } },
      t("No date (until filled)"));
    if (due) day.addEventListener("change", () => { if (day.value && rolling) roll.click(); });   // a date it closes on: not 招满即止
    const fields = () => ({ title: title.value.trim(), at: day.value ? day.value + (time.value ? " " + time.value : "") : "", ...(due ? { rolling } : {}) });
    title.addEventListener("input", () => { const box = title.parentNode && title.parentNode.parentNode.querySelector(".bk-chips"); if (box) for (const c of box.children) c.classList.toggle("on", c.textContent === title.value); });
    if (!e) {   // a new one: what, when, Add
      const add = () => {
        const f = fields();
        if (!f.title) { title.focus(); return; }
        if (!day.value) { day.focus(); return; }
        bookedCall("POST", base, f);
      };
      title.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.isComposing) add(); });
      return h("div", { class: "bk-new" }, chips(title),
        h("div", { class: "row bk-row" }, title, day, time, h("span", { class: "spacer" }),
          h("button", { class: "btn sm primary", id: "bk-add", onclick: add }, icon("plus"), t("Add"))));
    }
    const minutes = h("input", { type: "number", min: "0", step: "15", class: "bk-min", value: e.minutes || "", placeholder: "60", "aria-label": t("Minutes") });
    const where = h("input", { type: "text", class: "bk-where", value: e.where || "", placeholder: t("Where: a room, a meeting link…"), spellcheck: "false", "aria-label": t("Where") });
    const note = h("textarea", { rows: 2, class: "bk-note", placeholder: t("Notes: who, what to prepare…"), spellcheck: "false", "aria-label": t("Notes") }, e.note || "");
    const save = () => {
      const f = fields();
      if (!f.title) { title.focus(); return; }
      if (!day.value && !due) { day.focus(); return; }
      bookedCall("PUT", `${base}/${encodeURIComponent(e.id)}`, { ...f, minutes: minutes.value || 0, where: where.value.trim(), note: note.value.trim() });
    };
    return h("div", { class: "bk-edit", "data-id": e.id }, !due && chips(title),
      h("div", { class: "row bk-row" }, title, day, time, roll,
        !due && h("label", { class: "bk-minl" }, minutes, h("span", { class: "muted" }, t("min")))),
      !due && where, note,
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", { class: "btn sm ghost", onclick: () => { H.editing = null; paintHunt(); } }, t("Cancel")),
        h("button", { class: "btn sm primary", onclick: save }, t("Save"))));
  };
  const row = (e) => h("div", { class: "bk-item" + (bookedPast(e) ? " past" : "") + (e.kind === "apply" ? " is-due" : ""), "data-id": e.id },
    h("span", { class: "bk-what" }, h("span", { class: "bk-line" }, h("b", null, evTitle(e)), h("span", { class: "bk-when" + (e.at ? "" : " muted") }, bookedWhenOf(e))),
      (e.minutes || e.where) && h("span", { class: "muted" }, [e.minutes ? t("{n} min", { n: e.minutes }) : "", e.where].filter(Boolean).join(" · ")),
      e.note && h("span", { class: "bk-notes" }, e.note)),
    h("span", { class: "bk-acts" },
      e.at && h("button", { class: "btn icon sm ghost", title: t("Add to my calendar"), "aria-label": t("Add to my calendar"), onclick: () => exportIcs({ app: a.id, event: e.id }) }, icon("calendar")),
      h("button", { class: "btn icon sm ghost", title: t("Edit"), "aria-label": t("Edit"), onclick: () => { H.editing = e.id; paintHunt(); } }, icon("pen")),
      h("button", { class: "btn icon sm ghost", title: t("Delete"), "aria-label": t("Delete"), onclick: async () => {
        if (await modal({ title: t("Delete “{what}”?", { what: evTitle(e) }), message: bookedWhenOf(e), ok: t("Delete"), danger: true }))
          bookedCall("DELETE", `${base}/${encodeURIComponent(e.id)}`);
      } }, icon("trash"))));
  return card(t("Events"), list.length ? t("{n} booked", { n: list.length }) : t("interviews, tests: anything with a date"), [
    list.length > 0 && h("div", { class: "bk-list" }, list.map((e) => (H.editing === e.id ? form(e) : row(e)))),
    form(null),
    (!applyEvent(a) || list.some((e) => e.at)) && h("div", { class: "row" },
      // made by the user, it opens for its date (or 招满即止) right away
      !applyEvent(a) && h("button", { class: "btn sm ghost", id: "bk-add-due", onclick: async () => {
        if (!(await bookedCall("POST", base, { kind: "apply" }))) return;
        const due = applyEvent(H.view.application);
        if (due) { H.editing = due.id; paintHunt(); }
      } }, icon("plus"), t("Add “{name}”", { name: t("Apply by") })),
      h("span", { class: "spacer" }),
      list.some((e) => e.at) && h("button", { class: "btn sm ghost", onclick: () => exportIcs({ app: a.id }) }, icon("calendar"), t("Add all to my calendar"))),
  ], { fold: "app-events" });
}

function appsTable() {
  const d = H.data;
  const names = Object.fromEntries(d.companies.map((c) => [c.id, c.name]));
  const all = [...d.applications].sort((a, b) => (names[a.company] || "").localeCompare(names[b.company] || "") || (a.order || 0) - (b.order || 0));
  const shown = all.filter((a) => matches(names[a.company], a.role, a.note, a.jd, a.batch, ...(a.schedule || []).map(evTitle), ...(a.places || []).map(placeText)));
  return [
    h("div", { class: "hunt-head" }, h("h1", null, t("Your applications")),
      h("p", null, t("Every role you apply for, wherever it goes in. Tell the AI on the left what happened (“Xinghe 一面过了”), or change it here."))),
    tableTools(
      h("button", { class: "btn sm primary", id: "hunt-new-app", onclick: newApplication }, icon("plus"), t("Add an application")),
      linkButton(),
      h("button", { class: "btn sm", id: "hunt-ics", title: t("Every interview, test and deadline as an .ics file, opened in your calendar app (Outlook…)"),
        disabled: !d.applications.some((a) => (a.schedule || []).some((e) => e.at)), onclick: () => exportIcs({}) }, icon("calendar"), t("Add to my calendar"))),
    dbView({ table: "apps", props: appProps(), rows: shown, rowId: (a) => a.id, open: (a) => openHunt("app", a.id), defaults: APP_VIEWS(), repaint: paintHunt,
      dated: appDated, located: appLocated, pin: pinPlace, sub: (a) => (huntCompany(a.company) || {}).name || "",
      empty: !d.applications.length
        ? h("div", null, h("div", { class: "t" }, t("No applications yet")),
          h("p", null, t("Add one here, or a company with the roles you apply for on its own site (网申).")))
        : t("Nothing matches") }),
  ];
}

// the resume a role goes out with (its own subset, else its company's), open in Resume
const roleSubset = (a) => a.subset || (huntCompany(a.company) || {}).subset || null;
function resumeButton(a) {
  const id = roleSubset(a);
  if (!id || !subsets().some((x) => x.id === id)) return null;
  return h("button", { class: "btn sm ghost hunt-resume", title: t("Open “{name}” in Resume", { name: subsetName(subsets().find((x) => x.id === id)) }),
    onclick: () => previewSubset(id) }, icon("file"), t("Resume"));
}

const today = () => dbvYmd(new Date());

// a company's logo (downloaded into the data folder by Claude's fill), or its first letter
// the company's real logo, or nothing: never a made-up stand-in. `slot` keeps an empty space (table rows line up).
function companyLogo(c, cls = "", slot = false) {
  const box = h("span", { class: "co-logo " + cls, "aria-hidden": "true" });
  const none = () => { if (slot) { box.classList.add("none"); box.replaceChildren(); } else box.remove(); };
  if (!c.logo_url) { if (!slot) return null; none(); return box; }
  box.append(h("img", { src: c.logo_url, alt: "", loading: "lazy", onerror: none }));
  return box;
}

// ---- open postings (docs/JOB_HUNT.md, 2026-10-18) ----
const scanInfo = () => (H.data && H.data.scan) || { running: {}, keywords: [] };
// read now, or asked for a moment ago (H.starting, until the server has the run)
const scanning = (cid) => !!(scanInfo().running || {})[cid] || H.starting === "all" || (Array.isArray(H.starting) && H.starting.includes(cid));
const scanQueued = (cid) => (scanInfo().queued || []).includes(cid);   // in the run, after the companies before it
const kwMatch = (title) => { const kw = scanInfo().keywords || []; return !kw.length || kw.some((k) => title.toLowerCase().includes(k.toLowerCase())); };

// the company's open postings, as one chip in the table (the ones matching the user's keywords)
// an empty Roles cell whose sites couldn't be read says so: "none yet" would read as "it has no openings"
function scanTrouble(c) {
  const ps = c.postings || {}, sc = ps.scan || {};
  const say = sc.at && sc.ok === false ? (sc.signed_out ? t("The site asks to sign in") : t("The last scan didn't work"))
    : ps.needs === "claude" ? t("A plain scan can't read its site")
    : ps.needs === "sites" ? t("No careers site to scan") : "";
  return say && h("span", { class: "fs-status warn" }, icon("alert"), say);
}

function openChip(c) {
  const ps = c.postings || {};
  const news = (ps.news || {}).new || 0;
  if (!ps.open) return null;
  return h("button", { class: "role-chip open-chip", title: t("{n} open on its sites", { n: ps.open }), onclick: () => openHunt("company", c.id) },
    icon("briefcase"), h("span", { class: "rc-name" }, t("{n} open", { n: ps.matching })), news > 0 && h("span", { class: "rc-new" }, t("{n} new", { n: news })));
}

// a scan started from the window or by Claude has ended (the browser it opened is closed by then): say what it found;
// the daily scan on opening says nothing, its news is in "Today"
function scanDone() {
  const run = scanInfo().last_run || {};
  const seen = H.lastRun;
  H.lastRun = run.at || 0;
  if (seen === undefined || !run.at || run.at === seen || run.auto) return;
  // counts only from sites that were read: a run that read none says why instead of "0 new"
  const parts = run.read === 0 ? [] : [t("{n} new", { n: run.new }), t("{n} closed", { n: run.closed })];
  if (run.failed) parts.push(t("{n} company couldn't be read", { n: run.failed }, "{n} companies couldn't be read"));
  if (run.needs_ai) parts.push(t("{n} company's site needs the AI to learn it first", { n: run.needs_ai }, "{n} companies' sites need the AI to learn them first"));
  if (run.no_site) parts.push(t("{n} company has no careers site", { n: run.no_site }, "{n} companies have no careers site"));
  toast(t("Scan finished: {what}", { what: parts.join(" · ") }), { ms: 6000 });
}

async function scanCompanies(ids = null) {
  H.starting = ids || "all";   // the button spins from the click, not from the server's first word
  paintHunt();
  try {
    await api("POST", "/api/hunt/scan", ids ? { companies: ids } : {});
    H.starting = null;
    await loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
  finally { if (H.starting) { H.starting = null; paintHunt(); } }
}

// while a run goes on: how far it got, counted the whole time (not only while a company is being read)
function scanAllButton() {
  const sc = scanInfo(), b = sc.batch;
  const busy = !!b || H.starting === "all" || Object.keys(sc.running || {}).length > 0;
  const now = H.data.companies.filter((c) => (sc.running || {})[c.id]).map((c) => c.name);
  return h("button", { class: "btn sm", id: "hunt-scan-all", "aria-busy": busy && "true", disabled: busy,
    title: now.length ? t("Reading {names}", { names: now.join(", ") }) : sc.last_all ? t("Last scanned {when}", { when: huntWhen(sc.last_all) }) : t("Reads every company's careers sites for open roles"),
    onclick: () => scanCompanies() }, icon("refresh"),
  !busy ? t("Scan all") : b && b.total > 1 ? t("Scanning {n} of {total}…", { n: Math.min(b.done + 1, b.total), total: b.total }) : t("Scanning…"));
}

// what the last day's scans brought, and the sites Claude still has to learn once
function scanNews() {
  const d = H.data;
  const names = Object.fromEntries(d.companies.map((c) => [c.id, c]));
  const learn = d.companies.filter((c) => (c.postings || {}).needs === "claude");
  const noSites = d.companies.filter((c) => (c.postings || {}).needs === "sites");
  // "nothing new" beside a scan that didn't work would read as "read fine, found nothing"
  const broke = d.companies.filter((c) => { const sc = (c.postings || {}).scan || {}; return sc.at && sc.ok === false && !scanning(c.id); });
  const readToday = d.companies.some((c) => { const sc = (c.postings || {}).scan || {}; return sc.ok && Date.now() - Date.parse(sc.at) < 864e5; });
  const rows = (d.changes || []).map((ch) => {
    const c = names[ch.company];
    if (!c) return null;
    const parts = [ch.new.length && t("{n} new", { n: ch.new.length }), ch.closed.length && t("{n} closed", { n: ch.closed.length }),
      ch.changed.length && t("{n} changed", { n: ch.changed.length })].filter(Boolean);
    return h("div", { class: "news-row" },
      h("button", { class: "linkish news-co", onclick: () => openHunt("company", c.id) }, companyLogo(c), c.name),
      h("span", { class: "muted" }, parts.join(" · ")),
      h("span", { class: "news-items" }, ch.new.slice(0, 4).map((p) => h("span", { class: "news-item" }, p.title)),
        ch.new.length > 4 && h("span", { class: "muted" }, t("and {n} more", { n: ch.new.length - 4 }))));
  }).filter(Boolean);
  const kw = scanInfo().keywords || [];
  const kwInput = h("input", { class: "kw-input", id: "hunt-keywords", value: kw.join(", "), spellcheck: "false",
    placeholder: t("e.g. 算法, AI, Agent, 后端"), "aria-label": t("Roles you look for"),
    onchange: async (e) => { try { await api("PUT", "/api/hunt/scan-settings", { keywords: e.target.value }); await loadHunt({ quiet: true }); } catch (err) { toast(err.message); } } });
  return h("div", { class: "scan-news" },
    // "nothing new" only after a scan that read a site; with none read, the bars below say why
    h("div", { class: "sn-head" }, h("b", null, t("Today")), h("span", { class: "muted" }, rows.length || !readToday ? "" : t("nothing new from the last day's scans")),
      h("span", { class: "spacer" }), h("label", { class: "kw-label", for: "hunt-keywords" }, t("Roles you look for")), kwInput),
    rows,
    learn.length > 0 && h("div", { class: "note-bar" }, icon("sparkle"), h("span", { class: "nb-text" },
      t("{names}: the AI reads the site once and records how, then scans need no AI.", { names: learn.map((c) => c.name).join(", ") })),
      h("button", { class: "btn sm", "data-ai": aiKey("learn", learn), disabled: !ui.chat.available, onclick: () => askLearn(learn) }, icon("sparkle"), t("Let AI learn them"))),
    noSites.length > 0 && h("div", { class: "note-bar" }, icon("alert"), h("span", { class: "nb-text" },
      t("{names}: no careers site known yet.", { names: noSites.map((c) => c.name).join(", ") })),
      h("button", { class: "btn sm", "data-ai": aiKey("fill", noSites), disabled: !ui.chat.available, onclick: () => askFill(noSites) }, icon("sparkle"), t("Ask AI to find them"))),
    broke.length > 0 && h("div", { class: "note-bar warn" }, icon("alert"), h("span", { class: "nb-text" },
      t("{names}: the last scan didn't work; their pages say why.", { names: broke.map((c) => c.name).join(", ") }))));
}

// a button's key for asking the AI about these companies: one company's buttons share it, wherever they are
const aiKey = (what, cs) => what + ":" + cs.map((c) => c.id).join(",");

function askLearn(cs) {
  aiRun(aiKey("learn", cs), () => sendChat(t("Find the open roles on the careers sites of {names}, and record a scan route for each so later scans need no AI.", { names: cs.map((c) => c.name).join(", ") })));
}

// a company's open postings, on its page
function postingsCard(v) {
  const c = v.company;
  const sc = v.scan || {};
  const all = v.postings || [];
  const open = all.filter((p) => !p.closed);
  const closed = all.filter((p) => p.closed);
  const fresh = new Set(sc.new || []);
  const q = (H.pq || "").trim().toLowerCase();
  const showAll = !!H.pall;
  const shown = open.filter((p) => (showAll || kwMatch(p.title)) && (!q || [p.title, p.location, p.category].join(" ").toLowerCase().includes(q)));
  const hidden = open.length - open.filter((p) => kwMatch(p.title)).length;
  const limit = H.plimit || 120;
  const busy = scanning(c.id) || sc.running;
  const waiting = !busy && (sc.queued || scanQueued(c.id));
  const status = busy ? h("span", { class: "fs-status" }, h("span", { class: "spin" }), t("Scanning…"))
    : waiting ? null   // the Scan button says it, spinning
    // a scan that stopped because it can't read the site records no time: say why, not "not scanned yet"
    : !sc.at && sc.needs === "claude" ? h("span", { class: "fs-status warn" }, icon("alert"), t("A plain scan can't read its site"))
    : !sc.at && sc.needs === "sites" ? h("span", { class: "fs-status warn" }, icon("alert"), t("No careers site to scan"))
    : !sc.at ? h("span", { class: "muted" }, t("not scanned yet"))
    : sc.ok ? h("span", { class: "muted" }, t("scanned {when} · {how}", { when: huntWhen(sc.at), how: howName(sc.how) }))
    : h("span", { class: "fs-status bad" }, icon("alert"), sc.signed_out ? t("The site asks to sign in") : t("The last scan didn't work"));
  const row = (p) => h("div", { class: "post-row" + (p.closed ? " closed" : ""), "data-id": p.id },
    h("div", { class: "pr-main" },
      h("a", { class: "pr-title", href: p.url || null, "data-ext": p.url ? "" : null }, p.title),
      fresh.has(p.id) && h("span", { class: "chip new-chip" }, t("new")),
      p.changed && p.changed.deadline && h("span", { class: "chip", title: p.changed.deadline.join(" → ") }, t("deadline changed")),
      // one line, one separator: the deadline is a part like the others, only coloured when it has passed
      h("span", { class: "pr-meta" }, [p.location, p.category, p.batch && t(BATCH_NAMES[p.batch] || p.batch),
        p.deadline && h("span", { class: "c-due" + (p.deadline < today() ? " late" : "") }, t("closes {date}", { date: p.deadline }))]
        .filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x])))),
    // added already: a tick and where it stands, so it never reads like the "+ Want to apply" next to it
    p.app ? h("button", { class: "btn sm ghost pr-added", title: t("In your applications: open it"), "aria-label": t("In your applications: open it"), onclick: () => openHunt("app", p.app) },
      icon("check"), hChip(((v.applications || []).find((a) => a.id === p.app) || {}).status || "saved"))
      : !p.closed && h("button", { class: "btn sm", title: t("Add it to your applications"), onclick: () => applyPosting(p) }, icon("plus"), t("Want to apply")));
  const search = searchBox("posting-search", H.pq || "", t("Search these roles"), (v) => { H.pq = v; });
  // the head carries the count, when it was read and Scan: all still at hand with the list folded away
  const acts = [status,
    sc.needs === "claude" && h("button", { class: "btn sm", "data-ai": aiKey("learn", [c]), disabled: !ui.chat.available, onclick: () => askLearn([c]) }, icon("sparkle"), t("Let AI learn its site")),
    !sc.ok && sc.at && sc.how === "route" && h("button", { class: "btn sm", "data-ai": aiKey("learn", [c]), disabled: !ui.chat.available, onclick: () => askLearn([c]) }, icon("sparkle"), t("Ask AI to record it again")),
    h("button", { class: "btn sm", id: "company-scan", "aria-busy": (busy || waiting) && "true", disabled: !!busy || waiting || sc.needs === "sites",
      title: sc.needs === "sites" ? t("Add a careers site below first") : waiting ? t("Waiting for the companies before it…") : null,
      onclick: () => scanCompanies([c.id]) }, icon("refresh"), busy ? t("Scanning…") : waiting ? t("Waiting…") : t("Scan"))];
  return card(t("Open roles"), "", [
    !sc.ok && sc.error && h("div", { class: "note-bar warn" }, icon("alert"), h("span", { class: "nb-text" }, sc.error),
      sc.signed_out && c.site && h("button", { class: "btn sm", onclick: () => signInMode(c.site) }, icon("user"), t("Sign in to {site}", { site: c.name }))),
    sc.needs === "sites" && h("p", { class: "muted card-p" }, t("No careers site known yet: ask the AI to fill the company in, or type one below.")),
    // read fine and found nothing: say so, or an empty card looks like a scan that never ran
    sc.ok && !busy && !open.length && h("p", { class: "muted card-p", id: "company-no-postings" },
      t("Its sites list no open roles right now. If you know they hire, check the addresses under Its careers sites.")),
    open.length > 0 && h("div", { class: "row wrap post-tools" }, search, hidden > 0 && h("button", { class: "btn sm ghost", onclick: () => { H.pall = !H.pall; paintHunt(); } },
      showAll ? t("Only the roles you look for") : t("Show all ({n} more)", { n: hidden }))),
    shown.length ? h("div", { class: "post-list" }, shown.slice(0, limit).map(row),
      shown.length > limit && h("button", { class: "btn sm ghost", onclick: () => { H.plimit = limit + 200; paintHunt(); } }, t("Show {n} more", { n: Math.min(200, shown.length - limit) })))
      : open.length > 0 && h("p", { class: "muted card-p" }, t("Nothing here matches. Change the roles you look for on the Companies tab, or show all.")),
    closed.length > 0 && h("details", { class: "post-closed" }, h("summary", null, icon("down"), t("{n} closed", { n: closed.length })), h("div", { class: "post-list" }, closed.slice(0, 200).map(row))),
  ], { fold: "company-postings", count: open.length || null, actions: acts });
}

const BATCH_NAMES = { campus: "Campus", intern: "Internship", social: "Experienced", other: "Other" };
function howName(how) {
  return String(how || "").split("+").map((x) => (x === "route" ? t("its recorded route") : x === "claude" ? t("read by AI") : x)).join(" + ");
}

async function applyPosting(p) {
  try {
    const v = await api("POST", `/api/hunt/postings/${encodeURIComponent(p.id)}/apply`);
    await loadHunt({ quiet: true });
    toast((v.reading || {}).running ? t("Added to your applications: {role}. Its description is being read from its page.", { role: v.application.role })
      : t("Added to your applications: {role}", { role: v.application.role }),
    { ms: 6000, action: { label: t("Open"), fn: () => openHunt("app", v.application.id) } });
  } catch (e) { toast(e.message); }
}

// a role as a chip, like a relation in a table: it opens the role
function roleChip(a) {
  return h("button", { class: "role-chip s-" + a.status, title: huntState(a.status), onclick: () => openHunt("app", a.id) },
    h("span", { class: "rc-dot" }), h("span", { class: "rc-name" }, a.role));
}

function companyProps() {
  const text = (key, label, width, extra = {}) => ({ key, label, type: "text", width, get: (c) => c[key] || "", set: (c, v) => cellCompany(c, { [key]: v }), ...extra });
  return [
    { key: "name", label: t("Company"), type: "title", width: 230, get: (c) => c.name,
      render: (c) => h("span", { class: "co-name" }, companyLogo(c, "", true),
        h("span", { class: "co-text" }, h("span", { class: "c-main-name" }, c.name),
          (c.aliases || []).length > 0 && h("span", { class: "c-alias" }, c.aliases.join(" · ")))) },
    text("industry", t("Industry"), 150, { group: true }),
    text("strength", t("Strength"), 200),
    { key: "roles", label: t("Roles"), type: "relation", width: 300, get: (c) => (c.apps || []).map((a) => a.role),
      // a scan going on comes first: the chips after it may be cut off at the cell's edge
      render: (c) => h("div", { class: "role-chips" },
        scanning(c.id) ? h("span", { class: "scan-chip" }, h("span", { class: "spin" }), t("Scanning…"))
          : scanQueued(c.id) && h("span", { class: "scan-chip c-muted" }, t("Waiting…")),
        (c.apps || []).map(roleChip), openChip(c),
        !scanning(c.id) && !scanQueued(c.id) && !(c.apps || []).length && !openChip(c) && (scanTrouble(c) || h("span", { class: "c-muted" }, t("none yet")))) },
    placesProp("places", t("Hires in"), (c, v) => cellCompany(c, { places: v })),
    { key: "hq", label: t("Headquarters"), type: "custom", width: 130, hidden: true, group: true, get: (c) => (c.hq ? placeName(c.hq) : ""),
      render: (c) => c.hq ? placeChip(c.hq) : "",
      edit: (td, c) => {
        let next;
        const ed = placesEditor(c.hq ? [c.hq] : [], (v) => { next = v; }, { single: true });
        openFloat(td, h("div", { class: "menu dbv-pop pl-pop" }, ed), { minWidth: Math.max(td.offsetWidth, 360), focus: ed.querySelector(".pl-input"),
          onClose: () => { if (next) cellCompany(c, { hq: next[0] || "" }); dbvAfterClose(); } });
      } },
    text("size", t("Size"), 120, { hidden: true }),
    text("site", t("Careers site"), 220, { hidden: true }),
    text("note", t("Notes"), 220, { hidden: true }),
  ];
}

function companiesTable() {
  const d = H.data;
  const shown = d.companies.filter((c) => matches(c.name, (c.aliases || []).join(" "), c.industry, c.strength, c.note, c.hq ? placeName(c.hq) : "", ...(c.places || []).map(placeText)));
  return [
    h("div", { class: "hunt-head" }, h("h1", null, t("Companies")),
      h("p", null, t("What each company is and how it is doing, your notes, and its own careers site: one profile there, several roles in your order of preference."))),
    tableTools(h("button", { class: "btn sm primary", id: "hunt-new-company", onclick: () => newCompany() }, icon("plus"), t("Add a company")), linkButton(),
      d.companies.length > 0 && scanAllButton()),
    d.companies.length > 0 && scanNews(),
    languagesBar(d.companies),
    dbView({ table: "companies", props: companyProps(), rows: shown, rowId: (c) => c.id, open: (c) => openHunt("company", c.id),
      defaults: [{ id: "v1", name: t("All companies") }, { id: "v2", name: t("Map"), layout: "map" }], repaint: paintHunt,
      located: companyLocated, pin: pinPlace, sub: (c) => c.industry || "",
      empty: !d.companies.length
        ? h("div", null, h("div", { class: "t" }, t("No companies yet")), h("p", null, t("Add the companies you're applying to; the AI can tell you about each one.")))
        : t("Nothing matches") }),
  ];
}

function platformsTable() {
  const sites = F.sites || [];
  const apps = (H.data && H.data.applications) || [];
  if (!F.sites) { loadFill(); return h("div", { class: "muted" }, t("Loading…")); }
  const host = (s) => (s.home || "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
  // a check that didn't finish comes first: "Not read yet" or "In step" would hide it
  const profile = (s) => s.check_error ? "failed" : !s.synced_at ? "unread" : s.conflicts ? "conflicts" : s.changes ? "changes" : "step";
  const PROFILE = { failed: t("The last check didn't finish."), unread: t("Not read yet"), conflicts: t("Has conflicts"), changes: t("Changes to push"), step: t("In step") };
  const nApps = (s) => apps.filter((a) => a.channel === "platform" && a.platform === s.id).length;
  const props = [
    { key: "name", label: t("Platform"), type: "title", width: 200, get: (s) => s.name,
      render: (s) => h("span", { class: "co-text" }, h("span", { class: "c-main-name" }, s.name), h("span", { class: "c-alias" }, host(s))) },
    { key: "profile", label: t("Your profile there"), type: "custom", width: 180, group: true, get: (s) => PROFILE[profile(s)],
      render: (s) => s.check_error ? h("span", { class: "fs-status bad", title: s.check_error }, icon("alert"), PROFILE.failed)
        : h("span", { class: "c-muted" }, !s.synced_at ? t("Not read yet") : s.conflicts ? t("{n} conflict", { n: s.conflicts }, "{n} conflicts")
        : s.changes ? t("{n} change to push", { n: s.changes }, "{n} changes to push") : t("In step")) },
    { key: "signin", label: t("Sign-in"), type: "custom", width: 130, get: (s) => (s.signed_out ? t("Sign in") : s.synced_at ? t("Signed in") : ""),
      render: (s) => s.signed_out ? h("span", { class: "state-chip s-warn" }, t("Sign in")) : h("span", { class: "c-muted" }, s.synced_at ? t("Signed in") : "") },
    { key: "apps", label: t("Applications"), type: "custom", width: 120, get: (s) => nApps(s) || "", render: (s) => h("span", { class: "c-muted" }, String(nApps(s) || "")) },
    { key: "checked", label: t("Last checked"), type: "custom", width: 140, get: (s) => s.synced_at || "", render: (s) => h("span", { class: "c-muted" }, s.synced_at ? relTime(s.synced_at) : "") },
  ];
  return [
    h("div", { class: "hunt-head" }, h("h1", null, t("Platforms")),
      h("p", null, t("Your profiles on job platforms. Resume Studio keeps a copy of each, in step with the site: open one to see it field by field."))),
    tableTools(h("button", { class: "btn sm primary", id: "hunt-new-platform", onclick: () => siteDialog(null) }, icon("plus"), t("Add a site"))),
    sites.length ? dbView({ table: "platforms", props, rows: sites.filter((s) => matches(s.name, s.home)), rowId: (s) => s.id,
      open: (s) => openHunt("platform", s.id), defaults: [{ id: "v1", name: t("All platforms") }], repaint: paintHunt, empty: t("Nothing matches") }) : noSitesCard(),
  ];
}

// ================================================================ a small form dialog (several fields, nothing saved before OK)
// `onOk(values, m)`, when given, saves while the form stays open: m.close(result), m.notice(node) shows something
// above the buttons (cleared when a field changes), m.retry(extra) runs onOk again with more values
function formModal({ title, message = "", fields, ok = t("OK"), onOk = null }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const inputs = {};
    const slot = h("div", { class: "fm-notice-slot" });
    let busy = false;
    const close = (val) => { root.replaceChildren(); document.removeEventListener("keydown", onKey, true); resolve(val); };
    const run = async (out) => {
      if (busy) return;
      busy = true;
      try { await onOk(out, { close, notice: (node) => slot.replaceChildren(node), retry: (extra) => run({ ...out, ...extra }) }); }
      finally { busy = false; }
    };
    const done = () => {
      const out = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()]));
      const missing = fields.find((f) => f.required && !out[f.key]);
      if (missing) { inputs[missing.key].focus(); return; }
      if (onOk) run(out); else close(out);
    };
    const suggests = [];
    const onKey = (e) => {
      const open = suggests.find((s) => !s.hidden);
      if (e.key === "Escape") { e.stopPropagation(); if (open) { open.hidden = true; return; } close(null); }
      if (e.key === "Enter" && e.target.closest(".fm-suggest")) return;   // the option's own click picks it
      if (e.key === "Enter" && !e.isComposing && e.target.tagName !== "TEXTAREA") { e.preventDefault(); done(); }
    };
    document.addEventListener("keydown", onKey, true);
    // a field with known values (the companies) suggests them as you type: our own list, never a native one
    const suggest = (f, input) => {
      const box = h("div", { class: "listbox fm-suggest", role: "listbox", id: "fm-" + f.key + "-list", "aria-label": f.label, hidden: true });
      const paint = () => {
        const q = input.value.trim().toLowerCase();
        const hits = f.list.filter((x) => x.toLowerCase() !== q && (!q || x.toLowerCase().includes(q))).slice(0, 8);
        box.replaceChildren(...hits.map((x) => h("button", { type: "button", role: "option", class: "lb-opt", tabindex: "-1",
          onmousedown: (e) => e.preventDefault(), onclick: () => { input.value = x; box.hidden = true; input.focus(); } }, x)));
        box.hidden = !hits.length;
      };
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-controls", box.id);
      input.addEventListener("input", paint);   // only while typing (or ↓): a dialog that just opened still closes with one Esc
      input.addEventListener("blur", () => setTimeout(() => { if (!box.contains(document.activeElement)) box.hidden = true; }, 0));
      input.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowDown") return;
        if (box.hidden) paint();
        const first = !box.hidden && box.querySelector(".lb-opt");
        if (first) { e.preventDefault(); first.focus(); }
      });
      box.addEventListener("keydown", (e) => { menuKeys(e, box, ".lb-opt"); if (e.key === "Tab") box.hidden = true; });
      box.addEventListener("focusout", () => setTimeout(() => { if (!box.contains(document.activeElement) && document.activeElement !== input) box.hidden = true; }, 0));
      suggests.push(box);
      return box;
    };
    const rows = fields.map((f) => {
      inputs[f.key] = h("input", { id: "fm-" + f.key, value: f.value || "", placeholder: f.placeholder || "", spellcheck: "false", autocomplete: "off",
        oninput: () => slot.replaceChildren() });
      return h("div", { class: "field" + (f.list && f.list.length ? " fm-combo" : "") }, h("label", { for: "fm-" + f.key }, f.label, f.hint && h("span", { class: "hint" }, f.hint)), inputs[f.key],
        f.list && f.list.length && suggest(f, inputs[f.key]));
    });
    root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(null); } },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
        h("h3", null, title), message && h("p", null, message), rows, slot,
        h("div", { class: "acts" }, h("button", { class: "btn", onclick: () => close(null) }, t("Cancel")),
          h("button", { class: "btn primary", onclick: done }, ok)))));
    setTimeout(() => (inputs[fields.find((f) => !f.value)?.key || fields[0].key]).focus(), 0);
  });
}

async function newCompany(name = "") {
  const made = await formModal({ title: t("Add a company"), ok: t("Add"), fields: [
    { key: "name", label: t("Company"), value: name, required: true, placeholder: t("e.g. 星河科技") },
    { key: "site", label: t("Careers site"), hint: t("optional: the AI finds it"), placeholder: "https://careers.example.com/campus" }],
    onOk: async (f, m) => {
      try { m.close((await api("POST", "/api/hunt/companies", f)).company); }
      catch (e) {
        if (!sameCompany(e, f.name, m, { label: (c) => t("Open {company}", { company: c.name }), pick: (c) => { m.close(null); openHunt("company", c.id); } })) toast(e.message);
      }
    } });
  if (!made) return null;
  await loadHunt({ quiet: true });
  openHunt("company", made.id);
  askFill([made]);
  return made;
}

// a new company's name that is on the list already (by its name or an alias) or close to one: the form says
// which, and the user picks it or says it is another company (a region's arm, 麦当劳中国, never asks: store.py)
function sameCompany(e, name, m, { label, pick }) {
  if (e.status !== 409 || !e.data) return false;
  const { same, close } = e.data;
  const when = (c) => (c.created ? t("added {date}", { date: c.created.slice(0, 10) }) : "");
  const row = (c) => h("li", null, h("span", { class: "fm-same-name" }, c.name, c.aliases.length ? h("span", { class: "hint" }, " · " + c.aliases.join(", ")) : null),
    h("span", { class: "hint" }, when(c)), h("button", { class: "btn sm", type: "button", onclick: () => pick(c) }, label(c)));
  m.notice(h("div", { class: "fm-notice", role: "alert" },
    h("p", null, same ? t("“{name}” is already on the list:", { name }) : t("“{name}” may be a company you already added:", { name })),
    h("ul", { class: "fm-same" }, ...(same ? [same] : close).map(row)),
    !same && h("div", { class: "fm-notice-acts" }, h("button", { class: "btn sm", type: "button", onclick: () => m.retry({ different: true }) }, t("It's another company: add it")))));
  return true;
}

// a posting's link: Claude reads it and fills in the company (new or known) and the role (docs/JOB_HUNT.md, 2026-10-18)
function linkButton() {
  return h("button", { class: "btn sm", id: "hunt-from-link", disabled: !ui.chat.available,
    title: ui.chat.available ? t("Paste a posting's link: the AI reads it and fills in the company and the role") : missingAgentText(),
    onclick: addFromLink }, icon("link"), t("Add from a link"));
}

async function addFromLink() {
  const f = await formModal({ title: t("Add from a link"), ok: t("Read it"),
    message: t("The link to a posting. The AI reads it, adds its company (and finds out about it, when it is new) and the role, with its deadline."),
    fields: [{ key: "url", label: t("Link"), required: true, placeholder: "https://careers.example.com/campus/job/12345" }] });
  if (!f) return;
  let url = f.url.trim();
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  const same = ((H.data && H.data.applications) || []).find((a) => a.url && a.url.replace(/\/$/, "") === url.replace(/\/$/, ""));
  if (same) { toast(t("That posting is already on the list")); openHunt("app", same.id); return; }
  aiRun("add-link", () => sendChat(t("Read this posting and add it: {url}", { url })));
}

async function newApplication(companyId = null) {
  const cs = (H.data && H.data.companies) || [];
  const fixed = companyId ? huntCompany(companyId) : null;
  const v = await formModal({ title: t("Add an application"), ok: t("Add"), fields: [
    ...(fixed ? [] : [{ key: "company", label: t("Company"), required: true, list: cs.map((c) => c.name), placeholder: t("e.g. 星河科技") }]),
    { key: "role", label: t("Role"), required: true, placeholder: t("e.g. 服务端研发工程师") },
    { key: "url", label: t("Link"), hint: t("optional"), placeholder: "https://…" }],
    onOk: async (f, m) => {
      try {
        let cid = fixed ? fixed.id : f.cid || (cs.find((c) => c.name.toLowerCase() === f.company.toLowerCase()) || {}).id;
        if (!cid) {
          try { const made = (await api("POST", "/api/hunt/companies", { name: f.company, different: !!f.different })).company; cid = made.id; askFill([made]); }
          catch (e) {
            if (e.status === 409 && e.data && e.data.same) cid = e.data.same.id;   // one of its other names: that company
            else if (sameCompany(e, f.company, m, { label: (c) => t("Use {company}", { company: c.name }), pick: (c) => m.retry({ cid: c.id }) })) return;
            else throw e;
          }
        }
        m.close(await api("POST", `/api/hunt/companies/${encodeURIComponent(cid)}/roles`, { role: f.role, url: f.url }));
      } catch (e) { toast(e.message); }
    } });
  if (!v) return;
  await loadHunt({ quiet: true });
  if (!companyId) openHunt("app", v.application.id);
}

// ================================================================ a company's page
async function editCompany(id, fields, { repaint = true } = {}) {
  try {
    H.view = await api("PUT", `/api/hunt/companies/${encodeURIComponent(id)}`, fields);
    if (H.data) H.data.companies = H.data.companies.map((c) => (c.id === id ? { ...c, ...H.view.company } : c));
    if (repaint) paintHunt();
    return true;
  } catch (e) { toast(e.message); if (repaint) paintHunt(); return false; }
}

// a text field on a page: saved when it changes, without repainting (the caret stays)
function hfield(label, value, save, { placeholder = "", hint = "", multiline = false, type = "text", id = null, link = false } = {}) {
  const attrs = { value: value || "", placeholder, spellcheck: "false", id,
    onchange: (e) => { const v = e.target.value.trim(); if (v !== (value || "")) { value = v; save(v); } } };
  const input = multiline ? h("textarea", { ...attrs, rows: 2 }, value || "") : h("input", { ...attrs, type });
  return h("div", { class: "field" }, h("label", null, label, hint && h("span", { class: "hint" }, hint)), link ? linkOpen(input) : input);
}

function backTo(tab) {
  const label = { apps: t("All applications"), companies: t("All companies"), platforms: t("All platforms") }[tab];
  return h("button", { class: "btn sm ghost hunt-back", onclick: () => setHuntTab(tab) }, icon("chevLeft"), label);
}

async function openOnSite(url) {
  if (!/^https?:\/\//.test(url || "")) return;
  try {
    await api("POST", "/api/browser/do", { cmd: "open", url });
    await api("POST", "/api/browser/do", { cmd: "front" });
  } catch (e) { toast(e.message, { ms: 7000 }); }
}

function companyPage(v) {
  const c = v.company;
  const apps = v.applications;
  const save = (key) => (val) => editCompany(c.id, { [key]: val }, { repaint: false }).then((ok) => {
    if (ok && key === "name") { const hd = $(".hunt-wrap .ed-head h1"); if (hd) hd.textContent = val; }
  });
  const choices = subsets().filter((x) => x.id !== ALL);
  const pick = uiSelect({ id: "company-subset", label: t("Subset"), menuWidth: 300, value: c.subset || null, placeholder: t("Pick a subset"),
    options: choices.map((x) => ({ value: x.id, label: subsetName(x), hint: (versionStatus(x.id) || {}).latest ? "v" + versionStatus(x.id).latest : "" })),
    onChange: (val) => editCompany(c.id, { subset: val }) });
  const st = c.subset ? versionStatus(c.subset) : null;
  const siteApps = apps.filter((a) => a.channel === "site");
  const unsent = siteApps.filter((a) => !(a.sent || []).length);
  const add = h("input", { class: "su-add-input", id: "company-add-role", placeholder: t("Add a role and press Enter (several at once: separate them with commas)"), spellcheck: "false",
    onkeydown: async (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      const names = e.target.value.split(/[\n,，;；]/).map((x) => x.trim()).filter(Boolean);
      if (!names.length) return;
      e.target.disabled = true;
      for (const role of names) {
        try { await api("POST", `/api/hunt/companies/${encodeURIComponent(c.id)}/roles`, { role }); }
        catch (err) { toast(err.message); }
      }
      await loadHunt({ quiet: true });
      const again = $("#company-add-role"); if (again) again.focus();
    } });
  const roles = apps.map((a, i) => h("div", { class: "su-role", "data-id": a.id },
    h("span", { class: "su-n" }, a.channel === "site" ? a.order : "·"),
    h("button", { class: "su-main linkish", onclick: () => openHunt("app", a.id) },
      h("span", { class: "su-name" }, a.role), h("span", { class: "su-url" }, channelText(a), a.sent && a.sent.length ? " · " + sentText(a.sent[a.sent.length - 1]) : "")),
    hChip(a.status),
    h("span", { class: "su-acts" },
      h("button", { class: "btn icon sm ghost", title: t("Move up"), "aria-label": t("Move up"), disabled: i === 0, onclick: () => moveApp(a, i) }, icon("up")),
      h("button", { class: "btn icon sm ghost", title: t("Move down"), "aria-label": t("Move down"), disabled: i === apps.length - 1, onclick: () => moveApp(a, i + 2) }, icon("down")))));
  const ab = c.about;
  return [
    backTo("companies"),
    h("div", { class: "ed-head" }, companyLogo(c, "lg"), h("h1", null, c.name), h("span", { class: "id" }, c.id), h("span", { class: "ed-acts" },
      h("button", { class: "btn sm", id: "company-fill", "data-ai": aiKey("fill", [c]), disabled: !ui.chat.available, onclick: () => askFill([c]) }, icon("sparkle"),
        filledSome(c) ? t("Ask AI to fill it in again") : t("Ask AI to fill it in")),
      c.site && h("button", { class: "btn sm", title: c.site, onclick: () => openInBrowser(c.site) }, icon("external"), t("Open its site")),
      h("button", { class: "btn icon ghost", title: t("More"), "aria-label": t("More"), onclick: (e) => openMenu(e.currentTarget, [
        c.site && { label: t("Open it in Resume Studio's browser"), icon: "globe", onClick: () => openOnSite(c.site) },
        c.site && { label: t("Sign in to it in Resume Studio's browser"), icon: "user", onClick: () => signInMode(c.site) },
        { label: c.logo ? t("Change the logo…") : t("Add a logo…"), icon: "file", onClick: () => pickLogo(c) },
        c.logo && { label: t("Remove the logo"), icon: "x", onClick: () => removeLogo(c) },
        (c.kept || []).length > 0 && { label: t("Let AI fill the fields you edited too"), icon: "sparkle", onClick: () => editCompany(c.id, { kept: [] }) },
        "sep",
        { label: t("Delete this company"), icon: "trash", danger: true, onClick: () => deleteCompany(c, apps.length) }], { align: "end" }) }, icon("more")))),
    card(t("Roles"), siteApps.length ? t("on its own site, in your order of preference") : t("the roles you apply for here"), [
      roles.length ? h("div", { class: "su-roles" }, roles) : h("p", { class: "muted card-p" }, t("No roles yet: add the ones you apply for below.")),
      add,
    ], { fold: "company-roles" }),
    postingsCard(v),
    card(t("Its careers site"), t("one profile there, filled from a subset"), [
      h("div", { class: "field" }, h("label", null, t("Its careers sites"), h("span", { class: "hint" }, keptHint(c, "sites") || t("scanned for open roles"))), sitesEditor(c)),
      h("div", { class: "grid2" }, hfield(t("Where you sign up"), c.site, save("site"), { placeholder: "https://careers.example.com/campus", hint: keptHint(c, "site"), link: true }),
        hfield(t("Account"), c.account, save("account"), { placeholder: t("e.g. the phone or email you signed up with"), hint: t("never a password") })),
      h("div", { class: "field" }, h("label", null, t("Filled from"), h("span", { class: "hint" }, t("the subset this site gets"))), h("div", { class: "su-subset" }, pick.el,
        st && h("span", { class: "chip" + (st.latest && !st.same_as ? " vd-changed" : "") }, !st.latest ? t("No versions yet") : st.same_as ? `v${st.same_as}` : t("v{n} · changed", { n: st.latest })),
        c.subset && c.subset_exists && h("button", { class: "btn sm ghost", onclick: () => previewSubset(c.subset) }, icon("file"), t("Preview")))),
      c.subset && !c.subset_exists && h("p", { class: "muted card-p" }, t("This subset is gone; pick another.")),
      h("p", { class: "card-p muted" }, t("When you submit the sign-up on the site, mark it here: the subset is kept as a version (if it changed) and pinned on every role there that hasn't gone out yet.")),
      h("div", { class: "row" }, h("button", { class: "btn primary", id: "company-send", disabled: !(c.subset && c.subset_exists && unsent.length), onclick: () => sendCompany(c, unsent) },
        icon("check"), unsent.length ? t("Mark as sent ({n})", { n: unsent.length }) : t("Mark as sent")),
        siteApps.length > 0 && !unsent.length ? h("span", { class: "muted" }, t("Every role here has gone out."))
          : !siteApps.length ? h("span", { class: "muted" }, t("First add the roles you apply for on its site, under Roles."))
          : !(c.subset && c.subset_exists) && h("span", { class: "muted" }, t("First pick the subset this site gets, above."))),
    ], { fold: "company-site" }),
    card(t("About {company}", { company: c.name }), ab && ab.read_at ? t("read {date}", { date: ab.read_at }) : t("not looked into yet"), [
      languageBar(c),
      ab && ab.summary ? h("p", { class: "about-summary" }, ab.summary) : h("p", { class: "muted card-p" }, t("The AI can find out what it does, how it is doing, its prospects and how it hires graduates, with a source for every point.")),
      ab && (ab.points || []).length > 0 && h("div", { class: "about-points" }, ab.points.map((p) => h("div", { class: "about-point" },
        p.topic && h("b", null, p.topic), h("span", null, p.text),
        (p.sources || []).length > 0 && h("span", { class: "about-src" }, p.sources.map((s, k) => h("a", { href: s, "data-ext": "", title: s }, `[${k + 1}]`)))))),
    ], { fold: "company-about" }),
    card(t("Company details"), t("filled by AI, each with its source; what you edit stays yours"), [
      h("div", { class: "grid2" }, hfield(t("Name"), c.name, save("name")), hfield(t("Also called"), (c.aliases || []).join(", "), save("aliases"), { placeholder: "Xinghe, Nebula", hint: keptHint(c, "aliases") || t("The AI knows it by these too") })),
      h("div", { class: "grid2" }, hfield(t("Industry"), c.industry, save("industry"), { hint: keptHint(c, "industry") }),
        hfield(t("Strength"), c.strength, save("strength"), { placeholder: t("e.g. market cap, or the latest valuation"), hint: keptHint(c, "strength") })),
      h("div", { class: "grid2" }, h("div", { class: "field", id: "company-hq" }, h("label", null, t("Headquarters"), keptHint(c, "hq") && h("span", { class: "hint" }, keptHint(c, "hq"))),
        placesEditor(c.hq ? [c.hq] : [], (v) => { c.hq = v[0] || null; save("hq")(v[0] || ""); }, { single: true })),
        hfield(t("Size"), c.size, save("size"), { hint: keptHint(c, "size") })),
      h("div", { class: "field", id: "company-places" }, h("label", null, t("Hires in"), h("span", { class: "hint" }, keptHint(c, "places") || t("the cities it hires in: they put it on the map"))),
        placesEditor(c.places, (v) => { c.places = v; save("places")(v); })),
      hfield(t("Notes"), c.note, save("note"), { multiline: true, placeholder: t("e.g. which batch (提前批 / 正式批), what you heard") }),
    ], { fold: "company-details" }),
    card(t("What happened"), v.events.length ? t("{n} event", { n: v.events.length }, "{n} events") : "", [timeline(v.events, apps)], { fold: "company-history" }),
  ];
}

const SITE_KINDS = { campus: "Campus", intern: "Internships", social: "Experienced", other: "Other" };

// the company's careers sites, each one changeable: its kind, its address (emptied, it goes), or gone; and more
function sitesEditor(c) {
  const sites = (c.sites || []).map((x) => ({ ...x }));
  const save = () => editCompany(c.id, { sites });
  const kinds = Object.entries(SITE_KINDS).map(([value, label]) => ({ value, label: t(label) }));
  const rows = sites.map((x, i) => h("div", { class: "co-site co-site-edit" },
    uiSelect({ label: t("Kind"), menuWidth: 170, value: x.kind, cls: "sm", options: kinds, onChange: (v) => { x.kind = v; save(); } }).el,
    h("input", { class: "co-site-input", value: x.url, spellcheck: "false", "aria-label": t("Address"),
      onchange: (e) => { const v = e.target.value.trim(); if (v === x.url) return; if (v) x.url = v; else sites.splice(i, 1); save(); } }),
    x.system && h("span", { class: "muted" }, x.system),
    h("button", { class: "btn icon sm ghost", title: t("Open it in your everyday browser"), "aria-label": t("Open it in your everyday browser"), onclick: () => openInBrowser(x.url) }, icon("external")),
    h("button", { class: "btn icon sm ghost", title: t("Remove this site"), "aria-label": t("Remove this site"), onclick: () => { sites.splice(i, 1); save(); } }, icon("x"))));
  const add = h("input", { class: "su-add-input", id: "company-add-site", spellcheck: "false", placeholder: t("Add a careers site: paste its address and press Enter"),
    onkeydown: (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      const url = e.target.value.trim();
      if (!url) return;
      sites.push({ url, kind: sites.some((x) => x.kind === "campus") ? "other" : "campus", system: "" });
      save().then(() => { const again = $("#company-add-site"); if (again) again.focus(); });
    } });
  return h("div", { class: "co-sites" }, rows, add);
}
const filledSome = (c) => !!(c.industry || c.strength || c.logo || (c.about && c.about.summary));

// next to a field's label: where Claude found it, or that it is the user's now
function keptHint(c, key) {
  if ((c.kept || []).includes(key)) return t("yours: the AI leaves it");
  const src = (c.sources || {})[key];
  if (!src) return "";
  let host = src;
  try { host = new URL(src).hostname.replace(/^www\./, ""); } catch (e) { /* keep it as written */ }
  return h("a", { href: src, "data-ext": "", class: "src-link", title: src }, t("source: {site}", { site: host }));
}

// the logo is the user's to change: a picture they pick, or none (a wrong one); either way Claude leaves it
async function pickLogo(c) {
  try {
    const r = await api("POST", "/api/pick", { kind: "files" });
    const path = (r.paths || [])[0];
    if (!path) return;
    H.view = await api("PUT", `/api/hunt/logos/${encodeURIComponent(c.id)}`, { path });
    paintHunt();
    loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
}

async function removeLogo(c) {
  try {
    H.view = await api("DELETE", `/api/hunt/logos/${encodeURIComponent(c.id)}`);
    paintHunt();
    loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
}

// what Claude writes about a company (industry, strength, size, the About) is in the language of where the user
// goes for it, among their resume's languages: not the window's, which they switch (jobhunt/writing.py, 2026-10-27)
function researchWhy(r) {
  const place = r.place ? placeName({ id: r.place, area: r.place_name }) : "";
  return r.why === "applying" ? t("you apply in {place}", { place }) : r.why === "hq" ? t("its headquarters is in {place}", { place })
    : r.why === "fallback" ? t("{place} speaks none of your resume's languages", { place }) : "";
}
const wrongLanguage = (c) => !!(c.research && c.research.rewrite);
function askRewrite(cs) {
  if (!ui.chat.available || !cs.length) return;
  aiRun(aiKey("rewrite", cs), () => sendChat(cs.length === 1
    ? t("Write what is known about {company} again in {lang}: its industry, strength, size and the About, with the same facts and sources.", { company: cs[0].name, lang: cs[0].research.lang_name })
    : t("Write what is known about these companies again, each in the language it is described in: their industry, strength, size and the About, with the same facts and sources. {names}", { names: cs.map((c) => c.name).join(", ") })));
}
// on a company's page: what is there is in another language than the company is described in
function languageBar(c) {
  const r = c.research;
  if (!wrongLanguage(c)) return null;
  return h("div", { class: "note-bar", id: "company-language" }, icon("translate"),
    h("span", { class: "nb-text" }, t("This is in {now}. {company} is described in {lang}: {why}.", { now: r.rewrite_name, company: c.name, lang: r.lang_name, why: researchWhy(r) })),
    h("button", { class: "btn sm", "data-ai": aiKey("rewrite", [c]), disabled: !ui.chat.available, onclick: () => askRewrite([c]) }, icon("sparkle"), t("Ask AI to write it in {lang}", { lang: r.lang_name })));
}
// over the companies table: every company described in another language, rewritten at once
function languagesBar(cs) {
  const wrong = cs.filter(wrongLanguage);
  if (!wrong.length) return null;
  return h("div", { class: "note-bar", id: "hunt-languages" }, icon("translate"),
    h("span", { class: "nb-text", title: t("A company is described in the language of where you apply to it, else of its headquarters, when your resume is written in that language; else in English.") },
      t("Described in another language than theirs: {names}.", { names: wrong.map((c) => c.name).join(", ") })),
    h("button", { class: "btn sm", "data-ai": aiKey("rewrite", wrong), disabled: !ui.chat.available, onclick: () => askRewrite(wrong) }, icon("sparkle"), t("Ask AI to rewrite them")));
}

// Claude researches each company and fills every field (docs/JOB_HUNT.md, 2026-10-18); one message each, queued in one chat
function askFill(cs) {
  if (!ui.chat.available) return;
  aiRun(aiKey("fill", cs), async () => {
    let ok = false;
    for (const c of cs) ok = await sendChat(t("Find out about {company} and fill in all its fields: industry, strength, headquarters, the cities it hires in, size, its careers sites, its logo and the About, each with a source.", { company: c.name })) || ok;
    return ok;
  });
}

async function moveApp(a, place) {
  try { await api("PUT", `/api/hunt/apps/${encodeURIComponent(a.id)}`, { order: place }); }
  catch (e) { toast(e.message); }
  await loadHunt({ quiet: true });
}


async function sendCompany(c, unsent) {
  const st = versionStatus(c.subset) || {};
  const label = subsetName(subsets().find((x) => x.id === c.subset)) || c.subset;
  const lines = [
    st.same_as ? t("“{name}” goes out as v{n}, as it is now.", { name: label, n: st.same_as })
      : t("“{name}” is kept as v{n} first, so you'll always know what this site got.", { name: label, n: (st.latest || 0) + 1 }),
    t("{n} role is marked Applied: {roles}.", { n: unsent.length, roles: unsent.map((a) => a.role).join(", ") }, "{n} roles are marked Applied: {roles}."),
    H.data && H.data.tracker === "career-ops" && t("They also go into {name}'s tracker.", { name: jobsTool().name }),
  ].filter(Boolean);
  if (!(await modal({ title: t("Mark “{company}” as sent?", { company: c.name }), message: h("div", { class: "modal-lines" }, lines.map((l) => h("p", null, l))), ok: t("Mark as sent") }))) return;
  try {
    const r = await api("POST", `/api/hunt/companies/${encodeURIComponent(c.id)}/sent`, {});
    applyState(r);
    H.view = { company: r.company, applications: r.applications, events: r.events };
    toast(t("Sent with “{name}” v{n}", { name: label, n: r.n }));
    await loadHunt({ quiet: true });
  } catch (e) { toast(e.message, { ms: 9000 }); }
}

async function deleteCompany(c, n) {
  if (!(await modal({ title: t("Delete “{company}”?", { company: c.name }),
    message: n ? t("Its {n} application goes too. The versions that were sent stay with their subsets.", { n }, "Its {n} applications go too. The versions that were sent stay with their subsets.")
      : t("Nothing else is touched."), ok: t("Delete"), danger: true }))) return;
  try { await api("DELETE", `/api/hunt/companies/${encodeURIComponent(c.id)}`); }
  catch (e) { toast(e.message); return; }
  await setHuntTab("companies");
  loadHunt({ quiet: true });
}

// ================================================================ an application's page
async function editApp(id, fields, { repaint = true } = {}) {
  try {
    H.view = await api("PUT", `/api/hunt/apps/${encodeURIComponent(id)}`, fields);
    if (repaint) paintHunt();
    loadHunt({ quiet: true });
    return true;
  } catch (e) { toast(e.message); if (repaint) paintHunt(); return false; }
}

function appPage(v) {
  const a = v.application;
  const c = v.company;
  const save = (key) => (val) => editApp(a.id, { [key]: val }, { repaint: false }).then((ok) => {
    if (ok && key === "role") { const hd = $(".hunt-wrap .ed-head h1 .r"); if (hd) hd.textContent = val; }
  });
  const status = uiSelect({ id: "app-status", label: t("Status"), menuWidth: 220, value: a.status,
    options: ((H.data && H.data.states) || []).map((s) => ({ value: s.id, label: huntState(s.id) })),
    onChange: (val) => setAppStatus(a, val) });
  const channel = uiSeg(Object.entries(HCHANNELS).map(([value, label]) => ({ value, label: t(label) })), a.channel || "site",
    (val) => editApp(a.id, { channel: val, ...(val === "platform" && !a.platform && (F.sites || []).length ? { platform: F.sites[0].id } : {}) }), { cls: "sm", label: t("How") });
  const plat = a.channel === "platform" && uiSelect({ id: "app-platform", label: t("Platform"), menuWidth: 220, value: a.platform || null, placeholder: t("Pick a platform"),
    options: (F.sites || []).map((s) => ({ value: s.id, label: s.name })), onChange: (val) => editApp(a.id, { platform: val }) });
  const subId = a.subset || c.subset || null;
  const pick = uiSelect({ id: "app-subset", label: t("Subset"), menuWidth: 300, value: a.subset || null,
    placeholder: c.subset ? t("Like the company: {name}", { name: subsetName(subsets().find((x) => x.id === c.subset)) || c.subset }) : t("Pick a subset"),
    options: subsets().filter((x) => x.id !== ALL).map((x) => ({ value: x.id, label: subsetName(x) })),
    onChange: (val) => editApp(a.id, { subset: val }) });
  // a repaint (a refresh from the server, Claude's edits) rebuilds these, so what is typed lives in H.notes, not only in the box
  const draft = H.notes[a.id] || (H.notes[a.id] = { text: "", on: today() });
  const note = h("textarea", { id: "app-note-new", rows: 2, placeholder: t("What happened? e.g. 笔试已交"), spellcheck: "false",
    oninput: (e) => { draft.text = e.target.value; } }, draft.text);
  const when = h("input", { type: "date", id: "app-note-on", value: draft.on, "aria-label": t("When"),
    oninput: (e) => { draft.on = e.target.value; }, onchange: (e) => { draft.on = e.target.value; } });
  return [
    h("div", { class: "row" }, backTo("apps"), h("button", { class: "btn sm ghost", onclick: () => openHunt("company", c.id) }, icon("globe"), c.name)),
    h("div", { class: "ed-head" }, h("h1", null, h("span", { class: "co" }, c.name, " / "), h("span", { class: "r" }, a.role)),
      h("span", { class: "ed-acts" }, status.el,
      h("button", { class: "btn icon ghost", title: t("More"), "aria-label": t("More"), onclick: (e) => openMenu(e.currentTarget, [
        a.url && { label: t("Open the role's page"), icon: "external", onClick: () => openInBrowser(a.url) },
        a.co_app && { label: t("Its evaluation in {name}", { name: jobsTool().name }), icon: "bolt", onClick: () => openPosting(String(a.co_app)) },
        { label: t("Delete this application"), icon: "trash", danger: true, onClick: () => deleteApp(a) }], { align: "end" }) }, icon("more")))),
    bookedCard(a),
    card(t("Progress"), a.applied ? t("applied {date}", { date: a.applied }) : t("not applied yet"), [
      h("div", { class: "field" }, h("label", { for: "app-note-new" }, t("Add to the timeline")), note,
        h("div", { class: "row" }, when, h("span", { class: "spacer" }), h("button", { class: "btn sm primary", id: "app-note-add", onclick: () => addAppNote(a, note.value, when.value) }, icon("plus"), t("Add")))),
      timeline(v.events, [a]),
    ], { fold: "app-progress" }),
    card(t("The role"), "", [
      h("div", { class: "grid2" }, hfield(t("Role"), a.role, save("role")), hfield(t("Link"), a.url, save("url"), { placeholder: "https://…", link: true })),
      h("div", { class: "field", id: "app-places" }, h("label", null, t("Location")), placesEditor(a.places, (v) => { a.places = v; save("places")(v); })),
      h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", { for: "app-batch" }, t("Batch")),
        h("input", { id: "app-batch", list: "app-batches", value: a.batch || "", placeholder: t("27秋招, 2027 Graduate Program…"), spellcheck: "false",
          onchange: (e) => { const val = e.target.value.trim(); if (val !== (a.batch || "")) { a.batch = val; save("batch")(val); } } }),
        h("datalist", { id: "app-batches" }, usedBatches().map((b) => h("option", { value: b })))),
        hfield(t("Referral code"), a.referral, save("referral"), { placeholder: t("none: no referral") })),
      h("div", { class: "field" }, h("label", null, t("How it goes in")), h("div", { class: "row wrap" }, channel, plat && plat.el,
        a.channel === "site" && h("span", { class: "muted" }, t("choice {n} on {company}'s site", { n: a.order, company: c.name })))),
      hfield(t("Notes"), a.note, save("note"), { multiline: true }),
    ], { fold: "app-role" }),
    card(t("Job description"), jdSub(a, v.reading), [
      (v.reading || {}).error && h("div", { class: "note-bar warn" }, icon("alert"),
        h("span", { class: "nb-text" }, t("Its page didn't read: {error}", { error: v.reading.error })),
        ui.chat.available && h("button", { class: "btn sm", "data-ai": "read:" + a.id, onclick: () => aiRun("read:" + a.id, () => sendChat(t("Read the posting at {url} and fill in {role} at {company}: its job description, location and apply-by date.", { url: a.url, role: a.role, company: c.name }))) },
          icon("sparkle"), t("Ask AI to read it"))),
      h("div", { class: "field" }, h("textarea", { id: "app-jd", rows: 6, value: a.jd || "", spellcheck: "false", "aria-label": t("Job description"),
        placeholder: t("Paste the posting's text here: duties, requirements, pluses. Or send the AI its link or a screenshot."),
        onchange: (e) => { const val = e.target.value.trim(); if (val !== (a.jd || "")) { a.jd = val; a.jd_read = ""; editApp(a.id, { jd: val, jd_read: "" }, { repaint: false }); } } }, a.jd || "")),
    ], { actions: readButton(a, v.reading), fold: "app-jd" }),
    card(t("Resume sent"), (a.sent || []).length ? t("{n} time", { n: a.sent.length }, "{n} times") : t("not yet"), [
      a.channel !== "site" && h("div", { class: "field" }, h("label", null, t("Goes out with")), h("div", { class: "su-subset" }, pick.el)),
      a.channel === "site" && h("p", { class: "card-p muted" }, t("It goes out with the sign-up on {company}'s site: mark that as sent on the company's page.", { company: c.name })),
      h("div", { class: "row" },
        a.channel !== "site" && h("button", { class: "btn primary", id: "app-send", disabled: !subId, onclick: () => sendApp(a, subId) }, icon("check"),
          (a.sent || []).length ? t("Mark as sent again") : t("Mark as sent")),
        subId && subsets().some((x) => x.id === subId) && h("button", { class: "btn sm ghost", id: "app-resume", onclick: () => previewSubset(subId) }, icon("file"),
          t("Open “{name}” in Resume", { name: subsetName(subsets().find((x) => x.id === subId)) }))),
      (a.sent || []).length > 0 && h("div", { class: "su-sent" }, [...a.sent].reverse().map((x) => h("div", { class: "su-sent-row" },
        h("span", { class: "su-info" }, h("span", { class: "su-what" }, sentText(x)), h("span", { class: "su-when" }, fmtWhen(x.at))),
        h("button", { class: "btn sm ghost", onclick: () => showSent(x.subset, x.n) }, icon("history"), t("See what went out"))))),
    ], { fold: "app-sent" }),
  ];
}

// the role's page, read for its description (jobhunt/reader.py): what the card says about it, and the button
function jdSub(a, reading) {
  if ((reading || {}).running) return h("span", { class: "fs-status" }, h("span", { class: "spin" }), t("Reading its page…"));
  if (a.jd && a.jd_read) return t("read from its page {date}", { date: a.jd_read });
  // folded, the card shows only this line: a page that didn't read must not pass for one never tried
  if (!a.jd && (reading || {}).error) return h("span", { class: "fs-status bad" }, icon("alert"), t("Its page didn't read"));
  return a.jd ? "" : t("not kept yet");
}

function readButton(a, reading) {
  const link = /^https?:\/\//.test(a.url || "");
  return h("button", { class: "btn sm", id: "app-read", disabled: !link || !!(reading || {}).running,
    title: link ? a.url : t("Give the role its link first"), onclick: () => readPosting(a) },
  icon("refresh"), a.jd ? t("Read its page again") : t("Fill in from its page"));
}

async function readPosting(a) {
  if (a.jd && !(await modal({ title: t("Replace the job description?"),
    message: t("The description here is replaced by the one on its page now. The location and the apply-by date are only filled where they are empty."),
    ok: t("Replace") }))) return;
  try {
    H.view = await api("POST", `/api/hunt/apps/${encodeURIComponent(a.id)}/read`, { replace: true });
    paintHunt();
  } catch (e) { toast(e.message); }
}

async function setAppStatus(a, status) {
  try {
    H.view = await api("POST", `/api/hunt/apps/${encodeURIComponent(a.id)}/status`, { status });
    paintHunt();
    loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
}

async function addAppNote(a, text, on) {
  if (!text.trim()) { const el = $("#app-note-new"); if (el) el.focus(); return; }
  try {
    H.view = await api("POST", `/api/hunt/apps/${encodeURIComponent(a.id)}/note`, { note: text, on: on === today() ? "" : on });
    delete H.notes[a.id];
    paintHunt();
    loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
}

async function sendApp(a, subId) {
  const st = versionStatus(subId) || {};
  const label = subsetName(subsets().find((x) => x.id === subId)) || subId;
  const msg = st.same_as ? t("“{name}” goes out as v{n}, as it is now.", { name: label, n: st.same_as })
    : t("“{name}” is kept as v{n} first, so you'll always know what this posting got.", { name: label, n: (st.latest || 0) + 1 });
  if (!(await modal({ title: t("Mark this application as sent?"), message: msg, ok: t("Mark as sent") }))) return;
  try {
    const r = await api("POST", `/api/hunt/apps/${encodeURIComponent(a.id)}/sent`, { subset: subId });
    applyState(r);
    H.view = { application: r.application, company: r.company, events: r.events };
    toast(t("Sent with “{name}” v{n}", { name: label, n: r.n }));
    paintHunt();
    loadHunt({ quiet: true });
  } catch (e) { toast(e.message, { ms: 9000 }); }
}

async function deleteApp(a) {
  if (!(await modal({ title: t("Delete “{role}”?", { role: a.role }), message: t("Its timeline goes from this page. The versions that were sent stay with their subsets."),
    ok: t("Delete"), danger: true }))) return;
  try { await api("DELETE", `/api/hunt/apps/${encodeURIComponent(a.id)}`); }
  catch (e) { toast(e.message); return; }
  await setHuntTab("apps");
  loadHunt({ quiet: true });
}

// ================================================================ the timeline (newest first)
function timeline(events, apps) {
  if (!events.length) return h("p", { class: "muted card-p" }, t("Nothing yet."));
  const roles = Object.fromEntries(apps.map((a) => [a.id, a.role]));
  const many = apps.length > 1;
  return h("ol", { class: "timeline" }, [...events].reverse().map((e) => h("li", { class: "tl-" + e.kind },
    h("span", { class: "tl-dot" }),
    h("div", { class: "tl-body" },
      h("div", { class: "tl-head" },
        many && roles[e.app] && h("b", null, roles[e.app]),
        e.kind === "status" ? h("span", null, hChip(e.from), " → ", hChip(e.to))
          : e.kind === "created" ? h("span", null, t("Added as {status}", { status: huntState(e.to) }))
          : e.kind === "sent" ? h("span", null, t("Went out: {what}", { what: sentNote(e.note) }), e.from !== e.to ? [" · ", hChip(e.to)] : null)
          : null,
        h("span", { class: "tl-when", title: e.at }, e.at.endsWith("T00:00:00") ? e.at.slice(0, 10) : fmtWhen(e.at)),
        e.source === "chat" && h("span", { class: "tl-src" }, t("by AI"))),
      e.note && e.kind !== "sent" && h("div", { class: "tl-note" }, e.note)))));
}

// ================================================================ Settings: which tracker the applications go into (saved on Done)
let pendingTracker, pendingDaily;
function trackerField() {
  const name = jobsTool().name;
  const box = h("div", { class: "field", id: "tracker-settings" });
  const paint = () => {
    const d = H.data || {};
    const v = pendingTracker || d.tracker || "studio";
    box.replaceChildren(
      h("label", null, t("Applications tracker"), h("span", { class: "hint" }, t("Where the roles you send go; the Applying workspace shows Resume Studio's either way"))),
      uiSeg([{ value: "studio", label: t("Resume Studio") }, { value: "career-ops", label: name }], v, (x) => { pendingTracker = x; paint(); }, { cls: "sm", label: t("Applications tracker") }),
      h("p", { class: "settings-note" }, v === "career-ops"
        ? (d.career_ops ? t("Roles you mark as sent also get a row in {name}'s tracker (its pipeline, follow-ups and stats see them).", { name })
          : t("{name} isn't connected yet: roles go into Resume Studio's tracker until it is.", { name }))
        : t("Everything stays in Resume Studio's own tracker, next to your data file. {name} keeps its own rows.", { name })),
      (() => { const on = pendingDaily ?? ((d.scan || {}).daily !== false);
        return h("button", { type: "button", class: "check-row", id: "daily-scan", role: "checkbox", "aria-checked": String(on),
          onclick: () => { pendingDaily = !on; paint(); } }, h("span", { class: "check" + (on ? " on" : "") }),
        h("span", null, t("Scan the companies' sites once a day, when the window opens"), h("span", { class: "hint" }, t("by their providers and recorded routes; never AI")))); })());
  };
  paint();
  if (!H.data) loadHunt({ quiet: true }).then(paint);
  return box;
}

async function applyTracker() {
  if (pendingDaily !== undefined) {
    const v = pendingDaily;
    pendingDaily = undefined;
    await api("PUT", "/api/hunt/scan-settings", { daily: v });
    if (H.data && H.data.scan) H.data.scan.daily = v;
  }
  if (pendingTracker === undefined) return;
  const v = pendingTracker;
  pendingTracker = undefined;
  if (H.data && v === H.data.tracker) return;
  const r = await api("PUT", "/api/hunt/settings", { tracker: v });
  if (H.data) H.data.tracker = r.tracker;
}
