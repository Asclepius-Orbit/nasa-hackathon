import { expect, it } from 'vitest';

import { banner, dueLabel, groundLinkValue, harnessValue, levelLabel, minutes, triesLeft } from './common';

it('pluralises tries', () => {
  expect(triesLeft(1)).toBe('1 try left.');
  expect(triesLeft(2)).toBe('2 tries left.');
});
it('formats minutes and last contact', () => {
  expect(minutes(12)).toBe('12 min');
  expect(banner.lastContact(4)).toBe('Last contact 4 min ago.');
});
it('describes the ground link, never hiding that it is down', () => {
  expect(groundLinkValue({ state: 'down', backInMin: 12 })).toBe('Down, back in 12 min');
  expect(groundLinkValue({ state: 'down', backInMin: null })).toBe('Down');
  expect(groundLinkValue({ state: 'up' })).toBe('Up');
  expect(groundLinkValue(null)).toBe('Not known');
});
it('describes the harness battery and its weak channels', () => {
  const ok = { batteryPct: 64, degraded: false, weakStreams: [], stale: false };
  expect(harnessValue({ ...ok, weakStreams: [{ stream: 'emg_lat_right', state: 'stalled' }] }, '09:58')).toBe('64 %, EMG right latissimus stalled');
  expect(harnessValue(ok, '09:58')).toBe('64 %');
  expect(harnessValue({ ...ok, batteryPct: null, degraded: true }, '09:58')).toBe('Battery not reported, degraded');
  expect(harnessValue(null, '')).toBe('Not paired yet');
});
it('shows a stale harness as last seen, never its old battery, degraded flag or streams as current', () => {
  const stale = { batteryPct: 18, degraded: true, weakStreams: [{ stream: 'stretch_lumbar', state: 'stalled' }], stale: true };
  expect(harnessValue(stale, '3 Nov 09:58')).toBe('Last seen 3 Nov 09:58 GMT');
});
it('labels levels, with no-data explicit', () => {
  expect(levelLabel('amber')).toBe('Amber');
  expect(levelLabel('nodata')).toBe('No data');
});
it('labels the due badge', () => {
  expect(dueLabel(1)).toBe('1 due');
});
