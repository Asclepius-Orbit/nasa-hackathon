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

/** Optional inclusive window of the History view. Dates are real calendar days; `from` may not be after `to`. */
export const historyInputSchema: z.ZodType<ViewRange> = z
  .strictObject({ from: isoDateSchema.exactOptional(), to: isoDateSchema.exactOptional() })
  .refine(({ from, to }) => from === undefined || to === undefined || from <= to);

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
