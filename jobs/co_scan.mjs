// Run career-ops' own job providers for the careers sites Resume Studio's tracker knows (docs/JOB_HUNT.md, 2026-10-18).
//
//   node jobs/co_scan.mjs <career-ops folder> <entries.json> [--detect]
//
// entries.json: {"entries": [{"key": "C1#0", "name": "ByteDance", "url": "https://jobs.bytedance.com"}]}
// Prints one JSON line: {"results": [{"key", "url", "provider": "feishu-jobs" | null, "jobs": [...] | "error": "..."}]}
//
// Nothing here is career-ops' code: it loads career-ops' providers/*.mjs from its own install (through its
// providers/_registry.mjs, the same way its scan.mjs does) and calls them, so career-ops' updates keep
// flowing in. Moka, Feishu (ByteDance), Tencent, Meituan, Alibaba, Greenhouse, Lever, Ashby, Workday and
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

const results = [];
for (const e of entries) {
  const entry = { name: e.name || '', careers_url: e.url };
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
  try {
    const ctx = { ...makeHttpCtx(), maxPages: 40, includeUndated: true };
    // A Feishu tenant has a portal per kind of hiring, named by the first part of its address
    // (<tenant>.jobs.feishu.cn/123456 is campus, /index is experienced). The search API answers for the portal
    // named in a "website-path" header and for none without it: career-ops' provider sends none, so a tenant's
    // campus portal read as empty (2026-10-21). Its roles' pages live under that portal too.
    const portal = id === 'feishu-jobs' ? feishuPortal(e.url) : '';
    if (portal) {
      const fetchJson = ctx.fetchJson;
      ctx.fetchJson = (url, init = {}) => fetchJson(url, { ...init, headers: { ...(init.headers || {}), 'website-path': portal } });
    }
    let jobs = await r.provider.fetch(entry, ctx);
    if (portal) jobs = (jobs || []).map((j) => ({ ...j, url: String(j.url || '').replace('/index/position/', `/${portal}/position/`) }));
    results.push({ key: e.key, url: e.url, provider: id, jobs: (jobs || []).filter((j) => j && j.title && j.url).map(pick) });
  } catch (err) {
    results.push({ key: e.key, url: e.url, provider: id, error: String(err && err.message || err).slice(0, 400) });
  }
}
quiet(JSON.stringify({ results }));
