/**
 * Demo crew for dev (`electron-vite dev`) and e2e (`--mode e2e`) builds. Only src/main/index.ts imports it, behind
 * build-time flags, so production bundles do not contain it (`pnpm check:bundle` verifies that).
 */
import type { DemoCrew } from './crew-store';

export const DEMO_CREW: DemoCrew = {
  pin: '1234',
  members: [
    { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'wb-01' },
    { id: 'AST-02', name: 'Marc Dubois', harnessId: 'wb-02' },
  ],
};
