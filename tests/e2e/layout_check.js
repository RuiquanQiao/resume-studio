// The layout checker (TESTING.md, "Layout checks"): looks at what is on screen the way a reviewer would
// and lists what is broken. Run in the page by tests/e2e/test_layout.py; returns [{kind, el, detail}].
//
//   squeezed  text pushed into a column a few letters wide (one word per line)
//   wrapped   a compact button, tab or chip whose label broke over two lines
//   hidden    a heading or note cut to an ellipsis (they wrap; a compact label may be cut, its hover shows the rest)
//   cut       an element cut off by a container that hides its overflow
//   sticks    an element painting outside the box it belongs to, over its neighbours
//   spills    text running past its own box (nothing clips it), over its neighbours
//   scrolls   a pane or box that scrolls sideways when it should not
//   overlap   two controls on top of each other
//
// Things meant to break these rules (a popover, a sideways-scrolling table) are skipped by `opts.skip`,
// a list of CSS selectors, each with its reason in test_layout.py.
(opts) => {
  const skip = (opts && opts.skip) || [];
  const out = [];
  const seen = new Set();
  const report = (kind, el, detail) => {
    const key = kind + "|" + name(el);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ kind, el: name(el), detail });
  };
  const name = (el) => {
    const parts = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && i < 3; n = n.parentElement, i++) {
      let s = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(s + "#" + n.id); break; }
      const cls = [...n.classList].slice(0, 3).join(".");
      if (cls) s += "." + cls;
      parts.unshift(s);
    }
    const text = (el.innerText || el.value || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return parts.join(" > ") + (text ? ` "${text}"` : "");
  };
  const skipped = (el) => skip.some((s) => el.closest(s));
  const shown = (el) => el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  const css = (el) => getComputedStyle(el);
  const px = (v) => parseFloat(v) || 0;

  // the part of the window an element can actually be seen in: the viewport cut by every
  // ancestor that clips (overflow other than visible)
  const clipOf = (el) => {
    let r = { l: 0, t: 0, r: innerWidth, b: innerHeight };
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const s = css(n);
      if (s.position === "fixed") break;
      if (s.overflowX !== "visible" || s.overflowY !== "visible") {
        const b = n.getBoundingClientRect();
        r = { l: Math.max(r.l, b.left), t: Math.max(r.t, b.top), r: Math.min(r.r, b.right), b: Math.min(r.b, b.bottom) };
      }
    }
    return r;
  };
  const inView = (el) => {
    const b = el.getBoundingClientRect(), c = clipOf(el);
    // a box squeezed to 0px wide still shows its text (overflowing it): that is the worst case, not "hidden"
    return (b.width >= 1 || b.height >= 1) && b.right >= c.l && b.left <= c.r && b.bottom > c.t && b.top < c.b;
  };
  // floating layers (menus, tooltips, dialogs, sticky bars) are meant to sit over other things
  const floats = (el, upTo) => {
    for (let n = el; n && n !== upTo; n = n.parentElement) {
      const p = css(n).position;
      if (p === "fixed" || p === "sticky" || p === "absolute") return true;
    }
    return false;
  };

  const all = [...document.body.querySelectorAll("*")].filter((el) =>
    !["SCRIPT", "STYLE", "svg", "path", "IFRAME", "IMG", "CANVAS", "BR", "OPTION"].includes(el.tagName)
    && !(el instanceof SVGElement) && !skipped(el) && shown(el) && inView(el));

  // lines a text node is laid out on
  const lineCount = (el) => {
    const tops = [];
    for (const node of el.childNodes) {
      if (node.nodeType !== 3 || !node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width < 1) continue;
        if (!tops.some((t) => Math.abs(t - r.top) < r.height / 2)) tops.push(r.top);
      }
    }
    return tops.length;
  };
  const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();

  for (const el of all) {
    const s = css(el), b = el.getBoundingClientRect();
    const fs = px(s.fontSize) || 13;
    const text = ownText(el);

    // squeezed: three or more lines in a box narrower than six letters
    if (text.length >= 8) {
      const lines = lineCount(el);
      const width = b.width - px(s.paddingLeft) - px(s.paddingRight);
      if (lines >= 3 && width < fs * 6) report("squeezed", el, `${lines} lines in ${Math.round(width)}px`);
    }

    // wrapped: a button or chip label over two lines
    // compact controls by name, and by look: any inline box with its own fill or outline (a badge, a pill, a
    // tab) whatever its class is called; a card or row may wrap
    const boxed = s.backgroundColor !== "rgba(0, 0, 0, 0)" || px(s.borderTopWidth) > 0 || s.boxShadow !== "none";
    const isControl = el.matches(".btn, [role=tab], .chip, [class*='-chip'], .seg > button")
      || (/^inline-(flex|block|grid)$/.test(s.display) && boxed);
    if (isControl && text && s.whiteSpace !== "nowrap" && lineCount(el) >= 2 && !el.matches("[data-wrap]"))
      report("wrapped", el, `label on ${lineCount(el)} lines`);

    // hidden: a heading or a note (hint, subtitle, a version's note) cut to an ellipsis. Those are read, so they
    // wrap; a compact label (a rail's titles, a table cell, a chip) may end in an ellipsis, since hovering it shows
    // the rest (ui.js cutBox). Added 2026-10-31 after a fold's note could never be read in full.
    if (s.textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1 && (el.innerText || "").trim()
        && el.matches("h1, h2, h3, h4, .hint, [class*='-sub'], [class$='sub'], .vtext, [class*='-note'], [class*='desc']"))
      report("hidden", el, `${el.scrollWidth - el.clientWidth}px of a heading or note behind an ellipsis`);

    // cut: content hidden by its own box, with no ellipsis to say so
    if ((s.overflowX === "hidden" || s.overflowX === "clip") && el.scrollWidth > el.clientWidth + 1
        && s.textOverflow !== "ellipsis" && (el.innerText || "").trim())
      report("cut", el, `${el.scrollWidth - el.clientWidth}px of ${el.scrollWidth}px hidden on the right`);
    if ((s.overflowY === "hidden" || s.overflowY === "clip") && el.scrollHeight > el.clientHeight + 2
        && s.webkitLineClamp === "none" && !el.matches("details:not([open])") && (el.innerText || "").trim()
        && el.clientHeight > 0)
      report("cut", el, `${el.scrollHeight - el.clientHeight}px of ${el.scrollHeight}px hidden below`);

    // spills: its own text runs past its box, with nothing clipping it, over what is next to it
    if (text && s.overflowX === "visible" && el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0
        && !["inline", "contents"].includes(s.display))
      report("spills", el, `text runs ${el.scrollWidth - el.clientWidth}px past its box`);

    // scrolls sideways
    if ((s.overflowX === "auto" || s.overflowX === "scroll") && el.scrollWidth > el.clientWidth + 1
        && el.tagName !== "TEXTAREA" && el.tagName !== "INPUT" && !el.matches("[data-scrolls]"))   // [data-scrolls]: meant to (a board, a sized table)
      report("scrolls", el, `scrolls ${el.scrollWidth - el.clientWidth}px sideways`);

    // sticks out of its parent, painting over what is next to it
    const p = el.parentElement;
    if (p && p !== document.body && s.position !== "absolute" && s.position !== "fixed" && css(p).display !== "contents"
        && !p.matches("[data-scrolls]")) {   // what a sideways scroller holds runs past it on purpose
      const pb = p.getBoundingClientRect();
      const over = Math.max(b.right - pb.right, pb.left - b.left);
      if (over > 2 && pb.width > 0) report("sticks", el, `${Math.round(over)}px outside ${name(p)}`);
    }
  }

  // two controls on top of each other
  const controls = all.filter((el) => el.matches("button, a[href], input, textarea, [role=option], [role=tab], [contenteditable=true], .btn")
    && !el.closest(".float, .tip, .modal"));
  const vis = controls.map((el) => {
    const b = el.getBoundingClientRect(), c = clipOf(el);
    return { el, l: Math.max(b.left, c.l), t: Math.max(b.top, c.t), r: Math.min(b.right, c.r), b: Math.min(b.bottom, c.b) };
  });
  for (let i = 0; i < vis.length; i++) {
    for (let j = i + 1; j < vis.length; j++) {
      const a = vis[i], z = vis[j];
      if (a.el.contains(z.el) || z.el.contains(a.el)) continue;
      const w = Math.min(a.r, z.r) - Math.max(a.l, z.l), hgt = Math.min(a.b, z.b) - Math.max(a.t, z.t);
      if (w <= 2 || hgt <= 2) continue;
      let common = a.el.parentElement;
      while (common && !common.contains(z.el)) common = common.parentElement;
      if (floats(a.el, common) || floats(z.el, common)) continue;
      report("overlap", a.el, `${Math.round(w)}×${Math.round(hgt)}px under ${name(z.el)}`);
    }
  }

  if (document.documentElement.scrollWidth > innerWidth + 1)
    out.push({ kind: "scrolls", el: "the window", detail: `${document.documentElement.scrollWidth - innerWidth}px sideways` });
  return out;
}
