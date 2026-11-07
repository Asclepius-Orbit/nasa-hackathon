/**
 * JSON persisted through Electron safeStorage (DPAPI on Windows). Fails closed: if encryption is unavailable,
 * nothing is read or written, and plaintext never reaches disk. A file that does not decrypt, parse or match
 * its strict schema reads as `unavailable`; it is never overwritten from here, the caller decides.
 */
import type { z } from 'zod';

import { isNotFound, nodeFileSystem, writeFileAtomic, type FileSystemLike } from './atomic-write';

/** The async subset of Electron's `safeStorage` (the sync API is deprecated in Electron 45). */
export interface SafeStorageLike {
  isAsyncEncryptionAvailable(): Promise<boolean>;
  encryptStringAsync(plainText: string): Promise<Buffer>;
  decryptStringAsync(encrypted: Buffer): Promise<{ result: string; shouldReEncrypt: boolean }>;
}

export type ReadResult<T> = { status: 'ok'; value: T } | { status: 'missing' } | { status: 'unavailable' };

export interface EncryptedJsonFile<T> {
  read(): Promise<ReadResult<T>>;
  /** Resolves false (and leaves the previous file intact) if encryption or the write fails. */
  write(value: T): Promise<boolean>;
}

export interface EncryptedJsonFileOptions<T> {
  path: string;
  schema: z.ZodType<T>;
  safeStorage: SafeStorageLike;
  fs?: FileSystemLike;
}

const UNAVAILABLE = { status: 'unavailable' } as const;

export function createEncryptedJsonFile<T>({
  path,
  schema,
  safeStorage,
  fs = nodeFileSystem,
}: EncryptedJsonFileOptions<T>): EncryptedJsonFile<T> {
  async function write(value: T): Promise<boolean> {
    try {
      if (!(await safeStorage.isAsyncEncryptionAvailable())) return false;
      const encrypted = await safeStorage.encryptStringAsync(JSON.stringify(value));
      await writeFileAtomic(fs, path, encrypted);
      return true;
    } catch {
      return false;
    }
  }

  async function read(): Promise<ReadResult<T>> {
    if (!(await safeStorage.isAsyncEncryptionAvailable())) return UNAVAILABLE;
    let raw: Buffer;
    try {
      raw = await fs.readFile(path);
    } catch (error) {
      return isNotFound(error) ? { status: 'missing' } : UNAVAILABLE;
    }
    let decrypted: { result: string; shouldReEncrypt: boolean };
    let json: unknown;
    try {
      decrypted = await safeStorage.decryptStringAsync(raw);
      json = JSON.parse(decrypted.result);
    } catch {
      return UNAVAILABLE;
    }
    const parsed = schema.safeParse(json);
    if (!parsed.success) return UNAVAILABLE;
    // Best effort: a failed re-encryption keeps the old (still decryptable) file.
    if (decrypted.shouldReEncrypt) await write(parsed.data);
    return { status: 'ok', value: parsed.data };
  }

  return { read, write };
}
