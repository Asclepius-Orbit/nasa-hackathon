// Shared launch helper for the e2e specs: every test gets its own throwaway profile.
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { _electron as electron, expect, type ElectronApplication, type Page } from '@playwright/test';

export interface Launched {
  app: ElectronApplication;
  page: Page;
  userDataDir: string;
  close: () => Promise<void>;
}

/**
 * Launches the built app (out/) on a fresh --user-data-dir: default settings, dev crew seeds, no lockout state.
 * `env` is added to the process environment (the real-mode spec passes the backend token and URL this way).
 */
export async function launch(env: Readonly<Record<string, string>> = {}): Promise<Launched> {
  const userDataDir = await mkdtemp(path.join(tmpdir(), 'asclepius-e2e-'));
  const app = await electron.launch({
    args: ['.', `--user-data-dir=${userDataDir}`],
    env: { ...process.env, ...env } as Record<string, string>,
  });
  const page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  return {
    app,
    page,
    userDataDir,
    close: async () => {
      await app.close();
      await rm(userDataDir, { recursive: true, force: true });
    },
  };
}

/** Taps the on-screen keypad (digits only). */
export async function tapPin(page: Page, pin: string): Promise<void> {
  for (const digit of pin) await page.getByRole('button', { name: digit, exact: true }).click();
}

/** Picks a seeded crew member and enters the PIN; resolves once Today is showing. */
export async function signInAs(page: Page, name: string, pin = '1234'): Promise<void> {
  await page.getByRole('button', { name: new RegExp(name) }).click();
  await tapPin(page, pin);
  await expect(page.locator('#today-title')).toBeVisible();
}
