// Job search: a job-posting tool (today career-ops) behind the window.
// The tool evaluates postings and keeps the tracker; Resume Studio shows them, runs the tool's work as
// Claude Code chats in the tool's folder, and makes the tailored resume itself (a job in resume.yaml).
// Everything here speaks the generic shapes in jobs/base.py, never career-ops' files directly.
// Loaded after chat.js and before app.js: only definitions; app.js's state is read when they run.
"use strict";

const J = {
  data: null,            // GET /api/jobs: {status, applications, states, actions}
  detail: null,          // GET /api/jobs/app/<id> for the posting open in the middle pane
  progress: {},          // task -> {step: state, state, error} while installing / updating
  update: null,          // the last update check
  draft: "",             // the posting being pasted (kept while its box is closed)
  filter: "all",
  loading: null,
  fit: {},               // application id -> GET /api/jobs/app/<id>/fit (the subsets ranked for it)
  fitLoading: {},
  fitAll: {},            // application id -> show every subset, not just the best few
};

// the tool's own state names, in the interface language (unknown ones show as the tool writes them)
const STATE_NAMES = {
  evaluated: "Evaluated", applied: "Applied", responded: "Responded", assessment: "Assessment", interview: "Interview",
  offer: "Offer", hired: "Hired", rejected: "Rejected", discarded: "Discarded", skip: "Not applying",
};
const DECISIONS = { apply: "Worth applying", consider: "Worth considering", "research first": "Look into it first", skip: "Better to skip" };
const IMPORTANCE = { critical: "Critical", high: "High", meaningful: "Meaningful", preferred: "Preferred", low_signal: "Low signal" };
// career-ops' enum values (Machine Summary), shown in the interface language
const VALUE_NAMES = {
  "high confidence": "Looks genuine", "proceed with caution": "Some doubts", suspicious: "Suspicious",
  sponsors: "Visa sponsorship offered", not_needed: "No visa needed", unstated: "Visa sponsorship not stated", no_sponsorship: "No visa sponsorship",
  low: "Low", medium: "Medium", high: "High",
};
const valueName = (v) => (VALUE_NAMES[String(v || "").toLowerCase()] ? t(VALUE_NAMES[String(v).toLowerCase()]) : v);
const CHECK_NAMES = {
  node: "Node.js 22 or newer", deps: "career-ops' dependencies", tracker: "Tracker table", states: "Status list",
  report: "Report summary block", status: "Status writer", updater: "Updater", skill: "Skill entrypoints",
  web: "Its web interface", modes: "Mode catalog",
};
const STEP_NAMES = { download: "Download career-ops", deps: "Install its dependencies", repair: "Finish setting up", update: "Run career-ops' updater" };

const stateName = (id, label) => (STATE_NAMES[id] ? t(STATE_NAMES[id]) : label || id || "—");
const decisionName = (d) => (DECISIONS[String(d || "").toLowerCase()] ? t(DECISIONS[String(d).toLowerCase()]) : d);
const jobsTool = () => (J.data && J.data.status && J.data.status.tool) || { name: "career-ops" };
const jobsReady = () => !!(J.data && J.data.status && J.data.status.installed);

function scoreTone(score, max = 5) {
  if (score == null) return "none";
  const s = (score / (max || 5)) * 5;
  return s >= 4 ? "good" : s >= 3 ? "mid" : "low";
}
function scorePill(a, big = false) {
  const tone = scoreTone(a.score, a.score_max);
  return h("span", { class: "score " + tone + (big ? " big" : ""), title: a.score != null ? t("Fit score {score} out of {max}", { score: a.score, max: a.score_max || 5 }) : t("Not scored") },
    a.score != null ? (Math.round(a.score * 10) / 10).toFixed(1) : "–");
}
function stateChip(a) {
  const id = a.status || "";
  return h("span", { class: "state-chip s-" + (id || "unknown") }, stateName(id, a.status_label));
}

// ================================================================ data
async function loadJobs({ quiet = false } = {}) {
  if (J.loading) {   // asked again while a read is out: that read may predate the change, so read once more after it
    J.again = true;
    return J.loading;
  }
  J.loading = (async () => {
    try {
      do {
        J.again = false;
        J.data = await api("GET", "/api/jobs");
      } while (J.again);
    } catch (e) {
      if (!quiet) toast(e.message);
    } finally {
      J.loading = null;
    }
    renderJobs();
    if (ui.sel.kind === "posting") refreshPosting();
    if (ui.sel.kind === "subset") buildEditorKeepDraft();   // the posting link in Job settings
    paintJobspace();
    renderWsTools();
    if (W.ws === "jobs" && !visibleChats().some((c) => c.id === ui.chat.id)) chatForWorkspace();
    // the tool's interface starts once it is wanted: in Job search, or ahead of time once it was used
    if (!W.autoStarted && J.data && (W.ws === "jobs" || store("jobs-web-used") === "1")) { W.autoStarted = true; startWeb(); }
  })();
  return J.loading;
}

async function refreshPosting() {
  const id = ui.sel.id;
  try {
    const d = await api("GET", "/api/jobs/app/" + encodeURIComponent(id));
    if (ui.sel.kind !== "posting" || ui.sel.id !== id) return;
    J.detail = d;
  } catch (e) {
    if (ui.sel.kind !== "posting" || ui.sel.id !== id) return;
    J.detail = { id, error: e.message };
  }
  paintEditor({ keepScroll: true });
}

async function openPosting(id) {
  if (!S || (ui.sel.kind === "posting" && ui.sel.id === id)) return;   // S: the resume is still loading
  J.detail = (J.data && J.data.applications.find((a) => a.id === id)) || { id };
  J.detail = { ...J.detail, loading: true };
  if (W.ws !== "resume") setWorkspace("resume");   // its page sits next to the resume preview
  await select("posting", id);
  refreshPosting();
}

// a chat the server started (in the tool's folder, or a resume chat): show it like any other
async function jobChat(url, body = {}) {
  if (!ui.chat.available) { toast(t("The claude command was not found")); return null; }
  await saveDraft();
  let r;
  try { r = await api("POST", url, { ...chatSettings(), ...body }); }
  catch (e) { toast(e.message); return null; }
  if (r.doc) applyState(r);   // tailor made a job
  ui.chat.list = [r.chat, ...ui.chat.list.filter((c) => c.id !== r.chat.id)];
  await openChat(r.chat.id);
  showChat();
  return r;
}

// ================================================================ around the tool's interface, in Job search
// The Resume workspace had an Applications tab with the tool's list, the paste box and the warnings; since
// the window has one chat and Job search shows the tool's own interface, they live here (docs/LAYOUT.md).
function renderJobs() {
  paintJobspace();
}

function connectCard(st) {
  const p = J.progress.install || {};
  const running = p.state === "running";
  const steps = ["download", "deps", "repair"];
  const missing = [!st.git && "git", !st.node && "Node.js"].filter(Boolean);
  return h("div", { class: "connect" },
    h("div", { class: "connect-ic" }, icon("bolt")),
    h("div", { class: "connect-t" }, t("Evaluate job postings with {name}", { name: st.tool.name })),
    h("p", null, t("{name} reads a posting, scores how well it fits your resume, says what to stress and what's missing, and keeps a tracker of your applications. Resume Studio runs it for you: no terminal.", { name: st.tool.name })),
    h("p", { class: "connect-small" }, t("Open source ({license}) by {author}. It's downloaded into Resume Studio's own folder and updated only when you say so.", { license: st.tool.license, author: st.tool.author })),
    running || p.state === "error" ? h("div", { class: "steps" }, steps.map((s) => {
      const state = p[s] || "todo";
      return h("div", { class: "step " + state }, h("span", { class: "st-ic" }, state === "done" ? icon("check") : null), t(STEP_NAMES[s]));
    })) : null,
    p.state === "error" && h("pre", { class: "connect-err" }, p.error || ""),
    missing.length > 0 && !running && h("div", { class: "note-bar warn" }, icon("alert"), h("span", { class: "nb-text" },
      t("{tools} not found on this computer. {name} needs git and Node.js 22 or newer.", { tools: missing.join(", "), name: st.tool.name }))),
    h("div", { class: "connect-acts" },
      h("button", { class: "btn primary", disabled: running || missing.length > 0, onclick: installTool }, running ? t("Installing…") : t("Install {name}", { name: st.tool.name })),
      h("button", { class: "btn", disabled: running, onclick: useToolFolder }, icon("folderOpen"), t("I already have it…"))),
    h("button", { class: "linkish connect-link", onclick: () => api("POST", "/api/open", { url: st.tool.homepage }) }, icon("external"), t("About {name}", { name: st.tool.name })));
}

