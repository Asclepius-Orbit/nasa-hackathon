import { describe, expect, it } from 'vitest';

import catalogJson from '../../../../../../spine_monitor/contracts/catalog/protocol-catalog.json?raw';
import { FakeScheduler } from '../../test/fake-scheduler';
import type { RepState } from '../types';
import { stages } from '../../strings/session';
import { MockDataSource } from './MockDataSource';
import { SESSION, todayData } from './mock-data';

interface CatalogEntry {
  protocol_id: string;
  band: { color: string; nominal_kg: number } | null;
  total_reps: number | null;
  planned_sets: number | null;
  reps_per_set: number | null;
  rest_between_sets_s: number | null;
  rest_seconds: number | null;
}

const catalog = (JSON.parse(catalogJson) as { entries: CatalogEntry[] }).entries;
const entry = catalog.find((e) => e.protocol_id === SESSION.protocolId);

describe('mock protocol values against the catalog', () => {
  it('finds the catalog entry of the mock session protocol', () => {
    expect(entry).toBeDefined();
  });

  it('mock SESSION carries the catalog sets, reps, rests and band', () => {
    expect(SESSION.plannedSets).toBe(entry?.planned_sets);
    expect(SESSION.repsPerSet).toBe(entry?.reps_per_set);
    expect(SESSION.totalReps).toBe(entry?.total_reps);
    expect(SESSION.restBetweenSetsS).toBe(entry?.rest_between_sets_s);
    expect(SESSION.restSeconds).toBe(entry?.rest_seconds);
    expect(SESSION.band).toEqual({ color: entry?.band?.color, nominalKg: entry?.band?.nominal_kg });
  });

  it('the in-progress session of Today uses the catalog sets and reps', () => {
    const progress = todayData(new Date('2026-11-05T13:40:00Z')).sessionInProgress;
    expect(progress).toMatchObject({
      protocolId: SESSION.protocolId,
      plannedSets: entry?.planned_sets,
      repsPerSet: entry?.reps_per_set,
      totalReps: entry?.total_reps,
    });
  });

  it('the simulated rep counter of MockDataSource uses the catalog sets and reps', () => {
    const source = new MockDataSource({ scheduler: new FakeScheduler() });
    let state: RepState | null = null;
    const stop = source.subscribeReps('s', (update) => {
      if (update.kind === 'state') state = update.state;
    });
    stop();
    expect(state).toMatchObject({
      setsTotal: entry?.planned_sets,
      repsPerSet: entry?.reps_per_set,
      total: entry?.total_reps,
    });
  });

  it('the rest text of the finish stage says the catalog rest_seconds (120 s is "2 min")', () => {
    expect(entry?.rest_seconds).toBeDefined();
    const minutes = (entry?.rest_seconds ?? 0) / 60;
    expect(stages.finish.lede(entry?.rest_seconds ?? null)).toContain(`stand still for ${String(minutes)} min.`);
  });
});
