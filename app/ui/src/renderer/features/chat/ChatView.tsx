import { useEffect, useMemo, useReducer, useRef, useState } from 'react';

import type { ChatErrorCode, ChatEvent } from '../../../shared/api';
import { useApi } from '../../api/ApiContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Panel } from '../../components/ui/Panel';
import { shouldUseMockData } from '../../data/AppDataProvider';
import { createMockChat } from '../../data/mock/MockChat';
import { chat as copy, toolName } from '../../strings/chat';
import styles from './ChatView.module.css';

const MAX_QUESTION = 2000;

type TurnStatus = 'thinking' | 'answering' | 'done' | 'cancelled' | 'error';

interface Turn {
  id: number;
  question: string;
  text: string;
  tools: string[];
  /** The text is a summary made without the language model, not the assistant's own words. */
  fallback: boolean;
  notices: ('correction' | 'mid_stream')[];
  status: TurnStatus;
  error: ChatErrorCode | null;
}

type Action =
  | { type: 'add'; turn: Turn }
  | { type: 'event'; id: number; event: ChatEvent }
  | { type: 'cancel'; id: number }
  | { type: 'reset' };

const LIVE: readonly TurnStatus[] = ['thinking', 'answering'];
const isLive = (turn: Turn | undefined): boolean => turn !== undefined && LIVE.includes(turn.status);

function applyEvent(turn: Turn, event: ChatEvent): Turn {
  switch (event.kind) {
    case 'text':
      return { ...turn, status: 'answering', text: turn.text + event.delta };
    case 'tool_call':
      return { ...turn, status: 'answering', tools: [...turn.tools, event.toolName] };
    case 'fallback':
      return { ...turn, status: 'answering', text: event.text, fallback: true };
    case 'notice':
      return { ...turn, status: 'answering', notices: turn.notices.includes(event.code) ? turn.notices : [...turn.notices, event.code] };
    case 'end':
      return { ...turn, status: 'done' };
    case 'error':
      return { ...turn, status: 'error', error: event.code };
  }
}

function reduce(turns: Turn[], action: Action): Turn[] {
  switch (action.type) {
    case 'add':
      return [...turns, action.turn];
    case 'event':
      return turns.map((turn) => (turn.id === action.id && isLive(turn) ? applyEvent(turn, action.event) : turn));
    case 'cancel':
      return turns.map((turn) => (turn.id === action.id && isLive(turn) ? { ...turn, status: 'cancelled' } : turn));
    case 'reset':
      return [];
  }
}

/**
 * Chat with the spine assistant about the astronaut's own data. It goes straight through the bridge (`useApi().chat`), not
 * the DataSource: it is a live conversation, not a view. One answer at a time; text is shown as plain text as it streams;
 * the assistant's tool calls are shown as small "Looked at" chips; a `fallback` replaces the text and says it is not the
 * assistant's own. Nothing here diagnoses: the screen says so, and a corrected answer says it was corrected.
 */
export function ChatView(): React.JSX.Element {
  const api = useApi();
  // The mock data source has no agent: the scripted stand-in lets the screen be driven end to end.
  const chat = useMemo(() => (shouldUseMockData() ? createMockChat() : api.chat), [api]);
  const [turns, dispatch] = useReducer(reduce, []);
  const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState('');
  const nextId = useRef(1);
  const cancelRef = useRef<(() => void) | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const last = turns.at(-1);
  const busy = isLive(last);
  const canSend = draft.trim().length > 0 && !busy;

  useEffect(
    () => () => {
      cancelRef.current?.();
    },
    [],
  );
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [turns]);

  function start(question: string): void {
    const id = nextId.current++;
    dispatch({
      type: 'add',
      turn: { id, question, text: '', tools: [], fallback: false, notices: [], status: 'thinking', error: null },
    });
    cancelRef.current = chat.ask({ conversationId, question }, (event) => {
      dispatch({ type: 'event', id, event });
      if (event.kind === 'end' || event.kind === 'error') cancelRef.current = null;
    });
  }

  function submit(): void {
    if (!canSend) return;
    start(draft.trim());
    setDraft('');
  }

  function cancelAnswer(): void {
    cancelRef.current?.();
    cancelRef.current = null;
    if (last !== undefined) dispatch({ type: 'cancel', id: last.id });
  }

  function newConversation(): void {
    cancelAnswer();
    void chat.forget({ conversationId });
    dispatch({ type: 'reset' });
    setConversationId(crypto.randomUUID());
  }

  return (
    <section aria-labelledby="chat-title" data-testid="chat-view" className={styles.chat}>
      <header className={styles.head}>
        <div>
          <h1 id="chat-title">{copy.title}</h1>
          <p className={styles.lede}>{copy.lede}</p>
        </div>
        <Button variant="quiet" onClick={newConversation} disabled={turns.length === 0}>
          {copy.newConversation}
        </Button>
      </header>
      <p className={styles.disclaimer}>{copy.disclaimer}</p>

      {turns.length === 0 ? (
        <p className={styles.empty}>{copy.empty}</p>
      ) : (
        <ol className={styles.thread} aria-label={copy.conversationLabel}>
          {turns.map((turn) => (
            <li key={turn.id} className={styles.turn}>
              <div className={styles.question}>
                <b className={styles.who}>{copy.you}</b>
                <p className={styles.plain}>{turn.question}</p>
              </div>
              <Panel className={styles.answer} aria-busy={isLive(turn)}>
                <b className={styles.who}>{copy.assistant}</b>
                {turn.tools.length > 0 && (
                  <ul className={styles.tools}>
                    {turn.tools.map((tool, index) => (
                      // A tool may be used more than once in a turn, so the position is part of the key.
                      <li key={`${tool}-${String(index)}`} className={styles.chip}>
                        {copy.tool(toolName(tool))}
                      </li>
                    ))}
                  </ul>
                )}
                {turn.status === 'thinking' && (
                  <p role="status" className={styles.thinking}>
                    <span>{copy.thinking}</span> <small>{copy.slowNote}</small>
                  </p>
                )}
                {turn.fallback && <p className={styles.fallback}>{copy.fallbackLabel}</p>}
                {turn.text !== '' && <p className={styles.text}>{turn.text}</p>}
                {turn.notices.map((code) => (
                  <p key={code} className={styles.notice}>
                    {copy.notice[code]}
                  </p>
                ))}
                {turn.status === 'cancelled' && <p className={styles.notice}>{copy.stopped}</p>}
                {turn.error !== null && (
                  <Alert
                    variant="warning"
                    action={
                      turn === last ? (
                        <Button
                          variant="quiet"
                          onClick={() => {
                            start(turn.question);
                          }}
                        >
                          {copy.retry}
                        </Button>
                      ) : undefined
                    }
                  >
                    {copy.errors[turn.error]}
                  </Alert>
                )}
              </Panel>
            </li>
          ))}
        </ol>
      )}
      <div ref={endRef} />

      <form
        className={styles.composer}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label htmlFor="chat-question" className={styles.label}>
          {copy.questionLabel}
        </label>
        <div className={styles.row}>
          <textarea
            id="chat-question"
            className={styles.input}
            value={draft}
            maxLength={MAX_QUESTION}
            rows={2}
            placeholder={copy.placeholder}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              }
            }}
          />
          {busy ? (
            <Button variant="quiet" onClick={cancelAnswer}>
              {copy.cancel}
            </Button>
          ) : (
            <Button type="submit" disabled={!canSend}>
              {copy.send}
            </Button>
          )}
        </div>
        <small className={styles.hint}>{copy.hint}</small>
      </form>
    </section>
  );
}
