import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ChatErrorCode, ChatEvent } from '../../../shared/api';
import { FakeAsclepiusApi } from '../../test/fake-api';
import { renderInWorld } from '../../test/providers';
import { ChatView } from './ChatView';

afterEach(() => {
  vi.unstubAllEnvs();
});

function setup(): { api: FakeAsclepiusApi } & ReturnType<typeof renderInWorld> {
  return renderInWorld(<ChatView />, { route: '/chat' });
}

const box = (): HTMLElement => screen.getByRole('textbox', { name: 'Your question' });
async function ask(question: string): Promise<void> {
  await userEvent.type(box(), question);
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));
}
const push = (api: FakeAsclepiusApi, ...events: ChatEvent[]): void => {
  act(() => {
    for (const event of events) api.emitChat(event);
  });
};

describe('ChatView: asking', () => {
  it('says the assistant does not diagnose and invites a first question', () => {
    setup();
    expect(screen.getByRole('heading', { level: 1, name: 'Ask' })).toBeInTheDocument();
    expect(screen.getByText(/The assistant does not diagnose/)).toBeInTheDocument();
    expect(screen.getByText(/No questions yet/)).toBeInTheDocument();
  });

  it('sends the trimmed question with a lowercase conversation id and shows it as a turn', async () => {
    const { api } = setup();
    await ask('  How is my back today?  ');
    expect(api.chatAsks).toHaveLength(1);
    expect(api.chatAsks[0]?.question).toBe('How is my back today?');
    expect(api.chatAsks[0]?.conversationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u);
    const thread = screen.getByRole('list', { name: 'Conversation' });
    expect(within(thread).getByText('How is my back today?')).toBeInTheDocument();
    expect(box()).toHaveValue('');
    expect(screen.queryByText(/No questions yet/)).not.toBeInTheDocument();
  });

  it('Enter sends, Shift+Enter makes a new line', async () => {
    const { api } = setup();
    await userEvent.type(box(), 'line one{Shift>}{Enter}{/Shift}line two');
    expect(api.chatAsks).toHaveLength(0);
    expect(box()).toHaveValue('line one\nline two');
    await userEvent.type(box(), '{Enter}');
    expect(api.chatAsks[0]?.question).toBe('line one\nline two');
  });

  it('does not send a blank question', async () => {
    const { api } = setup();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    await userEvent.type(box(), '   {Enter}');
    expect(api.chatAsks).toHaveLength(0);
  });

  it('limits the question to 2000 characters', () => {
    setup();
    expect(box()).toHaveAttribute('maxlength', '2000');
  });

  it('says it is thinking until the first event, and warns that it can take a minute', async () => {
    const { api } = setup();
    await ask('hello');
    expect(screen.getByText('Thinking…')).toBeInTheDocument();
    expect(screen.getByText(/can take up to a minute/)).toBeInTheDocument();
    push(api, { kind: 'text', delta: 'Hi' });
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument();
  });

  it('keeps the question box locked to one answer at a time', async () => {
    const { api } = setup();
    await ask('first');
    expect(screen.getByRole('button', { name: 'Cancel answer' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    await userEvent.type(box(), 'second{Enter}');
    expect(api.chatAsks).toHaveLength(1);
    push(api, { kind: 'end' });
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
  });
});

describe('ChatView: the answer', () => {
  it('appends streamed text', async () => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'text', delta: 'Your ' }, { kind: 'text', delta: 'back is fine.' });
    expect(screen.getByText('Your back is fine.')).toBeInTheDocument();
    push(api, { kind: 'end' });
    expect(screen.getByText('Your back is fine.')).toBeInTheDocument();
  });

  it('shows each tool call as a "Looked at" chip with a plain name', async () => {
    const { api } = setup();
    await ask('q');
    push(
      api,
      { kind: 'tool_call', toolName: 'get_current_status' },
      { kind: 'tool_call', toolName: 'compare_periods' },
      { kind: 'tool_call', toolName: 'new_tool_x' },
    );
    expect(screen.getByText('Looked at: your current status')).toBeInTheDocument();
    expect(screen.getByText('Looked at: a comparison of two periods')).toBeInTheDocument();
    expect(screen.getByText('Looked at: new tool x')).toBeInTheDocument();
  });

  it.each([
    ['get_current_status', 'your current status'],
    ['get_history', 'your history'],
    ['get_sessions', 'your sessions'],
    ['get_self_reports', 'your self-reports'],
    ['compare_periods', 'a comparison of two periods'],
    ['explain_assessment', 'how the assessment was reached'],
    ['simulate_scenario', 'a what-if simulation'],
  ])('names the tool %s as "%s"', async (tool, name) => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'tool_call', toolName: tool });
    expect(screen.getByText(`Looked at: ${name}`)).toBeInTheDocument();
  });

  it('a fallback replaces the text so far and is labelled as a summary without the assistant', async () => {
    const { api } = setup();
    await ask('q');
    push(
      api,
      { kind: 'text', delta: 'Partial words' },
      { kind: 'notice', code: 'mid_stream', text: 'Model failed.' },
      { kind: 'fallback', text: 'Status: amber.' },
      { kind: 'end' },
    );
    expect(screen.queryByText(/Partial words/)).not.toBeInTheDocument();
    expect(screen.getByText('Status: amber.')).toBeInTheDocument();
    expect(screen.getByText('Summary without the assistant')).toBeInTheDocument();
    expect(screen.getByText('The assistant stopped part-way. What it had written was removed.')).toBeInTheDocument();
  });

  it('a correction notice keeps the text and says the answer was corrected', async () => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'text', delta: 'You have a disc problem.' }, { kind: 'notice', code: 'correction', text: 'x' }, { kind: 'end' });
    expect(screen.getByText('You have a disc problem.')).toBeInTheDocument();
    expect(screen.getByText('This answer was corrected: it is not a diagnosis.')).toBeInTheDocument();
    expect(screen.queryByText('Summary without the assistant')).not.toBeInTheDocument();
  });

  it('renders the answer as text, never as markup', async () => {
    const { api, container } = setup();
    await ask('q');
    push(api, { kind: 'text', delta: '<img src=x onerror=alert(1)>' });
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });
});

