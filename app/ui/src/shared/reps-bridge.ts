/**
 * Renderer side of the live repetition counter, built on a minimal `ipcRenderer`. Lives here (not in the preload
 * entry) so it can be tested; it imports no zod, because the sandboxed preload must stay small. Main validates
 * every pushed message strictly before sending (`repsMessageSchema`); here only the envelope is checked, to
 * route it.
 *
 * - The Electron event object of `ipcRenderer.on` is never passed on: the callback gets the plain `RepsEvent`.
 * - Events are filtered by subscription id, so a window's other subscriptions never mix.
 * - The unsubscribe function works at once, even before main has confirmed the subscription: the listener is
 *   removed immediately and main is told as soon as the id is known.
 * - A refused subscription is reported as an `error` event whose code is normalized like any view call.
 */
import type { AsclepiusApi, RepsEvent, RepsMessage } from './api';
import { IPC_CHANNELS, IPC_EVENT_CHANNELS } from './ipc-channels';
import { normalizeViewsError, viewsCall } from './views-errors';

type Listener = (event: unknown, ...args: unknown[]) => void;

/** The part of `ipcRenderer` used here. */
export interface IpcRendererLike {
  invoke(channel: string, ...args: unknown[]): Promise<unknown>;
  on(channel: string, listener: Listener): unknown;
  removeListener(channel: string, listener: Listener): unknown;
}

/** Events kept while the subscription id is still on its way; the first events can overtake the reply. */
const MAX_EARLY_MESSAGES = 16;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;

function asMessage(value: unknown): RepsMessage | null {
  if (!isObject(value) || typeof value['subscriptionId'] !== 'string' || !isObject(value['event'])) return null;
  const event = value['event'];
  const valid =
    event['kind'] === 'lost' ||
    (event['kind'] === 'error' && typeof event['code'] === 'string') ||
    ((event['kind'] === 'state' || event['kind'] === 'end') && isObject(event['state']));
  return valid ? (value as unknown as RepsMessage) : null;
}

export function createRepsBridge(ipc: IpcRendererLike): AsclepiusApi['reps'] {
  return {
    subscribe(sessionId, onEvent) {
      let subscriptionId: string | null = null;
      let stopped = false;
      let early: RepsMessage[] = [];

      const deliver = (event: RepsEvent): void => {
        try {
          onEvent(event);
        } catch {
          // A bug in the renderer's callback must not break the bridge or the other subscriptions.
        }
      };
      const listener: Listener = (_electronEvent, raw) => {
        const message = asMessage(raw);
        if (message === null || stopped) return;
        if (subscriptionId === null) {
          if (early.length < MAX_EARLY_MESSAGES) early.push(message);
        } else if (message.subscriptionId === subscriptionId) {
          deliver(message.event);
        }
      };
      const detach = (): void => {
        ipc.removeListener(IPC_EVENT_CHANNELS.repsEvent, listener);
      };
      const tellMainToStop = (id: string): void => {
        void ipc.invoke(IPC_CHANNELS.repsUnsubscribe, { subscriptionId: id }).catch(() => undefined);
      };

      ipc.on(IPC_EVENT_CHANNELS.repsEvent, listener);
      viewsCall(() => ipc.invoke(IPC_CHANNELS.repsSubscribe, { sessionId })).then(
        (id) => {
          if (typeof id !== 'string') {
            detach();
            if (!stopped) deliver({ kind: 'error', code: 'unavailable' });
            return;
          }
          if (stopped) {
            tellMainToStop(id);
            return;
          }
          subscriptionId = id;
          const pending = early;
          early = [];
          for (const message of pending) if (message.subscriptionId === id) deliver(message.event);
        },
        (reason: unknown) => {
          detach();
          if (!stopped) deliver({ kind: 'error', code: normalizeViewsError(reason).message as 'locked' | 'not_found' | 'unavailable' });
        },
      );

      return () => {
        if (stopped) return;
        stopped = true;
        detach();
        early = [];
        if (subscriptionId !== null) tellMainToStop(subscriptionId);
      };
    },
  };
}
