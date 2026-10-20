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
      if (!H.autoScan) { H.autoScan = true; api("POST", "/api/hunt/scan", { auto: true }).catch(() => {}); }
    } catch (e) { if (!quiet) toast(e.message); }
    finally { H.loading = null; }
    if (W.ws === "fill" && dbvBusy()) DBV.pending = true;   // an open cell editor or view panel: after it closes
    else if (W.ws === "fill" && !fillTyping()) paintFill();
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

function listenHunt(es) {
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
  H.page = { kind, id }; H.view = null;
  storeHunt();
  paintFill();
  await loadHuntPage();
  paintFill();
  welcomeAgain();
}

// the workspace: a platform's own page is autofill.js's; everything else is here
function paintFill() {
  if (H.tab === "platforms" && H.page && H.page.kind === "platform" && (F.sites || []).some((s) => s.id === H.page.id)) {
    paintPlatform();
    return;
  }
  if (H.page && H.page.kind === "platform") { H.page = null; storeHunt(); }
  paintHunt();
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
  if (!el) return;
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
  for (const ta of el.querySelectorAll("textarea")) autosizeFull(ta);
}
const pageKey = () => (H.page ? H.page.kind + ":" + H.page.id : "table:" + H.tab);

// ================================================================ the tables
function tableTools(...right) {
  const search = h("input", { class: "hunt-search", type: "search", value: H.q, placeholder: t("Search"), spellcheck: "false", "aria-label": t("Search"),
    oninput: (e) => { H.q = e.target.value; const pos = e.target.selectionStart; paintHunt(); const s = $(".hunt-search"); if (s) { s.focus(); s.setSelectionRange(pos, pos); } } });
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
const late = (d) => !!d && String(d).slice(0, 10) < today();

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
  return [
    { key: "role", label: t("Role"), type: "title", width: 220, get: (a) => a.role },
    { key: "company", label: t("Company"), type: "relation", width: 170, group: true, get: (a) => co(a).name || "",
      render: (a) => h("button", { type: "button", class: "role-chip co-chip", onclick: () => openHunt("company", a.company) },
        companyLogo(co(a), "xs"), h("span", { class: "rc-name" }, co(a).name || "")) },
    { key: "status", label: t("Status"), type: "select", width: 140, get: (a) => a.status, options: states, set: (a, v) => cellStatus(a, v) },
    { key: "channel", label: t("How"), type: "select", width: 150, min: 120, wrap: true, get: (a) => a.channel || "site", options: channels,
      render: (a) => h("span", { class: "c-muted" }, channelText(a)),
      set: (a, v) => cellApp(a, { channel: v, ...(v === "platform" && !a.platform && (F.sites || []).length ? { platform: F.sites[0].id } : {}) }) },
    { key: "deadline", label: t("Deadline"), type: "date", width: 120, get: (a) => a.deadline || "",
      render: (a) => a.deadline ? h("span", { class: "c-date" + (late(a.deadline) && huntOpen(a) ? " late" : "") }, a.deadline) : "",
      set: (a, v) => cellApp(a, { deadline: v }) },
    { key: "sent", label: t("Resume sent"), type: "custom", width: 190, min: 130, get: (a) => (a.sent && a.sent.length ? sentText(a.sent[a.sent.length - 1]) : ""),
      render: (a) => h("span", { class: "c-muted c-resume" }, a.sent && a.sent.length ? sentText(a.sent[a.sent.length - 1]) : "", resumeButton(a)) },
    { key: "last", label: t("Latest"), type: "custom", width: 220, get: (a) => (a.last ? a.last.at : ""),
      render: (a) => a.last ? h("span", { class: "c-last" }, h("span", { class: "c-when" }, huntWhen(a.last.at)), h("span", { class: "c-what" }, lastText(a.last))) : "" },
    { key: "next", label: t("Next step"), type: "text", width: 180, get: (a) => a.next || "", set: (a, v) => cellApp(a, { next: v }),
      render: (a) => h("span", { class: "c-next" + (late(a.due) ? " late" : "") }, a.next || "", a.due && h("span", { class: "c-due" }, a.due)) },
    { key: "due", label: t("By"), type: "date", width: 120, hidden: true, get: (a) => (a.due || "").slice(0, 10),
      render: (a) => a.due ? h("span", { class: "c-date" + (late(a.due) ? " late" : "") }, a.due) : "", set: (a, v) => cellApp(a, { due: v }) },
    { key: "note", label: t("Notes"), type: "text", width: 220, hidden: true, get: (a) => a.note || "", set: (a, v) => cellApp(a, { note: v }) },
    { key: "url", label: t("Link"), type: "text", width: 200, hidden: true, get: (a) => a.url || "", set: (a, v) => cellApp(a, { url: v }) },
  ];
}

