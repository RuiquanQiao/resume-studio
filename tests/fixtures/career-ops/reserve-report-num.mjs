// Fixture: `node reserve-report-num.mjs --count N` prints the next free numbers ("004" or "004-005"),
// past the tracker's rows and earlier reservations, and leaves a sentinel per number like career-ops'
// allocator does; `--release 004-005` removes them.
import { existsSync, mkdirSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const root = dirname(fileURLToPath(import.meta.url));
const reports = join(root, 'reports');
mkdirSync(reports, { recursive: true });
const args = process.argv.slice(2);
const pad = (n) => String(n).padStart(3, '0');
if (args[0] === '--release') {
  const [a, b] = args[1].split('-').map(Number);
  for (let n = a; n <= (b || a); n++) { const f = join(reports, `${pad(n)}-RESERVED.md`); if (existsSync(f)) unlinkSync(f); }
  process.exit(0);
}
const count = args[0] === '--count' ? Number(args[1]) : 1;
const tracker = readFileSync(join(root, 'data', 'applications.md'), 'utf8');
const used = [...tracker.matchAll(/^\|\s*(\d+)\s*\|/gm)].map((m) => Number(m[1]));
const files = readdirSync(reports).map((f) => Number((/^(\d+)-/.exec(f) || [])[1] || 0));
const first = Math.max(0, ...used, ...files) + 1;
for (let n = first; n < first + count; n++) writeFileSync(join(reports, `${pad(n)}-RESERVED.md`), '');
console.log(count > 1 ? `${pad(first)}-${pad(first + count - 1)}` : pad(first));
