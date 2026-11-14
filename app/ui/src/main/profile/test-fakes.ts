/** Hand-written fakes of the backend clients for the profile, daily context, session and report services. Test-only. */
import type {
  AstronautProfile,
  DailyContext,
  DeviceStatus,
  MedicalContactEntry,
  ReportsDue,
  WriteResult,
} from '../../shared/api';
import type { RecordsClient, SessionStream } from '../views/records-client';
import type { ViewsError } from '../views/views-error';
import type { CreateResult, WriteClient, WriteRequest } from '../views/write-client';

/**
 * In-memory backend: stored records by key, an optional failure for reads, every PUT body it got, and every `send`
 * request with the body it answers (run through the request's own schema, as the real client does).
 */
export class FakeBackend {
  profiles = new Map<string, AstronautProfile>();
  contexts = new Map<string, DailyContext>();
  devices: DeviceStatus[] = [];
  sessionStreams = new Map<string, SessionStream[]>();
  due: ReportsDue = { dailyDue: true, weeklyOdiDue: false, lastDailyAt: null, lastWeeklyOdiAt: null };
  contacts: MedicalContactEntry[] = [];
  readFailure: ViewsError | null = null;
  /** Answer to the next writes instead of storing them. */
  writeAnswer: WriteResult<never> | null = null;
  /** Every PUT it got; `onlyCreate` marks those sent with `If-None-Match: *`. */
  readonly puts: { path: string; body: Record<string, unknown>; onlyCreate?: true }[] = [];
  /** Runs when a create-only PUT arrives, before the check: another writer storing in between. */
  beforeCreate: () => void = () => undefined;
  beforeContextPut: () => void = () => undefined;
  readonly sent: Omit<WriteRequest<unknown>, 'schema'>[] = [];
  /** Body of the 2xx answer to the next `send`s. */
  sendAnswer: unknown = {};

  readonly records: RecordsClient = {
    profile: (id) => this.read(() => this.profiles.get(id) ?? null),
    profileVersioned: (id) => this.read(() => { const value = this.profiles.get(id); return value ? { value, etag: JSON.stringify(value) } : null; }),
    dailyContext: (id, day) => this.read(() => this.contexts.get(`${id}/${day}`) ?? null),
    dailyContextVersioned: (id, day) => this.read(() => { const value = this.contexts.get(`${id}/${day}`); return value ? { value, etag: JSON.stringify(value) } : null; }),
    devices: () => this.read(() => this.devices),
    sessionStreams: (_id, sessionId) => this.read(() => this.sessionStreams.get(sessionId) ?? null),
    reportsDue: () => this.read(() => this.due),
    contacts: () => this.read(() => this.contacts),
  };

  readonly writer: Pick<WriteClient, 'putProfile' | 'createProfile' | 'putDailyContext' | 'createDailyContext' | 'send'> = {
    send: <T>({ schema, ...request }: WriteRequest<T>): Promise<WriteResult<T>> => {
      this.sent.push(request);
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const parsed = schema.safeParse(this.sendAnswer);
      if (!parsed.success) {
        return Promise.reject(new Error(`fake answer outside the schema of ${request.path}`, { cause: parsed.error }));
      }
      return Promise.resolve({ ok: true, value: parsed.data });
    },
    putProfile: (id, body, etag) => {
      if (etag !== undefined && JSON.stringify(this.profiles.get(id)) !== etag) return Promise.resolve({ ok: false, reason: 'conflict' });
      this.puts.push({ path: `${id}/profile`, body });
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const stored = { schema_version: '1.0.0', updated_at: '2026-11-11T12:00:00+00:00', ...body } as unknown as AstronautProfile;
      this.profiles.set(id, stored);
      return Promise.resolve({ ok: true, value: stored });
    },
    createProfile: (id, body) => {
      this.puts.push({ path: `${id}/profile`, body, onlyCreate: true });
      if (this.profiles.has(id)) return Promise.resolve<CreateResult<AstronautProfile>>({ ok: false, reason: 'exists' });
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const stored = { schema_version: '1.0.0', updated_at: '2026-11-11T12:00:00+00:00', ...body } as unknown as AstronautProfile;
      this.profiles.set(id, stored);
      return Promise.resolve({ ok: true, value: stored });
    },
    putDailyContext: (id, day, body, etag) => {
      this.beforeContextPut();
      if (etag !== undefined && JSON.stringify(this.contexts.get(`${id}/${day}`)) !== etag) return Promise.resolve({ ok: false, reason: 'conflict' });
      this.puts.push({ path: `${id}/daily-context/${day}`, body });
      if (this.writeAnswer !== null) return Promise.resolve(this.writeAnswer);
      const stored = { schema_version: '1.0.0', ...body } as unknown as DailyContext;
      this.contexts.set(`${id}/${day}`, stored);
      return Promise.resolve({ ok: true, value: stored });
    },
    createDailyContext: (id, day, body) => {
      this.puts.push({ path: `${id}/daily-context/${day}`, body, onlyCreate: true });
      this.beforeCreate();
      if (this.contexts.has(`${id}/${day}`)) return Promise.resolve<CreateResult<DailyContext>>({ ok: false, reason: 'exists' });
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
