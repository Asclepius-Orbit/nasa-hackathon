import { useEffect, useRef } from 'react';

import { useUrgent } from './UrgentContext';
import styles from './UrgentDialog.module.css';

/** PLACEHOLDER: the real Urgent dialog replaces the inside of this file. Keep it free of any session requirement. */
export function UrgentDialog(): React.JSX.Element | null {
  const { isOpen, close } = useUrgent();
  const returnTo = useRef<Element | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    returnTo.current = document.activeElement;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (returnTo.current instanceof HTMLElement) returnTo.current.focus();
    };
  }, [isOpen, close]);

  if (!isOpen) return null;
  return (
    <div className={styles.backdrop}>
      <div role="dialog" aria-modal="true" aria-labelledby="urgent-title" className={styles.dialog} data-testid="urgent-dialog">
        <h2 id="urgent-title">What do you have now?</h2>
        <button ref={closeRef} type="button" className={styles.close} onClick={close}>
          Close
        </button>
      </div>
    </div>
  );
}
