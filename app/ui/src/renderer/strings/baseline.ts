import { protocolShort } from './codes';

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

/** Days relative to launch: -30 is `L-30`, 0 is launch day. */
export const launchOffset = (days: number): string => (days === 0 ? 'Launch day' : days < 0 ? `L${String(days)}` : `L+${String(days)}`);

export const sessionLine = (session: { protocolId: string; status: 'complete' | 'planned'; offsetDays: number; date: string | null }): string => {
  const when = launchOffset(session.offsetDays);
  const date = session.date === null ? '' : ` (${session.date})`;
  return session.status === 'complete'
    ? `${when}${date}, ${protocolShort(session.protocolId)}, complete`
    : `Planned for ${when}${date}`;
};

export const sessionName = (index: number): string => `Session ${String(index + 1)}`;

export const referenceLine = (version: number | null, built: number): string =>
  version === null
    ? 'No reference built yet. Assessments say “low confidence” until it exists.'
    : `Version ${String(version)}, built from ${String(built)} ${built === 1 ? 'session' : 'sessions'}. Assessments say “low confidence” until the third.`;
