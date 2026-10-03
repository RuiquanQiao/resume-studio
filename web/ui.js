// Resume Studio UI kit: the controls the app uses instead of the platform's own.
// Native <select>, tooltips, colour picker and right-click menu look like Windows
// dialogs inside the page, so each one is rebuilt here on the app's tokens.
// Loaded before app.js; everything here is global on purpose (no build step).
"use strict";

const $ = (s, root = document) => root.querySelector(s);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let tip = null;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = v;
    else if (k === "title") tip = v;          // never a native tooltip: see setTip
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === true) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  if (tip) setTip(el, tip);
  return el;
}

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3.2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  chev: '<path d="m7 10 5 5 5-5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  sparkle: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  zoomIn: '<circle cx="11" cy="11" r="7"/><path d="M11 8v6M8 11h6M20 20l-4-4"/>',
  zoomOut: '<circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-4-4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  folderOpen: '<path d="M3 8V6a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v2"/><path d="M3 8h17.5a1 1 0 0 1 1 1.2l-1.6 8A1 1 0 0 1 19 18H4.6a1 1 0 0 1-1-.8L2.1 9.2A1 1 0 0 1 3 8z"/>',
  git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 8.2v7.6M18 11.2c0 3.5-4 3.3-10.4 5.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v3h3"/><path d="M4 13a8 8 0 0 0 14.6 4.5M20 20v-3h-3"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  translate: '<path d="M4 5h8M8 3v2M5.5 5c.8 3 3 5.5 6 7M10.5 5c-.8 3-3 5.5-6 7"/><path d="m13 21 4-9 4 9M14.4 18h5.2"/>',
  compress: '<path d="M4 9h16M4 15h10"/><path d="m17 13 3 2-3 2"/>',
  scissors: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M8 7.5 20 17M8 16.5 20 7"/>',
  clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4.5V3h6v1.5"/>',
  selectAll: '<rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 2.4"/><path d="M8 12h8"/>',
  pen: '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3z"/>',
  chevRight: '<path d="m10 7 5 5-5 5"/>',
  clip: '<path d="m20 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
  fork: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7v2a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 12v5"/>',
  shield: '<path d="M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
  hand: '<path d="M8 13V6a1.5 1.5 0 0 1 3 0v6M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-.6a6 6 0 0 1-4.6-2.2L3.5 15a1.6 1.6 0 0 1 2.4-2.1L8 15"/>',
  plan: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1 1 2-2M3.5 12l1 1 2-2"/><circle cx="5" cy="18" r="1.2"/>',
  bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

// ================================================================ tooltips
// No element keeps a native `title`: it becomes data-tip (and the accessible name of
// icon-only buttons), and one styled tooltip follows the pointer's target.
function setTip(el, text) {
  if (text) el.setAttribute("data-tip", text); else el.removeAttribute("data-tip");
  // icon-only buttons are named by their tooltip, unless they already carry their own name
  const iconOnly = !el.textContent.trim() && /^(BUTTON|A)$/.test(el.tagName) && !el.hasAttribute("aria-label");
  if (iconOnly || el.hasAttribute("data-tip-label")) {
    if (text) { el.setAttribute("aria-label", text); el.setAttribute("data-tip-label", ""); }
  }
  if (Tip.target === el) { if (text) Tip.el.textContent = text; else hideTip(); }
}
function adoptTitles(root) {
  if (root.nodeType !== 1) return;
  if (root.hasAttribute("title")) { const t = root.getAttribute("title"); root.removeAttribute("title"); setTip(root, t); }
  for (const el of root.querySelectorAll("[title]")) { const t = el.getAttribute("title"); el.removeAttribute("title"); setTip(el, t); }
}
const Tip = { el: null, target: null, timer: 0 };
function hideTip() {
  clearTimeout(Tip.timer);
  Tip.target = null;
  if (Tip.el) Tip.el.classList.remove("show");
}
function showTip(t) {
  const text = t.getAttribute("data-tip");
  if (!text || !t.isConnected || Float.cur && Float.cur.anchor === t) return;
  if (!Tip.el) { Tip.el = h("div", { class: "tip", role: "tooltip" }); document.body.append(Tip.el); }
  const el = Tip.el;
  el.textContent = text;
  el.style.left = "0px"; el.style.top = "0px";
  el.classList.add("show");
  const r = t.getBoundingClientRect(), w = el.offsetWidth, ht = el.offsetHeight;
  let top = r.bottom + 7, side = "bottom";
  if (top + ht > innerHeight - 6) { top = r.top - ht - 7; side = "top"; }
  const left = Math.max(6, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 6));
  el.style.left = left + "px"; el.style.top = top + "px"; el.dataset.side = side;
}
function initTips() {
  adoptTitles(document.body);
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "attributes") { if (m.target.hasAttribute("title")) adoptTitles(m.target); }
      else for (const n of m.addedNodes) adoptTitles(n);
    }
  }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["title"] });
  // disabled buttons get no pointer events in Chromium, so track the pointer position instead
  document.addEventListener("pointermove", (e) => {
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const t = under && under.closest("[data-tip]");
    if (t === Tip.target) return;
    hideTip();
    if (!t) return;
    Tip.target = t;
    Tip.timer = setTimeout(() => showTip(t), 420);
  }, { passive: true });
  for (const ev of ["pointerdown", "keydown", "wheel"]) document.addEventListener(ev, hideTip, true);
  document.addEventListener("scroll", hideTip, true);
  document.documentElement.addEventListener("pointerleave", hideTip);
}

