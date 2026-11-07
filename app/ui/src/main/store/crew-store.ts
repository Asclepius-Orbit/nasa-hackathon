/**
 * Crew members and their PIN hashes, in an encrypted file. Renderer-facing results are DTOs without hashes.
 * `null` from `list`/`nextId` means the store is unavailable (encryption off, file corrupt) or, for `nextId`,
 * that no `AST-NN` code is left.
 */
import { z } from 'zod';

import type { CreateCrewResult, CrewMember } from '../../shared/api';
import { CREW_ID_PATTERN, PIN_PATTERN } from '../../shared/ipc-schema';
import type { PinHasher } from '../auth/pin';
import type { EncryptedJsonFile } from './encrypted-file';
import { createSerial } from './serial';

const crewRecordSchema = z.strictObject({
  id: z.string().regex(CREW_ID_PATTERN),
  name: z.string(),
  harnessId: z.string().nullable(),
  pinHash: z.string(),
});

export const crewFileSchema = z.strictObject({ version: z.literal(1), members: z.array(crewRecordSchema) });

export type CrewRecord = z.infer<typeof crewRecordSchema>;
export type CrewFile = z.infer<typeof crewFileSchema>;

export type Credentials = { status: 'ok'; pinHash: string } | { status: 'unknown' } | { status: 'unavailable' };

export interface CrewStore {
  list(): Promise<CrewMember[] | null>;
  nextId(): Promise<string | null>;
  create(input: { name: string; pin: string }): Promise<CreateCrewResult>;
  findCredentials(crewId: string): Promise<Credentials>;
}

export interface CrewStoreOptions {
  file: EncryptedJsonFile<CrewFile>;
  hasher: PinHasher;
  /** Dev/e2e only (`!app.isPackaged`): seed the two mock crew members when the store is empty. */
  seed: boolean;
}

const SEED_PIN = '1234';
const SEED_MEMBERS = [
  { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
  { id: 'AST-02', name: 'Marc Dubois', harnessId: 'wb-02' },
] as const;
const MAX_CREW_NUMBER = 99;
const MIN_NAME_CHARS = 2;

const toMember = ({ id, name, harnessId }: CrewRecord): CrewMember => ({ id, name, harnessId });

const graphemes = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
const graphemeCount = (text: string): number => [...graphemes.segment(text)].length;

export function normalizeName(name: string): string {
  return name.normalize('NFC').trim().replace(/\s+/g, ' ');
}

function nextIdOf(members: readonly CrewRecord[]): string | null {
  const highest = Math.max(0, ...members.map((m) => Number(m.id.slice('AST-'.length))));
  const next = highest + 1;
  return next > MAX_CREW_NUMBER ? null : `AST-${String(next).padStart(2, '0')}`;
}

export function createCrewStore({ file, hasher, seed }: CrewStoreOptions): CrewStore {
  const serial = createSerial();
  let members: CrewRecord[] | null = null;

  async function persist(next: CrewRecord[]): Promise<boolean> {
    if (!(await file.write({ version: 1, members: next }))) return false;
    members = next;
    return true;
  }

  /** Loads once; an unavailable store is retried on the next call and is never written over. */
  async function load(): Promise<CrewRecord[] | null> {
    if (members !== null) return members;
    const read = await file.read();
    if (read.status === 'unavailable') return null;
    const stored = read.status === 'ok' ? read.value.members : [];
    if (stored.length > 0 || !seed) {
      members = stored;
      return members;
    }
    // Each seed gets its own salt.
    const seeded = await Promise.all(SEED_MEMBERS.map(async (m) => ({ ...m, pinHash: await hasher.hash(SEED_PIN) })));
    return (await persist(seeded)) ? members : null;
  }

  return {
    list: () => serial(async () => (await load())?.map(toMember) ?? null),

    nextId: () =>
      serial(async () => {
        const current = await load();
        return current === null ? null : nextIdOf(current);
      }),

    create: ({ name, pin }) =>
      serial(async (): Promise<CreateCrewResult> => {
        const current = await load();
        if (current === null) return { ok: false, reason: 'unavailable' };
        const normalized = normalizeName(name);
        if (graphemeCount(normalized) < MIN_NAME_CHARS) return { ok: false, reason: 'name_too_short' };
        const key = normalized.toLowerCase();
        if (current.some((m) => m.name.toLowerCase() === key)) return { ok: false, reason: 'name_taken' };
        if (!PIN_PATTERN.test(pin)) return { ok: false, reason: 'invalid_pin' };
        const id = nextIdOf(current);
        if (id === null) return { ok: false, reason: 'unavailable' };
        const record: CrewRecord = { id, name: normalized, harnessId: null, pinHash: await hasher.hash(pin) };
        if (!(await persist([...current, record]))) return { ok: false, reason: 'unavailable' };
        return { ok: true, member: toMember(record) };
      }),

    findCredentials: (crewId) =>
      serial(async (): Promise<Credentials> => {
        const current = await load();
        if (current === null) return { status: 'unavailable' };
        const record = current.find((m) => m.id === crewId);
        return record === undefined ? { status: 'unknown' } : { status: 'ok', pinHash: record.pinHash };
      }),
  };
}