describe('ChatView: errors', () => {
  it.each([
    ['locked', 'The assistant did not accept this tablet. Unlock the tablet and ask again.'],
    ['busy', 'The assistant is still answering the previous question. Wait for it to finish, then ask again.'],
    ['invalid', 'The assistant could not use that question. Reword it, or start a new conversation, and ask again.'],
    ['unavailable', 'The assistant is not available right now. The other screens still work. Try again in a minute.'],
    ['internal', 'The assistant had a problem of its own. Try again. If it keeps happening, tell the flight surgeon.'],
  ] as [ChatErrorCode, string][])('explains %s', async (code, text) => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'error', code });
    expect(screen.getByRole('alert')).toHaveTextContent(text);
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
  });

  it('keeps the text received before a cut', async () => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'text', delta: 'Half an ans' }, { kind: 'error', code: 'unavailable' });
    expect(screen.getByText('Half an ans')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('"Ask again" asks the same question as a new turn', async () => {
    const { api } = setup();
    await ask('How am I?');
    push(api, { kind: 'error', code: 'unavailable' });
    await userEvent.click(screen.getByRole('button', { name: 'Ask again' }));
    expect(api.chatAsks.map((a) => a.question)).toEqual(['How am I?', 'How am I?']);
    expect(api.chatAsks[1]?.conversationId).toBe(api.chatAsks[0]?.conversationId);
    expect(screen.getAllByText('How am I?')).toHaveLength(2);
    expect(screen.getByText('Thinking…')).toBeInTheDocument();
  });

  it('offers "Ask again" only on the last turn', async () => {
    const { api } = setup();
    await ask('first');
    push(api, { kind: 'error', code: 'busy' });
    await ask('second');
    push(api, { kind: 'end' });
    expect(screen.queryByRole('button', { name: 'Ask again' })).not.toBeInTheDocument();
  });
});

describe('ChatView: cancel and new conversation', () => {
  it('Cancel stops the answer, keeps what was written and frees the box', async () => {
    const { api } = setup();
    await ask('q');
    push(api, { kind: 'text', delta: 'So far' });
    await userEvent.click(screen.getByRole('button', { name: 'Cancel answer' }));
    expect(api.chatAsks[0]?.cancelled).toBe(true);
    expect(screen.getByText('So far')).toBeInTheDocument();
    expect(screen.getByText('Answer cancelled.')).toBeInTheDocument();
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
    push(api, { kind: 'text', delta: ' late' });
    expect(screen.queryByText(/late/)).not.toBeInTheDocument();
  });

  it('New conversation forgets the old one, clears the turns and starts a new id', async () => {
    const { api } = setup();
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeDisabled();
    await ask('first');
    push(api, { kind: 'end' });
    const first = api.chatAsks[0]?.conversationId;
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(api.chatForgotten).toEqual([first]);
    expect(screen.getByText(/No questions yet/)).toBeInTheDocument();
    await ask('second');
    expect(api.chatAsks[1]?.conversationId).not.toBe(first);
  });

  it('New conversation while answering cancels the answer first', async () => {
    const { api } = setup();
    await ask('q');
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(api.chatAsks[0]?.cancelled).toBe(true);
    expect(api.chatForgotten).toHaveLength(1);
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument();
  });

  it('cancels the live answer when the screen is left', async () => {
    const { api, unmount } = setup();
    await ask('q');
    unmount();
    expect(api.chatAsks[0]?.cancelled).toBe(true);
  });

  it('leaving with nothing asked cancels nothing', () => {
    const { api, unmount } = setup();
    unmount();
    expect(api.calls).not.toContain('chat.cancel');
  });
});

describe('ChatView: mock data', () => {
  it('answers with the scripted sample, without the agent', async () => {
    vi.stubEnv('VITE_MOCK_DATA', 'true');
    const { api } = setup();
    await ask('How am I?');
    expect(await screen.findByText(/This is a sample answer/, {}, { timeout: 8000 })).toBeInTheDocument();
    expect(await screen.findByText(/I do not diagnose/, {}, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByText('Looked at: your current status')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Send' }, { timeout: 8000 })).toBeInTheDocument();
    expect(api.chatAsks).toHaveLength(0);
  }, 20_000);
});
