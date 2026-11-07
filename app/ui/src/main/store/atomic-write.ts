/** Write-to-temp then rename, so a crash mid-write never leaves a half-written file in place. */
import { randomBytes } from 'node:crypto';
import * as nodeFs from 'node:fs/promises';

export interface FileSystemLike {
  readFile(path: string): Promise<Buffer>;
  writeFile(path: string, data: string | Buffer, options: { flush: boolean }): Promise<void>;
  rename(from: string, to: string): Promise<void>;
  rm(path: string, options: { force: boolean }): Promise<void>;
}

export const nodeFileSystem: FileSystemLike = {
  readFile: (path) => nodeFs.readFile(path),
  writeFile: (path, data, options) => nodeFs.writeFile(path, data, options),
  rename: (from, to) => nodeFs.rename(from, to),
  rm: (path, options) => nodeFs.rm(path, options),
};

/** Throws if the write fails; the destination is either the old content or the new one, never partial. */
export async function writeFileAtomic(fs: FileSystemLike, path: string, data: string | Buffer): Promise<void> {
  const temp = `${path}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    await fs.writeFile(temp, data, { flush: true });
    await fs.rename(temp, path);
  } catch (error) {
    await fs.rm(temp, { force: true });
    throw error;
  }
}

export function isNotFound(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
