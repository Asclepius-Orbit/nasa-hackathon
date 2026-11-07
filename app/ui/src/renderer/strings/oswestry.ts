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
} as const;
