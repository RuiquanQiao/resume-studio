// Autofill workspace (Ctrl+3): keep online profiles (the sites the user lists: LinkedIn, BOSS直聘, any
// other) in step with a resume version.
// Resume Studio drives its own browser (core/browser.py, no extension). Claude reads the profile there;
// this page shows what the site has next to what the resume says, the user ticks and edits the changes,
// and Claude makes exactly those, reporting each one back (autofill/api.py). Nothing changes on a site
// before the user presses "Make these changes".
// Loaded after jobs.js and before app.js: only definitions.
"use strict";

const F = {
  sites: null,           // GET /api/autofill: [{id, name, read_at, signed_out, changes}]
  browser: null,         // the browser's status
  site: "linkedin",
  view: {},              // site id -> GET /api/autofill/<site>
  open: {},              // plan item id -> expanded (or collapsed) by the user
  showSame: false,
  editing: null,         // "<item id>|<field>": the field whose text box is open (the others show their diff)
  unfolded: new Set(),   // the same, for diffs the user unfolded
  saveTimer: 0,
  loading: null,
};

const OPS = { add: "New", update: "Change", remove: "Only on {site}", same: "Already the same" };
const APPLIED = { doing: "Working on it…", done: "Done", failed: "Didn't work", skipped: "Skipped" };
const SECTION_NAMES = { basics: "Headline and about", experience: "Experience", education: "Education", projects: "Projects",
  awards: "Honours and awards", skills: "Skills" };
const FIELD_NAMES = {
  headline: "Headline", about: "About", title: "Title", org: "Organisation", location: "Location", start: "Start", end: "End",
  description: "Description", degree: "Degree", name: "Project name", role: "Your role", link: "Link", issuer: "Issued by",
  date: "Date", skills: "Skills to add",
};
const fieldName = (section, name) => {
  if (name === "org") return t(section === "education" ? "School" : "Company");
  if (name === "about" && fillSite().preset === "boss") return t("Personal strengths (个人优势)");
  return t(FIELD_NAMES[name] || name);
};
const fillView = () => F.view[F.site] || null;
// what the browser shows, in a few words ("" for an empty tab)
const pageName = (b) => (b && b.url && b.url !== "about:blank" ? b.title || b.url : "");
const SITE_NAMES = { linkedin: "LinkedIn", boss: "BOSS直聘" };   // before the list has loaded
const fillSite = () => ((F.sites || []).find((s) => s.id === F.site)) || { id: F.site, name: SITE_NAMES[F.site] || F.site };

// ================================================================ data
async function loadFill() {
  if (F.loading) return F.loading;
  F.loading = (async () => {
    try {
      const r = await api("GET", "/api/autofill");
      F.sites = r.sites; F.presets = r.presets; F.browser = r.browser;
      if (!F.sites.length) { paintFill(); renderWsTools(); return; }
      if (!F.sites.some((s) => s.id === F.site)) F.site = F.sites[0].id;
      await loadFillSite(F.site);
    } catch (e) { toast(e.message); }
    finally { F.loading = null; }
  })();
  return F.loading;
}

async function loadFillSite(id = F.site, { keepEdits = false, quiet = false } = {}) {
  try {
    const v = await api("GET", "/api/autofill/" + encodeURIComponent(id));
    F.browser = v.browser;
    // an edit typed while the answer was on its way stays as typed
    if (keepEdits && F.view[id]) carryEdits(F.view[id], v);
    F.view[id] = v;
  } catch (e) { if (!quiet) toast(e.message); return; }
  if (id !== F.site) return;   // the user went on to another site meanwhile
  if (W.ws === "fill") { if (fillTyping()) paintFillFoot(); else paintFill(); renderWsTools(); }
}

// the user is typing in one of the plan's fields: repainting would take the caret away
const fillTyping = () => !!(document.activeElement && document.activeElement.closest(".fillspace textarea"));

function carryEdits(old, next) {
  const before = new Map(old.plan.items.map((i) => [i.id, i]));
  for (const it of next.plan.items) {
    const o = before.get(it.id);
    if (!o) continue;
    for (const f of it.fields) {
      const of = o.fields.find((x) => x.name === f.name);
      if (of && of.edited && of.to !== f.to) { f.to = of.to; f.edited = true; }
    }
  }
}

// the user's ticks and edits, as the server keeps them
function choicesOf(v) {
  const out = {};
  for (const it of v.plan.items) {
    const fields = Object.fromEntries(it.fields.filter((f) => f.edited).map((f) => [f.name, f.to]));
    out[it.id] = { checked: it.checked, ...(Object.keys(fields).length ? { fields } : {}) };
  }
  return out;
}

