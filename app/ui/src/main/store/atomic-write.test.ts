import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { isNotFound, nodeFileSystem, writeFileAtomic } from './atomic-write';
import { FlakyFs, makeTempDir } from './test-fakes';

let dir: string;
let cleanup: () => Promise<void>;

beforeEach(async () => {
  ({ dir, cleanup } = await makeTempDir());
});
afterEach(async () => {
  await cleanup();
});

describe('writeFileAtomic with the real filesystem', () => {
  it('replaces the file and leaves no temp file', async () => {
    const path = join(dir, 'settings.json');
    await writeFileAtomic(nodeFileSystem, path, 'one');
    await writeFileAtomic(nodeFileSystem, path, Buffer.from('two'));
    expect(await readFile(path, 'utf8')).toBe('two');
    expect((await nodeFileSystem.readFile(path)).toString()).toBe('two');
    expect(await readdir(dir)).toEqual(['settings.json']);
  });

  it('removes the temp file and keeps the old content when the rename fails', async () => {
    const path = join(dir, 'settings.json');
    await writeFileAtomic(nodeFileSystem, path, 'old');
    const fs = new FlakyFs();
    fs.failRename = true;
    await expect(writeFileAtomic(fs, path, 'new')).rejects.toThrow('EPERM');
    expect(await readdir(dir)).toEqual(['settings.json']);
    expect(await readFile(path, 'utf8')).toBe('old');
  });

  it('rejects and cleans up when the target directory does not exist', async () => {
    await expect(writeFileAtomic(nodeFileSystem, join(dir, 'missing', 'x.json'), 'x')).rejects.toThrow();
    expect(await readdir(dir)).toEqual([]);
  });
});

describe('isNotFound', () => {
  it('is true only for ENOENT errors', async () => {
    const missing: unknown = await nodeFileSystem.readFile(join(dir, 'nope')).catch((error: unknown) => error);
    expect(isNotFound(missing)).toBe(true);
    expect(isNotFound(new Error('ENOENT'))).toBe(false);
    expect(isNotFound('ENOENT')).toBe(false);
    expect(isNotFound(Object.assign(new Error('denied'), { code: 'EACCES' }))).toBe(false);
    await nodeFileSystem.rm(join(dir, 'nope'), { force: true });
  });
});
