/** Copy of the session screen (mock: SESSION). Session data (title, load, steps) comes from the DataSource. */

export const session = {
  loadError: 'The session could not be loaded.',
  loading: 'Loading the session.',
  lede: 'Keep your hands on the band. The harness counts the reps; you do not need to touch the screen.',
  band: (color: string, kg: number): string => `Band ${color}, ${String(kg)} kg nominal`,
  stretched: (cm: number): string => `Stretched ${String(cm)} cm at start`,
  reps: (n: number): string => `${String(n)} reps`,
  late: (started: string, minutes: number, planned: string): string =>
    `Started ${started} GMT, ${String(minutes)} min before the planned ${planned}.`,
  stopPain: 'Stop: pain',
  stopPainHint: 'or say “stop, pain”',
  stepsLabel: 'Protocol steps',
  stepsHint: 'Tap a step to see it.',
  pending: 'Placement and procedure pending Aguado',
  back: 'Back',
  next: 'Next step',
} as const;

export const voiceUndo = {
  heard: 'Heard “stop, pain”.',
  countdown: (s: number): string => `Stopping in ${String(s)} s.`,
  keepGoing: 'Keep going, it was not me',
  stopNow: 'Stop now',
  mockTrigger: 'Mock: hear “stop, pain”',
} as const;

export const stages = {
  fit: {
    title: '1. Fit electrodes, IMU and cord',
    lede: 'Ask a crewmate to fit and check the back: you cannot see it. Tick each one when it is on.',
    done: (gmt: string): string => `Fitted at ${gmt} GMT and checked by a crewmate.`,
  },
  check: {
    title: '2. Check the harness is sending',
    lede: 'Stand still for 10 s. Each sensor must send a clean signal before you start.',
    good: 'Good',
    weak: 'Weak',
    again: 'Check again',
    without: 'Continue without right EMG',
    hint: "Continuing without it leaves the right-side muscle values out of today's assessment, and says so.",
    done: (gmt: string): string => `Continued without right EMG at ${gmt} GMT.`,
  },
  mvc: {
    title: '3. Calibrate maximum effort',
    lede: 'Pull the band down as hard as you can for 5 s, three times, with 1 min of rest between pulls. This sets the 100 % of your muscle signal for today.',
    pull: (n: number): string => `Pull ${String(n)}`,
    recorded: 'Recorded by the harness',
    done: (gmt: string, note: string): string => `Calibration saved at ${gmt} GMT. ${note}`,
  },
  reps: {
    of: (total: number): string => `of ${String(total)}`,
    pulling: 'Pulling down',
    lettingUp: 'Letting up',
    countingBy: 'Counting by',
    lastRep: 'Last rep',
    backup: 'Backup',
    noLast: 'No rep counted yet',
    last: (rep: number, seconds: number): string => `Rep ${String(rep)}, ${seconds.toFixed(1)} s, recorded by the harness`,
    artefact: 'Mark an artefact',
    recount: 'Recount this rep',
    progress: 'Movement progress',
  },
  finish: {
    title: '5. Rest and finish',
    lede: 'Let the band go slack and stand still for 2 min. The stretch cord needs time to settle before its last reading.',
    resting: 'Resting',
    finish: 'Finish session',
    hint: 'Finish is available when the rest ends. Then the session is saved and a new assessment runs.',
  },
} as const;
