import { act, renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { useFormSave } from './useFormSave';

it('ignores a second save before React renders the busy state', async () => {
  let release: () => void = () => undefined;
  const send = vi.fn(() => new Promise<{ ok: true; value: unknown }>((resolve) => {
    release = () => { resolve({ ok: true, value: null }); };
  }));
  const { result } = renderHook(() => useFormSave([], {
    unavailable: 'unavailable', locked: 'locked', conflict: 'conflict', invalid: 'invalid',
  }));
  act(() => {
    result.current.run(send);
    result.current.run(send);
  });
  await waitFor(() => { expect(send).toHaveBeenCalledTimes(1); });
  act(() => { release(); });
  await waitFor(() => { expect(result.current.state.status).toBe('saved'); });
});
