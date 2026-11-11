import { describe, expect, it } from 'vitest';

import { authHeaders, resolveBackendToken } from './backend-auth';

const noFile = (): string => {
  throw new Error('ENOENT');
};

describe('backend token', () => {
  it('prefers the variable, then the token file, else none', () => {
    const errors: string[] = [];
    const log = (m: string): void => {
      errors.push(m);
    };
    expect(resolveBackendToken({ ASCLEPIUS_BACKEND_TOKEN: ' abc ' }, noFile, log)).toBe('abc');
    expect(resolveBackendToken({ ASCLEPIUS_BACKEND_TOKEN_FILE: '/data/ui-token' }, () => 'from-file\n', log)).toBe('from-file');
    expect(resolveBackendToken({}, noFile, log)).toBeNull();
    expect(resolveBackendToken({ ASCLEPIUS_BACKEND_TOKEN_FILE: '/missing' }, noFile, log)).toBeNull();
    expect(errors).toEqual(['ASCLEPIUS_BACKEND_TOKEN_FILE: the token file cannot be read']);
  });

  it('sends the token and the unlocked astronaut only when there are', () => {
    expect(authHeaders('t', 'AST-01')).toEqual({ authorization: 'Bearer t', 'x-astronaut-id': 'AST-01' });
    expect(authHeaders(null)).toEqual({});
  });
});

describe('views client credentials', () => {
  it('sends the token and the astronaut it asks for', async () => {
    const { createViewsClient } = await import('./views-client');
    const seen: Record<string, string>[] = [];
    const client = createViewsClient({
      baseUrl: 'http://127.0.0.1:8000',
      token: 'secret',
      fetch: (_url, init) => {
        seen.push({ ...init.headers });
        return Promise.resolve(new Response('{}', { status: 500 }));
      },
    });

    await expect(client.today('AST-02')).rejects.toThrow();

    expect(seen).toEqual([{ accept: 'application/json', authorization: 'Bearer secret', 'x-astronaut-id': 'AST-02' }]);
  });
});
