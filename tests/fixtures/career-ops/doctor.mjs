// Fixture: the shape of career-ops' `node doctor.mjs --json` (read-only setup check).
import { existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const root = process.env.CAREER_OPS_ROOT || dirname(fileURLToPath(import.meta.url));
const missing = ['cv.md', 'config/profile.yml', 'modes/_profile.md', 'portals.yml'].filter((p) => !existsSync(join(root, p)));
console.log(JSON.stringify({ onboardingNeeded: missing.length > 0, missing, unpersonalized: [], warnings: [], autoCopied: [] }));