const APP_VIEWS = () => [
  { id: "v1", name: t("Active"), filters: [{ key: "status", op: "is", value: OPEN_STATES }] },
  { id: "v2", name: t("All") },
  { id: "v3", name: t("Board"), layout: "board", group: "status" },
];

function appsTable() {
  const d = H.data;
  const names = Object.fromEntries(d.companies.map((c) => [c.id, c.name]));
  const all = [...d.applications].sort((a, b) => (names[a.company] || "").localeCompare(names[b.company] || "") || (a.order || 0) - (b.order || 0));
  const shown = all.filter((a) => matches(names[a.company], a.role, a.note, a.next));
  return [
    h("div", { class: "hunt-head" }, h("h1", null, t("Your applications")),
      h("p", null, t("Every role you apply for, wherever it goes in. Tell Claude on the left what happened (“Xinghe 一面过了”), or change it here."))),
    tableTools(
      h("button", { class: "btn sm primary", id: "hunt-new-app", onclick: newApplication }, icon("plus"), t("Add an application")),
      linkButton()),
    dbView({ table: "apps", props: appProps(), rows: shown, rowId: (a) => a.id, open: (a) => openHunt("app", a.id), defaults: APP_VIEWS(), repaint: paintHunt,
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

const today = () => new Date().toISOString().slice(0, 10);

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
const scanning = (cid) => !!(scanInfo().running || {})[cid];
const kwMatch = (title) => { const kw = scanInfo().keywords || []; return !kw.length || kw.some((k) => title.toLowerCase().includes(k.toLowerCase())); };

// the company's open postings, as one chip in the table (the ones matching the user's keywords)
function openChip(c) {
  const ps = c.postings || {};
  const news = (ps.news || {}).new || 0;
  if (!ps.open) return null;
  return h("button", { class: "role-chip open-chip", title: t("{n} open on its sites", { n: ps.open }), onclick: () => openHunt("company", c.id) },
    icon("briefcase"), h("span", { class: "rc-name" }, t("{n} open", { n: ps.matching })), news > 0 && h("span", { class: "rc-new" }, t("{n} new", { n: news })));
}

async function scanCompanies(ids = null) {
  try {
    await api("POST", "/api/hunt/scan", ids ? { companies: ids } : {});
    await loadHunt({ quiet: true });
  } catch (e) { toast(e.message); }
}

function scanAllButton() {
  const busy = Object.keys(scanInfo().running || {}).length > 0;
  const last = scanInfo().last_all;
  return h("button", { class: "btn sm", id: "hunt-scan-all", disabled: busy,
    title: last ? t("Last scanned {when}", { when: huntWhen(last) }) : t("Reads every company's careers sites for open roles"),
    onclick: () => scanCompanies() }, busy ? h("span", { class: "spin" }) : icon("refresh"), busy ? t("Scanning…") : t("Scan all"));
}

// what the last day's scans brought, and the sites Claude still has to learn once
function scanNews() {
  const d = H.data;
  const names = Object.fromEntries(d.companies.map((c) => [c.id, c]));
  const learn = d.companies.filter((c) => (c.postings || {}).needs === "claude");
  const noSites = d.companies.filter((c) => (c.postings || {}).needs === "sites");
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
    h("div", { class: "sn-head" }, h("b", null, t("Today")), h("span", { class: "muted" }, rows.length ? "" : t("nothing new from the last day's scans")),
      h("span", { class: "spacer" }), h("label", { class: "kw-label", for: "hunt-keywords" }, t("Roles you look for")), kwInput),
    rows,
    learn.length > 0 && h("div", { class: "note-bar" }, icon("sparkle"), h("span", { class: "nb-text" },
      t("{names}: Claude reads the site once and records how, then scans need no Claude.", { names: learn.map((c) => c.name).join(", ") })),
      h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => askLearn(learn) }, icon("sparkle"), t("Let Claude learn them"))),
    noSites.length > 0 && h("div", { class: "note-bar" }, icon("alert"), h("span", { class: "nb-text" },
      t("{names}: no careers site known yet.", { names: noSites.map((c) => c.name).join(", ") })),
      h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => noSites.forEach(askFill) }, icon("sparkle"), t("Ask Claude to find them"))));
}

