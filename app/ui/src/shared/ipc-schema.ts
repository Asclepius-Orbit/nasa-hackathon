/** Zod schemas for every IPC input. The main process validates each call with these; the renderer is untrusted. */
import { z } from 'zod';

import type { Settings, ViewRange } from './api';

export { IPC_CHANNELS, type IpcChannel } from './ipc-channels';

export const CREW_ID_PATTERN = /^AST-\d{2}$/;
export const PIN_PATTERN = /^\d{4}$/;
export const NAME_MAX_LENGTH = 40;

export const crewIdSchema = z.string().regex(CREW_ID_PATTERN);
export const pinSchema = z.string().regex(PIN_PATTERN);
export const nameSchema = z
  .string()
  .min(1)
  .max(NAME_MAX_LENGTH)
  .refine((name) => !/\p{Cc}/u.test(name));

export const createCrewInputSchema = z.strictObject({ name: nameSchema, pin: pinSchema });
export const verifyPinInputSchema = z.strictObject({ crewId: crewIdSchema, pin: pinSchema });

const isoDateSchema = z.iso.date();

const MS_PER_DAY = 86_400_000;
/** Longest History window (inclusive days) the renderer may ask for: one leap year. */
export const MAX_HISTORY_DAYS = 366;

/** Inclusive days from `from` to `to`; both are valid ISO dates (checked by `isoDateSchema` before this runs). */
function inclusiveDays(from: string, to: string): number {
  return (Date.parse(to) - Date.parse(from)) / MS_PER_DAY + 1;
}

/**
 * Optional inclusive window of the History view. Dates are real calendar days; `from` may not be after `to` and a
 * window with both ends is at most `MAX_HISTORY_DAYS` long, so a compromised renderer cannot ask for years of rows.
 */
export const historyInputSchema: z.ZodType<ViewRange> = z
  .strictObject({ from: isoDateSchema.exactOptional(), to: isoDateSchema.exactOptional() })
  .refine(({ from, to }) => from === undefined || to === undefined || from <= to)
  .refine(({ from, to }) => from === undefined || to === undefined || inclusiveDays(from, to) <= MAX_HISTORY_DAYS);

/** Lowercase UUID, as the contract defines `session_id`. */
export const sessionInputSchema = z.strictObject({
  sessionId: z.uuid().refine((id) => id === id.toLowerCase()),
});

const settingsShape = {
  lengthUnit: z.enum(['mm', 'in']),
  timeFormat: z.enum(['gmt', 'gmt_met']),
  textSize: z.enum(['standard', 'large']),
  voiceCommands: z.boolean(),
  footPedal: z.boolean(),
};

export const settingsSchema: z.ZodType<Settings> = z.strictObject(settingsShape);

/** A patch may omit any key but may not set one to `undefined` (matches `Partial<Settings>` under exactOptionalPropertyTypes). */
export const settingsPatchSchema: z.ZodType<Partial<Settings>> = z.strictObject({
  lengthUnit: settingsShape.lengthUnit.exactOptional(),
  timeFormat: settingsShape.timeFormat.exactOptional(),
  textSize: settingsShape.textSize.exactOptional(),
  voiceCommands: settingsShape.voiceCommands.exactOptional(),
  footPedal: settingsShape.footPedal.exactOptional(),
});
