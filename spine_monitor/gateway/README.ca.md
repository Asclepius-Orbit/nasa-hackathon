[English](README.md) · [Español](README.es.md) · **Català**

# spine-gateway

Gateway real del wearable. Llegeix l'Arduino Uno/Nano del wearable per USB sèrie i publica al bus MQTT v1 el que defineix el contracte: chunks de stream, estat del dispositiu, sincronització de rellotge, calibratges i acks de comandes. El seu comportament observable és el mateix que el del gateway fals de `spine_monitor/physiology`, de manera que el backend, el motor i la UI no els distingeixen (tret de `source: device` en lloc de `simulator`).

## Estat de verificació

Verificat **sense hardware**:

- Tests unitaris de tots els mòduls (protocol, assemblador, calibratge, comandes, estat, font sèrie, app, emulador, ajustos) i tests de propietats del protocol i de l'assemblador.
- Emulador de l'Arduino (`spine-arduino-emulator`) que parla el protocol sèrie per TCP.
- Test d'extrem a extrem contra un Mosquitto real: emulador -> gateway -> broker -> observador, amb validació completa d'esquema, topics, QoS, retained i acks.
- Build del firmware (`firmware-build`): test de paritat de `protocol.h` contra els vectors daurats i compilació per a `arduino:avr:uno`.

**Encara no provat amb una placa real.** Res d'això s'ha executat en hardware:

- Temporització real del mostreig de l'ADC i de la ISR de Timer1 (EMG a 1000 Hz, cordó a 20 Hz).
- Lectura I2C del MPU-6050 (100 Hz, 400 kHz).
- Cabal del pont USB-sèrie a 250000 baud (uns 5 KB/s de càrrega mitjana).
- El mòdul EMG real: alimentació, rang de `SIG` i si lliura senyal crua o envolvent.
- Soroll del divisor de tensió del cordó.
- Reinici de l'Uno en obrir el port sèrie (el gateway ho dona per fet i espera el següent hello/tick).
- Pas del dispositiu USB a Docker a Linux (servei `gateway`, perfil `hardware`).
- SIGTERM de l'emulador a Docker.

## Arquitectura

Paquet `spine_gateway` (`src/spine_gateway/`):

| Mòdul | Què fa |
|---|---|
| `settings.py` | `GatewaySettings`: totes les variables `SPINE_GATEWAY_*` (o `.env`) amb validació d'ids contra `spine_contracts.topics`. |
| `protocol.py` | Protocol de línia sèrie: parser (`parse_line`, mai llança) i codificadors, CRC-8/MAXIM, `frame_gap` sobre el comptador `u16`. |
| `ports.py` | Port `LineSource` i els seus esdeveniments (`SerialLine`, `LinkUp`, `LinkDown`). |
| `serial_source.py` | `SerialLineSource`: pyserial en un fil, talla per `\n`, reconnecta amb backoff exponencial. |
| `assembler.py` | `ChunkAssembler`: converteix els lots `D` en `EmgChunk`/`ImuChunk`/`StretchChunk` (unitats, qualitat, `seq`, forats, finestres de fons a 10 Hz). |
| `calibration.py` | `CalibrationRecorder`: grava els calibratges `stretch` i `emg_mvc` a partir dels lots. |
| `commands.py` | Llegeix i valida els payloads de `cmd` i construeix els `command-ack`. |
| `status.py` | `DeviceStatus`, `ClockSync` i el `session_id` de fons; estat de streams i global. |
| `app.py` | `Gateway`: bucle MQTT (aiomqtt), Last Will, temporitzadors, comandes, sessions i calibratges. |
| `emulator.py` | Arduino fals per TCP (`spine-arduino-emulator`) per treballar sense placa. |
| `__main__.py` | Punt d'entrada `spine-gateway`. |

Flux de dades:

```
Arduino (firmware/spine_wearable) --USB sèrie, 250000 baud--> línies H / T / D (+ CRC-8)
  -> SerialLineSource -> Gateway (parse_line, ChunkAssembler, CalibrationRecorder)
  -> MQTT v1 (aiomqtt, QoS 1) -> Mosquitto
```

## Com executar-lo

Les ordres parteixen de l'arrel del repo; les d'`uv` s'executen des de `spine_monitor/`.

