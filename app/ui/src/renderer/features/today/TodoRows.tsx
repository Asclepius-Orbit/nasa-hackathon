import { useNavigate } from 'react-router';

import { Button } from '../../components/ui/Button';
import type { TodoItem } from '../../data/types';
import styles from './TodoRows.module.css';

/** "To do" rows: title, detail and one action. An action without a screen yet stays visible but disabled. */
export function TodoRows({ items }: { items: readonly TodoItem[] }): React.JSX.Element {
  const navigate = useNavigate();
  return (
    <ul className={styles.todo}>
      {items.map((item) => {
        const { route, label, kind } = item.action;
        return (
          <li key={item.id} className={styles.row}>
            <p>
              {item.title}
              <small>{item.detail}</small>
            </p>
            <Button
              variant={kind}
              disabled={route === null}
              onClick={route === null ? undefined : () => void navigate(route)}
            >
              {label}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
