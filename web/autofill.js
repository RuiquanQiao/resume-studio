// Autofill workspace (Ctrl+3): a local copy of each online profile (the sites the user lists:
// LinkedIn, BOSS直聘, any other), kept in sync with the site like a working copy with its remote.
// Resume Studio drives its own browser (core/browser.py, no extension). The first time, Claude reads the
// profile there and records a read route; after that "Check for updates" reads the site by that route,
// without Claude: changes made on the site come into the copy, the user's own edits here wait to go up,
// and a field changed on both sides is a conflict to settle. Pushing is Claude's, one ticked change at a
// time, each reported back (autofill/api.py). Nothing changes on a site before "Make these changes".
// Loaded after jobs.js and before app.js: only definitions.
"use strict";

const F = {
  sites: null,           // GET /api/autofill: [{id, name, synced_at, signed_out, changes, conflicts}]
  browser: null,         // the browser's status
  site: "linkedin",
  view: {},              // site id -> GET /api/autofill/<site>
  tab: "copy",           // "copy" (the local copy) | "push" (the changes waiting to go up)
  open: {},              // item id -> expanded (or collapsed) by the user
  unfolded: new Set(),   // diffs the user unfolded
  loading: null,
  edits: Promise.resolve(),   // the copy's edits go to the server one after another
};

const OPS = { add: "New", update: "Change", remove: "Remove" };
const APPLIED = { doing: "Working on it…", done: "Done", failed: "Didn't work", skipped: "Skipped" };
const SECTION_NAMES = { basics: "Headline and about", experience: "Experience", education: "Education", projects: "Projects",
  awards: "Honours and awards", skills: "Skills" };
const ADD_ONE = { experience: "Add a position", education: "Add a school", projects: "Add a project", awards: "Add an award" };
const FIELD_NAMES = {
  headline: "Headline", about: "About", title: "Title", org: "Organisation", location: "Location", start: "Start", end: "End",
  description: "Description", degree: "Degree", name: "Project name", role: "Your role", link: "Link", issuer: "Issued by",
  date: "Date", skills: "Skills",
};
const LONG_FIELDS = new Set(["description", "about"]);
const DATE_FIELDS = new Set(["start", "end", "date"]);
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
// an item in a few words, the way autofill/profile.py label_of says it
const LABEL_PARTS = { experience: ["title", "org"], education: ["degree", "org"], projects: ["name", "role"], awards: ["title", "issuer"] };
const itemTitle = (section, fields) => (LABEL_PARTS[section] || []).map((k) => (fields || {})[k]).filter(Boolean).join(" · ") || t("(untitled)");

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

async function loadFillSite(id = F.site, { quiet = false } = {}) {
  try {
    const v = await api("GET", "/api/autofill/" + encodeURIComponent(id));
    F.browser = v.browser;
    F.view[id] = v;
  } catch (e) { if (!quiet) toast(e.message); return; }
  if (id !== F.site) return;   // the user went on to another site meanwhile
  if (W.ws === "fill") { paintFill(); renderWsTools(); }
}


// one edit to the local copy (autofill/service.py `edit`); edits go one after another, in order
function editCopy(op, { repaint = true } = {}) {
  const site = F.site;
  const run = async () => {
    try {
      const v = await api("PUT", `/api/autofill/${encodeURIComponent(site)}/local`, op);
      F.view[site] = v;
      if (site !== F.site || W.ws !== "fill") return v;
      if (repaint) paintFill(); else { paintFillFoot(); paintFillTabs(); }
      return v;
    } catch (e) { toast(e.message); return null; }
  };
  F.edits = F.edits.then(run, run);
  return F.edits;
}

// typing in a field: saved a moment after the last key
const typingTimers = {};
function typed(item, field, value) {
  const key = item + "|" + field;
  clearTimeout(typingTimers[key]);
  typingTimers[key] = setTimeout(() => { delete typingTimers[key]; editCopy({ op: "set", item, field, value }, { repaint: false }); }, 450);
}
async function flushTyping() {
  for (const k of Object.keys(typingTimers)) {
    clearTimeout(typingTimers[k]); delete typingTimers[k];
    const el = document.querySelector(`.fillspace [data-edit="${CSS.escape(k)}"]`);
    if (el) { const [item, field] = k.split("|"); editCopy({ op: "set", item, field, value: el.value }, { repaint: false }); }
  }
  await F.edits;
}

async function saveTicks(ticks) {
  const site = F.site;
  try { F.view[site] = await api("PUT", "/api/autofill/" + encodeURIComponent(site), { ticks }); }
  catch (e) { toast(e.message); }
  if (site === F.site && W.ws === "fill") paintFill();
}

async function chooseTarget(fields) {
  try { F.view[F.site] = await api("PUT", "/api/autofill/" + encodeURIComponent(F.site), fields); }
  catch (e) { toast(e.message); }
  paintFill();
}

