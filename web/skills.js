// ================================================================ skills (resume/skills.py)
// An entry's "Tech / keywords" (a list section's items) are skills: chips, each an instance of one skill of the
// catalog (S.skills), matched without case or a trailing version ("Next.js 16" is Next.js). A chip shows how well
// the user really has the skill (the fact layer: never on the resume) and opens a panel to set it, to accept the
// AI's reading of the notes, and, on a list line in a subset, to show or hide it there. The line in a subset shows
// only the skills its chosen entries back (user, 2026-11-14: "专业技能应该写有经历支撑的"); the rest stay as chips,
// dimmed, one click from showing anyway. What the resume prints is unchanged: the names, as text.

const SKILL_LEVELS = ["strong", "working", "basic", "none"];
const skillLevelName = (lv) => ({ strong: t("Proficient"), working: t("Can use"), basic: t("Familiar"), none: t("Can't use yet") })[lv] || "";
const skillLevelHint = (lv) => ({
  strong: t("You could explain it and build on it without help"),
  working: t("You use it with the documentation at hand"),
  basic: t("You have used or followed it, but couldn't build with it alone"),
  none: t("It is in a project, but AI or someone else wrote that part"),
})[lv] || "";

function skillKey(name) {
  const s = String(name ?? "").normalize("NFKC").split(/\s+/).filter(Boolean).join(" ");
  const base = s.replace(/\s+v?\d+(?:\.\d+)*[a-z]?$/i, "");
  return (base || s).toLowerCase();
}
const skillTexts = (item) => (item && typeof item === "object" ? Object.values(item).filter(Boolean).map(String) : item ? [String(item)] : []);

// the catalog as the window last read it: key -> record, and alias key -> record key
let skillIdx = { of: null, aliases: new Map(), byKey: new Map() };
function skillIndex() {
  if (skillIdx.of === S) return skillIdx;
  const byKey = new Map(), aliases = new Map();
  for (const r of (S && S.skills) || []) {
    byKey.set(r.key, r);
    for (const a of r.aliases || []) if (!aliases.has(skillKey(a))) aliases.set(skillKey(a), r.key);
  }
  skillIdx = { of: S, aliases, byKey };
  return skillIdx;
}
function skillRec(item) {
  const idx = skillIndex();
  const name = display(item);
  const k = idx.aliases.get(skillKey(name)) || skillKey(name);
  return idx.byKey.get(k) || { key: k, name: name.replace(/\s+v?\d+(?:\.\d+)*[a-z]?$/i, "") || name, level: "", used_in: [], lines: [], suggest: null };
}
const skillName = (item) => skillTexts(item)[0] || "";

// on a list line in a subset: "" shown by the rule, "pinned" shown by the user, "hidden" hidden by the user,
// "unbacked" left out: other entries of the library use it, none this subset picks
function skillShown(e, item) {
  const v = subset();
  if (!v || v.id === ALL || !isListEntry(e)) return "";
  const r = skillRec(item);
  const has = (list) => (list || []).some((x) => skillRec(x).key === r.key);
  if (has(v.hide_skills)) return "hidden";
  if (has(v.show_skills)) return "pinned";
  const picked = new Set(v.entries || []);
  if (r.used_in.some((id) => picked.has(id))) return "";
  return r.used_in.length ? "unbacked" : "";
}
const isListEntry = (e) => { const s = sectionById(e.section); return !!(s && s.kind === "list"); };

