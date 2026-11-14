import type { GroundLink } from '../data/types';

/** Copy of the "session stopped for pain" screen. */
export const stopped = {
  title: 'Session stopped for pain',
  loading: 'Loading what happened.',
  failed: 'This screen could not be loaded.',
  /** `stopped` is whether this app stopped a session; `rep` is where, `null` when the counter did not know it. Nothing is claimed that was not recorded. */
  lede: (stopped: boolean, rep: number | null): string => {
    if (!stopped) return 'This app has not stopped a session since it was opened.';
    const where = rep === null ? 'The harness stopped recording. The rep it stopped at is not known.' : `The harness stopped recording at rep ${String(rep)}.`;
    return `${where} The session is saved with the reason “stopped for pain” and its data still counts.`;
  },
  talk: 'Talk to someone',
  link: (link: GroundLink | null): string => {
    if (link === null) return 'The state of the ground link is not known.';
    if (link.state === 'up') return 'The ground link is up.';
    return link.backInMin === null ? 'The ground link is down.' : `The ground link is down for ${String(link.backInMin)} min.`;
  },
  callOfficer: 'Call the crew medical officer',
  messageSurgeon: 'Message the flight surgeon',
  callNotLogged: 'The call could not be logged. Write it down and tell the flight surgeon.',
  contactsLabel: 'Contacts today',
  next: 'What happens next',
  nextBody: 'A new assessment runs with this stop. Do not restart the session today unless the flight surgeon clears it.',
} as const;
