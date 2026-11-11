/**
 * What main declares when it starts a session of each protocol the UI offers (`domain/session`).
 *
 * Band and repetitions are the catalog's (`spine_monitor/contracts/catalog/protocol-catalog.json`; the file lies outside
 * the app, so it is copied here and `protocols.test.ts` checks the copy). The streams cannot come from the device: its
 * status (`telemetry/device-status`) names each stream and rate but not where it is placed on the body, which a session
 * must declare. Until the standard protocol (#12) fixes the harness layout, they are those of the contract examples
 * `examples/valid/domain/session.exercise.json` and `session.spine-check.json` (also checked by the test).
 */
import type { ProtocolId } from '../../shared/api';

export interface StreamDeclaration {
  stream_id: string;
  stream_kind: 'emg' | 'imu' | 'stretch';
  placement: Record<string, unknown>;
  sample_rate_hz: number;
}

export interface ProtocolTemplate {
  session_type: 'exercise' | 'spine_check';
  load_declared?: { type: 'elastic_band'; nominal_kg: number; band_id: string };
  planned_reps?: number;
  streams: readonly StreamDeclaration[];
}

const STRETCH_LUMBAR: StreamDeclaration = {
  stream_id: 'stretch_lumbar',
  stream_kind: 'stretch',
  placement: { spine_region: 'lumbar', span: { from_level: 'l1', to_level: 'l5' } },
  sample_rate_hz: 20,
};

export const PROTOCOLS: Readonly<Record<ProtocolId, ProtocolTemplate>> = Object.freeze({
  lat_pulldown_standing_v1: {
    session_type: 'exercise',
    load_declared: { type: 'elastic_band', nominal_kg: 15, band_id: 'blue' },
    planned_reps: 10,
    streams: [
      {
        stream_id: 'emg_lat_left',
        stream_kind: 'emg',
        placement: { muscle: 'latissimus_dorsi', side: 'left' },
        sample_rate_hz: 1000,
      },
      { stream_id: 'imu_l3', stream_kind: 'imu', placement: { spine_level: 'l3' }, sample_rate_hz: 100 },
      STRETCH_LUMBAR,
    ],
  },
  spine_length_rest_v1: { session_type: 'spine_check', streams: [STRETCH_LUMBAR] },
});