// "Python (NumPy, SciPy), Docker" -> two items: a comma inside brackets stays
function splitSkills(text) {
  const out = [];
  let cur = "", depth = 0;
  for (const ch of String(text)) {
    if ("(（[【".includes(ch)) depth++;
    else if (")）]】".includes(ch)) depth = Math.max(0, depth - 1);
    if (depth === 0 && ",，;；\n".includes(ch)) { if (cur.trim()) out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

// where the last comma outside brackets is (-1: none)
function lastSeparator(text) {
  let depth = 0, at = -1;
  const s = String(text);
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if ("(（[【".includes(ch)) depth++;
    else if (")）]】".includes(ch)) depth = Math.max(0, depth - 1);
    else if (depth === 0 && ",，;；\n".includes(ch)) at = i;
  }
  return at;
}

// ---- the field -------------------------------------------------------------------
function techField(e) {
  const list = isListEntry(e);
  e.tech = e.tech || [];
  const chips = h("span", { class: "skx-chips" });
  const input = h("input", { class: "skx-input", spellcheck: "false", "aria-label": list ? t("Add an item") : t("Add a skill"),
    placeholder: e.tech.length ? t("Add…") : list ? t("Type an item, then Enter") : t("Type a skill, then Enter") });
  const box = h("div", { class: "skx-field", "data-key": "tech", onclick: (ev) => { if (ev.target === box) input.focus(); } }, chips, input);
  const add = () => {
    const items = splitSkills(input.value);
    if (!items.length) return false;
    const have = new Set(e.tech.map((x) => display(x)));
    const added = [];
    for (const x of items) if (!have.has(x)) { e.tech.push(ui.editLang && langs().length > 1 && e.tech.some((y) => y && typeof y === "object") ? setT("", ui.editLang, x) : x); have.add(x); added.push(x); }
    input.value = "";
    touch();
    // typed into a line while looking at a subset: it shows there, even when only entries this subset leaves out use it
    const pin = added.filter((x) => skillShown(e, x) === "unbacked");
    if (pin.length) mutateSubset((v) => { v.show_skills = [...(v.show_skills || []), ...pin.map((x) => skillRec(x).name)]; });
    box.skxPaint();
    return true;
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.isComposing) return;
    if (ev.key === "Enter") { ev.preventDefault(); add(); }
    else if (ev.key === "Backspace" && !input.value && e.tech.length) { ev.preventDefault(); const last = chips.lastElementChild; if (last) last.focus(); }
  });
  // a comma outside brackets (typed, pasted, or from an input method) ends an item; what follows it stays to type on
  input.addEventListener("input", (ev) => {
    if (ev.isComposing) return;
    const cut = lastSeparator(input.value);
    if (cut < 0) return;
    const rest = input.value.slice(cut + 1);
    input.value = input.value.slice(0, cut);
    add();
    input.value = rest.trimStart();
  });
  input.addEventListener("blur", () => { if (input.value.trim()) add(); });
  box.skxPaint = () => {
    // one of these chips has its panel open (its name may be being typed): they are drawn again when it closes
    if (Float.cur && Float.cur.panel.classList.contains("skx-pop") && chips.contains(Float.cur.anchor)) return;
    chips.replaceChildren(...e.tech.map((item, i) => skillChip(e, i, box)));
    input.placeholder = e.tech.length ? t("Add…") : list ? t("Type an item, then Enter") : t("Type a skill, then Enter");
  };
  box.skxPaint();
  return h("div", { class: "field" }, h("label", null, list ? t("Items") : t("Tech / keywords"),
    h("span", { class: "hint" }, t("Enter or a comma adds one; click one to set how well you have it"))), box);
}

function skillChip(e, i, box) {
  const item = e.tech[i];
  const r = skillRec(item);
  const shown = skillShown(e, item);
  const off = shown === "hidden" || shown === "unbacked";
  const lv = r.level;
  const tip = [r.name !== display(item) ? t("Counts as {name}", { name: r.name }) : "",
    lv ? t("How well you have it: {level}", { level: skillLevelName(lv) }) : t("How well you have it is not set yet"),
    r.suggest ? t("The AI suggests: {level}", { level: skillLevelName(r.suggest.level) }) : "",
    shown === "unbacked" ? t("Not on this resume: no entry it picks uses it") : shown === "hidden" ? t("Hidden on this resume") : ""].filter(Boolean).join("\n");
  const chip = h("span", { class: "skx-chip" + (lv ? " lv-" + lv : "") + (r.suggest ? " sug" : "") + (off ? " off" : ""), tabindex: "0", role: "button",
    draggable: "true", "data-i": i, "aria-haspopup": "dialog", title: tip,
    onclick: (ev) => { if (!ev.target.closest(".skx-x")) openSkillPanel(chip, e, i, box); },
    onkeydown: (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openSkillPanel(chip, e, i, box); }
      else if (ev.key === "Backspace" || ev.key === "Delete") { ev.preventDefault(); removeSkill(e, i, box, true); }
      else if (ev.key === "ArrowLeft" && ev.altKey) { ev.preventDefault(); moveSkill(e, i, i - 1, box); }
      else if (ev.key === "ArrowRight" && ev.altKey) { ev.preventDefault(); moveSkill(e, i, i + 1, box); }
    },
    ondragstart: (ev) => { ev.dataTransfer.setData("text/x-skill", JSON.stringify([e.id, i])); ev.dataTransfer.effectAllowed = "move"; chip.classList.add("dragging"); },
    ondragend: () => chip.classList.remove("dragging"),
    ondragover: (ev) => { if ([...ev.dataTransfer.types].includes("text/x-skill")) { ev.preventDefault(); chip.classList.add("drop"); } },
    ondragleave: () => chip.classList.remove("drop"),
    ondrop: (ev) => {
      chip.classList.remove("drop");
      let from;
      try { from = JSON.parse(ev.dataTransfer.getData("text/x-skill")); } catch { return; }
      if (!from || from[0] !== e.id) return;
      ev.preventDefault();
      moveSkill(e, from[1], i, box);
    } },
  h("span", { class: "skx-name" }, display(item)),
  lv ? h("span", { class: "skx-lv" }, skillLevelName(lv)) : null,
  r.suggest ? h("span", { class: "skx-dot", "aria-hidden": "true" }) : null,
  h("button", { type: "button", class: "skx-x", tabindex: "-1", "aria-label": t("Remove {name}", { name: display(item) }), onclick: () => removeSkill(e, i, box) }, icon("x")));
  return chip;
}

