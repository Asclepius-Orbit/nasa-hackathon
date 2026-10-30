[English](README.md) · [Español](README.es.md) · **Català**

# spine-physiology

Generador sintètic de missió. Publica el que publicaria el maquinari real: `StreamChunk` v1 amb `source: simulator` (EMG, IMU i cordó elàstic), sessions, calibratges, estat del dispositiu, sincronització de rellotge, autoinformes diaris, context diari, perfils d'astronauta i, a més, la **veritat de terreny** (el que realment va passar) per poder avaluar el motor contra el simulador.

## Disseny

- **Nucli pur.** `MissionGenerator` és un iterador mandrós d'`Envelope`s (un missatge de contracte més el seu topic, l'indicador retained i l'instant UTC `at`). No fa E/S.
- **Sinks.** `JsonlSink` ho escriu tot a disc; `MqttSink` publica només els missatges que tenen topic (chunks, estat del dispositiu, rellotge, acks de comandes i calibratges, aquests retained). El gateway fals reutilitza el mateix nucli en temps real.
- **Contractes.** Els payloads es construeixen amb els models de `spine_contracts` i es validen contra els seus JSON Schemas; `source` és sempre `simulator`.

## Sense valors clínics al codi

Tot paràmetre numèric, fisiològic o tècnic, és obligatori i surt del YAML de missió: el codi no té valors per defecte. Els camps opcionals del contracte s'escriuen com un `null` explícit. `configs/example-mission.yaml` és un **PLACEHOLDER**: números inventats perquè el simulador funcioni d'extrem a extrem, no referències clíniques. Els valors reals l'han de substituir. Les hores del dia van entre cometes (`"07:00"`) i els exponents s'escriuen `1.0e-3` (PyYAML llegeix `1e-3` com a text); el carregador és estricte i falla en lloc de convertir en silenci.

## Configuració

`load_config(path)` valida el YAML amb Pydantic (les claus desconegudes es rebutgen). Blocs de `configs/example-mission.yaml`:

