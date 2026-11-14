import { describe, expect, it } from 'vitest';

import { APP_ENTRY_URL, createAppUrlPolicy, createSenderGuard } from './app-url';

describe('createAppUrlPolicy (built app: app:// renderer)', () => {
  const policy = createAppUrlPolicy({});

  it('loads the renderer from the fixed app origin', () => {
    expect(policy.entryUrl).toBe('app://asclepius/index.html');
    expect(APP_ENTRY_URL).toBe('app://asclepius/index.html');
  });

  it.each([
    'app://asclepius/index.html',
    'app://asclepius/index.html#/settings',
    'app://asclepius/index.html#/session/42?tab=1',
    'app://asclepius/assets/../index.html', // the URL parser removes the dot segment
  ])('allows %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(true);
  });

  it.each([
    'app://asclepius/other.html',
    'app://asclepius/assets/index.js',
    'app://asclepius/index.html?x=1',
    'app://asclepius/',
    'app://asclepius',
    'app://asclepius/INDEX.html',
    'app://asclepius/%69ndex.html',
    'app://asclepius:8080/index.html',
    'app://user:pw@asclepius/index.html',
    'app://other/index.html',
    'app://asclepius.evil.com/index.html',
    'file:///C:/Program%20Files/Asclepius%20Orbit/resources/app.asar/out/renderer/index.html',
    'https://asclepius/index.html',
    'http://localhost:5173/',
    'javascript:alert(1)',
    'about:blank',
    'not a url',
    '',
  ])('denies %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(false);
  });
});

describe('createAppUrlPolicy (dev server)', () => {
  const policy = createAppUrlPolicy({ devServerUrl: 'http://localhost:5173' });

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
    'app://asclepius/index.html',
    'garbage',
  ])('denies %s', (url) => {
    expect(policy.isAppUrl(url)).toBe(false);
  });

  it('treats an empty dev server URL as no dev server', () => {
    const fallback = createAppUrlPolicy({ devServerUrl: '' });
    expect(fallback.entryUrl).toBe('app://asclepius/index.html');
  });
});

describe('createSenderGuard', () => {
  const guard = createSenderGuard(createAppUrlPolicy({}));
  const top = { url: 'app://asclepius/index.html#/login', parent: null };

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
