import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { makeTempDir } from '../store/test-fakes';
import { APP_SCHEME_PRIVILEGES, contentTypeFor, createAppProtocolHandler, resolveAppFile } from './app-protocol';

const ROOT = resolve('/srv/app/out/renderer');

describe('APP_SCHEME_PRIVILEGES', () => {
  it('registers app: as a standard, secure scheme with fetch support (a real origin for module scripts and the CSP)', () => {
    expect(APP_SCHEME_PRIVILEGES).toEqual([
      { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
    ]);
  });
});

describe('resolveAppFile', () => {
  it.each([
    ['app://asclepius/index.html', join(ROOT, 'index.html')],
    ['app://asclepius/index.html#/settings', join(ROOT, 'index.html')],
    ['app://asclepius/assets/index-BfboVBDK.js', join(ROOT, 'assets', 'index-BfboVBDK.js')],
    ['app://asclepius/assets/atkinson-hyperlegible-next-latin-400-normal-x_1.woff2', join(ROOT, 'assets', 'atkinson-hyperlegible-next-latin-400-normal-x_1.woff2')],
    // The URL parser removes dot segments before we see the path; what is left must still be inside the root.
    ['app://asclepius/assets/../index.html', join(ROOT, 'index.html')],
    ['app://asclepius/../../main/index.js', join(ROOT, 'main', 'index.js')],
    ['app://asclepius/%2e%2e/index.html', join(ROOT, 'index.html')],
  ])('maps %s inside the renderer folder', (url, file) => {
    expect(resolveAppFile(ROOT, url)).toBe(file);
  });

  it.each([
    'app://asclepius/..%2fmain%2findex.js', // encoded slash
    'app://asclepius/..%2Fmain/index.js',
    'app://asclepius/..%5cmain%5cindex.js', // encoded backslash
    'app://asclepius/..\\main\\index.js', // raw backslash
    'app://asclepius/C:%5cWindows%5cwin.ini',
    'app://asclepius/C:/Windows/win.ini', // drive letter: never a segment we serve
    'app://asclepius//etc/passwd', // empty segment
    'app://asclepius/%2e%2e%2f%2e%2e%2fsecret',
    'app://asclepius/index.html%00.png', // NUL
    'app://asclepius/%E0%A4%A', // malformed percent-encoding
    'app://asclepius/.hidden',
    'app://asclepius/assets/', // directory: no listing
    'app://asclepius/',
    'app://asclepius',
    'app://asclepius/index.html?x=1',
    'app://other/index.html',
    'app://ASCLEPIUS/index.html',
    'app://asclepius:8080/index.html',
    'app://user@asclepius/index.html',
    'file:///srv/app/out/renderer/index.html',
    'https://asclepius/index.html',
    'not a url',
  ])('refuses %s', (url) => {
    expect(resolveAppFile(ROOT, url)).toBeNull();
  });
});

describe('contentTypeFor', () => {
  it.each([
    ['index.html', 'text/html; charset=utf-8'],
    ['a.js', 'text/javascript; charset=utf-8'],
    ['a.css', 'text/css; charset=utf-8'],
    ['a.woff2', 'font/woff2'],
    ['a.woff', 'font/woff'],
    ['a.svg', 'image/svg+xml'],
    ['a.png', 'image/png'],
    ['A.PNG', 'image/png'],
  ])('%s is %s', (file, type) => {
    expect(contentTypeFor(file)).toBe(type);
  });

  it.each(['a.json', 'a.map', 'a.exe', 'Makefile', 'a.html.bak'])('serves nothing it has no type for: %s', (file) => {
    expect(contentTypeFor(file)).toBeNull();
  });
});

describe('createAppProtocolHandler', () => {
  const CSP = "default-src 'self'";
  let dir: string;
  let root: string;
  let cleanup: () => Promise<void>;

  beforeEach(async () => {
    ({ dir, cleanup } = await makeTempDir());
    root = join(dir, 'renderer');
    await mkdir(join(root, 'assets'), { recursive: true });
    await writeFile(join(root, 'index.html'), '<!doctype html><title>x</title>');
    await writeFile(join(root, 'assets', 'app.js'), 'console.log(1)');
    await writeFile(join(root, 'assets', 'notes.txt'), 'no type');
    await writeFile(join(dir, 'secret.js'), 'secret');
  });
  afterEach(async () => {
    await cleanup();
  });

  const get = (url: string, method = 'GET') => createAppProtocolHandler({ root, csp: CSP })({ url, method });

  it('serves a renderer file with its type, the CSP header and nosniff', async () => {
    const response = await get('app://asclepius/index.html');
    expect(response.status).toBe(200);
    expect(await response.text()).toBe('<!doctype html><title>x</title>');
    expect(response.headers.get('content-type')).toBe('text/html; charset=utf-8');
    expect(response.headers.get('content-security-policy')).toBe(CSP);
    expect(response.headers.get('x-content-type-options')).toBe('nosniff');
  });

  it('serves assets', async () => {
    const response = await get('app://asclepius/assets/app.js');
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('text/javascript; charset=utf-8');
    expect(await response.text()).toBe('console.log(1)');
  });

  it.each([
    'app://asclepius/missing.js',
    'app://asclepius/assets/', // directory listing
    'app://asclepius/assets.js', // EISDIR-like misses still 404
    'app://asclepius/assets/notes.txt', // no content type
    'app://asclepius/..%2fsecret.js',
    'app://asclepius/../secret.js', // normalized to <root>/secret.js, which does not exist
    'app://evil/index.html',
  ])('answers 404 (with the CSP) for %s', async (url) => {
    const response = await get(url);
    expect(response.status).toBe(404);
    expect(await response.text()).toBe('');
    expect(response.headers.get('content-security-policy')).toBe(CSP);
  });

  it('answers 404 for a directory even when it has a known extension', async () => {
    await mkdir(join(root, 'folder.js'));
    expect((await get('app://asclepius/folder.js')).status).toBe(404);
  });

  it('only answers GET and HEAD', async () => {
    expect((await get('app://asclepius/index.html', 'HEAD')).status).toBe(200);
    expect((await get('app://asclepius/index.html', 'POST')).status).toBe(405);
  });
});
