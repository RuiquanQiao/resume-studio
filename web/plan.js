// The Plan workspace (docs/PLAN.md): one Markdown document the user works out with Claude, and its
// versions. It has no fixed shape: empty, it says one line; written, it reads like a document. The top bar
// holds Edit and Versions; ticking a box is the only other thing the page does.
"use strict";

const P = {
  data: null,        // GET /api/plan: {text, digest, updated, kept, versions: [{n, at, why, by, edited}]}
  editing: false,
  draft: "", orig: "", why: "", base: null,   // the text being edited, what it was, its digest
  stale: false,      // changed elsewhere while the user edits
  side: false,       // the versions list is open
  view: null,        // an old version open instead of the plan: {n, at, why, by, text}
  compare: false,    // that version shown as its changes against the plan now
};

async function loadPlan() {
  try { P.data = await api("GET", "/api/plan"); } catch (_) { return; }
  if (P.view && !P.data.versions.some((v) => v.n === P.view.n)) P.view = null;
  if (W.ws !== "plan") return;
  paintPlan();
  renderWsTools();
  renderComposerState();
}

function listenPlan(es) {
  es.addEventListener("plan", () => {
    if (P.editing && P.data) P.stale = true;
    loadPlan();
  });
}

const planWhen = (iso) => String(iso || "").slice(0, 16).replace("T", " ");
const planBy = (v) => ({ claude: "Claude", outside: t("another editor") }[v.by] || t("you"));
const planLine = () => (P.view ? t("version {n} of your plan", { n: P.view.n }) : P.editing ? t("your plan, being edited") : t("your plan"));

function paintPlan() {
  const el = $("#planspace");
  if (!el) return;
  // a field being typed in keeps its focus and caret: only the bar above it follows
  if (P.editing && el.querySelector(".plan-text")) { paintPlanEditBar(); return; }
  if (!P.data) { el.replaceChildren(h("div", { class: "plan-empty" }, h("p", { class: "muted" }, t("Loading…")))); return; }
  let main;
  if (P.editing) main = planEditor();
  else if (P.view) main = planVersionView();
  else if (!P.data.text.trim()) main = h("div", { class: "plan-empty" }, h("p", null, t("Whatever worries you, tell Claude: it will help you sort it out here.")));
  else main = planDoc(P.data.text, true);
  // the same page drawn again (a box ticked, Claude's change) keeps where the user was reading
  const key = P.editing ? "edit" : P.view ? `v${P.view.n}:${P.compare}` : "now";
  const prev = el.querySelector(".plan-scroll"), top = prev && el.dataset.key === key ? prev.scrollTop : 0;
  const scroll = h("div", { class: "plan-scroll" + (P.editing ? " editing" : "") }, main);
  el.replaceChildren(...[scroll, P.side && !P.editing && planVersions()].filter(Boolean));
  el.dataset.key = key;
  scroll.scrollTop = top;
  if (P.editing) paintPlanEditBar();
}

// the document; `live`: its boxes tick (an old version's don't)
function planDoc(text, live) {
  const art = h("article", { class: "plan-doc", html: md(text) });
  art.querySelectorAll("li.task > .check").forEach((c, i) => {
    if (!live) { c.classList.add("ro"); return; }
    c.tabIndex = 0;
    setTip(c, c.classList.contains("on") ? t("Mark as not done") : t("Mark as done"));
    const go = () => tickPlan(i, !c.classList.contains("on"), c);
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); go(); } });
  });
  art.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-ext]");
    if (a) { e.preventDefault(); api("POST", "/api/open", { url: a.getAttribute("href") }).catch(() => window.open(a.href, "_blank")); }
  });
  return art;
}

async function tickPlan(index, done, box) {
  box.classList.toggle("on", done);
  box.setAttribute("aria-checked", String(done));
  const focused = document.activeElement === box;
  try {
    P.data = await api("POST", "/api/plan/tick", { index, done, base: P.data.digest });
    paintPlan();
    const again = document.querySelectorAll("#planspace .plan-doc li.task > .check")[index];
    if (focused && again) again.focus();
  }
  catch (e) {
    toast(e.status === 409 ? t("The plan changed meanwhile; look again and tick it once more") : e.message);
    loadPlan();
  }
}

