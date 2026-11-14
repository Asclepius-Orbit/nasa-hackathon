import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import { prepareIdempotentWrite } from './idempotent-write';
import { createWriteClient, type WriteFetch } from './write-client';

describe('prepareIdempotentWrite', () => {
  it.each(['report_id', 'contact_id'] as const)('reuses %s and the original payload after an uncertain failure', async (field) => {
    const bodies: unknown[] = [];
    const fetch: WriteFetch = (_url, init) => {
      if (init.body === undefined) return Promise.reject(new Error('expected a request body'));
      bodies.push(JSON.parse(init.body) as unknown);
      if (bodies.length === 1) return Promise.reject(new Error('response lost'));
      return Promise.resolve(new Response('{}', { status: 200 }));
    };
    const writer = createWriteClient({ baseUrl: 'http://127.0.0.1:8000', fetch });
    const fields = { value: { score: 1 } };
    const retry = prepareIdempotentWrite(writer, { path: '/ui/test', astronautId: 'AST-01', schema: z.object({}) }, field, fields, () => 'id-1');
    expect(await retry()).toEqual({ ok: false, reason: 'unavailable' });
    fields.value.score = 2;
    expect((await retry()).ok).toBe(true);
    expect(bodies).toEqual([{ value: { score: 1 }, [field]: 'id-1' }, { value: { score: 1 }, [field]: 'id-1' }]);
  });
});
