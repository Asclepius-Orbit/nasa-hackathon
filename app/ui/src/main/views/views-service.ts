/**
 * What the renderer may ask the backend for. The astronaut is never an argument: it is whoever is signed in and
 * unlocked here, so a compromised renderer cannot read another crew member's views. With nobody signed in, or
 * behind the lock screen, every call fails with `locked` before any request is made.
 */
import type { BaselineView, CordHistoryView, CordProtocolsView, HistoryView, HoustonView, OutboxView, SessionView, StripView, TodayView } from '../../contracts/v1';
import type { FirstDayResult, ViewRange } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { CrewStore } from '../store/crew-store';
import type { HoustonReportPdf, ViewsClient } from './views-client';
import { ViewsError } from './views-error';

export interface ViewsService {
  strip(): Promise<StripView>;
  today(): Promise<TodayView>;
  history(range: ViewRange): Promise<HistoryView>;
  baseline(): Promise<BaselineView>;
  /** The backend view plus the member's first name, which only exists on this device. */
  firstDay(): Promise<FirstDayResult>;
  cordProtocols(): Promise<CordProtocolsView>;
  cordHistory(): Promise<CordHistoryView>;
  session(sessionId: string): Promise<SessionView>;
  houston(): Promise<HoustonView>;
  outbox(): Promise<OutboxView>;
  /** The weekly PDF for Houston of the unlocked astronaut. */
  houstonReport(): Promise<HoustonReportPdf>;
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
    /**
     * The strip shows the harness paired on this device, not whichever one reported last. The pairing lives only in the
     * crew store, so it travels as `device_id`; the backend keeps the stale/offline rules. The contract has no way to ask
     * for "no device", so for a member without pairing (or one that cannot be found) the view is asked for plainly and
     * its harness is dropped here: unpaired is never shown as a device.
     */
    async strip() {
      const id = await astronaut();
      const harnessId = (await crew.list())?.find((candidate) => candidate.id === id)?.harnessId ?? null;
      if (harnessId !== null) return client.strip(id, harnessId);
      return { ...(await client.strip(id)), harness: null };
    },
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
    cordProtocols: async () => client.cordProtocols(await astronaut()),
    cordHistory: async () => client.cordHistory(await astronaut()),
    houston: async () => client.houston(await astronaut()),
    outbox: async () => client.outbox(await astronaut()),
    houstonReport: async () => client.houstonReport(await astronaut()),
    async session(sessionId) {
      return client.session(await astronaut(), sessionId);
    },
  };
}
