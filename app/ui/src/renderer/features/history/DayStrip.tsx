import { useRef } from 'react';

import { Button } from '../../components/ui/Button';
import { cx } from '../../components/ui/cx';
import type { DayLevel, DayRecord } from '../../data/types';
import { levelLabel } from '../../strings/common';
import { dayButtonLabel, dayText, history, stripLabel } from '../../strings/history';
import styles from './History.module.css';
import { dayAxisTicks } from './scales';

export type DayFilter = 'all' | DayLevel;

export interface DayStripProps {
  days: readonly DayRecord[];
  /** Date of the selected day. */
  selectedDay: string;
  todayDay: number | null;
  filter: DayFilter;
  onSelect: (date: string) => void;
}

const LEVEL_CLASS: Record<DayLevel, string | undefined> = {
  green: styles.green,
  amber: styles.amber,
  red: styles.red,
  nodata: styles.nodata,
};

/** The 30 day buttons with previous/next, arrow-key navigation (roving tabindex) and the day axis. */
export function DayStrip({ days, selectedDay, todayDay, filter, onSelect }: DayStripProps): React.JSX.Element {
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const index = Math.max(0, days.findIndex((d) => d.date === selectedDay));
  const move = (to: number): void => {
    const target = days[Math.min(days.length - 1, Math.max(0, to))];
    if (!target) return;
    onSelect(target.date);
    buttons.current.get(target.date)?.focus();
  };
  const onKeyDown = (event: React.KeyboardEvent): void => {
    const to: Record<string, number> = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: days.length - 1 };
    const next = to[event.key];
    if (next === undefined) return;
    event.preventDefault();
    move(next);
  };
  const first = days[0];
  const last = days[days.length - 1];
  return (
    <div className={styles.stripNav}>
      <Button variant="quiet" className={styles.navDay} aria-label={history.previousDay} disabled={index === 0} onClick={() => { move(index - 1); }}>
        ‹
      </Button>
      <div className={styles.daysWrap}>
        <div className={styles.days} role="group" aria-label={first && last ? stripLabel(first, last) : history.emptyWindow}>
          {days.map((d) => (
            <button
              key={d.date}
              type="button"
              ref={(el) => {
                if (el) buttons.current.set(d.date, el);
                else buttons.current.delete(d.date);
              }}
              className={cx(styles.day, LEVEL_CLASS[d.level])}
              aria-pressed={d.date === selectedDay}
              aria-label={dayButtonLabel(d, levelLabel(d.level))}
              title={`${dayText(d)}: ${levelLabel(d.level)}`}
              tabIndex={d.date === selectedDay ? 0 : -1}
              data-dim={filter !== 'all' && d.level !== filter ? 'true' : undefined}
              onKeyDown={onKeyDown}
              onClick={() => { onSelect(d.date); }}
            />
          ))}
        </div>
        <div className={styles.daysAxis} aria-hidden="true">
          {dayAxisTicks(days, todayDay).map((t) => (
            <span key={t.label} className={cx(styles.tick, t.isToday && styles.tickToday)} style={t.isToday ? undefined : { left: `${String(t.percent)}%` }}>
              {t.label}
            </span>
          ))}
        </div>
      </div>
      <Button variant="quiet" className={styles.navDay} aria-label={history.nextDay} disabled={index === days.length - 1} onClick={() => { move(index + 1); }}>
        ›
      </Button>
    </div>
  );
}
