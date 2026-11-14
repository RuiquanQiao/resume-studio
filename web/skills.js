// ================================================================ skills (resume/skills.py)
// An entry's "Tech / keywords" (a list section's items) are skills: chips, each an instance of one skill of the
// catalog (S.skills), matched without case or a trailing version ("Next.js 16" is Next.js). A chip shows how well
// the user really has the skill (the fact layer: never on the resume) and opens a panel to set it, to accept the
// AI's reading of the notes, and, on a list line in a subset, to show or hide it there. The line in a subset shows
// only the skills its chosen entries back (user, 2026-11-14: "专业技能应该写有经历支撑的"); the rest stay as chips,
// dimmed, one click from showing anyway. What the resume prints is unchanged: the names, as text.

// Four levels named as job descriptions name them (精通 / 熟练 / 熟悉 / 了解); what each really means, how an
// interview or test on the skill would go, comes up on hover and is what the job match counts (user, 2026-11-15).
// Written for anyone: nothing of one user's own case. resume/skills.py LEVELS.
const SKILL_LEVELS = ["expert", "strong", "working", "none"];
const skillLevelName = (lv) => ({ expert: t("Expert"), strong: t("Proficient"), working: t("Familiar"), none: t("Basic") })[lv] || "";
const skillLevelMeans = (lv) => ({
  expert: t("Holds up however deep the examination goes: principles, mechanisms and details known thoroughly; the level of a senior practitioner"),
  strong: t("Holds up under a normal examination: common tasks done alone without references, routine interview questions and live tasks handled; the level of a junior practitioner ready to work alone"),
  working: t("Does not hold up under examination: tasks done with references open; without them, or when questioned further, the gaps show; the level of a student who barely passed the course"),
  none: t("Not started yet: knows only what it is for and can mention it, but cannot answer a follow-up question") })[lv] || "";

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
  const sug = skillSuggest(e, input, box, (name) => { input.value = name; add(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.isComposing) return;
    if (sug.keys(ev)) return;
    if (ev.key === "Enter") { ev.preventDefault(); add(); sug.paint(); }
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
    sug.paint();
  });
  // leaving the field keeps what was typed there but makes nothing: it may be half a search (Enter or a comma adds)
  input.addEventListener("blur", () => sug.hide());
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

