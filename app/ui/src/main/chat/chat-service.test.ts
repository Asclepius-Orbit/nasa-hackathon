import { describe, expect, it } from 'vitest';

import type { AuthSession, ChatEvent, ChatMessage } from '../../shared/api';
import { IPC_EVENT_CHANNELS } from '../../shared/ipc-channels';
import type { WebContentsLike } from '../security/app-url';
import { createChatService, MAX_ANSWERS_PER_SENDER } from './chat-service';
import type { ChatAskInput, ChatStreamClient } from './chat-stream';

const CONVERSATION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const QUESTION = { conversationId: CONVERSATION, question: 'How am I?' };

class FakeSender implements WebContentsLike {
  sent: { channel: string; payload: ChatMessage }[] = [];
  destroyed = false;
  failSend = false;
  listeners = new Set<() => void>();
  send(channel: string, payload: unknown): void {
    if (this.failSend) throw new Error('Object has been destroyed');
    this.sent.push({ channel, payload: payload as ChatMessage });
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
  get events(): ChatEvent[] {
    return this.sent.map(({ payload }) => payload.event);
  }
}

interface Opened {
  input: ChatAskInput;
  emit: (event: ChatEvent) => void;
  cancelled: boolean;
}

function setup() {
  let session: AuthSession | null = { crewId: 'AST-01', locked: false };
  const opened: Opened[] = [];
  const forgotten: { conversationId: string; astronautId: string }[] = [];
  const logged: unknown[] = [];
  let ids = 0;
  const chat: ChatStreamClient = {
    ask(input, emit) {
      const stream: Opened = { input, emit, cancelled: false };
      opened.push(stream);
      return () => {
        stream.cancelled = true;
      };
    },
    forget(conversationId, astronautId) {
      forgotten.push({ conversationId, astronautId });
      return Promise.resolve();
    },
  };
  const service = createChatService({
    auth: { current: () => (session === null ? null : { ...session }) },
    chat,
    newId: () => `0b9c3d4e-1f2a-4b5c-8d6e-${String(++ids).padStart(12, '0')}`,
    logError: (error) => logged.push(error),
  });
  return {
    service,
    opened,
    forgotten,
    logged,
    sender: new FakeSender(),
    setSession: (next: AuthSession | null) => {
      session = next;
    },
  };
}

describe('createChatService.ask', () => {
  it('asks as the unlocked astronaut, never as one the caller names', () => {
    const { service, sender, opened } = setup();
    const id = service.ask(sender, QUESTION);
    expect(id).toBe('0b9c3d4e-1f2a-4b5c-8d6e-000000000001');
    expect(opened.map((o) => o.input)).toEqual([{ ...QUESTION, astronautId: 'AST-01' }]);
  });

  it.each([
    ['nobody is signed in', null],
    ['the lock screen is up', { crewId: 'AST-01', locked: true }],
  ])('throws "locked" without any request when %s', (_label, session) => {
    const { service, sender, opened, setSession } = setup();
    setSession(session);
    expect(() => service.ask(sender, QUESTION)).toThrow(expect.objectContaining({ code: 'locked' }));
    expect(opened).toEqual([]);
  });

  it('pushes each event to the asking window only, tagged with the subscription id', () => {
    const { service, sender, opened } = setup();
    const other = new FakeSender();
    const id = service.ask(sender, QUESTION);
    service.ask(other, QUESTION);
    opened[0]?.emit({ kind: 'text', delta: 'Hi' });
    opened[0]?.emit({ kind: 'tool_call', toolName: 'get_history' });
    expect(sender.sent).toEqual([
      { channel: IPC_EVENT_CHANNELS.chatEvent, payload: { subscriptionId: id, event: { kind: 'text', delta: 'Hi' } } },
      { channel: IPC_EVENT_CHANNELS.chatEvent, payload: { subscriptionId: id, event: { kind: 'tool_call', toolName: 'get_history' } } },
    ]);
    expect(other.sent).toEqual([]);
  });

  it.each(['end', 'error'] as const)('delivers %s last and frees everything', (kind) => {
    const { service, sender, opened } = setup();
    service.ask(sender, QUESTION);
    opened[0]?.emit(kind === 'end' ? { kind: 'end' } : { kind: 'error', code: 'busy' });
    expect(sender.events).toHaveLength(1);
    expect(opened[0]?.cancelled).toBe(true);
    expect(sender.listeners.size).toBe(0);
    opened[0]?.emit({ kind: 'text', delta: 'late' });
    expect(sender.events).toHaveLength(1);
  });

  it('turns an event that does not pass validation into an error and stops that answer', () => {
    const { service, sender, opened, logged } = setup();
    service.ask(sender, QUESTION);
    opened[0]?.emit({ kind: 'tool_call', toolName: 'Not A Tool!' });
    expect(sender.events).toEqual([{ kind: 'error', code: 'unavailable' }]);
    expect(opened[0]?.cancelled).toBe(true);
    expect(logged).toHaveLength(1);
  });

  it('stops delivering when the lock screen goes up without anyone calling stopAll', () => {
    const { service, sender, opened, setSession } = setup();
    service.ask(sender, QUESTION);
    setSession({ crewId: 'AST-01', locked: true });
    opened[0]?.emit({ kind: 'text', delta: 'secret' });
    expect(sender.sent).toEqual([]);
    expect(opened[0]?.cancelled).toBe(true);
  });

  it('stops delivering when another astronaut signed in', () => {
    const { service, sender, opened, setSession } = setup();
    service.ask(sender, QUESTION);
    setSession({ crewId: 'AST-02', locked: false });
    opened[0]?.emit({ kind: 'text', delta: 'secret' });
    expect(sender.sent).toEqual([]);
    expect(opened[0]?.cancelled).toBe(true);
  });

  it('stops when the window is gone or sending fails', () => {
    const { service, sender, opened, logged } = setup();
    service.ask(sender, QUESTION);
    sender.failSend = true;
    opened[0]?.emit({ kind: 'text', delta: 'x' });
    expect(opened[0]?.cancelled).toBe(true);
    expect(logged).toHaveLength(1);

    const gone = new FakeSender();
    service.ask(gone, QUESTION);
    gone.destroyed = true;
    opened[1]?.emit({ kind: 'text', delta: 'x' });
    expect(gone.sent).toEqual([]);
    expect(opened[1]?.cancelled).toBe(true);
  });

  it('cancels every answer of a window when it is destroyed', () => {
    const { service, sender, opened } = setup();
    service.ask(sender, QUESTION);
    service.ask(sender, { ...QUESTION, conversationId: '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d' });
    sender.destroy();
    expect(opened.every((o) => o.cancelled)).toBe(true);
    expect(sender.listeners.size).toBe(0);
  });

  it('refuses a window that already has too many live answers, leaving them alone', () => {
    const { service, sender, opened, logged } = setup();
    for (let i = 0; i < MAX_ANSWERS_PER_SENDER; i += 1) service.ask(sender, QUESTION);
    expect(() => service.ask(sender, QUESTION)).toThrow(expect.objectContaining({ code: 'unavailable' }));
    expect(opened).toHaveLength(MAX_ANSWERS_PER_SENDER);
    expect(opened.some((o) => o.cancelled)).toBe(false);
    expect(logged).toEqual([]);
  });

  it('throws unavailable when the window is already destroyed', () => {
    const { service, sender, opened } = setup();
    sender.destroyed = true;
    expect(() => service.ask(sender, QUESTION)).toThrow(expect.objectContaining({ code: 'unavailable' }));
    expect(opened).toEqual([]);
  });
});

describe('createChatService.cancel', () => {
  it('cancels one answer of the window, silently and idempotently', () => {
    const { service, sender, opened } = setup();
    const id = service.ask(sender, QUESTION);
    service.cancel(sender, id);
    service.cancel(sender, id);
    expect(opened[0]?.cancelled).toBe(true);
    opened[0]?.emit({ kind: 'text', delta: 'late' });
    expect(sender.sent).toEqual([]);
  });

  it('lets a window cancel only its own answers', () => {
    const { service, sender, opened } = setup();
    const id = service.ask(sender, QUESTION);
    service.cancel(new FakeSender(), id);
    service.cancel(sender, 'not-an-id');
    expect(opened[0]?.cancelled).toBe(false);
  });
});

describe('createChatService.stopAll', () => {
  it('cancels the answers of every window', () => {
    const { service, sender, opened } = setup();
    const other = new FakeSender();
    service.ask(sender, QUESTION);
    service.ask(other, QUESTION);
    service.stopAll();
    expect(opened.every((o) => o.cancelled)).toBe(true);
    expect(sender.listeners.size + other.listeners.size).toBe(0);
    opened[0]?.emit({ kind: 'text', delta: 'late' });
    expect(sender.sent).toEqual([]);
  });
});

describe('createChatService.forget', () => {
  it('forgets the conversation as the unlocked astronaut', async () => {
    const { service, forgotten } = setup();
    await service.forget(CONVERSATION);
    expect(forgotten).toEqual([{ conversationId: CONVERSATION, astronautId: 'AST-01' }]);
  });

  it('does nothing while locked, and never rejects', async () => {
    const { service, forgotten, setSession } = setup();
    setSession({ crewId: 'AST-01', locked: true });
    await expect(service.forget(CONVERSATION)).resolves.toBeUndefined();
    expect(forgotten).toEqual([]);
  });
});
