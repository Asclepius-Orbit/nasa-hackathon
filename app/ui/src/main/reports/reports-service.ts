/**
 * Daily report and weekly Oswestry (ODI) of the unlocked astronaut, and which of them are due. Nobody unlocked:
 * writes answer `locked` with nothing sent, `due` rejects with `locked`.
 */
import type { DailyReportFields, OdiAnswer, ReportSaved, ReportsDue, WriteResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { DailyContextService } from '../profile/daily-context-service';
import { requireUnlocked, unlockedId } from '../profile/unlocked';
import type { RecordsClient } from '../views/records-client';
import type { WriteClient } from '../views/write-client';
import { createSelfReportPoster } from './self-report';

export interface ReportsService {
  saveDaily(fields: DailyReportFields): Promise<WriteResult<ReportSaved>>;
  saveOswestry(input: { items: OdiAnswer[] }): Promise<WriteResult<ReportSaved>>;
  due(): Promise<ReportsDue>;
}

export interface ReportsServiceOptions {
  auth: Pick<AuthService, 'current'>;
  records: Pick<RecordsClient, 'reportsDue'>;
  writer: Pick<WriteClient, 'send'>;
  dailyContext: Pick<DailyContextService, 'addExercise'>;
  newId?: () => string;
}

/** The daily form as `domain/self-report` kind `daily`: no empty locations, no side or sleep that is not known. */
export function dailyReportBody(reportId: string, fields: DailyReportFields): Record<string, unknown> {
  const { pain_locations: locations, sleep_hours: hours, sleep_quality_nrs: quality } = fields;
  return {
    report_id: reportId,
    kind: 'daily',
    pain_nrs: fields.pain_nrs,
    stiffness_nrs: fields.stiffness_nrs,
    ...(locations.length === 0
      ? {}
      : { pain_locations: locations.map(({ region, side }) => (side === null ? { region } : { region, side })) }),
    ...(hours === null ? {} : { sleep: { hours, ...(quality === null ? {} : { quality_nrs: quality }) } }),
    // Always sent: the form asks them, and an empty list means "asked, none" (absent would be "not asked").
    red_flags: fields.red_flags,
  };
}

const LOCKED = { ok: false, reason: 'locked' } as const;

export function createReportsService({
  auth,
  records,
  writer,
  dailyContext,
  newId = () => crypto.randomUUID(),
}: ReportsServiceOptions): ReportsService {
  const post = createSelfReportPoster(writer, newId);

  return {
    async saveDaily(fields) {
      const id = unlockedId(auth);
      if (id === null) return LOCKED;
      const result = await post(id, { kind: 'daily', fields }, (reportId) => dailyReportBody(reportId, fields));
      // The exercise goes in today's daily context, best effort: the report is stored whatever happens to it.
      if (result.ok && fields.exercise !== null) await dailyContext.addExercise(id, fields.exercise);
      return result;
    },

    async saveOswestry({ items }) {
      const id = unlockedId(auth);
      if (id === null) return LOCKED;
      return post(id, { kind: 'weekly_odi', items }, (reportId) => ({ report_id: reportId, kind: 'weekly_odi', items }));
    },

    due: async () => records.reportsDue(await requireUnlocked(auth)),
  };
}
