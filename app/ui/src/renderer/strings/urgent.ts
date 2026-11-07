/** Copy of the Urgent dialog (mock: "What do you have now?"). */
export const urgent = {
  title: 'What do you have now?',
  forLabel: 'For:',
  forGroup: 'Who needs help',
  intro: 'Mark what applies and send it. The crew medical officer is also alerted on board.',
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
  linkUpBody: ' It goes to the flight surgeon as soon as you send it.',
  queuedTitle: 'Queued.',
  queuedBody: ' Saved on board and the crew medical officer alerted. It changes to Sent when the ground station receives it.',
} as const;
