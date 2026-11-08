**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-ui (the on-board desktop app)

Desktop app the astronaut uses on the tablet: today's verdict, the daily report, exercise sessions, history,
the urgent-symptom alert and the summary for Houston. Electron + React + TypeScript, built with Vite and pnpm.

Data is **mock for now**: every screen reads from a typed `DataSource` (`src/renderer/data/`) filled with the
mock's values. Connecting it to the app's HTTP APIs (`/ui` on 8000, read-only on 8002) is separate work.

## Requirements

- Node 24 LTS (`.node-version`; `engines` rejects other majors).
- pnpm through corepack: `corepack enable pnpm`. The exact version (12.16.1, with its hash) comes from
  `packageManager` in `package.json`. Node tooling uses pnpm only; never npm or npx.

## Start it

```bash
cd app/ui
pnpm start          # from the repo root: pnpm -C app/ui start
```

`pnpm start` installs anything missing (frozen lockfile), downloads the Electron binary if needed and opens the
app in development mode with hot reload.

In development (and in the e2e build only) two demo crew members exist: **Elena Ruiz** and **Marc Dubois**,
PIN **1234**. The "Mock screens" menu and "Mock: skip login or lock" are development-only too. A production build
starts with no crew: add the first member with "New crew member".

## Scripts

| Script | What it does |
|---|---|
| `pnpm start` | Install, fetch Electron, run the dev app |
| `pnpm dev` | Dev app only (assumes dependencies are installed) |
| `pnpm build` | Production build into `out/` |
| `pnpm dist` | Windows installer (NSIS, unsigned) into `release/` |
| `pnpm test` / `pnpm test:cov` | Vitest unit and component tests (coverage ≥ 90 % lines, branches, functions, statements) |
| `pnpm e2e` | Builds in `e2e` mode and runs the Playwright flows against the real app |
| `pnpm e2e:packaged` | Packs the app (`--dir`) and checks the packaged exe renders the login |
| `pnpm lint` / `pnpm typecheck` | ESLint (strict, no warnings) and `tsc -b` |
| `pnpm gen:types` / `pnpm check:types` | Regenerate / check `src/contracts/v1.ts` from the JSON Schemas |
| `pnpm check:bundle` | Fails if development code (demo crew, dev server URL, relaxed CSP, devtools) reaches the production bundle |
| `pnpm check` | The whole gate: lint, types, contract types, coverage, `pnpm audit` (high) and `check:bundle` |

The same gate runs in Docker and in CI:

```bash
docker compose -f app/docker-compose.yml --profile quality run --rm ui-quality
```

## Layout

```
src/main/       Electron main process: window hardening, app:// protocol, IPC, PIN auth, encrypted stores
src/preload/    The only bridge: window.asclepius (crew, auth, settings, views), nothing else
src/shared/     Bridge contract (api.ts) and IPC channel names + zod schemas
src/renderer/   React app: router, layout, screens in features/<name>/, strings/, data/, components/ui/
src/contracts/  Generated types from spine_monitor/contracts/schemas/v1 (do not edit)
e2e/            Playwright: smoke, flows, visual captures, packaged app
scripts/        Contract type generator, Electron fetch, bundle check, fuses (after-pack)
```

Copy lives in `src/renderer/strings/` (English, one file per screen) so other languages can be added later.

## Security

- Renderer: `sandbox`, `contextIsolation`, no Node integration, no webviews, new windows denied, navigation
  limited to the app, every permission denied.
- The UI is served by a privileged `app://asclepius` scheme restricted to `out/renderer`, with a strict CSP
  (meta tag and response header). No remote resources: the font is bundled.
- IPC: each call checks that the sender is the app's own top-level frame and validates its input with a strict
  zod schema; errors reach the renderer as generic messages.
- PIN: scrypt with a per-member salt, checked in the main process. Three wrong tries lock that member for five
  minutes, persisted across restarts; the attempt is recorded before the PIN is checked.
- Backend views: the renderer never talks to the backend (the CSP `connect-src 'self'` is unchanged). The main process fetches `/ui/...` views for the unlocked astronaut only, over loopback HTTP (`ASCLEPIUS_BACKEND_URL`, default `http://127.0.0.1:8000`, other hosts are ignored), with a 5 s timeout, no redirects and no cache. Errors reach the renderer as `locked`, `not_found` or `unavailable`.
- Crew and auth state are encrypted with Electron `safeStorage` and fail closed when encryption is unavailable.
- Electron fuses: no `RunAsNode`, no `NODE_OPTIONS`, no inspect flags, asar integrity, app loaded only from asar.
- Supply chain: exact versions, a lockfile, build scripts allowed only for `electron`, and a one-day minimum
  release age (`pnpm-workspace.yaml`).

Known limits, kept for later work: authorization lives in the renderer while the data is mock (real health data
must be served by the main process, checked against the main session); no sign-out or crew switch; 4-digit PINs;
per-user, unsigned installer; the packaged exe accepts `--remote-debugging-port`.
