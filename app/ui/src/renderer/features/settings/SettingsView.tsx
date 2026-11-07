import { useState } from 'react';

import type { Settings } from '../../../shared/api';
import pick from '../../components/ui/Pick.module.css';
import { cx } from '../../components/ui/cx';
import { useSettings } from '../../settings/SettingsProvider';
import { settingsText } from '../../strings/settings';
import styles from './Settings.module.css';

interface Choice<Value> {
  value: Value;
  label: string;
}

interface RowProps<Value extends string | boolean> {
  label: string;
  note?: string;
  choices: readonly [Choice<Value>, Choice<Value>] | readonly Choice<Value>[];
  current: Value;
  onChoose: (value: Value) => void;
}

/** One setting: a label and single-choice buttons (`aria-pressed`) in a named group. */
function SettingRow<Value extends string | boolean>({ label, note, choices, current, onChoose }: RowProps<Value>): React.JSX.Element {
  return (
    <div className={styles.row}>
      <span id={`set-${label}`}>
        {label}
        {note !== undefined && <small className={styles.note}>{note}</small>}
      </span>
      <div className={styles.picks} role="group" aria-label={label}>
        {choices.map((choice) => (
          <button
            key={String(choice.value)}
            type="button"
            className={cx(pick.pick, styles.pick)}
            aria-pressed={choice.value === current}
            onClick={() => {
              onChoose(choice.value);
            }}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Settings: each choice is saved at once through the bridge; the screen then shows what main stored. */
export function SettingsView(): React.JSX.Element {
  const { settings, update } = useSettings();
  const [failed, setFailed] = useState(false);

  const choose =
    <K extends keyof Settings>(key: K) =>
    (value: Settings[K]): void => {
      setFailed(false);
      update({ [key]: value }).catch(() => {
        setFailed(true);
      });
    };

  const t = settingsText;
  return (
    <section aria-labelledby="settings-title" data-testid="settings-view">
      <h1 id="settings-title">{t.title}</h1>
      <div className={styles.settings}>
        <SettingRow
          label={t.length}
          choices={[
            { value: 'mm', label: t.mm },
            { value: 'in', label: t.inches },
          ]}
          current={settings.lengthUnit}
          onChoose={choose('lengthUnit')}
        />
        <SettingRow
          label={t.time}
          choices={[
            { value: 'gmt', label: t.gmt },
            { value: 'gmt_met', label: t.gmtMet },
          ]}
          current={settings.timeFormat}
          onChoose={choose('timeFormat')}
        />
        <SettingRow
          label={t.textSize}
          choices={[
            { value: 'standard', label: t.standard },
            { value: 'large', label: t.large },
          ]}
          current={settings.textSize}
          onChoose={choose('textSize')}
        />
        <SettingRow
          label={t.voice}
          note={t.voiceNote}
          choices={[
            { value: true, label: t.on },
            { value: false, label: t.off },
          ]}
          current={settings.voiceCommands}
          onChoose={choose('voiceCommands')}
        />
        <SettingRow
          label={t.footPedal}
          choices={[
            { value: true, label: t.paired },
            { value: false, label: t.notPaired },
          ]}
          current={settings.footPedal}
          onChoose={choose('footPedal')}
        />
      </div>
      <p className={styles.error} role="alert">
        {failed ? t.saveError : ''}
      </p>
    </section>
  );
}