function removeSkill(e, i, box, keyboard = false) {
  e.tech.splice(i, 1);
  touch();
  box.skxPaint();
  if (keyboard) { const c = box.querySelectorAll(".skx-chip"); (c[Math.min(i, c.length - 1)] || box.querySelector(".skx-input")).focus(); }
}
function moveSkill(e, from, to, box) {
  if (to < 0 || to >= e.tech.length || from === to) return;
  e.tech = moved(e.tech, from, to);
  touch();
  box.skxPaint();
  const c = box.querySelectorAll(".skx-chip")[to];
  if (c) c.focus();
}

// ---- one skill's panel ----------------------------------------------------------
function openSkillPanel(anchor, e, i, box) {
  if (Float.cur && Float.cur.anchor === anchor) { closeFloat(); return; }
  const own = ui.eds.get("entry:" + e.id);   // what the panel changes saves with this entry, however it was opened
  if (own && ui.ed !== own) activate(own);
  const item = e.tech[i];
  const r = skillRec(item);
  const shown = skillShown(e, item);
  const v = subset();
  const L = ui.editLang;
  const name = h("input", { class: "skx-p-name", value: getT(item, L) || display(item), spellcheck: "false", "aria-label": t("As the resume writes it"),
    onkeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); closeFloat(); anchor.focus(); } },
    oninput: () => { const val = name.value.trim(); if (!val) return; e.tech[i] = item && typeof item === "object" ? setT(item, L, val) : val; touch(); } });
  const pick = async (lv) => {
    closeFloat();
    try {
      applyState(await api("PUT", "/api/skills/level", { name: r.name, level: lv === r.level ? "" : lv }));
      paintSkills();
    } catch (err) { toast(t("Save failed: {error}", { error: err.message })); }
  };
  const settle = async (accept) => {
    closeFloat();
    try { applyState(await api("POST", "/api/skills/settle", { names: [r.name], accept })); paintSkills(); } catch (err) { toast(err.message); }
  };
  const titles = (ids) => ids.map((id) => display((entryById(id) || {}).title) || id).join("、");
  const seg = h("div", { class: "seg skx-levels", role: "radiogroup", "aria-label": t("How well you have it") },
    SKILL_LEVELS.map((lv) => h("button", { type: "button", role: "radio", "aria-checked": String(r.level === lv), class: r.level === lv ? "on" : "",
      title: skillLevelHint(lv) + (r.level === lv ? "\n" + t("Click again to clear") : ""), onclick: () => pick(lv) }, skillLevelName(lv))));
  const picks = (fn) => async () => { closeFloat(); await mutateSubset(fn); paintSkills(); };
  const without = (list) => (list || []).filter((x) => skillRec(x).key !== r.key);
  const here = v && v.id !== ALL && isListEntry(e) && h("div", { class: "skx-p-row skx-p-here" },
    h("span", { class: "skx-p-k" }, t("On “{name}”", { name: subsetName(v) })),
    shown === "unbacked" ? [h("span", { class: "skx-p-v muted" }, t("Not shown: no entry it picks uses it ({entries})", { entries: titles(r.used_in) })),
      h("button", { type: "button", class: "btn sm", onclick: picks((x) => { x.show_skills = [...without(x.show_skills), r.name]; }) }, t("Show anyway"))]
      : shown === "hidden" ? [h("span", { class: "skx-p-v muted" }, t("Hidden by you")),
        h("button", { type: "button", class: "btn sm", onclick: picks((x) => { x.hide_skills = without(x.hide_skills); if (!x.hide_skills.length) delete x.hide_skills; }) }, t("Show it again"))]
      : [h("span", { class: "skx-p-v" }, shown === "pinned" ? t("Shown by you, though no entry it picks uses it") : t("Shown")),
        shown === "pinned" && h("button", { type: "button", class: "btn sm ghost", onclick: picks((x) => { x.show_skills = without(x.show_skills); if (!x.show_skills.length) delete x.show_skills; }) }, t("Follow the entries again")),
        h("button", { type: "button", class: "btn sm ghost", onclick: picks((x) => { x.show_skills = without(x.show_skills); if (!x.show_skills.length) delete x.show_skills; x.hide_skills = [...without(x.hide_skills), r.name]; }) }, t("Hide on this resume"))]);
  const panel = h("div", { class: "skx-pop", role: "dialog", "aria-label": r.name },
    h("div", { class: "skx-p-row" }, h("span", { class: "skx-p-k" }, t("As the resume writes it")), name),
    r.name !== (getT(item, L) || display(item)) && h("p", { class: "skx-p-note muted" }, t("Counts as the skill {name} everywhere", { name: r.name })),
    h("div", { class: "skx-p-row" }, h("span", { class: "skx-p-k" }, t("How well you have it")), seg),
    h("p", { class: "skx-p-note muted" }, r.level
      ? [r.by === "ai" ? t("From the AI's reading of your fact layer, which you accepted") : t("Set by you"), r.why ? " · " + r.why : ""]
      : t("Only you and the AI see this; it never appears on the resume. Matching roles use it to tell what you can really do.")),
    r.suggest && h("div", { class: "skx-p-sug" },
      h("span", { class: "skx-p-sug-t" }, icon("sparkle"), t("The AI suggests: {level}", { level: skillLevelName(r.suggest.level) })),
      r.suggest.why && h("span", { class: "skx-p-sug-why" }, r.suggest.why),
      h("span", { class: "skx-p-acts" },
        h("button", { type: "button", class: "btn sm primary", onclick: () => settle(true) }, t("Accept")),
        h("button", { type: "button", class: "btn sm ghost", onclick: () => settle(false) }, t("Dismiss")))),
    h("div", { class: "skx-p-row" }, h("span", { class: "skx-p-k" }, t("Used in")),
      h("span", { class: "skx-p-v" + (r.used_in.length ? "" : " muted") }, r.used_in.length ? titles(r.used_in) : t("No entry uses it yet"))),
    here,
    h("div", { class: "skx-p-foot" },
      h("button", { type: "button", class: "btn sm ghost", disabled: i === 0 || null, onclick: () => { closeFloat(); moveSkill(e, i, i - 1, box); } }, t("Move left")),
      h("button", { type: "button", class: "btn sm ghost", disabled: i === e.tech.length - 1 || null, onclick: () => { closeFloat(); moveSkill(e, i, i + 1, box); } }, t("Move right")),
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn sm ghost danger", onclick: () => { closeFloat(); removeSkill(e, i, box); } }, icon("trash"), isListEntry(e) ? t("Remove from this line") : t("Remove from this entry"))));
  openFloat(anchor, panel, { minWidth: 320, onClose: () => box.skxPaint(), focus: seg.querySelector(".on") || seg.firstChild });
}

// ---- above a list section: the levels, the AI's readings, the skills no line has ----
function skillStrip(sid) {
  const el = h("div", { class: "skx-strip", "data-sec": sid });
  el.skxPaint = () => el.replaceChildren(...skillStripBody(sid));
  el.skxPaint();
  return el;
}

function skillStripBody(sid) {
  const all = (S && S.skills) || [];
  const used = all.filter((r) => r.used_in.length || r.lines.length);
  const rated = used.filter((r) => r.level).length;
  const sugs = all.filter((r) => r.suggest);
  const changed = (S && S.skills_review) || [];
  const lines = entries().filter((e) => e.section === sid);
  const settleAll = async (accept) => { try { applyState(await api("POST", "/api/skills/settle", { names: [], accept })); paintSkills(); } catch (err) { toast(err.message); } };
  const read = h("button", { class: "btn sm" + (changed.length ? " primary" : ""), "data-ai": "skills", disabled: !ui.chat.available || null,
    title: ui.chat.available ? t("The AI reads the notes and suggests how well you have each skill; you accept or dismiss each") : missingAgentText(),
    onclick: () => readSkillLevels(!changed.length) }, icon("sparkle"),
    changed.length ? t("Let AI read the changed fact layer ({n})", { n: changed.length }) : t("Let AI read the whole fact layer again"));
  const out = [h("div", { class: "skx-s-head" },
    h("span", { class: "skx-s-t" }, t("How well you really have them")),
    h("span", { class: "skx-s-n muted" }, t("{n} of {all} skills set", { n: rated, all: used.length })),
    h("span", { class: "spacer" }), read)];
  if (changed.length && !sugs.length) out.push(h("p", { class: "skx-s-note muted" },
    t("The fact layer of {n} entries changed since the AI last read it for your skill levels.", { n: changed.length })));
  if (sugs.length) out.push(h("div", { class: "skx-sugs" },
    h("div", { class: "skx-s-head" }, h("span", { class: "skx-s-t" }, t("The AI's readings ({n})", { n: sugs.length })), h("span", { class: "spacer" }),
      h("button", { class: "btn sm primary", onclick: () => settleAll(true) }, t("Accept all")),
      h("button", { class: "btn sm ghost", onclick: () => settleAll(false) }, t("Dismiss all"))),
    sugs.map((r) => h("div", { class: "skx-sug" },
      h("span", { class: "skx-sug-n" }, r.name),
      h("span", { class: "skx-sug-lv" }, (r.level ? skillLevelName(r.level) + " → " : "") + skillLevelName(r.suggest.level)),
      h("span", { class: "skx-sug-why muted" }, r.suggest.why || ""),
      h("button", { class: "btn sm", onclick: async () => { try { applyState(await api("POST", "/api/skills/settle", { names: [r.name], accept: true })); paintSkills(); } catch (err) { toast(err.message); } } }, t("Accept")),
      h("button", { class: "btn sm ghost", onclick: async () => { try { applyState(await api("POST", "/api/skills/settle", { names: [r.name], accept: false })); paintSkills(); } catch (err) { toast(err.message); } } }, t("Dismiss"))))));
  const loose = all.filter((r) => r.used_in.length && !r.lines.length);
  if (loose.length && lines.length) {
    const open = ui.skxPool === sid;
    const shownLoose = open ? loose : loose.slice(0, 12);
    out.push(h("div", { class: "skx-pool" },
      h("span", { class: "skx-s-k muted" }, t("Used in entries, on no line here:")),
      shownLoose.map((r) => h("button", { type: "button", class: "skx-chip pool" + (r.level ? " lv-" + r.level : ""), title: t("Add {name} to a line", { name: r.name }),
        onclick: (ev) => openMenu(ev.currentTarget, [{ header: t("Add {name} to", { name: r.name }) },
          ...lines.map((line) => ({ label: display(line.title) || t("(untitled)"), icon: "plus", onClick: () => addToLine(line.id, r.name) }))]) },
      h("span", { class: "skx-name" }, r.name), r.level ? h("span", { class: "skx-lv" }, skillLevelName(r.level)) : null)),
      loose.length > 12 && h("button", { type: "button", class: "btn sm ghost", onclick: () => { ui.skxPool = open ? null : sid; paintSkills(); } },
        open ? t("Fewer") : t("{n} more", { n: loose.length - 12 }))));
  }
  return out;
}

function addToLine(id, name) {
  const ed = edFor("entry", id);
  ed.draft.tech = [...(ed.draft.tech || []), name];
  ed.dirty = true;
  ed.seq++;
  saveItem(ed);
  paintItem(ed);
  // added while looking at a subset: it shows there, like one typed into the line
  if (skillShown(ed.draft, name) === "unbacked") mutateSubset((v) => { v.show_skills = [...(v.show_skills || []), skillRec(name).name]; });
  paintSkills();
}

// the AI reads the notes for the levels; the button spins until the chat ends, then says what it found
async function readSkillLevels(everything = false) {
  const before = ((S && S.skills) || []).filter((r) => r.suggest).length;
  const check = async () => {
    const s = await api("GET", "/api/state");
    applyState(s);
    syncEditor();
    paintSkills();
    const n = (s.skills || []).filter((r) => r.suggest).length;
    return { ok: true, say: n > before ? t("The AI has {n} readings of your skill levels: accept or dismiss each above Skills", { n }) : t("The AI found nothing new about your skill levels in the fact layer") };
  };
  await aiRun("skills", () => sendChat("", { action: "skills", fresh: true, all: everything }), check);
}

// the catalog or the subset changed: every chip field and strip on screen paints again (the field being typed in
// keeps its text: only the chips are redrawn)
let skillSig = "";
function paintSkills(force = true) {
  const v = subset();
  const sig = JSON.stringify([S && S.skills, S && S.skills_review, v && [v.id, v.entries, v.show_skills, v.hide_skills], ui.skxPool]);
  if (!force && sig === skillSig) return;
  skillSig = sig;
  for (const el of document.querySelectorAll(".skx-field, .skx-strip")) if (el.skxPaint) el.skxPaint();
  // a skill's panel whose chip was just drawn again would float where nothing is
  if (Float.cur && Float.cur.panel.classList.contains("skx-pop") && !Float.cur.anchor.isConnected) closeFloat();
}
