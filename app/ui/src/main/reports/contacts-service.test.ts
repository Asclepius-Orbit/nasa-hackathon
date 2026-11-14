import { describe, expect, it } from 'vitest';

import type { AuthSession } from '../../shared/api';
import { FakeBackend } from '../profile/test-fakes';
import { ViewsError } from '../views/views-error';
import { createContactsService } from './contacts-service';

const ID = 'AST-01';
const CONTACT = 'c0a7c7e0-0000-4000-8000-000000000001';
const SESSION = '3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03';
const AT = '2026-11-11T14:20:00+00:00';

function build(session: AuthSession | null = { crewId: ID, locked: false }) {
  const backend = new FakeBackend();
  const service = createContactsService({
    auth: { current: () => session },
    records: backend.records,
    writer: backend.writer,
    newId: () => CONTACT,
  });
  return { backend, service };
}

describe('createContactsService', () => {
  it('lists the unlocked astronaut contacts', async () => {
    const { backend, service } = build();
    backend.contacts = [{ contactId: CONTACT, at: AT, sessionId: null }];
    expect(await service.list()).toEqual(backend.contacts);
  });

  it.each([
    [SESSION, { contact_id: CONTACT, contact: 'crew_medical_officer', session_id: SESSION }, SESSION],
    [null, { contact_id: CONTACT, contact: 'crew_medical_officer' }, null],
  ])('logs a call to the crew medical officer (session %s)', async (sessionId, body, answered) => {
    const { backend, service } = build();
    backend.sendAnswer = { contact_id: CONTACT, at: AT, contact: 'crew_medical_officer', ...(answered === null ? {} : { session_id: answered }) };
    expect(await service.logOfficerCall({ sessionId })).toEqual({
      ok: true,
      value: { contactId: CONTACT, at: AT, sessionId: answered },
    });
    expect(backend.sent).toEqual([{ method: 'POST', path: `/ui/crew/${ID}/contacts`, astronautId: ID, body }]);
  });

  it.each([null, { crewId: ID, locked: true }])('is locked when nobody is unlocked (%j)', async (session) => {
    const { backend, service } = build(session);
    await expect(service.list()).rejects.toThrow(ViewsError);
    expect(await service.logOfficerCall({ sessionId: null })).toEqual({ ok: false, reason: 'locked' });
    expect(backend.sent).toEqual([]);
  });
});
