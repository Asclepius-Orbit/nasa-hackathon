/** Harness pairing of the unlocked member, stored only on this device (crew store). Nobody else's can be changed. */
import type { SetHarnessResult } from '../../shared/api';
import type { AuthService } from '../auth/auth-service';
import type { CrewStore } from '../store/crew-store';
import { unlockedId } from './unlocked';

export interface HarnessService {
  setHarness(input: { harnessId: string | null }): Promise<SetHarnessResult>;
}

export function createHarnessService({
  auth,
  crew,
}: {
  auth: Pick<AuthService, 'current'>;
  crew: Pick<CrewStore, 'setHarness'>;
}): HarnessService {
  return {
    async setHarness({ harnessId }) {
      const id = unlockedId(auth);
      if (id === null) return { ok: false, reason: 'locked' };
      const result = await crew.setHarness(id, harnessId);
      // The unlocked member always exists; `unknown` would mean the store changed under us.
      return result.ok ? result : { ok: false, reason: 'unavailable' };
    },
  };
}