### Natiu, amb l'Arduino real

Aixeca el broker (només escolta a `127.0.0.1:1883`) i el gateway:

```bash
docker compose -f app/docker-compose.yml up -d mosquitto
cd spine_monitor
SPINE_GATEWAY_SERIAL_URL=/dev/ttyACM0 uv run spine-gateway      # Linux
```

```powershell
cd spine_monitor
$env:SPINE_GATEWAY_SERIAL_URL = "COM3"; uv run spine-gateway    # Windows
```

El broker s'espera a `127.0.0.1:1883` (`SPINE_GATEWAY_MQTT_HOST` / `SPINE_GATEWAY_MQTT_PORT`). Les variables també poden anar en un `.env` al directori des del qual s'arrenca el gateway. `Ctrl+C` (o SIGTERM) deixa un estat `offline` retained i tanca el port i la connexió.

### Sense hardware: emulador

Dos terminals, amb el broker aixecat:

```bash
cd spine_monitor
uv run spine-arduino-emulator --port 5000
SPINE_GATEWAY_SERIAL_URL=socket://127.0.0.1:5000 uv run spine-gateway
```

pyserial converteix `socket://host:port` en una connexió TCP, així que el gateway executa exactament el mateix codi que amb una placa. L'emulador genera senyals sintètics deterministes i dona a cada client un dispositiu nou (`boot_id` nou, comptadors a zero), com un Arduino reiniciat en obrir el port. Opcions: `--host` (per defecte `127.0.0.1`), `--port` (per defecte 5000), `--seed`, `--no-imu` (firmware sense MPU-6050: sense trames `I` i amb `#imu absent`), `--drop-every N` (cada N-èsim lot no surt però gasta el seu `frame_seq`, com un overrun) i `--reboot-after S` (reinici del dispositiu als S segons).

La mateixa demo a Docker, perfil `gateway-demo` (emulador + gateway; Mosquitto arrenca com a dependència). La imatge `spine-gateway:dev` es construeix al primer `up` (fes servir `--build` després de canviar el codi):

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

### Placa real a Docker (només Linux)

Perfil `hardware`, servei `gateway`:

```bash
docker compose -f app/docker-compose.yml --profile hardware up -d gateway
```

El dispositiu es mapeja a `/dev/ttyACM0` dins del contenidor; `SPINE_GATEWAY_SERIAL_DEVICE` tria el dispositiu del host (per defecte `/dev/ttyACM0`). Només funciona a Linux: Docker Desktop a Windows i macOS no pot passar un dispositiu USB a un contenidor, així que allà el gateway s'executa de manera nativa.

### Notes

