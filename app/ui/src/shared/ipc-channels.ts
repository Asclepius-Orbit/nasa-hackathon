/**
 * IPC channel names shared by main and preload. Kept free of imports so the sandboxed preload bundle
 * does not pull in zod; the input schemas live in `ipc-schema.ts` (main process only).
 */
export const IPC_CHANNELS = Object.freeze({
  crewList: 'asclepius:crew:list',
  crewNextId: 'asclepius:crew:next-id',
  crewCreate: 'asclepius:crew:create',
  crewSetHarness: 'asclepius:crew:set-harness',
  profileGet: 'asclepius:profile:get',
  profileUpdate: 'asclepius:profile:update',
  dailyContextToday: 'asclepius:daily-context:today',
  dailyContextUpdateToday: 'asclepius:daily-context:update-today',
  devicesList: 'asclepius:devices:list',
  sessionsStart: 'asclepius:sessions:start',
  sessionsStop: 'asclepius:sessions:stop',
  sessionsMarkArtifact: 'asclepius:sessions:mark-artifact',
  sessionsCalibrateMvc: 'asclepius:sessions:calibrate-mvc',
  sessionsReprocess: 'asclepius:sessions:reprocess',
  reportsSaveDaily: 'asclepius:reports:save-daily',
  reportsSaveOswestry: 'asclepius:reports:save-oswestry',
  reportsAddExercise: 'asclepius:reports:add-exercise',
  reportsDue: 'asclepius:reports:due',
  urgentSend: 'asclepius:urgent:send',
  contactsList: 'asclepius:contacts:list',
  contactsLogOfficerCall: 'asclepius:contacts:log-officer-call',
  authVerifyPin: 'asclepius:auth:verify-pin',
  authLock: 'asclepius:auth:lock',
  authSignOut: 'asclepius:auth:sign-out',
  chatAsk: 'asclepius:chat:ask',
  chatCancel: 'asclepius:chat:cancel',
  chatForget: 'asclepius:chat:forget',
  authCurrent: 'asclepius:auth:current',
  settingsGet: 'asclepius:settings:get',
  settingsUpdate: 'asclepius:settings:update',
  viewsStrip: 'asclepius:views:strip',
  viewsToday: 'asclepius:views:today',
  viewsHistory: 'asclepius:views:history',
  viewsBaseline: 'asclepius:views:baseline',
  viewsFirstDay: 'asclepius:views:first-day',
  viewsSession: 'asclepius:views:session',
  viewsHouston: 'asclepius:views:houston',
  viewsOutbox: 'asclepius:views:outbox',
  houstonReportGenerate: 'asclepius:houston-report:generate',
  repsSubscribe: 'asclepius:reps:subscribe',
  repsUnsubscribe: 'asclepius:reps:unsubscribe',
} as const);

/** Channels main pushes to the renderer (`webContents.send`). They have no handler: the renderer cannot call them. */
export const IPC_EVENT_CHANNELS = Object.freeze({
  repsEvent: 'asclepius:reps:event',
  chatEvent: 'asclepius:chat:event',
} as const);

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
