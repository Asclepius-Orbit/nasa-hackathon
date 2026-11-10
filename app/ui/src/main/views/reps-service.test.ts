import { describe, expect, it } from 'vitest';

import type { AuthSession, RepsEvent, RepsMessage } from '../../shared/api';
import { IPC_EVENT_CHANNELS } from '../../shared/ipc-channels';
import type { WebContentsLike } from '../security/app-url';
import { createRepsService, MAX_SUBSCRIPTIONS_PER_SENDER } from './reps-service';
import type { RepsStreamClient } from './reps-stream';
import { ViewsError } from './views-error';

const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const OTHER_SESSION = '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d';
const STATE = {
  schema_version: '1.0.0',
  session_id: SESSION,
  rep: 1,
  total: 10,
  phase: 'pulling',
  progress_pct: 20,
  last: null,
  signal: 'ok',
  as_of: '2026-11-08T10:00:00Z',
} as const;

/** Stands in for a window's webContents: records what is pushed to it. */
class FakeSender implements WebContentsLike {
  sent: { channel: string; payload: RepsMessage }[] = [];
  destroyed = false;
  failSend = false;
  listeners = new Set<() => void>();
  send(channel: string, payload: unknown): void {
    if (this.failSend) throw new Error('Object has been destroyed');
    this.sent.push({ channel, payload: payload as RepsMessage });
  }
  isDestroyed(): boolean {
    return this.destroyed;
  }
  once(_event: 'destroyed', listener: () => void): void {
    this.listeners.add(listener);
  }
  removeListener(_event: 'destroyed', listener: () => void): void {
    this.listeners.delete(listener);
  }
  destroy(): void {
    this.destroyed = true;
    for (const listener of [...this.listeners]) listener();
  }
  get events(): RepsEvent[] {
    return this.sent.map(({ payload }) => payload.event);
  }
}

interface OpenedStream {
  sessionId: string;
  emit: (event: RepsEvent) => void;
  closed: boolean;
}

function setup() {
  let session: AuthSession | null = { crewId: 'AST-01', locked: false };
  const opened: OpenedStream[] = [];
  const membership: string[] = [];
  const hooks: {
    failMembership: Error | null;
    beforeMembershipAnswers: () => void;
    membershipAnswer: (() => Promise<never>) | null;
  } = {
    failMembership: null,
    beforeMembershipAnswers: () => undefined,
    membershipAnswer: null,
  };
  const logged: unknown[] = [];
  let ids = 0;
  const streams: RepsStreamClient = {
    open(sessionId, emit) {
      const stream: OpenedStream = { sessionId, emit, closed: false };
      opened.push(stream);
      return () => {
        stream.closed = true;
      };
    },
  };
  const service = createRepsService({
    auth: { current: () => (session === null ? null : { ...session }) },
    views: {
      session: (sessionId) => {
        membership.push(sessionId);
        hooks.beforeMembershipAnswers();
        if (hooks.membershipAnswer !== null) return hooks.membershipAnswer();
        return hooks.failMembership === null ? Promise.resolve({} as never) : Promise.reject(hooks.failMembership);
      },
    },
    streams,
    newId: () => `0b9c3d4e-1f2a-4b5c-8d6e-${String(++ids).padStart(12, '0')}`,
    logError: (error) => logged.push(error),
  });
  return {
    service,
    opened,
    membership,
    hooks,
    logged,
    sender: new FakeSender(),
    setSession: (next: AuthSession | null) => {
      session = next;
    },
  };
}