async function saveChoices({ now = false } = {}) {
  clearTimeout(F.saveTimer);
  const site = F.site;
  const run = async () => {
    const v = F.view[site];
    if (!v || !(F.sites || []).some((s) => s.id === site)) return;
    try {
      const next = await api("PUT", "/api/autofill/" + encodeURIComponent(site), { choices: choicesOf(v) });
      carryEdits(F.view[site], next);
      F.view[site] = next;
      if (W.ws === "fill" && !fillTyping()) paintFill();
      else paintFillFoot();
    } catch (e) { toast(e.message); }
  };
  if (now) return run();
  F.saveTimer = setTimeout(run, 600);
}

async function chooseTarget(fields) {
  try {
    F.view[F.site] = await api("PUT", "/api/autofill/" + encodeURIComponent(F.site), fields);
  } catch (e) { toast(e.message); }
  paintFill();
}

// a chat the server started for this workspace: show it on the left
async function fillChat(url, body = {}) {
  if (!ui.chat.available) { toast(t("The claude command was not found")); return null; }
  let r;
  try { r = await api("POST", url, { ...chatSettings(), ...body }); }
  catch (e) { toast(e.message); return null; }
  ui.chat.list = [r.chat, ...ui.chat.list.filter((c) => c.id !== r.chat.id)];
  await openChat(r.chat.id);
  return r;
}

async function readProfile() {
  const r = await fillChat(`/api/autofill/${F.site}/read`);
  if (r) loadFillSite();
}

async function applyPlan() {
  const v = fillView();
  const n = v ? v.plan.checked : 0;
  if (!n) return;
  await saveChoices({ now: true });
  const r = await fillChat(`/api/autofill/${F.site}/apply`);
  if (r) loadFillSite();
}

async function openSite() {
  try {
    const r = await api("POST", `/api/autofill/${F.site}/open`, {});
    F.browser = r.browser;
    if (r.signed_out) toast(t("Sign in to {site} in the browser window; Resume Studio's browser keeps you signed in.", { site: fillSite().name }), { ms: 7000 });
  } catch (e) { toast(e.message, { ms: 7000 }); }
  loadFillSite();
}

async function showBrowser() {
  try { F.browser = await api("POST", "/api/browser/start", {}); }
  catch (e) { toast(e.message, { ms: 7000 }); }
  paintFill(); renderWsTools();
}

function listenFill(es) {
  es.addEventListener("autofill", (m) => {
    const d = JSON.parse(m.data || "{}");
    if (!F.sites) return;
    const had = F.sites.map((s) => s.id).join();
    if (F.sites.length && F.site !== F.gone && (!d.site || d.site === F.site)) loadFillSite(F.site, { keepEdits: true, quiet: true });
    api("GET", "/api/autofill").then((r) => {
      F.sites = r.sites; F.presets = r.presets;
      // the list itself changed (here or in another window): the page follows it
      if (had !== F.sites.map((s) => s.id).join() || !F.sites.some((s) => s.id === F.site)) fillSitesChanged();
      else if (W.ws === "fill") paintFillSites();
    }).catch(() => {});
  });
  es.addEventListener("browser", (m) => {
    F.browser = JSON.parse(m.data || "{}");
    if (W.ws === "fill") { paintFillBrowser(); renderWsTools(); }
  });
}

// the resume changed: what each site should say did too
let fillResumeTimer = 0;
function fillAfterResumeChange() {
  clearTimeout(fillResumeTimer);
  fillResumeTimer = setTimeout(() => { if (F.sites) loadFillSite(F.site, { keepEdits: true }); }, 1200);
}

// ================================================================ the page
function paintFill() {
  const el = $("#fillspace");
  if (!el) return;
  if (F.sites && !F.sites.length) { el.replaceChildren(h("div", { class: "fill-scroll" }, h("div", { class: "fill-wrap" }, noSitesCard()))); return; }
  if (!F.sites || !fillView()) {
    el.replaceChildren(h("div", { class: "fill-wrap" }, h("div", { class: "muted" }, t("Loading…"))));
    return;
  }
  const wrap = el.querySelector(".fill-scroll");
  const scroll = wrap ? wrap.scrollTop : 0;
  const v = fillView();
  el.replaceChildren(
    h("div", { class: "fill-scroll" },
      h("div", { class: "fill-wrap" },
        h("div", { class: "fill-head" },
          h("div", { class: "fh-text" }, h("h1", null, t("Your online profiles")),
            h("p", null, t("Resume Studio keeps them saying what your resume says. Claude reads the profile in Resume Studio's own browser, you tick the changes, and Claude makes them."))),
          h("div", { id: "fill-sites", class: "fill-sites" })),
        h("div", { id: "fill-browser" }),
        targetCard(v),
        planArea(v))),
    h("div", { id: "fill-foot", class: "fill-foot" }));
  paintFillSites();
  paintFillBrowser();
  paintFillFoot();
  const nw = el.querySelector(".fill-scroll");
  if (nw) nw.scrollTop = scroll;
  for (const ta of el.querySelectorAll("textarea")) autosizeFull(ta);
}

