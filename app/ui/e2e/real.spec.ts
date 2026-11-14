// Real-mode demo: the app (built with `--mode e2e-real`) against the real backend, engine, broker and simulated harness.
// Only `pnpm e2e:real` runs it (it starts the Docker stack and passes the shared token). Waits are polls on the UI with
// generous timeouts, never fixed sleeps: the simulator runs in real time, so a spine-length session lasts about 2 minutes.
import { expect, test } from '@playwright/test';

import { launch, signInAs, type Launched } from './helpers';

const SESSION_MS = 120_000; // `spine_length_rest_v1` in spine_monitor/physiology/configs/example-mission.yaml
const tokenFile = process.env['ASCLEPIUS_BACKEND_TOKEN_FILE'];
// The producer of this run (set by scripts/e2e-real.mjs): the harness id it publishes as (sim-01 for the simulator,
// gw-demo for the gateway reading the Arduino emulator). The e2e-real build seeds the crew's harness from it (demo-crew.ts).
const device = process.env['E2E_REAL_SOURCE'] === 'device';

let ctx: Launched;

test.beforeAll(async () => {
  if (!tokenFile) throw new Error('Run this through `pnpm e2e:real`: it starts the stack and sets ASCLEPIUS_BACKEND_TOKEN_FILE.');
  ctx = await launch({ ASCLEPIUS_BACKEND_TOKEN: '', ASCLEPIUS_BACKEND_TOKEN_FILE: tokenFile });
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

test('login, profile, report, a spine-length session, the assessment', async () => {
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

  // The harness is paired by the build (the app has no pairing screen): the demo crew is seeded with this run's producer.

  if (device) {
    // Device mode (Arduino emulator behind the real gateway): its signals are synthetic and there is no rep counting, so the
    // spine-length assessment and the live rep counter (all fed by simulated physiology) are not run.
    // The lat pulldown is not started either: the app declares emg_lat_left and emg_lat_right, the gateway serves one EMG
    // stream (emg_lat_left), so it rejects the start (unknown stream) and the UI shows "The harness did not accept the
    // session". That mismatch is a gap to fix in the product, not something this spec asserts.
    // What the emulator does exercise end to end: start (ack) and stop through gateway commands.
    await page.getByRole('link', { name: /Session/ }).click();
    await page.getByRole('radiogroup', { name: 'Choose the session' }).getByText('Spine length check').click();
    await page.getByRole('button', { name: 'Start session' }).click();
    await expect(page.getByText(/^Started \d{2}:\d{2} GMT/)).toBeVisible({ timeout: 120_000 });
    await expect(page.getByRole('button', { name: /Stop: pain/ })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('The harness did not accept the session.')).toHaveCount(0);
    // `running` (the gateway acked the start): the Calibrate button of its step is enabled only then, and a stop sent while the
    // session is still `starting` is refused with a 409.
    await page.getByRole('button', { name: /Calibrate maximum effort/ }).click();
    await expect(page.getByRole('button', { name: 'Calibrate', exact: true })).toBeEnabled({ timeout: 60_000 });
  } else {
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

    // Today: the completed session must produce an assessment, not merely a placeholder.
    await page.getByRole('link', { name: 'Today' }).click();
    const level = page.locator('#today-title');
    await expect(level).toHaveText(/^(Green|Amber|Red)$/, { timeout: 180_000 });

    // The spine check has no repetitions. Start a counted exercise and observe the live counter advance.
    await page.getByRole('link', { name: 'Session' }).click();
    await page.getByRole('radiogroup', { name: 'Choose the session' }).getByText('Standing lat pulldown with band').click();
    await page.getByRole('button', { name: 'Start session' }).click();
    await expect(page.getByRole('heading', { name: 'Standing lat pulldown with band' })).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'Do the reps' }).click();
    const liveCount = page.locator('[data-live="true"] span').first();
    await expect(liveCount).toBeVisible({ timeout: 60_000 });
    const firstCount = Number.parseInt((await liveCount.innerText()).trim(), 10);
    expect(Number.isFinite(firstCount)).toBe(true);
    await expect.poll(async () => Number.parseInt((await liveCount.innerText()).trim(), 10), { timeout: 90_000 }).toBeGreaterThan(firstCount);
  }

  // Calibration (needs an EMG stream) and artefact marking (counted reps only) need the lat pulldown. The emulator device cannot
  // run it (see above), and on the spine check the app refuses the calibration (no EMG stream declared), so only the simulator does.
  if (!device) {
    // Calibrate the maximum effort where the UI offers it (the step of a running session), then mark an artefact on a rep.
    await page.getByRole('button', { name: /Calibrate maximum effort/ }).click();
    await expect(page.getByRole('button', { name: 'Calibrate', exact: true })).toBeEnabled({ timeout: 60_000 }); // running
    await page.getByRole('button', { name: 'Calibrate', exact: true }).click();
    await expect(page.getByText('Calibration requested. Waiting for the harness to confirm.')).toBeVisible({ timeout: 30_000 });
    // The view follows the session's current step once the ack lands: open the calibration step again to read its result.
    await page.getByRole('button', { name: /Calibrate maximum effort/ }).click();
    await expect(page.getByText(/Calibration saved at \d{2}:\d{2} GMT\./)).toBeVisible({ timeout: 120_000 }); // the harness acked it
    await page.getByRole('button', { name: /Do the reps/ }).click();
    await page.getByRole('button', { name: 'Mark an artefact' }).click();
    await expect(page.getByText('Artefact marked.')).toBeVisible({ timeout: 30_000 });
  }

  // Stop the session (pain): the stop is recorded and the session no longer offers it.
  const stop = page.getByRole('button', { name: /Stop: pain/ });
  await stop.click();
  await expect(stop).toBeHidden({ timeout: 120_000 });
});
