import { describe, expect, it } from 'vitest';

import {
  checkDetail,
  featureLabel,
  fitItemText,
  missingReasonText,
  phaseText,
  protocolTitle,
  reasonText,
  recommendationText,
  redFlagLabels,
  reportKindText,
  sessionStateText,
  streamLabel,
  taskText,
  weakStreamText,
} from './codes';

describe('recommendationText', () => {
  it.each(['REC_LUMBAR_STAB_01', 'REC_INCREASE_ARED', 'REC_CONTACT_FLIGHT_SURGEON', 'REC_SESSION_REPEAT_01'])(
    'writes known code %s as words, never as the bare code',
    (code) => {
      const text = recommendationText(code);
      expect(text.title).not.toBe(code);
      expect(text.title.length).toBeGreaterThan(5);
    },
  );

  it('keeps the code in the detail of known recommendations so the crew can quote it', () => {
    expect(recommendationText('REC_LUMBAR_STAB_01').detail).toContain('REC_LUMBAR_STAB_01');
  });

  it('opens the session screen from the session repeat recommendation', () => {
    expect(recommendationText('REC_SESSION_REPEAT_01').action).toEqual({ label: 'Return to session', route: '/session', kind: 'primary' });
  });

  it('shows an unknown code as it is, with no action, instead of hiding it', () => {
    expect(recommendationText('REC_FUTURE_99')).toEqual({ title: 'REC_FUTURE_99', detail: '', action: null });
  });
});

describe('taskText', () => {
  it.each([
    ['TASK_DAILY_REPORT', '/report'],
    ['TASK_ODI', '/oswestry'],
    ['TASK_BASELINE_SESSION', '/baseline'],
    ['TASK_SESSION', '/session'],
  ] as const)('%s has a title, a label and the route %s', (code, route) => {
    const text = taskText(code);
    expect(text.title.length).toBeGreaterThan(3);
    expect(text.action?.label.length).toBeGreaterThan(3);
    expect(text.action?.route).toBe(route);
  });

  it('shows an unknown task code as it is', () => {
    expect(taskText('TASK_NEW')).toEqual({ title: 'TASK_NEW', detail: '', action: null });
  });
});

describe('reasonText', () => {
  it('names a known feature and says how far from the reference it is', () => {
    expect(reasonText('stretch.lumbar.peak_elongation_mm', 2.4)).toEqual({
      label: 'Lumbar length',
      text: '2.4 standard deviations above your preflight reference.',
    });
  });

  it('says below for a negative deviation, with one decimal', () => {
    expect(reasonText('stretch.lumbar.peak_elongation_mm', -1).text).toBe('1.0 standard deviations below your preflight reference.');
  });

  it('does not invent a direction when the deviation is unknown', () => {
    expect(reasonText('symptoms.pain_nrs_7d_mean', null).text).toBe('Deviation not known.');
  });

  it('shows an unknown feature id as it is', () => {
    expect(reasonText('future.thing', 1.2).label).toBe('future.thing');
    expect(featureLabel('future.thing')).toBe('future.thing');
  });
});

describe('missingReasonText', () => {
  it.each(['no_data', 'low_quality', 'no_baseline', 'stale', 'sensor_offline', 'signal_not_raw'] as const)('writes %s as words', (reason) => {
    expect(missingReasonText('x', reason)).not.toContain('_');
  });

  it('names the data and why it is missing, for features and for the special names', () => {
    expect(missingReasonText('emg.latissimus_dorsi.right.median_freq_hz', 'sensor_offline')).toBe('Right latissimus EMG frequency (sensor offline)');
    expect(missingReasonText('self_report', 'no_data')).toBe("Today's report (no data)");
    expect(missingReasonText('baseline', 'no_baseline')).toBe('Preflight reference (no reference yet)');
  });

  it('keeps an unknown code visible', () => {
    expect(missingReasonText('thing_x', 'new_reason')).toBe('thing_x (new_reason)');
  });
});

describe('small code maps', () => {
  it('writes flight phases and shows an unknown phase as it is', () => {
    expect(phaseText('inflight')).toBe('In flight, microgravity');
    expect(phaseText('preflight')).toBe('Preflight, 1 g');
    expect(phaseText('postflight')).toBe('Postflight');
    expect(phaseText('orbit')).toBe('orbit');
  });

  it('writes session states', () => {
    expect(sessionStateText('running')).toBe('in progress');
    expect(sessionStateText('processed')).toBe('complete');
    expect(sessionStateText('processing_failed')).toBe('could not be processed');
    expect(sessionStateText('zzz')).toBe('zzz');
  });

  it('writes report kinds', () => {
    expect(reportKindText('daily')).toBe('Daily report done');
    expect(reportKindText('weekly_odi')).toBe('Weekly Oswestry done');
    expect(reportKindText('red_flag')).toBe('Warning symptom reported');
    expect(reportKindText('other')).toBe('other');
  });

  it('writes fit items, streams and protocols, and falls back to the id', () => {
    expect(fitItemText('imu').label).toBe('IMU at L3:');
    expect(fitItemText('mystery')).toEqual({ label: 'mystery', detail: '' });
    expect(streamLabel('emg_lat_left')).toBe('EMG left latissimus');
    expect(streamLabel('new_stream')).toBe('new_stream');
    expect(protocolTitle('lat_pulldown_standing_v1')).toBe('Standing lat pulldown with band');
    expect(protocolTitle('spine_length_rest_v1')).toBe('Spine length check');
    expect(protocolTitle('p_v9')).toBe('p_v9');
  });

  it('writes weak streams and sensor check details', () => {
    expect(weakStreamText({ stream: 'stretch_lumbar', state: 'stalled' })).toBe('Stretch cord L1 to L5 stalled');
    expect(weakStreamText({ stream: 'imu_l3', state: 'disabled' })).toBe('IMU at L3 disabled');
    expect(checkDetail('good', 'active')).toBe('Clean signal');
    expect(checkDetail('weak', 'stalled')).toContain('stopped sending');
    expect(checkDetail('weak', 'disabled')).toContain('switched off');
    expect(checkDetail('weak', 'active')).toContain('weak');
  });

  it('has a label for each of the 8 contract red flags', () => {
    expect(Object.keys(redFlagLabels)).toHaveLength(8);
    expect(redFlagLabels.saddle_numbness).toBe('Numbness between the legs or around the bottom');
  });
});
