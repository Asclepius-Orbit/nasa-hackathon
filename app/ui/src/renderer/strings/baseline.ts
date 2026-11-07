/** Copy of the preflight reference screen. */
export const baseline = {
  title: 'Your preflight reference',
  lede: 'Everything in flight is compared with how your spine behaves on Earth. The reference is built from sessions in 1 g, before launch, with the same protocol.',
  progressLabel: 'Preflight sessions recorded',
  sessionsLabel: 'Preflight sessions',
  reference: 'Reference',
  keepNote: 'After each preflight session the reference is rebuilt; earlier versions are kept so every assessment says which one it used.',
  loading: 'Loading the preflight reference.',
  error: 'The preflight reference could not be loaded.',
} as const;

export const recordedHeading = (recorded: number, required: number): string => `${String(recorded)} of ${String(required)} sessions recorded`;

export const sessionLine = (session: { protocol: string; status: 'complete' | 'planned'; when: string }): string =>
  session.status === 'complete' ? `${session.when}, ${session.protocol}, complete` : `Planned for ${session.when}`;

export const referenceLine = (version: number, built: number): string =>
  `Version ${String(version)}, built from ${String(built)} ${built === 1 ? 'session' : 'sessions'}. Assessments say “low confidence” until the third.`;
