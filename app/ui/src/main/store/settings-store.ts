/** Display preferences as plain JSON (no health data, nothing secret). A missing or invalid file reads as defaults. */
import type { Settings } from '../../shared/api';
import { settingsPatchSchema, settingsSchema } from '../../shared/ipc-schema';
import { nodeFileSystem, writeFileAtomic, type FileSystemLike } from './atomic-write';
import { createSerial } from './serial';

/** What the mock's Settings view shows selected. */
export const DEFAULT_SETTINGS: Readonly<Settings> = Object.freeze({
  lengthUnit: 'mm',
  timeFormat: 'gmt',
  textSize: 'large',
  voiceCommands: true,
  footPedal: false,
});

export interface SettingsStore {
  get(): Promise<Settings>;
  update(patch: Partial<Settings>): Promise<Settings>;
}

export interface SettingsStoreOptions {
  path: string;
  fs?: FileSystemLike;
}

export function createSettingsStore({ path, fs = nodeFileSystem }: SettingsStoreOptions): SettingsStore {
  const serial = createSerial();
  let cached: Settings | null = null;

  async function load(): Promise<Settings> {
    if (cached !== null) return cached;
    try {
      const parsed = settingsSchema.safeParse(JSON.parse((await fs.readFile(path)).toString('utf8')));
      cached = parsed.success ? parsed.data : { ...DEFAULT_SETTINGS };
    } catch {
      cached = { ...DEFAULT_SETTINGS };
    }
    return cached;
  }

  return {
    get: () => serial(async () => ({ ...(await load()) })),

    update: (patch) =>
      serial(async () => {
        const parsed = settingsPatchSchema.safeParse(patch);
        if (!parsed.success) throw new Error('invalid settings');
        const next: Settings = { ...(await load()), ...parsed.data };
        try {
          await writeFileAtomic(fs, path, `${JSON.stringify(next, null, 2)}\n`);
        } catch {
          throw new Error('unavailable');
        }
        cached = next;
        return { ...next };
      }),
  };
}
