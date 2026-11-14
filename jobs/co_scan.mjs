// Run career-ops' own job providers for the careers sites Resume Studio's tracker knows (docs/JOB_HUNT.md, 2026-10-18).
//
//   node jobs/co_scan.mjs <providers folder> <entries.json> [--detect]
//
// <providers folder> is jobs/vendor/career-ops (career-ops' providers copied into this repository, 2026-11-12).
//
// entries.json: {"entries": [{"key": "C1#0", "name": "ByteDance", "url": "https://jobs.bytedance.com"}]}
// Prints one JSON line: {"results": [{"key", "url", "provider": "feishu-jobs" | null, "jobs": [...] | "error": "..."}]}
//
// This file is not career-ops' code: it loads career-ops' providers/*.mjs (through its providers/_registry.mjs,
// the same way its scan.mjs does) and calls them. scripts/update_career_ops.py brings in a newer copy. Moka, Feishu (ByteDance), Tencent, Meituan, Alibaba, Greenhouse, Lever, Ashby, Workday and
// the rest are career-ops' (MIT, Santiago Fernández de Valderrama and contributors).
import { readFileSync } from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

const [coDir, inFile, flag] = process.argv.slice(2);
const detectOnly = flag === '--detect';
const load = (rel) => import(pathToFileURL(path.join(coDir, rel)).href);

const { loadProviders, resolveProvider } = await load('providers/_registry.mjs');
const { makeHttpCtx } = await load('providers/_http.mjs');
// providers log their own warnings; keep stdout for our one line of JSON
const quiet = console.log;
console.log = (...a) => console.error(...a);
const providers = await loadProviders(path.join(coDir, 'providers'));
const { entries = [] } = JSON.parse(readFileSync(inFile, 'utf8'));

const pick = (j) => ({
  title: String(j.title || '').slice(0, 200),
  url: String(j.url || ''),
  location: String(j.location || '').slice(0, 120),
  category: String(j.department || j.team || j.category || '').slice(0, 80),
  kind: String(j.employmentType || j.commitment || '').slice(0, 40),
  posted: typeof j.postedAt === 'number' && j.postedAt > 0 ? new Date(j.postedAt).toISOString().slice(0, 10) : '',
});

function feishuPortal(url) {
  try {
    const seg = new URL(url).pathname.split('/').filter(Boolean)[0] || '';
    return /^[\w-]{1,40}$/.test(seg) ? seg : '';
  } catch { return ''; }
}

// Moka's own lists also live at /campus_apply/<org>/<site> (and /social_apply/): the same site and API as
// /campus-recruitment/<org>/<site>, which is the only spelling career-ops' provider knows. 思摩尔国际's campus list
// (2026-11-04) was read as "no scan can read it" though its 53 roles come through the same API.
function providerUrl(url) {
  try {
    const u = new URL(url);
    if (!/(^|\.)mokahr\.com$/i.test(u.hostname)) return url;
    const m = u.pathname.match(/^\/(campus|social)_apply\/([^/]+)\/(\d+)\/?$/);
    return m ? `${u.origin}/${m[1]}-recruitment/${m[2]}/${m[3]}` : url;
  } catch { return url; }
}

// A provider whose page 7 fails mid-read returns the rows of pages 1 to 6 as if they were the whole list, and says
// so only in a warning (tencent, workday, lever, mokahr and most others: "keeping the 192 jobs collected so far",
// "truncated at max_pages=40"). A list read in part must never close the roles it didn't reach: 腾讯's
// careers.tencent.com stopped after 192 of about 2,000 roles and the scan closed the other 769 (2026-11-14).
// The warnings a provider prints while it reads are kept, and one that says the list is partial marks it so.
const PARTIAL = /truncat|keeping|kept|stopped|partial|incomplete|so far|more results|has more|may have more|not read|missing|exceed|ceiling|offset-clamped|\bcap\b|page \d+ (?:failed|fetch failed)|failed (?:on|after)|returned \d+ of \d+|no progress/i;
let heard = null;   // the warnings of the provider reading now, or null between reads
for (const name of ['error', 'warn']) {
  const write = console[name];
  console[name] = (...a) => {
    if (heard) heard.push(a.map(String).join(' '));
    write(...a);
  };
}
const partialOf = (lines) => lines.map((l) => l.trim()).find((l) => !l.startsWith('ℹ') && PARTIAL.test(l)) || '';

