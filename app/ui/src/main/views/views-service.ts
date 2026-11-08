/**
 * What the renderer may ask the backend for. The astronaut is never an argument: it is whoever is signed in and
 * unlocked here, so a compromised renderer cannot read another crew member's views. With nobody signed in, or
 * behind the lock screen, every call fails with `locked` before any request is made.
 */
import type { BaselineView, HistoryView, SessionView, StripView, TodayView } from '../../contracts/v1';
import type { FirstDayResult, ViewRange } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { CrewStore } from '../store/crew-store';
import type { ViewsClient } from './views-client';
import { ViewsError } from './views-error';

export interface ViewsService {
  strip(): Promise<StripView>;
  today(): Promise<TodayView>;
  history(range: ViewRange): Promise<HistoryView>;
  baseline(): Promise<BaselineView>;
  /** The backend view plus the member's first name, which only exists on this device. */
  firstDay(): Promise<FirstDayResult>;
  session(sessionId: string): Promise<SessionView>;
}

export interface ViewsServiceOptions {
  auth: Pick<AuthService, 'current'>;
  crew: Pick<CrewStore, 'list'>;
  client: ViewsClient;
}

export function createViewsService({ auth, crew, client }: ViewsServiceOptions): ViewsService {
  /** A rejected promise rather than a throw, so every method fails the same way. */
  function astronaut(): Promise<string> {
    const session = auth.current();
    if (session === null || session.locked) return Promise.reject(new ViewsError('locked'));
    return Promise.resolve(session.crewId);
  }

  return {
    strip: async () => client.strip(await astronaut()),
    today: async () => client.today(await astronaut()),
    history: async (range) => client.history(await astronaut(), range),
    baseline: async () => client.baseline(await astronaut()),
    async firstDay() {
      const id = await astronaut();
      const view = await client.firstDay(id);
      const member = (await crew.list())?.find((candidate) => candidate.id === id);
      const crewFirstName = member?.name.trim().split(/\s+/u)[0];
      if (crewFirstName === undefined || crewFirstName === '') throw new ViewsError('unavailable');
      return { ...view, crewFirstName };
    },
    async session(sessionId) {
      await astronaut();
      return client.session(sessionId);
    },
  };
}
