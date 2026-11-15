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

/** Env var the real-mode e2e runner sets with the harness id the producer publishes as (sim-01, or gw-demo in device mode). */
export const E2E_REAL_HARNESS_ENV = 'E2E_REAL_HARNESS';

/**
 * Demo crew of the `e2e-real` build: the first member is paired to the producer's harness (the app has no pairing screen).
 * Falls back to the simulator's `sim-01` when the variable is unset or blank.
 */
export function e2eRealCrew(env: Readonly<Record<string, string | undefined>>): DemoCrew {
  const harnessId = env[E2E_REAL_HARNESS_ENV]?.trim() || 'sim-01';
  const [first, ...rest] = DEMO_CREW.members;
  return { ...DEMO_CREW, members: first === undefined ? rest : [{ ...first, harnessId }, ...rest] };
}
