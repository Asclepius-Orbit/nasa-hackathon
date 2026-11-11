import { useState } from 'react';

import type { WriteResult } from '../../../shared/api';
import { NO_ERRORS, sortFieldErrors, type FormErrors } from '../profile/fields';
import { toKnownPointers } from './report-fields';

export interface FormSaveState {
  status: 'idle' | 'saving' | 'saved';
  /** Refusals by the question that holds them, plus the ones that matched no question. */
  errors: FormErrors;
  /** Why nothing was stored when no question is to blame. */
  failure: string | null;
}

const IDLE: FormSaveState = { status: 'idle', errors: NO_ERRORS, failure: null };

/**
 * One form's save, as in the profile: "saved" only after `ok: true`; a refusal keeps the entries and says why (by question
 * when its pointer is one of `known`, otherwise in the general list). `failures` is the text of each refusal without fields.
 */
export function useFormSave(
  known: readonly string[],
  failures: Readonly<Record<'unavailable' | 'locked' | 'conflict' | 'invalid', string>>,
): { state: FormSaveState; run: (send: () => Promise<WriteResult<unknown>>) => void } {
  const [state, setState] = useState<FormSaveState>(IDLE);
  const run = (send: () => Promise<WriteResult<unknown>>): void => {
    setState({ ...IDLE, status: 'saving' });
    send().then(
      (result) => {
        if (result.ok) setState({ ...IDLE, status: 'saved' });
        else if (result.reason === 'invalid') setState({ ...IDLE, errors: sortFieldErrors(toKnownPointers(result.fields, known), known), failure: result.fields.length === 0 ? failures.invalid : null });
        else setState({ ...IDLE, failure: failures[result.reason] });
      },
      () => {
        setState({ ...IDLE, failure: failures.unavailable });
      },
    );
  };
  return { state, run };
}
