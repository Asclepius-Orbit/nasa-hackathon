import { useNavigate } from 'react-router';

import { Button } from '../../components/ui/Button';
import type { TaskCode } from '../../data/types';
import { taskText } from '../../strings/codes';
import styles from './TodoRows.module.css';

/** "To do" rows from task codes: title, detail and one action. An action without a screen yet stays visible but disabled. */
export function TodoRows({ items }: { items: readonly TaskCode[] }): React.JSX.Element {
  const navigate = useNavigate();
  return (
    <ul className={styles.todo}>
      {items.map((code) => {
        const { title, detail, action } = taskText(code);
        return (
          <li key={code} className={styles.row}>
            <p>
              {title}
              {detail !== '' && <small>{detail}</small>}
            </p>
            {action !== null && (
              <Button
                variant={action.kind}
                disabled={action.route === null}
                onClick={action.route === null ? undefined : () => void navigate(action.route ?? '')}
              >
                {action.label}
              </Button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
