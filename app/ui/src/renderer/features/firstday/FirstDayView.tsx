import { NoData, Panel } from '../../components/ui/Panel';
import type { DataSource } from '../../data/DataSource';
import { REFRESH_MS, useLoad } from '../../data/useLoad';
import { LoadFailure } from '../../components/layout/NoMonitor';
import { firstDay } from '../../strings/firstday';
import { TodoRows } from '../today/TodoRows';
import styles from './FirstDayView.module.css';

const selectFirstDay = (d: DataSource): ReturnType<DataSource['getFirstDay']> => d.getFirstDay();

/** Nothing measured yet: an explicit "not assessed" state and the tasks before the first assessment. */
export function FirstDayView(): React.JSX.Element {
  const { data, error } = useLoad(selectFirstDay, { refreshMs: REFRESH_MS });
  return (
    <section aria-labelledby="firstday-title" data-testid="firstday-view">
      <h1 id="firstday-title">{data === null ? firstDay.loading : firstDay.welcome(data.crewFirstName)}</h1>
      {error !== null && <LoadFailure error={error} fallback={firstDay.error} />}
      {data !== null && (
        <>
          <p className={styles.lede}>{firstDay.lede}</p>
          <Panel className={styles.panel}>
            <p className={styles.chip}>
              <NoData>{firstDay.notAssessed}</NoData>
            </p>
            <h2>{firstDay.tasksHeading}</h2>
            <TodoRows items={data.todo} />
          </Panel>
        </>
      )}
    </section>
  );
}
