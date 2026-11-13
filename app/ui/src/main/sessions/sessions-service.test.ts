import { describe, expect, it } from 'vitest';

import type { AuthSession, CrewMember, WriteResult } from '../../shared/api';
import { FakeBackend } from '../profile/test-fakes';
import { ViewsError } from '../views/views-error';
import { PROTOCOLS } from './protocols';
import { createSessionsService } from './sessions-service';

const ID = 'AST-01';
const UNLOCKED: AuthSession = { crewId: ID, locked: false };
const SESSION = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01';
const COMMAND = '7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f12';
const CREW: CrewMember[] = [
  { id: ID, name: 'Elena Ruiz', harnessId: 'wb-01' },
  { id: 'AST-02', name: 'Marc Dubois', harnessId: null },
];

function build(session: AuthSession | null = UNLOCKED, crew: CrewMember[] | null = CREW) {
  const backend = new FakeBackend();
  const logged: unknown[] = [];
  const service = createSessionsService({
    auth: { current: () => session },
    crew: { list: () => Promise.resolve(crew) },
    records: backend.records,
    writer: backend.writer,
    logError: (e) => logged.push(e),
  });
  return { backend, service, logged };
}

const commandAnswer = { command: { command_id: COMMAND, type: 'stop' } };
const LOCKED = { ok: false, reason: 'locked' };

describe('createSessionsService start', () => {
  it("starts an exercise on the unlocked member's harness with the protocol's load, sets, reps and streams", async () => {
    const { backend, service } = build();
    backend.sendAnswer = { session: { session_id: SESSION, streams: [] }, state: 'starting', command: { command_id: COMMAND } };
    expect(await service.start({ protocolId: 'lat_pulldown_standing_v1' })).toEqual({
      ok: true,
      value: { sessionId: SESSION, state: 'starting' },
    });
    expect(backend.sent).toEqual([
      {
        method: 'POST',
        path: `/ui/crew/${ID}/sessions`,
        astronautId: ID,
        body: {
          device_id: 'wb-01',
          protocol_id: 'lat_pulldown_standing_v1',
          session_type: 'exercise',
          load_declared: { type: 'elastic_band', nominal_kg: 25, band_id: 'green' },
          planned_reps: 60,
          planned_sets: 4,
          reps_per_set: 15,
          streams: PROTOCOLS.lat_pulldown_standing_v1.streams,
        },
      },
    ]);
  });

  it('starts a spine check without load or reps (the backend fills mission_context)', async () => {
    const { backend, service } = build();
    backend.sendAnswer = { session: { session_id: SESSION }, state: 'starting' };
    await service.start({ protocolId: 'spine_length_rest_v1' });
    expect(backend.sent[0]?.body).toEqual({
      device_id: 'wb-01',
      protocol_id: 'spine_length_rest_v1',
      session_type: 'spine_check',
      streams: PROTOCOLS.spine_length_rest_v1.streams,
    });
  });

  it('answers no_harness without sending when the member has none paired', async () => {
    const { backend, service } = build({ crewId: 'AST-02', locked: false });
    expect(await service.start({ protocolId: 'spine_length_rest_v1' })).toEqual({ ok: false, reason: 'no_harness' });
    expect(backend.sent).toEqual([]);
  });

  it.each([null, { crewId: ID, locked: true }])('is locked without sending (%j)', async (session) => {
    const { backend, service } = build(session);
    expect(await service.start({ protocolId: 'spine_length_rest_v1' })).toEqual(LOCKED);
    expect(backend.sent).toEqual([]);
  });

  it('is unavailable when the crew store cannot be read', async () => {
    const { backend, service } = build(UNLOCKED, null);
    expect(await service.start({ protocolId: 'spine_length_rest_v1' })).toEqual({ ok: false, reason: 'unavailable' });
    expect(backend.sent).toEqual([]);
  });

  it.each<WriteResult<never>>([
    { ok: false, reason: 'invalid', fields: [] },
    { ok: false, reason: 'conflict' },
    { ok: false, reason: 'unavailable' },
  ])('passes a refusal through ($reason)', async (answer) => {
    const { backend, service } = build();
    backend.writeAnswer = answer;
    expect(await service.start({ protocolId: 'lat_pulldown_standing_v1' })).toEqual(answer);
  });

  it.each([
    ['an uppercase session id', { session: { session_id: SESSION.toUpperCase() }, state: 'starting' }],
    ['another state', { session: { session_id: SESSION }, state: 'running' }],
    ['no session', { state: 'starting' }],
  ])('refuses an answer with %s', async (_label, answer) => {
    const { backend, service } = build();
    backend.sendAnswer = answer;
    await expect(service.start({ protocolId: 'spine_length_rest_v1' })).rejects.toThrow(/outside the schema/);
  });
});

