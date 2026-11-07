import { useRef } from 'react';

import { Button } from '../../components/ui/Button';
import { cx } from '../../components/ui/cx';
import type { DayLevel, DayRecord } from '../../data/types';
import { levelLabel } from '../../strings/common';
import { dayButtonLabel, history, stripLabel } from '../../strings/history';
import styles from './History.module.css';
import { dayAxisTicks } from './scales';

export type DayFilter = 'all' | DayLevel;

export interface DayStripProps {
  days: readonly DayRecord[];
  selectedDay: number;
  todayDay: number;
  filter: DayFilter;
  onSelect: (day: number) => void;
}

const LEVEL_CLASS: Record<DayLevel, string | undefined> = {
  green: styles.green,
  amber: styles.amber,
  red: styles.red,
  nodata: styles.nodata,
};

/** The 30 day buttons with previous/next, arrow-key navigation (roving tabindex) and the day axis. */
export function DayStrip({ days, selectedDay, todayDay, filter, onSelect }: DayStripProps): React.JSX.Element {
  const buttons = useRef(new Map<number, HTMLButtonElement>());
  const index = Math.max(0, days.findIndex((d) => d.day === selectedDay));
  const move = (to: number): void => {
    const target = days[Math.min(days.length - 1, Math.max(0, to))];
    if (!target) return;
    onSelect(target.day);
    buttons.current.get(target.day)?.focus();
  };
  const onKeyDown = (event: React.KeyboardEvent): void => {
    const to: Record<string, number> = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: days.length - 1 };
    const next = to[event.key];
    if (next === undefined) return;
    event.preventDefault();
    move(next);
  };
  const first = days[0]?.day ?? 0;
  const last = days[days.length - 1]?.day ?? 0;
  return (
    <div className={styles.stripNav}>
      <Button variant="quiet" className={styles.navDay} aria-label={history.previousDay} disabled={index === 0} onClick={() => { move(index - 1); }}>
        ‹
      </Button>
      <div className={styles.daysWrap}>
        <div className={styles.days} role="group" aria-label={stripLabel(first, last)}>
          {days.map((d) => (
            <button
              key={d.day}
              type="button"
              ref={(el) => {
                if (el) buttons.current.set(d.day, el);
                else buttons.current.delete(d.day);
              }}
              className={cx(styles.day, LEVEL_CLASS[d.level])}
              aria-pressed={d.day === selectedDay}
              aria-label={dayButtonLabel(d.day, levelLabel(d.level))}
              title={`Day ${String(d.day)}: ${levelLabel(d.level)}`}
              tabIndex={d.day === selectedDay ? 0 : -1}
              data-dim={filter !== 'all' && d.level !== filter ? 'true' : undefined}
              onKeyDown={onKeyDown}
              onClick={() => { onSelect(d.day); }}
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