// a chat the server started for this workspace: show it on the left
async function fillChat(url, body = {}) {
  if (!ui.chat.available) { toast(missingAgentText()); return null; }
  let r;
  try { r = await api("POST", url, { ...chatSettings(), ...body }); }
  catch (e) { toast(e.message); return null; }
  ui.chat.list = [r.chat, ...ui.chat.list.filter((c) => c.id !== r.chat.id)];
  await openChat(r.chat.id);
  return r;
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


async function checkSite() {
  await flushTyping();
  const v = fillView();
  if (v && !v.route.recorded && !ui.chat.available) { toast(missingAgentText()); return; }
  if (v && v.route.recorded) {
    try { await api("POST", `/api/autofill/${encodeURIComponent(F.site)}/check`, {}); }
    catch (e) { toast(e.message, { ms: 7000 }); }
    loadFillSite();
    return;
  }
  const r = await fillChat(`/api/autofill/${F.site}/check`);
  if (r) loadFillSite();
}

async function learnRoute() {
  await flushTyping();
  const r = await fillChat(`/api/autofill/${F.site}/learn`);
  if (r) loadFillSite();
}

async function applyPlan() {
  await flushTyping();
  const v = fillView();
  if (!v || !v.pending.some((p) => p.checked)) return;
  const r = await fillChat(`/api/autofill/${F.site}/apply`);
  if (r) loadFillSite();
}

function listenFill(es) {
  es.addEventListener("autofill", (m) => {
    const d = JSON.parse(m.data || "{}");
    if (!F.sites) return;
    const had = F.sites.map((s) => s.id).join();
    if (F.sites.length && F.site !== F.gone && (!d.site || d.site === F.site)) loadFillSite(F.site, { quiet: true });
    api("GET", "/api/autofill").then((r) => {
      F.sites = r.sites; F.presets = r.presets;
      // the list itself changed (here or in another window): the page follows it
      if (had !== F.sites.map((s) => s.id).join() || !F.sites.some((s) => s.id === F.site)) fillSitesChanged();
      else if (W.ws === "fill") paintFillSites();
    }).catch(() => {});
  });
  es.addEventListener("browser", (m) => {
    const was = F.browser;
    F.browser = JSON.parse(m.data || "{}");
    // a browser work started closes itself when the work is done; a few seconds before, the user may keep it
    if (F.browser.closing && !(was && was.closing)) {
      toast(t("Resume Studio's browser closes in a few seconds: the work in it is done."),
        { ms: 14000, action: { label: t("Keep it open"), fn: () => api("POST", "/api/browser/keep", {}).catch((e) => toast(e.message)) } });
    }
    paintSignin();
    if (W.ws === "fill") { paintFillBrowser(); renderWsTools(); }
  });
  api("GET", "/api/browser").then((b) => { if (!F.browser) F.browser = b; paintSignin(); }).catch(() => {});
}

// ================================================================ sign-in mode
// Some sites won't sign in a browser that is being driven (their risk checks see it and drop the request).
// Sign-in mode reopens Resume Studio's browser profile as a plain browser; the bar stays up, in every
// workspace, until the user switches back.
async function signInMode(url) {
  if (!/^https?:\/\//.test(url || "")) return;
  try { F.browser = await api("POST", "/api/browser/sign-in", { url }); }
  catch (e) { toast(e.message, { ms: 7000 }); }
  paintSignin();
  if (W.ws === "fill") { paintFillBrowser(); renderWsTools(); }
}

async function signedIn(btn) {
  if (btn) btn.disabled = true;
  try { F.browser = await api("POST", "/api/browser/sign-in/done", {}); }
  catch (e) { toast(e.message, { ms: 7000 }); if (btn) btn.disabled = false; }
  paintSignin();
  if (W.ws === "fill") { paintFillBrowser(); renderWsTools(); }
}

function paintSignin() {
  const el = $("#signin-bar");
  if (!el) return;
  const b = F.browser || {};
  if (b.state !== "signin") { el.replaceChildren(); el.classList.add("hidden"); return; }
  let where = b.signin_url || "";
  try { where = new URL(where).hostname.replace(/^www\./, ""); } catch (e) { /* keep it as it is */ }
  el.classList.remove("hidden");
  el.replaceChildren(icon("user"),
    h("span", { class: "nb-text" }, t("Sign in to {site} in the browser window that just opened. Resume Studio isn't controlling it, so the site sees an ordinary browser.", { site: where })),
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm primary", id: "signin-done", onclick: (e) => signedIn(e.currentTarget) }, icon("check"), t("I've signed in: switch back")));
}

// the resume changed: what it suggests for each copy did too
let fillResumeTimer = 0;
function fillAfterResumeChange() {
  clearTimeout(fillResumeTimer);
  fillResumeTimer = setTimeout(() => { if (F.sites && F.sites.length) loadFillSite(F.site, { quiet: true }); }, 1200);
}

// ================================================================ a platform's page (hunt.js's Platforms tab opens it)
function paintPlatform() {
  const el = $("#fillspace");
  if (!el) return;
  if (!F.sites || !fillView()) {
    el.replaceChildren(huntBar(), h("div", { class: "fill-wrap" }, backTo("platforms"), h("div", { class: "muted" }, t("Loading…"))));
    return;
  }
  const wrap = el.querySelector(".fill-scroll");
  const scroll = wrap ? wrap.scrollTop : 0;
  const v = fillView();
  el.replaceChildren(
    huntBar(),
    h("div", { class: "fill-scroll" },
      h("div", { class: "fill-wrap" },
        backTo("platforms"),
        h("div", { class: "fill-head" },
          h("div", { class: "fh-text" }, h("h1", null, t("Your profile on {site}", { site: fillSite().name })),
            h("p", null, t("Resume Studio keeps a copy of each profile. Edit it here or on the site: “Check for updates” brings the site's changes in, and yours go up when you say so."))),
          h("div", { id: "fill-sites", class: "fill-sites" })),
        h("div", { id: "fill-browser" }),
        syncCard(v),
        v.synced && h("div", { id: "fill-tabs" }),
        v.synced && (F.tab === "push" ? pushArea(v) : copyArea(v)))),
    h("div", { id: "fill-foot", class: "fill-foot" }));
  paintFillSites();
  paintFillBrowser();
  paintFillTabs();
  paintFillFoot();
  const nw = el.querySelector(".fill-scroll");
  if (nw) nw.scrollTop = scroll;
  for (const ta of el.querySelectorAll("textarea")) autosizeFull(ta);
}

// ---- the sync state: never read / reading / when, what came in, the route
function syncCard(v) {
  const site = fillSite();
  const reading = !!v.reading;
  if (!v.synced) {
    return h("div", { class: "fill-card fill-start" },
      h("h2", null, t("Read your {site} profile once", { site: site.name })),
      h("ol", { class: "fill-steps" },
        h("li", null, t("Sign in to {site} once in Resume Studio's browser (“Open {site}” above).", { site: site.name })),
        h("li", null, t("The AI reads your profile there and records how to read it: which pages, and where each field is.")),
        h("li", null, t("From then on “Check for updates” reads {site} by itself in seconds, without AI, and you edit the copy here.", { site: site.name }))),
      h("p", { class: "muted" }, t("Nothing changes on {site} until you press “Make these changes”.", { site: site.name })),
      h("div", { class: "row" },
        h("button", { class: "btn primary", id: "fill-check", disabled: !ui.chat.available || reading, onclick: checkSite }, icon("sparkle"),
          reading ? t("The AI is reading…") : t("Read {site}", { site: site.name }))));
  }
  const c = v.checking;
  const lc = v.last_check || {};
  const r = v.route;
  const status = c ? h("span", { class: "fs-status" }, h("span", { class: "spin" }),
    c.pages ? t("Reading {site}: page {n} of {total}…", { site: site.name, n: Math.max(1, c.page), total: c.pages }) : t("Reading {site}…", { site: site.name }))
    : reading ? h("span", { class: "fs-status" }, h("span", { class: "spin" }), t("The AI is reading…"))
    : lc.error ? h("span", { class: "fs-status bad" }, icon("alert"), lc.signed_out ? t("{site} wants you to sign in.", { site: site.name }) : t("The last check didn't finish."))
    : h("span", { class: "fs-status" }, icon("check"),
      (lc.mode === "claude" || !lc.at) && !lc.synced ? t("The AI read {site}", { site: site.name })   // no check yet: the copy is Claude's first read
        : lc.synced ? t("{n} change from {site} came into the copy", { n: lc.synced, site: site.name }, "{n} changes from {site} came into the copy")
        : t("Nothing new on {site}", { site: site.name }));
  const menu = (e) => openMenu(e.currentTarget, [
    { label: t("Let AI read {site} again and record the route anew", { site: site.name }), icon: "sparkle", onClick: learnRoute, disabled: !ui.chat.available || reading },
    { label: t("Open {site}", { site: site.name }), icon: "external", onClick: openSite },
  ], { align: "end" });
  return h("div", { class: "fill-card fill-sync" },
    h("div", { class: "fsy-row" },
      h("div", { class: "fsy-text" },
        h("b", null, t("Your copy of {site}", { site: site.name })),
        h("span", { class: "muted" }, v.synced_at ? t("synced {when}", { when: relTime(v.synced_at) }) : "")),
      status,
      h("span", { class: "spacer" }),
      h("button", { class: "btn", id: "fill-check", disabled: !!c || reading || (!r.recorded && !ui.chat.available), onclick: checkSite },
        icon("refresh"), t("Check for updates")),
      h("button", { class: "btn icon ghost", title: t("More"), "aria-label": t("More"), onclick: menu }, icon("more"))),
    h("div", { class: "fsy-route muted" }, r.recorded
      ? t("Reads {site} by the recorded route ({n} page), without AI.", { site: site.name, n: r.pages }, "Reads {site} by the recorded route ({n} pages), without AI.")
      : t("No read route yet: “Check for updates” lets the AI read {site} and record one.", { site: site.name })),
    r.broken && h("div", { class: "fill-banner warn" }, icon("alert"),
      h("span", { class: "nb-text" }, t("The route no longer reads {site} ({error}). The site has probably changed; the AI can read it again and record the route anew.", { site: site.name, error: r.broken })),
      h("button", { class: "btn sm primary", disabled: !ui.chat.available || reading, onclick: learnRoute }, icon("sparkle"), t("Record the route anew"))),
    (lc.notes || []).map((n) => h("div", { class: "fill-banner" }, icon("alert"), h("span", { class: "nb-text" },
      n.kind === "removed_there_changed_here"
        ? t("“{item}” is gone from {site}, but you had edited it here: it stays in your copy and goes up again if you push it.", { item: n.label, site: site.name })
        : t("“{item}” changed on {site}, but you had removed it here: it stays removed in your copy.", { item: n.label, site: site.name })))));
}

function paintFillTabs() {
  const box = $("#fill-tabs");
  const v = fillView();
  if (!box || !v) return;
  const n = v.pending.length;
  const nc = Object.keys(v.conflicts).length;
  box.replaceChildren(h("div", { class: "fill-viewtabs", role: "tablist" },
    h("button", { type: "button", role: "tab", class: "fv-tab" + (F.tab === "copy" ? " on" : ""), "aria-selected": String(F.tab === "copy"),
      onclick: () => setFillTab("copy") }, t("The copy"), nc ? h("span", { class: "fs-n warn" }, nc) : null),
    h("button", { type: "button", role: "tab", class: "fv-tab" + (F.tab === "push" ? " on" : ""), "aria-selected": String(F.tab === "push"),
      id: "fill-tab-push", onclick: () => setFillTab("push") }, t("To push"), n ? h("span", { class: "fs-n" }, n) : null)));
}

async function setFillTab(tab) {
  if (tab === F.tab) return;
  await flushTyping();
  F.tab = tab;
  store("fill-tab", tab);
  paintFill();
}

// ================================================================ the copy (editable)
function copyArea(v) {
  const site = fillSite();
  const local = v.local || { basics: {}, items: [], skills: [] };
  const base = v.base || { basics: {}, items: [], skills: [] };
  const busy = new Set(v.applying || []);
  const baseById = Object.fromEntries((base.items || []).map((i) => [i.id, i]));
  const ref = v.reference || { items: {}, basics: {}, missing: [], skills: [] };
  const sections = [...(v.site.basics.length ? ["basics"] : []), ...v.site.sections];
  const nc = Object.keys(v.conflicts).length;
  const out = [compareCard(v)];
  if (nc) out.push(h("div", { class: "fill-banner warn" }, icon("alert"), h("span", { class: "nb-text" },
    t("{n} field was changed both here and on {site}: pick which to keep below. Pushing waits until then.", { n: nc, site: site.name },
      "{n} fields were changed both here and on {site}: pick which to keep below. Pushing waits until then."))));
  for (const sec of sections) {
    if (sec === "basics") {
      out.push(h("div", { class: "fill-group" }, h("div", { class: "fg-title" }, t(SECTION_NAMES.basics)),
        h("div", { class: "fi copy" }, h("div", { class: "fi-body" },
          v.site.basics.map((name) => fieldEdit(v, { id: "basics", section: "basics", fields: local.basics },
            { fields: base.basics }, name, ref.basics[name], busy.has("basics." + name)))))));
      continue;
    }
    if (sec === "skills") { out.push(skillsCard(v, local, base, ref, busy.has("skills"))); continue; }
    const items = local.items.filter((i) => i.section === sec);
    const removed = base.items.filter((b) => b.section === sec && !local.items.some((l) => l.id === b.id));
    const missing = ref.missing.filter((m) => m.section === sec);
    out.push(h("div", { class: "fill-group" },
      h("div", { class: "fg-title" }, t(SECTION_NAMES[sec] || sec)),
      items.map((it) => copyItem(v, it, baseById[it.id], ref.items[it.id], busy.has("item." + it.id))),
      removed.map((b) => h("div", { class: "fi removed" }, h("div", { class: "fi-row" },
        h("span", { class: "fi-op op-remove" }, t("Removed")), h("span", { class: "fi-label" }, h("span", { class: "fi-name" }, itemTitle(b.section, b.fields))),
        h("span", { class: "muted fi-hint" }, t("goes from {site} when you push", { site: site.name })),
        h("button", { class: "btn sm ghost", onclick: () => editCopy({ op: "revert", change: "item." + b.id }) }, icon("rewind"), t("Keep it"))))),
      missing.map((m) => h("div", { class: "fi suggest" }, h("div", { class: "fi-row" },
        h("span", { class: "fi-op op-ref" }, t("In your resume")), h("span", { class: "fi-label" }, h("span", { class: "fi-name" }, m.label)),
        h("span", { class: "muted fi-hint" }, t("not in this copy")),
        h("button", { class: "btn sm", onclick: () => addItem(sec, m.fields) }, icon("plus"), t("Add to the copy"))))),
      ADD_ONE[sec] && h("button", { type: "button", class: "btn sm ghost fill-add", onclick: () => addItem(sec, {}) }, icon("plus"), t(ADD_ONE[sec]))));
  }
  return h("div", { class: "fill-copy" }, out);
}

async function addItem(section, fields) {
  const r = await editCopy({ op: "add", section, fields });
  if (r && r.item) { F.open[r.item] = true; paintFill(); }
}

// which resume subset and language the copy is compared with (its suggestions)
function compareCard(v) {
  const vs = subsets();
  const sel = uiSelect({ id: "fill-subset", label: t("Resume subset"), menuWidth: 300, value: v.subset_id,
    options: vs.map((x) => ({ value: x.id, label: subsetName(x), hint: x.id === ALL ? t("every entry") : null })),
    onChange: (id) => chooseTarget({ subset_id: id }) });
  const ls = langs();
  return h("div", { class: "fill-compare" },
    h("span", { class: "muted" }, t("Suggestions from your resume:")), sel.el,
    ls.length > 1 && uiSeg(ls.map((l) => ({ value: l, label: langShort(l), tip: langName(l) })), v.lang, (l) => chooseTarget({ lang: l }),
      { label: t("Resume language") }));
}

function copyItem(v, it, b, ref, busy) {
  const site = fillSite();
  const conflicts = Object.keys(v.conflicts).filter((k) => k.startsWith(it.id + "."));
  const changed = !b || (v.site.fields[it.section] || []).some((f) => String((b.fields || {})[f] || "") !== String(it.fields[f] || ""));
  const open = F.open[it.id] ?? (!b || conflicts.length > 0);
  const tag = !b ? h("span", { class: "fi-op op-add" }, t("New")) : conflicts.length ? h("span", { class: "fi-op op-conflict" }, t("Conflict"))
    : changed ? h("span", { class: "fi-op op-update" }, t("Edited")) : null;
  const sum = [it.fields.start, it.fields.end].filter(Boolean).map((s) => (s === "present" ? t("present") : s)).join(" – ") || it.fields.date || "";
  return h("div", { class: "fi copy" + (open ? " open" : ""), "data-id": it.id },
    h("div", { class: "fi-row" },
      tag,
      h("button", { type: "button", class: "fi-label", "aria-expanded": String(open), onclick: () => { F.open[it.id] = !open; paintFill(); } },
        h("span", { class: "fi-name" }, itemTitle(it.section, it.fields)), sum && h("span", { class: "fi-was" }, sum),
        ref && Object.keys(ref.fields).length > 0 && !open && h("span", { class: "fi-ref-dot", title: t("Your resume words this differently") })),
      busy && h("span", { class: "fi-state s-doing" }, h("span", { class: "spin" }), t(APPLIED.doing)),
      h("button", { type: "button", class: "btn icon sm ghost", title: t("Remove from the copy"), "aria-label": t("Remove from the copy"), disabled: busy,
        onclick: () => editCopy({ op: "remove", item: it.id }) }, icon("trash")),
      h("button", { type: "button", class: "btn icon sm ghost", title: open ? t("Hide the fields") : t("Show the fields"), onclick: () => { F.open[it.id] = !open; paintFill(); } }, icon(open ? "up" : "down"))),
    open && h("div", { class: "fi-body" }, (v.site.fields[it.section] || []).map((f) => fieldEdit(v, it, b, f, ref && ref.fields[f], busy))));
}

// one field of the copy: the text box, what the site has when it differs, a conflict, the resume's wording
function fieldEdit(v, it, b, f, refValue, busy) {
  const site = fillSite();
  const item = it.id;
  const key = `${item}|${f}`;
  const value = String((it.fields || {})[f] || "");
  const was = b ? String((b.fields || {})[f] || "") : null;
  const conflict = v.conflicts[(item === "basics" ? "basics" : item) + "." + f];
  const lim = v.site.limits[`${it.section}.${f}`];
  const shown = (s) => (DATE_FIELDS.has(f) && s === "present" ? t("present") : s);
  const count = h("span", { class: "ff-count" });
  const paintCount = (s) => {
    count.textContent = lim ? `${s.length.toLocaleString()} / ${lim.toLocaleString()}` : "";
    count.classList.toggle("over", !!lim && s.length > lim);
  };
  paintCount(value);
  const onInput = (e) => {
    paintCount(e.target.value);
    if (e.target.tagName === "TEXTAREA") autosizeFull(e.target);
    typed(item, f, e.target.value);
  };
  const box = LONG_FIELDS.has(f)
    ? h("textarea", { class: "ff-to", rows: 1, spellcheck: "false", disabled: busy, "aria-label": fieldName(it.section, f), "data-edit": key, oninput: onInput }, value)
    : h("input", { class: "ff-to ff-line", spellcheck: "false", disabled: busy, "aria-label": fieldName(it.section, f), "data-edit": key, value,
      placeholder: DATE_FIELDS.has(f) ? t("YYYY-MM, or present") : "", oninput: onInput });
  const differs = was !== null && was !== value && !conflict;
  return h("div", { class: "ff" + (conflict ? " conflict" : differs ? " changed" : "") },
    h("div", { class: "ff-name" }, fieldName(it.section, f), h("span", { class: "spacer" }),
      differs && h("button", { type: "button", class: "linkish ff-reset", disabled: busy, title: t("Go back to what {site} has", { site: site.name }),
        onclick: () => editCopy({ op: "set", item, field: f, value: was }) }, t("Undo my edit")),
      count),
    conflict && h("div", { class: "ff-conflict" },
      h("div", { class: "ffc-head" }, icon("alert"), t("Changed both here and on {site}. Which to keep?", { site: site.name })),
      h("div", { class: "ffc-pair" },
        h("div", { class: "ffc-side" }, h("div", { class: "ffc-k" }, t("Yours")), h("div", { class: "ffc-v" }, shown(conflict.local) || h("i", null, t("(empty)"))),
          h("button", { class: "btn sm", onclick: () => editCopy({ op: "settle", key: (item === "basics" ? "basics" : item) + "." + f, take: "local" }) }, t("Keep my version"))),
        h("div", { class: "ffc-side" }, h("div", { class: "ffc-k" }, t("On {site}", { site: site.name })), h("div", { class: "ffc-v" }, shown(conflict.site) || h("i", null, t("(empty)"))),
          h("button", { class: "btn sm", onclick: () => editCopy({ op: "settle", key: (item === "basics" ? "basics" : item) + "." + f, take: "site" }) }, t("Take {site}'s", { site: site.name }))))),
    box,
    differs && h("div", { class: "ff-from" }, h("span", { class: "ff-k" }, t("Now on {site}:", { site: site.name })), " ", was ? shown(was) : h("i", null, t("(empty)"))),
    refValue && !busy && h("div", { class: "ff-ref" }, h("span", { class: "ff-k" }, t("Your resume:")), " ", h("span", { class: "ffr-v" }, shown(refValue)),
      h("button", { type: "button", class: "linkish", onclick: () => editCopy({ op: "set", item, field: f, value: refValue }) }, t("Use this"))),
    lim && value.length > lim && h("div", { class: "ff-warn" }, t("{site} takes at most {n} characters here: shorten it before it goes up.", { site: site.name, n: lim.toLocaleString() })));
}

function skillsCard(v, local, base, ref, busy) {
  const site = fillSite();
  const value = (local.skills || []).join("\n");
  const ta = h("textarea", { class: "ff-to", rows: 3, spellcheck: "false", disabled: busy, "aria-label": t("Skills"), "data-edit": "skills|skills",
    oninput: (e) => { autosizeFull(e.target); typed("skills", "skills", e.target.value); } }, value);
  return h("div", { class: "fill-group" }, h("div", { class: "fg-title" }, t(SECTION_NAMES.skills)),
    h("div", { class: "fi copy" }, h("div", { class: "fi-body" },
      h("div", { class: "ff" }, h("div", { class: "ff-name" }, t("One per line"), h("span", { class: "spacer" }),
        h("span", { class: "ff-count" }, t("{n} skill", { n: (local.skills || []).length }, "{n} skills"))), ta),
      ref.skills.length > 0 && h("div", { class: "ff-ref" }, h("span", { class: "ff-k" }, t("In your resume, not here yet:")), " ",
        h("span", { class: "ffr-v" }, ref.skills.join(", ")),
        h("button", { type: "button", class: "linkish", disabled: busy, onclick: () => editCopy({ op: "add_skills", skills: ref.skills }) }, t("Add them"))))));
}

// ================================================================ the changes to push (git-style diffs)
function pushArea(v) {
  const site = fillSite();
  const p = v.pending;
  // what the last push got done (those are no longer waiting: the site has them)
  const ids = new Set(p.map((i) => i.id));
  const done = Object.entries(v.applied || {}).filter(([id, a]) => a.state === "done" && !ids.has(id));
  const doneLine = done.length > 0 && h("div", { class: "fill-banner ok" }, icon("check"), h("span", { class: "nb-text" },
    t("{n} change is on {site} now.", { n: done.length, site: site.name }, "{n} changes are on {site} now.")));
  if (!p.length) {
    return h("div", { class: "fill-plan" }, doneLine, h("div", { class: "fill-card fill-start" }, h("h2", null, t("Nothing to push")),
      h("p", { class: "muted" }, t("Your copy says what {site} says. Edit the copy, and the changes show up here.", { site: site.name }))));
  }
  const groups = [];
  for (const sec of ["basics", "experience", "projects", "education", "awards", "skills"]) {
    const list = p.filter((i) => i.section === sec);
    if (list.length) groups.push(h("div", { class: "fill-group" }, h("div", { class: "fg-title" }, t(SECTION_NAMES[sec])), list.map((i) => pushItem(v, i))));
  }
  return h("div", { class: "fill-plan" },
    h("div", { class: "fp-head" },
      h("span", null, h("b", null, t("{n} change waits to go up", { n: p.length }, "{n} changes wait to go up"))),
      h("span", { class: "spacer" }),
      p.length > 1 && h("button", { class: "btn sm ghost", onclick: () => saveTicks(Object.fromEntries(p.map((i) => [i.id, true]))) }, t("Tick all")),
      p.length > 1 && h("button", { class: "btn sm ghost", onclick: () => saveTicks(Object.fromEntries(p.map((i) => [i.id, false]))) }, t("Untick all"))),
    doneLine,
    groups);
}

function pushLabel(i) {
  if (i.section === "basics") return fieldName("basics", i.item);
  if (i.section === "skills") return t("Skills");
  return i.label;
}

function pushItem(v, i) {
  const site = fillSite();
  const busy = (v.applying || []).includes(i.id);
  const open = F.open["push:" + i.id] ?? true;
  const a = i.applied;
  const box = h("button", { type: "button", class: "check" + (i.checked ? " on" : ""), role: "checkbox", "aria-checked": String(i.checked),
    disabled: busy, "aria-label": pushLabel(i),
    onclick: () => saveTicks({ ...Object.fromEntries(v.pending.map((x) => [x.id, x.checked])), [i.id]: !i.checked }) });
  const key = "push:" + i.id;
  return h("div", { class: "fi op-" + i.op + (i.checked ? "" : " off"), "data-id": i.id },
    h("div", { class: "fi-row" },
      box,
      h("span", { class: "fi-op" }, t(OPS[i.op])),
      h("button", { type: "button", class: "fi-label", "aria-expanded": String(open), onclick: () => { F.open[key] = !open; paintFill(); } },
        h("span", { class: "fi-name" }, pushLabel(i)),
        i.op === "update" && i.site_label && i.site_label !== i.label && h("span", { class: "fi-was" }, t("on {site}: {label}", { site: site.name, label: i.site_label }))),
      a && h("span", { class: "fi-state s-" + a.state, title: a.note || null }, a.state === "doing" ? h("span", { class: "spin" }) : null, t(APPLIED[a.state] || a.state)),
      h("button", { type: "button", class: "btn sm ghost", disabled: busy, title: t("Put back what {site} has", { site: site.name }),
        onclick: () => editCopy({ op: "revert", change: i.id }) }, icon("rewind"), t("Undo")),
      h("button", { type: "button", class: "btn icon sm ghost", title: open ? t("Hide the fields") : t("Show the fields"), onclick: () => { F.open[key] = !open; paintFill(); } }, icon(open ? "up" : "down"))),
    a && a.state === "failed" && a.note && h("div", { class: "fi-note" }, icon("alert"), a.note),
    open && h("div", { class: "fi-body" }, i.fields.map((f) => {
      const shown = (s) => (DATE_FIELDS.has(f.name) && s === "present" ? t("present") : s || "");
      return h("div", { class: "ff" },
        h("div", { class: "ff-name" }, i.section === "skills" ? t("Skills") : fieldName(i.section, f.name), h("span", { class: "spacer" }),
          f.limit && h("span", { class: "ff-count" + (f.over ? " over" : "") }, `${(f.to || "").length.toLocaleString()} / ${f.limit.toLocaleString()}`)),
        diffEl(shown(f.from), shown(f.to), i.id + "|" + f.name, F.unfolded),
        f.over && h("div", { class: "ff-warn" }, t("{site} takes at most {n} characters here: shorten it before it goes up.", { site: site.name, n: f.limit.toLocaleString() })));
    })));
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
    message: t("Nothing changes on {site} itself. What the AI read there and your ticks stay saved: add the site again to get them back.", { site: s.name }),
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
  if (pick) { F.site = pick; H.tab = "platforms"; H.page = { kind: "platform", id: pick }; storeHunt(); }
  if (F.sites.length && !F.sites.some((s) => s.id === F.site)) F.site = F.sites[0].id;
  // the platform whose page was open is gone: the next one's page (none left: the table)
  if (H.page && H.page.kind === "platform" && !F.sites.some((s) => s.id === H.page.id)) {
    H.page = F.sites.length ? { kind: "platform", id: F.site } : null;
    storeHunt();
  }
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
    h("p", { class: "muted" }, t("Add the sites whose profile should say what your resume says. Resume Studio knows a few; any other works too: the AI finds its way around the first time it reads it.")),
    h("div", { class: "row wrap" },
      (F.presets || []).map((p) => h("button", { class: "btn", onclick: () => addSite({ preset: p.id }) }, icon("plus"), p.name)),
      h("button", { class: "btn primary", onclick: () => siteDialog(null) }, icon("plus"), t("Another site…"))));
}

async function addSite(body) {
  let r;
  try { r = await api("POST", "/api/autofill/sites", body); }
  catch (e) { toast(e.message, { ms: 7000 }); return false; }
  F.sites = r.sites; F.presets = r.presets;
  await flushTyping();
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
    h("label", null, t("Sites Resume Studio knows"), h("span", { class: "hint" }, t("Each comes with notes for the AI about its editor"))),
    h("div", { class: "row wrap" }, F.presets.filter((p) => !p.added).map((p) =>
      h("button", { class: "btn", onclick: async () => { if (await addSite({ preset: p.id })) close(); } }, icon("plus"), p.name))));
  root.replaceChildren(h("div", { class: "modal-back", onmousedown: (e) => { if (e.target === e.currentTarget) close(); } },
    h("div", { class: "modal site-dialog", role: "dialog", "aria-modal": "true", "aria-label": s ? t("Site settings") : t("Add a site") },
      h("h3", null, s ? t("{site} settings", { site: s.name }) : t("Add a site")),
      presetRow,
      !s && presetRow && h("div", { class: "fd-or" }, t("or any other site")),
      h("div", { class: "field" }, h("label", { for: "site-name" }, t("Name")), name),
      h("div", { class: "field" }, h("label", { for: "site-home" }, t("Address"),
        h("span", { class: "hint" }, t("The page with your profile, or the site's home page: the AI finds the profile from there"))), home),
      ls.length > 1 && h("div", { class: "field" }, h("label", null, t("Resume language"), h("span", { class: "hint" }, t("What the profile usually gets; you can switch it on the page too"))),
        uiSeg(ls.map((l) => ({ value: l, label: langName(l) })), lang || ls[0], (l) => { lang = l; }, { label: t("Resume language") })),
      h("div", { class: "field" }, h("label", { for: "site-notes" }, t("Notes for the AI"),
        h("span", { class: "hint" }, t("Optional: where things are on this site, what to watch out for. The AI reads them every time"))), notes),
      s && !s.preset && h("p", { class: "muted" }, s.learned
        ? t("The AI learned what this site has when it last read it: {list}.", { list: s.sections.map((x) => t(SECTION_NAMES[x] || x)).join(", ") })
        : t("The AI works out which sections and limits this site has the first time it reads it.")),
      h("div", { class: "acts" },
        s && h("button", { class: "btn danger-soft", onclick: () => { close(); removeSite(s); } }, icon("trash"), t("Remove from the list")),
        h("span", { class: "spacer" }),
        h("button", { class: "btn", onclick: close }, t("Cancel")),
        h("button", { class: "btn primary", onclick: done }, s ? t("Done") : t("Add"))))));
  setTimeout(() => name.focus(), 0);
}

async function switchFillSite(id) {
  if (id === F.site) return;
  await flushTyping();
  F.site = id;
  store("fill-site", id);
  F.open = {};
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
      h("button", { class: "btn sm primary", onclick: openSite }, icon("external"), t("Show the sign-in page")),
      h("button", { class: "btn sm", title: t("For a site that refuses to sign in (a code that never arrives): the same browser, not controlled by Resume Studio"),
        onclick: () => signInMode(/^https?:/.test(b.url || "") ? b.url : site.home) }, t("Sign in without remote control"))));
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
        : b.state === "signin" ? t("open for you to sign in, without remote control")
        : t("a window of its own; your sign-ins stay in it, apart from your everyday browser"))),
    h("span", { class: "spacer" }),
    running && h("button", { class: "btn sm ghost", onclick: showBrowser, title: t("Bring the browser window to the front") }, t("Show")),
    h("button", { class: "btn sm", disabled: !b.found, onclick: openSite }, icon("external"), t("Open {site}", { site: site.name })),
    h("button", { class: "btn sm icon ghost", id: "fill-site-settings", title: t("{site} settings", { site: site.name }),
      "aria-label": t("{site} settings", { site: site.name }), onclick: (e) => siteMenu(e.currentTarget, site) }, icon("settings"))));
  box.replaceChildren(...out);
}


