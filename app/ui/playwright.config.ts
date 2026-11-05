import { defineConfig } from '@playwright/test';

// No webServer: tests launch the built app (out/) through Electron's `_electron.launch`.
export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  reporter: [['list']],
  use: { trace: 'retain-on-failure' },
});
