import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { devCsp, PROD_CSP } from './csp';

describe('CSP', () => {
  it('matches the meta CSP in the renderer index.html exactly', async () => {
    const html = await readFile(join(import.meta.dirname, '../../renderer/index.html'), 'utf8');
    const meta = /http-equiv="Content-Security-Policy"\s+content="([^"]+)"/.exec(html)?.[1];
    expect(PROD_CSP).toBe(meta);
  });

  it('is strict in production', () => {
    expect(PROD_CSP).not.toContain('unsafe');
    expect(PROD_CSP).toContain("object-src 'none'");
  });

  it('in dev applies exactly the .replace() pairs of the devCsp plugin in electron.vite.config.ts', async () => {
    const config = await readFile(join(import.meta.dirname, '../../../electron.vite.config.ts'), 'utf8');
    const pairs = [...config.matchAll(/\.replace\("([^"]+)",\s*"([^"]+)"\)/g)].map((m): [string, string] => [m[1] ?? '', m[2] ?? '']);
    expect(pairs).toHaveLength(3);
    const expected = pairs.reduce((csp, [from, to]) => csp.replace(from, to), PROD_CSP);
    expect(devCsp()).toBe(expected);
  });

  it('in dev mirrors the electron-vite devCsp relaxations (inline for HMR, localhost websocket)', () => {
    expect(devCsp()).toBe(
      "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
        "font-src 'self'; connect-src 'self' ws://localhost:* http://localhost:*; object-src 'none'; base-uri 'none'; " +
        "form-action 'none'; frame-ancestors 'none'",
    );
  });
});
