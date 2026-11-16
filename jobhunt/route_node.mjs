// Run a recorded scan route's scripts with no browser (jobhunt/replay.py, 2026-11-16).
//
//   node --permission --allow-fs-read=<this file> --allow-fs-read=<job.json> jobhunt/route_node.mjs <job.json>
//
// job.json: {"url": "<the page the scripts were recorded on>", "scripts": ["async (h) => ({postings: [...]})", ...],
//            "timeout_ms": 120000, "request_ms": 30000}
// Prints one JSON line: {"results": [{"ok": <the script's answer>} | {"error": "...", "needs_page": true?}]}
//
// Most routes the AI records call the site's own JSON API with fetch (304 of 343 pages, 2026-11-16): that needs
// no page drawn. Each script runs here as it would on the page: `location` is the page's address, fetch resolves
// a relative address against it and sends the page as Referer (and Origin for a POST), and the cookies the page
// itself sets on opening go with every request to its site, as a browser's would. A script that touches the page
// (document, DOMParser, XMLHttpRequest, h.waitFor) fails here with needs_page, and the scan reads it in the
// browser instead.
//
// The boundary is node's permission model: no file writes, no reads beyond this file and the job, no child
// processes, no workers, no addons. The vm context only keeps the scripts' globals to the shims below (no
// `process`, `require` or `import` by name); it is not a security boundary by itself.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const page = new URL(job.url);
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const requestMs = job.request_ms || 30000;
const jar = new Map();   // the page's site's cookies, as the browser would keep them

class NeedsPage extends Error {}

function keep(res) {
  const all = typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : [];
  for (const c of all) {
    const kv = c.split(';')[0];
    const i = kv.indexOf('=');
    if (i > 0) jar.set(kv.slice(0, i).trim(), kv.slice(i + 1).trim());
  }
}

function sameSite(u) {
  const a = u.hostname.split('.').slice(-2).join('.');
  const b = page.hostname.split('.').slice(-2).join('.');
  return a === b;
}

async function pageFetch(input, init = {}) {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input && input.url;
  const url = new URL(String(raw), page);
  const headers = new Headers(init.headers || {});
  const method = String(init.method || 'GET').toUpperCase();
  if (!headers.has('user-agent')) headers.set('user-agent', UA);
  if (!headers.has('referer')) headers.set('referer', page.href);
  if (!headers.has('accept-language')) headers.set('accept-language', 'zh-CN,zh;q=0.9,en;q=0.8');
  if (method !== 'GET' && method !== 'HEAD' && !headers.has('origin')) headers.set('origin', page.origin);
  if (sameSite(url) && jar.size && !headers.has('cookie')) headers.set('cookie', [...jar].map(([k, v]) => `${k}=${v}`).join('; '));
  const { credentials, mode, cache, referrer, referrerPolicy, ...rest } = init;   // browser-only options
  // a site across the Great Firewall often fails to connect once and answers the next time (undici gives a
  // connection 10 s): a connection that never opened is tried again, as the browser's own retries would
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, { ...rest, method, headers, redirect: 'follow', signal: AbortSignal.timeout(requestMs) });
      keep(res);
      return res;
    } catch (e) {
      const code = e && e.cause && e.cause.code;
      const connect = /UND_ERR_CONNECT_TIMEOUT|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|UND_ERR_SOCKET/.test(String(code));
      if (!connect || attempt >= 3) throw e;
      await new Promise((r) => setTimeout(r, [1000, 3000, 8000][attempt]));
    }
  }
}

const noPage = (what) => { throw new NeedsPage(`${what} needs the page drawn`); };
const store = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k),
    clear: () => m.clear(), key: (i) => [...m.keys()][i] ?? null, get length() { return m.size; } };
};
const loc = {
  href: page.href, origin: page.origin, protocol: page.protocol, host: page.host, hostname: page.hostname, port: page.port,
  pathname: page.pathname, search: page.search, hash: page.hash, toString() { return page.href; },
  assign: () => noPage('location.assign'), replace: () => noPage('location.replace'), reload: () => noPage('location.reload'),
};
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.min(Number(ms) || 0, 10000)));
const helpers = {
  sleep, text: () => noPage('h.text'), all: () => noPage('h.all'), one: () => noPage('h.one'), waitFor: () => noPage('h.waitFor'),
  scrollToEnd: () => noPage('h.scrollToEnd'), section: () => noPage('h.section'),
};
const trap = (what) => new Proxy({}, { get: (_, k) => (k === Symbol.toPrimitive ? () => '' : noPage(`${what}.${String(k)}`)) });

const sandbox = {
  fetch: pageFetch, location: loc, URL, URLSearchParams, Headers, Request, Response, FormData, Blob, TextEncoder, TextDecoder,
  AbortController, AbortSignal, setTimeout, clearTimeout, setInterval, clearInterval, console: { log() {}, warn() {}, error() {} },
  atob, btoa, structuredClone, crypto: globalThis.crypto, performance: { now: () => performance.now(), timeOrigin: performance.timeOrigin },
  encodeURIComponent, decodeURIComponent, encodeURI, decodeURI, escape, unescape,
  navigator: { userAgent: UA, language: 'zh-CN', languages: ['zh-CN', 'zh', 'en'] },
  localStorage: store(), sessionStorage: store(),
  document: trap('document'),
  DOMParser: function DOMParser() { noPage('DOMParser'); },
  XMLHttpRequest: function XMLHttpRequest() { noPage('XMLHttpRequest'); },
};
// scrolling only loads a drawn list, and there is none here: a no-op, so a script that scrolls before its API calls runs
sandbox.scrollTo = sandbox.scroll = sandbox.scrollBy = () => {};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox, { codeGeneration: { strings: false, wasm: false } });

function withTimeout(p, ms) {
  let t;
  return Promise.race([p, new Promise((_, rej) => { t = setTimeout(() => rej(new Error(`the script took over ${ms / 1000} s`)), ms); })])
    .finally(() => clearTimeout(t));
}

// the page opened first, as in the browser: what it sets (a session cookie, a CSRF token) the scripts' calls carry
try { await (await pageFetch(page.href)).arrayBuffer(); } catch { /* the scripts' own calls say what is wrong */ }

const results = [];
for (const script of job.scripts || []) {
  try {
    const fn = vm.runInContext(`(${script}\n)`, ctx, { timeout: 5000 });
    if (typeof fn !== 'function') throw new Error('the script must be a function: async (h) => ({...})');
    const out = await withTimeout(Promise.resolve(fn(helpers)), job.timeout_ms || 120000);
    results.push({ ok: JSON.parse(JSON.stringify(out === undefined ? null : out)) });
  } catch (e) {
    const needs = e instanceof NeedsPage || /is not defined|needs the page/.test(String(e && e.message));
    const why = e && e.cause ? ` (${e.cause.code || ''} ${e.cause.message || e.cause})` : '';
    results.push({ error: (String((e && e.message) || e) + why).slice(0, 300), ...(needs ? { needs_page: true } : {}) });
  }
}
process.stdout.write(JSON.stringify({ results }) + '\n');
process.exit(0);
