import { describe, expect, it } from 'vitest';

import { stages } from './session';

describe('finish stage lede', () => {
  it('says the rest in whole minutes when it is a whole number of minutes', () => {
    expect(stages.finish.lede(120)).toContain('stand still for 2 min.');
    expect(stages.finish.lede(60)).toContain('stand still for 1 min.');
  });

  it('says minutes and seconds when the rest is not whole minutes', () => {
    expect(stages.finish.lede(90)).toContain('stand still for 1 min 30 s.');
  });

  it('says seconds when the rest is under a minute', () => {
    expect(stages.finish.lede(45)).toContain('stand still for 45 s.');
  });

  it('gives no duration when the protocol has no rest', () => {
    expect(stages.finish.lede(null)).not.toMatch(/\d/);
  });
});