// ================================================================ floating panels
// One floating panel at a time (menus, listboxes, pickers). It sits on the body,
// flips above its anchor when there is no room below and closes on outside click,
// Escape, scrolling elsewhere or resize.
const Float = { cur: null };
function openFloat(anchor, panel, { place = "bottom", align = "start", minWidth = 0, onClose = null, focus = null } = {}) {
  closeFloat();
  panel.classList.add("float");
  document.body.append(panel);
  const f = { anchor, panel, place, align, minWidth, onClose };
  Float.cur = f;
  if (anchor.setAttribute) anchor.setAttribute("aria-expanded", "true");
  hideTip();
  placeFloat(f);
  requestAnimationFrame(() => panel.classList.add("in"));
  if (focus) focus.focus({ preventScroll: true });   // at once: the next key press must land in the panel
  return f;
}
function placeFloat(f) {
  const p = f.panel, r = f.anchor.getBoundingClientRect();
  const W = innerWidth, H = innerHeight, gap = 5, pad = 8;
  p.style.minWidth = (f.minWidth === "anchor" ? r.width : f.minWidth || 0) + "px";
  p.style.maxHeight = "";
  const roomBelow = H - r.bottom - gap - pad, roomAbove = r.top - gap - pad;
  const natural = p.scrollHeight;
  let below = f.place !== "top";
  if (below && natural > roomBelow && roomAbove > roomBelow) below = false;
  if (!below && natural > roomAbove && roomBelow > roomAbove) below = true;
  p.style.maxHeight = Math.max(120, below ? roomBelow : roomAbove) + "px";
  const ph = p.offsetHeight, pw = p.offsetWidth;
  const top = below ? r.bottom + gap : r.top - gap - ph;
  let left = f.align === "end" ? r.right - pw : f.align === "center" ? r.left + r.width / 2 - pw / 2 : r.left;
  left = Math.max(pad, Math.min(left, W - pw - pad));
  p.style.top = Math.max(pad, top) + "px";
  p.style.left = left + "px";
  p.dataset.side = below ? "bottom" : "top";
}
function closeFloat() {
  const f = Float.cur;
  if (!f) return;
  Float.cur = null;
  if (f.anchor.setAttribute) f.anchor.setAttribute("aria-expanded", "false");
  f.panel.remove();
  if (f.onClose) f.onClose();
}
function pointAnchor(x, y) {
  return { getBoundingClientRect: () => new DOMRect(x, y, 0, 0), contains: () => false };
}
document.addEventListener("pointerdown", (e) => {
  const f = Float.cur;
  if (f && !f.panel.contains(e.target) && !f.anchor.contains(e.target)) closeFloat();
}, true);
// something scrolled (the chat streaming, the form): follow the anchor; close only once it is off screen
document.addEventListener("scroll", (e) => {
  const f = Float.cur;
  if (!f || f.panel.contains(e.target)) return;
  const r = f.anchor.getBoundingClientRect();
  if (r.bottom < 0 || r.top > innerHeight) closeFloat(); else placeFloat(f);
}, true);
window.addEventListener("resize", closeFloat);
window.addEventListener("blur", closeFloat);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && Float.cur) {
    e.stopPropagation();
    const a = Float.cur.anchor;
    closeFloat();
    if (a.focus) a.focus();
  }
}, true);

