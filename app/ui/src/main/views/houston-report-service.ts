/**
 * The weekly PDF for Houston: fetch it for the unlocked astronaut, ask where to save it, write it and open it.
 * The renderer only triggers it (no arguments: not the astronaut, not the path), so a compromised renderer can neither
 * read another member's report nor write anywhere the crew member did not pick in the native dialog.
 * It never throws: every outcome is a `HoustonReportResult`. Nothing is written until the dialog is confirmed.
 */
import { basename } from 'node:path';

import type { HoustonReportResult } from '../../shared/api';
import { ViewsError } from './views-error';
import type { ViewsService } from './views-service';

export interface HoustonReportServiceOptions {
  views: Pick<ViewsService, 'houstonReport'>;
  /** The native save dialog with `defaultName` pre-filled; `filePath` is `undefined` or `canceled` when dismissed. */
  showSaveDialog: (defaultName: string) => Promise<{ canceled: boolean; filePath?: string }>;
  writeFile: (path: string, bytes: Uint8Array) => Promise<void>;
  /** Electron's `shell.openPath`: resolves with an error message, or `''` when it opened. */
  openPath: (path: string) => Promise<string>;
  logError?: (error: unknown) => void;
}

export interface HoustonReportService {
  generate(): Promise<HoustonReportResult>;
}

export function createHoustonReportService({
  views,
  showSaveDialog,
  writeFile,
  openPath,
  logError = console.error,
}: HoustonReportServiceOptions): HoustonReportService {
  return {
    async generate() {
      let report;
      try {
        report = await views.houstonReport();
      } catch (error) {
        if (error instanceof ViewsError) {
          if (error.code === 'unavailable' && error.cause !== undefined) logError(error.cause);
          return { status: 'error', reason: error.code };
        }
        logError(error);
        return { status: 'error', reason: 'unavailable' };
      }
      try {
        const chosen = await showSaveDialog(report.fileName);
        if (chosen.canceled || chosen.filePath === undefined || chosen.filePath === '') return { status: 'cancelled' };
        await writeFile(chosen.filePath, report.bytes);
        // The PDF is saved either way: a missing viewer is reported, never turned into a failure.
        let opened = false;
        try {
          opened = (await openPath(chosen.filePath)) === '';
        } catch (error) {
          logError(error);
        }
        return { status: 'saved', fileName: basename(chosen.filePath), opened };
      } catch (error) {
        logError(error);
        return { status: 'error', reason: 'write_failed' };
      }
    },
  };
}
