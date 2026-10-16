// Fixture: `node merge-tracker.mjs` merges batch/tracker-additions/*.tsv (a header row of labels, then one
// data row) into the tracker like career-ops' writer: it dedups by URL and by company + role (the later
// one updates the earlier row), then moves the files to merged/.
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const root = dirname(fileURLToPath(import.meta.url));
const dir = join(root, 'batch', 'tracker-additions');
if (!existsSync(dir)) process.exit(0);
const file = join(root, 'data', 'applications.md');
const lines = readFileSync(file, 'utf8').split('\n');
const head = lines.find((l) => l.startsWith('| #')).split('|').slice(1, -1).map((c) => c.trim().toLowerCase());
const cellsOf = (l) => l.split('|').slice(1, -1).map((c) => c.trim());
const isRow = (l) => /^\|\s*\d+\s*\|/.test(l);
const key = { '#': 'num', num: 'num', date: 'date', company: 'company', role: 'role', score: 'score', status: 'status', pdf: 'pdf', report: 'report', notes: 'notes', url: 'url' };
mkdirSync(join(dir, 'merged'), { recursive: true });
for (const f of readdirSync(dir).filter((x) => x.endsWith('.tsv')).sort()) {
  const [labels, values] = readFileSync(join(dir, f), 'utf8').trim().split('\n').map((l) => l.split('\t'));
  const add = Object.fromEntries(labels.map((l, i) => [l.trim().toLowerCase(), (values[i] || '').trim()]));
  const row = '| ' + head.map((h) => add[key[h] === 'num' ? 'num' : key[h]] ?? '').join(' | ') + ' |';
  const at = lines.findIndex((l) => {
    if (!isRow(l)) return false;
    const c = Object.fromEntries(head.map((h, i) => [key[h], cellsOf(l)[i]]));
    return (add.url && c.url === add.url) || (c.company === add.company && c.role === add.role);
  });
  if (at >= 0) {
    const num = cellsOf(lines[at])[0];
    lines[at] = row.replace(/^\|\s*\d+\s*\|/, `| ${num} |`);
    console.log(`Update: #${num}`);
  } else {
    const last = lines.reduce((k, l, i) => (isRow(l) ? i : k), lines.findIndex((l) => l.startsWith('|---')));
    lines.splice(last + 1, 0, row);
    console.log(`Add #${add.num}`);
  }
  renameSync(join(dir, f), join(dir, 'merged', f));
}
writeFileSync(file, lines.join('\n'));