function jobsBanners(st) {
  const out = [];
  const bad = (st.checks || []).filter((c) => !c.ok);
  if (bad.length) {
    out.push(h("div", { class: "jobs-banner warn" }, icon("alert"),
      h("span", { class: "nb-text" }, t("{name} changed something Resume Studio reads: {list}. Everything else keeps working.", { name: st.tool.name, list: bad.map((c) => t(CHECK_NAMES[c.id] || c.id)).join(", ") })),
      h("button", { class: "btn sm", onclick: () => openSettings("jobs") }, t("Details"))));
  }
  const cv = st.cv || {};
  if (cv.sync && (cv.conflicts || []).length) {
    const file = cv.conflicts[0];
    out.push(h("div", { class: "jobs-banner warn" }, icon("alert"),
      h("span", { class: "nb-text" }, t("{file} in {name} was changed there, so Resume Studio stopped updating it from your library.", { file, name: st.tool.name })),
      h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => jobChat("/api/jobs/cv/resolve", { file }) }, icon("sparkle"), t("Bring the changes in")),
      h("button", { class: "btn sm ghost", onclick: () => overwriteCv(file) }, t("Use my library"))));
  }
  // its targeting profile (missing or stale) has its button in the top bar
  return out;
}

// career-ops' targeting profile, filled by Claude from the resume data (profile notes, subsets)
function setupProfile(update) {
  return jobChat("/api/jobs/setup", { update });
}

// the resume changed: the subset ranking and the profile's freshness may have too
let jobsResumeTimer = 0;
function jobsAfterResumeChange() {
  J.fit = {};
  clearTimeout(jobsResumeTimer);
  jobsResumeTimer = setTimeout(() => { if (jobsReady()) loadJobs({ quiet: true }); }, 1500);
}

// a posting to evaluate: its link or its text, pasted into a box under the button
function openEvaluate(anchor) {
  const ta = h("textarea", { id: "posting-input", rows: 3, placeholder: t("Paste a job posting: its link, or the whole text"), spellcheck: "false",
    oninput: (e) => { J.draft = e.target.value; autosize(e.target); go.disabled = !J.draft.trim(); },
    onkeydown: (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); evaluatePosting(); } } }, J.draft);
  const go = h("button", { class: "btn primary sm", disabled: !J.draft.trim() || !ui.chat.available, onclick: evaluatePosting }, icon("sparkle"), t("Evaluate"));
  const panel = h("div", { class: "eval-box eval-float", role: "dialog", "aria-label": t("Evaluate a posting") }, ta,
    h("div", { class: "eval-bar" },
      h("span", { class: "eval-hint" }, t("Claude runs {name}'s evaluation in a new chat; it takes a few minutes.", { name: jobsTool().name })),
      go));
  requestAnimationFrame(() => autosize(ta));
  openFloat(anchor, panel, { minWidth: 460, focus: ta, onClose: () => setTimeout(renderWsTools, 0) });
}

async function evaluatePosting() {
  const posting = J.draft.trim();
  if (!posting) return;
  closeFloat();
  const r = await jobChat("/api/jobs/evaluate", { posting });
  if (r) J.draft = "";
}

// the tool's evaluations as a list, for a version of it without an interface of its own
function applicationsList() {
  const apps = J.data.applications || [];
  const open = (a) => !["rejected", "discarded", "skip"].includes(a.status);
  const shown = J.filter === "open" ? apps.filter(open) : apps;
  const head = h("div", { class: "lib-head apps-head" },
    h("span", null, h("span", { class: "t" }, t("Applications")), apps.length ? h("span", { class: "n" }, apps.length) : null),
    apps.length > 0 && uiSeg([{ value: "all", label: t("All") }, { value: "open", label: t("Active") }], J.filter, (v) => { J.filter = v; paintJobspace(); }, { cls: "sm", label: t("Show") }));
  if (!apps.length) return h("div", { class: "lib-section" }, head, h("div", { class: "empty" }, h("div", { class: "t" }, t("No applications yet"))));
  return h("div", { class: "lib-section", id: "jobs-list" }, head,
    shown.map(appRow),
    !shown.length && h("div", { class: "muted", style: "padding:8px 10px;font-size:12.5px" }, t("Nothing active right now")));
}

// what went out to an application: the subset and version pinned when it was sent
function sentChip(sent) {
  if (!sent) return null;
  const v = subsets().find((x) => x.id === sent.subset);
  return h("span", { class: "chip sent-chip", title: t("Sent on {date} with “{name}” v{n}", { date: (sent.at || "").slice(0, 10), name: subsetName(v) || sent.subset, n: sent.n }) },
    `${subsetName(v) || sent.subset} · v${sent.n}`);
}

function appRow(a, { inGroup = false } = {}) {
  const on = ui.sel.kind === "posting" && ui.sel.id === a.id;
  return h("div", { class: "app-row" + (on ? " on" : "") + (inGroup ? " in-group" : ""), "data-id": a.id, role: "button", tabindex: "0",
    onclick: () => openPosting(a.id), onkeydown: (e) => { if (e.key === "Enter") openPosting(a.id); } },
    inGroup ? null : scorePill(a),
    h("span", { class: "e-main" },
      h("span", { class: "e-title" }, inGroup ? a.role || "" : a.company || t("(no company)"),
        !inGroup && !a.sent && (a.subsets || []).length > 0 && h("span", { class: "has-resume", title: t("Sending: {jobs}", { jobs: a.subsets.map((v) => v.label).join(", ") }) }, icon("file"))),
      inGroup ? null : h("span", { class: "e-meta" }, a.role || "", a.sent && sentChip(a.sent))),
    h("span", { class: "app-side" }, stateChip(a), !inGroup && a.date && h("span", { class: "app-date" }, a.date.slice(5))));
}

// a version that went out: its page in Resume, shown as it was
async function showSent(subsetId, n) {
  if (!subsets().some((x) => x.id === subsetId)) { toast(t("That subset is gone; its versions are kept in .studio/subsets")); return; }
  if (W.ws !== "resume") setWorkspace("resume");
  if (ui.subsetId !== subsetId) await switchSubset(subsetId);
  await select("versions", subsetId);
  viewVersion(subsetId, n);
}

function openInBrowser(url) {
  return api("POST", "/api/open", { url }).catch((e) => toast(e.message));
}

// an ordinary posting: mark it sent with its linked subset (kept as a version and pinned on it)
async function sendPosting(a) {
  const linked = (a.subsets || [])[0];
  if (!linked) return;
  const st = versionStatus(linked.id) || {};
  const msg = st.same_as ? t("“{name}” goes out as v{n}, as it is now.", { name: linked.label, n: st.same_as })
    : t("“{name}” is kept as v{n} first, so you'll always know what this posting got.", { name: linked.label, n: (st.latest || 0) + 1 });
  if (!(await modal({ title: t("Mark this application as sent?"), message: msg, ok: t("Mark as sent") }))) return;
  try {
    const r = await api("POST", `/api/jobs/app/${encodeURIComponent(a.id)}/sent`, { subset_id: linked.id });
    applyState(r);
    J.detail = { ...J.detail, ...r.application };
    toast(t("Sent with “{name}” v{n}", { name: linked.label, n: r.n }));
    loadJobs({ quiet: true });
    paintEditor({ keepScroll: true });
  } catch (e) { toast(e.message, { ms: 8000 }); }
}