| Bloc | Què defineix |
|---|---|
| `seed`, `start_date`, `start_mission_day` | llavor mestra; data del dia 0; dia de missió del dia 0 (llançament = 0) |
| `segments` | trams consecutius (`days`, `phase`, `gravity_environment`): preflight 1 g, inflight microgravetat, postflight 1 g |
| `crew` | per astronauta: ids, `latent_multipliers`, `exercise_adherence`, línia base (longitud de repòs del cordó, MVC d'EMG per stream) i l'`AstronautProfile` |
| `device` | firmware, enllaç, encesa i apagada diàries (UTC), bateria, temperatura, intervals d'estat i de sincronització de rellotge, streams amb la seva col·locació |
| `protocols` | protocols d'exercici (repeticions, durades, %MVC, ROM, càrrega) i de control de columna |
| `schedule` | sessions per dia (`time_utc`, jitter, fases, `every_n_days`) i la finestra opcional de monitorització de fons (`background.chunk_ms`, obligatori: durada del chunk del stream de fons d'IMU + cordó, separada de `signals.chunk_ms`; l'exemple usa 1000 ms) |
| `calibration` | calibratges de cordó, MVC d'EMG i IMU: periodicitat, soroll, punts |
| `curves` | trajectòria típica dels quatre latents diaris per dia de missió (punts per trams), `daily_sd` i `dose_drift_per_day` |
| `signals` | `chunk_ms` i la síntesi d'EMG, IMU i cordó (freqüències, soroll, filtres, ADC, histèresi...) |
| `faults` | `quality` (una entrada per flag de qualitat) i `device` (pèrdua de paquets, reinicis, rellotge) |
| `daily_context` | exercici planificat, contramesures i medicació per fase |
| `self_report` | hora, model de NRS de dolor i rigidesa, localitzacions del dolor, son, banderes vermelles |

Els quatre latents diaris són `spine_elongation` (mm sobre la longitud de repòs preflight), `muscle_deconditioning`, `rom_motor_control` i `pain_stiffness` (0 = preflight, 1 = deteriorament màxim modelat). Valor del dia = multiplicador de l'astronauta x corba(dia de missió) + deriva per dosi + N(0, `daily_sd`). Les corbes són la trajectòria típica *amb* l'exercici planificat; cada dia la deriva suma `dose_drift_per_day x (1 - minuts reals / minuts planificats)`.

## Inici ràpid

Des del workspace `spine_monitor`. L'índex de dia `d` és la data `start_date + d`; la missió d'exemple té 14 dies (0 a 13).

```python
from pathlib import Path
from spine_physiology import MissionGenerator, load_config
from spine_physiology.sinks.jsonl import write_jsonl

config = load_config("physiology/configs/example-mission.yaml")
gen = MissionGenerator(config)                 # llavor del YAML; MissionGenerator(config, seed=7) la substitueix

# Tot un astronauta en un dia, regenerat per separat (mateixos payloads que a la missió completa)
for env in gen.day_messages(5, "AST-01"):
    print(env.kind.value, env.topic, env.at)  # tipus, topic MQTT o None, instant UTC
    break

# A disc: una carpeta per astronauta i dia (la missió completa és gen.iter_messages())
n = write_jsonl(gen.day_messages(5, "AST-01"), Path("out"), config=config, seed=gen.seed)
```

Publicar per MQTT (val qualsevol objecte amb la signatura d'`aiomqtt.Client.publish`; QoS 1, retained per a l'estat del dispositiu i els calibratges). Els objectes de domini s'ometen i no es compten:

```python
import asyncio
import aiomqtt
from spine_physiology.sinks.mqtt import MqttSink, publish_all

async def main() -> None:
    async with aiomqtt.Client("127.0.0.1", 1883) as client:
        sent = await publish_all(gen.day_messages(5, "AST-01"), MqttSink(client))
        print(sent, "messages published")

asyncio.run(main())
```

`spine_physiology.sinks.pacing.paced` pot dosificar un flux a temps real o a `speed` x temps real. Un sol dia de la missió d'exemple són uns 22 600 missatges, gairebé tots chunks (vegeu Límits coneguts); per a un fragment més barat useu `gen.session_messages(ref)` (més avall).

## Reproductibilitat

- Mateixa configuració i mateixa llavor donen els mateixos bytes: el JSONL fa servir JSON compacte, ordre de claus fix, UTF-8 i cap valor de rellotge de paret.
- L'atzar surt de fluxos independents derivats per branca (astronauta, dia, sessió, stream...), així que canviar una branca no desplaça la resta.
- El dispositiu arrenca net cada dia, de manera que res passa d'un dia al següent tret de la deriva per dosi dels latents, que es pot regenerar per dia.
- `gen.day_messages(dia, astronaut_id)` i `gen.session_messages(ref)` donen exactament els mateixos payloads, en el mateix ordre relatiu, que dins de l'execució completa d'`iter_messages()`.

## Terra vs microgravetat (`resimulate`)

La mateixa sessió, regenerada amb una altra gravetat, dona un parell A/B exacte (mateixa llavor, mateixa agenda, mateixos errors; només canvia la física):

```python
from spine_contracts import GravityEnvironment
from spine_physiology import SessionRef

ref = SessionRef("AST-01", 5, "lat_pulldown")        # astronauta, dia, slot = `name` a l'agenda
micro = list(gen.session_messages(ref))              # tal com està configurat (microgravetat els dies inflight)
earth = gen.resimulate(ref, GravityEnvironment.EARTH_1G)
```

`gen.sessions(dia, astronaut_id)` llista els `SessionPlan` d'un dia (`plan.ref`, `session_id`, inici i fi).

## Errors

**Errors de qualitat** (`faults.quality`, una entrada per `QualityFlag`). Cadascun té una probabilitat per chunk d'iniciar un episodi, un rang de durada, un `score` de qualitat, una `amplitude` (relativa al senyal net del canal en aquell chunk: RMS, o pic per a `clipping`) i els `kinds` de stream als quals afecta:

| Flag | Què fa al senyal |
|---|---|
| `clipping` | retalla a +-amplitude x pic |
| `lead_off` | substitueix el senyal per soroll d'amplitude x RMS |
| `motion_artifact` | afegeix una ràfega de baixa freqüència; la banda la fixen `band_low_hz`, `band_high_hz` i `max_fraction_of_rate` |
| `powerline_noise` | afegeix un sinus a `frequency_hz` |
| `low_snr` | afegeix soroll blanc |
| `saturated_adc` | comptes clavats al màxim de l'ADC (o al rang del canal) |
| `out_of_calibration` | afegeix un desplaçament (offset) |
| `sensor_warmup` | afegeix una deriva lenta que decau (`decay_fraction` de la durada de l'episodi) |

Al cordó només s'alteren les `adc_counts` (enters, retallats a l'ADC); `length_mm` es deriva d'elles i en hereta la qualitat. Cada chunk afectat porta el flag i l'score mínim.

**Errors de dispositiu** (`faults.device`):

- `packet_loss`: chunks perduts en ràfegues. També consumeixen `seq`, així que el forat es veu; el primer chunk després d'una pèrdua porta `packet_gap`, i les pèrdues llargues marquen el stream com aturat (stalled) a l'estat del dispositiu.
- `reboot`: reinicis espontanis (Poisson per hora) amb temps offline; després d'un, `seq` i `t0_device_ms` tornen a 0 amb un `boot_id` nou.
- `clock`: deriva de rellotge per arrencada (ppm) i latència de recepció; quan l'error des de l'última sincronització supera el llindar, els chunks porten `clock_uncertain`.

La veritat de terreny registra cada episodi injectat, cada chunk perdut i cada reinici.

## Estructura de la sortida

```
out/
  manifest.json                    configuració, llavor, versions del paquet i del contracte
  AST-01/
    profile.json                   AstronautProfile
    day-012/
      chunks.jsonl  sessions.jsonl  calibrations.jsonl  self_reports.jsonl
      daily_context.jsonl  device_status.jsonl  clock_sync.jsonl
      command_acks.jsonl  ground_truth.jsonl
```

Cada línia és `{"topic", "retained", "at", "payload"}` per als tipus amb topic (chunks, estat, rellotge, acks, calibratges) i `{"at", "payload"}` per als objectes de domini. `JsonlSink` obre els fitxers de manera mandrosa amb un topall de manejadors oberts i escriu `manifest.json` a `close()` (també funciona com a gestor de context). El manifest porta `complete: false` si el sink s'ha tancat per una excepció. Les sessions que no caben entre l'encesa, la sessió anterior i l'apagada es descarten aquell dia.

`ground_truth.jsonl` no té schema de contracte; cada registre porta `type`, `astronaut_id` i `day` (els de sessió també `session_id` i `slot`): `latent_state` (latents diaris i la dosi que els va produir), `device_events` (arrencades, reinicis, deriva), `quality_faults`, `lost_chunks` i `expected_features` (valors veritables de les features del catàleg calculats del model sense soroll de sensor ni errors).

## Gateway fals

`spine-sim-gateway` es comporta en MQTT com el gateway real del wearable, amb un dispositiu per astronauta del config. La configuració són variables d'entorn (o un `.env`):

| Variable | Per defecte | Significat |
|---|---|---|
| `SPINE_SIM_CONFIG` | obligatòria | YAML de missió |
| `SPINE_SIM_SEED` | la del YAML | substitueix la llavor |
| `SPINE_SIM_MQTT_HOST` / `SPINE_SIM_MQTT_PORT` | `127.0.0.1` / `1883` | broker |
| `SPINE_SIM_MQTT_CLIENT_ID` | `spine-sim-gateway` | prefix del client id; cada dispositiu es connecta com `<prefix>-<device_id>` |
| `SPINE_SIM_SPEED` | obligatòria | velocitat del dia de missió: 1 = temps real, 0 = dia fix |
| `SPINE_SIM_START_DAY` | obligatòria | índex de dia de missió en arrencar (0 = primer dia simulat) |
| `SPINE_SIM_MQTT_RECONNECT_INITIAL_S` | `1.0` | primera espera abans de reconnectar amb el broker |
| `SPINE_SIM_MQTT_RECONNECT_FACTOR` | `2.0` | multiplicador de l'espera després de cada intent fallit (>= 1) |
| `SPINE_SIM_MQTT_RECONNECT_MAX_S` | `30.0` | topall de l'espera (>= la inicial) |

```bash
SPINE_SIM_CONFIG=physiology/configs/example-mission.yaml SPINE_SIM_SPEED=1 SPINE_SIM_START_DAY=0 \
  uv run spine-sim-gateway
```

- Se subscriu al topic de comandes de cada dispositiu i respon al topic d'acks. Les comandes es validen contra el JSON Schema. Admet `start_session`, `stop_session`, `mark_event` i `calibrate`. Tota comanda acceptada rep `accepted` i després `completed`; `start_session` es rebutja (`rejected`) amb `busy`, `unknown_stream` o `device_offline`, i `stop_session` i `mark_event` amb `no_session` quan no hi ha res a aturar o marcar. `mark_event` no canvia el senyal; `calibrate` retorna un `calibration_id` i el calibratge es publica al bus just abans de l'ack `completed`. Una comanda reenviada (mateix `command_id`) rep els mateixos acks sense tornar-se a executar; les comandes mal formades, massa grans (> 16 KiB) o amb durades absurdes (> 30 dies) es rebutgen com a `invalid_command` i mai aturen el gateway.
- `DeviceStatus` i `ClockSync` retained en arrencar i de manera periòdica, un Last Will amb `status: offline` i el mateix `offline` en aturar-se netament. Per això hi ha **una connexió MQTT per dispositiu**: MQTT només admet un Last Will per connexió.
- Els calibratges es publiquen retained (QoS 1) a `asclepius/v1/crew/{astronaut_id}/device/{device_id}/calibration/{stream_id}`: l'últim vigent per stream. A més de la comanda `calibrate`, abans d'un chunk que cita un `calibration_id` el gateway republica aquest calibratge si no és el que està retained al bus, així un consumidor que arriba tard sempre el pot resoldre.
- Reconnexió: si el broker rebutja o talla la connexió, el gateway espera amb backoff exponencial (els paràmetres `SPINE_SIM_MQTT_RECONNECT_*`) i es torna a connectar amb el mateix estat (sessió, fons, `seq`). En cada connexió es torna a subscriure al topic de comandes i republica l'estat `ok` del dispositiu i l'ancoratge de rellotge. Un publish fallit es reintenta a la connexió següent (sense forat de `seq`; en QoS 1 pot haver-hi duplicats). La síntesi pesada corre en un fil de treball, així que el keepalive de MQTT no es bloqueja. `last_seq` de `DeviceStatus` és l'últim `seq` publicat de cada stream.
- Si `schedule.background.enabled`, els streams de fons d'IMU i cordó funcionen sempre que no hi hagi sessió (la finestra horària diària del config s'ignora, perquè les demos valguin a qualsevol hora). Una sessió pausa el fons (mateix `stream_id`, el `seq` continua) i després es reprèn amb el mateix `session_id`.
- Dia de missió = `start_day` + dies de rellotge de paret transcorreguts x `speed`, acotat a la missió. Una sessió mai canvia de dia a mig camí.
- Quan s'esgota la bateria de la finestra diària d'encesa i no hi ha sessió, el dispositiu es "recarrega": `boot_id` nou, `seq` i rellotge a 0, bateria plena.

