import type { DataSource } from '../../data/DataSource';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { useOffline } from '../../offline/OfflineContext';
import { banner } from '../../strings/common';
import { Button } from '../ui/Button';
import { AlertCircleIcon } from './icons';
import styles from './OfflineBanner.module.css';

const selectStrip = (data: DataSource): ReturnType<DataSource['getStrip']> => data.getStrip();

/** Shown while the data service cannot be reached: what happens to input, that Urgent still works, and when contact was last made. */
export function OfflineBanner(): React.JSX.Element | null {
  const { offline, retry } = useOffline();
  const { data } = useLoad(selectStrip, { refreshMs: REFRESH_MS });
  if (!offline) return null;
  return (
    <div role="status" className={styles.banner}>
      <AlertCircleIcon />
      <span>
        <b>{banner.title}</b> {banner.body}
        {data?.lastContactMinAgo != null && ` ${banner.lastContact(data.lastContactMinAgo)}`}
      </span>
      <Button variant="quiet" className={styles.retry} onClick={retry}>
        {banner.retry}
      </Button>
    </div>
  );
}
