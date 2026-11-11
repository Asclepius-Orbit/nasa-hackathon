// Real-mode demo: the app (built with `--mode e2e-real`) against the real backend, engine, broker and simulated harness.
// Only `pnpm e2e:real` runs it (it starts the Docker stack and passes the shared token). Waits are polls on the UI with
// generous timeouts, never fixed sleeps: the simulator runs in real time, so a spine-length session lasts about 2 minutes.
import { expect, test } from '@playwright/test';

import { launch, signInAs, type Launched } from './helpers';

const SESSION_MS = 120_000; // `spine_length_rest_v1` in spine_monitor/physiology/configs/example-mission.yaml
const token = process.env['ASCLEPIUS_BACKEND_TOKEN'];

let ctx: Launched;

test.beforeAll(async () => {
  if (!token) throw new Error('Run this through `pnpm e2e:real`: it starts the stack and sets ASCLEPIUS_BACKEND_TOKEN.');
  ctx = await launch({ ASCLEPIUS_BACKEND_TOKEN: token });
});
test.afterAll(async () => {
  await ctx.close();
});

/** A launch date ten days from now (local date, YYYY-MM-DD): today is then in the preflight phase. */
function launchDateInTenDays(): string {
  const date = new Date();
  date.setDate(date.getDate() + 10);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

test('login, profile, report, a spine-length session, the assessment and the Houston summary', async () => {
  const { page } = ctx;

  // Login: the demo crew is registered with the backend on start-up.
  await signInAs(page, 'Elena Ruiz');

  // Profile with a future launch date, so today is preflight and the baseline session is a preflight one.
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByRole('button', { name: 'Open profile' }).click();
  const profile = page.getByRole('region', { name: 'Your profile' });
  await profile.getByLabel('Date of birth').fill('1988-04-12');
  await profile.getByRole('button', { name: 'Female' }).click();
  await profile.getByLabel('Standing height (cm)').fill('171');
  await profile.getByLabel('Body mass (kg)').fill('64.5');
  await profile.getByLabel('Launch date, optional').fill(launchDateInTenDays());
  await profile.getByRole('button', { name: 'Save profile' }).click();
  await expect(profile.getByText('Saved')).toBeVisible({ timeout: 30_000 });

  // Today's context is filled in from the profile (no manual step): preflight. Reopen the screen to read what was stored.
  await page.getByRole('link', { name: 'Today' }).click();
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByRole('button', { name: 'Open profile' }).click();
  const context = page.getByRole('region', { name: 'Today’s context' });
  await expect(context.getByRole('button', { name: 'Preflight' })).toHaveAttribute('aria-pressed', 'true', { timeout: 30_000 });

  // Daily report.
  await page.getByRole('link', { name: /Report/ }).click();
  await page.getByRole('radiogroup', { name: 'How much does your back hurt right now?' }).getByText('1', { exact: true }).click();
  await page.getByRole('radiogroup', { name: 'How stiff does your back feel?' }).getByText('1', { exact: true }).click();
  await page.getByRole('button', { name: 'None of these' }).click();
  await page.getByRole('button', { name: 'Save daily report' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Daily report saved.' })).toBeVisible({ timeout: 30_000 });

  // Session: start the spine-length check; it is `starting` until the harness acknowledges, then `running`.
  await page.getByRole('link', { name: /Session/ }).click();
  await page.getByRole('radiogroup', { name: 'Choose the session' }).getByText('Spine length check').click();
  await page.getByRole('button', { name: 'Start session' }).click();
  const startedLine = page.getByText(/^Started \d{2}:\d{2} GMT/);
  const stopPain = page.getByRole('button', { name: /Stop: pain/ });
  await expect(stopPain).toBeVisible({ timeout: 60_000 });
  await expect(startedLine).toBeVisible({ timeout: 120_000 }); // running: the harness confirmed and the start time is known

  // The app starts the session without a duration (it runs until Finish), so let the simulator stream a full spine check
  // (polling the clock, not sleeping), then finish it from the last step. Stop: pain goes away once the device completes it.
  const runningSince = Date.now();
  await expect.poll(() => Date.now() - runningSince, { timeout: SESSION_MS + 30_000, intervals: [1_000] }).toBeGreaterThanOrEqual(SESSION_MS);
  await page.getByRole('button', { name: /Rest and finish/ }).click();
  await page.getByRole('button', { name: 'Finish session' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Finish sent. The session is being saved.' })).toBeVisible({ timeout: 30_000 });
  await expect(stopPain).toBeHidden({ timeout: 120_000 });

  // Today: an assessment level (or "No data" when the engine has nothing to assess yet).
  await page.getByRole('link', { name: 'Today' }).click();
  const level = page.locator('#today-title');
  await expect(level).toHaveText(/^(Green|Amber|Red|No data)$/, { timeout: 180_000 });

  // Houston: the day's summary with the session, and its outbox entry: Queued, or already Sent when the simulated ground
  // window is open (hash router: no link to this screen in real navigation yet).
  await page.evaluate(() => {
    (globalThis as unknown as { location: { hash: string } }).location.hash = '#/houston';
  });
  await expect(page.getByRole('heading', { level: 1, name: 'What goes to Houston' })).toBeVisible();
  await expect(page.getByText('spine length check, complete')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('list', { name: 'Outbox' }).getByRole('listitem').first()).toHaveText(/summary.*(Queued|Sent)/, { timeout: 60_000 });
});