function askLearn(cs) {
  sendChat(t("Find the open roles on the careers sites of {names}, and record a scan route for each so later scans need no Claude.", { names: cs.map((c) => c.name).join(", ") }));
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
  const status = busy ? h("span", { class: "fs-status" }, h("span", { class: "spin" }), t("Scanning…"))
    : !sc.at ? h("span", { class: "muted" }, t("not scanned yet"))
    : sc.ok ? h("span", { class: "muted" }, t("scanned {when} · {how}", { when: huntWhen(sc.at), how: howName(sc.how) }))
    : h("span", { class: "fs-status bad" }, icon("alert"), sc.signed_out ? t("The site asks to sign in") : t("The last scan didn't work"));
  const row = (p) => h("div", { class: "post-row" + (p.closed ? " closed" : ""), "data-id": p.id },
    h("div", { class: "pr-main" },
      h("a", { class: "pr-title", href: p.url || null, "data-ext": p.url ? "" : null }, p.title),
      fresh.has(p.id) && h("span", { class: "chip new-chip" }, t("new")),
      p.changed && p.changed.deadline && h("span", { class: "chip", title: p.changed.deadline.join(" → ") }, t("deadline changed")),
      h("span", { class: "pr-meta" }, [p.location, p.category, p.batch && t(BATCH_NAMES[p.batch] || p.batch)].filter(Boolean).join(" · "),
        p.deadline && h("span", { class: "c-due" + (p.deadline < today() ? " late" : "") }, t("closes {date}", { date: p.deadline })))),
    p.app ? h("button", { class: "btn sm ghost", onclick: () => openHunt("app", p.app) }, hChip(((v.applications || []).find((a) => a.id === p.app) || {}).status || "saved"))
      : !p.closed && h("button", { class: "btn sm", title: t("Add it to your applications"), onclick: () => applyPosting(p) }, icon("plus"), t("Want to apply")));
  const search = h("input", { class: "hunt-search", type: "search", id: "posting-search", value: H.pq || "", placeholder: t("Search these roles"),
    oninput: (e) => { H.pq = e.target.value; const pos = e.target.selectionStart; paintHunt(); const el = $("#posting-search"); if (el) { el.focus(); el.setSelectionRange(pos, pos); } } });
  return card(t("Open roles"), open.length ? t("{n} on its sites", { n: open.length }) : "", [
    h("div", { class: "row wrap post-tools" }, status, h("span", { class: "spacer" }),
      sc.needs === "claude" && h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => askLearn([c]) }, icon("sparkle"), t("Let Claude learn its site")),
      !sc.ok && sc.at && sc.how === "route" && h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => askLearn([c]) }, icon("sparkle"), t("Ask Claude to record it again")),
      h("button", { class: "btn sm", id: "company-scan", disabled: !!busy, onclick: () => scanCompanies([c.id]) }, icon("refresh"), t("Scan"))),
    !sc.ok && sc.error && h("div", { class: "note-bar warn" }, icon("alert"), h("span", { class: "nb-text" }, sc.error),
      sc.signed_out && c.site && h("button", { class: "btn sm", onclick: () => signInMode(c.site) }, icon("user"), t("Sign in to {site}", { site: c.name }))),
    sc.needs === "sites" && h("p", { class: "muted card-p" }, t("No careers site known yet: ask Claude to fill the company in, or type one below.")),
    open.length > 0 && h("div", { class: "row wrap" }, search, hidden > 0 && h("button", { class: "btn sm ghost", onclick: () => { H.pall = !H.pall; paintHunt(); } },
      showAll ? t("Only the roles you look for") : t("Show all ({n} more)", { n: hidden }))),
    shown.length ? h("div", { class: "post-list" }, shown.slice(0, limit).map(row),
      shown.length > limit && h("button", { class: "btn sm ghost", onclick: () => { H.plimit = limit + 200; paintHunt(); } }, t("Show {n} more", { n: Math.min(200, shown.length - limit) })))
      : open.length > 0 && h("p", { class: "muted card-p" }, t("Nothing here matches. Change the roles you look for on the Companies tab, or show all.")),
    closed.length > 0 && h("details", { class: "post-closed" }, h("summary", null, t("{n} closed", { n: closed.length })), h("div", { class: "post-list" }, closed.slice(0, 200).map(row))),
  ]);
}

