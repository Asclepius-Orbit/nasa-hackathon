// Smoke tests: launch the built app from out/ (run `pnpm build` first, or use `pnpm e2e`).
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

import type { AsclepiusApi } from '../src/shared/api';

/** Renderer globals used inside page.evaluate (this tsconfig has no DOM lib). Casts are inlined: evaluate() ships only the function body. */
interface RendererGlobals {
  asclepius: AsclepiusApi;
  open(url: string): unknown;
  location: { hash: string };
}

let app: ElectronApplication;
let page: Page;
let userDataDir: string;

test.beforeAll(async () => {
  userDataDir = await mkdtemp(path.join(tmpdir(), 'asclepius-e2e-'));
  // A throwaway profile per run: fresh settings, crew seeds and lockout state; the real profile is never touched.
  app = await electron.launch({ args: ['.', `--user-data-dir=${userDataDir}`] });
  page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
});

test.afterAll(async () => {
  await app.close();
  await rm(userDataDir, { recursive: true, force: true });
});

test('app window loads the renderer', async () => {
  // The renderer UI is still changing; the document title is stable.
  await expect(page).toHaveTitle('Asclepius Orbit');
  await expect(page.getByRole('heading', { name: 'Who is using this tablet?' })).toBeVisible();
  await expect(page.getByText('Asclepius Orbit, on-board spine monitoring')).toBeVisible();
});

test('runs on a throwaway profile, never the real one', async () => {
  const userData = await app.evaluate(({ app: electronApp }) => electronApp.getPath('userData'));
  expect(path.resolve(userData)).toBe(path.resolve(userDataDir));
});

test('a fresh profile gets the default settings (large text)', async () => {
  const prefs = await page.evaluate(() => (globalThis as unknown as RendererGlobals).asclepius.settings.get());
  expect(prefs.textSize).toBe('large');
});

test('renderer has no Node: no require, no process', async () => {
  expect(await page.evaluate(() => typeof require)).toBe('undefined');
  expect(await page.evaluate(() => typeof process)).toBe('undefined');
});

test('bridge exposes exactly the crew, auth, settings and views functions', async () => {
  const shape = await page.evaluate(() => {
    const api = (globalThis as unknown as RendererGlobals).asclepius as unknown as Record<string, Record<string, unknown>>;
    return Object.fromEntries(
      Object.entries(api).map(([group, fns]) => [
        group,
        Object.entries(fns)
          .map(([name, fn]) => `${name}:${typeof fn}`)
          .sort(),
      ]),
    );
  });
  expect(shape).toEqual({
    crew: ['create:function', 'list:function', 'nextId:function'],
    auth: ['current:function', 'lock:function', 'verifyPin:function'],
    settings: ['get:function', 'update:function'],
    views: ['baseline:function', 'firstDay:function', 'history:function', 'session:function', 'strip:function', 'today:function'],
  });
});

test('main process rejects invalid input from the renderer', async () => {
  const outcome = await page.evaluate(() =>
    (globalThis as unknown as RendererGlobals).asclepius.crew.create({ name: 'x'.repeat(41), pin: '1234' }).then(
      () => 'resolved',
      (error: unknown) => (error instanceof Error ? error.message : String(error)),
    ),
  );
  expect(outcome).toContain('invalid input');
});

test('bridge round-trips to the main process (dev seeds, sign-in, lock)', async () => {
  const result = await page.evaluate(async () => {
    const { crew, auth, settings } = (globalThis as unknown as RendererGlobals).asclepius;
    const members = await crew.list();
    const nextId = await crew.nextId();
    const signIn = await auth.verifyPin({ crewId: 'AST-01', pin: '1234' });
    await auth.lock();
    const session = await auth.current();
    const prefs = await settings.get();
    return { first: members[0], nextId, signIn, session, prefKeys: Object.keys(prefs).sort() };
  });
  expect(result.first).toEqual({ id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' });
  expect(result.nextId).toMatch(/^AST-\d{2}$/);
  expect(result.signIn).toEqual({ ok: true, crewId: 'AST-01' });
  expect(result.session).toEqual({ crewId: 'AST-01', locked: true });
  expect(result.prefKeys).toEqual(['footPedal', 'lengthUnit', 'textSize', 'timeFormat', 'voiceCommands']);
});

test('window.open is denied and hash routing stays in the app', async () => {
  const before = page.url();
  expect(await page.evaluate(() => (globalThis as unknown as RendererGlobals).open('https://example.com/') === null)).toBe(true);
  await page.evaluate(() => {
    (globalThis as unknown as RendererGlobals).location.hash = '#/settings';
  });
  expect(page.url().split('#')[0]).toBe(before.split('#')[0]);
  expect(app.windows()).toHaveLength(1);
});
