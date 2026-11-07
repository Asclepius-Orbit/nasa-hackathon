import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { ChipGroup } from '../../components/ui/ChipGroup';
import type { RedFlagCode, SymptomOption } from '../../data/types';
import { report } from '../../strings/report';
import { useUrgent } from '../urgent/UrgentContext';
import styles from './Form.module.css';

type Value = RedFlagCode | 'none';

export interface SymptomAnswer {
  codes: readonly RedFlagCode[];
  /** True when the person explicitly chose "None of these". */
  none: boolean;
}

export const NO_SYMPTOM_ANSWER: SymptomAnswer = { codes: [], none: false };

/** Warning-symptom chips with an explicit "None of these" and a live alert box with "Send alert". */
export function SymptomQuestion({
  options,
  answer,
  onChange,
}: {
  options: readonly SymptomOption<RedFlagCode>[];
  answer: SymptomAnswer;
  onChange: (next: SymptomAnswer) => void;
}): React.JSX.Element {
  const { open } = useUrgent();
  const chips: { value: Value; label: string }[] = [
    { value: 'none', label: report.symptoms.none },
    ...options.map((o) => ({ value: o.code, label: o.label })),
  ];
  const selected: Value[] = answer.none ? ['none'] : [...answer.codes];
  const handle = (next: Value[]): void => {
    if (next.includes('none') && !answer.none) onChange({ codes: [], none: true });
    else onChange({ codes: next.filter((v): v is RedFlagCode => v !== 'none'), none: false });
  };
  const labels = answer.codes.map((code) => options.find((o) => o.code === code)?.label ?? code);
  return (
    <div>
      <ChipGroup label={report.symptoms.question} options={chips} selected={selected} onChange={handle} tone="flag" />
      {answer.codes.length > 0 && (
        <div className={styles.alertSlot}>
          <Alert
            title={report.symptoms.marked(answer.codes.length)}
            action={
              <Button
                variant="urgent"
                onClick={() => {
                  open(answer.codes);
                }}
              >
                {report.symptoms.sendAlert}
              </Button>
            }
          >
            {labels.join('; ')}. {report.symptoms.sendNow}
          </Alert>
        </div>
      )}
    </div>
  );
}
