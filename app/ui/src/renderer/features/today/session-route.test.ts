import { describe, expect, it } from 'vitest';

import { isSessionRoute } from './session-route';

describe('isSessionRoute', () => {
  it.each(['/session', '/session?protocol=spine_resistance_straight_v1', '/session?stage=reps'])('treats %s as the session screen', (route) => {
    expect(isSessionRoute(route)).toBe(true);
  });

  it.each([undefined, null, '/sessions', '/baseline', '/report?x=/session'])('does not treat %s as the session screen', (route) => {
    expect(isSessionRoute(route)).toBe(false);
  });
});