function autosizeFull(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 2 + "px"; }

function paintFillSites() {
  const box = $("#fill-sites");
  if (!box) return;
  box.replaceChildren(...(F.sites || []).map((s) => h("button", { type: "button", role: "tab", class: "fs-site" + (s.id === F.site ? " on" : ""),
    "aria-selected": String(s.id === F.site), "data-site": s.id, onclick: () => switchFillSite(s.id),
    oncontextmenu: (e) => { e.preventDefault(); siteMenu(e.currentTarget, s); } },
  h("span", { class: "fs-name" }, s.name),
  s.signed_out ? h("span", { class: "fs-n warn", title: t("Not signed in") }, "!")
    : s.changes ? h("span", { class: "fs-n", title: t("{n} change ticked", { n: s.changes }, "{n} changes ticked") }, s.changes) : null)),
  h("button", { type: "button", class: "fs-site fs-add", id: "fill-add-site", title: t("Add a site"), "aria-label": t("Add a site"),
    onclick: () => siteDialog(null) }, icon("plus")));
}

// ================================================================ the user's sites
function siteMenu(anchor, s) {
  const i = F.sites.findIndex((x) => x.id === s.id);
  openMenu(anchor, [
    { label: t("Site settings…"), icon: "settings", onClick: () => siteDialog(s) },
    i > 0 && { label: t("Move left"), icon: "chevLeft", onClick: () => moveSite(s, i - 1) },
    i < F.sites.length - 1 && { label: t("Move right"), icon: "chevRight", onClick: () => moveSite(s, i + 1) },
    { label: t("Remove from the list"), icon: "trash", danger: true, onClick: () => removeSite(s) },
  ].filter(Boolean));
}

async function moveSite(s, index) {
  try { F.sites = (await api("PUT", `/api/autofill/${encodeURIComponent(s.id)}/settings`, { index })).sites; }
  catch (e) { toast(e.message); }
  paintFillSites();
}

async function removeSite(s) {
  const ok = await modal({ title: t("Remove {site} from the list?", { site: s.name }),
    message: t("Nothing changes on {site} itself. What Claude read there and your ticks stay saved: add the site again to get them back.", { site: s.name }),
    ok: t("Remove"), danger: true });
  if (!ok) return;
  F.gone = s.id;   // its "changed" event may come before this answer: nothing asks for it meanwhile
  try { const r = await api("DELETE", "/api/autofill/" + encodeURIComponent(s.id)); F.sites = r.sites; F.presets = r.presets; }
  catch (e) { toast(e.message); return; }
  finally { F.gone = null; }
  delete F.view[s.id];
  fillSitesChanged();
}

// the list gained or lost a site: show one that is on it (a new one, if given)
async function fillSitesChanged(pick = null) {
  if (pick) F.site = pick;
  if (F.sites.length && !F.sites.some((s) => s.id === F.site)) F.site = F.sites[0].id;
  store("fill-site", F.sites.length ? F.site : null);
  F.open = {};
  if (W.ws !== "fill") return;
  if (F.sites.length) { paintFill(); await loadFillSite(F.site); } else paintFill();
  renderWsTools();
  if (!ui.chat.messages.length && !ui.chat.running) renderChat();
}

function noSitesCard() {
  return h("div", { class: "fill-card fill-start" },
    h("h2", null, t("Which sites do you keep a profile on?")),
    h("p", { class: "muted" }, t("Add the sites whose profile should say what your resume says. Resume Studio knows a few; any other works too: Claude finds its way around the first time it reads it.")),
    h("div", { class: "row wrap" },
      (F.presets || []).map((p) => h("button", { class: "btn", onclick: () => addSite({ preset: p.id }) }, icon("plus"), p.name)),
      h("button", { class: "btn primary", onclick: () => siteDialog(null) }, icon("plus"), t("Another site…"))));
}

async function addSite(body) {
  let r;
  try { r = await api("POST", "/api/autofill/sites", body); }
  catch (e) { toast(e.message, { ms: 7000 }); return false; }
  F.sites = r.sites; F.presets = r.presets;
  await saveChoices({ now: true });
  await fillSitesChanged(r.site.id);
  return true;
}