describe('createRepsService', () => {
  it('checks the session belongs to the signed-in astronaut before opening any stream', async () => {
    const { service, sender, opened, membership } = setup();
    const id = await service.subscribe(sender, SESSION);
    expect(id).toMatch(/^0b9c3d4e-/u);
    expect(membership).toEqual([SESSION]);
    expect(opened.map((stream) => stream.sessionId)).toEqual([SESSION]);
  });

  it.each(['not_found', 'unavailable', 'locked'] as const)('opens nothing when the membership check says %s', async (code) => {
    const { service, sender, opened, hooks } = setup();
    hooks.failMembership = new ViewsError(code);
    await expect(service.subscribe(sender, SESSION)).rejects.toMatchObject({ code });
    expect(opened).toEqual([]);
    expect(sender.listeners.size).toBe(0);
  });

  it.each([
    ['nobody is signed in', null],
    ['the lock screen is up', { crewId: 'AST-01', locked: true }],
  ])('rejects "locked" without any request when %s', async (_label, session) => {
    const { service, sender, opened, membership, setSession } = setup();
    setSession(session);
    await expect(service.subscribe(sender, SESSION)).rejects.toMatchObject({ code: 'locked' });
    expect(membership).toEqual([]);
    expect(opened).toEqual([]);
  });

  it('rejects "locked" when the screen locks while the membership check is in flight', async () => {
    const { service, sender, opened, hooks, setSession } = setup();
    hooks.beforeMembershipAnswers = () => {
      setSession({ crewId: 'AST-01', locked: true });
    };
    await expect(service.subscribe(sender, SESSION)).rejects.toMatchObject({ code: 'locked' });
    expect(opened).toEqual([]);
  });

  it('pushes each stream event to the subscribing window only, tagged with the subscription and session ids', async () => {
    const { service, sender, opened } = setup();
    const other = new FakeSender();
    const id = await service.subscribe(sender, SESSION);
    await service.subscribe(other, OTHER_SESSION);
    opened[0]?.emit({ kind: 'state', state: STATE });
    opened[0]?.emit({ kind: 'lost' });
    expect(sender.sent).toEqual([
      {
        channel: IPC_EVENT_CHANNELS.repsEvent,
        payload: { subscriptionId: id, sessionId: SESSION, event: { kind: 'state', state: STATE } },
      },
      { channel: IPC_EVENT_CHANNELS.repsEvent, payload: { subscriptionId: id, sessionId: SESSION, event: { kind: 'lost' } } },
    ]);
    expect(other.sent).toEqual([]);
  });

  it('drops an event that does not pass validation and stops that stream', async () => {
    const { service, sender, opened, logged } = setup();
    await service.subscribe(sender, SESSION);
    opened[0]?.emit({ kind: 'state', state: { ...STATE, rep: -1 } });
    expect(sender.sent).toEqual([]);
    expect(opened[0]?.closed).toBe(true);
    expect(logged).toHaveLength(1);
  });

  it('stops after end, delivering it first, and frees the slot', async () => {
    const { service, sender, opened } = setup();
    await service.subscribe(sender, SESSION);
    opened[0]?.emit({ kind: 'end', state: STATE });
    expect(sender.events).toEqual([{ kind: 'end', state: STATE }]);
    expect(opened[0]?.closed).toBe(true);
    expect(sender.listeners.size).toBe(0);
  });

  it('unsubscribe closes the stream, ignores later events and is idempotent', async () => {
    const { service, sender, opened } = setup();
    const id = await service.subscribe(sender, SESSION);
    service.unsubscribe(sender, id);
    service.unsubscribe(sender, id);
    expect(opened[0]?.closed).toBe(true);
    opened[0]?.emit({ kind: 'lost' });
    expect(sender.sent).toEqual([]);
    expect(sender.listeners.size).toBe(0);
  });

  it('lets a window unsubscribe only its own subscriptions', async () => {
    const { service, sender, opened } = setup();
    const id = await service.subscribe(sender, SESSION);
    service.unsubscribe(new FakeSender(), id);
    service.unsubscribe(sender, 'not-an-id');
    expect(opened[0]?.closed).toBe(false);
  });

  it('keeps several subscriptions to the same session in one window: a newer one does not replace the older', async () => {
    const { service, sender, opened } = setup();
    const first = await service.subscribe(sender, SESSION);
    const second = await service.subscribe(sender, SESSION);
    expect(second).not.toBe(first);
    expect(opened.map((stream) => stream.closed)).toEqual([false, false]);
    opened[0]?.emit({ kind: 'lost' });
    opened[1]?.emit({ kind: 'lost' });
    expect(sender.sent.map(({ payload }) => payload.subscriptionId)).toEqual([first, second]);
    service.unsubscribe(sender, first);
    expect(opened.map((stream) => stream.closed)).toEqual([true, false]);
  });

  it('two near-simultaneous subscribes whose membership checks answer in reverse order both stay alive', async () => {
    const { service, sender, opened, hooks } = setup();
    const answers: (() => void)[] = [];
    hooks.membershipAnswer = () =>
      new Promise<never>((resolve) => {
        answers.push(() => {
          resolve({} as never);
        });
      });
    const first = service.subscribe(sender, SESSION);
    const second = service.subscribe(sender, SESSION);
    answers[1]?.();
    const secondId = await second;
    answers[0]?.();
    const firstId = await first;
    expect(opened).toHaveLength(2);
    expect(opened.map((stream) => stream.closed)).toEqual([false, false]);
    opened[0]?.emit({ kind: 'lost' });
    opened[1]?.emit({ kind: 'lost' });
    expect(sender.sent.map(({ payload }) => payload.subscriptionId).sort()).toEqual([firstId, secondId].sort());
  });

  it('caps the streams a single window can hold', async () => {
    const { service, sender, opened } = setup();
    for (let i = 0; i < MAX_SUBSCRIPTIONS_PER_SENDER; i += 1) {
      await service.subscribe(sender, `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`);
    }
    await expect(service.subscribe(sender, SESSION)).rejects.toMatchObject({ code: 'unavailable' });
    expect(opened).toHaveLength(MAX_SUBSCRIPTIONS_PER_SENDER);
    expect(opened.every((stream) => !stream.closed)).toBe(true);
  });

  it('a window over the cap is refused with an explicit cause for the log, and the live ones keep receiving', async () => {
    const { service, sender, opened } = setup();
    for (let i = 0; i < MAX_SUBSCRIPTIONS_PER_SENDER; i += 1) await service.subscribe(sender, SESSION);
    const refused: unknown = await service.subscribe(sender, SESSION).catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(ViewsError);
    expect((refused as ViewsError).cause).toBeInstanceOf(Error);
    opened[0]?.emit({ kind: 'lost' });
    expect(sender.events).toEqual([{ kind: 'lost' }]);
  });

  it('closeAll (the lock screen) stops every stream of every window', async () => {
    const { service, sender, opened } = setup();
    const other = new FakeSender();
    await service.subscribe(sender, SESSION);
    await service.subscribe(other, OTHER_SESSION);
    service.closeAll();
    expect(opened.map((stream) => stream.closed)).toEqual([true, true]);
    opened[0]?.emit({ kind: 'lost' });
    expect(sender.sent).toEqual([]);
    expect(sender.listeners.size + other.listeners.size).toBe(0);
  });

  it.each([
    ['the lock screen goes up', { crewId: 'AST-01', locked: true }],
    ['someone else signs in', { crewId: 'AST-02', locked: false }],
    ['the astronaut signs out', null],
  ])('sends nothing and stops the stream when %s, even if closeAll was not called', async (_label, session) => {
    const { service, sender, opened, setSession } = setup();
    await service.subscribe(sender, SESSION);
    setSession(session);
    opened[0]?.emit({ kind: 'state', state: STATE });
    expect(sender.sent).toEqual([]);
    expect(opened[0]?.closed).toBe(true);
  });

  it('stops every stream of a window when it is destroyed', async () => {
    const { service, sender, opened } = setup();
    await service.subscribe(sender, SESSION);
    await service.subscribe(sender, OTHER_SESSION);
    sender.destroy();
    expect(opened.map((stream) => stream.closed)).toEqual([true, true]);
    expect(sender.listeners.size).toBe(0);
  });

  it('never sends to a destroyed window, and stops its stream', async () => {
    const { service, sender, opened } = setup();
    await service.subscribe(sender, SESSION);
    sender.destroyed = true;
    opened[0]?.emit({ kind: 'lost' });
    expect(sender.sent).toEqual([]);
    expect(opened[0]?.closed).toBe(true);
  });

  it('rejects a subscribe from a window destroyed while the check was in flight', async () => {
    const { service, sender, opened, hooks } = setup();
    hooks.beforeMembershipAnswers = () => {
      sender.destroyed = true;
    };
    await expect(service.subscribe(sender, SESSION)).rejects.toMatchObject({ code: 'unavailable' });
    expect(opened).toEqual([]);
  });

  it('logs and stops the stream when sending fails', async () => {
    const { service, sender, opened, logged } = setup();
    await service.subscribe(sender, SESSION);
    sender.failSend = true;
    opened[0]?.emit({ kind: 'lost' });
    expect(opened[0]?.closed).toBe(true);
    expect(logged).toHaveLength(1);
  });
});