- El broker local no té autenticació i només escolta a `127.0.0.1`. Exposar-lo a la xarxa exigeix autenticació abans (issue #96, fora d'abast aquí).
- Observar el bus: `docker compose -f app/docker-compose.yml exec mosquitto mosquitto_sub -h 127.0.0.1 -t 'asclepius/v1/crew/AST-01/device/wb-01/#' -v` (ids de la configuració per defecte).

## Configuració

Totes les variables porten el prefix `SPINE_GATEWAY_` i són opcionals; `.env.example` les llista comentades. Els ids es validen amb les mateixes expressions que fa servir `spine_contracts.topics` per construir els topics.

| Variable | Per defecte | Què fa |
|---|---|---|
| `SPINE_GATEWAY_SERIAL_URL` | `COM3` | Port sèrie tal com l'entén pyserial: `COM3`, `/dev/ttyACM0` o `socket://host:port`. |
| `SPINE_GATEWAY_SERIAL_BAUD` | `250000` | Velocitat en bauds (el firmware està fixat a 250000). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S` | `1.0` | Primera espera (s) abans de reobrir el port si no obre o es desendolla. |
| `SPINE_GATEWAY_SERIAL_RECONNECT_FACTOR` | `2.0` | Multiplicador de l'espera després de cada intent fallit (>= 1). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S` | `10.0` | Límit (s) de l'espera de reconnexió sèrie (>= la inicial). |
| `SPINE_GATEWAY_ASTRONAUT_ID` | `AST-01` | Astronauta al qual s'atribueixen les dades (va als topics). |
| `SPINE_GATEWAY_DEVICE_ID` | `wb-01` | Id del dispositiu (va als topics i a l'id de client MQTT). |
| `SPINE_GATEWAY_MQTT_HOST` | `127.0.0.1` | Host del broker MQTT. |
| `SPINE_GATEWAY_MQTT_PORT` | `1883` | Port del broker MQTT (1-65535). |
| `SPINE_GATEWAY_MQTT_CLIENT_ID` | `spine-gateway` | Prefix de l'id de client MQTT; la connexió fa servir `<client_id>-<device_id>`. |
| `SPINE_GATEWAY_MQTT_RECONNECT_INITIAL_S` | `1.0` | Primera espera (s) abans de reconnectar amb el broker. |
| `SPINE_GATEWAY_MQTT_RECONNECT_FACTOR` | `2.0` | Multiplicador de l'espera després de cada intent fallit (>= 1); torna a la inicial en connectar. |
| `SPINE_GATEWAY_MQTT_RECONNECT_MAX_S` | `30.0` | Límit (s) de l'espera de reconnexió MQTT (>= la inicial). |
| `SPINE_GATEWAY_STATUS_INTERVAL_S` | `30.0` | Període (s) del missatge `status` (també es publica tan bon punt canvia alguna cosa rellevant). |
| `SPINE_GATEWAY_CLOCK_SYNC_INTERVAL_S` | `60.0` | Període (s) del missatge `clock`. |
| `SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS` | `20.0` | Error estimat (ms) de l'àncora de rellotge: hora d'arribada del tick al gateway (latència USB + lectura de línia). |
| `SPINE_GATEWAY_STALL_TIMEOUT_S` | `2.0` | Un stream sense lots durant aquest temps (s) passa a `stalled` i el dispositiu a `degraded`. |
| `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S` | `5.0` | Durada (s) d'un `calibrate` sense `duration_s`. |
| `SPINE_GATEWAY_EMG_STREAM_ID` | `emg_lat_left` | `stream_id` de l'EMG. |
| `SPINE_GATEWAY_IMU_STREAM_ID` | `imu_t9` | `stream_id` de l'IMU. |
| `SPINE_GATEWAY_STRETCH_STREAM_ID` | `stretch_spine` | `stream_id` del cordó elàstic. |
| `SPINE_GATEWAY_EMG_MUSCLE` | `latissimus_dorsi` | Múscul on va l'elèctrode EMG (enum `Muscle` del contracte); va al calibratge `emg_mvc`. |
| `SPINE_GATEWAY_EMG_SIDE` | `left` | Costat de l'elèctrode (`left`, `right` o `midline`). |
| `SPINE_GATEWAY_ADC_BITS` | `10` | Resolució de l'ADC (l'ATmega328P té 10 bits). |
| `SPINE_GATEWAY_VREF_V` | `5.0` | Tensió de referència de l'ADC (V). |
| `SPINE_GATEWAY_EMG_INPUT_PIN` | `A0` | Pin d'entrada de l'EMG, declarat al chunk (`acquisition.input_pin`). |
| `SPINE_GATEWAY_STRETCH_INPUT_PIN` | `A1` | Pin d'entrada del cordó, declarat al chunk (`circuit.input_pin`). |
| `SPINE_GATEWAY_STRETCH_SERIES_RESISTOR_OHM` | `10000.0` | Resistència fixa del divisor del cordó (ohm); serveix per calcular `resistance_ohm`. |
| `SPINE_GATEWAY_IMU_ACCEL_RANGE_G` | `4` | Rang de l'acceleròmetre (g) usat per convertir comptes: 2, 4, 8 o 16. Ha de coincidir amb el firmware (fix a 4). |
| `SPINE_GATEWAY_IMU_GYRO_RANGE_DPS` | `500` | Rang del giroscopi (°/s): 250, 500, 1000 o 2000. Ha de coincidir amb el firmware (fix a 500). |
| `SPINE_GATEWAY_IMU_DLPF_HZ` | `44` | Filtre passa-baix digital del MPU-6050, declarat al chunk (`config.dlpf_hz`). El firmware configura 44 Hz. |
| `SPINE_GATEWAY_EMG_GAIN` | `1.0` | Guany del mòdul EMG; els mV es divideixen per ell. |
| `SPINE_GATEWAY_EMG_SIGNAL_KIND` | `envelope` | Què lliura el mòdul EMG a l'ADC: `raw`, `rectified` o `envelope`. El valor per defecte és una suposició, no està confirmat amb la placa (vegeu a sota). |
| `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` | `650.0` | Longitud (mm) del cordó en repòs, usada pel calibratge `stretch`. PENDENT mesura Aguado (ohmímetre): marcador de ~650 mm per al cordó C3->S1 (#12). |
| `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` | `250` | Finestra (ms) de l'RMS lliscant del calibratge `emg_mvc`. |

El protocol declara dos mòduls EMG (dorsal ample esquerre/dret) però el maquinari i el gateway actuals en publiquen un (`emg_lat_left`, `SPINE_GATEWAY_EMG_STREAM_ID` / `SPINE_GATEWAY_EMG_SIDE`) fins que Polfer confirmi el maquinari (#13).

`SPINE_GATEWAY_EMG_SIGNAL_KIND` fixa `signal_kind` als chunks EMG i al calibratge `emg_mvc`, i decideix com es calculen els mV i la qualitat:

- `envelope` (per defecte) i `rectified`: senyal unipolar. mV = `comptes / 1023 * vref * 1000 / gain`. `clipping` només al màxim de l'ADC (0 és un múscul en repòs, un valor sa).
- `raw`: senyal bipolar que se suposa polaritzat a mitja escala (`(2^bits - 1) / 2`, 511,5 comptes). El gateway resta aquest offset abans de convertir a mV, i l'RMS de la MVC es calcula sobre el senyal amb l'offset restat. `clipping` a 0 i al màxim. La polarització a mitja escala és una suposició sense verificar.

## Comportament MQTT

Els topics es construeixen sempre amb `spine_contracts.topics` (arrel `asclepius/v1/crew/{astronaut_id}/device/{device_id}/...`). Tots els missatges són QoS 1. Un dispositiu per procés.

| Topic | Direcció | Retained | Contingut |
|---|---|---|---|
| `.../stream/{stream_id}` | gateway -> bus | no | `StreamChunk` (EMG, IMU, cordó). |
| `.../status` | gateway -> bus | sí | `DeviceStatus`. |
| `.../clock` | gateway -> bus | sí | `ClockSync`. |
| `.../calibration/{stream_id}` | gateway -> bus | sí | Calibratge vigent del stream. |
| `.../ack` | gateway -> bus | no | `CommandAck`. |
| `.../cmd` | bus -> gateway | no | `Command` (el gateway s'hi subscriu). |

No es publica res del dispositiu fins que arriba el primer hello o tick després d'obrir el port (obrir-lo reinicia l'Uno).

### Estat

`status` es publica retained cada `status_interval_s`, just després de cada connexió MQTT i tan bon punt canvia alguna cosa rellevant (estat global, `boot_id` nou, un stream que s'atura o torna, canvi de freqüència). Porta `source: device`, `boot_id`, `uptime_ms`, `firmware_version` (del hello), `link: usb_serial` i una entrada per stream amb `state`, `sample_rate_hz` i `last_seq`. Sense bateria ni temperatura: l'Arduino no les mesura.

- Estat global: `offline` amb l'enllaç sèrie caigut o sense cap hello/tick des que es va obrir el port; `degraded` si algun stream està `stalled`; si no, `ok`.
- Estat de stream: `active` (va arribar un lot fa menys de `stall_timeout_s`), `stalled` (participa i porta `stall_timeout_s` sense lots, o mai en va enviar passat aquest marge; per exemple el firmware no emet trames `I` si el MPU-6050 no respon) i `disabled` (no participa, és a dir, l'EMG fora d'una sessió que l'inclogui, o encara sense lots i sense haver passat el marge).
- Last Will: `offline` retained, QoS 1, registrat en connectar. Una aturada neta (SIGTERM, Ctrl+C) publica `offline` retained de manera explícita, perquè el Last Will només salta si es perd la connexió.

### Sincronització de rellotge

`clock` porta `method: gateway_receive_time`: els `millis()` de l'últim tick del dispositiu i l'hora UTC a la qual va arribar al gateway, amb `uncertainty_ms = SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS`. Es publica després de cada connexió MQTT, cada `clock_sync_interval_s`, en començar una sessió i en el primer tick després d'un `boot_id` nou.

### Chunks

- **Sessió** (entre `start_session` i el seu final): un chunk per lot de 100 ms, a freqüència nativa: EMG 1000 Hz (100 mostres), IMU 100 Hz (10), cordó 20 Hz (2). Si s'omet `stream_ids` participen els tres. Un stream que no és a la sessió continua com a fons.
- **Fons** (fora de sessió, només IMU i cordó): la mitjana de cada lot, 10 lots contigus per chunk, chunks d'1 s a 10 Hz. L'EMG no es publica en fons. El seu `session_id` és un uuid5 determinista del dispositiu i del dia UTC (canvia a les 00:00 UTC).
- **`seq`**: per `boot_id` i stream, comença a 0 i el comparteixen sessió i fons. Un `boot_id` nou el reinicia.
- **Forats**: cada lot perdut consumeix un `seq`; el següent chunk de sessió declara `dropped_samples` (lots perduts x mostres per lot), porta el flag `packet_gap` i una puntuació de qualitat com a màxim de 0,7. En fons un forat descarta la finestra parcial, consumeix el `seq` de les finestres d'1 s que toca i marca `packet_gap` (sense `dropped_samples`). Els lots duplicats, els que arriben desordenats i els que porten un nombre de mostres inesperat es descarten.
- **Unitats**: EMG en mV a l'entrada de l'ADC (vegeu `EMG_SIGNAL_KIND`); IMU en g i °/s amb les sensibilitats nominals del MPU-6050 per al rang configurat; cordó en `adc_counts` més `resistance_ohm = Rs * comptes / (max - comptes)`. Si alguna mostra d'un chunk del cordó és a 0 o al màxim (curtcircuit o circuit obert) el chunk omet `resistance_ohm` i `length_mm`. `length_mm` i `calibration_id` apareixen només mentre hi ha un calibratge `stretch` vigent (corba lineal per trams, retallada als extrems amb el flag `out_of_calibration`).
- **Qualitat** (`assessed_by: gateway`): `score` = mostres sense problema / mostres. Flags: `clipping` (EMG al màxim de l'ADC, i a 0 si és `raw`; cordó a 0 o al màxim; IMU a -32768 o 32767 en algun eix), `lead_off` (cordó al màxim: circuit obert), `packet_gap` i `out_of_calibration`.

### Comandes

El gateway se subscriu a `.../cmd`. Tipus: `start_session`, `stop_session`, `mark_event` i `calibrate`. Cada comanda rep acks a `.../ack`:

| Comanda | Acks, en ordre |
|---|---|
| `start_session` | `accepted`; més tard `completed` quan acaba la sessió (`stop_session` o `max_duration_s`). |
| `stop_session` | `accepted`; `completed` de la comanda `start_session`; `completed` del `stop_session`. |
| `mark_event` | `accepted`, `completed`. Sense efecte al dispositiu. |
| `calibrate` | `accepted`; el calibratge es publica retained; `completed` amb `result.calibration_id` (el del primer stream), o `failed`. |

Rebuigs (`rejected`, `error.code`):

| Codi | Quan |
|---|---|
| `busy` | `start_session` amb una sessió en curs; `calibrate` amb un altre calibratge en curs. |
| `unknown_stream` | Un `stream_id` que no existeix; a `calibrate`, un stream d'un altre tipus (`stretch` necessita el stream del cordó, `emg_mvc` el de l'EMG). |
| `no_session` | `stop_session` o `mark_event` d'una sessió que no és la que corre. |
| `device_offline` | `start_session` o `calibrate` amb el dispositiu sense connectar. |
| `invalid_command` | Payload invàlid (JSON Schema), ids que no coincideixen amb el topic, una durada de més de 30 dies, un payload de més de 16 KB, o `calibrate` de tipus `imu`. |
| `internal` | Error inesperat (l'ack és `failed`). |

Altres regles:

- Un payload sense `command_id` (UUID) llegible no es pot respondre i s'ignora. Un payload de més de 16 KB (16384 bytes) ni es parseja: si se'n troba el `command_id` es rebutja amb `invalid_command`.
- **Deduplicació:** es recorden els últims 256 `command_id`. Una reentrega QoS 1 rep de nou els mateixos acks i no s'executa dues vegades.
- `calibrate` admet `stretch` i `emg_mvc`; `imu` es rebutja amb `invalid_command` (l'enum d'`error.code` no té "no suportat"). Grava durant `duration_s` (per defecte `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S`) de **temps de dispositiu**; no exigeix que `session_id` sigui la sessió en curs. Si les dades no són completes 5 s després de `duration_s`, falla.
- `calibration_failed` i `calibration_timeout` viatgen tots dos com a `error.code = "calibration_failed"`; el detall va al missatge: `<motiu>: <detall>`, amb motiu `incomplete`, `rest_resistance_not_computable`, `too_few_samples`, `flat_signal` o `calibration_timeout`. Els missatges es tallen a 300 caràcters.
- Els calibratges viuen a la memòria del gateway i es republiquen retained després de cada reconnexió MQTT, abans del següent chunk que els citi. Reiniciar el gateway arrenca sense calibratge.
- `stretch`: `rest_resistance_ohm` és la resistència calculada amb la mitjana dels comptes; `rest_length_mm` surt de la configuració; la corba és l'**aproximació per defecte**, una lineal `R = R0 * L / L0` amb punts a `L0 * {1.0, 1.25, 1.5, 1.75, 2.0}`. No està mesurada: el cordó no és lineal i té histèresi, i la corba mesurada de #12 l'ha de substituir.
- `emg_mvc`: `mvc_rms_mv` és el màxim de l'RMS lliscant (`SPINE_GATEWAY_EMG_MVC_WINDOW_MS`) en mV.

## Protocol sèrie

Línies ASCII acabades en `\n`, cadascuna `<cos>*<XX>` amb `XX` el CRC-8/MAXIM del cos en 2 hex majúscula. El base64 és estàndard amb padding. Les línies de més de 1024 caràcters es descarten.

| Línia | Contingut |
|---|---|
| `H,1,<boot_id>,<fw_version>,<millis>` | Hello després de l'arrencada (`1` = versió del protocol). |
| `T,<boot_id>,<millis>` | Tick cada 1000 ms, àncora del rellotge del dispositiu. |
| `D,E,<frame_seq>,<t0_ms>,100,<b64>` | EMG: 100 x `uint16` LE, 1000 Hz, cada 100 ms. |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | IMU: 10 x (ax, ay, az, gx, gy, gz) `int16` LE, 100 Hz. |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Cordó: 2 x `uint16` LE, 20 Hz. |
| `# ...` | Text de depuració del firmware. No porta CRC, així que el gateway no l'interpreta: el registra al log a nivell info (`arduino: #imu ok`) i no el compta com a línia dolenta. |

Les línies no vàlides (CRC dolent, camp dolent, base64 dolent...) es compten per motiu, s'avisen a la 1a, 2a, 4a, 8a... ocurrència (la resta a nivell debug) i es descarten. El detall complet (cablejat, comportaments del firmware, exemples) és a [`firmware/README.ca.md`](../../firmware/README.ca.md).

## Tests

Des de `spine_monitor/`:

```bash
uv run pytest gateway/tests -q -p no:randomly                  # tot; els tests de broker se salten sense broker
uv run pytest gateway/tests -q -p no:randomly -m mosquitto     # només els que fan servir un broker real
```

Els tests `mosquitto` i l'E2E amb emulador (`test_e2e_emulator_mosquitto.py`) necessiten un broker a `127.0.0.1:1883` (`docker compose -f app/docker-compose.yml up -d mosquitto`); sense ell se salten. Amb `SPINE_REQUIRE_BROKER=1` fallen en lloc de saltar-se, que és com els executa el gate. Fan servir device ids propis (`itest-*`) i esborren els retained que deixen.

Gate de qualitat (ruff, pyright, tests amb cobertura i mutació incremental), igual en local i a CI:

```bash
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
```

El paquet `gateway` és nou al workspace, així que la imatge `spine-quality` s'ha de reconstruir una vegada abans de la primera execució: `docker compose -f app/docker-compose.yml build quality`.

Firmware (test de paritat del protocol contra els vectors daurats més compilació): `docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build`.

## Cablejat i prova amb la placa real

Per quan hi hagi Arduino. Res d'aquesta secció s'ha provat en hardware.

### Pins

| Senyal | Pin de l'Arduino | Notes |
|---|---|---|
| Mòdul EMG `SIG` | `A0` | Vegeu els avisos d'alimentació més avall. |
| Cordó elàstic | `A1` | Divisor: 5 V -> resistència de 10 kohm -> punt mig (`A1`) -> cordó -> GND. |
| MPU-6050 `SDA` | `A4` | I2C, adreça 0x68. |
| MPU-6050 `SCL` | `A5` | |
| MPU-6050 `VCC` / `GND` | 5 V (GY-521) / GND | |
| `A3` | sense connectar | Es deixa flotant a propòsit: el seu soroll alimenta el `boot_id`. |

Elèctrodes de l'EMG (tres, connectats al mòdul EMG, no a l'Arduino): dos diferencials sobre el ventre muscular, alineats amb la direcció de les fibres, i un de referència en una zona òssia neutra.

### Seguretat

- El mòdul EMG probablement necessita **alimentació dual** (`+Vs` i `-Vs`) i la seva sortida `SIG` pot superar els 5 V. Mesura amb un multímetre, en repòs i amb una contracció màxima, i limita el senyal a 5 V (i per sobre de 0 V) abans que arribi a `A0`: l'entrada de l'ATmega328P no tolera més de Vcc ni tensions negatives.
- La placa GY-521 accepta 5 V (porta regulador); un xip MPU-6050 nu és de 3,3 V.
- No connectis elèctrodes a una persona fins a haver verificat l'alimentació i l'aïllament del mòdul.

### Pas a pas

1. Puja el sketch amb l'Arduino IDE: obre `firmware/spine_wearable/spine_wearable.ino`, placa Arduino Uno o Nano (en clons Nano tria el processador "ATmega328P (Old Bootloader)"), tria el port i puja.
2. Obre el Monitor Sèrie a **250000 baud**. Has de veure un `H,...` una vegada, `T,...` cada segon, `D,E`/`D,I`/`D,S` cada 100 ms i `#imu ok` (o `#imu absent` si el MPU-6050 no respon).
3. **Tanca el Monitor Sèrie**: el port és exclusiu i el gateway no pot obrir-lo mentre el tingui l'IDE.
4. Aixeca el broker: `docker compose -f app/docker-compose.yml up -d mosquitto`.
5. Executa el gateway natiu (vegeu a dalt) amb el port correcte.
6. Observa el bus amb `mosquitto_sub` (vegeu Notes) o amb un petit script d'aiomqtt: `status` ha de passar a `ok` i han d'arribar cada segon chunks de fons d'`imu_t9` i `stretch_spine`.
7. Publica les comandes (JSON que validi contra `telemetry/command`) a `.../cmd`: `start_session` (chunks a 100 ms i apareix l'EMG), `calibrate` de tipus `stretch` i `emg_mvc` (repòs per al primer, una contracció màxima per al segon), `stop_session`. Comprova els acks i que els calibratges queden retained.
8. Comprova el cordó (resistència en repòs i el seu canvi en estirar) i l'IMU (al voltant d'1 g a l'eix vertical en repòs).

### Tancar "EMG crua o envolvent" (#13)

Amb el mòdul connectat, mesura `SIG` amb un multímetre (o mira `emg` en una sessió):

- Si `SIG` és en repòs al voltant de 0 V i puja en contraure el múscul, el mòdul lliura **envolvent**: deixa `SPINE_GATEWAY_EMG_SIGNAL_KIND=envelope`.
- Si `SIG` oscil·la al voltant d'una tensió de repòs, lliura senyal **crua**: posa `SPINE_GATEWAY_EMG_SIGNAL_KIND=raw` (i comprova que la tensió de repòs és de debò la mitja escala, que és el que suposa el gateway).

## Punts oberts

- EMG crua o envolvent (`SPINE_GATEWAY_EMG_SIGNAL_KIND`), pendent del mòdul real.
- Alimentació del mòdul EMG i rang real de `SIG`.
- `rest_length_mm` i la corba real del cordó (#12); avui són un marcador de posició i una aproximació lineal.
- `calibrate` de tipus `imu` no està implementat.
- Un codi d'error propi per a "no suportat": requereix canviar el contracte (avui es fa servir `invalid_command`).
- Autenticació i TLS del broker (#96).
- BLE queda fora d'abast.
