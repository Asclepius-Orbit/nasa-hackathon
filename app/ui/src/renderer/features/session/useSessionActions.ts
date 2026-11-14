import { useState } from 'react';

import type { WriteResult } from '../../../shared/api';
import { actionText } from '../../strings/session';

export interface Notice {
  kind: 'ok' | 'error';
  text: string;
}

export interface SessionActions {
  /** An action is on its way: every action button waits. */
  busy: boolean;
  /** What the last action came to; cleared when the next one starts. */
  notice: Notice | null;
  /** Sends one action. Says `okText` only when the backend took it; `conflictText` replaces the usual conflict text. */
  run: (send: () => Promise<WriteResult<unknown>>, okText: string, conflictText?: string) => void;
}

/** One action at a time for the session screen (finish, calibrate, mark an artefact, reprocess), each with an honest outcome. */
export function useSessionActions(onDone: () => void): SessionActions {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const run = (send: () => Promise<WriteResult<unknown>>, okText: string, conflictText?: string): void => {
    setBusy(true);
    setNotice(null);
    send().then(
      (result) => {
        setBusy(false);
        if (result.ok) {
          setNotice({ kind: 'ok', text: okText });
          onDone();
        } else {
          setNotice({ kind: 'error', text: result.reason === 'conflict' && conflictText !== undefined ? conflictText : actionText.failure[result.reason] });
        }
      },
      () => {
        setBusy(false);
        setNotice({ kind: 'error', text: actionText.failure.unavailable });
      },
    );
  };
  return { busy, notice, run };
}