function jobsMenu(anchor) {
  const st = J.data.status;
  openMenu(anchor, [
    { label: t("Find new postings"), hint: t("{name} scan", { name: st.tool.name }), icon: "zoomIn", disabled: !ui.chat.available, onClick: () => jobChat("/api/jobs/scan") },
    { label: t("Chat in {name}", { name: st.tool.name }), icon: "chat", disabled: !ui.chat.available, onClick: () => toolChat("") },
    { label: t("Open Job search"), hint: "Ctrl+2", icon: "briefcase", onClick: () => setWorkspace("jobs") },
    "sep",
    { label: t("Open the {name} folder", { name: st.tool.name }), icon: "folderOpen", onClick: () => api("POST", "/api/jobs/reveal", {}) },
    { label: t("{name} settings", { name: st.tool.name }), icon: "settings", onClick: () => openSettings("jobs") },
  ], { align: "end" });
}

// an empty chat in the tool's folder, the composer filled with `text` for the user to finish
async function toolChat(text, title = "") {
  if (!ui.chat.available) { toast(t("The claude command was not found")); return; }
  try {
    const r = await api("POST", "/api/jobs/chat", { ...chatSettings(), title: title || jobsTool().name });
    ui.chat.list = [r.chat, ...ui.chat.list];
    await openChat(r.chat.id);
    startChatWith(text);
  } catch (e) { toast(e.message); }
}

async function overwriteCv(file) {
  if (!(await modal({ title: t("Replace {file} with your library?", { file }), message: t("The changes made to it in {name} are lost; Resume Studio writes it from resume.yaml again and keeps it updated.", { name: jobsTool().name }), ok: t("Replace"), danger: true }))) return;
  try { await api("POST", "/api/jobs/cv/sync", { force: true }); loadJobs(); toast(t("{file} follows your library again", { file })); }
  catch (e) { toast(e.message); }
}

async function installTool() {
  J.progress.install = { state: "running" };
  renderJobs();
  try { await api("POST", "/api/jobs/install", {}); }
  catch (e) { J.progress.install = { state: "error", error: e.message }; renderJobs(); }
}

async function useToolFolder() {
  try {
    const r = await api("POST", "/api/jobs/folder", {});
    J.data = { ...(J.data || {}), status: r.status };
    await loadJobs();
  } catch (e) { toast(e.message); }
}

function onJobsProgress(ev) {
  const cur = J.progress[ev.task] || {};
  if (ev.step) cur[ev.step] = ev.state;
  if (!ev.step) { cur.state = ev.state; cur.error = ev.error; cur.result = ev.result; }
  else cur.state = cur.state === "error" ? "error" : "running";
  J.progress[ev.task] = cur;
  if (!ev.step && ev.state === "done") {
    if (ev.task === "install") toast(t("{name} is ready", { name: jobsTool().name }));
    if (ev.task === "update" || ev.task === "rollback") { J.update = null; toast(ev.task === "update" ? t("{name} updated", { name: jobsTool().name }) : t("Went back to the previous version")); }
    if (ev.task === "repair") toast(t("{name} is ready", { name: jobsTool().name }));
  }
  if (!ev.step && ev.state === "error" && ev.task !== "install") toast(ev.error || t("Something went wrong"), { ms: 8000 });
  renderJobs();
  paintJobsSettings();
}

// ================================================================ middle: one posting
function postingView() {
  const a = J.detail || { id: ui.sel.id };
  if (a.error) return [h("div", { class: "banner warn" }, icon("alert"), a.error)];
  const s = a.summary || {};
  const states = (J.data && J.data.states) || [];
  const status = uiSelect({ id: "posting-status", cls: "state-select", label: t("Status"), menuWidth: 220, value: a.status || null,
    placeholder: a.status_label || t("Status"),
    options: states.map((x) => ({ value: x.id, label: stateName(x.id, x.label) })),
    onChange: (v) => setPostingStatus(a.id, v) });
  const facts = [
    a.date && [t("Evaluated on"), a.date], s.archetype && [t("Role type"), s.archetype], s.location && [t("Where"), s.location],
    s.comp && [t("Advertised pay"), s.comp], s.work_auth && [t("Work rights"), valueName(s.work_auth)],
    s.legitimacy && [t("The posting"), valueName(s.legitimacy)], s.risk && [t("Risk"), valueName(s.risk)],
  ].filter(Boolean);
  // career-ops opens its verdict with the call in bold ("**Apply.** …"); the heading already says it
  const verdict = s.decision ? String(s.verdict || "").replace(/^\s*\*\*[^*\n]{1,40}\*\*\s*/, "") : s.verdict;
  const out = [
    h("div", { class: "ed-head posting-head" },
      scorePill(a, true),
      h("div", { class: "ph-main" }, h("h1", null, a.company || a.title || "…"), h("div", { class: "ph-role" }, a.role || "")),
      h("span", { class: "spacer" }),
      status.el,
      h("button", { class: "btn icon ghost", title: t("More"), onclick: (e) => postingMenu(e.currentTarget, a) }, icon("more"))),
  ];
  if (a.loading) { out.push(h("p", { class: "muted" }, t("Loading…"))); return out; }
  if (a.report_missing) out.push(h("div", { class: "banner warn" }, icon("alert"), t("The tracker points at a report that isn't there: {path}", { path: a.report })));
  if (s.verdict || s.decision || s.next_action) {
    const tone = { apply: "good", consider: "mid", "research first": "mid", skip: "low" }[String(s.decision || "").toLowerCase()] || "none";
    out.push(h("section", { class: "verdict " + tone },
      s.decision && h("div", { class: "v-decision" }, decisionName(s.decision)),
      verdict && h("div", { class: "md v-text", html: md(verdict) }),
      s.next_action && h("div", { class: "v-next" }, icon("chevRight"), h("span", null, s.next_action))));
  }
  if (facts.length) out.push(h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("span", { class: "fk" }, k), h("span", { class: "fv" }, v)))));
  out.push(resumeCard(a));
  if ((s.strengths || []).length || (s.hard_stops || []).length || (s.soft_gaps || []).length) {
    out.push(card(t("How you fit"), t("from {name}'s evaluation", { name: jobsTool().name }), h("div", { class: "fit" },
      (s.strengths || []).length > 0 && h("div", { class: "fit-col" }, h("div", { class: "fit-h good" }, t("Strengths")), h("ul", null, s.strengths.map((x) => h("li", { html: mdInline(x) })))),
      ((s.hard_stops || []).length || (s.soft_gaps || []).length) > 0 && h("div", { class: "fit-col" }, h("div", { class: "fit-h low" }, t("Gaps")),
        h("ul", null, (s.hard_stops || []).map((x) => h("li", { class: "hard", html: mdInline(x) })), (s.soft_gaps || []).map((x) => h("li", { html: mdInline(x) })))))));
  }
  if ((s.requirements || []).length) out.push(requirementsCard(s.requirements));
  out.push(actionsCard(a));
  const sections = a.sections || [];
  if (sections.length || a.posting || (a.raw && !a.structured)) {
    out.push(card(t("Full report"), a.report || "", [
      a.raw && !a.structured && !sections.length ? h("div", { class: "md report-md", html: md(a.raw) }) : null,
      ...sections.map((x) => h("details", { class: "rep-sec" }, h("summary", null, x.title), h("div", { class: "md report-md", html: md(x.markdown) }))),
      a.posting && h("details", { class: "rep-sec" }, h("summary", null, t("The posting (archived)")), h("div", { class: "md report-md posting-text", html: md(a.posting) })),
    ], { cls: "report" }));
  }
  return out;
}

// ---- which resume goes out: one of the user's subsets (a resume per kind of role, not per company) ----
async function loadFit(id, { force = false } = {}) {
  if (!force && (J.fit[id] || J.fitLoading[id])) return J.fit[id];
  J.fitLoading[id] = true;
  try { J.fit[id] = await api("GET", `/api/jobs/app/${encodeURIComponent(id)}/fit`); }
  catch (e) { J.fit[id] = { error: e.message, subsets: [], linked: [] }; }
  finally { delete J.fitLoading[id]; }
  if (ui.sel.kind === "posting" && ui.sel.id === id) paintEditor({ keepScroll: true });
  return J.fit[id];
}