function paintFillFoot() {
  const el = $("#fill-foot");
  if (!el) return;
  const v = fillView();
  if (!v || !v.synced) { el.replaceChildren(); el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const site = fillSite();
  const ticked = v.pending.filter((i) => i.checked);
  const over = ticked.some((i) => i.fields.some((f) => f.over));
  const nc = Object.keys(v.conflicts).length;
  const applying = (v.applying || []).length > 0;
  el.replaceChildren(...[
    h("span", { class: "ff-sum" }, applying ? t("The AI is making the changes in the browser; follow along in the chat.")
      : nc ? t("Settle the {n} conflict first.", { n: nc }, "Settle the {n} conflicts first.")
      : over ? t("Some text is over {site}'s limit.", { site: site.name })
      : !v.pending.length ? t("Your copy says what {site} says.", { site: site.name })
      : F.tab === "push" ? (ticked.length ? t("{n} change ticked", { n: ticked.length }, "{n} changes ticked") : t("Nothing ticked"))
      : t("{n} change waits to go up", { n: v.pending.length }, "{n} changes wait to go up")),
    h("span", { class: "spacer" }),
    applying && h("button", { class: "btn", onclick: showBrowser }, t("Show the browser")),
    F.tab !== "push" && v.pending.length > 0 && h("button", { class: "btn", id: "fill-review", onclick: () => setFillTab("push") },
      t("Review and push ({n})", { n: v.pending.length })),
    F.tab === "push" && h("button", { class: "btn primary", id: "fill-apply", disabled: applying || over || nc > 0 || !ticked.length || !ui.chat.available, onclick: applyPlan },
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
      /^https?:/.test(b.url || "") && { label: t("Sign in here without remote control"), icon: "user", onClick: () => signInMode(b.url) },
      { label: t("Close the browser"), icon: "x", onClick: async () => { F.browser = await api("POST", "/api/browser/stop", {}); paintFillBrowser(); renderWsTools(); } },
    ], { align: "end", onClose: () => setTimeout(renderWsTools, 0) }) }, icon("more"))].filter(Boolean));
}