// add a site (s = null) or change one; nothing is saved before "Add" / "Done"
function siteDialog(s) {
  const root = $("#modal-root");
  if (root.firstChild) return;
  const preset = s && s.preset ? (F.presets || []).find((p) => p.id === s.preset) : null;
  const ls = langs();
  let lang = s ? s.lang : "";
  const name = h("input", { id: "site-name", value: s ? s.name : "", placeholder: preset ? preset.name : t("e.g. SEEK, 猎聘, 实习僧"), spellcheck: "false" });
  const home = h("input", { id: "site-home", value: s ? s.home : "", placeholder: preset ? preset.home : "https://", spellcheck: "false", inputmode: "url" });
  const notes = h("textarea", { id: "site-notes", rows: 3, spellcheck: "false",
    placeholder: t("e.g. My online resume is under 我的 → 在线简历. Keep descriptions under 300 characters.") }, s ? s.notes || "" : "");
  const close = () => { root.replaceChildren(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  const done = async () => {
    const body = { name: name.value, home: home.value, notes: notes.value, ...(lang ? { lang } : {}) };
    if (s) {
      try { const r = await api("PUT", `/api/autofill/${encodeURIComponent(s.id)}/settings`, body); F.sites = r.sites; F.presets = r.presets; }
      catch (e) { toast(e.message, { ms: 7000 }); return; }
      close();
      await loadFillSite(F.site);
      paintFillSites();
      return;
    }
    if (!home.value.trim()) { toast(t("Give the site's address: the page with your profile, or its home page")); home.focus(); return; }
    if (await addSite(body)) close();
  };
  const presetRow = !s && (F.presets || []).some((p) => !p.added) && h("div", { class: "field" },
    h("label", null, t("Sites Resume Studio knows"), h("span", { class: "hint" }, t("Each comes with notes for Claude about its editor"))),
    h("div", { class: "row wrap" }, F.presets.filter((p) => !p.added).map((p) =>
      h("button", { class: "btn", onclick: async () => { if (await addSite({ preset: p.id })) close(); } }, icon("plus"), p.name))));
  root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal site-dialog", role: "dialog", "aria-modal": "true", "aria-label": s ? t("Site settings") : t("Add a site") },
      h("h3", null, s ? t("{site} settings", { site: s.name }) : t("Add a site")),
      presetRow,
      !s && presetRow && h("div", { class: "fd-or" }, t("or any other site")),
      h("div", { class: "field" }, h("label", { for: "site-name" }, t("Name")), name),
      h("div", { class: "field" }, h("label", { for: "site-home" }, t("Address"),
        h("span", { class: "hint" }, t("The page with your profile, or the site's home page: Claude finds the profile from there"))), home),
      ls.length > 1 && h("div", { class: "field" }, h("label", null, t("Resume language"), h("span", { class: "hint" }, t("What the profile usually gets; you can switch it on the page too"))),
        uiSeg(ls.map((l) => ({ value: l, label: langName(l) })), lang || ls[0], (l) => { lang = l; }, { label: t("Resume language") })),
      h("div", { class: "field" }, h("label", { for: "site-notes" }, t("Notes for Claude"),
        h("span", { class: "hint" }, t("Optional: where things are on this site, what to watch out for. Claude reads them every time"))), notes),
      s && !s.preset && h("p", { class: "muted" }, s.learned
        ? t("Claude learned what this site has when it last read it: {list}.", { list: s.sections.map((x) => t(SECTION_NAMES[x] || x)).join(", ") })
        : t("Claude works out which sections and limits this site has the first time it reads it.")),
      h("div", { class: "acts" },
        s && h("button", { class: "btn danger-soft", onclick: () => { close(); removeSite(s); } }, icon("trash"), t("Remove from the list")),
        h("span", { class: "spacer" }),
        h("button", { class: "btn", onclick: close }, t("Cancel")),
        h("button", { class: "btn primary", onclick: done }, s ? t("Done") : t("Add"))))));
  setTimeout(() => name.focus(), 0);
}

async function switchFillSite(id) {
  if (id === F.site) return;
  await saveChoices({ now: true });
  F.site = id;
  store("fill-site", id);
  F.open = {}; F.editing = null;
  paintFill();   // its saved view right away (or "Loading…"), then the fresh one
  await loadFillSite(id);
  renderWsTools();
  if (!ui.chat.messages.length && !ui.chat.running) renderChat();   // its suggestions name the site
}

function paintFillBrowser() {
  const box = $("#fill-browser");
  if (!box) return;
  const b = F.browser || { state: "off" };
  const v = fillView();
  const site = fillSite();
  const out = [];
  if (v && v.signed_out) {
    out.push(h("div", { class: "fill-banner warn" }, icon("user"),
      h("span", { class: "nb-text" }, t("{site} wants you to sign in. Sign in in the browser window (Resume Studio's browser remembers it), then read the profile again.", { site: site.name })),
      h("button", { class: "btn sm primary", onclick: openSite }, icon("external"), t("Show the sign-in page"))));
  }
  if (!b.found) {
    out.push(h("div", { class: "fill-banner warn" }, icon("alert"),
      h("span", { class: "nb-text" }, t("Autofill needs Google Chrome or Microsoft Edge on this computer. Install one, then come back here."))));
  } else if (b.state === "error") {
    out.push(h("div", { class: "fill-banner warn" }, icon("alert"), h("span", { class: "nb-text" }, t("The browser didn't start: {error}", { error: b.error })),
      h("button", { class: "btn sm", onclick: showBrowser }, icon("refresh"), t("Try again"))));
  }
  const running = b.state === "running";
  out.push(h("div", { class: "fill-browser" },
    h("span", { class: "fb-dot" + (running ? " on" : b.state === "starting" ? " busy" : "") }),
    h("span", { class: "fb-text" },
      h("b", null, b.browser ? t("Resume Studio's browser ({browser})", { browser: b.browser }) : t("Resume Studio's browser")),
      h("span", { class: "muted" }, running ? (pageName(b) || t("open")) : b.state === "starting" ? t("starting…")
        : t("a window of its own; your sign-ins stay in it, apart from your everyday browser"))),
    h("span", { class: "spacer" }),
    running && h("button", { class: "btn sm ghost", onclick: showBrowser, title: t("Bring the browser window to the front") }, t("Show")),
    h("button", { class: "btn sm", disabled: !b.found, onclick: openSite }, icon("external"), t("Open {site}", { site: site.name })),
    h("button", { class: "btn sm icon ghost", id: "fill-site-settings", title: t("{site} settings", { site: site.name }),
      "aria-label": t("{site} settings", { site: site.name }), onclick: (e) => siteMenu(e.currentTarget, site) }, icon("settings"))));
  box.replaceChildren(...out);
}