// the subset list, ranked; `onDone` runs after a pick (closes the picker)
function subsetChoices(a, F, { onDone = null, all = false } = {}) {
  const linkedId = (((F && F.linked) || a.subsets || [])[0] || {}).id || null;
  if (!F) return [h("p", { class: "muted card-p" }, t("Loading…"))];
  if (F.error) return [h("div", { class: "banner warn" }, icon("alert"), F.error)];
  const vs = F.subsets || [];
  if (!vs.length) return [h("p", { class: "muted card-p" }, t("You have no resume subsets yet: make one for this kind of role."))];
  const best = vs[0].score;
  const shown = all || J.fitAll[a.id] ? vs : vs.slice(0, 4);
  const rows = shown.map((v, i) => {
    const on = v.id === linkedId;
    const tip = [v.covered.length && t("Covers: {list}", { list: v.covered.join("; ") }), v.missing.length && t("Missing: {list}", { list: v.missing.join("; ") })].filter(Boolean).join("\n");
    return h("div", { class: "fit-row" + (on ? " on" : ""), "data-subset": v.id },
      h("span", { class: "fit-main" },
        h("span", { class: "fit-name" }, v.label, on && h("span", { class: "chip" }, t("Sending this"))),
        v.headline && h("span", { class: "fit-head" }, v.headline)),
      h("span", { class: "fit-score", title: tip || null },
        v.score == null ? h("span", { class: "muted" }, "–") : [
          h("span", { class: "meter" }, h("span", { class: "meter-fill " + (v.score >= 70 ? "good" : v.score >= 40 ? "mid" : "low"), style: `width:${Math.max(4, v.score)}%` })),
          h("span", { class: "fit-pct" }, v.score + "%")],
        F.requirements > 0 && h("span", { class: "fit-req" }, t("{n} of {total} requirements", { n: v.covered.length, total: F.requirements }))),
      h("span", { class: "fit-acts" },
        on ? h("button", { class: "btn sm ghost", onclick: () => useSubset(a, null, onDone) }, t("Unlink"))
           : h("button", { class: "btn sm" + (i === 0 && best != null && !linkedId ? " primary" : ""), onclick: () => useSubset(a, v.id, onDone) }, t("Use this")),
        h("button", { class: "btn icon sm ghost", title: t("More"), onclick: (e) => subsetMenu(e.currentTarget, a, v, onDone) }, icon("more"))));
  });
  return [h("div", { class: "fit-list" }, rows),
    vs.length > shown.length && h("button", { class: "linkish fit-more", onclick: () => { J.fitAll[a.id] = true; onDone ? onDone("redraw") : paintEditor({ keepScroll: true }); } }, t("Show all {n}", { n: vs.length }))]
    .filter(Boolean);
}

function subsetMenu(anchor, a, v, onDone) {
  openMenu(anchor, [
    { label: t("Preview"), icon: "file", onClick: () => { if (onDone) onDone("close"); previewSubset(v.id); } },
    { label: t("Adjust it for this kind of role"), hint: t("a chat"), icon: "sparkle", disabled: !ui.chat.available,
      onClick: () => { if (onDone) onDone("close"); tailor(a, v.id); } },
  ], { align: "end" });
}

function fitFoot(a, onDone) {
  return h("div", { class: "fit-foot" },
    h("button", { class: "btn sm", disabled: !ui.chat.available, title: t("Claude compares what each subset says with the evaluation and recommends one"),
      onclick: () => { if (onDone) onDone("close"); jobChat(`/api/jobs/app/${encodeURIComponent(a.id)}/choose`); } }, icon("sparkle"), t("Ask Claude which fits")),
    h("button", { class: "btn sm ghost", disabled: !ui.chat.available, onclick: () => { if (onDone) onDone("close"); newSubsetFor(a); } }, icon("plus"), t("New subset for this kind of role")),
    h("span", { class: "fit-note" }, t("Ranked by how many of the posting's requirements and keywords each resume already mentions.")));
}

function resumeCard(a) {
  const F = J.fit[a.id];
  if (!F) loadFit(a.id);
  const linked = (F && F.linked) || a.subsets || [];
  const sent = a.sent;
  const sentBar = linked.length > 0 && h("div", { class: "sent-bar" + (sent ? " done" : "") },
    sent ? [icon("check"), h("span", { class: "sb-text" }, t("Sent on {date} with “{name}” v{n}", { date: (sent.at || "").slice(0, 10), name: subsetName(subsets().find((x) => x.id === sent.subset)) || sent.subset, n: sent.n })),
      h("button", { class: "btn sm ghost", onclick: () => showSent(sent.subset, sent.n) }, icon("history"), t("See what went out"))]
      : [h("span", { class: "sb-text" }, t("Applied? Mark it sent: the subset is kept as a version and pinned on this posting."))],
    h("button", { class: "btn sm" + (sent ? "" : " primary"), id: "posting-send", onclick: () => sendPosting({ ...a, subsets: linked }) }, icon("check"), sent ? t("Mark as sent again") : t("Mark as sent")));
  return card(t("Resume to send"), linked.length ? t("“{name}”: change it any time", { name: linked[0].label }) : t("one of your subsets; no company names needed"),
    [sentBar, ...subsetChoices(a, F), fitFoot(a, null)], { cls: "resume-card" });
}

async function useSubset(a, subsetId, onDone) {
  try {
    const r = await api("PUT", `/api/jobs/app/${encodeURIComponent(a.id)}/resume`, { subset_id: subsetId });
    applyState(r);
    if (J.fit[a.id]) J.fit[a.id].linked = r.linked;
    if (J.detail && J.detail.id === a.id) J.detail.subsets = r.linked;
    toast(subsetId ? t("Linked: you'll send “{name}” for this posting", { name: (r.linked[0] || {}).label || subsetId }) : t("Unlinked"));
    loadJobs({ quiet: true });
    if (onDone) onDone("redraw");
    else paintEditor({ keepScroll: true });
  } catch (e) { toast(e.message); }
}

async function previewSubset(id) {
  if (W.ws !== "resume") setWorkspace("resume");
  if (ui.subsetId !== id) await switchSubset(id);
}

async function newSubsetFor(a) {
  const s = a.summary || {};
  const label = await modal({ title: t("New subset for this kind of role"),
    message: t("Name it after the kind of role, not the company: you'll send it to similar postings too. Claude then picks its entries and writes its headline from this evaluation."),
    input: a.role || s.archetype || "", placeholder: t("Subset name, e.g. AI Agent Engineer"), ok: t("Create") });
  if (!label || !label.trim()) return;
  await tailor(a, null, label.trim());
}

function requirementsCard(reqs) {
  const MATCH = { strong: ["check", "good", t("Strong")], partial: ["alert", "mid", t("Partial")], missing: ["x", "low", t("Missing")], na: [null, "none", t("Not applicable")] };
  return card(t("What the posting asks for"), t("{n} requirement", { n: reqs.length }, "{n} requirements"), h("div", { class: "reqs" }, reqs.map((r) => {
    const m = MATCH[String(r.match).toLowerCase()] || [null, "none", r.match];
    const imp = String(r.importance || "").toLowerCase();
    return h("div", { class: "req", title: r.signal ? `“${r.signal}”` : null },
      h("span", { class: "req-match " + m[1], title: m[2] }, m[0] ? icon(m[0]) : "–"),
      h("span", { class: "req-text" }, r.requirement),
      imp && h("span", { class: "imp imp-" + imp }, IMPORTANCE[imp] ? t(IMPORTANCE[imp]) : r.importance));
  })));
}