// typing in the field searches the skills already in the catalog (by name or alias); Enter takes the highlighted
// one, or, when none matches, makes a new one of what was typed. ↑ ↓ move, Esc closes, a click picks.
function skillSuggest(e, input, field, pick) {
  const list = h("div", { class: "float listbox skx-suggest in", role: "listbox", id: "skx-sug-" + Math.random().toString(36).slice(2, 8), hidden: true });
  let opts = [], hl = -1;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("autocomplete", "off");
  const place = () => {
    const r = field.getBoundingClientRect();
    const below = innerHeight - r.bottom - 8, above = r.top - 8;
    const up = below < 180 && above > below;
    list.dataset.side = up ? "top" : "bottom";
    Object.assign(list.style, { left: Math.max(8, r.left) + "px", width: Math.min(r.width, innerWidth - 16) + "px",
      maxHeight: Math.max(120, Math.min(300, up ? above : below)) + "px",
      top: up ? "" : r.bottom + 4 + "px", bottom: up ? innerHeight - r.top + 4 + "px" : "" });
  };
  const hide = () => {
    if (list.hidden) return;
    list.hidden = true; list.remove(); input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
    removeEventListener("scroll", onScroll, true); removeEventListener("resize", hide);
  };
  const onScroll = (ev) => { if (!list.contains(ev.target)) place(); };
  const mark = () => {
    list.querySelectorAll(".lb-opt").forEach((b, i) => { b.classList.toggle("sel-hl", i === hl); b.setAttribute("aria-selected", String(i === hl)); });
    const cur = list.querySelectorAll(".lb-opt")[hl];
    if (cur) { input.setAttribute("aria-activedescendant", cur.id); cur.scrollIntoView({ block: "nearest" }); }
  };
  const paint = () => {
    if (document.activeElement !== input) { hide(); return; }
    const typed = splitSkills(input.value).pop() || "";
    const q = skillKey(typed);
    const have = new Set(e.tech.map((x) => skillRec(x).key));
    const score = (r) => {
      const names = [r.name, ...(r.aliases || [])].map(skillKey);
      if (!q) return 3;
      if (names.includes(q)) return 0;
      if (names.some((n) => n.startsWith(q))) return 1;
      if (names.some((n) => n.includes(q))) return 2;
      return -1;
    };
    const hits = ((S && S.skills) || []).filter((r) => !have.has(r.key)).map((r) => [score(r), r]).filter(([s]) => s >= 0)
      .sort((a, b) => a[0] - b[0] || (b[1].used_in || []).length - (a[1].used_in || []).length || a[1].name.localeCompare(b[1].name))
      .slice(0, 8).map(([, r]) => r);
    const exact = q && (hits.some((r) => r.key === q) || have.has(q));
    opts = [...hits.map((r) => ({ name: r.name, rec: r })), ...(q && !exact ? [{ name: typed.trim(), make: true }] : [])];
    if (!opts.length) { hide(); return; }
    list.replaceChildren(...opts.map((o, i) => h("button", { type: "button", role: "option", class: "lb-opt" + (o.make ? " skx-make" : ""), id: `${list.id}-${i}`, tabindex: "-1",
      onmousedown: (ev) => ev.preventDefault(), onclick: () => { pick(o.name); paint(); } },
      o.make ? [icon("plus"), h("span", { class: "lb-main" }, h("span", { class: "lb-label" }, t("Create “{name}”", { name: o.name })))]
        : [h("span", { class: "lb-main" }, h("span", { class: "lb-label" }, o.rec.name)),
          h("span", { class: "lb-hint" }, [skillLevelName(o.rec.level), (o.rec.used_in || []).length ? t("in {n} entry", { n: o.rec.used_in.length }, "in {n} entries") : ""].filter(Boolean).join(t(", ")))])));
    // a typed name already in the catalog, or the only thing left to pick: Enter takes the first; else what was typed
    hl = !q ? -1 : 0;
    if (list.hidden) {
      list.hidden = false; document.body.append(list); input.setAttribute("aria-expanded", "true");
      addEventListener("scroll", onScroll, true); addEventListener("resize", hide);
    }
    place(); mark();
  };
  const keys = (ev) => {
    if (list.hidden) { if (ev.key === "ArrowDown") { ev.preventDefault(); paint(); return true; } return false; }
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); hl = (hl + (ev.key === "ArrowDown" ? 1 : -1) + opts.length + 1) % (opts.length + 1); if (hl === opts.length) hl = -1; mark(); return true; }
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); hide(); return true; }
    if (ev.key === "Enter" && hl >= 0 && opts[hl]) { ev.preventDefault(); pick(opts[hl].name); paint(); return true; }
    if (ev.key === "Tab") hide();
    return false;
  };
  input.addEventListener("input", paint);
  input.addEventListener("focus", paint);
  return { paint, hide, keys };
}

