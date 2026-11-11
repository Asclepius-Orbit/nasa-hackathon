/**
 * Contacts of the unlocked astronaut with the crew medical officer (`domain/medical-contact`, `GET`/`POST
 * /ui/crew/{id}/contacts`). Main sets `contact_id`; the backend sets `at`. `list` rejects with a `ViewsError`.
 */
import { z } from 'zod';

import type { MedicalContactEntry, WriteResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import { requireUnlocked, unlockedId } from '../profile/unlocked';
import type { RecordsClient } from '../views/records-client';
import type { WriteClient } from '../views/write-client';

export interface ContactsService {
  list(): Promise<MedicalContactEntry[]>;
  logOfficerCall(input: { sessionId: string | null }): Promise<WriteResult<MedicalContactEntry>>;
}

export interface ContactsServiceOptions {
  auth: Pick<AuthService, 'current'>;
  records: Pick<RecordsClient, 'contacts'>;
  writer: Pick<WriteClient, 'send'>;
  newId?: () => string;
}

const contactSchema: z.ZodType<MedicalContactEntry> = z
  .looseObject({ contact_id: z.string(), at: z.string(), session_id: z.string().optional() })
  .transform((c) => ({ contactId: c.contact_id, at: c.at, sessionId: c.session_id ?? null }));

export function createContactsService({
  auth,
  records,
  writer,
  newId = () => crypto.randomUUID(),
}: ContactsServiceOptions): ContactsService {
  return {
    list: async () => records.contacts(await requireUnlocked(auth)),

    async logOfficerCall({ sessionId }) {
      const id = unlockedId(auth);
      if (id === null) return { ok: false, reason: 'locked' };
      return writer.send({
        method: 'POST',
        path: `/ui/crew/${encodeURIComponent(id)}/contacts`,
        astronautId: id,
        body: { contact_id: newId(), contact: 'crew_medical_officer', ...(sessionId === null ? {} : { session_id: sessionId }) },
        schema: contactSchema,
      });
    },
  };
}
