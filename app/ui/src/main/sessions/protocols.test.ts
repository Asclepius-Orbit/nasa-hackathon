import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { PROTOCOLS } from './protocols';

const CONTRACTS = join(import.meta.dirname, '../../../../../spine_monitor/contracts');
const readJson = (path: string): Record<string, unknown> =>
  JSON.parse(readFileSync(join(CONTRACTS, path), 'utf8')) as Record<string, unknown>;

interface CatalogEntry {
  protocol_id: string;
  band: { color: string; nominal_kg: number } | null;
  total_reps: number | null;
  planned_sets: number | null;
  reps_per_set: number | null;
}

describe('PROTOCOLS', () => {
  const catalog = (readJson('catalog/protocol-catalog.json')['entries'] as CatalogEntry[]);

  it('has exactly the protocols of the catalog', () => {
    expect(Object.keys(PROTOCOLS).sort()).toEqual(catalog.map((e) => e.protocol_id).sort());
  });

  it.each(catalog)('matches the catalog band, sets and reps of $protocol_id', (entry) => {
    const protocol = PROTOCOLS[entry.protocol_id as keyof typeof PROTOCOLS];
    expect(protocol.load_declared ?? null).toEqual(
      entry.band === null ? null : { type: 'elastic_band', nominal_kg: entry.band.nominal_kg, band_id: entry.band.color },
    );
    expect(protocol.planned_reps ?? null).toBe(entry.total_reps);
    expect(protocol.planned_sets ?? null).toBe(entry.planned_sets);
    expect(protocol.reps_per_set ?? null).toBe(entry.reps_per_set);
  });

  it.each([
    ['lat_pulldown_standing_v1', 'examples/valid/domain/session.exercise.json'],
    ['spine_length_rest_v1', 'examples/valid/domain/session.spine-check.json'],
  ] as const)('declares the streams and session type of the contract example for %s', (protocolId, example) => {
    const session = readJson(example);
    const streams = (session['streams'] as Record<string, unknown>[]).map(({ calibration_id: _id, ...stream }) => stream);
    expect(PROTOCOLS[protocolId].streams).toEqual(streams);
    expect(PROTOCOLS[protocolId].session_type).toBe(session['session_type']);
  });

  it.each(['spine_resistance_straight_v1', 'spine_resistance_max_flexion_v1'] as const)(
    'declares %s as a spine check with the full-spine stretch stream of the spine-check example',
    (protocolId) => {
      const session = readJson('examples/valid/domain/session.spine-check.json');
      const streams = (session['streams'] as Record<string, unknown>[]).map(({ calibration_id: _id, ...stream }) => stream);
      expect(PROTOCOLS[protocolId].session_type).toBe('spine_check');
      expect(PROTOCOLS[protocolId].streams).toEqual(streams);
      expect(PROTOCOLS[protocolId].load_declared).toBeUndefined();
      expect(PROTOCOLS[protocolId].planned_reps).toBeUndefined();
    },
  );
});