function actionsCard(a) {
  const acts = (J.data && J.data.actions) || [];
  return card(t("Next steps with {name}", { name: jobsTool().name }), t("each one opens a chat"), h("div", { class: "act-grid" },
    acts.map((x) => h("button", { class: "act", disabled: !ui.chat.available, onclick: () => jobChat(`/api/jobs/app/${encodeURIComponent(a.id)}/action`, { action: x.id }) },
      icon(x.icon || "sparkle"), h("span", { class: "act-main" }, h("span", { class: "act-l" }, x.label), h("span", { class: "act-d" }, x.desc)))),
    h("button", { class: "act", disabled: !ui.chat.available, onclick: () => toolChat(t("About {job} (report {report}): ", { job: [a.company, a.role].filter(Boolean).join(" — "), report: a.report || "#" + a.id }), a.company || "") },
      icon("chat"), h("span", { class: "act-main" }, h("span", { class: "act-l" }, t("Ask about this job")), h("span", { class: "act-d" }, t("Anything else, in your own words"))))));
}

function postingMenu(anchor, a) {
  openMenu(anchor, [
    a.url && { label: t("Open the posting"), icon: "external", onClick: () => api("POST", "/api/open", { url: a.url }) },
    webInfo().available && webRoute("application", a.id) && { label: t("Open in Job search"), icon: "briefcase", onClick: () => setWorkspace("jobs", { path: webRoute("application", a.id) }) },
    a.report && { label: t("Open the report file"), icon: "file", onClick: () => api("POST", "/api/jobs/reveal", { app: a.id }) },
    { label: t("Refresh"), icon: "refresh", onClick: () => refreshPosting() },
  ], { align: "end" });
}

async function setPostingStatus(id, state) {
  try {
    J.detail = { ...(await api("PUT", `/api/jobs/app/${encodeURIComponent(id)}/status`, { state })), subsets: (J.detail || {}).subsets };
    toast(t("Status: {state}", { state: stateName(state) }));
    loadJobs({ quiet: true });
  } catch (e) { toast(e.message, { ms: 8000 }); refreshPosting(); }
}

// adjust a subset for this kind of posting (subsetId), or make a new one (label); Claude does the picking
async function tailor(a, subsetId, label = null) {
  if (W.ws !== "resume") setWorkspace("resume");
  const r = await jobChat(`/api/jobs/app/${encodeURIComponent(a.id)}/tailor`, { subset_id: subsetId, label });
  if (!r) return;
  delete J.fit[a.id];
  await switchSubset(r.subset_id);
  loadJobs({ quiet: true });
}

// the postings a job is sent to, in Job settings
function postingLink(v) {
  const ps = [...(v && v.posting ? [v.posting] : []), ...((v && v.postings) || [])];
  if (!ps.length) return null;
  const apps = (J.data && J.data.applications) || [];
  return h("div", { class: "posting-link" }, icon("bolt"),
    h("span", { class: "nb-text" }, t("Sent for {n} posting", { n: ps.length }, "Sent for {n} postings"), ": ",
      ps.map((p, i) => {
        const a = apps.find((x) => x.id === String(p.id));
        return [i ? ", " : "", a ? h("button", { class: "linkish", onclick: () => openPosting(a.id) }, [a.company, a.role].filter(Boolean).join(" — "))
          : t("application #{id} in {tool}", { id: p.id, tool: p.tool })];
      })));
}

// ================================================================ Settings: the tool's section
const jobsPending = {};   // cv_lang / notes / sync picked in Settings, saved on Done
function jobsSettingsSection() {
  const wrap = h("div", { id: "jobs-settings", class: "field jobs-settings" });
  paintJobsSettings(wrap);
  if (!J.data) loadJobs({ quiet: true }).then(() => paintJobsSettings());
  return wrap;
}

function paintJobsSettings(el = $("#jobs-settings")) {
  if (!el || !J.data) return;
  const st = J.data.status;
  const name = st.tool.name;
  const label = h("label", null, t("Job postings: {name}", { name }), h("span", { class: "hint" }, t("Evaluates postings and tracks applications; open source, runs in its own folder")));
  if (!st.installed) {
    el.replaceChildren(label, h("div", { class: "row wrap" }, h("span", { class: "muted" }, t("Not connected")),
      h("button", { class: "btn sm", onclick: () => { closeSettings(); setWorkspace("jobs"); } }, t("Set it up in Job search"))));
    return;
  }
  const up = J.update;
  const prog = ["update", "rollback", "repair"].map((k) => J.progress[k]).find((p) => p && p.state === "running")
    || J.progress.update || J.progress.rollback || J.progress.repair || {};
  const running = prog.state === "running";
  const fixable = (st.checks || []).some((c) => !c.ok && ["deps", "skill"].includes(c.id));
  const upLine = running ? h("span", { class: "badge busy" }, J.progress.repair && J.progress.repair.state === "running" ? t("Repairing…") : t("Updating…"))
    : !up ? null
    : up.status === "update-available" ? h("span", { class: "badge ok" }, t("Update available: {from} → {to}", { from: up.local, to: up.remote }))
    : up.status === "up-to-date" ? h("span", { class: "badge" }, t("Up to date"))
    : h("span", { class: "badge bad" }, up.status === "offline" ? t("Offline: couldn't check") : t("Couldn't check: {why}", { why: up.status }));
  const s = { ...st.settings, ...jobsPending };
  const resumeLangs = langs();
  const cvLang = s.cv_lang && resumeLangs.includes(s.cv_lang) ? s.cv_lang : (resumeLangs.includes("en") ? "en" : resumeLangs[0]);
  const checkRow = (key, text) => h("button", { type: "button", class: "check-row", role: "checkbox", "aria-checked": String(!!s[key]),
    onclick: () => { jobsPending[key] = !s[key]; paintJobsSettings(); } }, h("span", { class: "check" + (s[key] ? " on" : "") }), text);
  el.replaceChildren(...[label,
    h("div", { class: "data-path" }, st.root),
    h("div", { class: "row wrap" },
      h("span", { class: "chip" }, `v${st.version || "?"}`), upLine,
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm", disabled: running, onclick: checkToolUpdate }, icon("refresh"), t("Check for updates")),
      up && up.status === "update-available" && h("button", { class: "btn sm primary", disabled: running, onclick: runToolUpdate }, t("Update"))),
    up && up.changelog && h("pre", { class: "changelog" }, String(up.changelog).slice(0, 1200)),
    prog.state === "error" && h("pre", { class: "connect-err" }, prog.error || ""),
    h("div", { class: "checks" }, (st.checks || []).map((c) => h("div", { class: "chk " + (c.ok ? "ok" : "bad"), title: c.detail || null },
      h("span", { class: "chk-ic" }, icon(c.ok ? "check" : "x")), h("span", null, t(CHECK_NAMES[c.id] || c.id)), h("span", { class: "chk-d" }, c.detail || "")))),
    h("p", { class: "settings-note" }, t("After every update these are checked again: they are what Resume Studio reads from {name}. If one fails, you can go back to the previous version.", { name })),
    webInfo().available && h("div", { class: "row wrap" },
      h("span", { class: "muted", style: "font-size:12.5px" }, t("Its web interface (Job search)")),
      { running: h("span", { class: "badge ok" }, t("Running")), preparing: h("span", { class: "badge busy" }, t("Starting…")),
        starting: h("span", { class: "badge busy" }, t("Starting…")), error: h("span", { class: "badge bad" }, t("Didn't start")) }[webInfo().state]
        || h("span", { class: "badge" }, t("Not started")),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm", onclick: () => startWeb({ restart: true }) }, icon("refresh"), t("Restart")),
      h("button", { class: "btn sm ghost", title: t("Installs its dependencies and builds it again from scratch"), onclick: () => startWeb({ rebuild: true }) }, t("Rebuild it"))),
    h("div", { class: "row wrap" },
      fixable && h("button", { class: "btn sm primary", disabled: running, title: t("Installs its dependencies again and fixes its skill entrypoints; your data stays"), onclick: repairTool }, icon("refresh"), t("Repair")),
      h("button", { class: "btn sm", onclick: () => api("POST", "/api/jobs/reveal", {}) }, icon("folderOpen"), t("Open folder")),
      h("button", { class: "btn sm ghost", disabled: running, onclick: rollbackTool }, icon("undo"), t("Undo the last update"))),
    h("div", { class: "sub-label" }, t("The CV {name} reads", { name })),
    checkRow("sync", t("Write cv.md from my library (never over changes made in {name})", { name })),
    checkRow("notes", t("Also share my fact layer (notes) as proof points")),
    resumeLangs.length > 1 && h("div", { class: "row" }, h("span", { class: "muted", style: "font-size:12.5px" }, t("Language")),
      uiSeg(resumeLangs.map((l) => ({ value: l, label: langShort(l), tip: langName(l) })), cvLang, (v) => { jobsPending.cv_lang = v; paintJobsSettings(); }, { cls: "sm", label: t("Language") }))]
    .filter(Boolean));
}

