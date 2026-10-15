// Read a page the way Claude can act on it, and find the elements again afterwards.
// core/browser.py runs this in an isolated world of the page: the page's own scripts can't see it,
// and nothing is written into the page's DOM. The whole document is read, not just what is on screen.
//
// A snapshot is one line per thing worth knowing, in document order:
//   ## Experience                                  a heading
//   Software Engineer · Acme                       text
//   [12] button "Edit experience"                  something to act on, with its ref
//   [13] textbox "Title*" = "Engineer" maxlength=100
//   [14] select "Start month" = "Aug" options: Jan | Feb | …
//   === dialog "Edit experience" ===               an open dialog, up to "=== end dialog ==="
// A ref stays the same for an element as long as it is in the page, across snapshots.
(() => {
  if (globalThis.__rs) return;
  const S = { els: [null], refOf: new WeakMap() };

  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const q = (s) => JSON.stringify(s);
  const win = (el) => (el.ownerDocument && el.ownerDocument.defaultView) || window;
  const tag = (el) => el.tagName.toLowerCase();
  const role = (el) => (el.getAttribute("role") || "").toLowerCase();

  function refFor(el) {
    let r = S.refOf.get(el);
    if (!r) { r = S.els.length; S.els.push(el); S.refOf.set(el, r); }
    return r;
  }

  function shown(el) {
    if (el.getAttribute("aria-hidden") === "true" || el.hidden) return false;
    if (typeof el.checkVisibility === "function" && !el.checkVisibility({ checkVisibilityCSS: true })) return false;
    const cs = win(el).getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden";
  }

  const TEXT_INPUTS = new Set(["", "text", "email", "url", "tel", "number", "search", "password", "date", "month"]);

  // what kind of control this is, or "" for anything else
  function kind(el) {
    const t = tag(el), r = role(el);
    if (t === "input") {
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (type === "hidden") return "";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "file") return "file";
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (TEXT_INPUTS.has(type)) return r === "combobox" ? "combobox" : "textbox";
      return "textbox";
    }
    if (t === "textarea") return "textbox";
    if (t === "select") return "select";
    if (t === "button" || t === "summary") return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (["button", "link", "checkbox", "radio", "switch", "tab", "option", "menuitem", "menuitemcheckbox",
      "menuitemradio", "combobox", "textbox", "searchbox", "slider", "spinbutton", "treeitem"].includes(r)) return r === "searchbox" ? "textbox" : r;
    if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) return "textbox";
    return "";
  }

  // a div or span the site made clickable (its own click handler): the pointer cursor gives it away
  function clickable(el) {
    if (!el.parentElement) return false;
    const cs = win(el).getComputedStyle(el);
    if (cs.cursor !== "pointer") return false;
    return win(el).getComputedStyle(el.parentElement).cursor !== "pointer";
  }

  function labelText(el) {
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const doc = el.ownerDocument;
      const s = norm(by.split(/\s+/).map((id) => { const x = doc.getElementById(id); return x ? x.innerText || x.textContent : ""; }).join(" "));
      if (s) return s;
    }
    const aria = norm(el.getAttribute("aria-label"));
    if (aria) return aria;
    if (el.labels && el.labels.length) {
      const s = norm([...el.labels].map((l) => l.innerText || l.textContent).join(" "));
      if (s) return s;
    }
    return "";
  }

  function name(el, k) {
    const l = labelText(el);
    if (l) return l;
    if (["textbox", "select", "combobox", "file", "checkbox", "radio"].includes(k)) {
      const ph = norm(el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name"));
      if (ph) return ph;
    }
    if (tag(el) === "input" && el.value && k === "button") return norm(el.value);
    const img = el.querySelector && el.querySelector("img[alt]");
    const text = norm(el.innerText || el.textContent);
    return text || norm(el.getAttribute("title")) || (img ? norm(img.getAttribute("alt")) : "");
  }

  function valueOf(el) {
    const t = tag(el);
    if (t === "select") return el.selectedIndex >= 0 ? norm(el.options[el.selectedIndex].text) : "";
    if (t === "input" || t === "textarea") return String(el.value || "");
    if (el.isContentEditable) return String(el.innerText || "").replace(/\n$/, "");
    return norm(el.getAttribute("aria-valuetext") || el.getAttribute("aria-valuenow") || "");
  }

  function describe(el, k) {
    const r = refFor(el);
    let s = `[${r}] ${k} ${q(clip(name(el, k), 120))}`;
    if (k === "textbox" || k === "combobox") {
      const v = valueOf(el);
      if (v) s += " = " + q(clip(v, 4000));
      const max = el.getAttribute("maxlength");
      if (max && Number(max) > 0) s += ` maxlength=${max}`;
      if (tag(el) === "textarea" || el.isContentEditable) s += " multiline";
    } else if (k === "select") {
      s += " = " + q(valueOf(el));
      const opts = [...el.options].map((o) => norm(o.text)).filter(Boolean);
      s += opts.length > 40 ? ` options(${opts.length}): ${opts.slice(0, 12).join(" | ")} | … | ${opts.slice(-6).join(" | ")}` : ` options: ${opts.join(" | ")}`;
    } else if (k === "checkbox" || k === "radio" || k === "switch") {
      const on = el.checked === true || el.getAttribute("aria-checked") === "true";
      s += on ? " [checked]" : " [unchecked]";
    } else if (k === "link") {
      const href = el.getAttribute("href") || "";
      if (href && !href.startsWith("javascript:")) s += " -> " + clip(href, 160);
    } else if (k === "tab" || k === "option" || k.startsWith("menuitem")) {
      if (el.getAttribute("aria-selected") === "true") s += " [selected]";
    }
    if (el.getAttribute("aria-expanded") === "true") s += " [expanded]";
    if (el.disabled || el.getAttribute("aria-disabled") === "true") s += " [disabled]";
    if (el.required || el.getAttribute("aria-required") === "true") s += " [required]";
    if (el.readOnly) s += " [readonly]";
    if (el.getAttribute("aria-invalid") === "true") s += " [invalid]";
    return s;
  }

  const BLOCK = new Set(["block", "flex", "grid", "list-item", "table", "table-row", "table-cell", "flow-root", "table-caption"]);

  function snapshot(rootRef, maxChars) {
    const out = [];
    let line = [], size = 0, cut = false;
    const flush = () => {
      const s = norm(line.join(" "));
      line = [];
      if (s && out[out.length - 1] !== s) push(s);
    };
    const push = (s) => {
      if (cut) return;
      if (size + s.length > maxChars) { cut = true; return; }
      out.push(s); size += s.length + 1;
    };

    function walk(node) {
      if (cut) return;
      if (node.nodeType === 3) { if (node.nodeValue.trim()) line.push(node.nodeValue); return; }
      if (node.nodeType !== 1) return;
      const el = node, t = tag(el);
      if (["script", "style", "noscript", "template", "svg", "head", "meta", "link"].includes(t)) return;
      if (!shown(el)) return;
      const cs = win(el).getComputedStyle(el);
      const block = BLOCK.has(cs.display) || t === "br";
      const r = role(el);
      const isDialog = r === "dialog" || r === "alertdialog" || (t === "dialog" && el.open);
      const head = /^h[1-6]$/.test(t) ? Number(t[1]) : r === "heading" ? Number(el.getAttribute("aria-level") || 2) : 0;
      let k = kind(el);
      if (!k && clickable(el) && norm(el.innerText).length <= 80 && norm(el.innerText)) k = "clickable";

      if (isDialog) { flush(); push(`=== dialog ${q(clip(name(el, "dialog"), 80))} ===`); }
      if (head) {
        flush();
        const txt = norm(el.innerText || el.textContent);
        if (txt) push("#".repeat(Math.min(head, 6)) + " " + clip(txt, 200));
        if (!el.querySelector("a,button,input,select,textarea,[role],[contenteditable]")) return;
      } else if (k) {
        flush();
        push(describe(el, k));
        // a control is one line; a link or button wrapping a whole card still has its text read
        const long = norm(el.innerText).length > 150;
        if (!long || ["textbox", "select", "combobox"].includes(k)) {
          if (t === "label" || !el.children.length) return;
          // keep looking for controls inside (a link around a card with an edit button)
          for (const c of el.querySelectorAll("a[href],button,input,select,textarea,[role=button],[role=checkbox],[contenteditable=true]")) {
            if (c !== el && shown(c) && kind(c)) push("  " + describe(c, kind(c)));
          }
          return;
        }
      } else if (block) {
        flush();
        if (t === "li") line.push("•");
      }

      if (t === "iframe") {
        flush();
        let doc = null;
        try { doc = el.contentDocument; } catch (_) { doc = null; }
        if (doc && doc.body) { push(`--- frame ${q(clip(el.title || el.name || el.src || "", 80))} ---`); walk(doc.body); flush(); push("--- end frame ---"); }
        else push(`(a frame from another site is not read: ${clip(el.src || "", 100)})`);
        return;
      }
      if (el.shadowRoot) for (const c of el.shadowRoot.childNodes) walk(c);
      for (const c of el.childNodes) walk(c);
      if (block || head) flush();
      if (isDialog) { flush(); push("=== end dialog ==="); }
    }

    let root = document.body;
    if (rootRef === "dialog") {
      const ds = [...document.querySelectorAll("[role=dialog],[role=alertdialog],dialog[open]")].filter(shown);
      root = ds[ds.length - 1] || null;
      if (!root) return { lines: ["(no dialog is open)"], truncated: false };
    } else if (rootRef) {
      root = S.els[Number(rootRef)];
      if (!root || !root.isConnected) throw new Error(`ref ${rootRef} is gone; take a new snapshot`);
    }
    walk(root);
    flush();
    return { lines: out, truncated: cut };
  }

  function el(ref) {
    const e = S.els[Number(ref)];
    if (!e || !e.isConnected) throw new Error(`ref ${ref} is not on the page any more; take a new snapshot`);
    return e;
  }

  // where to click: the element's centre in the top page's coordinates, scrolled into view first
  function point(ref) {
    const e = el(ref);
    e.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    let r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0 && e.parentElement) r = e.parentElement.getBoundingClientRect();
    let x = r.left + r.width / 2, y = r.top + r.height / 2;
    let w = win(e), probe = w.document.elementFromPoint(x, y);
    const covered = probe && probe !== e && !e.contains(probe) && !(probe.contains && probe.contains(e)) ? describeShort(probe) : "";
    while (w !== w.top && w.frameElement) {   // inside a same-site frame: add the frame's offset
      const fr = w.frameElement.getBoundingClientRect();
      x += fr.left; y += fr.top; w = w.parent;
    }
    return { x, y, covered };
  }

  function describeShort(e) {
    const k = kind(e) || tag(e);
    return `${k} ${q(clip(name(e, k), 60))}`;
  }

  function focus(ref) {
    const e = el(ref);
    e.scrollIntoView({ block: "center", behavior: "instant" });
    e.focus();
    if ((tag(e) === "input" || tag(e) === "textarea") && typeof e.select === "function") {
      try { e.select(); } catch (_) { /* some input types can't be selected */ }
    }
    return { focused: e.ownerDocument.activeElement === e || e.contains(e.ownerDocument.activeElement) };
  }

  // a native <select>: pick the option by its text (or value), the way the site's own code hears it
  function choose(ref, wanted) {
    const e = el(ref);
    if (tag(e) !== "select") throw new Error(`ref ${ref} is not a native select; click it and pick the option instead`);
    const w = norm(wanted).toLowerCase();
    const opt = [...e.options].find((o) => norm(o.text).toLowerCase() === w || o.value.toLowerCase() === w)
      || [...e.options].find((o) => norm(o.text).toLowerCase().includes(w));
    if (!opt) throw new Error(`no option ${q(wanted)}; the options are: ${[...e.options].map((o) => norm(o.text)).join(" | ")}`);
    const setter = Object.getOwnPropertyDescriptor(win(e).HTMLSelectElement.prototype, "value").set;
    setter.call(e, opt.value);
    e.dispatchEvent(new (win(e).Event)("input", { bubbles: true }));
    e.dispatchEvent(new (win(e).Event)("change", { bubbles: true }));
    return { value: norm(opt.text) };
  }

  function read(ref) {
    const e = el(ref);
    const k = kind(e) || "element";
    return { line: describe(e, k), value: valueOf(e) };
  }

  // what an action is about to touch, in words a person reads in the chat: `button "Add position"`
  function target(ref) {
    const e = el(ref);
    const k = kind(e) || tag(e);
    return `${k} ${q(clip(name(e, k), 80))}`;
  }

  // what a recorded read route's script gets (autofill): reading helpers; the script only reads, apart
  // from opening "see more" and scrolling so that lazy lists load
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const helpers = {
    sleep,
    text: (e) => (e ? String(e.innerText || e.textContent || "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim() : ""),
    all: (sel, root = document) => [...root.querySelectorAll(sel)],
    one: (sel, root = document) => root.querySelector(sel),
    async waitFor(sel, ms = 10000) {
      const end = Date.now() + ms;
      while (Date.now() < end) { const e = document.querySelector(sel); if (e) return e; await sleep(200); }
      return null;
    },
    async scrollToEnd(rounds = 10) {
      let h = -1;
      for (let i = 0; i < rounds && document.documentElement.scrollHeight !== h; i++) {
        h = document.documentElement.scrollHeight;
        window.scrollTo(0, h);
        await sleep(500);
      }
      window.scrollTo(0, 0);
    },
    // the part of the page under a heading whose text matches (a string or a RegExp)
    section(heading, root = document) {
      const ok = (s) => (heading instanceof RegExp ? heading.test(s) : norm(s).toLowerCase() === String(heading).toLowerCase());
      const hd = [...root.querySelectorAll("h1,h2,h3,h4,[role=heading]")].find((x) => ok(norm(x.innerText || x.textContent)));
      return hd ? hd.closest("section") || hd.parentElement : null;
    },
  };

  globalThis.__rs = { snapshot, point, focus, choose, read, el, target, helpers, kind: (ref) => kind(el(ref)), rich: (ref) => !!el(ref).isContentEditable };
})();
