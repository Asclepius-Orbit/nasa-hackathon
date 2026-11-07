import type { OutboxItem } from '../data/types';

/** Copy of the Houston screen, taken from the mock. */
export const houston = {
  title: 'What goes to Houston',
  lede: (crewCode: string): string =>
    `A daily summary for the flight surgeon. It uses your crew code, ${crewCode}, never your name. You can read it before it leaves; you cannot edit it.`,
  summaryTitle: (day: number): string => `Day ${String(day)} summary`,
  crew: 'Crew',
  crewNote: '(crew code; your name is not sent)',
  level: 'Level',
  confidence: (pct: number, available: number, total: number): string => `confidence ${String(pct)} %, data ${String(available)} of ${String(total)}`,
  drivers: 'Drivers',
  warning: 'Warning symptoms',
  sessions: 'Sessions',
  contacts: 'Contacts',
  plan: 'Plan',
  outbox: 'Outbox',
  queued: 'Queued',
  sent: (receivedGmt: string | null): string => (receivedGmt === null ? 'Sent' : `Sent, received ${receivedGmt}`),
  queueNote: 'Queued items go out in order.',
  groundUp: 'Ground link up.',
  groundDown: (backInMin: number | null): string => (backInMin === null ? 'Ground link down.' : `Ground link down, back in ${String(backInMin)} min.`),
  loadError: 'The Houston summary could not be loaded.',
} as const;

export const outboxState = (item: OutboxItem): string => (item.state === 'queued' ? houston.queued : houston.sent(item.receivedGmt));
