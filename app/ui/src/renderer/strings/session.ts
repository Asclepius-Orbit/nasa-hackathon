/** Copy of the session screen (mock: SESSION). Session data (title, load, steps) comes from the DataSource. */

export const session = {
  loadError: 'The session could not be loaded.',
  loading: 'Loading the session.',
  lede: 'Keep your hands on the band. The harness counts the reps; you do not need to touch the screen.',
  band: (color: string, kg: number): string => `Band ${color}, ${String(kg)} kg nominal`,
  stretched: (cm: number): string => `Stretched ${String(cm)} cm at start`,
  reps: (n: number): string => `${String(n)} reps`,
  title: 'Session',
  none: 'No session is in progress.',
  noneHint: 'When one starts it appears here, with its steps.',
  started: (started: string): string => `Started ${started} GMT.`,
  late: (started: string, minutes: number, planned: string): string =>
    `Started ${started} GMT, ${String(minutes)} min before the planned ${planned}.`,
  stopPain: 'Stop: pain',
  stopPainHint: 'or say “stop, pain”',
  stepsLabel: 'Protocol steps',
  stepLabels: {
    fit: 'Fit electrodes, IMU and cord',
    check: 'Check the harness is sending',
    mvc: 'Calibrate maximum effort',
    reps: 'Do the reps',
    finish: 'Rest and finish',
  },
  countingBy: 'IMU at L3, automatic',
  backup: 'Say “mark rep” or press the foot pedal',
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
  },
  check: {
    title: '2. Check the harness is sending',
    lede: 'Stand still for 10 s. Each sensor must send a clean signal before you start.',
    good: 'Good',
    weak: 'Weak',
    again: 'Check again',
    without: 'Continue without right EMG',
    hint: "Continuing without it leaves the right-side muscle values out of today's assessment, and says so.",
    none: 'No sensor check has been reported yet.',
  },
  mvc: {
    title: '3. Calibrate maximum effort',
    lede: 'Pull the band down as hard as you can for 5 s, three times, with 1 min of rest between pulls. This sets the 100 % of your muscle signal for today.',
    notSent: 'No calibration command was sent yet.',
    waiting: 'Calibration sent. Waiting for the harness to confirm.',
    saved: (gmt: string): string => `Calibration saved at ${gmt} GMT.`,
  },
  reps: {
    of: (total: number): string => `of ${String(total)}`,
    pulling: 'Pulling down',
    lettingUp: 'Letting up',
    countingBy: 'Counting by',
    lastRep: 'Last rep',
    backup: 'Backup',
    noLast: 'No rep counted yet',
    noSignal: 'No live rep count yet.',
    noReps: 'This protocol has no repetitions. Stand still while the cord reads.',
    last: (rep: number, seconds: number): string => `Rep ${String(rep)}, ${seconds.toFixed(1)} s, recorded by the harness`,
    artefact: 'Mark an artefact',
    recount: 'Recount this rep',
    progress: 'Movement progress',
  },
  finish: {
    title: '5. Rest and finish',
    lede: 'Let the band go slack and stand still for 2 min. The stretch cord needs time to settle before its last reading.',
    resting: 'Resting',
    noRest: 'This protocol has no rest period.',
    finish: 'Finish session',
    hint: 'Finish is available when the rest ends. Then the session is saved and a new assessment runs.',
  },
} as const;
