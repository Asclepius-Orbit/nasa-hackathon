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
  settingsPatchSchema,
  verifyPinInputSchema,
} from '../shared/ipc-schema';
import type { AuthService } from './auth/auth-service';
import type { IpcEventLike } from './security/app-url';
import type { CrewStore } from './store/crew-store';
import type { SettingsStore } from './store/settings-store';

/** The part of Electron's `ipcMain` used here. */
export interface IpcMainLike {
  handle(channel: string, listener: (event: IpcEventLike, ...args: unknown[]) => unknown): void;
}

export interface IpcServices {
  crew: CrewStore;
  auth: AuthService;
  settings: SettingsStore;
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
  { crew, auth, settings }: IpcServices,
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
      if (!(error instanceof UnavailableError)) logError(error);
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

  function withInput<S extends z.ZodType, T>(channel: string, schema: S, task: (input: z.output<S>) => Promise<T>): void {
    ipcMain.handle(channel, (event, ...args) => {
      guard(event);
      const parsed = args.length === 1 ? schema.safeParse(args[0]) : null;
      if (!parsed?.success) throw new Error('invalid input');
      return run(() => task(parsed.data));
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

  withInput(IPC_CHANNELS.authVerifyPin, verifyPinInputSchema, (input) => auth.verifyPin(input));
  withoutInput(IPC_CHANNELS.authLock, () => {
    auth.lock();
  });
  withoutInput(IPC_CHANNELS.authCurrent, () => auth.current());

  withoutInput(IPC_CHANNELS.settingsGet, () => settings.get());
  withInput(IPC_CHANNELS.settingsUpdate, settingsPatchSchema, (patch) => settings.update(patch));
}