describe('createSessionsService commands', () => {
  it.each([
    [{ sessionId: SESSION, reason: 'completed' } as const, { reason: 'completed' }],
    [{ sessionId: SESSION, reason: 'aborted_pain', repIndex: 6 } as const, { reason: 'aborted_pain', rep_index: 6 }],
    [{ sessionId: SESSION, reason: 'aborted_pain', repIndex: null } as const, { reason: 'aborted_pain' }],
  ])('stops %j with body %j', async (input, body) => {
    const { backend, service } = build();
    backend.sendAnswer = commandAnswer;
    expect(await service.stop(input)).toEqual({ ok: true, value: { commandId: COMMAND } });
    expect(backend.sent).toEqual([{ method: 'POST', path: `/ui/sessions/${SESSION}/stop`, astronautId: ID, body }]);
  });

  it.each([
    [6, { event_type: 'artifact', rep_index: 6 }],
    [null, { event_type: 'artifact' }],
  ])('marks an artefact at rep %s', async (repIndex, body) => {
    const { backend, service } = build();
    backend.sendAnswer = commandAnswer;
    expect(await service.markArtifact({ sessionId: SESSION, repIndex })).toEqual({ ok: true, value: { commandId: COMMAND } });
    expect(backend.sent).toEqual([{ method: 'POST', path: `/ui/sessions/${SESSION}/events`, astronautId: ID, body }]);
  });

  it("calibrates the MVC of the session's EMG streams", async () => {
    const { backend, service } = build();
    backend.sessionStreams.set(SESSION, [
      { streamId: 'emg_lat_left', kind: 'emg' },
      { streamId: 'imu_t9', kind: 'imu' },
      { streamId: 'emg_lat_right', kind: 'emg' },
    ]);
    backend.sendAnswer = commandAnswer;
    expect(await service.calibrateMvc({ sessionId: SESSION })).toEqual({ ok: true, value: { commandId: COMMAND } });
    expect(backend.sent).toEqual([
      {
        method: 'POST',
        path: `/ui/sessions/${SESSION}/calibrations`,
        astronautId: ID,
        body: { calibration_type: 'emg_mvc', stream_ids: ['emg_lat_left', 'emg_lat_right'] },
      },
    ]);
  });

  it('refuses an MVC calibration of a session without EMG streams, without sending', async () => {
    const { backend, service } = build();
    backend.sessionStreams.set(SESSION, [{ streamId: 'stretch_spine', kind: 'stretch' }]);
    expect(await service.calibrateMvc({ sessionId: SESSION })).toEqual({
      ok: false,
      reason: 'invalid',
      fields: [{ pointer: '/stream_ids', message: 'no EMG streams' }],
    });
    expect(backend.sent).toEqual([]);
  });

  it.each([
    ['an unknown session', null, { ok: false, reason: 'unavailable' }],
    ['a locked read', new ViewsError('locked'), LOCKED],
    ['a failed read', new ViewsError('unavailable', { cause: new Error('down') }), { ok: false, reason: 'unavailable' }],
  ])('answers %s before calibrating as a failure', async (_label, failure, expected) => {
    const { backend, service } = build();
    if (failure !== null) backend.readFailure = failure;
    expect(await service.calibrateMvc({ sessionId: SESSION })).toEqual(expected);
    expect(backend.sent).toEqual([]);
  });

  it('reprocesses a session with no body', async () => {
    const { backend, service } = build();
    backend.sendAnswer = { session: SESSION, state: 'ended' };
    expect(await service.reprocess({ sessionId: SESSION })).toEqual({ ok: true, value: { sessionId: SESSION } });
    expect(backend.sent).toEqual([{ method: 'POST', path: `/ui/sessions/${SESSION}/reprocess`, astronautId: ID, body: undefined }]);
  });

  it('passes a reprocess conflict through (the session is not processing_failed)', async () => {
    const { backend, service } = build();
    backend.writeAnswer = { ok: false, reason: 'conflict' };
    expect(await service.reprocess({ sessionId: SESSION })).toEqual({ ok: false, reason: 'conflict' });
  });

  it('refuses a command answer without a command id', async () => {
    const { backend, service } = build();
    backend.sendAnswer = { command: {} };
    await expect(service.stop({ sessionId: SESSION, reason: 'completed' })).rejects.toThrow(/outside the schema/);
  });

  it.each([null, { crewId: ID, locked: true }])('answers every command with locked when nobody is unlocked (%j)', async (session) => {
    const { backend, service } = build(session);
    expect(await service.stop({ sessionId: SESSION, reason: 'completed' })).toEqual(LOCKED);
    expect(await service.markArtifact({ sessionId: SESSION, repIndex: null })).toEqual(LOCKED);
    expect(await service.calibrateMvc({ sessionId: SESSION })).toEqual(LOCKED);
    expect(await service.reprocess({ sessionId: SESSION })).toEqual(LOCKED);
    expect(backend.sent).toEqual([]);
  });
});