## Docker

El servei `simulator` és al perfil `sim` d'`app/docker-compose.yml` (necessita el broker, que arrenca amb ell: `mosquitto` té un healthcheck i `simulator` l'espera amb `condition: service_healthy`). Des de l'arrel del repo:

```bash
docker compose -f app/docker-compose.yml --profile sim up --build
```

Executa `spine-sim-gateway` amb la missió d'exemple (`SPINE_SIM_CONFIG=/app/configs/example-mission.yaml`), velocitat 1 i dia d'inici 0. La imatge es construeix amb `spine_monitor/physiology/Dockerfile` (context: l'arrel del repo) i s'executa amb un usuari sense privilegis.

## Tests

```bash
uv run pytest physiology
```

Els tests que necessiten un broker real porten el marcador `mosquitto` i es salten si res escolta a `127.0.0.1:1883` (per exemple, `docker run -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf` els activa). La resta de tests del gateway fan servir un client MQTT fals i temps virtual.

## Límits coneguts

- Volum del fons: amb `schedule.background.chunk_ms` a 1000 ms, cada hora de fons són uns 3 600 chunks per stream (10 vegades menys que amb chunks de 100 ms); continua creixent amb la finestra. La configuració d'exemple limita el fons a una hora al dia.
- El qüestionari ODI setmanal està ajornat: només es genera l'autoinforme diari.
- Sessions, autoinformes, context diari, perfils i veritat de terreny no tenen topic MQTT: només existeixen en JSONL (els objectes de domini mai van al bus). Els calibratges sí que van al bus.
- Tots els números de la configuració d'exemple són placeholders, no referències clíniques.
