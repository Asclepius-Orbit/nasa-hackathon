import { NavLink } from 'react-router';

import { useAuth } from '../../auth/AuthProvider';
import { useOffline } from '../../offline/OfflineContext';
import { mockMenu } from '../../strings/common';
import styles from './MockScreensMenu.module.css';

const LINKS = [
  { to: '/first-day', label: mockMenu.firstDay },
  { to: '/baseline', label: mockMenu.baseline },
  { to: '/oswestry', label: mockMenu.oswestry },
  { to: '/stopped', label: mockMenu.stopped },
] as const;

/** Development-only shortcuts to screens that real navigation does not reach yet, plus the offline toggle. Renders nothing in production. */
export function MockScreensMenu(): React.JSX.Element | null {
  const { signOut, lock } = useAuth();
  const { offline, setOffline } = useOffline();
  if (!import.meta.env.DEV) return null;
  return (
    <div className={styles.states} data-testid="mock-menu">
      <p>{mockMenu.title}</p>
      {LINKS.map(({ to, label }) => (
        <NavLink key={to} to={to}>
          {label}
        </NavLink>
      ))}
      <button
        type="button"
        onClick={() => {
          void signOut();
        }}
      >
        {mockMenu.login}
      </button>
      <button
        type="button"
        onClick={() => {
          void lock();
        }}
      >
        {mockMenu.lock}
      </button>
      <button
        type="button"
        aria-pressed={offline}
        onClick={() => {
          setOffline(!offline);
        }}
      >
        {mockMenu.offline}
      </button>
    </div>
  );
}
