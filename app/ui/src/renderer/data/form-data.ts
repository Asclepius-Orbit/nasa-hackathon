import { redFlagLabels } from '../strings/codes';
import type { ReportFormData, SymptomCatalog } from './types';

/**
 * Static data of the report forms: the choices the UI offers, not health data of the crew. Both data sources serve it
 * (the mock and the real one), since no backend holds it.
 */

/** ONE catalogue of warning symptoms, most serious first (contract red_flag enum). Pending clinical validation. */
const RED_FLAG_ORDER = [
  'saddle_numbness',
  'bladder_or_bowel_dysfunction',
  'leg_weakness',
  'radiating_leg_pain',
  'numbness_or_tingling',
  'severe_night_pain',
  'fever_with_back_pain',
  'trauma_event',
] as const;

export const CATALOG: SymptomCatalog = {
  flags: RED_FLAG_ORDER.map((code) => ({ code, label: redFlagLabels[code] })),
  escapes: [
    { code: 'very_severe_pain', label: 'Very severe pain' },
    { code: 'other', label: 'Something else' },
  ],
};

export const REPORT_FORM: ReportFormData = {
  catalog: CATALOG,
  painRegions: [
    { value: 'cervical', label: 'Neck' },
    { value: 'thoracic', label: 'Upper back' },
    { value: 'lumbar', label: 'Lower back' },
    { value: 'sacral', label: 'Tailbone' },
  ],
  painSides: [
    { value: 'left', label: 'Left' },
    { value: 'midline', label: 'Middle' },
    { value: 'right', label: 'Right' },
    { value: 'bilateral', label: 'Both sides' },
  ],
  sleepHours: [
    { value: 3, label: 'Under 4 h' },
    { value: 4, label: '4 h' },
    { value: 5, label: '5 h' },
    { value: 6, label: '6 h' },
    { value: 7, label: '7 h' },
    { value: 8, label: '8 h' },
    { value: 9, label: '9 h or more' },
    { value: null, label: 'Not sure' },
  ],
  exerciseKinds: [
    { value: 'ARED', label: 'ARED' },
    { value: 'CEVIS', label: 'CEVIS' },
    { value: 'T2', label: 'T2 treadmill' },
    { value: 'none', label: 'None yet' },
  ],
  exerciseMinutes: [
    { value: 15, label: '15 min' },
    { value: 30, label: '30 min' },
    { value: 45, label: '45 min' },
    { value: 60, label: '60 min' },
    { value: 90, label: '90 min or more' },
  ],
};
