/** Copy of the weekly Oswestry questionnaire. */
export const oswestry = {
  title: 'Weekly Oswestry questionnaire',
  lede: 'How much your back limits you this week. Pick the sentence closest to you.',
  pending: 'Item set pending validation by Aguado',
  loading: 'Loading the questionnaire.',
  failed: 'The questionnaire could not be loaded.',
  option: (n: number, text: string): string => `${String(n)}. ${text}`,
  save: 'Save questionnaire',
  left: (left: number, total: number): string => (left > 0 ? `${String(left)} of ${String(total)} still to answer.` : 'Ready to save.'),
  saved: 'Questionnaire saved.',
  saving: 'Saving',
  failure: {
    unavailable: 'The monitor did not answer. Nothing was saved; your answers are still here, try again.',
    locked: 'The app is locked. Unlock it, then save again.',
    conflict: 'The monitor already holds a different questionnaire for this week. Nothing was saved; reload the screen.',
    invalid: 'The monitor refused the questionnaire. Nothing was saved; your answers are still here.',
  },
} as const;
