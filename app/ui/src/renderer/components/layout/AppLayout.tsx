import { Outlet } from 'react-router';

import styles from './AppLayout.module.css';
import { OfflineBanner } from './OfflineBanner';
import { Rail } from './Rail';
import { Strip } from './Strip';

/** Frame for every signed-in screen: strip on top, rail on the left (tabs at the bottom when narrow), screens in `main`. Login and lock render outside it. */
export function AppLayout(): React.JSX.Element {
  return (
    <div className={styles.app}>
      <Strip />
      <OfflineBanner />
      <Rail />
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
