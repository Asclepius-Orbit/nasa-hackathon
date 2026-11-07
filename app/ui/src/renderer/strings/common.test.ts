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
});
it('describes the harness battery and its weak channels', () => {
  expect(harnessValue({ batteryPct: 64, note: 'EMG right weak' })).toBe('64 %, EMG right weak');
  expect(harnessValue({ batteryPct: 64, note: null })).toBe('64 %');
  expect(harnessValue(null)).toBe('Not paired yet');
});
it('labels levels, with no-data explicit', () => {
  expect(levelLabel('amber')).toBe('Amber');
  expect(levelLabel('nodata')).toBe('No data');
});
it('labels the due badge', () => {
  expect(dueLabel(1)).toBe('1 due');
});
