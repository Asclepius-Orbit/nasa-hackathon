/**
 * IPC handlers. Every call: (1) sender must be the app's own top-level frame, else `forbidden`;
 * (2) arguments must match the channel's strict schema, else `invalid input` (never echoed back);
 * (3) the service runs and a plain DTO goes back. Unexpected failures are logged here and reach the
 * renderer only as `unavailable`.
 */
import type { z } from 'zod';

import {
  IPC_CHANNELS,
  createCrewInputSchema,
  dailyContextFieldsSchema,
  dailyReportFieldsSchema,
  exerciseEntrySchema,
  historyInputSchema,
  logOfficerCallInputSchema,
  markArtifactInputSchema,
  oswestryInputSchema,
  profileFieldsSchema,
  repsUnsubscribeInputSchema,
  sessionInputSchema,
  sessionOnlyInputSchema,
  setHarnessInputSchema,
  settingsPatchSchema,
  startSessionInputSchema,
  stopInputSchema,
  urgentInputSchema,
  verifyPinInputSchema,
} from '../shared/ipc-schema';
import type { AuthService } from './auth/auth-service';
import type { DailyContextService } from './profile/daily-context-service';
import type { DevicesService } from './profile/devices-service';
import type { HarnessService } from './profile/harness-service';
import type { ProfileService } from './profile/profile-service';
import type { ContactsService } from './reports/contacts-service';
import type { ReportsService } from './reports/reports-service';
import type { UrgentService } from './reports/urgent-service';
import type { IpcEventLike } from './security/app-url';
import type { SessionsService } from './sessions/sessions-service';
import type { CrewStore } from './store/crew-store';
import type { SettingsStore } from './store/settings-store';
import type { RepsService } from './views/reps-service';
import { ViewsError } from './views/views-error';
import type { ViewsService } from './views/views-service';

/** The part of Electron's `ipcMain` used here. */
export interface IpcMainLike {
  handle(channel: string, listener: (event: IpcEventLike, ...args: unknown[]) => unknown): void;
}

export interface IpcServices {
  crew: CrewStore;
  auth: AuthService;
  settings: SettingsStore;
  views: ViewsService;
  reps: RepsService;
  profile: ProfileService;
  dailyContext: DailyContextService;
  devices: DevicesService;
  harness: HarnessService;
  sessions: SessionsService;
  reports: ReportsService;
  urgent: UrgentService;
  contacts: ContactsService;
}

class UnavailableError extends Error {
  constructor() {
    super('unavailable');
  }
}

function orUnavailable<T>(value: T | null): T {
  if (value === null) throw new UnavailableError();
  return value;
}

