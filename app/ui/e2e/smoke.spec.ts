// Scaffold smoke test: launches the built app from out/ (run `pnpm build` first, or use `pnpm e2e`).
import { _electron as electron, expect, test } from '@playwright/test';

test('scaffold smoke: app window shows the heading', async () => {
  const app = await electron.launch({ args: ['.'] });
  const window = await app.firstWindow();
  await expect(window.getByRole('heading', { name: 'Asclepius Orbit' })).toBeVisible();
  await app.close();
});
