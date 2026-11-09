/**
 * What the renderer may ask for in terms of live repetition counters, and the only place that decides who
 * receives them. The astronaut is never an argument: it is whoever is signed in and unlocked here.
 *
 * - Subscribing first asks for the session's view (`ViewsService.session`), which the views client only answers
 *   for a session of that astronaut; anything else is `not_found`, so a compromised renderer cannot open a
 *   stream of someone else's session. With nobody signed in, or behind the lock screen, it is `locked` before
 *   any request is made.
 * - A subscription belongs to one window (webContents) and one session. A window may hold several, even for the
 *   same session (a newer one never replaces an older one: two near-simultaneous subscribes can resolve in either
 *   order), up to `MAX_SUBSCRIPTIONS_PER_SENDER`. Past that the new one is refused (`unavailable`, with the cause
 *   logged in main) and the live ones are left alone. A window can only end its own.
 * - Every event is checked against the current sign-in right before it is pushed, so even if the lock screen
 *   went up without anyone calling `closeAll`, nothing is delivered to a locked or different astronaut and the
 *   stream is closed. Streams also end with the window (`destroyed`), on unsubscribe, and after `end` or `error`.
 */
import type { AuthSession } from '../../shared/api';
import { IPC_EVENT_CHANNELS } from '../../shared/ipc-channels';
import { repsMessageSchema } from '../../shared/ipc-schema';
import type { WebContentsLike } from '../security/app-url';
import type { RepsStreamClient } from './reps-stream';
import { ViewsError } from './views-error';
import type { ViewsService } from './views-service';

export const MAX_SUBSCRIPTIONS_PER_SENDER = 4;

export interface RepsService {
  /** Starts a subscription for `sender` and returns its id. Rejects with a `ViewsError` (`locked`, `not_found`, `unavailable`). */
  subscribe(sender: WebContentsLike, sessionId: string): Promise<string>;
  /** Ends one of `sender`'s subscriptions; an unknown id (or another window's) is ignored. */
  unsubscribe(sender: WebContentsLike, subscriptionId: string): void;
  /** Ends every subscription of every window (the lock screen went up). */
  closeAll(): void;
}

export interface RepsServiceOptions {
  auth: { current(): AuthSession | null };
  views: Pick<ViewsService, 'session'>;
  streams: RepsStreamClient;
  newId?: () => string;
  logError?: (error: unknown) => void;
}

interface Subscription {
  id: string;
  sessionId: string;
  crewId: string;
  close: () => void;
}

interface SenderState {
  subscriptions: Map<string, Subscription>;
  onDestroyed: () => void;
}

export function createRepsService({
  auth,
  views,
  streams,
  newId = () => crypto.randomUUID(),
  logError = () => undefined,
}: RepsServiceOptions): RepsService {
  const senders = new Map<WebContentsLike, SenderState>();

  function unlockedCrewId(): string {
    const session = auth.current();
    if (session === null || session.locked) throw new ViewsError('locked');
    return session.crewId;
  }

  function remove(sender: WebContentsLike, subscription: Subscription): void {
    subscription.close();
    const state = senders.get(sender);
    if (state === undefined) return;
    state.subscriptions.delete(subscription.id);
    if (state.subscriptions.size === 0) {
      sender.removeListener('destroyed', state.onDestroyed);
      senders.delete(sender);
    }
  }

  function removeAllOf(sender: WebContentsLike): void {
    for (const subscription of [...(senders.get(sender)?.subscriptions.values() ?? [])]) remove(sender, subscription);
  }

  function deliver(sender: WebContentsLike, subscription: Subscription, event: unknown): void {
    if (senders.get(sender)?.subscriptions.get(subscription.id) !== subscription) return;
    const session = auth.current();
    if (session === null || session.locked || session.crewId !== subscription.crewId) {
      remove(sender, subscription);
      return;
    }
    if (sender.isDestroyed()) {
      removeAllOf(sender);
      return;
    }
    const message = repsMessageSchema.safeParse({
      subscriptionId: subscription.id,
      sessionId: subscription.sessionId,
      event,
    });
    if (!message.success) {
      logError(message.error);
      remove(sender, subscription);
      return;
    }
    try {
      sender.send(IPC_EVENT_CHANNELS.repsEvent, message.data);
    } catch (error) {
      logError(error);
      remove(sender, subscription);
      return;
    }
    if (message.data.event.kind === 'end' || message.data.event.kind === 'error') remove(sender, subscription);
  }

  return {
    async subscribe(sender, sessionId) {
      const crewId = unlockedCrewId();
      await views.session(sessionId);
      // The sign-in may have changed while the backend was answering.
      if (unlockedCrewId() !== crewId) throw new ViewsError('locked');
      if (sender.isDestroyed()) throw new ViewsError('unavailable');

      let state = senders.get(sender);
      if ((state?.subscriptions.size ?? 0) >= MAX_SUBSCRIPTIONS_PER_SENDER) {
        throw new ViewsError('unavailable', {
          cause: new Error(`window already holds ${String(MAX_SUBSCRIPTIONS_PER_SENDER)} live rep subscriptions`),
        });
      }

      if (state === undefined) {
        const onDestroyed = (): void => {
          removeAllOf(sender);
        };
        state = { subscriptions: new Map(), onDestroyed };
        senders.set(sender, state);
        sender.once('destroyed', onDestroyed);
      }
      const subscription: Subscription = { id: newId(), sessionId, crewId, close: () => undefined };
      state.subscriptions.set(subscription.id, subscription);
      subscription.close = streams.open(sessionId, (event) => {
        deliver(sender, subscription, event);
      });
      return subscription.id;
    },
    unsubscribe(sender, subscriptionId) {
      const subscription = senders.get(sender)?.subscriptions.get(subscriptionId);
      if (subscription !== undefined) remove(sender, subscription);
    },
    closeAll() {
      for (const sender of [...senders.keys()]) removeAllOf(sender);
    },
  };
}
