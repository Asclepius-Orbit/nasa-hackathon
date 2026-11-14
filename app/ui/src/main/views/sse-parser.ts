/**
 * Incremental parser for `text/event-stream` (WHATWG HTML, "Server-sent events"), fed with decoded text in
 * whatever pieces the network delivers. Lines end in LF, CRLF or CR, and a CRLF may be cut between two chunks.
 * Only `event` and `data` are used; `id`, `retry` and comments are read and dropped. An event the stream cut short
 * (no closing blank line) is never dispatched, as the spec says.
 */

/** Most text kept for one unfinished line plus its event; the events here are small JSON objects. */
export const MAX_SSE_BUFFER_CHARS = 64 * 1024;

export interface SseEvent {
  /** The `event:` name, `message` when the event has none. */
  event: string;
  /** The `data:` lines joined with `\n`. */
  data: string;
}

export interface SseParser {
  push(chunk: string): void;
  /** The stream ended: a line terminated by a final lone CR still counts. */
  finish(): void;
}

export function createSseParser(onEvent: (event: SseEvent) => void): SseParser {
  let buffer = '';
  let atStart = true;
  let eventName = '';
  let data: string[] = [];
  let dataChars = 0;

  function dispatch(): void {
    const lines = data;
    const event = eventName === '' ? 'message' : eventName;
    eventName = '';
    data = [];
    dataChars = 0;
    if (lines.length > 0) onEvent({ event, data: lines.join('\n') });
  }

  function line(text: string): void {
    if (text === '') {
      dispatch();
      return;
    }
    if (text.startsWith(':')) return;
    const colon = text.indexOf(':');
    const field = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? '' : text.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') eventName = value;
    else if (field === 'data') {
      data.push(value);
      dataChars += value.length + 1;
    }
  }

  return {
    push(chunk) {
      buffer += chunk;
      if (atStart && buffer !== '') {
        atStart = false;
        if (buffer.startsWith('﻿')) buffer = buffer.slice(1);
      }
      let start = 0;
      for (;;) {
        const end = buffer.slice(start).search(/[\r\n]/u);
        if (end === -1) break;
        const at = start + end;
        // A CR at the very end may be the first half of a CRLF: wait for the next chunk.
        if (buffer[at] === '\r' && at === buffer.length - 1) break;
        line(buffer.slice(start, at));
        start = at + (buffer[at] === '\r' && buffer[at + 1] === '\n' ? 2 : 1);
      }
      buffer = buffer.slice(start);
      if (buffer.length + dataChars > MAX_SSE_BUFFER_CHARS) throw new Error('SSE event too large');
    },
    finish() {
      if (buffer.endsWith('\r')) line(buffer.slice(0, -1));
      buffer = '';
    },
  };
}
