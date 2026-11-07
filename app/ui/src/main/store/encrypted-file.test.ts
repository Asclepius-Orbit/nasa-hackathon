import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { z } from 'zod';

import { createEncryptedJsonFile } from './encrypted-file';
import { FakeSafeStorage, FlakyFs, makeTempDir } from './test-fakes';

const schema = z.strictObject({ version: z.literal(1), secret: z.string() });

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

function file(name = 'crew.bin') {
  return createEncryptedJsonFile({ path: join(dir, name), schema, safeStorage, fs });
}

describe('createEncryptedJsonFile', () => {
  it('round-trips a value and never writes plaintext to disk', async () => {
    const store = file();
    expect(await store.write({ version: 1, secret: 'Elena Ruiz 1234' })).toBe(true);
    expect(await store.read()).toEqual({ status: 'ok', value: { version: 1, secret: 'Elena Ruiz 1234' } });
    const raw = await readFile(join(dir, 'crew.bin'), 'utf8');
    expect(raw).not.toContain('Elena');
    expect(raw).not.toContain('1234');
    expect(raw).not.toContain('secret');
  });

  it('reports a missing file as missing', async () => {
    expect(await file().read()).toEqual({ status: 'missing' });
  });

  it('fails closed when encryption is unavailable: read is unavailable and write creates nothing', async () => {
    const store = file();
    await store.write({ version: 1, secret: 's' });
    safeStorage.available = false;
    expect(await store.read()).toEqual({ status: 'unavailable' });
    expect(await file('other.bin').write({ version: 1, secret: 'plain' })).toBe(false);
    expect(await readdir(dir)).toEqual(['crew.bin']);
  });

  it('treats a file that does not decrypt as unavailable and leaves it untouched', async () => {
    await writeFile(join(dir, 'crew.bin'), 'garbage');
    expect(await file().read()).toEqual({ status: 'unavailable' });
    expect(await readFile(join(dir, 'crew.bin'), 'utf8')).toBe('garbage');
  });

  it('treats decrypted text that is not JSON as unavailable', async () => {
    await writeFile(join(dir, 'crew.bin'), await safeStorage.encryptStringAsync('{not json'));
    expect(await file().read()).toEqual({ status: 'unavailable' });
  });

  it('treats JSON that fails the strict schema (extra key) as unavailable', async () => {
    const tampered = JSON.stringify({ version: 1, secret: 's', admin: true });
    await writeFile(join(dir, 'crew.bin'), await safeStorage.encryptStringAsync(tampered));
    expect(await file().read()).toEqual({ status: 'unavailable' });
  });

  it('treats a read error other than "not found" as unavailable', async () => {
    await mkdir(join(dir, 'crew.bin'));
    expect(await file().read()).toEqual({ status: 'unavailable' });
  });

  it('writes atomically: a failed rename keeps the old file and leaves no temp file behind', async () => {
    const store = file();
    await store.write({ version: 1, secret: 'old' });
    fs.failRename = true;
    expect(await store.write({ version: 1, secret: 'new' })).toBe(false);
    expect(await readdir(dir)).toEqual(['crew.bin']);
    fs.failRename = false;
    expect(await store.read()).toEqual({ status: 'ok', value: { version: 1, secret: 'old' } });
  });

  it('returns false when the temp write itself fails', async () => {
    fs.failWrite = true;
    expect(await file().write({ version: 1, secret: 's' })).toBe(false);
    expect(await readdir(dir)).toEqual([]);
  });

  it('returns false when encryption throws', async () => {
    const store = file();
    safeStorage.encryptStringAsync = () => Promise.reject(new Error('boom'));
    expect(await store.write({ version: 1, secret: 's' })).toBe(false);
    expect(await readdir(dir)).toEqual([]);
  });

  it('re-encrypts in place when safeStorage says the key rotated', async () => {
    const store = file();
    await store.write({ version: 1, secret: 's' });
    const before = await readFile(join(dir, 'crew.bin'));
    safeStorage.shouldReEncrypt = true;
    expect(await store.read()).toEqual({ status: 'ok', value: { version: 1, secret: 's' } });
    const after = await readFile(join(dir, 'crew.bin'));
    expect(after.equals(before)).toBe(false);
    safeStorage.shouldReEncrypt = false;
    expect(await store.read()).toEqual({ status: 'ok', value: { version: 1, secret: 's' } });
  });
});
