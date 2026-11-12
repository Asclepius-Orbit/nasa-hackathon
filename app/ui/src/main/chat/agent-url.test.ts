import { describe, expect, it, vi } from 'vitest';

import { AGENT_URL_ENV, DEFAULT_AGENT_URL, resolveAgentUrl } from './agent-url';

describe('resolveAgentUrl', () => {
  it('uses the default when nothing is set', () => {
    expect(AGENT_URL_ENV).toBe('ASCLEPIUS_AGENT_URL');
    expect(resolveAgentUrl(undefined, vi.fn())).toBe('http://127.0.0.1:8003');
    expect(resolveAgentUrl('', vi.fn())).toBe(DEFAULT_AGENT_URL);
  });

  it('accepts a loopback origin and normalises it', () => {
    expect(resolveAgentUrl('http://localhost:9000/', vi.fn())).toBe('http://localhost:9000');
  });

  it('reports and ignores anything that is not loopback', () => {
    const onInvalid = vi.fn();
    expect(resolveAgentUrl('http://evil.example:8003', onInvalid)).toBe(DEFAULT_AGENT_URL);
    expect(onInvalid).toHaveBeenCalledWith('http://evil.example:8003');
  });
});
