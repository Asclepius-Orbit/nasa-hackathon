/** Copy of the Urgent dialog (mock: "What do you have now?"). */
export const urgent = {
  title: 'What do you have now?',
  forLabel: 'For:',
  forGroup: 'Who needs help',
  intro: 'Mark what applies and send it. The crew medical officer is also alerted on board.',
  // Real mode: nothing is recorded or delivered yet (#63), so no promise of saving, queueing or alerting anyone.
  introNotSent: 'Mark what applies. This app cannot send urgent reports yet: tell the crew medical officer now, in person or by voice.',
  symptomsGroup: 'Symptoms',
  pendingTag: 'Contract change pending',
  pendingNote: '“Very severe pain” and “Something else” are not in the red-flag list of the contract yet.',
  send: 'Send',
  queued: 'Queued',
  close: 'Close',
  noLinkTitle: 'No link to the ground.',
  noLinkBody: (backInMin: number | null): string =>
    backInMin === null
      ? ' It waits in the queue and goes to the flight surgeon when the link is back.'
      : ` It waits in the queue and goes to the flight surgeon when the link is back, in about ${String(backInMin)} min.`,
  linkUpTitle: 'Link to the ground is up.',
  // Nothing is promised as sent until the delivery exists (#63): with the link up the report is still queued first.
  linkUpBody: ' Your report is queued on board first; it shows Sent only when the ground station confirms it.',
  notRecordedTitle: 'Not recorded.',
  notRecordedBody: 'This app cannot send urgent reports yet. Tell the crew medical officer now, in person or by voice.',
  queuedTitle: 'Queued.',
  queuedBody: ' Saved on board and the crew medical officer alerted. It changes to Sent when the ground station receives it.',
} as const;
