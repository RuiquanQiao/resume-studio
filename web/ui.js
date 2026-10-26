// Resume Studio UI kit: the controls the app uses instead of the platform's own.
// Native <select>, tooltips, colour picker and right-click menu look like Windows
// dialogs inside the page, so each one is rebuilt here on the app's tokens.
// Loaded before app.js; everything here is global on purpose (no build step).
"use strict";

const $ = (s, root = document) => root.querySelector(s);

// ================================================================ i18n
// window.I18N comes from /i18n.js (loaded first): {lang, dict, chosen, system, langs}.
// Keys are the English text; t("Saved"), t("{n} entry", {n}, "{n} entries") for counts.
const I18N = window.I18N || { lang: "en", dict: {}, langs: { en: "English" } };
function t(key, vars = {}, other = null) {
  const n = vars.n;
  const hit = I18N.lang !== "en" ? I18N.dict[key] : undefined;
  let s;
  if (Array.isArray(hit)) s = hit[(n === 1 || (I18N.lang === "fr" && (n === 0 || n === 1)) || hit.length === 1) ? 0 : 1];
  else if (hit != null) s = hit;
  else s = other != null && n !== 1 ? other : key;
  return s.replace(/\{(\w+)\}/g, (m, k, at) => {
    if (!(k in vars)) return m;
    let v = String(vars[k]);
    // Chinese sets a space between Han characters and a Latin word or number ("补齐 English 版")
    if (I18N.lang === "zh" && v) {
      if (HAN.test(s[at - 1] || "") && LATIN.test(v[0])) v = " " + v;
      if (HAN.test(s[at + m.length] || "") && LATIN.test(v[v.length - 1])) v += " ";
    }
    return v;
  });
}
const HAN = /[㐀-鿿豈-﫿]/, LATIN = /[A-Za-z0-9]/;
// the static page (index.html): the own text of elements marked data-t (child elements such as
// the chat tab's dot stay), and every title / placeholder / aria-label
function localizeStatic(root = document) {
  for (const el of root.querySelectorAll("[data-t]")) {
    const node = [...el.childNodes].find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
    if (node) node.textContent = t(node.textContent.trim());
  }
  for (const attr of ["title", "placeholder", "aria-label"]) {
    for (const el of root.querySelectorAll(`[${attr}]`)) el.setAttribute(attr, t(el.getAttribute(attr)));
  }
  document.documentElement.lang = I18N.lang === "zh" ? "zh-CN" : I18N.lang;
}

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

// A change shows at once, also while the user types: the part of the page is rebuilt, and the field being
// typed in comes back with its text, caret and focus, so typing carries on as in any app. Only while an IME
// (pinyin, kana) is mid-word does the rebuild wait for the finished word, which a rebuild would break off.
let imeComposing = false, imeLater = null;
document.addEventListener("compositionstart", () => { imeComposing = true; }, true);
document.addEventListener("compositionend", () => {
  imeComposing = false;
  const later = imeLater; imeLater = null;
  if (later) setTimeout(later, 0);   // after the field's own input event
}, true);

function fieldKey(el, root) {
  const parts = [];
  for (let n = el; n && n !== root; n = n.parentElement) {
    parts.push([n.tagName, n.id, n.dataset.id, n.dataset.key, n.dataset.edit, n.dataset.page, n.getAttribute("name"),
      n.getAttribute("aria-label"), n.getAttribute("placeholder")].map((x) => x || "").join("|"));
    if (n.id) break;
  }
  return parts.join(">");
}