// ---- menus: [{label, icon, onClick, danger, disabled, hint, tip}] | "sep" | {header} ----
function menuPanel(items, { cls = "" } = {}) {
  const panel = h("div", { class: "menu " + cls, role: "menu" });
  for (const it of items) {
    if (!it) continue;
    if (it === "sep") { panel.append(h("div", { class: "menu-sep", role: "separator" })); continue; }
    if (it.header) { panel.append(h("div", { class: "menu-head" }, it.header)); continue; }
    panel.append(h("button", {
      type: "button", role: "menuitem", class: "menu-item" + (it.danger ? " danger" : ""), disabled: it.disabled,
      title: it.tip, onpointerdown: it.keepFocus ? (e) => e.preventDefault() : null,
      onclick: () => { closeFloat(); it.onClick(); },
    }, it.icon ? icon(it.icon) : h("span", { class: "i-pad" }), h("span", { class: "mi-label" }, it.label), it.hint && h("span", { class: "mi-hint" }, it.hint)));
  }
  panel.addEventListener("keydown", (e) => menuKeys(e, panel, "[role=menuitem]:not(:disabled)"));
  return panel;
}
function openMenu(anchor, items, opts = {}) {
  const panel = menuPanel(items, opts);
  return openFloat(anchor, panel, { minWidth: 200, ...opts, focus: opts.focusFirst ? panel.querySelector("[role=menuitem]:not(:disabled)") : null });
}
function menuKeys(e, panel, sel) {
  const items = [...panel.querySelectorAll(sel)];
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
  else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
  else if (e.key === "Tab") closeFloat();
}

// ================================================================ select
// uiSelect({id, options: [{value, label, hint, desc, group, disabled}], value, onChange,
//           placeholder, cls, title, label, place, align, menuWidth})
// The trigger is a <button>: `el.dataset.value` mirrors the value for tests and CSS.
function uiSelect(o) {
  let opts = o.options || [], val = o.value ?? null, open = false;
  const lab = h("span", { class: "sel-label" });
  const btn = h("button", { type: "button", class: "select " + (o.cls || ""), id: o.id, "aria-haspopup": "listbox",
    "aria-expanded": "false", "aria-label": o.label, title: o.title }, lab, icon("chev"));
  const same = (a, b) => (a ?? null) === (b ?? null);
  const paint = () => {
    const cur = opts.find((x) => same(x.value, val));
    lab.textContent = cur ? (cur.short || cur.label) : (o.placeholder || "请选择");
    lab.classList.toggle("ph", !cur);
    btn.dataset.value = val ?? "";
  };
  const choose = (v) => {
    closeFloat();
    btn.focus({ preventScroll: true });
    if (same(v, val)) return;
    val = v;
    paint();
    if (o.onChange) o.onChange(v);
  };
  const show = () => {
    const list = h("div", { class: "listbox", role: "listbox", "aria-label": o.label || "" });
    let group = undefined, active = null;
    for (const x of opts) {
      if (x.group !== group) { group = x.group; if (group) list.append(h("div", { class: "lb-group" }, group)); }
      const sel = same(x.value, val);
      const item = h("button", { type: "button", role: "option", class: "lb-opt" + (sel ? " on" : ""), "aria-selected": String(sel),
        "data-value": x.value ?? "", disabled: x.disabled, title: x.tip, onclick: () => choose(x.value) },
        h("span", { class: "lb-check" }, sel ? icon("check") : null),
        h("span", { class: "lb-main" }, h("span", { class: "lb-label" }, x.label), x.desc && h("span", { class: "lb-desc" }, x.desc)),
        x.hint && h("span", { class: "lb-hint" }, x.hint));
      if (sel) active = item;
      list.append(item);
    }
    let typed = "", typedAt = 0;
    list.addEventListener("keydown", (e) => {
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        typed = (Date.now() - typedAt > 700 ? "" : typed) + e.key.toLowerCase();
        typedAt = Date.now();
        const hit = [...list.querySelectorAll(".lb-opt:not(:disabled)")].find((b) => b.textContent.toLowerCase().startsWith(typed));
        if (hit) hit.focus();
        return;
      }
      menuKeys(e, list, ".lb-opt:not(:disabled)");
    });
    open = true;
    openFloat(btn, list, { place: o.place, align: o.align, minWidth: o.menuWidth || "anchor",
      onClose: () => { open = false; }, focus: active || list.querySelector(".lb-opt:not(:disabled)") });
  };
  btn.addEventListener("click", () => (open ? closeFloat() : show()));
  btn.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); show(); }
  });
  paint();
  const api = {
    el: btn,
    get value() { return val; },
    set value(v) { val = v; paint(); },
    setOptions(next, v) { opts = next; if (v !== undefined) val = v; paint(); },
    set disabled(d) { btn.disabled = !!d; },
  };
  btn._select = api;
  return api;
}

// ---- segmented control: [{value, label, tip}] ----
function uiSeg(options, value, onChange, { cls = "", disabled = false, label = "" } = {}) {
  return h("div", { class: "seg " + cls, role: "radiogroup", "aria-label": label },
    options.map((x) => h("button", { type: "button", role: "radio", "aria-checked": String(x.value === value),
      class: x.value === value ? "on" : "", disabled, title: x.tip, "data-value": x.value,
      onclick: () => { if (x.value !== value) onChange(x.value); } }, x.label)));
}

