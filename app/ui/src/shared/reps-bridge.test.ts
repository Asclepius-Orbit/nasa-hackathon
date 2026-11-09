import { describe, expect, it } from 'vitest';

import type { RepsEvent } from './api';
import { IPC_CHANNELS, IPC_EVENT_CHANNELS } from './ipc-channels';
import { createRepsBridge, type IpcRendererLike } from './reps-bridge';

const SESSION = '3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b';
const ID = '0b9c3d4e-1f2a-4b5c-8d6e-000000000001';
const OTHER_ID = '0b9c3d4e-1f2a-4b5c-8d6e-000000000002';
const LOST: RepsEvent = { kind: 'lost' };

type Listener = (event: unknown, ...args: unknown[]) => void;

/** Stands in for `ipcRenderer`: the test decides when (and how) each invoke settles and what main pushes. */
class FakeIpcRenderer implements IpcRendererLike {
  invokes: { channel: string; args: unknown[]; resolve: (value: unknown) => void; reject: (reason: unknown) => void }[] = [];
  listeners = new Set<Listener>();
  invoke(channel: string, ...args: unknown[]): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this.invokes.push({ channel, args, resolve, reject });
    });
  }
  on(channel: string, listener: Listener): void {
    expect(channel).toBe(IPC_EVENT_CHANNELS.repsEvent);
    this.listeners.add(listener);
  }
  removeListener(channel: string, listener: Listener): void {
    expect(channel).toBe(IPC_EVENT_CHANNELS.repsEvent);
    this.listeners.delete(listener);
  }
  /** What `webContents.send` does: the listener gets Electron's event object first. */
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
const message = (subscriptionId: string, event: RepsEvent = LOST) => ({ subscriptionId, sessionId: SESSION, event });

function setup() {
  const ipc = new FakeIpcRenderer();
  const received: unknown[][] = [];
  const unsubscribe = createRepsBridge(ipc).subscribe(SESSION, (...args) => received.push(args));
  return { ipc, received, unsubscribe };
}

describe('createRepsBridge', () => {
  it('subscribes with exactly one argument, { sessionId }', () => {
    const { ipc } = setup();
    expect(ipc.invokes).toHaveLength(1);
    expect(ipc.invokes[0]?.channel).toBe(IPC_CHANNELS.repsSubscribe);
    expect(ipc.invokes[0]?.args).toEqual([{ sessionId: SESSION }]);
  });

  it('hands the callback only the events of its own subscription, and never the Electron event', async () => {
    const { ipc, received } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    ipc.push(message(ID));
    ipc.push(message(OTHER_ID, { kind: 'error', code: 'no_reps' }));
    ipc.push({ nonsense: true });
    ipc.push(null);
    ipc.push(message(ID, { kind: 'bogus' } as never));
    ipc.push(message(ID, { kind: 'error', code: 'no_reps' }));
    expect(received).toEqual([[LOST], [{ kind: 'error', code: 'no_reps' }]]);
  });

  it('keeps events that arrive before the subscription id does, and drops the ones of other subscriptions', async () => {
    const { ipc, received } = setup();
    ipc.push(message(ID));
    ipc.push(message(OTHER_ID, { kind: 'error', code: 'not_found' }));
    expect(received).toEqual([]);
    ipc.invokes[0]?.resolve(ID);
    await flush();
    expect(received).toEqual([[LOST]]);
  });

  it('stops listening and tells main to unsubscribe, once however often it is called', async () => {
    const { ipc, received, unsubscribe } = setup();
    ipc.invokes[0]?.resolve(ID);
    await flush();
    unsubscribe();
    unsubscribe();
    expect(ipc.listeners.size).toBe(0);
    expect(ipc.invokes.slice(1).map(({ channel, args }) => [channel, args])).toEqual([
      [IPC_CHANNELS.repsUnsubscribe, [{ subscriptionId: ID }]],
    ]);
    ipc.push(message(ID));
    expect(received).toEqual([]);
  });

  it('unsubscribes at once even if the subscription has not been confirmed yet', async () => {
    const { ipc, received, unsubscribe } = setup();
    unsubscribe();
    expect(ipc.listeners.size).toBe(0);
    ipc.push(message(ID));
    expect(ipc.channelsInvoked()).toEqual([IPC_CHANNELS.repsSubscribe]);
    ipc.invokes[0]?.resolve(ID);
    await flush();
    expect(ipc.invokes.slice(1).map(({ channel, args }) => [channel, args])).toEqual([
      [IPC_CHANNELS.repsUnsubscribe, [{ subscriptionId: ID }]],
    ]);
    expect(received).toEqual([]);
  });

  it('does not unsubscribe a subscription main refused, and reports the refusal as an error event', async () => {
    const { ipc, received, unsubscribe } = setup();
    ipc.invokes[0]?.reject(new Error(`Error invoking remote method '${IPC_CHANNELS.repsSubscribe}': Error: locked`));
    await flush();
    expect(received).toEqual([[{ kind: 'error', code: 'locked' }]]);
    expect(ipc.listeners.size).toBe(0);
    unsubscribe();
    expect(ipc.channelsInvoked()).toEqual([IPC_CHANNELS.repsSubscribe]);
  });

  it.each([
    ['an unrecognized rejection', () => ({ reject: new Error('EACCES C:\\secret') })],
    ['an answer that is not an id', () => ({ resolve: { id: ID } })],
  ])('reports %s as "unavailable"', async (_label, outcome) => {
    const { ipc, received } = setup();
    const { reject, resolve } = outcome() as { reject?: Error; resolve?: unknown };
    if (reject !== undefined) ipc.invokes[0]?.reject(reject);
    else ipc.invokes[0]?.resolve(resolve);
    await flush();
    expect(received).toEqual([[{ kind: 'error', code: 'unavailable' }]]);
    expect(ipc.listeners.size).toBe(0);
  });

  it('keeps delivering when a callback throws', async () => {
    const ipc = new FakeIpcRenderer();
    const received: RepsEvent[] = [];
    createRepsBridge(ipc).subscribe(SESSION, (event) => {
      received.push(event);
      throw new Error('renderer bug');
    });
    ipc.invokes[0]?.resolve(ID);
    await flush();
    expect(() => {
      ipc.push(message(ID));
      ipc.push(message(ID));
    }).not.toThrow();
    expect(received).toHaveLength(2);
  });
});
