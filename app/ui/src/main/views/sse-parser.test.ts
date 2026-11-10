import { describe, expect, it } from 'vitest';

import { createSseParser, MAX_SSE_BUFFER_CHARS, type SseEvent } from './sse-parser';

function parse(chunks: string[], finish = true): SseEvent[] {
  const events: SseEvent[] = [];
  const parser = createSseParser((event) => events.push(event));
  for (const chunk of chunks) parser.push(chunk);
  if (finish) parser.finish();
  return events;
}

const STREAM =
  ': keep-alive\r\n' +
  'event: state\r\ndata: {"a":1}\r\n\r\n' +
  'event: state\ndata: line1\ndata: line2\n\n' +
  'data:no-space\r\r' +
  'retry: 10\nid: 7\nevent: end\ndata: {"b":2}\r\n\r\n';

const EXPECTED: SseEvent[] = [
  { event: 'state', data: '{"a":1}' },
  { event: 'state', data: 'line1\nline2' },
  { event: 'message', data: 'no-space' },
  { event: 'end', data: '{"b":2}' },
];

describe('createSseParser', () => {
  it('parses LF, CRLF and CR line endings, comments, multi-line data and ignored fields', () => {
    expect(parse([STREAM])).toEqual(EXPECTED);
  });

  it('gives the same events however the stream is cut between reads (including inside CRLF)', () => {
    for (let cut = 1; cut < STREAM.length; cut += 1) {
      expect(parse([STREAM.slice(0, cut), STREAM.slice(cut)])).toEqual(EXPECTED);
    }
    expect(parse(STREAM.split(''))).toEqual(EXPECTED); // ASCII only: one code unit is one character
  });

  it('skips a leading BOM and strips only one space after the colon', () => {
    expect(parse(['﻿event: state\ndata:  two\n\n'])).toEqual([{ event: 'state', data: ' two' }]);
  });

  it('does not dispatch an event without data, nor one the stream cut short', () => {
    expect(parse(['event: state\n\n', 'event: end\ndata: {"b":2}\n'])).toEqual([]);
  });

  it('does not carry the event name over to the next event', () => {
    expect(parse(['event: end\ndata: 1\n\ndata: 2\n\n'])).toEqual([
      { event: 'end', data: '1' },
      { event: 'message', data: '2' },
    ]);
  });

  it('treats a trailing CR at end of stream as a line ending', () => {
    expect(parse(['data: 1\r\r'])).toEqual([{ event: 'message', data: '1' }]);
  });

  it('throws when a single event grows without bound', () => {
    const parser = createSseParser(() => undefined);
    expect(() => {
      parser.push(`data: ${'x'.repeat(MAX_SSE_BUFFER_CHARS)}`);
    }).toThrow(/too large/u);
    const lines = createSseParser(() => undefined);
    expect(() => {
      for (let i = 0; i < MAX_SSE_BUFFER_CHARS; i += 1) lines.push('data: xx\n');
    }).toThrow(/too large/u);
  });
});
