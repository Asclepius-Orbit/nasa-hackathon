import type { ChatErrorCode } from '../../shared/api';

/** Copy of the "Ask" screen: a chat with the assistant that reads the astronaut's own spine data. */
export const chat = {
  title: 'Ask',
  lede: 'Ask about your own readings, sessions and reports. The assistant explains what the monitor measured.',
  disclaimer: 'The assistant does not diagnose. For a symptom or any doubt, use Urgent symptom or talk to the flight surgeon.',
  slowNote: 'The first words can take up to a minute.',
  conversationLabel: 'Conversation',
  empty: 'No questions yet. Try “How is my back today?” or “What changed since last week?”',
  questionLabel: 'Your question',
  placeholder: 'Type your question',
  hint: 'Enter sends. Shift and Enter start a new line.',
  send: 'Send',
  cancel: 'Cancel answer',
  newConversation: 'New conversation',
  you: 'You',
  assistant: 'Assistant',
  thinking: 'Thinking…',
  stopped: 'Answer cancelled.',
  retry: 'Ask again',
  /** Shown above a summary made without the language model. */
  fallbackLabel: 'Summary without the assistant',
  tool: (name: string): string => `Looked at: ${name}`,
  notice: {
    correction: 'This answer was corrected: it is not a diagnosis.',
    mid_stream: 'The assistant stopped part-way. What it had written was removed.',
  },
  errors: {
    locked: 'The assistant did not accept this tablet. Unlock the tablet and ask again.',
    busy: 'The assistant is still answering the previous question. Wait for it to finish, then ask again.',
    invalid: 'The assistant could not use that question. Reword it, or start a new conversation, and ask again.',
    unavailable: 'The assistant is not available right now. The other screens still work. Try again in a minute.',
    internal: 'The assistant had a problem of its own. Try again. If it keeps happening, tell the flight surgeon.',
  } satisfies Record<ChatErrorCode, string>,
} as const;

/** Plain names for the read-only tools the assistant may use; an unknown tool is shown by its own name, spaced. */
const TOOL_NAMES: Readonly<Record<string, string>> = {
  get_current_status: 'your current status',
  get_history: 'your history',
  get_sessions: 'your sessions',
  get_self_reports: 'your self-reports',
  compare_periods: 'a comparison of two periods',
  explain_assessment: 'how the assessment was reached',
  simulate_scenario: 'a what-if simulation',
};

export function toolName(name: string): string {
  return TOOL_NAMES[name] ?? name.replaceAll('_', ' ');
}
