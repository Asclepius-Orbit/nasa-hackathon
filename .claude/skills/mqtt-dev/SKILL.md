---
name: mqtt-dev
description: Use when se toca MQTT en este repo (broker Mosquitto, topics, publicar o consumir telemetría con aiomqtt, tests del gateway o del ingest del backend), antes de levantar el broker o escribir código o tests MQTT.
---

# mqtt-dev

Todo cambio de código sigue `tdd-workflow`. Si cambia un topic, un payload, QoS o retain, aplica antes la skill `contract-first`: manda `spine_monitor/contracts/asyncapi.yaml` y los JSON Schemas de `schemas/v1/`, no el código.

## Broker local

Mosquitto 2.0.22 en `app/docker-compose.yml`, publicado solo en `127.0.0.1:1883`, con usuarios y ACL (#96): `allow_anonymous false`, `password_file` y `acl_file` en `app/mosquitto/config/mosquitto.conf`. Las credenciales no están en el repo: `sh app/mosquitto/setup.sh` (Git Bash o Linux, solo necesita docker) escribe contraseñas aleatorias en `app/.env` y genera `app/mosquitto/auth/{passwd,acl}` desde `app/mosquitto/acl.template` y `acl-device.template` (todo gitignored). Sin él, todo `docker compose` falla con `run sh app/mosquitto/setup.sh`. Repetirlo conserva las contraseñas; `--force` las rota; `--device AST-03:wb-01` da de alta un dispositivo real. Tras cambiarlas: `up -d --force-recreate mosquitto`.

| Usuario | Quién | Puede |
|---|---|---|
| `spine-app` | backend | leer `stream/+`, `status`, `ack`, `clock`, `calibration/+` de todos; escribir todo `cmd` |
| `spine-itest` | tests `mosquitto` | lo mismo que `spine-app` |
| `spine-health` | healthcheck | leer `$SYS/broker/uptime` |
| `<device_id>` (`sim-01`, `itest-01`...) | un dispositivo | escribir solo sus canales y leer solo su `cmd`, bajo su `crew/<astronaut_id>/device/<device_id>` |

Un publish denegado se descarta en silencio y una suscripción denegada no recibe nada: un test de ACL lo comprueba con un mensaje permitido detrás (mismo publicador, entrega en orden), no con un sleep. Acceso por LAN: listener TLS 8883 con `-f app/docker-compose.lan.yml` y certificados en `SPINE_MQTT_TLS_DIR` (fuera del repo); ver el README del backend.

```bash
sh app/mosquitto/setup.sh                                           # una vez (y con --device al dar de alta uno)
docker compose -f app/docker-compose.yml up -d mosquitto          # solo el broker
docker compose -f app/docker-compose.yml --profile sim up -d simulator   # + simulador de telemetría
docker compose -f app/docker-compose.yml logs -f mosquitto
docker compose -f app/docker-compose.yml down
```

El healthcheck es `mosquitto_sub` (usuario `spine-health`) sobre `$SYS/broker/uptime`; `backend` y `simulator` esperan a `service_healthy`.

## Inspeccionar topics

Con el usuario `spine-itest` (permisos del backend); su contraseña está en `app/.env` (`SPINE_MQTT_ITEST_PASSWORD`): cárgala desde el fichero, no la copies en comandos ni en logs. Con clientes mosquitto instalados (o `docker compose -f app/docker-compose.yml exec mosquitto ...`, que ya los trae):

```bash
set -a; . app/.env; set +a
mosquitto_sub -h 127.0.0.1 -u spine-itest -P "$SPINE_MQTT_ITEST_PASSWORD" -t 'asclepius/v1/crew/+/device/+/status' -v   # retained: llega al suscribirse
mosquitto_pub -h 127.0.0.1 -u spine-itest -P "$SPINE_MQTT_ITEST_PASSWORD" -t 'asclepius/v1/crew/AST-01/device/sim-01/cmd' -q 1 -f cmd.json
```

`asclepius/v1/#` no recibe nada: la ACL solo concede canales concretos. Sin clientes (p. ej. Windows), usa aiomqtt, que ya es dependencia de `physiology` y `backend`. Script temporal, fuera del repo, ejecutado con `cd spine_monitor && uv run python <ruta>` (con `app/.env` cargado como arriba):

```python
import asyncio, os, aiomqtt

async def main():
    async with aiomqtt.Client("127.0.0.1", 1883, username="spine-itest", password=os.environ["SPINE_MQTT_ITEST_PASSWORD"]) as c:
        await c.subscribe("asclepius/v1/crew/+/device/+/stream/+", qos=1)
        async for m in c.messages:
            print(m.topic, m.retain, m.payload.decode()[:200])

asyncio.run(main())
```

## Convención (de `asyncapi.yaml`)

Raíz `asclepius/v1/crew/{astronaut_id}/device/{device_id}/...`. Construye y parsea topics solo con `spine_contracts.topics` (`stream`, `status`, `cmd`, `ack`, `clock`, `calibration`, `parse_stream`, `SUBSCRIBE_ALL_*`); valida los ids y así no entra un `/`, `+` o `#`. Nunca concatenes strings a mano.

| Canal | QoS | Retain |
|---|---|---|
| `.../stream/{stream_id}` | 1 | no |
| `.../status` (con Last Will `offline`) | 1 | sí |
| `.../cmd` (app a gateway) | 1 | no |
| `.../ack` | 1 | no |
| `.../clock` | 1 | sí |
| `.../calibration/{stream_id}` | 1 | sí |

Los payloads se validan contra `schemas/v1/` con `spine_contracts.validation`. Los ids son seudónimos (`AST-01`): ningún dato personal viaja por el bus.

## Tests

- **Unit**: inyecta un `FakeClient` escrito a mano con `publish(topic, payload, qos, retain)`, `subscribe` y `messages`, como el de `physiology/tests/test_gateway.py` (con su `Harness` y `Clock`). Los puertos son los Protocols `MqttClient` de `backend/src/monitor/ingest.py` y el de `physiology/src/spine_physiology/sinks/mqtt.py`.
- **Integración con broker real**: marker `mosquitto` (`pytestmark = pytest.mark.mosquitto`), modelo `physiology/tests/test_gateway_mosquitto.py`. `conftest.py` le añade `integration`, que es el marker de la tabla de `tdd-workflow`; no pongas dos markers de tipo. Se salta si no hay nada en `127.0.0.1:1883`; con `SPINE_REQUIRE_BROKER=1` (lo fija el servicio `quality`) falla en vez de saltar. Credenciales por entorno (el servicio `quality` las pasa desde `app/.env`): `SPINE_TEST_MQTT_USERNAME` (`spine-itest`) y `SPINE_TEST_MQTT_PASSWORD` para el observador, `SPINE_TEST_MQTT_DEVICE_PASSWORD` para los dispositivos `itest-01` (AST-01) e `itest-02` (AST-02), que entran con su device id como usuario. Usa esos device ids (otros no tienen usuario) y borra los retained que dejes conectado como el propio dispositivo (el observador no puede escribir en sus topics).
- **Gateway real** (`spine_monitor/gateway`, Arduino por USB serie): sus tests unitarios usan el `FakeClient` y el `Harness` de `gateway/tests/conftest.py`; sin placa se prueba con el emulador (`spine-arduino-emulator`, `spine_gateway/emulator.py`, `SPINE_GATEWAY_SERIAL_URL=socket://host:puerto`) y `gateway/tests/test_e2e_emulator_mosquitto.py` es el modelo de E2E sin hardware contra Mosquitto real (observador aiomqtt, schema completo, ids del topic y retained con un suscriptor tardío).
- Reloj inyectado (`Clock` avanzable), nunca `datetime.now()` ni `time.sleep()`. Espera con eventos o `asyncio.wait_for` con tope, no con sleeps fijos.
- No mockees lógica propia: solo la frontera (cliente MQTT, reloj, rng). El test debe verificar QoS, retain y topic, no solo que se llamó a `publish`.
- Bucle rápido, con el broker de compose arriba y desde la raíz: `set -a; . app/.env; set +a; export SPINE_TEST_MQTT_PASSWORD="$SPINE_MQTT_ITEST_PASSWORD" SPINE_TEST_MQTT_DEVICE_PASSWORD="$SPINE_MQTT_ITEST_DEVICE_PASSWORD" SPINE_REQUIRE_BROKER=1`, y luego `cd spine_monitor && uv run pytest physiology/tests -q -p no:randomly -m mosquitto`. Gate completo: el comando de `tdd-workflow`.

## Seguridad

- Credenciales, hosts y puertos solo por entorno (`SPINE_{APP,SIM}_MQTT_{HOST,PORT,USERNAME,PASSWORD,TLS,TLS_CA_FILE}`, pydantic-settings; la contraseña es `SecretStr`); nunca en el repo, compose ni logs. Compose solo interpola `${SPINE_MQTT_*:?...}` de `app/.env`, que genera `setup.sh`. `.env.example` lista las variables sin valores: si añades una, documéntala ahí sin valores reales.
- Nunca `allow_anonymous true` ni un usuario sin ACL. Un cliente nuevo es un usuario nuevo en `acl.template` (o un dispositivo con `--device`), con los topics mínimos.
- El puerto 1883 (sin TLS) solo escucha en `127.0.0.1`; no lo publiques en `0.0.0.0`. La red solo ve el listener TLS 8883 de `docker-compose.lan.yml`, en la dirección de `SPINE_MQTT_LAN_ADDRESS`. No se commitean certificados ni claves (`*.crt`, `*.key`, `*.pem` están en `.gitignore`).
- No uses un MCP de MQTT de terceros: ejecutaría código ajeno con acceso al bus. Usa los comandos de arriba.

## Notas

aiomqtt 2.5.1 no declara soporte de Python 3.14 en PyPI, pero ya se usa en `physiology` y `backend` (`pythonVersion = "3.14"`) y los tests pasan. No cambies de cliente por eso; para su API consulta el MCP `context7`.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Armo el topic con un f-string» | Usa `spine_contracts.topics`; valida ids. |
| «Publico QoS 0 / retain para ir rápido» | Lo fija `asyncapi.yaml`; cambiarlo es cambio de contrato (`contract-first`). |
| «Mockeo `publish` con MagicMock» | Escribe un `FakeClient` y comprueba el efecto observable. |
| «Meto un `sleep(1)` hasta que llegue» | Reloj inyectado y espera con evento y timeout. |
| «El test de broker se salta, da igual» | Salta solo sin broker; en el gate debe correr (`SPINE_REQUIRE_BROKER=1`). |
| «Pongo usuario y clave en el compose o el código» | Solo por entorno; compose las lee de `app/.env` (`setup.sh`). |
| «Abro el puerto 1883 a toda la red» | Solo `127.0.0.1`; la LAN va por TLS 8883 (`docker-compose.lan.yml`). |
| «Para depurar pongo `allow_anonymous true` o `topic readwrite #`» | No: usa `spine-itest` y los canales concretos. |
| «El test de ACL espera 2 s a ver si llega» | Publica lo denegado y luego algo permitido del mismo cliente; espera a lo permitido. |
| «Instalo un MCP de MQTT» | No: usa `mosquitto_*` o el script aiomqtt. |
