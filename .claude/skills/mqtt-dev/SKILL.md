---
name: mqtt-dev
description: Use when touching MQTT in this repo (Mosquitto broker, topics, publishing or consuming telemetry with aiomqtt, gateway or backend ingest tests), before starting the broker or writing MQTT code or tests.
---

# mqtt-dev

Every code change follows `tdd-workflow`. If a topic, payload, QoS or retain changes, first apply the `contract-first` skill: `spine_monitor/contracts/asyncapi.yaml` and the JSON Schemas in `schemas/v1/` rule, not the code.

## Local broker

Mosquitto 2.0.22 in `app/docker-compose.yml`, published only on `127.0.0.1:1883`, with users and ACL (#96): `allow_anonymous false`, `password_file` and `acl_file` in `app/mosquitto/config/mosquitto.conf`. The credentials are not in the repo: `sh app/mosquitto/setup.sh` (Git Bash or Linux, only needs docker) writes random passwords to `app/.env` and generates `app/mosquitto/auth/{passwd,acl}` from `app/mosquitto/acl.template` and `acl-device.template` (all gitignored). Without it, every `docker compose` fails with `run sh app/mosquitto/setup.sh`. Repeating it keeps the passwords; `--force` rotates them; `--device AST-03:wb-01` registers a real device. After changing them: `up -d --force-recreate mosquitto`.

| User | Who | Can |
|---|---|---|
| `spine-app` | backend | read `stream/+`, `status`, `ack`, `clock`, `calibration/+` of all; write all `cmd` |
| `spine-itest` | `mosquitto` tests | the same as `spine-app` |
| `spine-health` | healthcheck | read `$SYS/broker/uptime` |
| `<device_id>` (`sim-01`, `itest-01`...) | a device | write only its own channels and read only its `cmd`, under its `crew/<astronaut_id>/device/<device_id>` |

A denied publish is silently dropped and a denied subscription receives nothing: an ACL test checks it with an allowed message behind it (same publisher, in-order delivery), not with a sleep. LAN access: TLS listener 8883 with `-f app/docker-compose.lan.yml` and certificates in `SPINE_MQTT_TLS_DIR` (outside the repo); see the backend README.

```bash
sh app/mosquitto/setup.sh                                           # once (and with --device when registering one)
docker compose -f app/docker-compose.yml up -d mosquitto          # broker only
docker compose -f app/docker-compose.yml --profile sim up -d simulator   # + telemetry simulator
docker compose -f app/docker-compose.yml logs -f mosquitto
docker compose -f app/docker-compose.yml down
```

The healthcheck is `mosquitto_sub` (user `spine-health`) on `$SYS/broker/uptime`; `backend` and `simulator` wait for `service_healthy`.

## Inspecting topics

With the `spine-itest` user (backend permissions); its password is in `app/.env` (`SPINE_MQTT_ITEST_PASSWORD`): load it from the file, do not copy it into commands or logs. With mosquitto clients installed (or `docker compose -f app/docker-compose.yml exec mosquitto ...`, which already has them):

```bash
set -a; . app/.env; set +a
mosquitto_sub -h 127.0.0.1 -u spine-itest -P "$SPINE_MQTT_ITEST_PASSWORD" -t 'asclepius/v1/crew/+/device/+/status' -v   # retained: arrives on subscribing
mosquitto_pub -h 127.0.0.1 -u spine-itest -P "$SPINE_MQTT_ITEST_PASSWORD" -t 'asclepius/v1/crew/AST-01/device/sim-01/cmd' -q 1 -f cmd.json
```

`asclepius/v1/#` receives nothing: the ACL only grants specific channels. Without clients (e.g. Windows), use aiomqtt, which is already a dependency of `physiology` and `backend`. Temporary script, outside the repo, run with `cd spine_monitor && uv run python <path>` (with `app/.env` loaded as above):

```python
import asyncio, os, aiomqtt

async def main():
    async with aiomqtt.Client("127.0.0.1", 1883, username="spine-itest", password=os.environ["SPINE_MQTT_ITEST_PASSWORD"]) as c:
        await c.subscribe("asclepius/v1/crew/+/device/+/stream/+", qos=1)
        async for m in c.messages:
            print(m.topic, m.retain, m.payload.decode()[:200])

asyncio.run(main())
```

## Convention (from `asyncapi.yaml`)

Root `asclepius/v1/crew/{astronaut_id}/device/{device_id}/...`. Build and parse topics only with `spine_contracts.topics` (`stream`, `status`, `cmd`, `ack`, `clock`, `calibration`, `parse_stream`, `SUBSCRIBE_ALL_*`); it validates the ids so no `/`, `+` or `#` gets in. Never concatenate strings by hand.

| Channel | QoS | Retain |
|---|---|---|
| `.../stream/{stream_id}` | 1 | no |
| `.../status` (with Last Will `offline`) | 1 | yes |
| `.../cmd` (app to gateway) | 1 | no |
| `.../ack` | 1 | no |
| `.../clock` | 1 | yes |
| `.../calibration/{stream_id}` | 1 | yes |

Payloads are validated against `schemas/v1/` with `spine_contracts.validation`. Ids are pseudonyms (`AST-01`): no personal data travels on the bus.

## Tests

- **Unit**: inject a hand-written `FakeClient` with `publish(topic, payload, qos, retain)`, `subscribe` and `messages`, like the one in `physiology/tests/test_gateway.py` (with its `Harness` and `Clock`). The ports are the `MqttClient` Protocols in `backend/src/monitor/ingest.py` and in `physiology/src/spine_physiology/sinks/mqtt.py`.
- **Integration with a real broker**: marker `mosquitto` (`pytestmark = pytest.mark.mosquitto`), model `physiology/tests/test_gateway_mosquitto.py`. `conftest.py` adds `integration` to it, which is the marker from the `tdd-workflow` table; do not put two kind markers. It is skipped if there is nothing on `127.0.0.1:1883`; with `SPINE_REQUIRE_BROKER=1` (set by the `quality` service) it fails instead of skipping. Credentials via environment (the `quality` service passes them from `app/.env`): `SPINE_TEST_MQTT_USERNAME` (`spine-itest`) and `SPINE_TEST_MQTT_PASSWORD` for the observer, `SPINE_TEST_MQTT_DEVICE_PASSWORD` for the devices `itest-01` (AST-01) and `itest-02` (AST-02), which connect with their device id as the user. Use those device ids (others have no user) and delete the retained messages you leave, connected as the device itself (the observer cannot write to its topics).
- **Real gateway** (`spine_monitor/gateway`, Arduino over USB serial): its unit tests use the `FakeClient` and the `Harness` from `gateway/tests/conftest.py`; without a board, test with the emulator (`spine-arduino-emulator`, `spine_gateway/emulator.py`, `SPINE_GATEWAY_SERIAL_URL=socket://host:port`) and `gateway/tests/test_e2e_emulator_mosquitto.py` is the model for E2E without hardware against a real Mosquitto (aiomqtt observer, full schema, topic ids and retained with a late subscriber).
- Injected clock (advanceable `Clock`), never `datetime.now()` or `time.sleep()`. Wait with events or `asyncio.wait_for` with a cap, not with fixed sleeps.
- Do not mock your own logic: only the boundary (MQTT client, clock, rng). The test must verify QoS, retain and topic, not just that `publish` was called.
- Fast loop, with the compose broker up and from the root: `set -a; . app/.env; set +a; export SPINE_TEST_MQTT_PASSWORD="$SPINE_MQTT_ITEST_PASSWORD" SPINE_TEST_MQTT_DEVICE_PASSWORD="$SPINE_MQTT_ITEST_DEVICE_PASSWORD" SPINE_REQUIRE_BROKER=1`, then `cd spine_monitor && uv run pytest physiology/tests -q -p no:randomly -m mosquitto`. Full gate: the `tdd-workflow` command.

## Security

- Credentials, hosts and ports only via environment (`SPINE_{APP,SIM}_MQTT_{HOST,PORT,USERNAME,PASSWORD,TLS,TLS_CA_FILE}`, pydantic-settings; the password is `SecretStr`); never in the repo, compose or logs. Compose only interpolates `${SPINE_MQTT_*:?...}` from `app/.env`, which `setup.sh` generates. `.env.example` lists the variables without values: if you add one, document it there without real values.
- Never `allow_anonymous true` or a user without an ACL. A new client is a new user in `acl.template` (or a device with `--device`), with the minimum topics.
- Port 1883 (no TLS) only listens on `127.0.0.1`; do not publish it on `0.0.0.0`. The network only sees the TLS 8883 listener from `docker-compose.lan.yml`, on the `SPINE_MQTT_LAN_ADDRESS` address. Certificates and keys are not committed (`*.crt`, `*.key`, `*.pem` are in `.gitignore`).
- Do not use a third-party MQTT MCP: it would run foreign code with access to the bus. Use the commands above.

## Notes

aiomqtt 2.5.1 does not declare Python 3.14 support on PyPI, but it is already used in `physiology` and `backend` (`pythonVersion = "3.14"`) and the tests pass. Do not switch clients because of that; for its API consult the `context7` MCP.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll build the topic with an f-string" | Use `spine_contracts.topics`; it validates ids. |
| "I'll publish QoS 0 / retain to go faster" | `asyncapi.yaml` fixes it; changing it is a contract change (`contract-first`). |
| "I'll mock `publish` with MagicMock" | Write a `FakeClient` and check the observable effect. |
| "I'll put in a `sleep(1)` until it arrives" | Injected clock and wait with an event and timeout. |
| "The broker test is skipped, no matter" | It skips only without a broker; in the gate it must run (`SPINE_REQUIRE_BROKER=1`). |
| "I'll put the user and password in compose or the code" | Only via environment; compose reads them from `app/.env` (`setup.sh`). |
| "I'll open port 1883 to the whole network" | Only `127.0.0.1`; the LAN goes through TLS 8883 (`docker-compose.lan.yml`). |
| "To debug I'll set `allow_anonymous true` or `topic readwrite #`" | No: use `spine-itest` and the specific channels. |
| "The ACL test waits 2 s to see if it arrives" | Publish the denied one and then an allowed one from the same client; wait for the allowed one. |
| "I'll install an MQTT MCP" | No: use `mosquitto_*` or the aiomqtt script. |
