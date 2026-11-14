/**
 * PIN hashing with scrypt. Encoding: `scrypt$v1$N$r$p$<salt b64>$<key b64>`, so parameters can be raised
 * later without breaking stored hashes. Comparison is constant-time on equal-length buffers.
 */
import { randomBytes as nodeRandomBytes, scrypt, timingSafeEqual } from 'node:crypto';

export interface ScryptParams {
  N: number;
  r: number;
  p: number;
}

export interface PinHasher {
  hash(pin: string): Promise<string>;
  verify(pin: string, encoded: string): Promise<boolean>;
  /** Spends the same work as `verify` and resolves false; evens out timing when there is nothing to compare. */
  dummyVerify(pin: string): Promise<false>;
}

export type RandomBytes = (size: number) => Buffer;

export const DEFAULT_SCRYPT_PARAMS: Readonly<ScryptParams> = Object.freeze({ N: 2 ** 15, r: 8, p: 1 });

const SALT_BYTES = 16;
const KEY_BYTES = 32;
const PREFIX = 'scrypt';
const VERSION = 'v1';
// Bounds for parameters read back from storage: an absurd N would stall the main process.
const N_MIN = 2 ** 10;
const N_MAX = 2 ** 20;
const R_MAX = 16;
const P_MAX = 4;

function derive(pin: string, salt: Buffer, { N, r, p }: ScryptParams): Promise<Buffer> {
  // scrypt needs ~128*N*r bytes; leave headroom over Node's 32 MiB default.
  const maxmem = 256 * N * r;
  return new Promise((resolve, reject) => {
    scrypt(pin, salt, KEY_BYTES, { N, r, p, maxmem }, (error, key) => {
      if (error) reject(error);
      else resolve(key);
    });
  });
}

function parseInteger(text: string, min: number, max: number): number | null {
  if (!/^\d{1,8}$/.test(text)) return null;
  const value = Number(text);
  return value >= min && value <= max ? value : null;
}

function decodeBase64(text: string, bytes: number): Buffer | null {
  const buffer = Buffer.from(text, 'base64');
  // Round-trip check rejects non-canonical input that Buffer.from would silently skip.
  return buffer.length === bytes && buffer.toString('base64') === text ? buffer : null;
}

interface Decoded {
  params: ScryptParams;
  salt: Buffer;
  key: Buffer;
}

function decode(encoded: string): Decoded | null {
  const parts = encoded.split('$');
  if (parts.length !== 7) return null;
  const [prefix, version, nText, rText, pText, saltText, keyText] = parts as [
    string,
    string,
    string,
    string,
    string,
    string,
    string,
  ];
  if (prefix !== PREFIX || version !== VERSION) return null;
  const N = parseInteger(nText, N_MIN, N_MAX);
  const r = parseInteger(rText, 1, R_MAX);
  const p = parseInteger(pText, 1, P_MAX);
  if (N === null || r === null || p === null || (N & (N - 1)) !== 0) return null;
  const salt = decodeBase64(saltText, SALT_BYTES);
  const key = decodeBase64(keyText, KEY_BYTES);
  if (salt === null || key === null) return null;
  return { params: { N, r, p }, salt, key };
}

export function createPinHasher(
  params: ScryptParams = DEFAULT_SCRYPT_PARAMS,
  randomBytes: RandomBytes = nodeRandomBytes,
): PinHasher {
  return {
    async hash(pin) {
      const salt = randomBytes(SALT_BYTES);
      const key = await derive(pin, salt, params);
      return [PREFIX, VERSION, params.N, params.r, params.p, salt.toString('base64'), key.toString('base64')].join('$');
    },

    async verify(pin, encoded) {
      const decoded = decode(encoded);
      if (decoded === null) return false;
      const key = await derive(pin, decoded.salt, decoded.params);
      return timingSafeEqual(key, decoded.key);
    },

    async dummyVerify(pin) {
      await derive(pin, randomBytes(SALT_BYTES), params);
      return false;
    },
  };
}
