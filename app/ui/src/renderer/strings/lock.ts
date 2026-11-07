/** Copy of the lock screen, taken from the mock. */
export const lock = {
  gmtLine: (day: number): string => `GMT, mission day ${String(day)}`,
  title: 'Enter your crew PIN',
  noHealthData: 'No health data is shown while locked.',
  urgent: 'Urgent symptom, works while locked',
} as const;
