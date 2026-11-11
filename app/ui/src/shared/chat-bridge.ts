/**
 * Renderer side of the chat with the agent, built on a minimal `ipcRenderer`; same shape as `reps-bridge.ts`. It lives here
 * (not in the preload entry) so it can be tested, and imports no zod: main validates every pushed message strictly before
 * sending (`chatMessageSchema`); here only the envelope is checked, to route it.
 *
 * - The Electron event object of `ipcRenderer.on` is never passed on: the callback gets the plain `ChatEvent`.
 * - Events are filtered by subscription id, so a window's other answers never mix.
 * - The listener is attached before the question is sent, and events that overtake main's reply are kept and replayed.
 * - Cancel works at once, even before main has confirmed the answer: the listener is removed immediately and main is told
 *   as soon as the id is known. It is idempotent and silent.
 * - A refused question is reported as an `error` event: `locked` when nobody is unlocked, `unavailable` otherwise.
 */
import type { AsclepiusApi, ChatEvent, ChatMessage } from './api';
import { IPC_CHANNELS, IPC_EVENT_CHANNELS } from './ipc-channels';
import type { IpcRendererLike } from './reps-bridge';
import { normalizeViewsError } from './views-errors';

type Listener = (event: unknown, ...args: unknown[]) => void;

/** Events kept while the subscription id is still on its way. */
const MAX_EARLY_MESSAGES = 64;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;

function asMessage(value: unknown): ChatMessage | null {
  if (!isObject(value) || typeof value['subscriptionId'] !== 'string' || !isObject(value['event'])) return null;
  const event = value['event'];
  const valid =
    (event['kind'] === 'text' && typeof event['delta'] === 'string') ||
    (event['kind'] === 'tool_call' && typeof event['toolName'] === 'string') ||
    (event['kind'] === 'fallback' && typeof event['text'] === 'string') ||
    (event['kind'] === 'notice' && typeof event['code'] === 'string' && typeof event['text'] === 'string') ||
    event['kind'] === 'end' ||
    (event['kind'] === 'error' && typeof event['code'] === 'string');
  return valid ? (value as unknown as ChatMessage) : null;
}

const isLast = (event: ChatEvent): boolean => event.kind === 'end' || event.kind === 'error';

export function createChatBridge(ipc: IpcRendererLike): AsclepiusApi['chat'] {
  return {
    ask(input, onEvent) {
      let subscriptionId: string | null = null;
      let stopped = false;
      let finished = false;
      let early: ChatMessage[] = [];

      const detach = (): void => {
        ipc.removeListener(IPC_EVENT_CHANNELS.chatEvent, listener);
      };
      const deliver = (event: ChatEvent): void => {
        if (finished) return;
        if (isLast(event)) {
          finished = true;
          detach();
        }
        try {
          onEvent(event);
        } catch {
          // A bug in the renderer's callback must not break the bridge or the other answers.
        }
      };
      const tellMainToStop = (id: string): void => {
        void ipc.invoke(IPC_CHANNELS.chatCancel, { subscriptionId: id }).catch(() => undefined);
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

      ipc.on(IPC_EVENT_CHANNELS.chatEvent, listener);
      ipc.invoke(IPC_CHANNELS.chatAsk, { conversationId: input.conversationId, question: input.question }).then(
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
          if (!stopped) deliver({ kind: 'error', code: normalizeViewsError(reason).message === 'locked' ? 'locked' : 'unavailable' });
        },
      );

      return () => {
        if (stopped) return;
        stopped = true;
        detach();
        early = [];
        if (subscriptionId !== null && !finished) tellMainToStop(subscriptionId);
      };
    },
    forget(input) {
      return ipc.invoke(IPC_CHANNELS.chatForget, { conversationId: input.conversationId }).then(
        () => undefined,
        () => undefined,
      );
    },
  };
}
