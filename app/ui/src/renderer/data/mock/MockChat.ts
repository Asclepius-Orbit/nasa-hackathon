import type { AsclepiusApi, ChatEvent } from '../../../shared/api';

/**
 * A scripted stand-in for the spine agent, for the mock data source (e2e build, `VITE_MOCK_DATA=true`): it needs no agent
 * and no LLM. Every question gets the same canned answer, streamed in pieces after a short "looking" pause, so the chat
 * screen can be driven end to end. It says it is canned, so nobody mistakes it for an assessment.
 */
export const MOCK_ANSWER =
  'This is a sample answer: no assistant is connected in this mode. In the sample data your assessment today is amber, ' +
  'with the morning pain a little above your usual and the last session shorter than planned. ' +
  'I do not diagnose. If the pain gets worse, stop and tell the flight surgeon.';

/** The part of the timers used here, so tests can drive time by hand. */
export interface MockTimers {
  setTimeout(fn: () => void, ms: number): number;
  clearTimeout(id: number): void;
}

const SYSTEM_TIMERS: MockTimers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => {
    globalThis.clearTimeout(id);
  },
};

const THINK_MS = 250;
const PIECE_MS = 60;
const PIECE_WORDS = 4;

function script(): ChatEvent[] {
  const words = MOCK_ANSWER.split(' ');
  const pieces: ChatEvent[] = [];
  for (let i = 0; i < words.length; i += PIECE_WORDS) {
    const delta = words.slice(i, i + PIECE_WORDS).join(' ');
    pieces.push({ kind: 'text', delta: i === 0 ? delta : ` ${delta}` });
  }
  return [{ kind: 'tool_call', toolName: 'get_current_status' }, ...pieces, { kind: 'end' }];
}

export function createMockChat(timers: MockTimers = SYSTEM_TIMERS): AsclepiusApi['chat'] {
  return {
    ask(_input, onEvent) {
      const events = script();
      let handle: number | undefined;
      let stopped = false;
      const step = (index: number): void => {
        handle = timers.setTimeout(
          () => {
            const event = events[index];
            if (stopped || event === undefined) return;
            onEvent(event);
            if (index + 1 < events.length) step(index + 1);
          },
          index === 0 ? THINK_MS : PIECE_MS,
        );
      };
      step(0);
      return () => {
        stopped = true;
        if (handle !== undefined) timers.clearTimeout(handle);
      };
    },
    forget: () => Promise.resolve(),
  };
}