// ---------------------------------------------------------------- editing it yourself
function startPlanEdit() {
  P.editing = true; P.view = null; P.stale = false;
  P.draft = P.orig = P.data.text; P.base = P.data.digest; P.why = "";
  paintPlan();
  renderWsTools();
  renderComposerState();
  const ta = $("#planspace .plan-text");
  if (ta) { ta.focus(); ta.setSelectionRange(0, 0); ta.scrollTop = 0; }
}

function endPlanEdit() {
  P.editing = false; P.stale = false;
  paintPlan();
  renderWsTools();
  renderComposerState();
}

async function cancelPlanEdit() {
  if (P.draft !== P.orig) {
    if (!(await modal({ title: t("Discard your changes to the plan?"), ok: t("Discard"), danger: true }))) return;
  }
  endPlanEdit();
}

async function savePlanEdit(force = false) {
  try {
    P.data = await api("PUT", "/api/plan", { text: P.draft, why: P.why, base: force ? null : P.base });
  } catch (e) {
    if (e.status !== 409) { toast(e.message); return; }
    const ok = await modal({ title: t("The plan changed while you were editing it"),
      message: t("Claude or another editor changed it after you started. Save yours anyway? What it said now stays in the versions."),
      ok: t("Save mine") });
    if (ok) savePlanEdit(true);
    return;
  }
  const v = P.data.versions[P.data.versions.length - 1];
  endPlanEdit();
  if (v) toast(t("Saved as v{n}", { n: v.n }));
}

function planEditor() {
  const ta = h("textarea", { class: "plan-text", spellcheck: "false", "aria-label": t("Your plan, in Markdown"), value: P.draft,
    oninput: (e) => { P.draft = e.target.value; },
    onkeydown: (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "s" || e.key === "Enter")) { e.preventDefault(); e.stopPropagation(); savePlanEdit(); }
      if (e.key === "Escape" && !Float.cur) { e.preventDefault(); cancelPlanEdit(); }
    } });
  return h("div", { class: "plan-edit" }, ta, h("div", { class: "plan-edit-bar" }));
}

function paintPlanEditBar() {
  const bar = $("#planspace .plan-edit-bar");
  if (!bar) return;
  const typing = document.activeElement && document.activeElement.classList.contains("plan-why");
  if (typing) {   // the reason being typed keeps its caret; only the note about other changes follows
    const old = bar.querySelector(".pe-stale");
    if (P.stale && !old) bar.prepend(planStale());
    return;
  }
  bar.replaceChildren(...[
    P.stale && planStale(),
    h("input", { class: "plan-why", value: P.why, maxlength: 300, placeholder: t("Why it changed (optional, kept with the version)"),
      "aria-label": t("Why it changed"), oninput: (e) => { P.why = e.target.value; },
      onkeydown: (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); savePlanEdit(); } } }),
    h("button", { class: "btn", onclick: cancelPlanEdit }, t("Cancel")),
    h("button", { class: "btn primary", id: "plan-save", title: "Ctrl+S", onclick: () => savePlanEdit() }, t("Save"))].filter(Boolean));
}
const planStale = () => h("div", { class: "pe-stale" }, icon("alert"), t("Claude changed the plan while you were editing. Saving keeps what it wrote in the versions."));

