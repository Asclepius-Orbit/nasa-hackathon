// Packaged-app check: launches the unpacked electron-builder output (fuses applied by scripts/after-pack.cjs),
// i.e. the binary users actually get. Run with `pnpm e2e:packaged` (production build + `electron-builder --dir`).
//
// Playwright's `_electron.launch` needs Node's --inspect, which the EnableNodeCliInspectArguments fuse turns off
// in this binary, so the helper below starts the exe itself and attaches over Chromium's remote debugging port.
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { chromium, expect, test, type Page } from '@playwright/test';

import { PROD_CSP } from '../src/main/security/csp';

const executablePath = path.join(import.meta.dirname, '../release/win-unpacked/Asclepius Orbit.exe');

interface Packaged {
  page: Page;
  close: () => Promise<void>;
}

/** Starts the packaged exe on a throwaway profile and attaches Playwright to its first window over CDP. */
async function launchPackaged(): Promise<Packaged> {
  const userDataDir = await mkdtemp(path.join(tmpdir(), 'asclepius-packaged-'));
  const child = spawn(executablePath, [`--user-data-dir=${userDataDir}`, '--remote-debugging-port=0'], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.resume(); // drained, unused
  const exited = new Promise((resolve) => child.once('exit', resolve));
  const close = async (): Promise<void> => {
    if (child.exitCode === null) child.kill();
    await exited;
    await rm(userDataDir, { recursive: true, force: true });
  };
  try {
    const wsEndpoint = await devToolsEndpoint(child);
    // Attaching while the first window is still being created can hang; wait until its page target exists.
    await waitForPageTarget(new URL(wsEndpoint).port);
    const browser = await chromium.connectOverCDP(wsEndpoint, { timeout: 10_000 });
    const context = browser.contexts()[0];
    if (context === undefined) throw new Error('no browser context');
    const page = context.pages()[0] ?? (await context.waitForEvent('page', { timeout: 10_000 }));
    return {
      page,
      close: async () => {
        await browser.close();
        await close();
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}

async function waitForPageTarget(port: string): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const targets = (await fetch(`http://127.0.0.1:${port}/json/list`).then(
      (r) => r.json(),
      () => [],
    )) as { type: string; url: string }[];
    if (targets.some((t) => t.type === 'page' && t.url !== '' && t.url !== 'about:blank')) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('the packaged app opened no window');
}

function devToolsEndpoint(child: ChildProcess): Promise<string> {
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => {
      reject(new Error(`no DevTools endpoint from the packaged app; stderr:\n${output}`));
    }, 20_000);
    child.stderr?.on('data', (chunk: Buffer) => {
      output += chunk.toString('utf8');
      const match = /DevTools listening on (ws:\/\/\S+)/.exec(output);
      if (match?.[1] !== undefined) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`packaged app exited early (${String(code)}); stderr:\n${output}`));
    });
  });
}

let packaged: Packaged;
let page: Page;

test.beforeAll(async () => {
  packaged = await launchPackaged();
  ({ page } = packaged);
});

test.afterAll(async () => {
  await packaged.close();
});

test('the packaged app renders the login screen', async () => {
  await expect(page.getByRole('heading', { name: 'Who is using this tablet?' })).toBeVisible();
});

test('loads from the app:// origin and the CSP header is on the document', async () => {
  expect(page.url()).toMatch(/^app:\/\/asclepius\/index\.html(#.*)?$/);
  const [response] = await Promise.all([page.waitForResponse(/index\.html/), page.reload()]);
  expect(await response.headerValue('content-security-policy')).toBe(PROD_CSP);
  await expect(page.getByRole('heading', { name: 'Who is using this tablet?' })).toBeVisible();
});

test('production build: no demo crew', async () => {
  const members = await page.evaluate(() =>
    (globalThis as unknown as { asclepius: { crew: { list(): Promise<unknown[]> } } }).asclepius.crew.list(),
  );
  expect(members).toEqual([]);
});

test('the app protocol serves the renderer folder and nothing else', async () => {
  const statuses = await page.evaluate(async () => {
    const urls = [
      'app://asclepius/index.html',
      'app://asclepius/../main/index.js',
      'app://asclepius/%2e%2e/main/index.js',
      'app://asclepius/..%2fmain%2findex.js',
      'app://asclepius/..%5cmain%5cindex.js',
      'app://asclepius/assets/',
      'app://asclepius/package.json',
      'app://other/index.html', // another origin: refused by the CSP (connect-src 'self')
    ];
    return Promise.all(
      urls.map((url) =>
        fetch(url).then(
          (r) => `${String(r.status)} ${r.headers.get('content-type') ?? ''}`.trim(),
          () => 'blocked',
        ),
      ),
    );
  });
  expect(statuses).toEqual(['200 text/html; charset=utf-8', '404', '404', '404', '404', '404', '404', 'blocked']);
});