function skillChip(e, i, box) {
  const item = e.tech[i];
  const r = skillRec(item);
  const shown = skillShown(e, item);
  const off = shown === "hidden" || shown === "unbacked";
  const lv = r.level;
  const tip = [r.name !== display(item) ? t("Counts as {name}", { name: r.name }) : "",
    lv ? t("How well you have it: {level}", { level: skillLevelName(lv) }) + "\n" + skillLevelMeans(lv) : t("How well you have it is not set yet"),
    r.suggest ? t("The AI suggests: {level}", { level: skillLevelName(r.suggest.level) }) : "",
    shown === "unbacked" ? t("Not on this resume: no entry it picks uses it") : shown === "hidden" ? t("Hidden on this resume") : ""].filter(Boolean).join("\n");
  const chip = h("span", { class: "skx-chip" + (lv ? " lv-" + lv : "") + (r.suggest ? " sug" : "") + (off ? " off" : ""), tabindex: "0", role: "button",
    "data-i": i, "aria-haspopup": "dialog", title: tip,
    onclick: (ev) => { if (chip.skxDragged) { chip.skxDragged = false; return; } if (!ev.target.closest(".skx-x")) openSkillPanel(chip, e, i, box); },
    onkeydown: (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openSkillPanel(chip, e, i, box); }
      else if (ev.key === "Backspace" || ev.key === "Delete") { ev.preventDefault(); removeSkill(e, i, box, true); }
      else if (ev.key === "ArrowLeft" && ev.altKey) { ev.preventDefault(); moveSkill(e, i, i - 1, box); }
      else if (ev.key === "ArrowRight" && ev.altKey) { ev.preventDefault(); moveSkill(e, i, i + 1, box); }
    },
    onpointerdown: (ev) => skillDrag(ev, chip, e, i, box) },
  h("span", { class: "skx-name" }, display(item)),
  lv ? h("span", { class: "skx-lv" }, skillLevelName(lv)) : null,
  r.suggest ? h("span", { class: "skx-dot", "aria-hidden": "true" }) : null,
  h("button", { type: "button", class: "skx-x", tabindex: "-1", "aria-label": t("Remove {name}", { name: display(item) }), onclick: () => removeSkill(e, i, box) }, icon("x")));
  return chip;
}

