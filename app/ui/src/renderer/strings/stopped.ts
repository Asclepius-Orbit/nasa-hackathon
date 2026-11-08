import type { GroundLink } from '../data/types';

/** Copy of the "session stopped for pain" screen. */
export const stopped = {
  title: 'Session stopped for pain',
  loading: 'Loading what happened.',
  failed: 'This screen could not be loaded.',
  lede: (rep: number): string =>
    `The harness stopped recording at rep ${String(rep)}. The session is saved with the reason “stopped for pain” and its data still counts.`,
  painQuestion: 'How much does it hurt now?',
  painLow: '0, no pain',
  painHigh: '10, worst imaginable',
  talk: 'Talk to someone',
  link: (link: GroundLink, officerOnBoard: boolean): string => {
    const ground =
      link.state === 'up'
        ? 'The ground link is up.'
        : link.backInMin === null
          ? 'The ground link is down.'
          : `The ground link is down for ${String(link.backInMin)} min.`;
    return officerOnBoard ? `${ground} The crew medical officer is on board.` : ground;
  },
  callOfficer: 'Call the crew medical officer',
  messageSurgeon: 'Message the flight surgeon',
  callNotLogged: 'The call could not be logged. Write it down and tell the flight surgeon.',
  contactsLabel: 'Contacts today',
  next: 'What happens next',
  nextBody:
    'A new assessment runs with this stop and your answers. Do not restart the session today unless the flight surgeon clears it.',
  save: 'Save and run the assessment',
  needed: 'Pain now is needed to save.',
  ready: 'Ready. Saving runs a new assessment.',
  saved: 'Saved. A new assessment is running.',
} as const;