export function registerIpc(
  ipcMain: IpcMainLike,
  { crew, auth, settings, views, reps, profile, dailyContext, devices, harness, sessions, reports, urgent, contacts }: IpcServices,
  isTrustedSender: (event: IpcEventLike) => boolean,
  logError: (error: unknown) => void = console.error,
): void {
  function guard(event: IpcEventLike): void {
    if (!isTrustedSender(event)) throw new Error('forbidden');
  }

  async function run<T>(task: () => Promise<T> | T): Promise<T> {
    try {
      return await task();
    } catch (error) {
      // `locked` and `not_found` are expected answers the renderer may tell apart; nothing else is let through.
      if (error instanceof ViewsError && error.code !== 'unavailable') throw new Error(error.code);
      if (error instanceof ViewsError) {
        if (error.cause !== undefined) logError(error.cause);
      } else if (!(error instanceof UnavailableError)) {
        logError(error);
      }
      throw new Error('unavailable');
    }
  }

  function withoutInput<T>(channel: string, task: () => Promise<T> | T): void {
    ipcMain.handle(channel, (event, ...args) => {
      guard(event);
      if (args.length !== 0) throw new Error('invalid input');
      return run(task);
    });
  }

  function withInput<S extends z.ZodType, T>(
    channel: string,
    schema: S,
    task: (input: z.output<S>, event: IpcEventLike) => Promise<T> | T,
  ): void {
    ipcMain.handle(channel, (event, ...args) => {
      guard(event);
      const parsed = args.length === 1 ? schema.safeParse(args[0]) : null;
      if (!parsed?.success) throw new Error('invalid input');
      return run(() => task(parsed.data, event));
    });
  }

  withoutInput(IPC_CHANNELS.crewList, async () => orUnavailable(await crew.list()));
  withoutInput(IPC_CHANNELS.crewNextId, async () => orUnavailable(await crew.nextId()));
  withInput(IPC_CHANNELS.crewCreate, createCrewInputSchema, async (input) => {
    // Creating a member signs them in; behind the lock screen that would be a way around it.
    if (auth.current()?.locked === true) return { ok: false, reason: 'unavailable' } as const;
    const result = await crew.create(input);
    if (result.ok) auth.signIn(result.member.id);
    return result;
  });

  withInput(IPC_CHANNELS.crewSetHarness, setHarnessInputSchema, (input) => harness.setHarness(input));

  withInput(IPC_CHANNELS.authVerifyPin, verifyPinInputSchema, async (input) => {
    const result = await auth.verifyPin(input);
    // Fill in today's daily context from the profile if missing; best effort, the PIN answer does not wait for it.
    if (result.ok) void dailyContext.ensureToday(result.crewId);
    return result;
  });
  withoutInput(IPC_CHANNELS.authLock, () => {
    auth.lock();
    // The astronaut behind the lock screen must stop receiving live data at once.
    reps.closeAll();
  });
  withoutInput(IPC_CHANNELS.authCurrent, () => auth.current());

  withoutInput(IPC_CHANNELS.settingsGet, () => settings.get());
  withInput(IPC_CHANNELS.settingsUpdate, settingsPatchSchema, (patch) => settings.update(patch));

  withoutInput(IPC_CHANNELS.viewsStrip, () => views.strip());
  withoutInput(IPC_CHANNELS.viewsToday, () => views.today());
  withInput(IPC_CHANNELS.viewsHistory, historyInputSchema, (range) => views.history(range));
  withoutInput(IPC_CHANNELS.viewsBaseline, () => views.baseline());
  withoutInput(IPC_CHANNELS.viewsFirstDay, () => views.firstDay());
  withoutInput(IPC_CHANNELS.viewsHouston, () => views.houston());
  withoutInput(IPC_CHANNELS.viewsOutbox, () => views.outbox());
  withInput(IPC_CHANNELS.viewsSession, sessionInputSchema, ({ sessionId }) => views.session(sessionId));
  // Reads reject like the views; writes resolve with a WriteResult (a backend refusal is an answer, not an error).
  withoutInput(IPC_CHANNELS.profileGet, () => profile.get());
  withInput(IPC_CHANNELS.profileUpdate, profileFieldsSchema, (fields) => profile.update(fields));
  withoutInput(IPC_CHANNELS.dailyContextToday, () => dailyContext.today());
  withInput(IPC_CHANNELS.dailyContextUpdateToday, dailyContextFieldsSchema, (fields) => dailyContext.updateToday(fields));
  withoutInput(IPC_CHANNELS.devicesList, () => devices.list());
  withInput(IPC_CHANNELS.sessionsStart, startSessionInputSchema, (input) => sessions.start(input));
  withInput(IPC_CHANNELS.sessionsStop, stopInputSchema, (input) => sessions.stop(input));
  withInput(IPC_CHANNELS.sessionsMarkArtifact, markArtifactInputSchema, (input) => sessions.markArtifact(input));
  withInput(IPC_CHANNELS.sessionsCalibrateMvc, sessionOnlyInputSchema, (input) => sessions.calibrateMvc(input));
  withInput(IPC_CHANNELS.sessionsReprocess, sessionOnlyInputSchema, (input) => sessions.reprocess(input));
  withInput(IPC_CHANNELS.reportsSaveDaily, dailyReportFieldsSchema, (fields) => reports.saveDaily(fields));
  withInput(IPC_CHANNELS.reportsSaveOswestry, oswestryInputSchema, (input) => reports.saveOswestry(input));
  withInput(IPC_CHANNELS.reportsAddExercise, exerciseEntrySchema, (entry) => reports.addExercise(entry));
  withoutInput(IPC_CHANNELS.reportsDue, () => reports.due());
  // The one write allowed without a PIN (see UrgentService); its schema still requires a well-formed crew id.
  withInput(IPC_CHANNELS.urgentSend, urgentInputSchema, (input) => urgent.send(input));
  withoutInput(IPC_CHANNELS.contactsList, () => contacts.list());
  withInput(IPC_CHANNELS.contactsLogOfficerCall, logOfficerCallInputSchema, (input) => contacts.logOfficerCall(input));
  // Live push: the handlers only start and stop it; events travel on `IPC_EVENT_CHANNELS.repsEvent` (see RepsService).
  withInput(IPC_CHANNELS.repsSubscribe, sessionInputSchema, ({ sessionId }, event) => reps.subscribe(event.sender, sessionId));
  withInput(IPC_CHANNELS.repsUnsubscribe, repsUnsubscribeInputSchema, ({ subscriptionId }, event) => {
    reps.unsubscribe(event.sender, subscriptionId);
  });
}
