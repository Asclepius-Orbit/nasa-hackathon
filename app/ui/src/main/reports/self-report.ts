/**
 * Posting a self-report (`POST /ui/crew/{id}/self-reports`, `domain/self-report`). Main sets `report_id`; the backend
 * sets the time, source and mission day. After an `unavailable` answer the report may still have been stored, so a
 * retry of the very same report reuses its body (same `report_id`): the backend answers that retry 200 instead of
 * storing it twice. Any other answer, or another report, starts afresh.
 */
import { z } from 'zod';

import type { ReportSaved, WriteResult } from '../../shared/api';
import type { WriteClient } from '../views/write-client';

const reportSavedSchema: z.ZodType<ReportSaved> = z
  .looseObject({
    report: z.looseObject({ report_id: z.string() }),
    assessment: z.looseObject({ level: z.enum(['green', 'amber', 'red']) }).nullable(),
    assessment_skipped: z.string().nullable(),
  })
  .transform(({ report, assessment, assessment_skipped: skipped }) => ({
    reportId: report.report_id,
    assessmentLevel: assessment?.level ?? null,
    assessmentSkipped: skipped,
  }));

export type PostSelfReport = (
  astronautId: string,
  /** What identifies the report for a retry: the caller's input, with its kind. */
  input: unknown,
  build: (reportId: string) => Record<string, unknown>,
) => Promise<WriteResult<ReportSaved>>;

export function createSelfReportPoster(writer: Pick<WriteClient, 'send'>, newId: () => string): PostSelfReport {
  let pending: { key: string; body: Record<string, unknown> } | null = null;
  return async (astronautId, input, build) => {
    const key = JSON.stringify([astronautId, input]);
    const body = pending?.key === key ? pending.body : build(newId());
    pending = null;
    const result = await writer.send({
      method: 'POST',
      path: `/ui/crew/${encodeURIComponent(astronautId)}/self-reports`,
      astronautId,
      body,
      schema: reportSavedSchema,
    });
    if (!result.ok && result.reason === 'unavailable') pending = { key, body };
    return result;
  };
}
