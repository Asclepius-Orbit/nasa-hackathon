import { beforeEach, describe, expect, it } from 'vitest';

import { FakeScheduler } from '../../test/fake-scheduler';
import type { RedFlagCode, RepState } from '../types';
import { MockDataSource } from './MockDataSource';

const NOW = new Date('2026-11-05T13:40:00Z');

let scheduler: FakeScheduler;
let data: MockDataSource;
beforeEach(() => {
  scheduler = new FakeScheduler();
  data = new MockDataSource({ now: () => NOW, scheduler });
});

describe('catalogue', () => {
  it('lists the 8 contract red flags, most serious first, plus 2 escapes outside the contract', async () => {
    const { flags, escapes } = await data.getCatalog();
    const expected: RedFlagCode[] = [
      'saddle_numbness',
      'bladder_or_bowel_dysfunction',
      'leg_weakness',
      'radiating_leg_pain',
      'numbness_or_tingling',
      'severe_night_pain',
      'fever_with_back_pain',
      'trauma_event',
    ];
    expect(flags.map((f) => f.code)).toEqual(expected);
    expect(flags[0]?.label).toBe('Numbness between the legs or around the bottom');
    expect(escapes.map((e) => e.code)).toEqual(['very_severe_pain', 'other']);
  });
});

describe('strip', () => {
  it('has the mock values and a mission start that makes today day 87', async () => {
    const strip = await data.getStrip();
    expect(strip).toMatchObject({
      missionDay: 87,
      flightPhase: 'In flight, microgravity',
      harness: { id: 'wb-01', batteryPct: 64, note: 'EMG right weak' },
      groundLink: { state: 'down', backInMin: 12 },
      lastContactMinAgo: 4,
      reportsDue: 1,
    });
    expect(strip.missionStart).toBe('2026-08-11T00:00:00.000Z');
  });
});

describe('history', () => {
  it('spans mission days 58 to 87 in order', async () => {
    const { days, todayDay } = await data.getHistory();
    expect(days).toHaveLength(30);
    expect(days[0]?.day).toBe(58);
    expect(days.at(-1)?.day).toBe(87);
    expect(todayDay).toBe(87);
  });

  it('is green before day 80, no data on 80 to 82, amber from day 83', async () => {
    const { days } = await data.getHistory();
    const counts = { green: 0, amber: 0, red: 0, nodata: 0 };
    for (const d of days) counts[d.level] += 1;
    expect(counts).toEqual({ green: 22, amber: 5, red: 0, nodata: 3 });
    expect(days.find((d) => d.day === 79)?.level).toBe('green');
    expect(days.find((d) => d.day === 83)?.level).toBe('amber');
  });

  it('keeps absence explicit: no-data days have null values, never zero', async () => {
    const { days } = await data.getHistory();
    const empty = days.filter((d) => d.level === 'nodata');
    expect(empty.map((d) => d.day)).toEqual([80, 81, 82]);
    for (const d of empty) {
      expect(d.lengthMm).toBeNull();
      expect(d.pain).toBeNull();
      expect(d.report).toBe('Not done');
      expect(d.session).toBe('None');
      expect(d.note).toBe('No session and no report: not assessed. No data is not the same as fine.');
    }
  });

  it('generates the mock values deterministically', async () => {
    const { days } = await data.getHistory();
    const again = await new MockDataSource({ now: () => NOW, scheduler }).getHistory();
    expect(again).toEqual({ ...again, days });
    expect(days.find((d) => d.day === 58)?.lengthMm).toBeCloseTo(-0.4 + ((58 * 37) % 9) / 10, 10);
    expect(days.find((d) => d.day === 83)?.lengthMm).toBeCloseTo(1.2, 10);
    expect(days.find((d) => d.day === 87)?.lengthMm).toBeCloseTo(2.08, 10);
    expect(days.find((d) => d.day === 71)?.pain).toBe(2);
    expect(days.find((d) => d.day === 79)?.pain).toBe(3);
    expect(days.find((d) => d.day === 87)?.pain).toBe(4);
    expect(days.find((d) => d.day === 59)?.session).toBe('Standing lat pulldown, complete');
    expect(days.find((d) => d.day === 60)?.session).toBe('Spine check, complete');
    expect(days.find((d) => d.day === 83)?.note).toBe('Lumbar length above your reference for the first time.');
    expect(days.find((d) => d.day === 84)?.note).toBe('Lumbar length above your reference; 7-day pain mean steady.');
    expect(days.find((d) => d.day === 86)?.note).toBe('Lumbar length above your reference; 7-day pain mean rising.');
    expect(days.find((d) => d.day === 70)?.note).toBe('Everything inside your reference.');
  });
});

