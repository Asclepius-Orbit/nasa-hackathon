/** Copy shared by every screen (strip, navigation, banner). Feature copy lives in one file per feature next to this one. */

export type DisplayLevel = 'green' | 'amber' | 'red' | 'nodata';

export const appName = 'Asclepius Orbit';

export const strip = {
  missionDay: 'Mission day',
  gmt: 'GMT',
  phase: 'Phase',
  groundLink: 'Ground link',
  harness: (id: string): string => `Harness ${id}`,
  harnessNone: 'Harness',
} as const;

export const urgentButton = 'Urgent symptom';

export const nav = {
  label: 'Sections',
  today: 'Today',
  report: 'Report',
  session: 'Session',
  history: 'History',
  settings: 'Settings',
} as const;

export const mockMenu = {
  title: 'Mock screens',
  firstDay: 'First day',
  baseline: 'Preflight reference',
  oswestry: 'Weekly Oswestry',
  stopped: 'After Stop: pain',
  houston: 'Report to Houston',
  login: 'Login',
  lock: 'Lock screen',
  offline: 'App offline',
} as const;

export const banner = {
  title: 'The app cannot reach its data service.',
  body: 'What you enter is kept on this tablet and saved when it reconnects. Urgent symptom still works.',
  lastContact: (min: number): string => `Last contact ${String(min)} min ago.`,
  retry: 'Try again',
} as const;

export const units = { mm: 'mm', in: 'inches' } as const;

export const levels: Record<DisplayLevel, string> = {
  green: 'Green',
  amber: 'Amber',
  red: 'Red',
  nodata: 'No data',
};

export const notPairedYet = 'Not paired yet';

export const levelLabel = (level: DisplayLevel): string => levels[level];
export const minutes = (n: number): string => `${String(n)} min`;
export const triesLeft = (n: number): string => `${String(n)} ${n === 1 ? 'try' : 'tries'} left.`;
export const dueLabel = (n: number): string => `${String(n)} due`;

export function groundLinkValue(link: { state: 'up' } | { state: 'down'; backInMin: number | null }): string {
  if (link.state === 'up') return 'Up';
  return link.backInMin === null ? 'Down' : `Down, back in ${minutes(link.backInMin)}`;
}

export function harnessValue(harness: { batteryPct: number; note: string | null } | null): string {
  if (harness === null) return notPairedYet;
  return harness.note === null ? `${String(harness.batteryPct)} %` : `${String(harness.batteryPct)} %, ${harness.note}`;
}
