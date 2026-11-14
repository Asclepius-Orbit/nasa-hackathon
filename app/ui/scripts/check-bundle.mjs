// Production-bundle guard: dev-only code (dev server, dev CSP, DevTools, demo crew) must be compiled out of
// out/main/index.js, not just switched off at runtime. Run after a production `pnpm build` (`pnpm check:bundle`).
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const FORBIDDEN = ['Elena Ruiz', 'Marc Dubois', 'ELECTRON_RENDERER_URL', 'unsafe-inline', 'openDevTools', 'isPackaged'];

const bundlePath = join(import.meta.dirname, '../out/main/index.js');
const bundle = await readFile(bundlePath, 'utf8');
const found = FORBIDDEN.filter((needle) => bundle.includes(needle));

if (found.length > 0) {
  console.error(`check:bundle FAILED: ${bundlePath} contains dev-only code: ${found.map((s) => JSON.stringify(s)).join(', ')}`);
  process.exit(1);
}
console.log(`check:bundle ok: none of ${FORBIDDEN.length} dev-only markers in ${bundlePath}`);
