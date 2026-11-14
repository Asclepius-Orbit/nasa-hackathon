// postinstall: fetch the Electron binary, which electron@45 no longer downloads on install.
// Idempotent (electron's install.js exits early when the binary is present).
// Skipped when ELECTRON_SKIP_BINARY_DOWNLOAD is set (Docker gate, CI: no app is launched there).
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';

if (process.env.ELECTRON_SKIP_BINARY_DOWNLOAD) {
  console.log('ensure-electron: skipped (ELECTRON_SKIP_BINARY_DOWNLOAD is set)');
  process.exit(0);
}

const require = createRequire(import.meta.url);
const installer = require.resolve('electron/install.js');
const result = spawnSync(process.execPath, [installer], { stdio: 'inherit' });
process.exit(result.status ?? 1);
