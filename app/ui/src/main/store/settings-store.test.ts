import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { Settings } from '../../shared/api';
import { createSettingsStore, DEFAULT_SETTINGS } from './settings-store';
import { FlakyFs, makeTempDir } from './test-fakes';

let dir: string;
let cleanup: () => Promise<void>;
let fs: FlakyFs;

beforeEach(async () => {
  ({ dir, cleanup } = await makeTempDir());
  fs = new FlakyFs();
});
afterEach(async () => {
  await cleanup();
});

const path = (): string => join(dir, 'settings.json');
const store = () => createSettingsStore({ path: path(), fs });

describe('settings store', () => {
  it('defaults to the options the mock shows selected: mm, GMT 24 h, large text, voice on, pedal not paired', async () => {
    const expected: Settings = {
      lengthUnit: 'mm',
      timeFormat: 'gmt',
      textSize: 'large',
      voiceCommands: true,
      footPedal: false,
    };
    expect(DEFAULT_SETTINGS).toEqual(expected);
    expect(await store().get()).toEqual(expected);
  });

  it('merges a patch, persists it as plain JSON, and returns the full settings', async () => {
    const updated = await store().update({ lengthUnit: 'in', voiceCommands: false });
    expect(updated).toEqual({ ...DEFAULT_SETTINGS, lengthUnit: 'in', voiceCommands: false });
    expect(JSON.parse(await readFile(path(), 'utf8'))).toEqual(updated);
    expect(await store().get()).toEqual(updated);
  });

  it('accepts an empty patch', async () => {
    expect(await store().update({})).toEqual(DEFAULT_SETTINGS);
  });

  it.each([
    ['not JSON', '{oops'],
    ['an extra key', JSON.stringify({ ...DEFAULT_SETTINGS, theme: 'dark' })],
    ['a missing key', JSON.stringify({ lengthUnit: 'in' })],
    ['a wrong value', JSON.stringify({ ...DEFAULT_SETTINGS, textSize: 'huge' })],
  ])('falls back to defaults for a file with %s', async (_label, content) => {
    await writeFile(path(), content);
    expect(await store().get()).toEqual(DEFAULT_SETTINGS);
  });

  it('rejects an invalid patch and writes nothing', async () => {
    const settings = store();
    await expect(settings.update({ theme: 'dark' } as Partial<Settings>)).rejects.toThrow('invalid settings');
    await expect(settings.update({ lengthUnit: 'cm' } as unknown as Partial<Settings>)).rejects.toThrow(
      'invalid settings',
    );
    expect(await readdir(dir)).toEqual([]);
  });

  it('rejects when the write fails and keeps the previous settings', async () => {
    const settings = store();
    fs.failRename = true;
    await expect(settings.update({ lengthUnit: 'in' })).rejects.toThrow('unavailable');
    expect(await settings.get()).toEqual(DEFAULT_SETTINGS);
  });

  it('does not lose either of two concurrent updates', async () => {
    const settings = store();
    await Promise.all([settings.update({ lengthUnit: 'in' }), settings.update({ textSize: 'standard' })]);
    expect(await store().get()).toEqual({ ...DEFAULT_SETTINGS, lengthUnit: 'in', textSize: 'standard' });
  });

  it('returns copies', async () => {
    const settings = store();
    const value = await settings.get();
    value.lengthUnit = 'in';
    expect((await settings.get()).lengthUnit).toBe('mm');
  });
});
