/**
 * Demo crew for dev (`electron-vite dev`) and e2e (`--mode e2e` and `--mode e2e-real`) builds. The harness ids are the
 * simulator's devices (`spine_monitor/physiology/configs/example-mission.yaml`), so the real-mode demo reaches them. Only src/main/index.ts imports it, behind
 * build-time flags, so production bundles do not contain it (`pnpm check:bundle` verifies that).
 */
import type { DemoCrew } from './crew-store';

export const DEMO_CREW: DemoCrew = {
  pin: '1234',
  members: [
    { id: 'AST-01', name: 'Elena Ruiz', harnessId: 'sim-01' },
    { id: 'AST-02', name: 'Marc Dubois', harnessId: 'sim-02' },
  ],
};
