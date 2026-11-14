import { useEffect, useEffectEvent, useState } from 'react';

import { useClock } from '../../clock/Clock';
import { Button } from '../../components/ui/Button';
import { voiceUndo } from '../../strings/session';
import styles from './SessionView.module.css';

/** How long a heard "stop, pain" can be undone before it takes effect. */
export const UNDO_SECONDS = 5;

export interface VoiceUndoState {
  /** Whole seconds left, or `null` when no voice stop is pending. */
  remaining: number | null;
  start: () => void;
  cancel: () => void;
}

/** Countdown driven by the injected clock: when it reaches zero `onExpire` runs once. */
export function useVoiceUndo(onExpire: () => void): VoiceUndoState {
  const clock = useClock();
  const [deadline, setDeadline] = useState<number | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const expire = useEffectEvent(onExpire);

  useEffect(() => {
    if (deadline === null) return undefined;
    return clock.subscribe((now) => {
      const left = Math.ceil((deadline - now.getTime()) / 1000);
      if (left > 0) {
        setRemaining(left);
        return;
      }
      setDeadline(null);
      setRemaining(null);
      expire();
    });
  }, [clock, deadline]);

  return {
    remaining,
    start: () => {
      setDeadline(clock.now().getTime() + UNDO_SECONDS * 1000);
      setRemaining(UNDO_SECONDS);
    },
    cancel: () => {
      setDeadline(null);
      setRemaining(null);
    },
  };
}

export interface VoiceUndoAlertProps {
  remaining: number;
  onKeepGoing: () => void;
  onStopNow: () => void;
}

/** Urgent box shown while a heard "stop, pain" can still be undone. */
export function VoiceUndoAlert({ remaining, onKeepGoing, onStopNow }: VoiceUndoAlertProps): React.JSX.Element {
  return (
    <div role="alert" className={styles.voiceUndo}>
      <b>{voiceUndo.heard}</b>
      <span>{voiceUndo.countdown(remaining)}</span>
      <Button variant="quiet" className={styles.pushRight} onClick={onKeepGoing}>
        {voiceUndo.keepGoing}
      </Button>
      <Button variant="urgent" onClick={onStopNow}>
        {voiceUndo.stopNow}
      </Button>
    </div>
  );
}