// ================================================================ colour picker
const SWATCHES = [
  ["1F4E79", "深海蓝（默认）"], ["2A4B8D", "墨水蓝"], ["1D3557", "午夜蓝"], ["0F5257", "深青"],
  ["1E5631", "松绿"], ["4A5D23", "橄榄"], ["7A1F2B", "酒红"], ["8C2F1B", "砖红"],
  ["5B2A86", "暗紫"], ["6B4E16", "古铜"], ["3A3F47", "石墨"], ["111111", "黑"],
];
function colorField({ value, onChange, onReset, isDefault }) {
  const norm = (v) => String(v || "").replace("#", "").toUpperCase();
  const chip = h("span", { class: "swatch" });
  const text = h("span", { class: "cf-hex" });
  const btn = h("button", { type: "button", class: "color-field", id: "ctl-accent", "aria-haspopup": "dialog" }, chip, text, icon("chev"));
  const paint = (v) => { chip.style.background = "#" + norm(v); text.textContent = "#" + norm(v); btn.dataset.value = norm(v); };
  paint(value);
  btn.addEventListener("click", () => {
    const cur = norm(btn.dataset.value);
    const hex = h("input", { class: "cp-hex", value: "#" + cur, spellcheck: "false", maxlength: "7", "aria-label": "十六进制颜色" });
    const commit = () => {
      const v = norm(hex.value);
      if (!/^[0-9A-F]{6}$/.test(v)) { hex.classList.add("bad"); return; }
      closeFloat(); paint(v); onChange(v);
    };
    hex.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    hex.addEventListener("input", () => {
      hex.classList.remove("bad");
      const v = norm(hex.value);
      if (/^[0-9A-F]{6}$/.test(v)) preview.style.background = "#" + v;
    });
    const preview = h("span", { class: "cp-preview", style: `background:#${cur}` });
    const panel = h("div", { class: "color-pop", role: "dialog", "aria-label": "强调色" },
      h("div", { class: "cp-grid" }, SWATCHES.map(([c, name]) => h("button", {
        type: "button", class: "cp-sw" + (c === cur ? " on" : ""), style: `--c:#${c}`, title: `${name} #${c}`, "aria-label": name,
        onclick: () => { closeFloat(); paint(c); onChange(c); } }))),
      h("div", { class: "cp-row" }, preview, hex, h("button", { type: "button", class: "btn sm", onclick: commit }, "应用")),
      onReset && h("button", { type: "button", class: "cp-reset", disabled: isDefault && isDefault(), onclick: () => { closeFloat(); onReset(); } }, icon("undo"), "恢复模板默认色"));
    openFloat(btn, panel, { focus: panel.querySelector(".cp-sw.on") || hex });
  });
  return { el: btn, set value(v) { paint(v); } };
}

// ================================================================ text context menu
// The app window has no native right-click menu, so text fields get this one.
function initContextMenu() {
  document.addEventListener("contextmenu", (e) => {
    const field = e.target.closest("input:not([type=range]):not([type=checkbox]), textarea");
    const selected = String(getSelection() || "");
    const hasFieldSel = field && field.selectionStart !== field.selectionEnd;
    if (!field && !selected) return;
    e.preventDefault();
    const ro = field && (field.readOnly || field.disabled);
    const items = [
      field && { label: "剪切", icon: "scissors", hint: "Ctrl+X", disabled: ro || !hasFieldSel, keepFocus: true, onClick: () => document.execCommand("cut") },
      { label: "复制", icon: "copy", hint: "Ctrl+C", disabled: field ? !hasFieldSel : !selected, keepFocus: true, onClick: () => document.execCommand("copy") },
      field && { label: "粘贴", icon: "clipboard", hint: "Ctrl+V", disabled: ro, keepFocus: true, onClick: () => pasteInto(field) },
      field && "sep",
      field && { label: "全选", icon: "selectAll", hint: "Ctrl+A", keepFocus: true, onClick: () => { field.focus(); field.select(); } },
    ];
    openMenu(pointAnchor(e.clientX, e.clientY), items, { minWidth: 168 });
  });
}
async function pasteInto(field) {
  let text = "";
  try { text = (await (await fetch("/api/clipboard")).json()).text || ""; } catch (_) { /* nothing to paste */ }
  if (!text) return;
  field.focus();
  document.execCommand("insertText", false, text); // keeps the field's own undo history
}

// ================================================================ range sliders
// The filled part of the track follows the value (a CSS variable; the thumb and track are styled in CSS).
function syncRange(el) {
  const min = parseFloat(el.min || 0), max = parseFloat(el.max || 100), v = parseFloat(el.value);
  el.style.setProperty("--p", `${((v - min) / (max - min)) * 100}%`);
}
document.addEventListener("input", (e) => { if (e.target.matches && e.target.matches("input[type=range]")) syncRange(e.target); });
