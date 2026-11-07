/** Copy of the daily report (also reused by the after-stop screen for the symptom question). */
export const report = {
  title: 'Daily report',
  lede: 'About 1 minute. Only pain, stiffness and warning symptoms need an answer. Marking a warning symptom opens the alert straight away.',
  loading: 'Loading the report form.',
  failed: 'The report form could not be loaded.',
  pain: {
    question: 'How much does your back hurt right now?',
    low: '0, no pain',
    high: '10, worst imaginable',
    whereHint: 'Where does it hurt? Optional, tap all that apply.',
    whereGroup: 'Where it hurts',
    cell: (region: string, side: string): string => `${region}, ${side.toLowerCase()}`,
  },
  stiffness: {
    question: 'How stiff does your back feel?',
    low: '0, not stiff',
    high: '10, can barely move',
  },
  symptoms: {
    question: 'Do you have any of these now?',
    none: 'None of these',
    marked: (n: number): string => (n === 1 ? '1 warning symptom marked.' : `${String(n)} warning symptoms marked.`),
    sendNow: 'Send the alert now; you do not need to finish first.',
    sendAlert: 'Send alert',
  },
  sleepHours: { question: 'How long did you sleep?', optional: 'Optional' },
  sleepQuality: { question: 'How well did you sleep?', low: '0, terrible', high: '10, perfect' },
  exercise: {
    question: 'Exercise today',
    optional: "Optional. Saved with today's context.",
    device: 'Exercise device',
    duration: 'Exercise duration',
  },
  save: 'Save daily report',
  left: {
    pain: 'pain',
    stiffness: 'stiffness',
    symptoms: 'warning symptoms',
    list: (parts: readonly string[]): string => `Still to answer: ${parts.join(', ')}.`,
    ready: 'Ready. Saving runs a new assessment.',
  },
  saved: 'Daily report saved. A new assessment is running.',
} as const;
