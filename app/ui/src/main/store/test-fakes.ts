/** Hand-written fakes for the store boundaries (Electron safeStorage, filesystem, clock). Test-only. */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { SafeStorageLike } from './encrypted-file';
import type { FileSystemLike } from './atomic-write';
import * as nodeFs from 'node:fs/promises';

const MAGIC = 'FAKE-ENC:';

/** Reversible but not plaintext: base64 of the reversed UTF-8 bytes, behind a magic prefix. */
export class FakeSafeStorage implements SafeStorageLike {
  available = true;
  shouldReEncrypt = false;
  encryptCalls = 0;

  isAsyncEncryptionAvailable(): Promise<boolean> {
    return Promise.resolve(this.available);
  }

  encryptStringAsync(plainText: string): Promise<Buffer> {
    this.encryptCalls += 1;
    // Like a weak backend (Linux basic_text): still "encrypts" when unavailable, so callers must check first.
    const body = Buffer.from(plainText, 'utf8').reverse().toString('base64');
    return Promise.resolve(Buffer.from(`${MAGIC}${String(this.encryptCalls)}:${body}`, 'utf8'));
  }

  decryptStringAsync(encrypted: Buffer): Promise<{ result: string; shouldReEncrypt: boolean }> {
    const text = encrypted.toString('utf8');
    const match = /^FAKE-ENC:\d+:(.*)$/s.exec(text);
    if (match?.[1] === undefined) return Promise.reject(new Error('Error while decrypting the ciphertext'));
    const result = Buffer.from(match[1], 'base64').reverse().toString('utf8');
    return Promise.resolve({ result, shouldReEncrypt: this.shouldReEncrypt });
  }
}

/** Real filesystem with switchable failures for one operation. */
export class FlakyFs implements FileSystemLike {
  failRename = false;
  failWrite = false;
  private renamesBeforeFailure: number | null = null;

  /** Lets the next `count` renames succeed, then fails every rename until `failRename` is set to false. */
  failRenamesAfter(count: number): void {
    this.renamesBeforeFailure = count;
  }

  readFile(path: string): Promise<Buffer> {
    return nodeFs.readFile(path);
  }

  writeFile(path: string, data: string | Buffer, options: { flush: boolean }): Promise<void> {
    if (this.failWrite) return Promise.reject(new Error('ENOSPC: no space left on device'));
    return nodeFs.writeFile(path, data, options);
  }

  rename(from: string, to: string): Promise<void> {
    if (this.renamesBeforeFailure !== null) {
      if (this.renamesBeforeFailure === 0) {
        this.renamesBeforeFailure = null;
        this.failRename = true;
      } else {
        this.renamesBeforeFailure -= 1;
      }
    }
    if (this.failRename) return Promise.reject(new Error('EPERM: operation not permitted, rename'));
    return nodeFs.rename(from, to);
  }

  rm(path: string, options: { force: boolean }): Promise<void> {
    return nodeFs.rm(path, options);
  }
}

export class FakeClock {
  constructor(public nowMs = Date.UTC(2026, 10, 5, 12, 0, 0)) {}
  now = (): number => this.nowMs;
  advance(ms: number): void {
    this.nowMs += ms;
  }
}

export async function makeTempDir(): Promise<{ dir: string; cleanup: () => Promise<void> }> {
  const dir = await mkdtemp(join(tmpdir(), 'asclepius-test-'));
  return { dir, cleanup: () => rm(dir, { recursive: true, force: true }) };
}
