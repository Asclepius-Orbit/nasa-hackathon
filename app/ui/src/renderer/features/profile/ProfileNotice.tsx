import { useNavigate } from 'react-router';

import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import type { DataSource } from '../../data/DataSource';
import { useLoad } from '../../data/useLoad';
import { profileNotice } from '../../strings/profile';
import styles from './Profile.module.css';

type Missing = 'profile' | 'context' | null;

/** What the assessments still lack: the profile first, then today's context. Either read failing is not a gap (the screen has its own error handling). */
const selectMissing = async (d: DataSource): Promise<{ missing: Missing }> => {
  if ((await d.getProfile()) === null) return { missing: 'profile' };
  return { missing: (await d.getTodayContext()) === null ? 'context' : null };
};

/** Banner for Today and First day: points to the Profile screen when the profile or today's context is not stored. Renders nothing otherwise, while loading and on any failure. */
export function ProfileNotice(): React.JSX.Element | null {
  const navigate = useNavigate();
  const { data } = useLoad(selectMissing);
  if (data?.missing == null) return null;
  const text = profileNotice[data.missing === 'profile' ? 'noProfile' : 'noContext'];
  return (
    <div className={styles.notice}>
      <Alert
        variant="warning"
        role="status"
        title={text.title}
        action={
          <Button
            variant="quiet"
            onClick={() => {
              void navigate('/profile');
            }}
          >
            {text.action}
          </Button>
        }
      />
    </div>
  );
}
