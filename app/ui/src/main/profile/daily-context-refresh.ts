import type { AuthService } from '../auth/auth-service';
import type { DailyContextService } from './daily-context-service';

/** Check the unlocked member periodically, including after UTC midnight and transient backend failures. */
export function createDailyContextRefresh(auth: Pick<AuthService, 'current'>, daily: Pick<DailyContextService, 'ensureToday'>): () => Promise<void> {
  let running = false;
  return async () => {
    const session = auth.current();
    if (running || session === null || session.locked) return;
    running = true;
    try {
      await daily.ensureToday(session.crewId);
    } finally {
      running = false;
    }
  };
}
