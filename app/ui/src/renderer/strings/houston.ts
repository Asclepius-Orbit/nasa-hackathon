import type { OutboxItem } from '../data/types';

/** `Day 87`, or `L-30` (launch minus 30 days) for a preflight day, which the contract gives as a negative mission day. */
const missionDayLabel = (missionDay: number): string => (missionDay < 0 ? `L${String(missionDay)}` : `Day ${String(missionDay)}`);

/** Transports that really reach the ground. None exists yet (the contract only has `simulated`): the simulated notice shows for every other value. */
export const REAL_TRANSPORTS: readonly string[] = [];

/** Copy of the Houston screen. Words for codes live in strings/codes; this file has the screen's own text. */
export const houston = {
  title: 'What goes to Houston',
  lede: (crewCode: string): string =>
    `A daily summary for the flight surgeon. It uses your crew code, ${crewCode}, never your name. You can read it before it leaves; you cannot edit it.`,
  // Visible notices, not only a colour: the content is provisional and the link is not real.
  pendingValidation: 'Pending validation by Aguado',
  simulatedLink: 'Simulated ground link: nothing leaves this computer',
  summaryTitle: (missionDay: number | null, date: string): string => (missionDay === null ? date : `${missionDayLabel(missionDay)} summary`),
  noSummary: 'No summary yet',
  noSummaryBody: 'Nothing has been prepared for the ground yet.',
  crew: 'Crew',
  crewNote: '(crew code; your name is not sent)',
  level: 'Level',
  confidence: (pct: number | null, available: number | null, total: number | null): string =>
    pct === null || available === null || total === null
      ? 'no confidence figure'
      : `confidence ${String(pct)} %, data ${String(available)} of ${String(total)}`,
  drivers: 'Drivers',
  warning: 'Warning symptoms',
  sessions: 'Sessions',
  escalation: 'Escalation',
  contacts: 'Contacts',
  plan: 'Plan',
  none: 'None',
  noneReported: 'None reported',
  notAssessed: 'Not assessed',
  notAvailable: 'Not available yet',
  // `urgency` and `reasons` are already words (strings/codes); this only joins them.
  escalationRequired: (urgency: string | null, reasons: readonly string[]): string =>
    `Required${urgency === null ? '' : `, urgency ${urgency}`}${reasons.length === 0 ? '' : ` (${reasons.join('; ')})`}`,
  noEscalation: 'Not required',
  outbox: 'Outbox',
  outboxEmpty: 'Nothing in the outbox yet.',
  queued: 'Queued',
  sent: 'Sent',
  outboxLabel: (missionDay: number | null, date: string, revision: number): string => {
    const base = missionDay === null ? date : `${missionDayLabel(missionDay)} summary`;
    return revision > 1 ? `${base}, revision ${String(revision)}` : base;
  },
  received: (gmtTime: string): string => `received ${gmtTime} GMT`,
  lastContact: (stamp: string): string => `Last contact ${stamp} GMT.`,
  // Never "up" by default: an unknown link says that nothing is sent.
  groundUnknown: 'Ground link: not known. Nothing is sent.',
  groundUp: 'Ground link up.',
  groundDown: (backInMin: number | null): string =>
    backInMin === null ? 'Ground link down.' : `Ground link down, back in ${String(backInMin)} min.`,
  loadError: 'The Houston summary could not be loaded.',
} as const;

export const outboxState = (item: Pick<OutboxItem, 'state'>): string => (item.state === 'queued' ? houston.queued : houston.sent);