function fillWelcome() {
  const site = fillSite();
  const onPlatform = H.tab === "platforms" && H.page && H.page.kind === "platform";
  const company = H.page && H.page.kind === "company" && H.view && H.view.company ? H.view.company.name : "";
  const suggest = onPlatform ? [
    t("Does my {site} profile sell me well for the roles I want?", { site: site.name }),
    t("Add my newest project to {site}", { site: site.name }),
  ] : [
    t("What's next this week, and what's overdue?"),
    company ? t("Tell me about {company}: how it is doing and its prospects", { company }) : t("Which of my applications have gone quiet?"),
    t("I passed the first interview at {company}; the second is next Tuesday", { company: company || "Xinghe" }),
  ];
  return h("div", { class: "chat-welcome" },
    h("div", { class: "t" }, onPlatform ? t("{agent}, with Resume Studio's browser", { agent: agentOf().short }) : t("{agent}, with your applications", { agent: agentOf().short })),
    h("div", null, !ui.chat.available ? missingAgentText()
      : onPlatform ? t("In these chats {agent} can look at and work in the browser on the right: reading a profile, changing one thing, checking a page. It changes a site only when you ask.", { agent: agentOf().short })
      : t("Tell {agent} what happened and it updates the tables; ask about a company and it finds out, with sources. It can also work in Resume Studio's browser.", { agent: agentOf().short })),
    ui.chat.available && h("div", { class: "suggest" }, suggest.map((s) => h("button", { onclick: () => sendChat(s) }, s))));
}
