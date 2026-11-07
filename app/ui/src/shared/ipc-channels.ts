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
} as const);

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