function targetCard(v) {
  const site = fillSite();
  const vs = versions();
  const sel = uiSelect({ id: "fill-version", label: t("Resume version"), menuWidth: 300, value: v.version_id,
    options: vs.map((x) => ({ value: x.id, label: jobName(x), hint: x.id === ALL ? t("every entry") : null })),
    onChange: (id) => chooseTarget({ version_id: id }) });
  const ls = langs();
  const skipped = (v.desired.skipped || []);
  return h("div", { class: "fill-card fill-target" },
    h("div", { class: "ft-row" },
      h("span", { class: "ft-k" }, t("{site} should say what this resume says:", { site: site.name })),
      sel.el,
      ls.length > 1 && uiSeg(ls.map((l) => ({ value: l, label: langShort(l), tip: langName(l) })), v.lang, (l) => chooseTarget({ lang: l }),
        { label: t("Resume language") })),
    h("div", { class: "ft-note" }, v.version_id === ALL
      ? t("“All entries” puts your whole library on the profile; a job puts only the entries it picks.")
      : t("Only the entries this job picks go on the profile; switch to “All entries” for your whole library.")),
    skipped.length > 0 && h("div", { class: "ft-note" }, t("{site} has no place for: {list}", { site: site.name,
      list: skipped.map((s) => `${s.label} (${s.section})`).join(", ") })));
}

function planArea(v) {
  const site = fillSite();
  const p = v.plan;
  const reading = !!v.reading;
  if (!p.read) {
    return h("div", { class: "fill-card fill-start" },
      h("h2", null, t("Compare your {site} profile with your resume", { site: site.name })),
      h("ol", { class: "fill-steps" },
        h("li", null, t("Sign in to {site} once in Resume Studio's browser (“Open {site}” above).", { site: site.name })),
        h("li", null, t("Claude reads your profile there and lists every difference from your resume.")),
        h("li", null, t("You tick and edit the changes; Claude makes only those, one at a time, and you watch it happen."))),
      h("p", { class: "muted" }, t("Nothing changes on {site} until you press “Make these changes”.", { site: site.name })),
      h("div", { class: "row" },
        h("button", { class: "btn primary", disabled: !ui.chat.available || reading, onclick: readProfile }, icon("sparkle"),
          reading ? t("Claude is reading…") : t("Read my {site} profile", { site: site.name }))));
  }
  const items = p.items;
  const pending = items.filter((i) => i.op !== "same");
  const same = items.filter((i) => i.op === "same");
  const groups = [];
  for (const sec of ["basics", "experience", "projects", "education", "awards", "skills"]) {
    const list = pending.filter((i) => i.section === sec);
    if (list.length) groups.push(h("div", { class: "fill-group" }, h("div", { class: "fg-title" }, t(SECTION_NAMES[sec])), list.map((i) => planItem(v, i))));
  }
  const ago = v.read_at ? relTime(v.read_at) : "";
  return h("div", { class: "fill-plan" },
    h("div", { class: "fp-head" },
      h("span", null, h("b", null, pending.length ? t("{n} difference", { n: pending.length }, "{n} differences") : t("Your profile already says what your resume says")),
        h("span", { class: "muted" }, " · " + t("read {when}", { when: ago }))),
      h("span", { class: "spacer" }),
      pending.length > 1 && h("button", { class: "btn sm ghost", onclick: () => tickAll(true) }, t("Tick all")),
      pending.length > 1 && h("button", { class: "btn sm ghost", onclick: () => tickAll(false) }, t("Untick all")),
      h("button", { class: "btn sm", disabled: !ui.chat.available || v.reading, onclick: readProfile }, icon("refresh"), v.reading ? t("Claude is reading…") : t("Read again"))),
    v.current && v.current.notes && h("div", { class: "fill-banner" }, icon("sparkle"), h("span", { class: "nb-text" }, v.current.notes)),
    groups,
    same.length > 0 && h("div", { class: "fill-group same" },
      h("button", { type: "button", class: "fg-title linkish", "aria-expanded": String(F.showSame), onclick: () => { F.showSame = !F.showSame; paintFill(); } },
        icon(F.showSame ? "down" : "chevRight"), t("{n} already the same", { n: same.length })),
      F.showSame && same.map((i) => h("div", { class: "fi same" }, h("span", { class: "fi-label" }, itemLabel(i))))));
}

