import { act, renderHook, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ApiProvider } from '../api/ApiContext';
import { FakeAsclepiusApi } from '../test/fake-api';
import { AuthProvider, redirectFor, useAuth } from './AuthProvider';

const setup = (api: FakeAsclepiusApi): ReturnType<typeof renderHook<ReturnType<typeof useAuth>, unknown>> =>
  renderHook(() => useAuth(), {
    wrapper: ({ children }) => (
      <ApiProvider api={api}>
        <AuthProvider>{children}</AuthProvider>
      </ApiProvider>
    ),
  });

it('starts loading, then signed-out when there is no session', async () => {
  const { result } = setup(new FakeAsclepiusApi());
  expect(result.current.status).toBe('loading');
  await waitFor(() => {
    expect(result.current.status).toBe('signed-out');
  });
  expect(result.current.crewId).toBeNull();
});

it('restores a signed-in session', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  const { result } = setup(api);
  await waitFor(() => {
    expect(result.current).toMatchObject({ status: 'signed-in', crewId: 'AST-01' });
  });
});

it('restores a locked session', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: true };
  const { result } = setup(api);
  await waitFor(() => {
    expect(result.current).toMatchObject({ status: 'locked', crewId: 'AST-01' });
  });
});

it('signs in on an ok pin result and ignores a failed one', async () => {
  const { result } = setup(new FakeAsclepiusApi());
  await waitFor(() => {
    expect(result.current.status).toBe('signed-out');
  });
  act(() => {
    result.current.signIn({ ok: false, reason: 'wrong_pin', attemptsLeft: 2 });
  });
  expect(result.current.status).toBe('signed-out');
  act(() => {
    result.current.signIn({ ok: true, crewId: 'AST-02' });
  });
  expect(result.current).toMatchObject({ status: 'signed-in', crewId: 'AST-02' });
});

it('locks through the api and shows the lock state', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  const { result } = setup(api);
  await waitFor(() => {
    expect(result.current.status).toBe('signed-in');
  });
  await act(async () => {
    await result.current.lock();
  });
  expect(result.current).toMatchObject({ status: 'locked', crewId: 'AST-01' });
  expect(api.calls).toContain('auth.lock');
});

it('treats an unreadable session as signed-out, never signed-in', async () => {
  const api = new FakeAsclepiusApi();
  api.auth.current = () => Promise.reject(new Error('boom'));
  const { result } = setup(api);
  await waitFor(() => {
    expect(result.current.status).toBe('signed-out');
  });
});

it('signOut drops the local session (dev menu only: main keeps its own)', async () => {
  const api = new FakeAsclepiusApi();
  api.session = { crewId: 'AST-01', locked: false };
  const { result } = setup(api);
  await waitFor(() => {
    expect(result.current.status).toBe('signed-in');
  });
  act(() => {
    result.current.signOut();
  });
  expect(result.current.status).toBe('signed-out');
});

it('useAuth outside a provider fails loudly', () => {
  expect(() => renderHook(() => useAuth())).toThrow(/AuthProvider/);
});

it.each([
  ['loading', '/today', null],
  ['signed-out', '/today', '/login'],
  ['signed-out', '/login', null],
  ['locked', '/history', '/lock'],
  ['locked', '/lock', null],
  ['signed-in', '/login', '/today'],
  ['signed-in', '/lock', '/today'],
  ['signed-in', '/history', null],
] as const)('redirectFor(%s, %s) -> %s', (status, path, expected) => {
  expect(redirectFor(status, path)).toBe(expected);
});
