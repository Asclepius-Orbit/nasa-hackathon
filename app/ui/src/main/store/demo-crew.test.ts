import { describe, expect, it } from 'vitest';

import { DEMO_CREW, e2eRealCrew } from './demo-crew';

describe('e2eRealCrew', () => {
  it('pairs the first member to the harness of the run', () => {
    const crew = e2eRealCrew({ E2E_REAL_HARNESS: 'gw-demo' });
    expect(crew.members.map((m) => m.harnessId)).toEqual(['gw-demo', 'sim-02']);
    expect(crew.pin).toBe(DEMO_CREW.pin);
  });

  it.each([{}, { E2E_REAL_HARNESS: '' }, { E2E_REAL_HARNESS: '  ' }])('falls back to sim-01 for %j', (env) => {
    expect(e2eRealCrew(env)).toEqual(DEMO_CREW);
  });
});