function itemLabel(i) {
  if (i.section === "basics") return fieldName("basics", i.key, F.site);
  if (i.section === "skills") return t("Skills");
  return i.label;
}

function tickAll(on) {
  const v = fillView();
  for (const i of v.plan.items) if (i.op !== "same") i.checked = on;
  paintFill();
  saveChoices({ now: true });
}

const busyItems = (v) => new Set(v.applying || []);

function planItem(v, i) {
  const site = fillSite();
  const busy = busyItems(v).has(i.id);
  const open = F.open[i.id] ?? (i.op === "update" || i.section === "basics");
  const a = i.applied;
  const box = h("button", { type: "button", class: "check" + (i.checked ? " on" : ""), role: "checkbox", "aria-checked": String(i.checked),
    disabled: busy, "aria-label": itemLabel(i),
    onclick: () => {
      i.checked = !i.checked;
      box.classList.toggle("on", i.checked); box.setAttribute("aria-checked", String(i.checked));
      card.classList.toggle("off", !i.checked); saveChoices({ now: true }); paintFillFoot();
    } });
  const card = h("div", { class: "fi op-" + i.op + (i.checked ? "" : " off"), "data-id": i.id },
    h("div", { class: "fi-row" },
      box,
      h("span", { class: "fi-op" }, t(OPS[i.op], { site: site.name })),
      h("button", { type: "button", class: "fi-label", "aria-expanded": String(open), onclick: () => { F.open[i.id] = !open; paintFill(); } },
        h("span", { class: "fi-name" }, itemLabel(i)),
        i.op === "update" && i.site_label && i.site_label !== i.label && h("span", { class: "fi-was" }, t("on {site}: {label}", { site: site.name, label: i.site_label })),
        i.op === "add" && !open && h("span", { class: "fi-was" }, addSummary(i))),
      a && h("span", { class: "fi-state s-" + a.state, title: a.note || null }, a.state === "doing" ? h("span", { class: "spin" }) : null, t(APPLIED[a.state] || a.state)),
      h("button", { type: "button", class: "btn icon sm ghost", title: open ? t("Hide the fields") : t("Show the fields"), onclick: () => { F.open[i.id] = !open; paintFill(); } }, icon(open ? "up" : "down"))),
    a && a.state === "failed" && a.note && h("div", { class: "fi-note" }, icon("alert"), a.note),
    open && h("div", { class: "fi-body" }, i.drafted && h("div", { class: "fi-draft" }, icon("sparkle"),
      t("Your resume has no text for this, so Claude drafted it from your entries. Edit it as you like before it goes up.")),
    i.fields.map((f) => fieldRow(v, i, f, busy))));
  return card;
}

// a collapsed new item: what goes up, in a few words (the skills themselves, or the dates)
function addSummary(i) {
  const get = (n) => (i.fields.find((f) => f.name === n) || {}).to || "";
  if (i.section === "skills") return get("skills");
  const when = (s) => (s === "present" ? t("present") : s);
  const dates = [get("start"), get("end")].filter(Boolean).map(when).join(" – ") || when(get("date"));
  return dates || t("{n} field", { n: i.fields.length }, "{n} fields");
}

