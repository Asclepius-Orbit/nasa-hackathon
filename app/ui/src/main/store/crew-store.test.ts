import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { createPinHasher } from '../auth/pin';
import { createCrewStore, crewFileSchema, type CrewFile } from './crew-store';
import { createEncryptedJsonFile } from './encrypted-file';
import { FakeSafeStorage, FlakyFs, makeTempDir } from './test-fakes';

const hasher = createPinHasher({ N: 2 ** 10, r: 8, p: 1 });

let dir: string;
let cleanup: () => Promise<void>;
let safeStorage: FakeSafeStorage;
let fs: FlakyFs;

beforeEach(async () => {
  ({ dir, cleanup } = await makeTempDir());
  safeStorage = new FakeSafeStorage();
  fs = new FlakyFs();
});
afterEach(async () => {
  await cleanup();
});

const crewPath = (): string => join(dir, 'crew.bin');
const crewFile = () => createEncryptedJsonFile({ path: crewPath(), schema: crewFileSchema, safeStorage, fs });
const store = (seed = false) => createCrewStore({ file: crewFile(), hasher, seed });

async function writeMembers(ids: string[]): Promise<void> {
  const pinHash = await hasher.hash('1111');
  const data: CrewFile = {
    version: 1,
    members: ids.map((id, i) => ({ id, name: `Person ${String(i)}`, harnessId: null, pinHash })),
  };
  expect(await crewFile().write(data)).toBe(true);
}

