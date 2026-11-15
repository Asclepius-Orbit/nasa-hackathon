**English** · [Español](README.es.md) · [Català](README.ca.md)

# nasa-hackathon

## Chat architecture

```text
Desktop UI -- HTTP POST /chat + SSE --> Agent HTTP server -- MCP --> MCP server --> backend read API
                                            |
                                            +--> Ollama (local LLM)
```

The UI calls the agent directly with a token and `X-Astronaut-Id`. Closing the connection cancels the response. The agent can provide a deterministic fallback when the LLM is unavailable. See the [agent API guide](spine_monitor/agent/README.md) and [OpenAPI contract](spine_monitor/contracts/agent-openapi.yaml).

## Anthropometry and offline evidence

The [source inventory](docs/anthropometry-sources.md) records NASA design guidance and a related study, their populations, methods, page references, limitations and reuse status. NASA's approximately 3% of standing stature and 6% of sitting height are spacecraft design allowances, not clinical thresholds. An engine prediction is experimental: it depends on the individual terrestrial baseline in the same posture, sensor uncertainty and the stated assumptions; standing height, seated height and estimated cord length are different quantities.

Sources reviewed include [NTRS 20110023150](https://ntrs.nasa.gov/citations/20110023150), [20090007782](https://ntrs.nasa.gov/citations/20090007782), [20090038743](https://ntrs.nasa.gov/citations/20090038743), [NASA-STD-3000 Volume I](https://standards.nasa.gov/standard/NASA/NASA-STD-3000_VOL_I), [NASA-STD-3001 Volume 2](https://standards.nasa.gov/node/237), the [Human Integration Design Handbook](https://www.nasa.gov/human-integration-design-handbook/), and the [NTRS API](https://ntrs.nasa.gov/api/openapi/). The inventory gives versions, access and limits; listing a source does not mean its full text is included in the local corpus.

Prepare the machine while connected: install the locked dependencies and Docker images, set up the broker as below, and cache the required Ollama model. The [agent guide](spine_monitor/agent/README.md) describes its model volume and settings. Preserve that volume and the local evidence resources when transferring or updating an offline installation; `docker compose down -v` deletes the model volume. Verify the exact model and resources are present before disconnecting. `ollama-pull` belongs to the explicit `llm-prepare` profile and downloads models that are missing; do not run it offline. To start an already prepared machine, use the `llm` profile, which starts Ollama without downloading models:

```bash
docker compose -f app/docker-compose.yml --profile llm up -d
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

The app can return its deterministic summary if Ollama is unavailable, but it cannot generate a model response without the local model. A generated report should cite retrievable source locations or say that evidence is insufficient.

The bundled corpus and sparse lexical-vector index are local files. After updating the corpus, rebuild its matching index from `spine_monitor/` (no Internet access is needed):

```bash
uv run python -c "from pathlib import Path; from spine_mcp_server.evidence import build_index; p=Path('mcp_server/src/spine_mcp_server/evidence_data'); build_index(p/'corpus.json', p/'index.json')"
```

Copy both files together when installing or updating offline. `SPINE_EVIDENCE_DIR` can point to their directory. Missing, invalid or mismatched files make retrieval fail explicitly; the index does not download evidence or provide semantic embeddings.

## Development environment (Python)

`spine_monitor/` is a uv workspace: a single `.venv` and a single `uv.lock` for every package. Always work from `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). In PyCharm, open the `spine_monitor/` folder and pick its `.venv` as the interpreter.

Docker Compose (`app/docker-compose.yml`): the MQTT broker requires users and ACLs, and their credentials are never in the repo. Run `sh app/mosquitto/setup.sh` once before any `docker compose` command (Git Bash on Windows); it writes them to `app/.env` (gitignored). Users, device registration and LAN access over TLS are described in [spine_monitor/backend/README.md](spine_monitor/backend/README.md).
## Wearable gateway

The real wearable gateway (`spine_monitor/gateway`) reads the Arduino over USB serial and publishes the MQTT v1 bus; the sketch is in `firmware/`. A demo without hardware runs the gateway against an Arduino emulator:

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

The build context is the repository root: if Docker cannot read it because of unreadable folders (leftover temporary directories on Windows), delete them or build from a clean clone. It has not been tested with a real board yet. See [`spine_monitor/gateway/README.md`](spine_monitor/gateway/README.md) (running, configuration, wiring and test with the board) and [`firmware/README.md`](firmware/README.md) (sketch and serial protocol).

## Telemetry source: simulator or device

`SPINE_APP_TELEMETRY_SOURCE` (in `app/.env` or the environment) picks the single producer of telemetry for the whole app: `simulator` (default) or `device`. The backend only accepts that `source`; any other value stops its start with `SPINE_APP_TELEMETRY_SOURCE must be one of: simulator, device (got 'x')`. `SPINE_GATEWAY_DEMO=1` (default `0`) makes `device` use the serial emulator instead of a board; it requires `device` and `start-demo.ps1` rejects it with `simulator`.

| Source | Producer (compose profile) | Where |
|---|---|---|
| `simulator` | physiology simulator (`sim`) | any OS |
| `device` | real gateway + Arduino over USB (`hardware`) | Linux |
| `device` + `SPINE_GATEWAY_DEMO=1` | gateway + Arduino emulator (`gateway-demo`), no board | any OS, including Windows |

Rule: one producer at a time, never simulator and gateway together. Only `app/start-demo.ps1` enforces it; composing profiles by hand can still mix them.

