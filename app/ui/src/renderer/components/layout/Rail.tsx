import { NavLink } from 'react-router';

import type { DataSource } from '../../data/DataSource';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { dueLabel, nav } from '../../strings/common';
import { AskIcon, HistoryIcon, ReportIcon, SessionIcon, SettingsIcon, TodayIcon } from './icons';
import { MockScreensMenu } from './MockScreensMenu';
import styles from './Rail.module.css';

const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

const TABS = [
  { to: '/today', label: nav.today, Icon: TodayIcon },
  { to: '/report', label: nav.report, Icon: ReportIcon },
  { to: '/session', label: nav.session, Icon: SessionIcon },
  { to: '/history', label: nav.history, Icon: HistoryIcon },
  { to: '/chat', label: nav.ask, Icon: AskIcon },
  { to: '/settings', label: nav.settings, Icon: SettingsIcon },
] as const;

/** Left rail (bottom tab bar under 900 px) with the six tabs; the Report tab carries the "due" badge. */
export function Rail(): React.JSX.Element {
  const { data } = useLoad(selectStrip, { refreshMs: REFRESH_MS });
  const due = data?.reportsDue ?? 0;
  return (
    <nav aria-label={nav.label} className={styles.rail}>
      {TABS.map(({ to, label, Icon }) => (
        <NavLink key={to} to={to} className={styles.tab ?? ''}>
          <Icon />
          <span>{label}</span>
          {to === '/report' && due > 0 && (
            <span className={styles.badge} aria-label={dueLabel(due)}>
              {due}
            </span>
          )}
        </NavLink>
      ))}
      <MockScreensMenu />
    </nav>
  );
}