describe('today, report, session, baseline, oswestry, first day', () => {
  it('gives the amber verdict with its plan, reasons and trust numbers', async () => {
    const today = await data.getToday();
    expect(today.level).toBe('amber');
    expect(today.plan.map((p) => p.recommendation)).toEqual(['REC_LUMBAR_STAB_01', 'REC_SESSION_REPEAT_01']);
    expect(today.reasons.map((r) => r.label)).toEqual(['Lumbar length', 'Lumbar curve', 'Pain, 7-day mean']);
    expect(today.trust).toMatchObject({ confidencePct: 70, dataAvailable: 8, dataTotal: 10, assessedMinAgo: 42 });
    expect(today.lumbarLengthMm).toBe(2.1);
    expect(today.sessionInProgress).toEqual({ title: 'Standing lat pulldown', sinceGmt: '13:38', totalReps: 10 });
    expect(today.alsoToday.map((t) => t.id)).toEqual(['daily-report', 'oswestry']);
  });

  it('gives the session protocol', async () => {
    const s = await data.getSession();
    expect(s).toMatchObject({
      title: 'Standing lat pulldown with band',
      band: { color: 'blue', nominalKg: 15 },
      stretchedCm: 40,
      totalReps: 10,
      startedGmt: '13:38',
      plannedGmt: '14:00',
      minutesBeforePlan: 22,
    });
    expect(s.steps.map((x) => x.status)).toEqual(['done', 'done', 'done', 'now', 'todo']);
    expect(s.checks.items.find((c) => c.status === 'weak')?.sensor).toBe('EMG right latissimus');
  });

  it('gives the baseline, 2 of 3', async () => {
    const b = await data.getBaseline();
    expect(b).toMatchObject({ recorded: 2, required: 3, referenceVersion: 2, builtFromSessions: 2 });
    expect(b.sessions.map((s) => s.when)).toEqual(['L-30', 'L-23', 'L-16']);
    expect(b.sessions.map((s) => s.status)).toEqual(['complete', 'complete', 'planned']);
  });

  it('gives six Oswestry items of six statements each', async () => {
    const { items } = await data.getOswestry();
    expect(items).toHaveLength(6);
    for (const item of items) expect(item.options).toHaveLength(6);
    expect(items[0]?.title).toBe('Pain intensity');
  });

  it('gives the report form choices mapped to contract values', async () => {
    const form = await data.getReportForm();
    expect(form.painRegions.map((r) => r.value)).toEqual(['cervical', 'thoracic', 'lumbar', 'sacral']);
    expect(form.painSides.map((r) => r.value)).toEqual(['left', 'midline', 'right', 'bilateral']);
    expect(form.sleepHours.at(-1)).toEqual({ value: null, label: 'Not sure' });
    expect(form.exerciseKinds.map((k) => k.label)).toEqual(['ARED', 'CEVIS', 'T2 treadmill', 'None yet']);
    expect(form.catalog.flags).toHaveLength(8);
  });

  it('gives the first-day checklist', async () => {
    const f = await data.getFirstDay();
    expect(f.crewFirstName).toBe('Elena');
    expect(f.todo).toHaveLength(3);
  });
});

describe('houston and stop', () => {
  it('summarises day 87 with the crew code, never the name', async () => {
    const h = await data.getHouston();
    expect(h).toMatchObject({ crewCode: 'AST-01', day: 87, level: 'amber', sessions: '1 in progress', contacts: 'None today' });
    expect(JSON.stringify(h)).not.toContain('Elena');
    expect(h.outbox.map((o) => o.state)).toEqual(['queued', 'sent', 'sent']);
    expect(h.outbox[1]?.receivedGmt).toBe('21:04');
  });

  it('records the stop at the current rep and the officer call', async () => {
    const entry = await data.logCrewMedicalOfficerCall();
    expect(entry.atGmt).toBe('13:52');
    expect(entry.text).toBe('13:52 GMT: call to the crew medical officer logged. Included in the day 87 report to Houston.');
    await data.stopSessionForPain();
    const h = await data.getHouston();
    expect(h.sessions).toBe('1 stopped for pain at rep 4');
    expect(h.contacts).toBe('Crew medical officer called at 13:52');
    const stopped = await data.getStopped();
    expect(stopped.rep).toBe(4);
    expect(stopped.contacts).toEqual([entry]);
    expect(stopped.crewMedicalOfficerOnBoard).toBe(true);
  });
});

describe('subscribeReps', () => {
  function collect(): { states: RepState[]; stop: () => void } {
    const states: RepState[] = [];
    const stop = data.subscribeReps((s) => states.push(s));
    return { states, stop };
  }

  it('starts at rep 4 of 10, pulling down, last rep 3', () => {
    const { states } = collect();
    expect(states).toEqual([{ rep: 4, total: 10, phase: 'pulling', barPercent: 60, last: { rep: 3, seconds: 2.3 } }]);
  });

  it('moves the bar every 450 ms and switches phase at half a cycle', () => {
    const { states } = collect();
    scheduler.advance(450);
    expect(states.at(-1)).toMatchObject({ phase: 'pulling', barPercent: 40, rep: 4 });
    scheduler.advance(450 * 4);
    expect(states.at(-1)).toMatchObject({ phase: 'letting_up', barPercent: 100 });
    scheduler.advance(450);
    expect(states.at(-1)).toMatchObject({ phase: 'letting_up', barPercent: 80 });
  });

  it('counts a rep after a full cycle and stops counting at 10', () => {
    const { states } = collect();
    scheduler.advance(450 * 10);
    expect(states.at(-1)).toMatchObject({ rep: 5, last: { rep: 4, seconds: 2.4 } });
    scheduler.advance(450 * 10 * 20);
    expect(states.at(-1)).toMatchObject({ rep: 10 });
  });

  it('shares one timer between subscribers and stops it when the last one leaves', () => {
    const a = collect();
    const b = collect();
    expect(scheduler.active).toBe(1);
    a.stop();
    expect(scheduler.active).toBe(1);
    b.stop();
    expect(scheduler.active).toBe(0);
    const count = b.states.length;
    scheduler.advance(4500);
    expect(b.states).toHaveLength(count);
  });

  it('resumes from where it was after a new subscription', () => {
    const first = collect();
    scheduler.advance(450 * 10);
    first.stop();
    const { states } = collect();
    expect(states[0]).toMatchObject({ rep: 5 });
  });
});
