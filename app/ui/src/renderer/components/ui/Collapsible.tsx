import styles from './Collapsible.module.css';

export interface CollapsibleProps {
  summary: React.ReactNode;
  /** Short decorative mark before the summary, e.g. `?` or `!`. */
  icon?: React.ReactNode;
  /** `urgent` paints it in the alert red. */
  tone?: 'default' | 'urgent';
  defaultOpen?: boolean;
  children: React.ReactNode;
}

/** Native details/summary toggle with a 64 px summary; keyboard and screen readers work for free. */
export function Collapsible({ summary, icon, tone = 'default', defaultOpen = false, children }: CollapsibleProps): React.JSX.Element {
  return (
    <details className={styles.toggle} data-tone={tone} open={defaultOpen}>
      <summary>
        {icon !== undefined && (
          <span className={styles.ico} aria-hidden="true">
            {icon}
          </span>
        )}
        {summary}
      </summary>
      <div className={styles.body}>{children}</div>
    </details>
  );
}
