/**
 * The urgent symptom report (`domain/self-report` kind `red_flag`). It is the one write that works without a PIN: with
 * nobody unlocked (signed out, or behind the lock screen) it is sent for the crew member chosen on screen, who must be
 * in this device's crew. With someone unlocked it can only be sent for them. Either way `X-Astronaut-Id` is that
 * member, the astronaut of the path, as the backend's /ui guard requires (`monitor/ui_auth.py`).
 */
import type { ReportSaved, UrgentInput, WriteResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import { unlockedId } from '../profile/unlocked';
import type { CrewStore } from '../store/crew-store';
import type { WriteClient } from '../views/write-client';
import { createSelfReportPoster } from './self-report';

export interface UrgentService {
  send(input: UrgentInput): Promise<WriteResult<ReportSaved>>;
}

export interface UrgentServiceOptions {
  auth: Pick<AuthService, 'current'>;
  crew: Pick<CrewStore, 'list'>;
  writer: Pick<WriteClient, 'send'>;
  now: () => number;
  newId?: () => string;
}

export function createUrgentService({
  auth,
  crew,
  writer,
  now,
  newId = () => crypto.randomUUID(),
}: UrgentServiceOptions): UrgentService {
  const post = createSelfReportPoster(writer, newId);

  return {
    async send(input) {
      const { crewId, red_flags: redFlags, other_symptoms: others } = input;
      const unlocked = unlockedId(auth);
      if (unlocked === null) {
        const members = await crew.list();
        if (members === null) return { ok: false, reason: 'unavailable' };
        if (!members.some((m) => m.id === crewId)) return { ok: false, reason: 'locked' };
      } else if (unlocked !== crewId) {
        return { ok: false, reason: 'locked' };
      }
      return post(crewId, { kind: 'red_flag', input }, (reportId) => ({
        report_id: reportId,
        kind: 'red_flag',
        red_flags: redFlags,
        ...(others.length === 0 ? {} : { other_symptoms: others }),
        onset: new Date(now()).toISOString(),
      }));
    },
  };
}
