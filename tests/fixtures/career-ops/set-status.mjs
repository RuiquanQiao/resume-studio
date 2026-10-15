// Fixture: `node set-status.mjs <report#> <State>` rewrites that row's Status cell, like career-ops'
// canonical writer (which also validates against states.yml, locks and logs the transition).
import { readFileSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const [num, state] = process.argv.slice(2);
const file = join(dirname(fileURLToPath(import.meta.url)), 'data', 'applications.md');
const lines = readFileSync(file, 'utf8').split('\n');
const head = lines.find((l) => l.startsWith('| #')).split('|').map((c) => c.trim());
const col = head.indexOf('Status');
let hit = false;
const out = lines.map((l) => {
  const cells = l.split('|');
  if (cells.length > col && cells[1] && cells[1].trim() === num) { cells[col] = ` ${state} `; hit = true; return cells.join('|'); }
  return l;
});
if (!hit) { console.error(`No row #${num}`); process.exit(1); }
writeFileSync(file, out.join('\n'));
console.log(`#${num} -> ${state}`);
