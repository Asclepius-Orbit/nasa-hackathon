/**
 * Session commands of the unlocked astronaut (contract: `ui-openapi.yaml`, POST sessions, stop, events, calibrations,
 * reprocess). Nobody unlocked is `locked` with nothing sent. That the session belongs to the unlocked astronaut is the
 * backend's check (`X-Astronaut-Id` against the session owner: 403, `locked`); it is not looked up again here.
 */
import { z } from 'zod';

import type {
  CommandSent,
  ProtocolId,
  StartedSession,
  StartSessionResult,
  StopInput,
  WriteResult,
} from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import { readFailure, unlockedId } from '../profile/unlocked';
import type { CrewStore } from '../store/crew-store';
import type { RecordsClient } from '../views/records-client';
import type { WriteClient } from '../views/write-client';
import { PROTOCOLS } from './protocols';

export interface SessionsService {
  start(input: { protocolId: ProtocolId }): Promise<StartSessionResult>;
  stop(input: StopInput): Promise<WriteResult<CommandSent>>;
  markArtifact(input: { sessionId: string; repIndex: number | null }): Promise<WriteResult<CommandSent>>;
  calibrateMvc(input: { sessionId: string }): Promise<WriteResult<CommandSent>>;
  reprocess(input: { sessionId: string }): Promise<WriteResult<{ sessionId: string }>>;
}

export interface SessionsServiceOptions {
  auth: Pick<AuthService, 'current'>;
  crew: Pick<CrewStore, 'list'>;
  records: Pick<RecordsClient, 'sessionStreams'>;
  writer: Pick<WriteClient, 'send'>;
  logError?: (error: unknown) => void;
}

const lowercaseUuid = z.uuid().refine((id) => id === id.toLowerCase());

const startedSchema: z.ZodType<StartedSession> = z
  .looseObject({ session: z.looseObject({ session_id: lowercaseUuid }), state: z.literal('starting') })
  .transform(({ session }) => ({ sessionId: session.session_id, state: 'starting' as const }));

const commandSchema: z.ZodType<CommandSent> = z
  .looseObject({ command: z.looseObject({ command_id: z.string() }) })
  .transform(({ command }) => ({ commandId: command.command_id }));

const reprocessSchema: z.ZodType<{ sessionId: string }> = z
  .looseObject({ session: lowercaseUuid })
  .transform(({ session }) => ({ sessionId: session }));

const LOCKED = { ok: false, reason: 'locked' } as const;

export function createSessionsService({
  auth,
  crew,
  records,
  writer,
  logError = () => undefined,
}: SessionsServiceOptions): SessionsService {
  /** POST to a session of the unlocked astronaut; `locked` with nothing sent when nobody is. */
  function command<T>(sessionId: string, action: string, body: unknown, schema: z.ZodType<T>): Promise<WriteResult<T>> {
    const id = unlockedId(auth);
    if (id === null) return Promise.resolve(LOCKED);
    const path = `/ui/sessions/${encodeURIComponent(sessionId)}/${action}`;
    return writer.send({ method: 'POST', path, astronautId: id, body, schema });
  }

  return {
    async start({ protocolId }) {
      const id = unlockedId(auth);
      if (id === null) return LOCKED;
      const members = await crew.list();
      if (members === null) return { ok: false, reason: 'unavailable' };
      const harnessId = members.find((m) => m.id === id)?.harnessId ?? null;
      if (harnessId === null) return { ok: false, reason: 'no_harness' };
      const { session_type: sessionType, load_declared: load, planned_reps: reps, planned_sets: sets, reps_per_set: repsPerSet, streams } = PROTOCOLS[protocolId];
      // No mission_context: the backend takes it from today's daily context (none stored: a 422, `invalid`).
      const body = {
        device_id: harnessId,
        protocol_id: protocolId,
        session_type: sessionType,
        ...(load === undefined ? {} : { load_declared: load }),
        ...(reps === undefined ? {} : { planned_reps: reps }),
        ...(sets === undefined ? {} : { planned_sets: sets }),
        ...(repsPerSet === undefined ? {} : { reps_per_set: repsPerSet }),
        streams,
      };
      return writer.send({ method: 'POST', path: `/ui/crew/${encodeURIComponent(id)}/sessions`, astronautId: id, body, schema: startedSchema });
    },

    stop: (input) =>
      command(
        input.sessionId,
        'stop',
        input.reason === 'aborted_pain' && input.repIndex !== null
          ? { reason: input.reason, rep_index: input.repIndex }
          : { reason: input.reason },
        commandSchema,
      ),

    markArtifact: ({ sessionId, repIndex }) =>
      command(sessionId, 'events', { event_type: 'artifact', ...(repIndex === null ? {} : { rep_index: repIndex }) }, commandSchema),

    async calibrateMvc({ sessionId }) {
      const id = unlockedId(auth);
      if (id === null) return LOCKED;
      let streams;
      try {
        streams = await records.sessionStreams(id, sessionId);
      } catch (error) {
        return readFailure(error, logError);
      }
      if (streams === null) return { ok: false, reason: 'unavailable' };
      const emg = streams.filter((s) => s.kind === 'emg').map((s) => s.streamId);
      if (emg.length === 0) return { ok: false, reason: 'invalid', fields: [{ pointer: '/stream_ids', message: 'no EMG streams' }] };
      return command(sessionId, 'calibrations', { calibration_type: 'emg_mvc', stream_ids: emg }, commandSchema);
    },

    reprocess: ({ sessionId }) => command(sessionId, 'reprocess', undefined, reprocessSchema),
  };
}
