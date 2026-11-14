import { defineConfig } from '@playwright/test';

// No webServer: tests launch the built app through Electron.
// - app: the e2e build in out/ (`pnpm e2e` builds it with `--mode e2e`, which seeds the demo crew).
// - real: the real-mode demo (e2e/real.spec.ts), only through `pnpm e2e:real`, which starts the Docker stack and builds
//   with `--mode e2e-real`. Local only, not CI.
// - packaged: the production binary from `electron-builder --dir` (`pnpm e2e:packaged`).
export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  reporter: [['list']],
  use: { trace: 'retain-on-failure' },
  projects: [
    { name: 'app', testIgnore: ['packaged.spec.ts', 'real.spec.ts'] },
    { name: 'real', testMatch: 'real.spec.ts', timeout: 15 * 60_000 },
    { name: 'packaged', testMatch: 'packaged.spec.ts' },
  ],
});
