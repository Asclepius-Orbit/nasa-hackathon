/**
 * What main declares when it starts a session of each protocol the UI offers (`domain/session`).
 *
 * Band, sets and repetitions are the catalog's (`spine_monitor/contracts/catalog/protocol-catalog.json`; the file lies
 * outside the app, so it is copied here and `protocols.test.ts` checks the copy). The streams cannot come from the
 * device: its status (`telemetry/device-status`) names each stream and rate but not where it is placed on the body,
 * which a session must declare. They are those of the contract examples
 * `examples/valid/domain/session.exercise.json` and `session.spine-check.json` (also checked by the test): standing
 * lat pulldown, EMG on each latissimus dorsi (10 cm between electrodes, T11 to L2, ground on the iliac crest), IMU at
 * T9, cord from C3 to S1. The cord calibration and the MVC are pending Aguado.
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
  /** TOTAL repetitions (all sets). */
  planned_reps?: number;
  planned_sets?: number;
  reps_per_set?: number;
  streams: readonly StreamDeclaration[];
}

const STRETCH_SPINE: StreamDeclaration = {
  stream_id: 'stretch_spine',
  stream_kind: 'stretch',
  placement: { spine_region: 'full_spine', span: { from_level: 'c3', to_level: 's1' } },
  sample_rate_hz: 20,
};

const emgLat = (side: 'left' | 'right'): StreamDeclaration => ({
  stream_id: `emg_lat_${side}`,
  stream_kind: 'emg',
  placement: {
    muscle: 'latissimus_dorsi',
    side,
    electrode_spacing_cm: 10,
    span: { from_level: 't11', to_level: 'l2' },
    ground: { location: 'iliac_crest' },
  },
  sample_rate_hz: 1000,
});

export const PROTOCOLS: Readonly<Record<ProtocolId | 'spine_resistance_straight_v1' | 'spine_resistance_max_flexion_v1', ProtocolTemplate>> = Object.freeze({
  lat_pulldown_standing_v1: {
    session_type: 'exercise',
    load_declared: { type: 'elastic_band', nominal_kg: 25, band_id: 'green' },
    planned_reps: 60,
    planned_sets: 4,
    reps_per_set: 15,
    streams: [
      emgLat('left'),
      emgLat('right'),
      { stream_id: 'imu_t9', stream_kind: 'imu', placement: { spine_level: 't9' }, sample_rate_hz: 100 },
      STRETCH_SPINE,
    ],
  },
  spine_length_rest_v1: { session_type: 'spine_check', streams: [STRETCH_SPINE] },
  spine_resistance_straight_v1: { session_type: 'spine_check', streams: [STRETCH_SPINE] },
  spine_resistance_max_flexion_v1: { session_type: 'spine_check', streams: [STRETCH_SPINE] },
});
