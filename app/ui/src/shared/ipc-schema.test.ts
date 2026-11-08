import { describe, expect, it } from 'vitest';

import { IPC_CHANNELS } from './ipc-channels';
import {
  createCrewInputSchema,
  crewIdSchema,
  historyInputSchema,
  sessionInputSchema,
  nameSchema,
  pinSchema,
  settingsPatchSchema,
  settingsSchema,
  verifyPinInputSchema,
} from './ipc-schema';

describe('IPC_CHANNELS', () => {
  it('namespaces every channel under asclepius: and keeps them unique', () => {
    const names = Object.values(IPC_CHANNELS);
    expect(names).toEqual([
      'asclepius:crew:list',
      'asclepius:crew:next-id',
      'asclepius:crew:create',
      'asclepius:auth:verify-pin',
      'asclepius:auth:lock',
      'asclepius:auth:current',
      'asclepius:settings:get',
      'asclepius:settings:update',
      'asclepius:views:strip',
      'asclepius:views:today',
      'asclepius:views:history',
      'asclepius:views:baseline',
      'asclepius:views:first-day',
      'asclepius:views:session',
    ]);
    expect(new Set(names).size).toBe(names.length);
  });
});

describe('crewIdSchema', () => {
  it.each(['AST-01', 'AST-99', 'AST-00'])('accepts %s', (id) => {
    expect(crewIdSchema.safeParse(id).success).toBe(true);
  });
  it.each(['AST-1', 'AST-001', 'ast-01', ' AST-01', 'AST-01\n', 'AST-٠١', 7, null])('rejects %j', (id) => {
    expect(crewIdSchema.safeParse(id).success).toBe(false);
  });
});

describe('pinSchema', () => {
  it('accepts exactly four ASCII digits', () => {
    expect(pinSchema.safeParse('0042').success).toBe(true);
  });
  it.each(['123', '12345', '12a4', '١٢٣٤', '1234\n', 1234])('rejects %j', (pin) => {
    expect(pinSchema.safeParse(pin).success).toBe(false);
  });
});

describe('nameSchema', () => {
  it('accepts 1..40 characters including accents and inner spaces', () => {
    expect(nameSchema.safeParse('Ana García').success).toBe(true);
    expect(nameSchema.safeParse('x'.repeat(40)).success).toBe(true);
    expect(nameSchema.safeParse('x').success).toBe(true);
  });
  it.each(['', 'x'.repeat(41), 'Ana\u0000', 'Ana\nGarcía', 'Ana\u007f', 'Ana\u0085', 42])('rejects %j', (name) => {
    expect(nameSchema.safeParse(name).success).toBe(false);
  });
});

describe('createCrewInputSchema', () => {
  it('accepts { name, pin }', () => {
    expect(createCrewInputSchema.parse({ name: 'Ana', pin: '1234' })).toEqual({ name: 'Ana', pin: '1234' });
  });
  it('rejects extra keys, missing keys and non-objects', () => {
    expect(createCrewInputSchema.safeParse({ name: 'Ana', pin: '1234', admin: true }).success).toBe(false);
    expect(createCrewInputSchema.safeParse({ name: 'Ana' }).success).toBe(false);
    expect(createCrewInputSchema.safeParse('Ana').success).toBe(false);
    expect(createCrewInputSchema.safeParse({ name: 'x'.repeat(41), pin: '1234' }).success).toBe(false);
  });
});

describe('verifyPinInputSchema', () => {
  it('accepts { crewId, pin }', () => {
    expect(verifyPinInputSchema.safeParse({ crewId: 'AST-01', pin: '1234' }).success).toBe(true);
  });
  it('rejects extra keys and bad fields', () => {
    expect(verifyPinInputSchema.safeParse({ crewId: 'AST-01', pin: '1234', x: 1 }).success).toBe(false);
    expect(verifyPinInputSchema.safeParse({ crewId: 'AST-1', pin: '1234' }).success).toBe(false);
    expect(verifyPinInputSchema.safeParse({ crewId: 'AST-01', pin: '12345' }).success).toBe(false);
  });
});

describe('settings schemas', () => {
  const full = { lengthUnit: 'mm', timeFormat: 'gmt', textSize: 'large', voiceCommands: true, footPedal: false };

  it('settingsSchema accepts a full settings object and rejects extra keys or missing ones', () => {
    expect(settingsSchema.safeParse(full).success).toBe(true);
    expect(settingsSchema.safeParse({ ...full, theme: 'dark' }).success).toBe(false);
    const { footPedal: _omit, ...partial } = full;
    expect(settingsSchema.safeParse(partial).success).toBe(false);
  });

  it('settingsPatchSchema accepts any strict subset', () => {
    expect(settingsPatchSchema.safeParse({}).success).toBe(true);
    expect(settingsPatchSchema.safeParse({ lengthUnit: 'in' }).success).toBe(true);
    expect(settingsPatchSchema.safeParse({ timeFormat: 'gmt_met', textSize: 'standard' }).success).toBe(true);
  });

  it('settingsPatchSchema rejects extra keys, wrong enum values and wrong types', () => {
    expect(settingsPatchSchema.safeParse({ theme: 'dark' }).success).toBe(false);
    expect(settingsPatchSchema.safeParse({ lengthUnit: 'cm' }).success).toBe(false);
    expect(settingsPatchSchema.safeParse({ voiceCommands: 'yes' }).success).toBe(false);
    expect(settingsPatchSchema.safeParse({ footPedal: undefined }).success).toBe(false);
    expect(settingsPatchSchema.safeParse(null).success).toBe(false);
  });
});

describe('historyInputSchema', () => {
  it.each([
    {},
    { from: '2026-11-01' },
    { to: '2026-11-07' },
    { from: '2026-11-01', to: '2026-11-07' },
    { from: '2026-11-01', to: '2026-11-01' },
    { from: '2028-02-29' },
  ])('accepts %j', (range) => {
    expect(historyInputSchema.safeParse(range).success).toBe(true);
  });

  it.each([
    ['a non-object', '2026-11-01'],
    ['null', null],
    ['extra keys', { from: '2026-11-01', astronautId: 'AST-01' }],
    ['a date-time', { from: '2026-11-01T00:00:00Z' }],
    ['a date with a trailing newline', { from: '2026-11-01\n' }],
    ['a non-existent day', { from: '2026-02-30' }],
    ['a month 13', { to: '2026-13-01' }],
    ['a number', { from: 20261101 }],
    ['an explicit undefined', { from: undefined }],
    ['an inverted range', { from: '2026-11-08', to: '2026-11-01' }],
  ])('rejects %s', (_label, range) => {
    expect(historyInputSchema.safeParse(range).success).toBe(false);
  });
});

describe('sessionInputSchema', () => {
  it('accepts { sessionId } with a UUID', () => {
    expect(sessionInputSchema.safeParse({ sessionId: '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b' }).success).toBe(true);
  });

  it.each([
    ['a missing id', {}],
    ['a bare string', '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b'],
    ['a non-UUID', { sessionId: 'abc' }],
    ['a path traversal', { sessionId: '../../x' }],
    ['an uppercase UUID', { sessionId: '3F2B8C1E-5D4A-4E6F-9A7B-1C2D3E4F5A6B' }],
    ['extra keys', { sessionId: '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b', x: 1 }],
  ])('rejects %s', (_label, input) => {
    expect(sessionInputSchema.safeParse(input).success).toBe(false);
  });
});
