/** Copy of the first-day screen (nothing measured yet). */
export const firstDay = {
  welcome: (name: string): string => `Welcome, ${name}`,
  lede: 'Nothing has been measured yet, so there is no assessment. Nothing here means “all fine”: it means “not known yet”.',
  notAssessed: 'Not assessed yet',
  tasksHeading: 'Three things before the first assessment',
  loading: 'Loading.',
  error: 'The first-day checklist could not be loaded.',
} as const;
