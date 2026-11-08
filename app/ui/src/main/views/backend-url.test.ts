import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_BACKEND_URL, parseBackendUrl, resolveBackendUrl } from './backend-url';

describe('parseBackendUrl', () => {
  it.each([
    ['http://127.0.0.1:8000', 'http://127.0.0.1:8000'],
    ['http://127.0.0.1:8000/', 'http://127.0.0.1:8000'],
    ['http://localhost:9000', 'http://localhost:9000'],
    ['http://LOCALHOST:9000/', 'http://localhost:9000'],
    ['https://127.0.0.1:8443', 'https://127.0.0.1:8443'],
    ['http://127.0.0.1', 'http://127.0.0.1'],
  ])('accepts %s as the origin %s', (raw, origin) => {
    expect(parseBackendUrl(raw)).toBe(origin);
  });

  it.each([
    ['another host', 'http://example.com:8000'],
    ['a LAN address', 'http://192.168.1.10:8000'],
    ['a loopback lookalike (suffix)', 'http://127.0.0.1.evil.com:8000'],
    ['a localhost lookalike (suffix)', 'http://localhost.evil.com:8000'],
    ['a localhost lookalike (prefix)', 'http://evil-localhost:8000'],
    ['credentials that hide the real host', 'http://127.0.0.1@evil.com:8000'],
    ['credentials on loopback', 'http://user:secret@127.0.0.1:8000'],
    ['another loopback address', 'http://127.0.0.2:8000'],
    ['the unspecified address', 'http://0.0.0.0:8000'],
    ['IPv6 loopback (not in the allow-list)', 'http://[::1]:8000'],
    ['a non-HTTP scheme', 'ftp://127.0.0.1:8000'],
    ['a file URL', 'file:///etc/passwd'],
    ['a path prefix', 'http://127.0.0.1:8000/api'],
    ['a query', 'http://127.0.0.1:8000/?a=1'],
    ['a fragment', 'http://127.0.0.1:8000/#frag'],
    ['an empty string', ''],
    ['text that is not a URL', 'not a url'],
  ])('rejects %s', (_label, raw) => {
    expect(parseBackendUrl(raw)).toBeNull();
  });
});

describe('resolveBackendUrl', () => {
  it('defaults to the loopback API port when nothing is configured', () => {
    expect(DEFAULT_BACKEND_URL).toBe('http://127.0.0.1:8000');
    expect(resolveBackendUrl(undefined, vi.fn())).toBe(DEFAULT_BACKEND_URL);
  });

  it('uses a valid override', () => {
    expect(resolveBackendUrl('http://localhost:8123/', vi.fn())).toBe('http://localhost:8123');
  });

  it('falls back to the default and reports an invalid override (never a non-loopback host)', () => {
    const onInvalid = vi.fn();
    expect(resolveBackendUrl('http://example.com', onInvalid)).toBe(DEFAULT_BACKEND_URL);
    expect(onInvalid).toHaveBeenCalledTimes(1);
  });

  it('treats an empty override as unset', () => {
    const onInvalid = vi.fn();
    expect(resolveBackendUrl('', onInvalid)).toBe(DEFAULT_BACKEND_URL);
    expect(onInvalid).not.toHaveBeenCalled();
  });
});