describe('seeding', () => {
  it('starts empty without the seed flag (packaged builds) and writes nothing until a member is created', async () => {
    const crew = store(false);
    expect(await crew.list()).toEqual([]);
    expect(await crew.nextId()).toBe('AST-01');
    expect(await crewFile().read()).toEqual({ status: 'missing' });
  });

  it('seeds Elena Ruiz and Marc Dubois with hashed PIN 1234 when the flag is set and the store is empty', async () => {
    const crew = store(true);
    expect(await crew.list()).toEqual([
      { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
      { id: 'AST-02', name: 'Marc Dubois', harnessId: 'wb-02' },
    ]);
    const credentials = await crew.findCredentials('AST-01');
    expect(credentials.status).toBe('ok');
    if (credentials.status !== 'ok') return;
    expect(credentials.pinHash.startsWith('scrypt$v1$')).toBe(true);
    expect(await hasher.verify('1234', credentials.pinHash)).toBe(true);
    expect(await store(false).list()).toHaveLength(2); // persisted
  });

  it('does not seed over existing members', async () => {
    await writeMembers(['AST-07']);
    expect((await store(true).list())?.map((m) => m.id)).toEqual(['AST-07']);
  });

  it('is unavailable if the seed cannot be written, and retries on the next call', async () => {
    fs.failWrite = true;
    const crew = store(true);
    expect(await crew.list()).toBeNull();
    fs.failWrite = false;
    expect(await crew.list()).toHaveLength(2);
  });
});

describe('list', () => {
  it('returns DTOs without PIN hashes', async () => {
    const [member] = (await store(true).list()) ?? [];
    expect(Object.keys(member ?? {}).sort()).toEqual(['harnessId', 'id', 'name']);
  });
});

describe('nextId', () => {
  it('is max existing number + 1, zero-padded', async () => {
    await writeMembers(['AST-01', 'AST-05']);
    expect(await store().nextId()).toBe('AST-06');
  });

  it('is null when AST-99 is taken', async () => {
    await writeMembers(['AST-99']);
    expect(await store().nextId()).toBeNull();
  });
});

describe('create', () => {
  it('stores a new member with the next id, no harness, and returns the DTO', async () => {
    const crew = store(true);
    const result = await crew.create({ name: 'Ana García', pin: '4321' });
    expect(result).toEqual({ ok: true, member: { id: 'AST-03', name: 'Ana García', harnessId: null } });
    const reopened = store(true);
    expect((await reopened.list())?.map((m) => m.name)).toEqual(['Elena Ruiz', 'Marc Dubois', 'Ana García']);
    const credentials = await reopened.findCredentials('AST-03');
    expect(credentials.status === 'ok' && (await hasher.verify('4321', credentials.pinHash))).toBe(true);
  });

  it('trims and collapses whitespace in the name', async () => {
    const result = await store().create({ name: '  Ana \t  García  ', pin: '4321' });
    expect(result.ok && result.member.name).toBe('Ana García');
  });

  it.each(['', ' ', 'A', '  a  '])('rejects %j as name_too_short', async (name) => {
    expect(await store().create({ name, pin: '4321' })).toEqual({ ok: false, reason: 'name_too_short' });
  });

  it('counts letters, not UTF-16 units, for the 2-character minimum', async () => {
    expect(await store().create({ name: '😀', pin: '4321' })).toEqual({ ok: false, reason: 'name_too_short' });
  });

  it.each(['elena ruiz', 'ELENA  RUIZ', ' Elena Ruiz '])('rejects %j as name_taken (case-insensitive)', async (name) => {
    expect(await store(true).create({ name, pin: '4321' })).toEqual({ ok: false, reason: 'name_taken' });
  });

  it('treats canonically equivalent Unicode spellings as the same name (NFC)', async () => {
    const crew = store();
    await crew.create({ name: 'José Luis', pin: '4321' });
    expect(await crew.create({ name: 'José Luis', pin: '4321' })).toEqual({ ok: false, reason: 'name_taken' });
  });

  it.each(['123', '12345', 'abcd', '١٢٣٤'])('rejects PIN %j as invalid_pin', async (pin) => {
    expect(await store().create({ name: 'Ana García', pin })).toEqual({ ok: false, reason: 'invalid_pin' });
  });

  it('never persists the PIN or its plaintext name to disk', async () => {
    await store().create({ name: 'Ana García', pin: '4321' });
    const raw = await readFile(crewPath(), 'utf8');
    expect(raw).not.toContain('4321');
    expect(raw).not.toContain('Ana');
  });

  it('gives concurrent creates distinct ids', async () => {
    const crew = store();
    const results = await Promise.all([
      crew.create({ name: 'Ana', pin: '1111' }),
      crew.create({ name: 'Bob', pin: '2222' }),
      crew.create({ name: 'Cleo', pin: '3333' }),
    ]);
    expect(results.map((r) => r.ok && r.member.id)).toEqual(['AST-01', 'AST-02', 'AST-03']);
  });

  it('is unavailable when no id is left', async () => {
    await writeMembers(['AST-99']);
    expect(await store().create({ name: 'Ana', pin: '1111' })).toEqual({ ok: false, reason: 'unavailable' });
  });

  it('is unavailable and keeps nothing in memory when the write fails', async () => {
    const crew = store();
    fs.failRename = true;
    expect(await crew.create({ name: 'Ana', pin: '1111' })).toEqual({ ok: false, reason: 'unavailable' });
    fs.failRename = false;
    expect(await crew.list()).toEqual([]);
    expect(await crew.nextId()).toBe('AST-01');
  });
});

describe('fail closed', () => {
  it('reports everything as unavailable when encryption is unavailable', async () => {
    safeStorage.available = false;
    const crew = store(true);
    expect(await crew.list()).toBeNull();
    expect(await crew.nextId()).toBeNull();
    expect(await crew.create({ name: 'Ana', pin: '1111' })).toEqual({ ok: false, reason: 'unavailable' });
    expect(await crew.findCredentials('AST-01')).toEqual({ status: 'unavailable' });
  });

  it('does not overwrite (or re-seed) a corrupt crew file', async () => {
    await writeFile(crewPath(), 'corrupt');
    const crew = store(true);
    expect(await crew.list()).toBeNull();
    expect(await crew.create({ name: 'Ana', pin: '1111' })).toEqual({ ok: false, reason: 'unavailable' });
    expect(await readFile(crewPath(), 'utf8')).toBe('corrupt');
  });
});

describe('findCredentials', () => {
  it('reports an unknown id', async () => {
    expect(await store(true).findCredentials('AST-42')).toEqual({ status: 'unknown' });
  });
});
