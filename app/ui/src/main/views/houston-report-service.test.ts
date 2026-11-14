import { describe, expect, it } from 'vitest';

import { createHoustonReportService, type HoustonReportServiceOptions } from './houston-report-service';
import { ViewsError } from './views-error';

const PDF = { fileName: 'houston-report-AST-01-2026-11-01_2026-11-07.pdf', bytes: new Uint8Array([37, 80, 68, 70]) };

function build(overrides: Partial<HoustonReportServiceOptions> = {}) {
  const log = { dialogs: [] as string[], written: [] as [string, Uint8Array][], opened: [] as string[], errors: [] as unknown[] };
  const service = createHoustonReportService({
    views: { houstonReport: () => Promise.resolve(PDF) },
    showSaveDialog: (name) => {
      log.dialogs.push(name);
      return Promise.resolve({ canceled: false, filePath: `/home/crew/${name}` });
    },
    writeFile: (path, bytes) => {
      log.written.push([path, bytes]);
      return Promise.resolve();
    },
    openPath: (path) => {
      log.opened.push(path);
      return Promise.resolve('');
    },
    logError: (error) => log.errors.push(error),
    ...overrides,
  });
  return { service, log };
}

describe('createHoustonReportService', () => {
  it('offers the name of the backend, writes the bytes where the crew member chose and opens the file', async () => {
    const { service, log } = build();
    expect(await service.generate()).toEqual({ status: 'saved', fileName: PDF.fileName, opened: true });
    expect(log.dialogs).toEqual([PDF.fileName]);
    expect(log.written).toEqual([[`/home/crew/${PDF.fileName}`, PDF.bytes]]);
    expect(log.opened).toEqual([`/home/crew/${PDF.fileName}`]);
  });

  it('writes nothing when the dialog is dismissed', async () => {
    const { service, log } = build({ showSaveDialog: () => Promise.resolve({ canceled: true }) });
    expect(await service.generate()).toEqual({ status: 'cancelled' });
    expect(log.written).toEqual([]);
    expect(log.opened).toEqual([]);
  });

  it('keeps the file as saved when the viewer cannot be launched', async () => {
    const { service } = build({ openPath: () => Promise.resolve('no application') });
    expect(await service.generate()).toMatchObject({ status: 'saved', opened: false });
  });

  it.each(['locked', 'not_found', 'unavailable'] as const)('answers an error (%s) without opening a dialog', async (code) => {
    const { service, log } = build({ views: { houstonReport: () => Promise.reject(new ViewsError(code)) } });
    expect(await service.generate()).toEqual({ status: 'error', reason: code });
    expect(log.dialogs).toEqual([]);
  });

  it('answers write_failed, and logs why, when the file cannot be written', async () => {
    const { service, log } = build({ writeFile: () => Promise.reject(new Error('EACCES')) });
    expect(await service.generate()).toEqual({ status: 'error', reason: 'write_failed' });
    expect(log.errors).toHaveLength(1);
    expect(log.opened).toEqual([]);
  });
});
