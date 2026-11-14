import { describe, expect, it } from 'vitest';

import { normalizeViewsError, viewsCall } from './views-errors';

/** What `ipcRenderer.invoke` rejects with when the main handler throws `new Error(code)`. */
const wrapped = (code: string, channel = 'asclepius:views:today'): Error =>
  new Error(`Error invoking remote method '${channel}': Error: ${code}`);

describe('normalizeViewsError', () => {
  it.each(['locked', 'not_found', 'unavailable'])('unwraps the Electron message of %s', (code) => {
    const error = normalizeViewsError(wrapped(code));
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe(code);
  });

  it('unwraps a subclass name in the wrapped message', () => {
    expect(normalizeViewsError(new Error("Error invoking remote method 'asclepius:views:session': ViewsError: not_found")).message).toBe(
      'not_found',
    );
  });

  it('keeps a bare code as it is', () => {
    expect(normalizeViewsError(new Error('locked')).message).toBe('locked');
  });

  it.each([
    ['an unknown code', wrapped('boom')],
    ['a code with trailing text', new Error("Error invoking remote method 'asclepius:views:today': Error: locked, please")],
    ['a code buried in other text', new Error('backend said not_found')],
    ['an invalid-input failure', new Error("Error invoking remote method 'asclepius:views:history': Error: invalid_input")],
    ['an empty message', new Error('')],
    ['a string', 'locked now'],
    ['undefined', undefined],
    ['an object', { message: 'locked' }],
  ])('maps %s to unavailable and never leaks its text', (_label, reason) => {
    expect(normalizeViewsError(reason).message).toBe('unavailable');
  });
});

describe('viewsCall', () => {
  it('passes the resolved value through', async () => {
    expect(await viewsCall(() => Promise.resolve(42))).toBe(42);
  });

  it('rejects with the normalized error', async () => {
    await expect(viewsCall(() => Promise.reject(wrapped('locked')))).rejects.toThrow(/^locked$/);
    await expect(viewsCall(() => Promise.reject(wrapped('weird')))).rejects.toThrow(/^unavailable$/);
  });

  it('turns a synchronous throw into the same rejection', async () => {
    await expect(
      viewsCall(() => {
        throw new Error('not_found');
      }),
    ).rejects.toThrow(/^not_found$/);
  });
});