async function applyJobsSettings() {
  if (!Object.keys(jobsPending).length) return;
  const body = { ...jobsPending };
  for (const k of Object.keys(jobsPending)) delete jobsPending[k];
  const r = await api("PUT", "/api/jobs/settings", body);
  if (J.data) J.data.status = r.status;
  renderJobs();
}

async function checkToolUpdate() {
  J.update = { status: "checking" };
  try { J.update = await api("GET", "/api/jobs/update"); }
  catch (e) { J.update = { status: e.message }; }
  paintJobsSettings();
}

async function runToolUpdate() {
  if (!(await confirmInSettings(t("Update {name}?", { name: jobsTool().name }), t("It uses {name}'s own updater: your evaluations, tracker and profile are not touched, and you can go back afterwards.", { name: jobsTool().name })))) return;
  J.progress.update = { state: "running" };
  paintJobsSettings();
  try { await api("POST", "/api/jobs/update", {}); }
  catch (e) { J.progress.update = { state: "error", error: e.message }; paintJobsSettings(); }
}

async function repairTool() {
  J.progress.repair = { state: "running" };
  paintJobsSettings();
  try { await api("POST", "/api/jobs/repair", {}); }
  catch (e) { J.progress.repair = { state: "error", error: e.message }; paintJobsSettings(); }
}

async function rollbackTool() {
  if (!(await confirmInSettings(t("Go back to the previous version?"), t("Undoes the last update of {name} with its own rollback. Your data stays.", { name: jobsTool().name })))) return;
  J.progress.rollback = { state: "running" };
  paintJobsSettings();
  try { await api("POST", "/api/jobs/rollback", {}); }
  catch (e) { J.progress.rollback = { state: "error", error: e.message }; paintJobsSettings(); }
}

// a yes/no inside the open Settings dialog (the shared modal() would replace it)
function confirmInSettings(title, message) {
  return new Promise((resolve) => {
    const el = $("#jobs-settings");
    if (!el) { resolve(false); return; }
    const box = h("div", { class: "inline-confirm" }, h("b", null, title), h("p", null, message),
      h("div", { class: "row" },
        h("button", { class: "btn sm", onclick: () => { box.remove(); resolve(false); } }, t("Cancel")),
        h("button", { class: "btn sm primary", onclick: () => { box.remove(); resolve(true); } }, t("Continue"))));
    el.append(box);
    box.scrollIntoView({ block: "nearest" });
  });
}

function listenJobs(es) {
  es.addEventListener("jobs", () => loadJobs({ quiet: true }));
  es.addEventListener("jobs.progress", (m) => onJobsProgress(JSON.parse(m.data)));
  es.addEventListener("jobs.web", () => refreshWeb());
}

// ================================================================ the Job search workspace
// The tool's own web interface fills the window (career-ops ships one: pipeline table, discovery,
// follow-ups, analytics…). Resume Studio adds what only it can: the switch back to the resume, Claude's
// chat in the tool's folder on the left, every mode of the tool, and "make a tailored resume" for the
// posting open in the frame. The frame tells the window which page it shows (core/webhost.py).
const W = {
  ws: "resume",
  path: "/",             // the page open in the frame
  frameBase: null,       // the address the frame was loaded from (reloads when the server comes back)
  pendingPath: null,     // a page to open once the frame is up
  chat: "on",            // the chat pane on the left (every workspace)
  autoStarted: false,
};

const MODE_GROUPS = [
  ["Find postings", ["scan", "pipeline", "triage", "batch", "titles", "discover"]],
  ["Evaluate", ["job", "jobs", "deep", "interview-redflag"]],
  ["Apply for jobs", ["apply", "cover", "email", "contacto"]],
  ["Interviews and offers", ["interview-prep", "interview/plan", "interview/practice", "interview/debrief", "offer-prep"]],
  ["Keep track", ["tracker", "followup", "reply-watch", "outcome", "patterns", "calibrate", "upskill"]],
  ["Your profile", ["interview", "master-profile", "intake", "expand", "add", "training", "project", "agent-inbox"]],
  // career-ops makes CVs of its own too; the resumes you send come from Resume Studio's subsets
  ["Its own CV files", ["pdf", "text", "latex", "latex-tex", "ats"]],
];
const HIDDEN_MODES = ["update"];   // Settings updates the tool with its own updater
const MODE_NAMES = {
  scan: "Scan job boards", pipeline: "Process saved links", triage: "Quick first pass", batch: "Evaluate many at once",
  titles: "Job titles to also search", discover: "Add companies to scan",
  job: "Evaluate one posting", jobs: "Compare postings", deep: "Company research", "interview-redflag": "Red flags at a company",
  apply: "Fill in an application", cover: "Cover letter", email: "Application email", contacto: "LinkedIn outreach",
  pdf: "Tailored CV (its own PDF)", text: "Tailored CV as Markdown", latex: "CV for LaTeX / Overleaf", "latex-tex": "Tailor my own .tex CV",
  ats: "ATS check of a CV",
  "interview-prep": "Interview prep", "interview/plan": "Interview prep plan", "interview/practice": "Practice interview",
  "interview/debrief": "After an interview", "offer-prep": "Read an offer before signing",
  tracker: "Where my applications stand", followup: "Follow-ups due", "reply-watch": "Sort employer replies",
  outcome: "Record an outcome", patterns: "Why I get rejected", calibrate: "Do the scores predict outcomes?", upskill: "Skills worth learning",
  interview: "Profile interview", "master-profile": "Master career profile", intake: "Import my documents",
  expand: "Skills I forgot to list", add: "Add something to my CV", training: "Is a course worth it?", project: "Is a project worth it?",
  "agent-inbox": "Requests for later",
};
const WEB_STEPS = { web_deps: "Install its dependencies", web_build: "Build it", start: "Start it" };

const webInfo = () => (J.data && J.data.web) || { available: false, state: "off" };
const webUp = () => jobsReady() && webInfo().state === "running" && !!webInfo().url;
// the interface's address under the window's own host name (localhost and 127.0.0.1 are different
// sites to the browser, and the interface only accepts a frame from the window's own site)
function webBase() {
  const u = new URL(webInfo().url);
  if (["localhost", "127.0.0.1"].includes(location.hostname)) u.hostname = location.hostname;
  return u.origin;
}

function setWorkspace(ws, { path = null } = {}) {
  if (ws === "jobs" && path) W.pendingPath = path;
  W.ws = ws;
  document.body.dataset.ws = ws;
  store("ws", ws);
  for (const b of document.querySelectorAll(".ws-btn")) {
    b.classList.toggle("on", b.dataset.ws === ws);
    b.setAttribute("aria-selected", String(b.dataset.ws === ws));
  }
  closeFloat();
  chatForWorkspace();
  if (ws === "jobs") {
    store("jobs-web-used", "1");
    paintJobspace();
    startWeb();
    loadJobs({ quiet: true });   // what changed in the tool meanwhile (a cv.md edited there, a failed check)
  } else if (ws === "fill") {
    paintFill();
    loadFill();
    loadHunt();
  } else if (ws === "plan") {
    paintPlan();
    loadPlan();
  } else if (typeof applyZoom === "function" && ui.zoom === "fit") applyZoom();
  renderWsTools();
}

