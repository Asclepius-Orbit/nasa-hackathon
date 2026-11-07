import { describe, expect, it } from 'vitest';

import { createAppUrlPolicy, createSenderGuard } from './app-url';

describe('createAppUrlPolicy (packaged / built: file:// renderer)', () => {
  const policy = createAppUrlPolicy({
    rendererFile: 'C:\\Program Files\\Asclepius Orbit\\resources\\app.asar\\out\\renderer\\index.html',
    platform: 'win32',
  });

  it('exposes the entry file URL', () => {
    expect(policy.entryUrl).toBe(
      'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html',
    );
  });

  it.each([
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html',
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html#/settings',
    'file:///c:/program%20files/asclepius%20orbit/resources/app.asar/out/renderer/INDEX.html', // NTFS is case-insensitive
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/../renderer/index.html',
  ])('allows %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(true);
  });

  it.each([
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/other.html',
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html?x=1',
    'file:///C:/Users/eve/Downloads/index.html',
    'file://evil-host/C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html',
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out%2Frenderer%2Findex.html',
    'https://example.com/',
    'http://localhost:5173/',
    'javascript:alert(1)',
    'about:blank',
    'not a url',
    '',
  ])('denies %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(false);
  });

  it('compares normalized paths: dot segments and forward slashes in the configured file still match', () => {
    const dotted = createAppUrlPolicy({ rendererFile: 'C:/app/out/main/../renderer/index.html', platform: 'win32' });
    expect(dotted.isAppUrl('file:///C:/app/out/renderer/index.html')).toBe(true);
    const posix = createAppUrlPolicy({ rendererFile: '/opt/app/out/main/../renderer/index.html', platform: 'linux' });
    expect(posix.isAppUrl('file:///opt/app/out/renderer/index.html')).toBe(true);
  });

  it('is case-sensitive on POSIX platforms', () => {
    const posix = createAppUrlPolicy({ rendererFile: '/opt/app/out/renderer/index.html', platform: 'linux' });
    expect(posix.isAppUrl('file:///opt/app/out/renderer/index.html')).toBe(true);
    expect(posix.isAppUrl('file:///opt/app/out/renderer/INDEX.html')).toBe(false);
  });
});

describe('createAppUrlPolicy (dev server)', () => {
  const policy = createAppUrlPolicy({
    devServerUrl: 'http://localhost:5173',
    rendererFile: '/unused/index.html',
    platform: 'linux',
  });

  it('uses the dev server URL as the entry', () => {
    expect(policy.entryUrl).toBe('http://localhost:5173');
  });

  it.each(['http://localhost:5173/', 'http://localhost:5173/#/today', 'http://localhost:5173/src/main.tsx'])(
    'allows same origin %s',
    (url) => {
      expect(policy.isAppUrl(url)).toBe(true);
    },
  );

  it.each([
    'http://localhost:5174/',
    'https://localhost:5173/',
    'http://127.0.0.1:5173/',
    'http://localhost:5173@evil.com/',
    'file:///unused/index.html',
    'garbage',
  ])('denies %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(false);
  });

  it('treats an empty dev server URL as no dev server', () => {
    const fallback = createAppUrlPolicy({ devServerUrl: '', rendererFile: '/app/index.html', platform: 'linux' });
    expect(fallback.entryUrl).toBe('file:///app/index.html');
  });
});

describe('createSenderGuard', () => {
  const policy = createAppUrlPolicy({ rendererFile: '/app/out/renderer/index.html', platform: 'linux' });
  const guard = createSenderGuard(policy);
  const top = { url: 'file:///app/out/renderer/index.html#/login', parent: null };

  it('trusts the app main frame', () => {
    expect(guard({ senderFrame: top })).toBe(true);
  });

  it('rejects a missing frame (destroyed or navigated away)', () => {
    expect(guard({ senderFrame: null })).toBe(false);
  });

  it('rejects a subframe even at the app URL', () => {
    expect(guard({ senderFrame: { url: top.url, parent: top } })).toBe(false);
  });

  it('rejects a main frame at a foreign URL', () => {
    expect(guard({ senderFrame: { url: 'https://example.com/', parent: null } })).toBe(false);
  });
});
