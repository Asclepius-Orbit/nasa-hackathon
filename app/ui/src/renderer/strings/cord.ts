/** Words of the cord-resistance sessions (contract codes `CORD_*`, `ESTIMATE_*`, `ASSUMPTION_*`). The estimate is always labelled as calculated. */

export const cord = {
  heading: 'Cord sessions',
  lede: 'Two independent sessions: back straight and back as curved as possible. The Earth reference is your own, taken before launch; in flight the same session is repeated.',
  loadError: 'The cord sessions could not be loaded.',
  cadence: (days: number): string => `Suggested every ${String(days)} days in flight (example cadence, not a fixed rule; pending Aguado).`,
  due: 'Due',
  upToDate: 'Up to date',
  dueReasons: {
    CORD_NO_EARTH_REFERENCE: 'No Earth reference yet. Take it before launch.',
    CORD_NO_FOLLOW_UP: 'No in-flight measurement yet.',
    CORD_FOLLOW_UP_OVERDUE: 'The last in-flight measurement is older than the cadence.',
    CORD_UP_TO_DATE: 'Nothing is due.',
  } as Readonly<Record<string, string>>,
  earthReference: 'Earth reference',
  lastFollowUp: 'Last in-flight measurement',
  none: 'None yet',
  /** `4500 ohm, 20 Jan 09:00 GMT` */
  measurement: (ohm: number, stamp: string): string => `${String(Math.round(ohm))} ohm, ${stamp} GMT`,
  history: {
    heading: 'Measurement history',
    lede: 'The latest measurements of each posture, newest first, including the ones that did not count and why. Only measurements: the calculated estimate is never stored here.',
    none: 'No measurements yet.',
    columns: { date: 'Date (GMT)', environment: 'Gravity', resistance: 'Resistance', quality: 'Quality', valid: 'Valid' },
    environments: { earth_1g: 'Earth (1 g)', microgravity: 'Microgravity', moon_0_16g: 'Moon (0.16 g)', mars_0_38g: 'Mars (0.38 g)' } as Readonly<Record<string, string>>,
    noResistance: 'none',
    yes: 'Yes',
    no: 'No',
    /** `0.90` */
    quality: (score: number): string => score.toFixed(2),
    invalidReasons: {
      CORD_INVALID_NOT_COMPLETED: 'The session did not complete.',
      CORD_INVALID_NO_VALUE: 'No resistance was obtained.',
      CORD_INVALID_LOW_QUALITY: 'The signal quality was below the minimum.',
    } as Readonly<Record<string, string>>,
  },
  estimate: {
    title: 'Calculated length estimate',
    /** Always shown with the value: what the number is not. */
    disclaimer: 'Calculated from cord measurements, not a measured length and not clinically validated. It does not change your level or any recommendation.',
    value: (lengthMm: number, elongationMm: number): string => {
      const sign = elongationMm < 0 ? '−' : '+';
      return `about ${lengthMm.toFixed(0)} mm between the back contacts (${sign}${Math.abs(elongationMm).toFixed(0)} mm vs your Earth reference)`;
    },
    assumptionsHeading: 'Assumptions',
    assumptions: {
      ASSUMPTION_UNIFORM_RESISTANCE: 'The cord resists the same along its whole length.',
      ASSUMPTION_NEGLIGIBLE_CONTACT_RESISTANCE: 'The contacts add no resistance.',
      ASSUMPTION_UNIFORM_DEFORMATION: 'The cord stretches evenly and its contacts stay fixed to it.',
    } as Readonly<Record<string, string>>,
    unknownReason: (code: string): string => `No estimate: ${code}`,
    unavailable: {
      ESTIMATE_NO_EARTH_REFERENCE: 'No estimate: there is no Earth reference yet.',
      ESTIMATE_NO_FOLLOW_UP: 'No estimate: there is no in-flight measurement yet.',
      ESTIMATE_REFERENCE_LENGTH_UNKNOWN: 'No estimate: the distance between the contacts in this posture has not been measured (pending Aguado).',
      ESTIMATE_OUT_OF_MODEL_RANGE: 'No estimate: a reading is outside the range of the bench measurements.',
      ESTIMATE_ENGINE_UNAVAILABLE: 'No estimate: the calculation is not available right now.',
    } as Readonly<Record<string, string>>,
  },
} as const;