// the chat pane on the left, in every workspace: shown or hidden from the Claude button in the top bar
function setChatPane(on) {
  W.chat = on ? "on" : "off";
  document.body.dataset.chat = W.chat;
  store("chat-pane", W.chat);
  if (on) { renderChat(); setTimeout(() => $("#chat-input").focus(), 0); }
  paintChatToggle();
  if (typeof applyZoom === "function" && ui.zoom === "fit") applyZoom();
}

function paintChatToggle() {
  const b = $("#chat-toggle");
  if (!b) return;
  b.classList.toggle("on", W.chat === "on");
  b.setAttribute("aria-pressed", String(W.chat === "on"));
  const lbl = b.querySelector(".lbl");   // the open chat's agent (Claude, Codex, Qwen…)
  if (lbl && typeof agentOf === "function") lbl.textContent = agentOf().short;
  const dot = b.querySelector(".tab-dot");
  if (dot) dot.classList.toggle("hidden", !(ui.chat.running || (ui.chat.busy || []).length || ui.chat.waiting.size));
  if (dot) dot.classList.toggle("wait", ui.chat.waiting.size > 0);
}

async function refreshWeb() {
  try {
    const web = await api("GET", "/api/jobs/web");
    if (J.data) J.data.web = web;
  } catch (_) { return; }
  paintJobspace();
  renderWsTools();
  paintJobsSettings();
}

// start the tool's interface when it is wanted and not running (it installs / builds itself the first time)
async function startWeb({ restart = false, rebuild = false } = {}) {
  const web = webInfo();
  if (!jobsReady() || !web.available) return;
  if (!restart && !rebuild && web.state !== "off") return;
  try { J.data.web = await api("POST", "/api/jobs/web/start", { restart, rebuild }); }
  catch (e) { toast(e.message); }
  paintJobspace();
  renderWsTools();
}

function paintJobspace() {
  const el = $("#jobspace");
  if (!el) return;
  let frame = el.querySelector("iframe.web-frame");
  const web = webInfo();
  if (webUp()) {
    if (!frame) {
      frame = h("iframe", { class: "web-frame", title: t("{name}'s own interface", { name: jobsTool().name }) });
      el.prepend(frame);
    }
    const base = webBase();
    if (W.frameBase !== base || W.pendingPath) {
      frame.src = base + (W.pendingPath || W.path || "/");
      W.frameBase = base;
      if (W.pendingPath) W.path = W.pendingPath;
      W.pendingPath = null;
    }
    frame.classList.remove("hidden");
  } else if (frame) {
    frame.classList.add("hidden");
    W.frameBase = null;   // load it again once the server is back (after an update, a restart)
  }
  const old = el.querySelector(".js-center");
  if (old) old.remove();
  if (!webUp()) el.append(h("div", { class: "js-center" }, jobspaceCard()));
  // what Resume Studio has to say about the tool (a check failed, cv.md changed there), above its interface
  const warn = el.querySelector(".js-banners");
  if (warn) warn.remove();
  const banners = jobsReady() && !(J.progress.install && J.progress.install.state === "running") ? jobsBanners(J.data.status) : [];
  if (banners.length) el.prepend(h("div", { class: "js-banners" }, banners));
  const dot = $("#ws-jobs-dot");
  if (dot) dot.classList.toggle("hidden", !["preparing", "starting"].includes(web.state) || W.ws === "jobs");
}

function jobspaceCard() {
  if (!J.data) return h("div", { class: "muted" }, t("Loading…"));
  const st = J.data.status;
  const name = st.tool.name;
  if (!st.installed || (J.progress.install && J.progress.install.state === "running")) return h("div", { class: "js-card" }, connectCard(st));
  const web = webInfo();
  if (!web.available) {
    return h("div", { class: "js-card" },
      h("h2", null, t("This version of {name} has no web interface of its own", { name })),
      h("p", null, t("Its evaluations are listed below; every mode is in “All of {name}” above.", { name })),
      applicationsList());
  }
  if (web.state === "error") {
    return h("div", { class: "js-card" },
      h("h2", null, t("{name}'s interface didn't start", { name })),
      h("pre", { class: "connect-err" }, [web.error, web.log].filter(Boolean).join("\n\n")),
      h("div", { class: "row wrap" },
        h("button", { class: "btn primary", onclick: () => startWeb({ restart: true }) }, icon("refresh"), t("Try again")),
        h("button", { class: "btn", title: t("Installs its dependencies and builds it again from scratch"), onclick: () => startWeb({ rebuild: true }) }, t("Rebuild it")),
        h("button", { class: "btn ghost", onclick: () => api("POST", "/api/jobs/reveal", {}) }, icon("folderOpen"), t("Open the {name} folder", { name }))));
  }
  const steps = web.steps || {};
  const stepState = (s) => (s === "start" ? (web.state === "starting" ? "running" : "todo") : steps[s] || "todo");
  return h("div", { class: "js-card" },
    h("h2", null, t("Starting {name}'s own interface", { name })),
    h("p", null, t("The first time, it is installed and built inside its own folder (about a minute); after that it starts in a few seconds. It is built again only after {name} is updated.", { name })),
    h("div", { class: "steps" }, Object.keys(WEB_STEPS).map((s) => {
      const state = stepState(s);
      return h("div", { class: "step " + state }, h("span", { class: "st-ic" }, state === "done" ? icon("check") : null), t(WEB_STEPS[s]));
    })));
}

// the application open in the frame (its id from the tool's own route), if any
function webApp() {
  const tpl = (webInfo().routes || {}).application;
  if (!tpl || !J.data) return null;
  const re = new RegExp("^" + tpl.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace("{id}", "([^/?#]+)") + "(?:[/?#]|$)");
  const m = re.exec(W.path || "");
  if (!m) return null;
  const id = decodeURIComponent(m[1]);
  return (J.data.applications || []).find((a) => a.id === id) || { id, company: "#" + id, role: "", subsets: [] };
}

function webRoute(kind, id) {
  const tpl = (webInfo().routes || {})[kind];
  return tpl ? tpl.replace("{id}", encodeURIComponent(id)) : null;
}

function renderWsTools() {
  const el = $("#ws-tools");
  if (!el) return;
  if (W.ws === "resume") { el.replaceChildren(); return; }
  if (Float.cur && el.contains(Float.cur.anchor)) return;   // a menu hangs off these buttons: redrawn when it closes
  if (W.ws === "fill") { renderFillTools(el); return; }
  if (W.ws === "plan") { renderPlanTools(el); return; }
  const name = jobsTool().name;
  const out = [];
  if (jobsReady()) out.push(h("button", { class: "btn ghost", id: "evaluate-btn", "aria-haspopup": "dialog", disabled: !ui.chat.available,
    title: t("Claude runs {name}'s evaluation in a new chat; it takes a few minutes.", { name }), onclick: (e) => openEvaluate(e.currentTarget) },
  icon("sparkle"), h("span", { class: "lbl" }, t("Evaluate a posting"))));
  if (jobsReady()) out.push(h("button", { class: "btn ghost", title: t("Every mode of {name}, each one as a chat with Claude", { name }), onclick: (e) => openLauncher(e.currentTarget) }, icon("apps"), h("span", { class: "lbl" }, t("All of {name}", { name }))));
  const a = webUp() ? webApp() : null;
  if (a) {
    const linked = a.subsets || [];
    out.push(h("span", { class: "ws-here" }, h("b", { title: [a.company, a.role].filter(Boolean).join(" — ") }, [a.company, a.role].filter(Boolean).join(" — ")),
      h("button", { class: "btn sm" + (linked.length ? "" : " primary"), "aria-haspopup": "dialog",
        title: t("Which of your resume subsets you send for this posting"), onclick: (e) => openResumePicker(e.currentTarget, a) },
      icon("file"), h("span", { class: "lbl" }, linked.length ? t("Resume: {name}", { name: linked[0].label }) : t("Pick a resume")), icon("chev"))));
  }
  out.push(h("span", { class: "spacer" }));
  const st = J.data && J.data.status;
  const prof = (st && st.profile) || {};
  if (jobsReady() && (st.needs_setup || prof.stale)) {
    out.push(h("button", { class: "btn sm" + (st.needs_setup ? " primary" : ""), disabled: !ui.chat.available,
      title: (st.needs_setup ? t("{name} doesn't know what you're looking for yet (roles, location, salary), so its scores would follow someone else's targets.", { name: st.tool.name }) + " "
        : t("Your fact layer or resume subsets changed since {name}'s profile was filled from them.", { name: st.tool.name }) + " ")
        + t("Claude fills it from your profile's fact layer and your resume subsets, and asks only for what's missing"),
      onclick: () => setupProfile(!st.needs_setup) }, icon("user"), st.needs_setup ? t("Fill its profile from my resume") : t("Update its profile")));
  }
  if (jobsReady()) out.push(h("button", { class: "btn icon ghost", title: t("More"), onclick: (e) => webMenu(e.currentTarget) }, icon("more")));
  el.replaceChildren(...out);
}