// press a chip and move it: it lifts under the pointer, a line shows where it lands among the others, and letting go
// puts it there (Esc puts it back). A press without a move is a click: the chip's panel opens.
function skillDrag(ev, chip, e, from, box) {
  if (ev.button !== 0 || ev.target.closest(".skx-x")) return;
  const x0 = ev.clientX, y0 = ev.clientY;
  let ghost = null, to = null, mark = null;
  const others = () => [...box.querySelectorAll(".skx-chip")].filter((c) => c !== chip);
  const unmark = () => { if (mark) mark.classList.remove("drop-before", "drop-after"); mark = null; };
  const where = (x, y) => {
    const list = others();
    if (!list.length) return null;
    const rects = list.map((c) => [c, c.getBoundingClientRect()]);
    let row = rects.filter(([, r]) => y >= r.top - 3 && y <= r.bottom + 3);
    if (!row.length) {   // between or past the rows: the nearest row
      const near = rects.reduce((a, b) => (Math.abs((b[1].top + b[1].bottom) / 2 - y) < Math.abs((a[1].top + a[1].bottom) / 2 - y) ? b : a));
      row = rects.filter(([, r]) => r.top === near[1].top);
    }
    const before = row.find(([, r]) => x < r.left + r.width / 2);
    const [c, side] = before ? [before[0], "before"] : [row[row.length - 1][0], "after"];
    const at = +c.dataset.i + (side === "after" ? 1 : 0);
    return { c, side, to: at > from ? at - 1 : at };
  };
  const move = (m) => {
    if (!ghost) {
      if (Math.hypot(m.clientX - x0, m.clientY - y0) < 5) return;
      closeFloat();
      const r = chip.getBoundingClientRect();
      ghost = chip.cloneNode(true);
      ghost.classList.add("skx-ghost");
      ghost.style.width = r.width + "px";
      ghost.dx = x0 - r.left; ghost.dy = y0 - r.top;
      document.body.append(ghost);
      chip.classList.add("dragging");
      document.body.classList.add("skx-dragging");
    }
    ghost.style.transform = `translate(${m.clientX - ghost.dx}px, ${m.clientY - ghost.dy}px)`;
    const w = where(m.clientX, m.clientY);
    unmark();
    to = w ? w.to : null;
    if (w && w.to !== from) { mark = w.c; mark.classList.add(w.side === "before" ? "drop-before" : "drop-after"); }
  };
  const end = (commit) => {
    removeEventListener("pointermove", move);
    removeEventListener("pointerup", up);
    removeEventListener("keydown", key, true);
    unmark();
    if (!ghost) return;
    ghost.remove();
    chip.classList.remove("dragging");
    document.body.classList.remove("skx-dragging");
    chip.skxDragged = true;   // the click that ends a drag opens nothing
    setTimeout(() => { chip.skxDragged = false; }, 0);
    if (commit && to != null && to !== from) moveSkill(e, from, to, box);
  };
  const up = () => end(true);
  const key = (k) => { if (k.key === "Escape") { k.preventDefault(); k.stopPropagation(); end(false); } };
  addEventListener("pointermove", move);
  addEventListener("pointerup", up);
  addEventListener("keydown", key, true);
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
// Its name as the resume writes it, how well the user has it (four levels), the AI's reading waiting to be accepted,
// then the two things a skill is tied to, each a list to tick: the entries that use it, and (on a skills line) the
// resumes that show it. Every tick saves at once and the panel stays open.
function openSkillPanel(anchor, e, i, box) {
  if (Float.cur && Float.cur.anchor === anchor) { closeFloat(); return; }
  const own = ui.eds.get("entry:" + e.id);   // what the panel changes saves with this entry, however it was opened
  if (own && ui.ed !== own) activate(own);
  const item = e.tech[i];
  const L = ui.editLang;
  const typed = () => getT(e.tech[i], L) || display(e.tech[i]);
  const panel = h("div", { class: "skx-pop", role: "dialog", "aria-label": skillRec(item).name });
  const name = h("input", { class: "skx-p-name", value: typed(), spellcheck: "false", "aria-label": t("As the resume writes it"),
    onkeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); closeFloat(); anchor.focus(); } },
    oninput: () => { const val = name.value.trim(); if (!val) return; e.tech[i] = item && typeof item === "object" ? setT(item, L, val) : val; touch(); } });
  const call = async (method, url, data) => {
    try {
      applyState(await api(method, url, data));
      paintSkills();
      // the chip the panel hangs from is kept (the panel would close with it): it takes the new look in place
      if (anchor.isConnected && e.tech[i] !== undefined) {
        const fresh = skillChip(e, i, box);
        anchor.className = fresh.className;
        anchor.replaceChildren(...fresh.childNodes);
        setTip(anchor, fresh.dataset.tip || "");
      }
      paint();
    } catch (err) { toast(t("Save failed: {error}", { error: err.message })); }
  };
  const option = (on, label, hint, onclick, extra = "") => h("button", { type: "button", role: "checkbox", "aria-checked": String(on), class: "skx-opt" + (on ? " on" : "") + extra, onclick },
    h("span", { class: "check" + (on ? " on" : ""), "aria-hidden": "true" }),
    h("span", { class: "skx-opt-t" }, label), hint ? h("span", { class: "skx-opt-h" }, hint) : null);
  const paint = () => {
    const r = skillRec(e.tech[i] ?? item);
    // the names show; what a level really means comes up under the row for the one under the pointer / keyboard focus
    const means = h("p", { class: "skx-lv-means" });
    const say = (lv) => { means.textContent = lv ? skillLevelMeans(lv) : t("Hover over a level to see what it means"); means.classList.toggle("on", !!lv); };
    say("");
    const seg = h("div", { class: "skx-levels", role: "radiogroup", "aria-label": t("How well you have it"), onmouseleave: () => say("") },
      SKILL_LEVELS.map((lv) => h("button", { type: "button", role: "radio", "aria-checked": String(r.level === lv), class: "skx-lvl" + (r.level === lv ? " on lv-" + lv : ""),
        onmouseenter: () => say(lv), onfocus: (ev) => { if (ev.target.matches(":focus-visible")) say(lv); }, onblur: () => say(""),
        onclick: () => { if (r.level !== lv) call("PUT", "/api/skills/level", { name: r.name, level: lv }); } }, skillLevelName(lv))));
    // the entries that name it: ticking one adds it to that entry's tech, unticking takes it out (this one too)
    const projects = entries().filter((x) => !isListEntry(x));
    const used = new Set(r.used_in);
    const uses = projects.map((x) => option(used.has(x.id), display(x.title) || t("(untitled)"), display((sectionById(x.section) || {}).title),
      () => (x.id === e.id ? (closeFloat(), removeSkill(e, i, box)) : call("POST", "/api/skills/use", { name: r.name, entry: x.id, on: !used.has(x.id) }))));
    // on a skills line: the resumes that show it (by the entries they pick, or by the user's own choice)
    const resumes = isListEntry(e) ? subsets().filter((v) => v.id !== ALL).map((v) => {
      const st = skillShownIn(v, r);
      return option(st.on, subsetName(v), st.why, () => call("POST", "/api/skills/show", { name: r.name, subset: v.id, on: !st.on }));
    }) : [];
    panel.replaceChildren(...[
      h("div", { class: "skx-p-head" }, name,
        r.name !== typed() ? h("p", { class: "skx-p-sub" }, t("Counts as the skill {name} everywhere", { name: r.name })) : null),
      h("section", { class: "skx-p-sec" }, h("h3", { class: "skx-p-label" }, t("How well you have it")), seg, means,
        r.level && r.by === "ai" ? h("p", { class: "skx-p-sub" }, t("From the AI's reading of your fact layer"), r.why ? ": " + r.why : "") : null),
      r.suggest ? h("div", { class: "skx-p-sug" },
        h("span", { class: "skx-p-sug-t" }, icon("sparkle"), t("The AI suggests: {level}", { level: skillLevelName(r.suggest.level) })),
        r.suggest.why ? h("span", { class: "skx-p-sug-why" }, r.suggest.why) : null,
        h("span", { class: "skx-p-acts" },
          h("button", { type: "button", class: "btn sm primary", onclick: () => call("POST", "/api/skills/settle", { names: [r.name], accept: true }) }, t("Accept")),
          h("button", { type: "button", class: "btn sm ghost", onclick: () => call("POST", "/api/skills/settle", { names: [r.name], accept: false }) }, t("Dismiss")))) : null,
      h("section", { class: "skx-p-sec" }, h("h3", { class: "skx-p-label" }, t("Used in")),
        projects.length ? h("div", { class: "skx-opts" }, uses) : h("p", { class: "skx-p-sub" }, t("No entry uses it yet"))),
      resumes.length ? h("section", { class: "skx-p-sec" }, h("h3", { class: "skx-p-label" }, t("Shown on")), h("div", { class: "skx-opts" }, resumes)) : null,
      h("div", { class: "skx-p-foot" },
        h("button", { type: "button", class: "btn sm ghost danger", onclick: () => { closeFloat(); removeSkill(e, i, box); } }, icon("trash"),
          isListEntry(e) ? t("Remove from this line") : t("Remove from this entry")))].filter(Boolean));
  };
  paint();
  openFloat(anchor, panel, { minWidth: 340, onClose: () => box.skxPaint(), focus: panel.querySelector(".skx-lvl.on") || panel.querySelector(".skx-lvl") });
}

// whether a skill shows on a subset's skills lines, and why when it isn't simply the entries it picks
function skillShownIn(v, r) {
  const has = (list) => (list || []).some((x) => skillRec(x).key === r.key);
  const picked = new Set(v.entries || []);
  const rule = r.used_in.some((id) => picked.has(id)) || !r.used_in.length;
  if (has(v.hide_skills)) return { on: false, why: t("Hidden by you") };
  if (has(v.show_skills)) return { on: true, why: rule ? "" : t("Shown by you") };
  return { on: rule, why: rule ? "" : t("No entry it picks uses it") };
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
