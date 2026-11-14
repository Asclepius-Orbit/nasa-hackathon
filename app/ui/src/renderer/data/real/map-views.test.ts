import { describe, expect, it } from 'vitest';

import type { CordHistoryView, CordProtocolsView } from '../../../shared/api';
import { example } from '../../test/contract-examples';
import { toCordHistory, toCordProtocols } from './map-views';

describe('toCordProtocols', () => {
  const view = example<CordProtocolsView>('ui-cord-protocols');

  it('only receives estimates that are calculated, not measured, not validated and outside the triage', () => {
    const estimate = view.postures[0].estimate;
    expect(estimate).toMatchObject({ calculated: true, measured_length: false, clinically_validated: false, affects_triage: false });
  });

  it('maps cadence, due flags and both measurements of each posture', () => {
    const data = toCordProtocols(view);
    expect(data.cadenceDays).toBe(7);
    expect(data.postures.map((p) => [p.protocolId, p.due, p.dueReason])).toEqual([
      ['spine_resistance_straight_v1', false, 'CORD_UP_TO_DATE'],
      ['spine_resistance_max_flexion_v1', true, 'CORD_FOLLOW_UP_OVERDUE'],
    ]);
    expect(data.postures[0]?.earthReference).toEqual({
      sessionId: '5f2c1f0e-3b7a-4c55-9d1e-2a6b8c4d7e01',
      measuredAt: '2027-01-20T09:00:00Z',
      resistanceOhm: 4500,
      gravity: 'earth_1g',
    });
    expect(data.postures[0]?.lastFollowUp).toMatchObject({ resistanceOhm: 5650, gravity: 'microgravity' });
  });

  it('keeps the estimate numbers and assumption codes, and carries nothing that could pass it off as a measurement', () => {
    const estimate = toCordProtocols(view).postures[0]?.estimate;
    expect(estimate).toEqual({
      referenceLengthMm: 500,
      estimatedLengthMm: 538.5723222423,
      elongationMm: 38.5723222423,
      assumptions: ['ASSUMPTION_UNIFORM_RESISTANCE', 'ASSUMPTION_NEGLIGIBLE_CONTACT_RESISTANCE', 'ASSUMPTION_UNIFORM_DEFORMATION'],
    });
    expect(Object.keys(estimate ?? {}).sort()).toEqual(['assumptions', 'elongationMm', 'estimatedLengthMm', 'referenceLengthMm']);
  });

  it('maps a missing estimate to null with its reason code, and missing measurements to null', () => {
    const [straight, flexion] = view.postures;
    const data = toCordProtocols({
      ...view,
      postures: [{ ...straight, earth_reference: null, last_follow_up: null, estimate: null, estimate_unavailable: 'ESTIMATE_NO_EARTH_REFERENCE' }, flexion],
    });
    expect(data.postures[0]).toMatchObject({ earthReference: null, lastFollowUp: null, estimate: null, estimateUnavailable: 'ESTIMATE_NO_EARTH_REFERENCE' });
    expect(data.postures[1]).toMatchObject({ estimate: null, estimateUnavailable: 'ESTIMATE_REFERENCE_LENGTH_UNKNOWN' });
  });
});

describe('toCordHistory', () => {
  const view = example<CordHistoryView>('ui-cord-history');

  it('maps every measurement with its validity and reason, and has no estimate field', () => {
    const data = toCordHistory(view);
    const [straight, flexion] = data.postures;
    expect(straight?.protocolId).toBe('spine_resistance_straight_v1');
    expect(straight?.measurements.map((m) => [m.valid, m.invalidReason])).toEqual([[true, null], [false, 'CORD_INVALID_LOW_QUALITY'], [true, null]]);
    expect(flexion?.measurements[0]).toMatchObject({ resistanceOhm: null, qualityScore: null, valid: false, invalidReason: 'CORD_INVALID_NOT_COMPLETED' });
    for (const posture of data.postures) {
      expect(posture).not.toHaveProperty('estimate');
      for (const m of posture.measurements) expect(m).not.toHaveProperty('estimate');
    }
  });
});
