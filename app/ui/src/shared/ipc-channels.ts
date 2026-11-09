/**
 * IPC channel names shared by main and preload. Kept free of imports so the sandboxed preload bundle
 * does not pull in zod; the input schemas live in `ipc-schema.ts` (main process only).
 */
export const IPC_CHANNELS = Object.freeze({
  crewList: 'asclepius:crew:list',
  crewNextId: 'asclepius:crew:next-id',
  crewCreate: 'asclepius:crew:create',
  authVerifyPin: 'asclepius:auth:verify-pin',
  authLock: 'asclepius:auth:lock',
  authCurrent: 'asclepius:auth:current',
  settingsGet: 'asclepius:settings:get',
  settingsUpdate: 'asclepius:settings:update',
  viewsStrip: 'asclepius:views:strip',
  viewsToday: 'asclepius:views:today',
  viewsHistory: 'asclepius:views:history',
  viewsBaseline: 'asclepius:views:baseline',
  viewsFirstDay: 'asclepius:views:first-day',
  viewsSession: 'asclepius:views:session',
  repsSubscribe: 'asclepius:reps:subscribe',
  repsUnsubscribe: 'asclepius:reps:unsubscribe',
} as const);

/** Channels main pushes to the renderer (`webContents.send`). They have no handler: the renderer cannot call them. */
export const IPC_EVENT_CHANNELS = Object.freeze({
  repsEvent: 'asclepius:reps:event',
} as const);

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