const BATCH_NAMES = { campus: "Campus", intern: "Internship", social: "Experienced", other: "Other" };
function howName(how) {
  return String(how || "").split("+").map((x) => (x === "route" ? t("its recorded route") : x === "claude" ? t("read by Claude") : x)).join(" + ");
}

async function applyPosting(p) {
  try {
    const v = await api("POST", `/api/hunt/postings/${encodeURIComponent(p.id)}/apply`);
    await loadHunt({ quiet: true });
    toast(t("Added to your applications: {role}", { role: v.application.role }));
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
      render: (c) => h("div", { class: "role-chips" }, (c.apps || []).map(roleChip), openChip(c),
        !(c.apps || []).length && !openChip(c) && h("span", { class: "c-muted" }, scanning(c.id) ? t("Scanning…") : t("none yet"))) },
    text("location", t("Headquarters"), 130, { hidden: true, group: true }),
    text("size", t("Size"), 120, { hidden: true }),
    text("site", t("Careers site"), 220, { hidden: true }),
    text("note", t("Notes"), 220, { hidden: true }),
  ];
}

function companiesTable() {
  const d = H.data;
  const shown = d.companies.filter((c) => matches(c.name, (c.aliases || []).join(" "), c.industry, c.strength, c.note));
  return [
    h("div", { class: "hunt-head" }, h("h1", null, t("Companies")),
      h("p", null, t("What each company is and how it is doing, your notes, and its own careers site: one profile there, several roles in your order of preference."))),
    tableTools(h("button", { class: "btn sm primary", id: "hunt-new-company", onclick: () => newCompany() }, icon("plus"), t("Add a company")), linkButton(),
      d.companies.length > 0 && scanAllButton()),
    d.companies.length > 0 && scanNews(),
    dbView({ table: "companies", props: companyProps(), rows: shown, rowId: (c) => c.id, open: (c) => openHunt("company", c.id),
      defaults: [{ id: "v1", name: t("All companies") }], repaint: paintHunt,
      empty: !d.companies.length
        ? h("div", null, h("div", { class: "t" }, t("No companies yet")), h("p", null, t("Add the companies you're applying to; Claude can tell you about each one.")))
        : t("Nothing matches") }),
  ];
}