function repaintKeeping(root, paint) {
  const a = document.activeElement;
  const typing = root && a && a !== root && root.contains(a) && a.matches("input, textarea, select") ? a : null;
  if (typing && imeComposing) { imeLater = () => repaintKeeping(root, paint); return; }
  if (!typing) { paint(); return; }
  const key = fieldKey(typing, root);
  const same = (r) => [...r.querySelectorAll(typing.tagName)].filter((x) => fieldKey(x, root) === key);
  const nth = Math.max(0, same(root).indexOf(typing));
  const value = typing.value;
  let sel = null;
  try { sel = [typing.selectionStart, typing.selectionEnd, typing.selectionDirection]; } catch (e) { /* no caret */ }
  paint();
  if (document.activeElement && document.activeElement !== document.body && root.contains(document.activeElement)) return;   // a nested repaint did it
  const fresh = same(root);
  const spot = fresh[nth] || fresh[0];
  if (!spot) return;   // the field is gone from the new page
  // the new page's own field (its buttons read it), with what was typed in it
  if (spot.value !== value) spot.value = value;
  if (spot.tagName === "TEXTAREA" && typeof autosizeFull === "function") autosizeFull(spot);
  spot.focus({ preventScroll: true });
  if (sel && sel[0] != null) try { spot.setSelectionRange(...sel); } catch (e) { /* no caret */ }
}

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3.2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  grip: '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" stroke-width="3.2"/>',
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
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
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
  chevLeft: '<path d="m14 7-5 5 5 5"/>',
  rewind: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 8.9"/><path d="M4.5 4.5v4.4h4.4"/>',
  history: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 8.9"/><path d="M4.5 4.5v4.4h4.4"/><path d="M12 8v4.2l2.8 1.8"/>',
  commit: '<circle cx="12" cy="12" r="3.2"/><path d="M3 12h5.8M15.2 12H21"/>',
  clip: '<path d="m20 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
  fork: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7v2a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 12v5"/>',
  shield: '<path d="M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
  hand: '<path d="M8 13V6a1.5 1.5 0 0 1 3 0v6M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-.6a6 6 0 0 1-4.6-2.2L3.5 15a1.6 1.6 0 0 1 2.4-2.1L8 15"/>',
  plan: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1 1 2-2M3.5 12l1 1 2-2"/><circle cx="5" cy="18" r="1.2"/>',
  bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
  pin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  home: '<path d="M4 11 12 4l8 7M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
  briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3 13h18"/>',
  outline: '<path d="M9 6h11M12 12h8M12 18h8"/><path d="M4.5 6h.01M7.5 12h.01M7.5 18h.01" stroke-width="3"/>',
  panelRight: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14.5 4v16"/>',
  // the database views' own (web/dbview.js)
  text: '<path d="M5 7h14M5 12h14M5 17h9"/>',
  option: '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 10.8 3.5 3.5 3.5-3.5"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  list: '<path d="M9 7h11M9 12h11M9 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" stroke-width="2.2"/>',
  table: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M9.5 10v9"/>',
  columns: '<rect x="3.5" y="5" width="4.5" height="14" rx="1.2"/><rect x="9.75" y="5" width="4.5" height="10" rx="1.2"/><rect x="16" y="5" width="4.5" height="7" rx="1.2"/>',
  minus: '<path d="M5 12h14"/>',
  map: '<path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6z"/><path d="M9 4v14M15 6v14"/>',
  gantt: '<path d="M4 6h9M7 12h10M10 18h10" stroke-width="3" stroke-linecap="round"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  filter: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  sort: '<path d="M7 4v16M4 7l3-3 3 3M17 20V4M14 17l3 3 3-3"/>',
  sortUp: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  sortDown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  layers: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  eyeOff: '<path d="M10.6 6.6A9 9 0 0 1 12 6.5c6 0 9.5 5.5 9.5 5.5a15 15 0 0 1-2.6 3.1M6.4 7.7A15 15 0 0 0 2.5 12S6 18.5 12 18.5a9 9 0 0 0 4.1-1M4 4l16 16"/>',
  wrap: '<path d="M4 6h16M4 12h13a3 3 0 0 1 0 6h-4M4 18h5"/><path d="m15 16-2 2 2 2"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="m15.8 8.2-2.4 5.2-5.2 2.4 2.4-5.2z"/>',
  apps: '<circle cx="6" cy="6" r="1.6"/><circle cx="12" cy="6" r="1.6"/><circle cx="18" cy="6" r="1.6"/><circle cx="6" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18" cy="12" r="1.6"/><circle cx="6" cy="18" r="1.6"/><circle cx="12" cy="18" r="1.6"/><circle cx="18" cy="18" r="1.6"/>',
};
// a field holding a web address: an open button at its end, there whenever the text is an http(s) address,
// so a link is one click away wherever it is typed (it opens in the everyday browser)
const isWebUrl = (v) => /^https?:\/\/\S+$/i.test(String(v || "").trim());
function linkOpen(input) {
  const btn = h("button", { type: "button", class: "btn icon sm ghost link-open", title: t("Open it in your everyday browser"), "aria-label": t("Open it in your everyday browser"),
    onclick: () => openInBrowser(input.value.trim()) }, icon("external"));
  const wrap = h("span", { class: "link-in" }, input, btn);
  const sync = () => wrap.classList.toggle("has-url", isWebUrl(input.value));
  input.addEventListener("input", sync);
  sync();
  return wrap;
}