// Each site's wait comes from Resume Studio (jobhunt/patience.py: how far the site is, how it answered before),
// never the providers' own 10 s. A request the site didn't answer in time is asked again after a pause; one it
// answered (an HTTP error) is the site's answer. `heardFrom` keeps the slowest answer and whether one was missed.
function patient(ctx, e, heardFrom) {
  const tries = Math.max(1, Number(e.tries) || 1);
  const waits = Array.isArray(e.waits_ms) ? e.waits_ms : [];
  for (const m of ['fetchJson', 'fetchText', 'fetchResponse']) {
    const call = ctx[m];
    if (typeof call !== 'function') continue;
    ctx[m] = async (url, opts = {}) => {
      for (let i = 0; ; i++) {
        const start = Date.now();
        try {
          const got = await call(url, opts);
          heardFrom.took = Math.max(heardFrom.took, (Date.now() - start) / 1000);
          return got;
        } catch (err) {
          if (err && typeof err === 'object' && typeof err.status === 'number') {
            heardFrom.took = Math.max(heardFrom.took, (Date.now() - start) / 1000);
            throw err;
          }
          heardFrom.missed = true;
          if (i >= tries - 1) throw err;
          await new Promise((r) => setTimeout(r, waits[Math.min(i, waits.length - 1)] || 0));
        }
      }
    };
  }
  return ctx;
}

const results = [];
for (const e of entries) {
  // max_pages on the entry, one under the context's cap: a provider then says when the cap, not the list, ended
  // its read (tencent stays quiet when the cap is the context's alone)
  const entry = { name: e.name || '', careers_url: providerUrl(e.url), max_pages: 40 };
  let r = null;
  try {
    r = resolveProvider(entry, providers, { skipIds: ['local-parser'] });
  } catch (err) {
    r = { error: String(err && err.message || err) };
  }
  if (!r) { results.push({ key: e.key, url: e.url, provider: null }); continue; }
  if (r.error) { results.push({ key: e.key, url: e.url, provider: null, error: r.error }); continue; }
  const id = r.provider.id;
  if (detectOnly) { results.push({ key: e.key, url: e.url, provider: id }); continue; }
  const heardFrom = { took: 0, missed: false };
  try {
    // the timeout for providers that call _http.mjs's fetchers themselves rather than through ctx
    if (Number(e.timeout_ms) > 0) process.env.CAREER_OPS_HTTP_TIMEOUT_MS = String(e.timeout_ms);
    else delete process.env.CAREER_OPS_HTTP_TIMEOUT_MS;
    const ctx = patient({ ...makeHttpCtx(), maxPages: 41, includeUndated: true }, e, heardFrom);
    // A Feishu tenant has a portal per kind of hiring, named by the first part of its address
    // (<tenant>.jobs.feishu.cn/123456 is campus, /index is experienced). The search API answers for the portal
    // named in a "website-path" header and for none without it: career-ops' provider sends none, so a tenant's
    // campus portal read as empty (2026-10-21). Its roles' pages live under that portal too.
    const portal = id === 'feishu-jobs' ? feishuPortal(e.url) : '';
    if (portal) {
      const fetchJson = ctx.fetchJson;
      ctx.fetchJson = (url, init = {}) => fetchJson(url, { ...init, headers: { ...(init.headers || {}), 'website-path': portal } });
    }
    heard = [];
    let jobs = await r.provider.fetch(entry, ctx);
    if (portal) jobs = (jobs || []).map((j) => ({ ...j, url: String(j.url || '').replace('/index/position/', `/${portal}/position/`) }));
    const partial = partialOf(heard);
    results.push({ key: e.key, url: e.url, provider: id, jobs: (jobs || []).filter((j) => j && j.title && j.url).map(pick),
                   ...(partial ? { partial: partial.replace(/^[^\w"[]+/u, '').slice(0, 300) } : {}),
                   took: Math.round(heardFrom.took * 10) / 10, missed: heardFrom.missed });
  } catch (err) {
    results.push({ key: e.key, url: e.url, provider: id, error: String(err && err.message || err).slice(0, 400),
                   took: Math.round(heardFrom.took * 10) / 10, missed: heardFrom.missed });
  } finally {
    heard = null;
  }
}
quiet(JSON.stringify({ results }));
