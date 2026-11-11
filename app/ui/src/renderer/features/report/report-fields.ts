import type { DailyReportFields } from '../../../shared/api';
import type { ReportFormData } from '../../data/types';
import type { SymptomAnswer } from './SymptomQuestion';

/** What the daily report form holds. Choices of the optional rows are indexes into `ReportFormData`, as `PickRow` gives them. */
export interface DailyReportAnswers {
  pain: number;
  stiffness: number;
  /** `"region:side"` keys of the cells marked, in the order they were marked. */
  where: ReadonlySet<string>;
  symptoms: SymptomAnswer;
  sleepHours: number | null;
  sleepQuality: number | null;
  device: number | null;
  minutes: number | null;
}

const MODALITY: Readonly<Record<string, 'ared' | 'cevis' | 't2'>> = { ARED: 'ared', CEVIS: 'cevis', T2: 't2' };

/** The exercise to send, or `null` for "none yet" and for no answer; a device without a duration is not sent (the form asks for it first). */
function exerciseOf(form: ReportFormData, a: DailyReportAnswers): DailyReportFields['exercise'] {
  const kind = a.device === null ? undefined : form.exerciseKinds[a.device]?.value;
  const modality = kind === undefined ? undefined : MODALITY[kind];
  const duration = a.minutes === null ? undefined : form.exerciseMinutes[a.minutes]?.value;
  return modality === undefined || duration === undefined ? null : { modality, duration_min: duration };
}

/** The form's answers as the bridge takes them: region and side from the cell keys, hours from the chosen option (`null` = not sure). */
export function buildDailyReport(form: ReportFormData, a: DailyReportAnswers): DailyReportFields {
  const hours = a.sleepHours === null ? undefined : form.sleepHours[a.sleepHours]?.value;
  return {
    pain_nrs: a.pain,
    stiffness_nrs: a.stiffness,
    pain_locations:
      a.pain === 0
        ? []
        : [...a.where].map((key) => {
            const [region = '', side = ''] = key.split(':');
            return { region, side };
          }),
    sleep_hours: hours ?? null,
    sleep_quality_nrs: a.sleepQuality,
    red_flags: a.symptoms.none ? [] : [...a.symptoms.codes],
    exercise: exerciseOf(form, a),
  };
}

/** A device is chosen (not "None yet") but no duration: the daily report would drop the exercise silently, so it asks first. */
export function exerciseNeedsMinutes(form: ReportFormData, a: Pick<DailyReportAnswers, 'device' | 'minutes'>): boolean {
  const kind = a.device === null ? undefined : form.exerciseKinds[a.device]?.value;
  return kind !== undefined && kind in MODALITY && a.minutes === null;
}

/** Body pointers the backend can refuse, each shown by the question that holds it. */
export const REPORT_POINTERS = ['/pain_nrs', '/stiffness_nrs', '/pain_locations', '/sleep/hours', '/sleep/quality_nrs', '/red_flags'] as const;

/** Maps a refusal pointing inside a known field (`/pain_locations/0/region`) to that field; others stay as they are. */
export function toKnownPointers(
  fields: readonly { pointer: string; message: string }[],
  known: readonly string[],
): { pointer: string; message: string }[] {
  return fields.map(({ pointer, message }) => ({ pointer: known.find((k) => pointer === k || pointer.startsWith(`${k}/`)) ?? pointer, message }));
}
