import { describe, expect, it } from 'vitest';

import { createPinHasher, DEFAULT_SCRYPT_PARAMS } from './pin';

// Cheap parameters keep the suite fast; the encoded string carries them, so verify() honours whatever was stored.
const FAST = { N: 2 ** 10, r: 8, p: 1 };
const fixedSalt = (size: number): Buffer => Buffer.alloc(size, 7);

describe('createPinHasher', () => {
  it('defaults to scrypt N=2^15, r=8, p=1', () => {
    expect(DEFAULT_SCRYPT_PARAMS).toEqual({ N: 32768, r: 8, p: 1 });
  });

  it('encodes as scrypt$v1$N$r$p$salt$hash with a 16-byte random salt and 32-byte key', async () => {
    const hasher = createPinHasher(FAST, fixedSalt);
    const encoded = await hasher.hash('1234');
    const parts = encoded.split('$');
    expect(parts.slice(0, 5)).toEqual(['scrypt', 'v1', '1024', '8', '1']);
    expect(parts[5]).toBe(Buffer.alloc(16, 7).toString('base64'));
    expect(Buffer.from(parts[6] ?? '', 'base64')).toHaveLength(32);
  });

  it('salts every hash: the same PIN hashes differently with real randomness', async () => {
    const hasher = createPinHasher(FAST);
    expect(await hasher.hash('1234')).not.toBe(await hasher.hash('1234'));
  });

  it('verifies the right PIN and rejects a wrong one', async () => {
    const hasher = createPinHasher(FAST);
    const encoded = await hasher.hash('1234');
    expect(await hasher.verify('1234', encoded)).toBe(true);
    expect(await hasher.verify('1235', encoded)).toBe(false);
    expect(await hasher.verify('', encoded)).toBe(false);
  });

  it('hashes and verifies with the default (production) parameters', async () => {
    const hasher = createPinHasher();
    const encoded = await hasher.hash('9876');
    expect(encoded.startsWith('scrypt$v1$32768$8$1$')).toBe(true);
    expect(await hasher.verify('9876', encoded)).toBe(true);
  });

  it('verifies a hash made with other in-bounds parameters (they are read from the encoding)', async () => {
    const encoded = await createPinHasher({ N: 2 ** 11, r: 4, p: 2 }).hash('1234');
    expect(await createPinHasher(FAST).verify('1234', encoded)).toBe(true);
  });

  describe('rejects malformed or out-of-bounds encodings without throwing', () => {
    const salt = Buffer.alloc(16, 1).toString('base64');
    const key = Buffer.alloc(32, 2).toString('base64');
    it.each([
      ['empty', ''],
      ['wrong algorithm', `bcrypt$v1$1024$8$1$${salt}$${key}`],
      ['wrong version', `scrypt$v2$1024$8$1$${salt}$${key}`],
      ['too few parts', `scrypt$v1$1024$8$1$${salt}`],
      ['too many parts', `scrypt$v1$1024$8$1$${salt}$${key}$x`],
      ['non-numeric N', `scrypt$v1$abc$8$1$${salt}$${key}`],
      ['N not a power of two', `scrypt$v1$1536$8$1$${salt}$${key}`],
      ['N too small', `scrypt$v1$512$8$1$${salt}$${key}`],
      ['N too large (DoS)', `scrypt$v1$${String(2 ** 21)}$8$1$${salt}$${key}`],
      ['r out of range', `scrypt$v1$1024$0$1$${salt}$${key}`],
      ['p out of range', `scrypt$v1$1024$8$9$${salt}$${key}`],
      ['short salt', `scrypt$v1$1024$8$1$${Buffer.alloc(8).toString('base64')}$${key}`],
      ['short key', `scrypt$v1$1024$8$1$${salt}$${Buffer.alloc(16).toString('base64')}`],
      ['non-canonical base64', `scrypt$v1$1024$8$1$${salt}!$${key}`],
    ])('%s', async (_label, encoded) => {
      expect(await createPinHasher(FAST).verify('1234', encoded)).toBe(false);
    });
  });

  it('rejects an otherwise valid hash whose version tag is unknown', async () => {
    const hasher = createPinHasher(FAST);
    const encoded = await hasher.hash('1234');
    expect(await hasher.verify('1234', encoded.replace('$v1$', '$v2$'))).toBe(false);
  });

  it('dummyVerify always resolves false (used to even out timing for unknown crew)', async () => {
    expect(await createPinHasher(FAST).dummyVerify('1234')).toBe(false);
  });
});
