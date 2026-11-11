/** Hand-written fakes of the backend clients for the profile and daily context services. Test-only. */
import type { AstronautProfile, DailyContext, DeviceStatus, WriteResult } from '../../shared/api';
import type { RecordsClient } from '../views/records-client';
import type { ViewsError } from '../views/views-error';
import type { WriteClient } from '../views/write-client';

/** In-memory backend: stored records by key, an optional failure for reads, and every PUT body it got. */
export class FakeBackend {
  profiles = new Map<string, AstronautProfile>();
  contexts = new Map<string, DailyContext>();
  devices: DeviceStatus[] = [];
  readFailure: ViewsError | null = null;
  /** Answer to the next writes instead of storing them. */
  writeAnswer: WriteResult<never> | null = null;
  readonly puts: { path: string; body: Record<string, unknown> }[] = [];

  readonly records: RecordsClient = {
    profile: (id) => this.read(() => this.profiles.get(id) ?? null),
    dailyContext: (id, day) => this.read(() => this.contexts.get(`${id}/${day}`) ?? null),
    devices: () => this.read(() => this.devices),
  };

  readonly writer: Pick<WriteClient, 'putProfile' | 'putDailyContext'> = {
    putProfile: (id, body) => {
      this.puts.push({ path: `${id}/profile`, body });
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const stored = { schema_version: '1.0.0', updated_at: '2026-11-11T12:00:00+00:00', ...body } as unknown as AstronautProfile;
      this.profiles.set(id, stored);
      return Promise.resolve({ ok: true, value: stored });
    },
    putDailyContext: (id, day, body) => {
      this.puts.push({ path: `${id}/daily-context/${day}`, body });
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const stored = { schema_version: '1.0.0', ...body } as unknown as DailyContext;
      this.contexts.set(`${id}/${day}`, stored);
      return Promise.resolve({ ok: true, value: stored });
    },
  };

  private read<T>(value: () => T): Promise<T> {
    return this.readFailure === null ? Promise.resolve(value()) : Promise.reject(this.readFailure);
  }
}