// One field: what changes on the site, as a diff (git style: − what goes, + what comes). A click on
// it (or "Edit") turns it into the text box; leaving the box shows the diff of the edited text.
function fieldRow(v, i, f, busy) {
  const site = fillSite();
  const name = fieldName(i.section, f.name, F.site);
  const shown = (s) => (["start", "end", "date"].includes(f.name) && s === "present" ? t("present") : s || "");
  const key = i.id + "|" + f.name;
  if (i.op === "remove") {
    return h("div", { class: "ff" }, h("div", { class: "ff-name" }, name), diffEl(shown(f.from), "", key));
  }
  const editing = F.editing === key && !busy;
  const count = h("span", { class: "ff-count" + (f.over ? " over" : "") });
  const paintCount = () => {
    count.textContent = f.limit ? `${f.to.length.toLocaleString()} / ${f.limit.toLocaleString()}` : "";
    count.classList.toggle("over", !!f.limit && f.to.length > f.limit);
  };
  const again = () => { const next = fieldRow(v, i, f, busy); row.replaceWith(next); return next; };
  const edit = () => {
    if (busy) return;
    F.editing = key;
    const ta = again().querySelector("textarea");
    autosizeFull(ta); ta.focus();
  };
  const reset = h("button", { type: "button", class: "linkish ff-reset" + (f.edited ? "" : " hidden"), disabled: busy,
    title: t("Go back to what the resume says"), onmousedown: (e) => e.preventDefault(),
    onclick: () => { F.editing = null; f.edited = false; if (document.activeElement) document.activeElement.blur(); saveChoices({ now: true }); } }, t("Undo my edit"));
  paintCount();
  let body;
  if (editing) {
    const ta = h("textarea", { class: "ff-to", rows: 1, spellcheck: "false", "aria-label": name,
      oninput: (e) => {
        f.to = e.target.value; f.edited = true; f.over = !!f.limit && f.to.length > f.limit;
        autosizeFull(e.target); paintCount(); reset.classList.remove("hidden"); saveChoices(); paintFillFoot();
      },
      onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); e.target.blur(); } },
      onblur: () => { if (F.editing === key) F.editing = null; if (row.isConnected) again(); } }, f.to);
    body = [i.op === "update" && h("div", { class: "ff-from" }, h("span", { class: "ff-k" }, t("Now on {site}:", { site: site.name })), " ",
      f.from ? shown(f.from) : h("i", null, t("(empty)"))), ta];
  } else {
    const d = diffEl(i.op === "update" ? shown(f.from) : "", shown(f.to), key);
    if (!busy) {
      d.classList.add("editable");
      d.setAttribute("role", "button"); d.setAttribute("tabindex", "0");
      d.setAttribute("aria-label", t("Edit {field}", { field: name }));
      d.addEventListener("click", (e) => { if (!e.target.closest(".dl-fold") && !getSelection().toString()) edit(); });
      d.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === d) { e.preventDefault(); edit(); } });
    }
    body = [d];
  }
  const row = h("div", { class: "ff" + (editing ? " editing" : "") },
    h("div", { class: "ff-name" }, name, h("span", { class: "spacer" }), reset,
      !editing && !busy && h("button", { type: "button", class: "linkish ff-edit", onclick: edit }, t("Edit text")), count),
    body,
    f.over && h("div", { class: "ff-warn" }, t("{site} takes at most {n} characters here: shorten it before it goes up.", { site: site.name, n: f.limit.toLocaleString() })));
  return row;
}

// ================================================================ the diff (lines, then words inside a changed line)
const DIFF_CELLS = 400000;   // past this much work, a changed block is shown as replaced without the word marks
function lcsOps(a, b) {
  const n = a.length, m = b.length;
  if (n * m > DIFF_CELLS) return [...a.map((x) => ["del", x]), ...b.map((x) => ["add", x])];
  const w = m + 1, dp = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push(["same", a[i]]); i++; j++; }
    else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) out.push(["del", a[i++]]);
    else out.push(["add", b[j++]]);
  }
  while (i < n) out.push(["del", a[i++]]);
  while (j < m) out.push(["add", b[j++]]);
  return out;
}

// words for Latin scripts, one character each for Chinese, Japanese and Korean
const WORD = /[぀-ヿ㐀-鿿豈-﫿가-힯]|[\p{L}\p{N}_]+|\s+|[^\s\p{L}\p{N}_]/gu;

// a changed line next to its replacement: the words that differ are marked on both
function wordMarks(oldLine, newLine) {
  const ops = lcsOps(oldLine.match(WORD) || [], newLine.match(WORD) || []);
  const same = ops.filter(([k, s]) => k === "same" && s.trim()).reduce((n, [, s]) => n + s.length, 0);
  if (same < 0.4 * Math.max(oldLine.length, newLine.length)) return null;   // hardly alike: a plain replacement
  const side = (kind) => ops.filter(([k]) => k === "same" || k === kind)
    .map(([k, s]) => (k === "same" ? s : h("mark", { class: "w-" + kind }, s)));
  return { del: side("del"), add: side("add") };
}

function diffRows(from, to) {
  const ops = lcsOps(from ? from.split("\n") : [], to ? to.split("\n") : []);
  const rows = [];
  for (let k = 0; k < ops.length;) {
    if (ops[k][0] === "same") { rows.push({ kind: "same", text: ops[k][1] }); k++; continue; }
    const dels = [], adds = [];
    while (k < ops.length && ops[k][0] !== "same") (ops[k][0] === "del" ? dels : adds).push(ops[k++][1]);
    const marks = dels.map((d, x) => (x < adds.length ? wordMarks(d, adds[x]) : null));
    dels.forEach((d, x) => rows.push({ kind: "del", text: d, parts: marks[x] && marks[x].del }));
    adds.forEach((a, x) => rows.push({ kind: "add", text: a, parts: marks[x] && marks[x].add }));
  }
  return rows;
}