function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

// ================================================================ drag to reorder
// A Notion-style six-dot handle. Rows are the `.sort-row` children of one parent; while
// dragging, the row moves through the list live, and onMove(from, to) runs on drop.
// The arrow keys move a focused handle one step.
function dragHandle(onMove, label = t("Drag to reorder")) {
  const btn = h("button", { type: "button", class: "btn icon sm ghost drag-handle", title: label, "aria-label": label }, icon("grip"));
  const rowsOf = (row) => [...row.parentElement.children].filter((x) => x.classList.contains("sort-row"));
  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const row = btn.closest(".sort-row");
    const list = row.parentElement, home = row.nextSibling, from = rowsOf(row).indexOf(row);
    // listen on window, not pointer capture: moving the row in the DOM would drop the capture
    row.classList.add("lifted");
    document.body.classList.add("sorting");
    const move = (ev) => {
      const others = rowsOf(row).filter((x) => x !== row);
      const before = others.find((x) => { const r = x.getBoundingClientRect(); return ev.clientY < r.top + r.height / 2; });
      const at = before || (others.length ? others[others.length - 1].nextSibling : null);
      if (at !== row && at !== row.nextSibling) list.insertBefore(row, at);
    };
    const end = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      row.classList.remove("lifted");
      document.body.classList.remove("sorting");
      const to = rowsOf(row).indexOf(row);
      if (ev.type === "pointercancel") { list.insertBefore(row, home); return; }
      if (to !== from) onMove(from, to);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  });
  btn.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const row = btn.closest(".sort-row"), rows = rowsOf(row), from = rows.indexOf(row);
    const to = from + (e.key === "ArrowUp" ? -1 : 1);
    if (to >= 0 && to < rows.length) onMove(from, to);
  });
  return btn;
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
  if (f.place === "over") {   // over the anchor itself (a table cell edited in place)
    p.style.maxHeight = "";
    p.style.top = Math.max(pad, Math.min(r.top, H - ph - pad)) + "px";
    p.style.left = Math.max(pad, Math.min(r.left, W - pw - pad)) + "px";
    p.dataset.side = "over";
    return;
  }
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
// A link to another site opens in the computer's own browser, never in this window: followed here, it
// would replace Resume Studio with the site (a long blank, then the page, and no way back but restarting).
// One handler for every link, so a link added anywhere later can't forget it.
function externalLink(e) {
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a || e.defaultPrevented || (e.type === "auxclick" && e.button !== 1)) return;
  let url;
  try { url = new URL(a.getAttribute("href"), location.href); } catch (err) { return; }
  if (!/^https?:$/.test(url.protocol) || url.origin === location.origin) return;
  e.preventDefault();
  e.stopPropagation();   // a link's own onclick would open it a second time
  api("POST", "/api/open", { url: url.href }).catch((err) => toast(err.message));
}
document.addEventListener("click", externalLink, true);
document.addEventListener("auxclick", externalLink, true);
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
// uiSelect({id, options: [{value, label, hint, desc, group, disabled, mark}], value, onChange,
//           placeholder, cls, title, label, place, align, menuWidth, marks})
// `marks`: the slot left of each option shows its `mark` (a state, as a small sign) instead of a tick; the chosen
// one is still coloured, and the trigger shows the chosen one's mark before its label.
// The trigger is a <button>: `el.dataset.value` mirrors the value for tests and CSS.
function uiSelect(o) {
  let opts = o.options || [], val = o.value ?? null, open = false;
  const lab = h("span", { class: "sel-label" });
  const slot = o.marks ? h("span", { class: "sel-mark" }) : null;
  const btn = h("button", { type: "button", class: "select " + (o.cls || ""), id: o.id, "aria-haspopup": "listbox",
    "aria-expanded": "false", "aria-label": o.label, title: o.title }, slot, lab, icon("chev"));
  const same = (a, b) => (a ?? null) === (b ?? null);
  const paint = () => {
    const cur = opts.find((x) => same(x.value, val));
    lab.textContent = cur ? (cur.short || cur.label) : (o.placeholder || t("Choose…"));
    lab.classList.toggle("ph", !cur);
    if (slot) slot.replaceChildren(cur && cur.mark ? cur.mark() : "");
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
  let shown = null;   // the open list, filled again when the options change under it (a chat's state, say)
  const fill = (list) => {
    const focused = document.activeElement && list.contains(document.activeElement) ? document.activeElement.dataset.value : null;
    list.querySelectorAll(".lb-group, .lb-opt").forEach((x) => x.remove());
    let group = undefined, active = null;
    for (const x of opts) {
      if (x.group !== group) { group = x.group; if (group) list.append(h("div", { class: "lb-group" }, group)); }
      const sel = same(x.value, val);
      const item = h("button", { type: "button", role: "option", class: "lb-opt" + (sel ? " on" : ""), "aria-selected": String(sel),
        "data-value": x.value ?? "", disabled: x.disabled, title: x.tip, onclick: () => choose(x.value) },
        h("span", { class: "lb-check" }, o.marks ? (x.mark ? x.mark() : null) : sel ? icon("check") : null),
        h("span", { class: "lb-main" }, h("span", { class: "lb-label" }, x.label), x.desc && h("span", { class: "lb-desc" }, x.desc)),
        x.hint && h("span", { class: "lb-hint" }, x.hint));
      if (sel) active = item;
      list.append(item);
      if (focused != null && String(x.value ?? "") === focused) item.focus({ preventScroll: true });
    }
    return active;
  };
  const show = () => {
    const list = h("div", { class: "listbox", role: "listbox", "aria-label": o.label || "" });
    const active = fill(list);
    shown = list;
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
      onClose: () => { open = false; shown = null; }, focus: active || list.querySelector(".lb-opt:not(:disabled)") });
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
    setOptions(next, v) { opts = next; if (v !== undefined) val = v; paint(); if (open && shown) fill(shown); },
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
  ["1F4E79", "Deep sea blue (default)"], ["2A4B8D", "Ink blue"], ["1D3557", "Midnight blue"], ["0F5257", "Dark teal"],
  ["1E5631", "Pine green"], ["4A5D23", "Olive"], ["7A1F2B", "Wine red"], ["8C2F1B", "Brick red"],
  ["5B2A86", "Dark purple"], ["6B4E16", "Bronze"], ["3A3F47", "Graphite"], ["111111", "Black"],
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
    const hex = h("input", { class: "cp-hex", value: "#" + cur, spellcheck: "false", maxlength: "7", "aria-label": t("Hex colour") });
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
    const panel = h("div", { class: "color-pop", role: "dialog", "aria-label": t("Accent colour") },
      h("div", { class: "cp-grid" }, SWATCHES.map(([c, name]) => h("button", {
        type: "button", class: "cp-sw" + (c === cur ? " on" : ""), style: `--c:#${c}`, title: `${t(name)} #${c}`, "aria-label": t(name),
        onclick: () => { closeFloat(); paint(c); onChange(c); } }))),
      h("div", { class: "cp-row" }, preview, hex, h("button", { type: "button", class: "btn sm", onclick: commit }, t("Apply"))),
      onReset && h("button", { type: "button", class: "cp-reset", disabled: isDefault && isDefault(), onclick: () => { closeFloat(); onReset(); } }, icon("undo"), t("Back to the template's colour")));
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
      field && { label: t("Cut"), icon: "scissors", hint: "Ctrl+X", disabled: ro || !hasFieldSel, keepFocus: true, onClick: () => document.execCommand("cut") },
      { label: t("Copy"), icon: "copy", hint: "Ctrl+C", disabled: field ? !hasFieldSel : !selected, keepFocus: true, onClick: () => document.execCommand("copy") },
      field && { label: t("Paste"), icon: "clipboard", hint: "Ctrl+V", disabled: ro, keepFocus: true, onClick: () => pasteInto(field) },
      field && "sep",
      field && { label: t("Select all"), icon: "selectAll", hint: "Ctrl+A", keepFocus: true, onClick: () => { field.focus(); field.select(); } },
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
const DIFF_OPEN = new Set();   // diffs the user unfolded, by key, unless the caller keeps its own set
function diffEl(from, to, key, opened = DIFF_OPEN) {
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
      out.push(h("button", { type: "button", class: "dl dl-fold", onclick: (e) => { e.stopPropagation(); opened.add(key); paint(true); } },
        h("span", { class: "dl-sign" }, "⋯"), h("span", { class: "dl-text" }, t("{n} unchanged line", { n: y - x }, "{n} unchanged lines"))));
      x = y;
    }
    if (!rows.length) out.push(h("div", { class: "dl dl-same" }, h("span", { class: "dl-sign" }, " "), h("span", { class: "dl-text muted" }, t("(empty)"))));
    box.replaceChildren(...out);
  };
  paint(opened.has(key));
  return box;
}
