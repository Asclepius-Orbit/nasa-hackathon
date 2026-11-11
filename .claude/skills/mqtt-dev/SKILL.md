---
name: mqtt-dev
description: Use when se toca MQTT en este repo (broker Mosquitto, topics, publicar o consumir telemetría con aiomqtt, tests del gateway o del ingest del backend), antes de levantar el broker o escribir código o tests MQTT.
---

# mqtt-dev

Todo cambio de código sigue `tdd-workflow`. Si cambia un topic, un payload, QoS o retain, aplica antes la skill `contract-first`: manda `spine_monitor/contracts/asyncapi.yaml` y los JSON Schemas de `schemas/v1/`, no el código.

## Broker local

Mosquitto 2.0.22 en `app/docker-compose.yml`, publicado solo en `127.0.0.1:1883`. Config en `app/mosquitto/config/mosquitto.conf` (`listener 1883`, `allow_anonymous true`: solo válido en dev local, sin ACL ni usuarios).

```bash
docker compose -f app/docker-compose.yml up -d mosquitto          # solo el broker
docker compose -f app/docker-compose.yml --profile sim up -d simulator   # + simulador de telemetría
docker compose -f app/docker-compose.yml logs -f mosquitto
docker compose -f app/docker-compose.yml down
```

El healthcheck es `mosquitto_sub` sobre `$SYS/broker/uptime`; `backend` y `simulator` esperan a `service_healthy`.

## Inspeccionar topics

Con clientes mosquitto instalados (o `docker compose -f app/docker-compose.yml exec mosquitto ...`, que ya los trae):

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'asclepius/v1/#' -v
mosquitto_sub -h 127.0.0.1 -t 'asclepius/v1/crew/+/device/+/status' -v   # retained: llega al suscribirse
mosquitto_pub -h 127.0.0.1 -t 'asclepius/v1/crew/AST-01/device/wb-01/cmd' -q 1 -f cmd.json
```

Sin clientes (p. ej. Windows), usa aiomqtt, que ya es dependencia de `physiology` y `backend`. Script temporal, fuera del repo, ejecutado con `cd spine_monitor && uv run python <ruta>`:

```python
import asyncio, aiomqtt

async def main():
    async with aiomqtt.Client("127.0.0.1", 1883) as c:
        await c.subscribe("asclepius/v1/#", qos=1)
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
- **Integración con broker real**: marker `mosquitto` (`pytestmark = pytest.mark.mosquitto`), modelo `physiology/tests/test_gateway_mosquitto.py`. `conftest.py` le añade `integration`, que es el marker de la tabla de `tdd-workflow`; no pongas dos markers de tipo. Se salta si no hay nada en `127.0.0.1:1883`; con `SPINE_REQUIRE_BROKER=1` (lo fija el servicio `quality`) falla en vez de saltar. Usa device ids propios (`itest-*`) y borra los retained que dejes.
- **Gateway real** (`spine_monitor/gateway`, Arduino por USB serie): sus tests unitarios usan el `FakeClient` y el `Harness` de `gateway/tests/conftest.py`; sin placa se prueba con el emulador (`spine-arduino-emulator`, `spine_gateway/emulator.py`, `SPINE_GATEWAY_SERIAL_URL=socket://host:puerto`) y `gateway/tests/test_e2e_emulator_mosquitto.py` es el modelo de E2E sin hardware contra Mosquitto real (observador aiomqtt, schema completo, ids del topic y retained con un suscriptor tardío).
- Reloj inyectado (`Clock` avanzable), nunca `datetime.now()` ni `time.sleep()`. Espera con eventos o `asyncio.wait_for` con tope, no con sleeps fijos.
- No mockees lógica propia: solo la frontera (cliente MQTT, reloj, rng). El test debe verificar QoS, retain y topic, no solo que se llamó a `publish`.
- Bucle rápido: `cd spine_monitor && uv run pytest physiology/tests -q -p no:randomly -m mosquitto` (con broker arriba). Gate completo: el comando de `tdd-workflow`.

## Seguridad

- Credenciales, hosts y puertos solo por entorno (`SPINE_SIM_MQTT_HOST/PORT`, `SPINE_APP_MQTT_HOST/PORT`, pydantic-settings); nunca en el repo, compose ni logs. `.env.example` está vacío: si añades una variable, documéntala ahí sin valores reales.
- El broker solo escucha en localhost en dev; no publiques el puerto en `0.0.0.0`. `allow_anonymous true` no se lleva a otro entorno sin ACL y usuarios, y eso lo decide Uri.
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
| «Pongo usuario y clave en el compose o el código» | Solo por entorno. |
| «Abro el puerto 1883 a toda la red» | Solo `127.0.0.1`. |
| «Instalo un MCP de MQTT» | No: usa `mosquitto_*` o el script aiomqtt. |