function platformsTable() {
  const sites = F.sites || [];
  const apps = (H.data && H.data.applications) || [];
  if (!F.sites) { loadFill(); return h("div", { class: "muted" }, t("Loading…")); }
  const host = (s) => (s.home || "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
  const profile = (s) => !s.synced_at ? "unread" : s.conflicts ? "conflicts" : s.changes ? "changes" : "step";
  const PROFILE = { unread: t("Not read yet"), conflicts: t("Has conflicts"), changes: t("Changes to push"), step: t("In step") };
  const nApps = (s) => apps.filter((a) => a.channel === "platform" && a.platform === s.id).length;
  const props = [
    { key: "name", label: t("Platform"), type: "title", width: 200, get: (s) => s.name,
      render: (s) => h("span", { class: "co-text" }, h("span", { class: "c-main-name" }, s.name), h("span", { class: "c-alias" }, host(s))) },
    { key: "profile", label: t("Your profile there"), type: "custom", width: 180, group: true, get: (s) => PROFILE[profile(s)],
      render: (s) => h("span", { class: "c-muted" }, !s.synced_at ? t("Not read yet") : s.conflicts ? t("{n} conflict", { n: s.conflicts }, "{n} conflicts")
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
function formModal({ title, message = "", fields, ok = t("OK") }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const inputs = {};
    const close = (val) => { root.replaceChildren(); document.removeEventListener("keydown", onKey, true); resolve(val); };
    const done = () => {
      const out = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()]));
      const missing = fields.find((f) => f.required && !out[f.key]);
      if (missing) { inputs[missing.key].focus(); return; }
      close(out);
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
      inputs[f.key] = h("input", { id: "fm-" + f.key, value: f.value || "", placeholder: f.placeholder || "", spellcheck: "false", autocomplete: "off" });
      return h("div", { class: "field" + (f.list && f.list.length ? " fm-combo" : "") }, h("label", { for: "fm-" + f.key }, f.label, f.hint && h("span", { class: "hint" }, f.hint)), inputs[f.key],
        f.list && f.list.length && suggest(f, inputs[f.key]));
    });
    root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(null); } },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
        h("h3", null, title), message && h("p", null, message), rows,
        h("div", { class: "acts" }, h("button", { class: "btn", onclick: () => close(null) }, t("Cancel")),
          h("button", { class: "btn primary", onclick: done }, ok)))));
    setTimeout(() => (inputs[fields.find((f) => !f.value)?.key || fields[0].key]).focus(), 0);
  });
}

async function newCompany(name = "") {
  const f = await formModal({ title: t("Add a company"), ok: t("Add"), fields: [
    { key: "name", label: t("Company"), value: name, required: true, placeholder: t("e.g. 星河科技") },
    { key: "site", label: t("Careers site"), hint: t("optional: Claude finds it"), placeholder: "https://careers.example.com/campus" }] });
  if (!f) return null;
  try {
    const v = await api("POST", "/api/hunt/companies", f);
    await loadHunt({ quiet: true });
    openHunt("company", v.company.id);
    askFill(v.company);
    return v.company;
  } catch (e) { toast(e.message); return null; }
}

// a posting's link: Claude reads it and fills in the company (new or known) and the role (docs/JOB_HUNT.md, 2026-10-18)
function linkButton() {
  return h("button", { class: "btn sm", id: "hunt-from-link", disabled: !ui.chat.available,
    title: ui.chat.available ? t("Paste a posting's link: Claude reads it and fills in the company and the role") : t("Claude isn't available"),
    onclick: addFromLink }, icon("link"), t("Add from a link"));
}

async function addFromLink() {
  const f = await formModal({ title: t("Add from a link"), ok: t("Read it"),
    message: t("The link to a posting. Claude reads it, adds its company (and finds out about it, when it is new) and the role, with its deadline."),
    fields: [{ key: "url", label: t("Link"), required: true, placeholder: "https://careers.example.com/campus/job/12345" }] });
  if (!f) return;
  let url = f.url.trim();
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  const same = ((H.data && H.data.applications) || []).find((a) => a.url && a.url.replace(/\/$/, "") === url.replace(/\/$/, ""));
  if (same) { toast(t("That posting is already on the list")); openHunt("app", same.id); return; }
  sendChat(t("Read this posting and add it: {url}", { url }));
}

