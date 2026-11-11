**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-ui (the on-board desktop app)

Desktop app the astronaut uses on the tablet: today's verdict, the daily report, exercise sessions, history,
the urgent-symptom alert and the summary for Houston. Electron + React + TypeScript, built with Vite and pnpm.

Every screen reads from a typed `DataSource` (`src/renderer/data/`). By default it is the backend (`/ui` on 8000),
reached only through the main process; the e2e build and `VITE_MOCK_DATA=true` use the on-board mock instead.

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
sh app/mosquitto/setup.sh   # once: broker credentials in app/.env
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
- Backend credentials: every `/ui` request carries the shared token (`ASCLEPIUS_BACKEND_TOKEN`, or the file named by `ASCLEPIUS_BACKEND_TOKEN_FILE`, e.g. the `ui-token` the backend writes next to its database) and the unlocked astronaut (`X-Astronaut-Id`). Both stay in the main process; without a token the backend answers 401 and the screens show `unavailable`.
- Backend views: the renderer never talks to the backend (the CSP `connect-src 'self'` is unchanged). The main process fetches `/ui/...` views for the unlocked astronaut only, over loopback HTTP (`ASCLEPIUS_BACKEND_URL`, default `http://127.0.0.1:8000`, other hosts are ignored), with a 5 s timeout, no redirects and no cache. Errors reach the renderer as `locked`, `not_found` or `unavailable`.
- Crew registry: the encrypted crew store owns the member list, the `AST-NN` ids, names and PINs. The main process only tells the backend each id (`PUT /ui/crew/{id}`, no body), at startup and after creating a member; while the backend is down the member is still created and the id is retried on the next listing. The backend profile is filled in on the Profile screen (below); without it `/assess` is skipped.
- Backend writes: the main process also writes for the unlocked astronaut only, with the same rules as the views (loopback, token, `X-Astronaut-Id`, 5 s timeout, no redirects). A write answers a result, never a rejection: `ok`, or `invalid` with the backend's field pointers (422), `locked` (401/403), `conflict` (409) or `unavailable`. The UI says "Saved" only on `ok`.
- Profile and daily context (`/profile`, also from Settings): the screen edits part of the profile (date of birth, sex at birth, heights, mass, launch and landing dates) and today's context (phase, gravity, mission day). Main keeps every other stored field. No name ever goes in the profile. When today (UTC) has no context and the profile has a launch date, main writes one after the PIN is accepted and after the profile is saved: the phase and mission day come from the dates (launch day is 1, no day 0), with microgravity in flight and 1 g otherwise. It never overwrites a stored context. Today and First day ask for whatever is missing.
- Harness pairing (Settings > Harness): it lists the devices that published a status for the astronaut (`GET /ui/crew/{id}/devices`), and the chosen `device_id` is stored only in the encrypted crew store. The backend does not pair: the strip shows the device that reported last.
- Sessions (Session screen): "Start session" with the protocol chosen (`lat_pulldown_standing_v1` or `spine_length_rest_v1`). Main sends the paired harness as `device_id`. It takes the session type, band and repetitions from the protocol catalog. The stream declarations come from a per-protocol template copied from the contract examples, pending the standard protocol (#12). Without a paired harness nothing is sent; without today's daily context the backend refuses (422) and the screen points to Profile. The screen follows the session from `starting` to `running`, `rejected` or the end. It remembers the last session, so a `processing_failed` one can be reprocessed. While `running`, Finish stops it as `completed`, Calibrate asks for an EMG MVC calibration and "Mark an artefact" marks the current repetition. Stop: pain sends `aborted_pain` with the current repetition and opens the Stopped screen only when the backend stored it.
- Self-reports: the daily report and the Oswestry are stored with `POST self-reports` (kinds `daily` and `weekly_odi`), and the exercise of the day is added to today's daily context. The six Oswestry items are provisional, pending validation by Aguado (#48). "Saved" appears only on `ok`.
- Urgent symptom: a `red_flag` report. With someone unlocked it is sent for them. From the login or lock screen it is sent without a PIN for the crew member chosen in the dialog: this single write is allowed for an emergency, and nothing is read. It shows Queued only when stored.
- Stopped screen: the repetition the stop was recorded at, the ground link, the call to the crew medical officer (`POST contacts`, also in the summary for Houston) and the logged contacts. There is no survey after the stop: symptoms go through Urgent, pain through the daily report.
- Crew and auth state are encrypted with Electron `safeStorage` and fail closed when encryption is unavailable.
- Electron fuses: no `RunAsNode`, no `NODE_OPTIONS`, no inspect flags, asar integrity, app loaded only from asar.
- Supply chain: exact versions, a lockfile, build scripts allowed only for `electron`, and a one-day minimum
  release age (`pnpm-workspace.yaml`).

Known limits, kept for later work: no sign-out or crew switch; 4-digit PINs;
per-user, unsigned installer; the packaged exe accepts `--remote-debugging-port`.