const DIFF_CONTEXT = 2;   // unchanged lines kept around a change; longer stretches fold
function diffEl(from, to, key) {
  const rows = diffRows(from, to);
  const box = h("div", { class: "diff" });
  const near = rows.map((r, x) => r.kind !== "same" ||
    rows.slice(Math.max(0, x - DIFF_CONTEXT), x + DIFF_CONTEXT + 1).some((o) => o.kind !== "same"));
  const line = (r) => h("div", { class: "dl dl-" + r.kind },
    h("span", { class: "dl-sign", "aria-hidden": "true" }, { del: "−", add: "+", same: " " }[r.kind]),
    h("span", { class: "dl-text" }, ...(r.parts || [r.text || " "])));
  const paint = (all) => {
    const out = [];
    for (let x = 0; x < rows.length;) {
      if (all || near[x] || !rows.some((r) => r.kind !== "same")) { out.push(line(rows[x])); x++; continue; }
      let y = x;
      while (y < rows.length && !near[y]) y++;
      if (y - x < 2) { for (; x < y; x++) out.push(line(rows[x])); continue; }
      out.push(h("button", { type: "button", class: "dl dl-fold", onclick: (e) => { e.stopPropagation(); F.unfolded.add(key); paint(true); } },
        h("span", { class: "dl-sign" }, "⋯"), h("span", { class: "dl-text" }, t("{n} unchanged line", { n: y - x }, "{n} unchanged lines"))));
      x = y;
    }
    if (!rows.length) out.push(h("div", { class: "dl dl-same" }, h("span", { class: "dl-sign" }, " "), h("span", { class: "dl-text muted" }, t("(empty)"))));
    box.replaceChildren(...out);
  };
  paint(F.unfolded.has(key));
  return box;
}

function paintFillFoot() {
  const el = $("#fill-foot");
  if (!el) return;
  const v = fillView();
  if (!v || !v.plan.read) { el.replaceChildren(); el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const site = fillSite();
  const ticked = v.plan.items.filter((i) => i.checked && i.op !== "same");
  const over = ticked.some((i) => i.fields.some((f) => f.over));
  const applying = (v.applying || []).length > 0;
  const done = v.plan.items.filter((i) => i.applied && i.applied.state === "done").length;
  el.replaceChildren(...[
    h("span", { class: "ff-sum" }, applying ? t("Claude is making the changes in the browser; follow along in the chat.")
      : over ? t("Some text is over {site}'s limit.", { site: site.name })
      : ticked.length ? t("{n} change ticked", { n: ticked.length }, "{n} changes ticked") : t("Nothing ticked")),
    done > 0 && !applying && h("span", { class: "muted" }, t("{n} done so far", { n: done })),
    h("span", { class: "spacer" }),
    applying && h("button", { class: "btn", onclick: showBrowser }, t("Show the browser")),
    h("button", { class: "btn primary", id: "fill-apply", disabled: applying || over || !ticked.length || !ui.chat.available, onclick: applyPlan },
      icon("sparkle"), t("Make these changes on {site}", { site: site.name }))].filter(Boolean));
}

// ================================================================ the workspace frame
function renderFillTools(el) {
  const b = F.browser || {};
  el.replaceChildren(...[
    h("span", { class: "ws-here" }, h("span", { class: "fb-dot" + (b.state === "running" ? " on" : "") }),
      h("b", null, b.state === "running" ? (pageName(b) || t("Resume Studio's browser")) : t("The browser isn't open"))),
    h("span", { class: "spacer" }),
    h("button", { class: "btn ghost", disabled: b.found === false, title: t("Bring the browser window to the front"), onclick: showBrowser },
      icon("external"), h("span", { class: "lbl" }, t("Show the browser"))),
    b.state === "running" && h("button", { class: "btn icon ghost", title: t("More"), onclick: (e) => openMenu(e.currentTarget, [
      { label: t("Close the browser"), icon: "x", onClick: async () => { F.browser = await api("POST", "/api/browser/stop", {}); paintFillBrowser(); renderWsTools(); } },
    ], { align: "end", onClose: () => setTimeout(renderWsTools, 0) }) }, icon("more"))].filter(Boolean));
}

function fillWelcome() {
  const site = fillSite();
  const suggest = F.sites && !F.sites.length ? [] : [   // no site on the list: nothing to name
    t("Does my {site} profile sell me well for the roles I want?", { site: site.name }),
    t("Add my newest project to {site}", { site: site.name }),
  ];
  return h("div", { class: "chat-welcome" },
    h("div", { class: "t" }, t("Claude, with Resume Studio's browser")),
    h("div", null, ui.chat.available ? t("In these chats Claude can look at and work in the browser on the right: reading a profile, changing one thing, checking a page. It changes a site only when you ask.")
      : t("The claude command was not found. Install Claude Code and sign in first.")),
    ui.chat.available && h("div", { class: "suggest" }, suggest.map((s) => h("button", { onclick: () => sendChat(s) }, s))));
}
