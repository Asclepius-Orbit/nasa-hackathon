import { describe, expect, it } from 'vitest';

import { IPC_CHANNELS, IPC_EVENT_CHANNELS } from './ipc-channels';
import {
  createCrewInputSchema,
  crewIdSchema,
  historyInputSchema,
  sessionInputSchema,
  nameSchema,
  pinSchema,
  repsMessageSchema,
  repsUnsubscribeInputSchema,
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
      'asclepius:crew:set-harness',
      'asclepius:profile:get',
      'asclepius:profile:update',
      'asclepius:daily-context:today',
      'asclepius:daily-context:update-today',
      'asclepius:devices:list',
      'asclepius:sessions:start',
      'asclepius:sessions:stop',
      'asclepius:sessions:mark-artifact',
      'asclepius:sessions:calibrate-mvc',
      'asclepius:sessions:reprocess',
      'asclepius:reports:save-daily',
      'asclepius:reports:save-oswestry',
      'asclepius:reports:add-exercise',
      'asclepius:reports:due',
      'asclepius:urgent:send',
      'asclepius:contacts:list',
      'asclepius:contacts:log-officer-call',
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
      'asclepius:views:houston',
      'asclepius:views:outbox',
      'asclepius:reps:subscribe',
      'asclepius:reps:unsubscribe',
    ]);
    expect(new Set(names).size).toBe(names.length);
  });

  it('keeps main-to-renderer event channels apart from the invoke channels', () => {
    expect(IPC_EVENT_CHANNELS).toEqual({ repsEvent: 'asclepius:reps:event' });
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
    // 366 days inclusive: a leap year, and the longest window allowed.
    { from: '2028-01-01', to: '2028-12-31' },
    { from: '2027-01-01', to: '2028-01-01' },
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
    ['a window of 367 days', { from: '2027-01-01', to: '2028-01-02' }],
    ['a window of many years', { from: '2000-01-01', to: '2026-11-01' }],
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

const SUBSCRIPTION = '0b9c3d4e-1f2a-4b5c-8d6e-7f8091a2b3c4';
const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const REP_STATE = {
  schema_version: '1.0.0',
  session_id: SESSION,
  rep: 2,
  total: 10,
  phase: 'pulling',
  progress_pct: 40.5,
  last: { rep: 1, duration_s: 3.2 },
  signal: 'ok',
  as_of: '2026-11-08T10:00:00Z',
};
const message = (event: unknown, extra: object = {}) => ({ subscriptionId: SUBSCRIPTION, sessionId: SESSION, event, ...extra });

describe('repsUnsubscribeInputSchema', () => {
  it('accepts { subscriptionId } with a UUID only', () => {
    expect(repsUnsubscribeInputSchema.safeParse({ subscriptionId: SUBSCRIPTION }).success).toBe(true);
    expect(repsUnsubscribeInputSchema.safeParse({ subscriptionId: 'abc' }).success).toBe(false);
    expect(repsUnsubscribeInputSchema.safeParse({ subscriptionId: SUBSCRIPTION, x: 1 }).success).toBe(false);
  });
});

describe('repsMessageSchema', () => {
  it.each([
    ['state', { kind: 'state', state: REP_STATE }],
    ['end', { kind: 'end', state: { ...REP_STATE, signal: 'no_signal', phase: null, progress_pct: null, total: null, last: null } }],
    ['lost', { kind: 'lost' }],
    ['error', { kind: 'error', code: 'no_reps' }],
    ['error', { kind: 'error', code: 'not_found' }],
  ])('accepts a %s event with the subscription and session ids', (_kind, event) => {
    expect(repsMessageSchema.safeParse(message(event)).success).toBe(true);
  });

  it('drops fields the contract does not know instead of forwarding them', () => {
    const parsed = repsMessageSchema.parse(message({ kind: 'state', state: { ...REP_STATE, secret: 'x', last: { rep: 1, duration_s: 3.2, y: 1 } } }));
    expect(parsed.event).toEqual({ kind: 'state', state: REP_STATE });
  });

  it.each([
    ['an unknown kind', message({ kind: 'other' })],
    ['an error code that is not public', message({ kind: 'error', code: 'forbidden' })],
    ['a lost event with a payload', message({ kind: 'lost', code: 'x' })],
    ['a missing session id', { subscriptionId: SUBSCRIPTION, event: { kind: 'lost' } }],
    ['an uppercase session id', message({ kind: 'lost' }, { sessionId: SESSION.toUpperCase() })],
    ['a state of another contract major', message({ kind: 'state', state: { ...REP_STATE, schema_version: '2.0.0' } })],
    ['a negative rep', message({ kind: 'state', state: { ...REP_STATE, rep: -1 } })],
    ['a progress above 100', message({ kind: 'state', state: { ...REP_STATE, progress_pct: 101 } })],
    ['an unknown phase', message({ kind: 'state', state: { ...REP_STATE, phase: 'flying' } })],
    ['a timestamp without offset', message({ kind: 'state', state: { ...REP_STATE, as_of: '2026-11-08T10:00:00' } })],
    ['a state without signal', message({ kind: 'state', state: { ...REP_STATE, signal: undefined } })],
  ])('rejects %s', (_label, input) => {
    expect(repsMessageSchema.safeParse(input).success).toBe(false);
  });
});
