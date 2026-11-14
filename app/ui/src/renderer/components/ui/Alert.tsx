import styles from './Alert.module.css';

export interface AlertProps {
  /** `urgent` red box, `warning` amber banner, `info` blue note. */
  variant?: 'urgent' | 'warning' | 'info';
  /** Bold lead line. */
  title?: React.ReactNode;
  /** Optional action, usually a Button, shown on the right. */
  action?: React.ReactNode;
  /** Use `status` for non-urgent, polite announcements. */
  role?: 'alert' | 'status';
  children?: React.ReactNode;
}

/** Announced message box (role=alert by default) with an optional bold title and action. */
export function Alert({ variant = 'urgent', title, action, role = 'alert', children }: AlertProps): React.JSX.Element {
  return (
    <div role={role} data-variant={variant} className={styles.alert}>
      <div>
        {title !== undefined && <b className={styles.title}>{title}</b>}
        {title !== undefined && children !== undefined && <br />}
        {children}
      </div>
      {action}
    </div>
  );
}
