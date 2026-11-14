import type { CreateCrewResult, PinResult } from '../../shared/api';
import { triesLeft } from './common';

/** Copy of the login screen, taken from the mock. */
export const login = {
  mark: 'Asclepius Orbit, on-board spine monitoring',
  title: 'Who is using this tablet?',
  intro: 'Pick your name and enter your PIN. Nothing about your health is visible until you are in.',
  privacy: 'Your name stays on this tablet and the on-board computer. Data sent on the harness link and to Houston uses your crew code instead.',
  factsLabel: 'Tablet facts',
  gmt: 'GMT',
  missionDay: (day: number): string => `mission day ${String(day)}`,
  groundLink: 'Ground link',
  dataStays: 'Data stays',
  dataStaysValue: 'On this tablet and the on-board computer',
  who: 'Who are you?',
  crewLoadError: 'The crew list could not be read. Try again in a moment.',
  harness: (id: string): string => `Harness ${id}`,
  notPaired: 'not paired yet',
  newTileTitle: 'New crew member',
  newTileHint: 'Add your name and a PIN',
  newTitle: 'New crew member',
  nameLabel: 'Your name',
  namePlaceholder: 'For example, Ana García',
  codeHint: 'Shown on this tablet only. You get a crew code for the data link:',
  next: 'Continue',
  cancel: 'Cancel',
  pinFor: (name: string): string => `PIN for ${name}`,
  choosePin: (name: string): string => `Choose a PIN for ${name}`,
  choosePinHint: '4 digits. You will enter it twice.',
  repeatPin: 'Enter the PIN again',
  repeatPinHint: 'To check you typed it right.',
  pinProgress: (entered: number): string => `${String(entered)} of 4 digits entered`,
  backToName: 'Back to your name',
  urgent: 'Urgent symptom, no login needed',
  nameTooShort: 'Write your name, at least 2 letters.',
  nameTaken: (name: string): string => `${name} is already on this tablet. Pick it above.`,
  pinMismatch: 'The two PINs are different. Choose it again.',
  pinInvalid: 'That PIN cannot be used. Choose 4 digits.',
  unavailable: 'The tablet could not reach its secure store. Try again, or ask the crew medical officer.',
  unknown: 'Something went wrong. Pick your name and try again.',
} as const;

export type PinFailure = Extract<PinResult, { ok: false }>;

const LOCKED_BASE = 'Too many tries.';
const lockedAdvice = 'ask the crew medical officer to reset your PIN.';

/** "Wait 5 min or ask ..." with the wait rounded up to whole minutes (at least 1). */
export function lockedMessage(retryAfterMs: number): string {
  const min = Math.max(1, Math.ceil(retryAfterMs / 60_000));
  return `${LOCKED_BASE} Wait ${String(min)} min or ${lockedAdvice}`;
}

/** Message for a failed PIN check; shared by login and lock. */
export function pinFailureMessage(result: PinFailure): string {
  switch (result.reason) {
    case 'wrong_pin':
      return `That PIN does not match. ${triesLeft(result.attemptsLeft)}`;
    case 'locked':
      return lockedMessage(result.retryAfterMs);
    case 'unavailable':
      return login.unavailable;
    case 'unknown_crew':
      return login.unknown;
  }
}

export type CreateFailure = Extract<CreateCrewResult, { ok: false }>;

/** Message for a refused new crew member, by main's reason. */
export function createFailureMessage(result: CreateFailure, name: string): string {
  switch (result.reason) {
    case 'name_too_short':
      return login.nameTooShort;
    case 'name_taken':
      return login.nameTaken(name);
    case 'invalid_pin':
      return login.pinInvalid;
    case 'unavailable':
      return login.unavailable;
  }
}

/** Trims and collapses inner whitespace, like main does. */
export const cleanName = (raw: string): string => raw.trim().replace(/\s+/g, ' ');

/** Up to two initials, computed as plain text (never HTML). */
export function initials(name: string): string {
  return cleanName(name)
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => String.fromCodePoint(word.codePointAt(0) ?? 63).toUpperCase())
    .join('');
}