// the resume for the posting open in the frame: its subsets ranked, as on its page in Resume
function openResumePicker(anchor, a) {
  const panel = h("div", { class: "resume-picker", role: "dialog", "aria-label": t("Resume to send") });
  const paint = () => {
    const F = J.fit[a.id];
    if (!F && !J.fitLoading[a.id]) loadFit(a.id).then(() => { if (panel.isConnected) paint(); });   // the resume just changed
    const linked = (F && F.linked) || a.subsets || [];
    panel.replaceChildren(...[
      h("div", { class: "rp-head" }, h("b", null, t("Resume to send")), h("span", { class: "muted" }, [a.company, a.role].filter(Boolean).join(" — "))),
      ...subsetChoices(a, F, { onDone }),
      fitFoot(a, onDone),
      h("div", { class: "rp-acts" },
        h("button", { class: "btn sm ghost", id: "posting-page-btn", title: t("Its verdict, how you fit, its requirements and tailoring, next to the resume preview"),
          onclick: () => { closeFloat(); openPosting(a.id); } }, icon("bolt"), t("Open its page in Resume")),
        h("span", { class: "spacer" }),
        linked.length > 0 && h("button", { class: "btn sm", onclick: () => { closeFloat(); previewSubset(linked[0].id); } }, icon("file"), t("Preview in Resume")),
        linked.length > 0 && h("button", { class: "btn sm primary", onclick: async () => { closeFloat(); await previewSubset(linked[0].id); exportPdf(); } }, t("Export PDF")))]
      .filter(Boolean));
  };
  const onDone = (kind) => { if (kind === "close") closeFloat(); else paint(); };
  paint();
  openFloat(anchor, panel, { minWidth: 520, align: "start", onClose: () => setTimeout(renderWsTools, 0) });
  loadFit(a.id, { force: true }).then(paint);
}

function webMenu(anchor) {
  const name = jobsTool().name;
  const web = webInfo();
  const frame = $("#jobspace iframe.web-frame");
  openMenu(anchor, [
    webUp() && { label: t("Reload this page"), icon: "refresh", onClick: () => { if (frame) frame.src = webBase() + (W.path || "/"); } },
    webUp() && { label: t("Open in the browser"), icon: "external", onClick: () => api("POST", "/api/open", { url: webBase() + (W.path || "/") }) },
    web.available && "sep",
    web.available && { label: t("Restart its interface"), icon: "refresh", onClick: () => startWeb({ restart: true }) },
    web.available && { label: t("Rebuild its interface"), hint: t("about a minute"), icon: "settings", onClick: () => startWeb({ rebuild: true }) },
    "sep",
    { label: t("Open the {name} folder", { name }), icon: "folderOpen", onClick: () => api("POST", "/api/jobs/reveal", {}) },
    { label: t("{name} settings", { name }), icon: "settings", onClick: () => openSettings("jobs") },
  ], { align: "end", onClose: () => setTimeout(renderWsTools, 0) });
}

// every mode of the tool, grouped; each one starts a chat in its folder
function openLauncher(anchor) {
  const name = jobsTool().name;
  const modes = ((J.data && J.data.modes) || []).filter((m) => !HIDDEN_MODES.includes(m.id));
  const label = (m) => (MODE_NAMES[m.id] ? t(MODE_NAMES[m.id]) : m.id);
  const placed = new Set(MODE_GROUPS.flatMap(([, ids]) => ids));
  const groups = MODE_GROUPS.map(([title, ids]) => [t(title), ids.map((id) => modes.find((m) => m.id === id)).filter(Boolean)]);
  const rest = modes.filter((m) => !placed.has(m.id));
  if (rest.length) groups.push([t("More"), rest]);
  const run = (m) => { closeFloat(); jobChat("/api/jobs/mode", { mode: m.id }); };
  const item = (m) => h("button", { type: "button", class: "lg-item", "data-mode": m.id, "data-q": `${label(m)} ${m.id} ${m.purpose}`.toLowerCase(), onclick: () => run(m) },
    h("span", { class: "lg-l" }, label(m)), m.purpose && h("span", { class: "lg-d" }, m.purpose), h("span", { class: "lg-id" }, m.file));
  const grid = h("div", { class: "launcher-grid" }, groups.filter(([, ms]) => ms.length).map(([title, ms]) =>
    h("div", { class: "lg-group" }, h("div", { class: "lg-title" }, title), ms.map(item))));
  const search = h("input", { type: "search", placeholder: t("Search {n} modes", { n: modes.length }), spellcheck: "false",
    oninput: () => {
      const q = search.value.trim().toLowerCase();
      for (const b of grid.querySelectorAll(".lg-item")) b.classList.toggle("hidden", !!q && !b.dataset.q.includes(q));
      for (const g of grid.querySelectorAll(".lg-group")) g.classList.toggle("hidden", ![...g.querySelectorAll(".lg-item")].some((b) => !b.classList.contains("hidden")));
    },
    onkeydown: (e) => { if (e.key === "Enter") { const b = grid.querySelector(".lg-item:not(.hidden)"); if (b) b.click(); } } });
  const panel = h("div", { class: "launcher", role: "dialog", "aria-label": t("All of {name}", { name }) },
    h("div", { class: "launcher-head" }, search,
      h("button", { class: "btn sm", disabled: !ui.chat.available, onclick: () => { closeFloat(); toolChat(""); } }, icon("chat"), t("Chat in {name}", { name }))),
    grid,
    h("div", { class: "launcher-foot" }, t("Each one opens a chat on the left where Claude runs that mode of {name}; it asks you for anything it needs.", { name })));
  openFloat(anchor, panel, { minWidth: 560, focus: search, onClose: () => setTimeout(renderWsTools, 0) });
}

// the frame reports its page (only from the tool's interface, see core/webhost.py)
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || d.source !== "resume-studio-webhost") return;
  if (!webUp() || e.origin !== webBase()) return;
  W.path = String(d.path || "/");
  store("jobs-path", W.path);
  renderWsTools();
  renderComposerState();   // the chat says which page is open
});
// a click inside the frame never reaches this page: close open menus when it takes the focus
window.addEventListener("blur", () => setTimeout(() => {
  if (document.activeElement && document.activeElement.tagName === "IFRAME") closeFloat();
}, 0));

function initWorkspaces() {
  for (const b of document.querySelectorAll(".ws-btn")) {
    b.prepend(icon({ jobs: "briefcase", fill: "globe", plan: "compass" }[b.dataset.ws] || "file"));
    b.onclick = () => setWorkspace(b.dataset.ws);
  }
  W.path = store("jobs-path") || "/";
  W.chat = store("chat-pane") === "off" ? "off" : "on";
  document.body.dataset.chat = W.chat;
  const toggle = $("#chat-toggle");
  toggle.prepend(icon("chat"));
  toggle.onclick = () => setChatPane(W.chat !== "on");
  paintChatToggle();
  F.site = store("fill-site") || F.site;
  F.tab = store("fill-tab") === "push" ? "push" : "copy";
  restoreHunt();
  setWorkspace(["jobs", "fill", "plan"].includes(store("ws")) ? store("ws") : "resume");
}
