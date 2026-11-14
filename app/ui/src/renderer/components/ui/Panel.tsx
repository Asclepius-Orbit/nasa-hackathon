import { cx } from './cx';
import styles from './Panel.module.css';

export type PanelProps = React.ComponentPropsWithoutRef<'div'> & {
  /** Element to render; use `section` with an aria-label for a landmark, `figure` for charts. */
  as?: 'div' | 'section' | 'figure';
};

/** Card with the surface background, a line border and the large radius. */
export function Panel({ as: Tag = 'div', className, ...rest }: PanelProps): React.JSX.Element {
  return <Tag {...rest} className={cx(styles.panel, className)} />;
}

/** Hatched "no data" chip: absence is shown explicitly, never as a blank or a green. */
export function NoData({ children = 'No data' }: { children?: React.ReactNode }): React.JSX.Element {
  return <span className={styles.nodata}>{children}</span>;
}