Reproduce it (from the repo root, Docker needed):

```bash
sh app/mosquitto/setup.sh                                  # once: credentials in app/.env
powershell -File app/start-demo.ps1                        # simulator (default)
SPINE_APP_TELEMETRY_SOURCE=device SPINE_GATEWAY_DEMO=1 powershell -File app/start-demo.ps1   # device through the emulator
```

On PowerShell set the variables with `$env:NAME='value'` or write them in `app/.env`; `-NoUi` skips the desktop app.

**Demo data.** Each start of `app/start-demo.ps1` runs a one-shot seeder (`python -m monitor.demo_seed`, compose service `seed`) before the backend starts. It gives AST-01 and AST-02 a complete profile and about 60 days of fictitious but realistic history, built with the real engine: AST-01 is green and turns amber in the last week (driver `stretch.full_spine.elongation_mm`); AST-02 reaches red with an urgent symptom report already sent to Houston. It is idempotent: an astronaut that already has data is skipped (`-NoSeed` skips the seeder altogether), and to start from scratch delete only the backend volume (`docker volume rm app_backend_data`, not the Ollama models). Dates are anchored to the moment of the seed, so restart the demo before presenting it: reassessments made later have no fresh device window and show lower confidence, and after about a week the seeded urgent report leaves the 7-day window and AST-02 loses its escalation.

The backend drops (warning in the log, counter `wrong_source`) every stream, status or calibration message whose `source` differs from the configured one, `replay` included. `ack` and `clock` carry no `source`, so they are not filtered. In device mode the offline Last Will of the gateway always wins in the device status.

Verify it (stop the dev stack first: both need ports 1883, 8000 and 8001 free):

```bash
cd spine_monitor && SPINE_REQUIRE_BROKER=1 uv run pytest tests/test_e2e_telemetry_source.py   # real Mosquitto, both modes
cd app/ui && pnpm run e2e:real          # UI against the simulator
cd app/ui && pnpm run e2e:real:device   # UI against gateway + emulator
```

Known limits:

- Verification with a physical Arduino is **pending**: no test on a board has been done. On a real session the EMG is broken and the IMU signal is unreliable.
- Device mode through the emulator covers pairing, session start and stop only.
- The lat pulldown protocol declares `emg_lat_left` and `emg_lat_right`, but the gateway has one EMG stream, so it is rejected. `calibrate` and artefact injection are simulator-only in the e2e tests. A follow-up issue will track this.
- The realism of the simulated signal is tracked separately (#131).

## Claude Code skills for pull requests

Two project skills in `.claude/skills/` drive PR review with subagents:

- `review-pr`: reviews a PR. It reads the PR, linked issue, CI and threads first, runs parallel reviewers (correctness, tests/TDD, security), verifies each blocker, and publishes one review with `gh`: request changes with inline suggestions if there are blockers, approve if there are only style nits. On your own PR, GitHub forbids a formal verdict, so it posts a `COMMENT` review whose first line is the verdict.
- `receive-review`: applies the requested changes on a PR. It triages each comment against the code, rebases onto `develop`, applies fixes with TDD, fixes failing CI, re-runs the quality gate, pushes with `--force-with-lease`, resolves threads and re-requests review.

Both ask for confirmation before publishing, committing or pushing, and never add Claude attribution.

## Third-party skills and tooling

Vendored skills in `.claude/skills/` (provenance and licences in `.claude/skills/THIRD_PARTY.md`):

- `mcp-builder`: designing MCP servers and their evals (for `mcp_server`).
- `property-based-testing`: Hypothesis properties for `physiology` and `engine`.
- `modern-python`: uv and ruff conventions. Its advice to use `ty` does not apply: this repo type-checks with pyright.
- `async-python-patterns`, `python-testing-patterns`, `python-type-safety`: asyncio, pytest and typing references.

The project `.mcp.json` registers Context7 (pinned version) so Claude Code can look up current docs for FastAPI, aiomqtt, the `mcp` SDK, numpy and pydantic. It sends the queries to Upstash.

Project skills for the domain: `physiology-engine`, `contract-first`, `agent-evals`, `mqtt-dev` and `mcp-tool-change`.

`.claude/skills/ROUTING.md` maps each kind of task to the skills that apply. `implement-feature`, `tdd-workflow`, `review-pr` and `receive-review` use it to name those skills in the subagent prompts.

Node tooling uses pnpm only (`pnpm install`, `pnpm run`, `pnpm dlx`); never npm or npx. The lockfile is `app/ui/pnpm-lock.yaml`.

New dependencies: `pint` (units, in `physiology`), `pydantic-evals` (dev, for agent evals), and `vitest` and `uplot` (dev, in `app/ui`).

## Logo

The Asclepius Orbit logo (`app/ui/build/icon.png` and `app/ui/src/renderer/assets/logo.png`) was generated with AI by OpenAI.

## Licence

All rights reserved until **2026-11-15 11:59 (Europe/Madrid)**; from then on the repository is licensed under the GNU General Public License v3. The terms are in [`LICENSE`](LICENSE). Third-party material, such as everything under `.claude/skills/`, keeps its own licence and is not covered (see [`.claude/skills/THIRD_PARTY.md`](.claude/skills/THIRD_PARTY.md)).

## Sources and resources

See the [project resources inventory](docs/project-resources.md) for NASA data, scientific references, hardware, software, licences and AI disclosure.
