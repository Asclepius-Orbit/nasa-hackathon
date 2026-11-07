// Visual capture: every screen plus the Urgent dialog at two window sizes, into test-results/visual/ (gitignored).
// Slow-ish; skip with `pnpm exec playwright test --grep-invert @visual`.
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { launch, tapPin } from './helpers';

const OUT = path.join('test-results', 'visual');
const SIZES = [
  { w: 1280, h: 800 },
  { w: 800, h: 1000 },
] as const;

const SCREENS = ['today', 'report', 'session', 'stopped', 'history', 'settings', 'first-day', 'baseline', 'oswestry', 'houston'] as const;

test('@visual captures every screen at 1280x800 and 800x1000', async () => {
  test.setTimeout(180_000);
  await mkdir(OUT, { recursive: true });
  for (const { w, h } of SIZES) {
    const ctx = await launch();
    const { page, app } = ctx;
    try {
      const tag = `${String(w)}x${String(h)}`;
      await app.evaluate(({ BrowserWindow }, size) => {
        BrowserWindow.getAllWindows()[0]?.setContentSize(size.w, size.h);
      }, { w, h });
      await page.waitForTimeout(300);
      const shot = async (name: string): Promise<void> => {
        await page.waitForTimeout(250);
        await page.screenshot({ path: path.join(OUT, `${name}-${tag}.png`), scale: 'css' });
      };

      await expect(page.getByRole('heading', { name: 'Who is using this tablet?' })).toBeVisible();
      await shot('login');
      await page.getByRole('button', { name: /Elena Ruiz/ }).click();
      await tapPin(page, '1234');
      await expect(page.locator('#today-title')).toBeVisible();

      for (const screen of SCREENS) {
        await page.evaluate((s) => {
          (globalThis as unknown as { location: { hash: string } }).location.hash = `#/${s}`;
        }, screen);
        await expect(page.locator('main, [role="main"], h1').first()).toBeVisible();
        await shot(screen);
        // Tall screens scroll inside the layout: capture the end of the page too.
        await page.mouse.move(Math.round(w / 2), Math.round(h / 2));
        await page.mouse.wheel(0, 20_000);
        await shot(`${screen}-end`);
        await page.mouse.wheel(0, -20_000);
        if (screen === 'today') {
          await page.getByRole('button', { name: 'Urgent symptom' }).click();
          await expect(page.getByRole('dialog')).toBeVisible();
          await page.getByRole('dialog').getByRole('group', { name: 'Symptoms' }).getByRole('button').first().click();
          await shot('urgent');
          await page.keyboard.press('Escape');
          await expect(page.getByRole('dialog')).toBeHidden();
        }
      }

      await page.getByRole('button', { name: 'Lock', exact: true }).click();
      await expect(page.getByTestId('lock-view')).toBeVisible();
      await shot('lock');
      await tapPin(page, '1234');
    } finally {
      await ctx.close();
    }
  }
});

