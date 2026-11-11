/**
 * What the renderer may ask of the spine agent, and the only place that decides who talks to it. The astronaut is never an
 * argument: it is whoever is signed in and unlocked here, and that id is what the agent sees.
 *
 * - With nobody signed in, or behind the lock screen, `ask` throws `locked` before any request is made.
 * - An answer belongs to one window (webContents). A window may hold up to `MAX_ANSWERS_PER_SENDER` live answers; past that
 *   the new question is refused (`unavailable`) and the live ones are left alone. A window can only cancel its own.
 * - Every event is checked against the current sign-in right before it is pushed, so even if the lock screen went up
 *   without anyone calling `stopAll`, nothing is delivered to a locked or different astronaut and the answer is cancelled.
 *   Answers also end with the window (`destroyed`), on cancel, and after `end` or `error`.
 * - Events are validated (`chatMessageSchema`) before they leave main; one that fails becomes an `unavailable` error.
 */
import type { AuthSession, ChatEvent } from '../../shared/api';
import { IPC_EVENT_CHANNELS } from '../../shared/ipc-channels';
import { chatMessageSchema } from '../../shared/ipc-schema';
import type { WebContentsLike } from '../security/app-url';
import { ViewsError } from '../views/views-error';
import type { ChatStreamClient } from './chat-stream';

export const MAX_ANSWERS_PER_SENDER = 3;

export interface ChatService {
  /** Starts an answer for `sender` and returns its subscription id. Throws a `ViewsError` (`locked`, `unavailable`). */
  ask(sender: WebContentsLike, input: { conversationId: string; question: string }): string;
  /** Cancels one of `sender`'s answers; an unknown id (or another window's) is ignored. */
  cancel(sender: WebContentsLike, subscriptionId: string): void;
  /** Forgets a conversation on the agent, best effort. Does nothing while locked. Never rejects. */
  forget(conversationId: string): Promise<void>;
  /** Cancels every answer of every window (lock, sign-out). */
  stopAll(): void;
}

export interface ChatServiceOptions {
  auth: { current(): AuthSession | null };
  chat: ChatStreamClient;
  newId?: () => string;
  logError?: (error: unknown) => void;
}

interface Answer {
  id: string;
  crewId: string;
  cancel: () => void;
}

interface SenderState {
  answers: Map<string, Answer>;
  onDestroyed: () => void;
}

export function createChatService({
  auth,
  chat,
  newId = () => crypto.randomUUID(),
  logError = () => undefined,
}: ChatServiceOptions): ChatService {
  const senders = new Map<WebContentsLike, SenderState>();

  function unlockedCrewId(): string {
    const session = auth.current();
    if (session === null || session.locked) throw new ViewsError('locked');
    return session.crewId;
  }

  function remove(sender: WebContentsLike, answer: Answer): void {
    answer.cancel();
    const state = senders.get(sender);
    if (state === undefined) return;
    state.answers.delete(answer.id);
    if (state.answers.size === 0) {
      sender.removeListener('destroyed', state.onDestroyed);
      senders.delete(sender);
    }
  }

  function removeAllOf(sender: WebContentsLike): void {
    for (const answer of [...(senders.get(sender)?.answers.values() ?? [])]) remove(sender, answer);
  }

  function push(sender: WebContentsLike, answer: Answer, event: ChatEvent): boolean {
    const message = chatMessageSchema.safeParse({ subscriptionId: answer.id, event });
    if (!message.success) {
      logError(message.error);
      return false;
    }
    try {
      sender.send(IPC_EVENT_CHANNELS.chatEvent, message.data);
    } catch (error) {
      logError(error);
      remove(sender, answer);
      return true;
    }
    if (message.data.event.kind === 'end' || message.data.event.kind === 'error') remove(sender, answer);
    return true;
  }

  function deliver(sender: WebContentsLike, answer: Answer, event: ChatEvent): void {
    if (senders.get(sender)?.answers.get(answer.id) !== answer) return;
    const session = auth.current();
    if (session === null || session.locked || session.crewId !== answer.crewId) {
      remove(sender, answer);
      return;
    }
    if (sender.isDestroyed()) {
      removeAllOf(sender);
      return;
    }
    if (!push(sender, answer, event)) {
      // The event was outside what the renderer may receive: end the answer cleanly.
      if (!push(sender, answer, { kind: 'error', code: 'unavailable' })) remove(sender, answer);
    }
  }

  return {
    ask(sender, { conversationId, question }) {
      const crewId = unlockedCrewId();
      if (sender.isDestroyed()) throw new ViewsError('unavailable');
      let state = senders.get(sender);
      if ((state?.answers.size ?? 0) >= MAX_ANSWERS_PER_SENDER) {
        throw new ViewsError('unavailable', {
          cause: new Error(`window already holds ${String(MAX_ANSWERS_PER_SENDER)} live chat answers`),
        });
      }
      if (state === undefined) {
        const onDestroyed = (): void => {
          removeAllOf(sender);
        };
        state = { answers: new Map(), onDestroyed };
        senders.set(sender, state);
        sender.once('destroyed', onDestroyed);
      }
      const answer: Answer = { id: newId(), crewId, cancel: () => undefined };
      state.answers.set(answer.id, answer);
      answer.cancel = chat.ask({ conversationId, question, astronautId: crewId }, (event) => {
        deliver(sender, answer, event);
      });
      return answer.id;
    },
    cancel(sender, subscriptionId) {
      const answer = senders.get(sender)?.answers.get(subscriptionId);
      if (answer !== undefined) remove(sender, answer);
    },
    async forget(conversationId) {
      try {
        await chat.forget(conversationId, unlockedCrewId());
      } catch (error) {
        if (!(error instanceof ViewsError)) logError(error);
      }
    },
    stopAll() {
      for (const sender of [...senders.keys()]) removeAllOf(sender);
    },
  };
}
