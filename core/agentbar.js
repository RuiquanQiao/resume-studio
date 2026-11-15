// The bar Resume Studio shows on a page that the AI works on (core/handoff.py drives it).
//
// It runs in an isolated world of its own ("resume-studio-bar"): the page's scripts never see its variables, and its
// elements sit in a closed shadow root, so the site can't read or restyle them. It shows who drives the tab (the AI,
// or the user, or the user signing in), what the AI is doing, and the buttons to take over, hand back and stop. It
// also notes the user's own clicks and keys on the page (trusted events only; Resume Studio tells its own input apart
// by time), which hand the tab to the user at once. Nothing here acts on the page: Resume Studio polls `sync`, which
// draws the state it is given and returns what happened since the last poll.
(() => {
  if (globalThis.__rsBar) return globalThis.__rsBar;
  const queue = [];
  const push = (ev) => { queue.push(ev); if (queue.length > 40) queue.shift(); };

  const host = document.createElement("div");
  host.style.cssText = "all: initial; position: fixed; inset: 0; pointer-events: none; z-index: 2147483647; display: block;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    :host { all: initial; }
    * { box-sizing: border-box; }
    .glow { position: fixed; inset: 0; pointer-events: none; opacity: 0; transition: opacity .25s ease;
      box-shadow: inset 0 0 0 2px rgba(61, 90, 254, .9), inset 0 0 26px rgba(61, 90, 254, .32); }
    .glow.on { opacity: 1; animation: rs-breathe 2.4s ease-in-out infinite; }
    @keyframes rs-breathe { 0%, 100% { opacity: 1; } 50% { opacity: .55; } }
    .cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%;
      border: 2px solid #3d5afe; background: rgba(61, 90, 254, .18); pointer-events: none; opacity: 0; }
    .cursor.go { animation: rs-tap .65s ease-out; }
    @keyframes rs-tap { 0% { opacity: 1; transform: scale(.55); } 100% { opacity: 0; transform: scale(1.35); } }
    /* at the bottom, in the middle: a site's own navigation and its sign-in link sit at the top, and a sign-in
       dialog in the middle. Centred by auto margins (not left 50% + translate, which leaves it half the width) */
    .bar { position: fixed; bottom: 16px; left: 12px; right: 12px; margin: 0 auto; width: fit-content;
      pointer-events: auto; display: none; row-gap: 6px;
      align-items: center; column-gap: 12px; max-width: min(760px, calc(100vw - 24px)); padding: 6px 6px 6px 12px;
      border-radius: 10px; background: rgba(27, 30, 42, .95); color: #f2f3f8;
      box-shadow: 0 8px 28px rgba(0, 0, 0, .3), 0 0 0 1px rgba(255, 255, 255, .09);
      font: 500 13px/1.45 "Noto Sans SC", "Microsoft YaHei UI", "PingFang SC", system-ui, -apple-system, "Segoe UI", sans-serif;
      letter-spacing: 0; text-align: left; -webkit-font-smoothing: antialiased; user-select: none; cursor: grab; }
    .bar.show { display: flex; }
    .bar.moved { right: auto; bottom: auto; margin: 0; }
    .bar.dragging { cursor: grabbing; }
    .lead { display: flex; align-items: flex-start; gap: 9px; min-width: 0; flex: 1 1 auto; }
    /* a narrow page: the words take the whole row and the buttons go under them, rather than squeezing them */
    @media (max-width: 560px) {
      .bar { flex-wrap: wrap; }
      .lead { flex-basis: 100%; }
    }
    .dot { flex: none; width: 8px; height: 8px; margin-top: 6px; border-radius: 50%; background: #6f86ff; }
    .bar[data-mode="ai"] .dot.acting { animation: rs-pulse 1.4s ease-in-out infinite; }
    @keyframes rs-pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(111, 134, 255, .55); } 50% { box-shadow: 0 0 0 5px rgba(111, 134, 255, 0); } }
    .bar[data-mode="user"] .dot { background: transparent; box-shadow: inset 0 0 0 2px #c5c9dc; }
    .bar[data-mode="signin"] .dot { background: #f5b14c; }
    .words { display: flex; flex-direction: column; min-width: 0; }
    .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bar[data-mode="signin"] .text, .bar[data-mode="user"] .text { white-space: normal; display: -webkit-box;
      -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow-wrap: anywhere; }
    .sub { font-size: 12px; font-weight: 400; color: #a9aec4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .sub:empty { display: none; }
    .sub.good { color: #8fe0b0; }
    .acts { display: flex; gap: 6px; flex: none; margin-left: auto; }
    button { all: unset; box-sizing: border-box; height: 28px; padding: 0 11px; border-radius: 6px; cursor: pointer;
      font: 500 12.5px/28px "Noto Sans SC", "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif; white-space: nowrap;
      color: #f2f3f8; background: rgba(255, 255, 255, .1); transition: background .12s ease; }
    button:hover { background: rgba(255, 255, 255, .18); }
    button.primary { background: #3d5afe; color: #fff; }
    button.primary:hover { background: #5570ff; }
    button:focus-visible { outline: 2px solid #8fa2ff; outline-offset: 2px; }
    button:disabled { opacity: .55; cursor: default; }
    .through { pointer-events: none !important; }
    @media (prefers-reduced-motion: reduce) {
      .glow.on, .bar[data-mode="ai"] .dot.acting { animation: none; }
      .cursor.go { animation-duration: .01s; }
    }
  </style>
  <div class="glow"></div>
  <div class="cursor"></div>
  <div class="bar" role="region">
    <div class="lead"><span class="dot"></span><span class="words"><span class="text" role="status"></span><span class="sub"></span></span></div>
    <div class="acts"></div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const bar = $(".bar"), glow = $(".glow"), cursor = $(".cursor"), text = $(".text"), sub = $(".sub"), acts = $(".acts");
  const dot = $(".dot");
  let state = {}, key = "", cursorSeq = 0, throughUntil = 0;

  const attach = () => {
    const parent = document.documentElement || document;
    if (host.parentNode !== parent) parent.appendChild(host);
  };

  // ---- the user's own input on the page: it hands the tab to them (handoff.py decides) ----
  const mine = (e) => e.composedPath().includes(host);
  addEventListener("pointerdown", (e) => { if (e.isTrusted && !mine(e)) push({ t: "input", kind: "pointer", at: Date.now() }); }, true);
  addEventListener("keydown", (e) => { if (e.isTrusted && !mine(e)) push({ t: "input", kind: "key", at: Date.now() }); }, true);

  // ---- dragging the bar out of the way: where it was put is kept for the tab (the next page shows it there) ----
  let drag = null;
  bar.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest && e.target.closest("button")) return;
    const r = bar.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, id: e.pointerId };
    bar.setPointerCapture(e.pointerId);
    bar.classList.add("dragging");
  });
  const place = (x, y) => {
    const r = bar.getBoundingClientRect();
    x = Math.max(4, Math.min(innerWidth - r.width - 4, x));
    y = Math.max(4, Math.min(innerHeight - r.height - 4, y));
    bar.classList.add("moved");
    bar.style.left = x + "px";
    bar.style.top = y + "px";
    return [x, y];
  };
  bar.addEventListener("pointermove", (e) => { if (drag && e.pointerId === drag.id) place(e.clientX - drag.dx, e.clientY - drag.dy); });
  const drop = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    bar.classList.remove("dragging");
    const r = bar.getBoundingClientRect();
    push({ t: "pos", x: Math.round(r.left), y: Math.round(r.top) });
  };
  bar.addEventListener("pointerup", drop);
  bar.addEventListener("pointercancel", drop);
  bar.addEventListener("dblclick", (e) => {   // back to its place at the bottom
    if (e.target.closest && e.target.closest("button")) return;
    bar.classList.remove("moved");
    bar.style.left = bar.style.top = "";
    push({ t: "pos", x: null, y: null });
  });

  const draw = () => {
    const s = state;
    bar.classList.toggle("show", !!s.visible);
    glow.classList.toggle("on", !!(s.visible && s.glow));
    if (!s.visible) return;
    bar.dataset.mode = s.mode || "ai";
    dot.classList.toggle("acting", !!s.acting);
    if (text.textContent !== (s.text || "")) text.textContent = s.text || "";
    if (sub.textContent !== (s.sub || "")) sub.textContent = s.sub || "";
    sub.classList.toggle("good", !!s.sub_good);
    bar.setAttribute("aria-label", s.label || "Resume Studio");
    const want = JSON.stringify(s.buttons || []);
    if (acts.dataset.key !== want) {
      acts.dataset.key = want;
      acts.replaceChildren(...(s.buttons || []).map((b) => {
        const el = document.createElement("button");
        el.type = "button";
        el.textContent = b.label;
        el.dataset.id = b.id;
        if (b.title) el.title = b.title;
        if (b.primary) el.className = "primary";
        el.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          el.disabled = true;   // the next state redraws it
          push({ t: "button", id: b.id, at: Date.now() });
        });
        return el;
      }));
    }
    if (s.pos && !drag) place(s.pos.x, s.pos.y);
    else if (!s.pos && !drag && bar.classList.contains("moved")) { bar.classList.remove("moved"); bar.style.left = bar.style.top = ""; }
  };

  const api = {
    // Resume Studio's poll: draw this state, give back what happened since the last poll
    sync(next) {
      attach();
      if (next) {
        const k = JSON.stringify(next);
        if (k !== key) { key = k; state = next; draw(); }
        if (next.cursor && next.cursor.seq !== cursorSeq) {
          cursorSeq = next.cursor.seq;
          api.tap(next.cursor.x, next.cursor.y);
        }
      }
      bar.classList.toggle("through", Date.now() < throughUntil);
      return queue.splice(0, queue.length);
    },
    // the AI is about to click or read where the bar is: let its input and its hit tests pass through for a moment
    through(ms) {
      throughUntil = Date.now() + (ms || 1200);
      bar.classList.add("through");
      setTimeout(() => bar.classList.toggle("through", Date.now() < throughUntil), (ms || 1200) + 20);
      return true;
    },
    // where the AI clicks: a ring that fades, so the user sees what it pressed
    tap(x, y) {
      if (!state.visible) return false;
      cursor.style.left = x + "px";
      cursor.style.top = y + "px";
      cursor.classList.remove("go");
      void cursor.offsetWidth;
      cursor.classList.add("go");
      return true;
    },
    // out of a screenshot the AI takes (it shows the page, not Resume Studio's bar)
    hide(on) { host.style.visibility = on ? "hidden" : "visible"; return true; },
    // where the bar is and whether its words fit (the layout tests measure it)
    metrics() {
      const r = bar.getBoundingClientRect();
      return { shown: bar.classList.contains("show"), left: r.left, top: r.top, right: r.right, bottom: r.bottom,
        vw: innerWidth, vh: innerHeight, textCut: text.scrollWidth > text.clientWidth + 1 || text.scrollHeight > text.clientHeight + 1,
        buttonsCut: [...acts.children].some((b) => b.scrollWidth > b.clientWidth + 1), mode: bar.dataset.mode || "",
        buttons: [...acts.children].map((b) => { const q = b.getBoundingClientRect();
          return { id: b.dataset.id, x: q.left + q.width / 2, y: q.top + q.height / 2 }; }) };
    },
  };
  attach();
  globalThis.__rsBar = api;
  return api;
})();
