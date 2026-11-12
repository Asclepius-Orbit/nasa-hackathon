import { describe, expect, it } from 'vitest';

import type { ChatEvent } from './api';
import { createChatBridge } from './chat-bridge';
import { IPC_CHANNELS, IPC_EVENT_CHANNELS } from './ipc-channels';
import type { IpcRendererLike } from './reps-bridge';

const CONVERSATION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const ID = '0b9c3d4e-1f2a-4b5c-8d6e-000000000001';
const OTHER_ID = '0b9c3d4e-1f2a-4b5c-8d6e-000000000002';
const TEXT: ChatEvent = { kind: 'text', delta: 'Hi' };
const END: ChatEvent = { kind: 'end' };

type Listener = (event: unknown, ...args: unknown[]) => void;

class FakeIpcRenderer implements IpcRendererLike {
  invokes: { channel: string; args: unknown[]; resolve: (value: unknown) => void; reject: (reason: unknown) => void }[] = [];
  listeners = new Set<Listener>();
  invoke(channel: string, ...args: unknown[]): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this.invokes.push({ channel, args, resolve, reject });
    });
  }
  on(channel: string, listener: Listener): void {
    expect(channel).toBe(IPC_EVENT_CHANNELS.chatEvent);
    this.listeners.add(listener);
  }
  removeListener(channel: string, listener: Listener): void {
    expect(channel).toBe(IPC_EVENT_CHANNELS.chatEvent);
    this.listeners.delete(listener);
  }
  push(message: unknown): void {
    for (const listener of [...this.listeners]) listener({ sender: 'electron-internals' }, message);
  }
  channelsInvoked(): string[] {
    return this.invokes.map((call) => call.channel);
  }
}

const flush = async (): Promise<void> => {
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
};
const message = (subscriptionId: string, event: ChatEvent = TEXT) => ({ subscriptionId, event });

function setup() {
  const ipc = new FakeIpcRenderer();
  const received: unknown[][] = [];
  const bridge = createChatBridge(ipc);
  const cancel = bridge.ask({ conversationId: CONVERSATION, question: 'How am I?' }, (...args) => received.push(args));
  return { ipc, received, cancel, bridge };
}

describe('chat bridge: ask', () => {
  it('listens before asking and sends exactly the conversation and question', () => {
    const { ipc } = setup();
    expect(ipc.listeners.size).toBe(1);
    expect(ipc.invokes).toHaveLength(1);
    expect(ipc.invokes[0]?.channel).toBe(IPC_CHANNELS.chatAsk);
    expect(ipc.invokes[0]?.args).toEqual([{ conversationId: CONVERSATION, question: 'How am I?' }]);
  });

  it('delivers the plain event, never the Electron event, and only for its own subscription', async () => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    ipc.push(message(ID));
    ipc.push(message(OTHER_ID, { kind: 'text', delta: 'not mine' }));
    expect(received).toEqual([[TEXT]]);
  });

  it('keeps events that overtake the subscription id and delivers them in order', async () => {
    const { ipc, received } = setup();
    ipc.push(message(ID, TEXT));
    ipc.push(message(OTHER_ID, { kind: 'text', delta: 'not mine' }));
    ipc.push(message(ID, END));
    expect(received).toEqual([]);
    ipc.invokes[0]?.resolve(ID);
    await flush();
    expect(received).toEqual([[TEXT], [END]]);
    expect(ipc.listeners.size).toBe(0);
  });

  it('stops listening after end or error', async () => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    ipc.push(message(ID, { kind: 'error', code: 'busy' }));
    ipc.push(message(ID));
    expect(received).toEqual([[{ kind: 'error', code: 'busy' }]]);
    expect(ipc.listeners.size).toBe(0);
  });

  it('ignores malformed messages', async () => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    for (const bad of [null, 'x', {}, { subscriptionId: ID }, { subscriptionId: ID, event: { kind: 'nope' } }, { subscriptionId: 1, event: TEXT }]) {
      ipc.push(bad);
    }
    ipc.push(message(ID, { kind: 'text' } as unknown as ChatEvent));
    expect(received).toEqual([]);
  });

  it('keeps going when the callback throws', async () => {
    const ipc = new FakeIpcRenderer();
    const seen: ChatEvent[] = [];
    createChatBridge(ipc).ask({ conversationId: CONVERSATION, question: 'q' }, (event) => {
      seen.push(event);
      throw new Error('renderer bug');
    });
    ipc.invokes[0]?.resolve(ID);
    await flush();
    ipc.push(message(ID));
    ipc.push(message(ID, END));
    expect(seen).toEqual([TEXT, END]);
  });

  it.each([
    ['locked', 'locked'],
    ['invalid input', 'unavailable'],
    ['anything else', 'unavailable'],
  ])('reports a refused ask (%s) as an error event with code %s', async (reason, code) => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.reject(new Error(`Error invoking remote method 'asclepius:chat:ask': Error: ${reason}`));
    await flush();
    expect(received).toEqual([[{ kind: 'error', code }]]);
    expect(ipc.listeners.size).toBe(0);
  });

  it('reports an id that is not a string as unavailable', async () => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.resolve(42);
    await flush();
    expect(received).toEqual([[{ kind: 'error', code: 'unavailable' }]]);
    expect(ipc.listeners.size).toBe(0);
  });
});

describe('chat bridge: cancel', () => {
  it('stops listening at once and tells main the id, idempotently', async () => {
    const { ipc, received, cancel } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    cancel();
    cancel();
    ipc.push(message(ID));
    expect(received).toEqual([]);
    expect(ipc.listeners.size).toBe(0);
    expect(ipc.channelsInvoked()).toEqual([IPC_CHANNELS.chatAsk, IPC_CHANNELS.chatCancel]);
    expect(ipc.invokes[1]?.args).toEqual([{ subscriptionId: ID }]);
  });

  it('before main answered, tells main as soon as the id is known', async () => {
    const { ipc, received, cancel } = setup();
    cancel();
    expect(ipc.listeners.size).toBe(0);
    expect(ipc.channelsInvoked()).toEqual([IPC_CHANNELS.chatAsk]);
    ipc.invokes[0]?.resolve(ID);
    await flush();
    expect(ipc.channelsInvoked()).toEqual([IPC_CHANNELS.chatAsk, IPC_CHANNELS.chatCancel]);
    expect(received).toEqual([]);
  });

  it('says nothing when the ask was refused, or when the answer already ended', async () => {
    const refused = setup();
    refused.cancel();
    refused.ipc.invokes[0]?.reject(new Error('locked'));
    await flush();
    expect(refused.ipc.channelsInvoked()).toEqual([IPC_CHANNELS.chatAsk]);

    const ended = setup();
    ended.ipc.invokes[0]?.resolve(ID);
    await flush();
    ended.ipc.push(message(ID, END));
    ended.cancel();
    expect(ended.ipc.channelsInvoked()).toEqual([IPC_CHANNELS.chatAsk]);
  });

  it('swallows a failed cancel', async () => {
    const { ipc, cancel } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    cancel();
    ipc.invokes[1]?.reject(new Error('gone'));
    await flush();
  });
});

describe('chat bridge: forget', () => {
  it('sends the conversation and never rejects', async () => {
    const { ipc, bridge } = setup();
    const done = bridge.forget({ conversationId: CONVERSATION });
    expect(ipc.invokes[1]?.channel).toBe(IPC_CHANNELS.chatForget);
    expect(ipc.invokes[1]?.args).toEqual([{ conversationId: CONVERSATION }]);
    ipc.invokes[1]?.reject(new Error('unavailable'));
    await expect(done).resolves.toBeUndefined();
  });
});