async function newApplication(companyId = null) {
  const cs = (H.data && H.data.companies) || [];
  const fixed = companyId ? huntCompany(companyId) : null;
  const f = await formModal({ title: t("Add an application"), ok: t("Add"), fields: [
    ...(fixed ? [] : [{ key: "company", label: t("Company"), required: true, list: cs.map((c) => c.name), placeholder: t("e.g. 星河科技") }]),
    { key: "role", label: t("Role"), required: true, placeholder: t("e.g. 服务端研发工程师") },
    { key: "url", label: t("Link"), hint: t("optional"), placeholder: "https://…" }] });
  if (!f) return;
  try {
    let cid = fixed ? fixed.id : (cs.find((c) => c.name.toLowerCase() === f.company.toLowerCase()) || {}).id;
    if (!cid) { const made = (await api("POST", "/api/hunt/companies", { name: f.company })).company; cid = made.id; askFill(made); }
    const v = await api("POST", `/api/hunt/companies/${encodeURIComponent(cid)}/roles`, { role: f.role, url: f.url });
    await loadHunt({ quiet: true });
    if (!companyId) openHunt("app", v.application.id);
  } catch (e) { toast(e.message); }
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
function hfield(label, value, save, { placeholder = "", hint = "", multiline = false, type = "text", id = null } = {}) {
  const attrs = { value: value || "", placeholder, spellcheck: "false", id,
    onchange: (e) => { const v = e.target.value.trim(); if (v !== (value || "")) { value = v; save(v); } } };
  const input = multiline ? h("textarea", { ...attrs, rows: 2 }, value || "") : h("input", { ...attrs, type });
  return h("div", { class: "field" }, h("label", null, label, hint && h("span", { class: "hint" }, hint)), input);
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
    h("div", { class: "ed-head" }, companyLogo(c, "lg"), h("h1", null, c.name), h("span", { class: "id" }, c.id), h("span", { class: "spacer" }),
      h("button", { class: "btn sm", id: "company-fill", disabled: !ui.chat.available, onclick: () => askFill(c) }, icon("sparkle"),
        filledSome(c) ? t("Ask Claude to fill it in again") : t("Ask Claude to fill it in")),
      c.site && h("button", { class: "btn sm", onclick: () => openOnSite(c.site) }, icon("external"), t("Open its site")),
      h("button", { class: "btn icon ghost", title: t("More"), "aria-label": t("More"), onclick: (e) => openMenu(e.currentTarget, [
        c.site && { label: t("Sign in to it in Resume Studio's browser"), icon: "user", onClick: () => signInMode(c.site) },
        c.site && { label: t("Open it in your everyday browser"), icon: "external", onClick: () => openInBrowser(c.site) },
        (c.kept || []).length > 0 && { label: t("Let Claude fill the fields you edited too"), icon: "sparkle", onClick: () => editCompany(c.id, { kept: [] }) },
        "sep",
        { label: t("Delete this company"), icon: "trash", danger: true, onClick: () => deleteCompany(c, apps.length) }], { align: "end" }) }, icon("more"))),
    card(t("Roles"), siteApps.length ? t("on its own site, in your order of preference") : t("the roles you apply for here"), [
      roles.length ? h("div", { class: "su-roles" }, roles) : h("p", { class: "muted card-p" }, t("No roles yet: add the ones you apply for below.")),
      add,
    ]),
    postingsCard(v),
    card(t("Its careers site"), t("one profile there, filled from a subset"), [
      (c.sites || []).length > 0 && h("div", { class: "co-sites" }, c.sites.map((x) => h("div", { class: "co-site" },
        h("span", { class: "chip" }, t(SITE_KINDS[x.kind] || "Other")), h("a", { href: x.url, "data-ext": "", class: "co-site-url" }, x.url.replace(/^https?:\/\//, "")),
        x.system && h("span", { class: "muted" }, x.system)))),
      h("div", { class: "grid2" }, hfield(t("Careers site"), c.site, save("site"), { placeholder: "https://careers.example.com/campus", hint: keptHint(c, "site") }),
        hfield(t("Account"), c.account, save("account"), { placeholder: t("e.g. the phone or email you signed up with"), hint: t("never a password") })),
      h("div", { class: "field" }, h("label", null, t("Filled from"), h("span", { class: "hint" }, t("the subset this site gets"))), h("div", { class: "su-subset" }, pick.el,
        st && h("span", { class: "chip" + (st.latest && !st.same_as ? " vd-changed" : "") }, !st.latest ? t("No versions yet") : st.same_as ? `v${st.same_as}` : t("v{n} · changed", { n: st.latest })),
        c.subset && c.subset_exists && h("button", { class: "btn sm ghost", onclick: () => previewSubset(c.subset) }, icon("file"), t("Preview")))),
      c.subset && !c.subset_exists && h("p", { class: "muted card-p" }, t("This subset is gone; pick another.")),
      h("p", { class: "card-p muted" }, t("When you submit the sign-up on the site, mark it here: the subset is kept as a version (if it changed) and pinned on every role there that hasn't gone out yet.")),
      h("div", { class: "row" }, h("button", { class: "btn primary", id: "company-send", disabled: !(c.subset && c.subset_exists && unsent.length), onclick: () => sendCompany(c, unsent) },
        icon("check"), unsent.length ? t("Mark as sent ({n})", { n: unsent.length }) : t("Mark as sent")),
        siteApps.length > 0 && !unsent.length && h("span", { class: "muted" }, t("Every role here has gone out."))),
    ]),
    card(t("About {company}", { company: c.name }), ab && ab.read_at ? t("read {date}", { date: ab.read_at }) : t("not looked into yet"), [
      ab && ab.summary ? h("p", { class: "about-summary" }, ab.summary) : h("p", { class: "muted card-p" }, t("Claude can find out what it does, how it is doing, its prospects and how it hires graduates, with a source for every point.")),
      ab && (ab.points || []).length > 0 && h("div", { class: "about-points" }, ab.points.map((p) => h("div", { class: "about-point" },
        p.topic && h("b", null, p.topic), h("span", null, p.text),
        (p.sources || []).length > 0 && h("span", { class: "about-src" }, p.sources.map((s, k) => h("a", { href: s, "data-ext": "", title: s }, `[${k + 1}]`)))))),
    ]),
    card(t("Company details"), t("filled by Claude, each with its source; what you edit stays yours"), [
      h("div", { class: "grid2" }, hfield(t("Name"), c.name, save("name")), hfield(t("Also called"), (c.aliases || []).join(", "), save("aliases"), { placeholder: "Xinghe, Nebula", hint: keptHint(c, "aliases") || t("Claude knows it by these too") })),
      h("div", { class: "grid2" }, hfield(t("Industry"), c.industry, save("industry"), { hint: keptHint(c, "industry") }),
        hfield(t("Strength"), c.strength, save("strength"), { placeholder: t("e.g. market cap, or the latest valuation"), hint: keptHint(c, "strength") })),
      h("div", { class: "grid2" }, hfield(t("Headquarters"), c.location, save("location"), { hint: keptHint(c, "location") }),
        hfield(t("Size"), c.size, save("size"), { hint: keptHint(c, "size") })),
      hfield(t("Notes"), c.note, save("note"), { multiline: true, placeholder: t("e.g. which batch (提前批 / 正式批), referral code, what you heard") }),
    ]),
    card(t("What happened"), v.events.length ? t("{n} event", { n: v.events.length }, "{n} events") : "", [timeline(v.events, apps)]),
  ];
}

const SITE_KINDS = { campus: "Campus", intern: "Internships", social: "Experienced", other: "Other" };
const filledSome = (c) => !!(c.industry || c.strength || c.logo || (c.about && c.about.summary));

// next to a field's label: where Claude found it, or that it is the user's now
function keptHint(c, key) {
  if ((c.kept || []).includes(key)) return t("yours: Claude leaves it");
  const src = (c.sources || {})[key];
  if (!src) return "";
  let host = src;
  try { host = new URL(src).hostname.replace(/^www\./, ""); } catch (e) { /* keep it as written */ }
  return h("a", { href: src, "data-ext": "", class: "src-link", title: src }, t("source: {site}", { site: host }));
}

// Claude researches the company and fills every field (docs/JOB_HUNT.md, 2026-10-18)
function askFill(c) {
  if (!ui.chat.available) return;
  sendChat(t("Find out about {company} and fill in all its fields: industry, strength, headquarters, size, its careers sites, its logo and the About, each with a source.", { company: c.name }));
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
  const note = h("textarea", { id: "app-note-new", rows: 2, placeholder: t("What happened? e.g. 笔试已交"), spellcheck: "false" });
  const when = h("input", { type: "date", id: "app-note-on", value: today(), "aria-label": t("When") });
  return [
    h("div", { class: "row" }, backTo("apps"), h("button", { class: "btn sm ghost", onclick: () => openHunt("company", c.id) }, icon("globe"), c.name)),
    h("div", { class: "ed-head" }, h("h1", null, h("span", { class: "co" }, c.name, " / "), h("span", { class: "r" }, a.role)),
      h("span", { class: "spacer" }), status.el,
      h("button", { class: "btn icon ghost", title: t("More"), "aria-label": t("More"), onclick: (e) => openMenu(e.currentTarget, [
        a.url && { label: t("Open the role's page"), icon: "external", onClick: () => openInBrowser(a.url) },
        a.co_app && { label: t("Its evaluation in {name}", { name: jobsTool().name }), icon: "bolt", onClick: () => openPosting(String(a.co_app)) },
        { label: t("Delete this application"), icon: "trash", danger: true, onClick: () => deleteApp(a) }], { align: "end" }) }, icon("more"))),
    card(t("Progress"), a.applied ? t("applied {date}", { date: a.applied }) : t("not applied yet"), [
      h("div", { class: "grid2" }, hfield(t("Next step"), a.next, save("next"), { placeholder: t("e.g. 等二面通知") }),
        hfield(t("By"), a.due, save("due"), { type: "date" })),
      h("div", { class: "field" }, h("label", { for: "app-note-new" }, t("Add to the timeline")), note,
        h("div", { class: "row" }, when, h("span", { class: "spacer" }), h("button", { class: "btn sm primary", id: "app-note-add", onclick: () => addAppNote(a, note.value, when.value) }, icon("plus"), t("Add")))),
      timeline(v.events, [a]),
    ]),
    card(t("The role"), "", [
      h("div", { class: "grid2" }, hfield(t("Role"), a.role, save("role")), hfield(t("Link"), a.url, save("url"), { placeholder: "https://…" })),
      h("div", { class: "grid2" }, hfield(t("Deadline"), a.deadline, save("deadline"), { type: "date", id: "app-deadline" }), h("span")),
      h("div", { class: "field" }, h("label", null, t("How it goes in")), h("div", { class: "row wrap" }, channel, plat && plat.el,
        a.channel === "site" && h("span", { class: "muted" }, t("choice {n} on {company}'s site", { n: a.order, company: c.name })))),
      hfield(t("Notes"), a.note, save("note"), { multiline: true }),
    ]),
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
    ]),
  ];
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
        e.source === "chat" && h("span", { class: "tl-src" }, t("by Claude"))),
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
        h("span", null, t("Scan the companies' sites once a day, when the window opens"), h("span", { class: "hint" }, t("by their providers and recorded routes; never Claude")))); })());
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
