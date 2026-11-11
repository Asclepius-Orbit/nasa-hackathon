import { useState } from 'react';

import { LoadFailure } from '../../components/layout/NoMonitor';
import pick from '../../components/ui/Pick.module.css';
import { useData } from '../../data/DataContext';
import type { DataSource } from '../../data/DataSource';
import type { OdiItem } from '../../data/types';
import { useLoad } from '../../data/useLoad';
import { oswestry as copy } from '../../strings/oswestry';
import form from '../report/Form.module.css';
import { SaveBar } from '../report/SaveBar';
import { useFormSave } from '../report/useFormSave';
import styles from './OswestryView.module.css';

const selectOswestry = (data: DataSource): ReturnType<DataSource['getOswestry']> => data.getOswestry();

function Item({ item, value, onChange }: { item: OdiItem; value: number | undefined; onChange: (n: number) => void }): React.JSX.Element {
  return (
    <div role="radiogroup" aria-label={item.title} className={styles.item}>
      <p className={form.legend}>{item.title}</p>
      <div className={styles.opts}>
        {item.options.map((text, n) => (
          <label key={text} className={styles.opt}>
            <input
              type="radio"
              name={item.id}
              checked={value === n}
              onChange={() => {
                onChange(n);
              }}
              className={pick.visuallyHidden}
            />
            <span className={pick.pick}>{copy.option(n, text)}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

export function OswestryView(): React.JSX.Element {
  const { data, error } = useLoad(selectOswestry);
  const [answers, setAnswers] = useState<Readonly<Record<string, number>>>({});
  const source = useData();
  const save = useFormSave([], copy.failure);
  const items = data?.items ?? [];
  const left = items.filter((item) => answers[item.id] === undefined).length;
  return (
    <section aria-labelledby="oswestry-title" data-testid="oswestry-view">
      <h1 id="oswestry-title">{copy.title}</h1>
      <p className={form.lede}>
        {copy.lede} <span className={styles.pending}>{copy.pending}</span>
      </p>
      {error !== null && <LoadFailure error={error} fallback={copy.failed} />}
      {data === null && error === null && <p role="status">{copy.loading}</p>}
      {data !== null && (
        <>
          <div>
            {items.map((item) => (
              <Item
                key={item.id}
                item={item}
                value={answers[item.id]}
                onChange={(n) => {
                  setAnswers({ ...answers, [item.id]: n });
                }}
              />
            ))}
          </div>
          <SaveBar
            label={copy.save}
            canSave={left === 0}
            note={copy.left(left, items.length)}
            busy={save.state.status === 'saving'}
            busyLabel={copy.saving}
            problem={{ failure: save.state.failure, errors: save.state.errors, fixMarked: '' }}
            onSave={() => {
              const answered = items.flatMap((item) => (answers[item.id] === undefined ? [] : [{ item_id: item.id, score: answers[item.id] as number }]));
              save.run(() => source.saveOswestry(answered));
            }}
            savedMessage={save.state.status === 'saved' ? copy.saved : null}
          />
        </>
      )}
    </section>
  );
}