// ---------------------------------------------------------------- versions
function planVersions() {
  const vs = [...P.data.versions].reverse();
  const item = (v) => h("li", null, h("button", { type: "button", class: "ps-item" + (P.view && P.view.n === v.n ? " on" : ""), onclick: () => openPlanVersion(v.n) },
    h("span", { class: "ps-top" }, h("span", { class: "ps-n" }, `v${v.n}`), h("span", { class: "ps-by" }, planBy(v)), h("span", { class: "ps-when" }, planWhen(v.at))),
    v.why ? h("span", { class: "ps-why" }, v.why) : h("span", { class: "ps-why muted" }, t("(no reason given)"))));
  return h("aside", { class: "plan-side", "aria-label": t("Versions of the plan") },
    h("div", { class: "ps-head" }, h("b", null, t("Versions")),
      h("button", { class: "btn icon sm ghost", title: t("Close"), onclick: () => { P.side = false; P.view = null; paintPlan(); renderWsTools(); renderComposerState(); } }, icon("x"))),
    h("p", { class: "ps-note" }, t("A version is kept every time the plan changes course. Ticked boxes and lines of progress go into the latest one.")),
    h("ul", { class: "ps-list" },
      h("li", null, h("button", { type: "button", class: "ps-item" + (P.view ? "" : " on"), onclick: () => { P.view = null; paintPlan(); renderComposerState(); } },
        h("span", { class: "ps-top" }, h("span", { class: "ps-n" }, t("Now")), h("span", { class: "ps-when" }, planWhen(P.data.updated))),
        h("span", { class: "ps-why muted" }, P.data.kept || !vs.length ? t("The plan as it is") : t("Changed outside the window since v{n}", { n: vs[0].n })))),
      vs.map(item)));
}

async function openPlanVersion(n) {
  try { P.view = await api("GET", `/api/plan/versions/${n}`); } catch (e) { toast(e.message); return; }
  paintPlan();
  renderComposerState();
}

function planVersionView() {
  const v = P.view;
  const same = v.text === P.data.text;
  return h("div", { class: "plan-version" },
    h("div", { class: "plan-vbar" },
      h("div", { class: "pv-title" }, h("b", null, `v${v.n}`), h("span", { class: "muted" }, ` · ${planWhen(v.at)} · ${planBy(v)}`),
        v.why && h("div", { class: "pv-why" }, v.why)),
      uiSeg([{ value: "doc", label: t("As it was") }, { value: "diff", label: t("Changes since") }], P.compare ? "diff" : "doc",
        (x) => { P.compare = x === "diff"; paintPlan(); }, { label: t("Show") }),
      h("button", { class: "btn", disabled: same, title: same ? t("The plan says this now") : t("The plan says this again; every version stays"),
        onclick: () => restorePlan(v.n) }, icon("rewind"), t("Go back to this version"))),
    P.compare ? h("div", { class: "plan-diff" }, same ? h("p", { class: "muted" }, t("The plan says the same now.")) : diffEl(v.text, P.data.text, "plan:" + v.n))
      : planDoc(v.text || "", false));
}

async function restorePlan(n) {
  try { P.data = await api("POST", `/api/plan/versions/${n}/restore`); } catch (e) { toast(e.message); return; }
  P.view = null;
  const v = P.data.versions[P.data.versions.length - 1];
  paintPlan();
  renderComposerState();
  toast(t("The plan is v{n} again (kept as v{m})", { n, m: v ? v.n : n }));
}

// the empty chat in this workspace: no suggestions to pick from, the user says it in their own words
function planWelcome() {
  return h("div", { class: "chat-welcome" },
    h("div", { class: "t" }, t("Tell Claude what's on your mind")),
    h("div", null, !ui.chat.available ? t("The claude command was not found. Install Claude Code and sign in first.")
      : t("It helps you see where you stand, the easiest way forward and how you're doing, and writes what you work out into the plan on the right, so it lasts beyond this chat.")));
}

// ---------------------------------------------------------------- the top bar
function renderPlanTools(el) {
  if (!P.data) { el.replaceChildren(); return; }
  const n = P.data.versions.length;
  el.replaceChildren(
    h("button", { class: "btn ghost" + (P.editing ? " on" : ""), id: "plan-edit", disabled: P.editing, title: t("Edit the plan's Markdown yourself"), onclick: startPlanEdit },
      icon("pen"), h("span", { class: "lbl" }, t("Edit"))),
    h("button", { class: "btn ghost" + (P.side ? " on" : ""), id: "plan-versions", disabled: !n || P.editing, "aria-pressed": String(P.side),
      title: n ? t("Every version of the plan, and why it changed") : t("No versions yet"),
      onclick: () => { P.side = !P.side; if (!P.side) P.view = null; paintPlan(); renderWsTools(); renderComposerState(); } },
    icon("history"), h("span", { class: "lbl" }, n ? t("Versions ({n})", { n }) : t("Versions"))),
    h("span", { class: "spacer" }));
}
