[English](README.md) · [Español](README.es.md) · **Català**

# Firmware del wearable

Sketch Arduino Uno / Nano (ATmega328P, 16 MHz, 2 KB de RAM) que mostreja tres sensors i emet
SEMPRE per USB sèrie (250000 baud, 8N1) el protocol de chunks v1. No espera ordres del host.
Qui el llegeix és el gateway real (`spine_monitor/gateway`, vegeu [el seu README](../spine_monitor/gateway/README.ca.md)),
que el publica al bus MQTT v1.

```
firmware/
  spine_wearable/spine_wearable.ino   sketch (mostreig per Timer1 + ADC per interrupció)
  spine_wearable/protocol.h           codificació pura (CRC-8, base64, trames), sense Arduino
  tests/host_protocol_test.c          test de paritat al host contra els vectors daurats
  tests/golden_lines.txt              vectors daurats del protocol
  Dockerfile                          arduino-cli + core avr fixats, gcc per al test
```

## Cablejat

| Sensor | Pin de l'Arduino | Notes |
|---|---|---|
| EMG (Muscle Sensor V3, sortida SIG) | A0 | Se suposa envolvent analògica (sense confirmar amb el mòdul, vegeu més avall); 1000 Hz |
| Cordó elàstic resistiu (Adafruit 519) | A1 | Divisor: 5 V -> resistència de 10 kΩ -> punt mig (A1) -> cordó -> GND; 20 Hz |
| MPU-6050 SDA | A4 | I2C, adreça 0x68, 400 kHz; 100 Hz |
| MPU-6050 SCL | A5 | |
| MPU-6050 VCC / GND | 5 V (o 3.3 V segons el mòdul) / GND | A confirmar amb el multímetre segons el mòdul (regulador i pull-ups a bord) |
| A3 | sense connectar | Es deixa flotant a propòsit: el seu soroll alimenta el `boot_id` |

Els tres elèctrodes de l'EMG (2 diferencials + referència) van al mòdul, no a l'Arduino.

Colocació (protocol estandarditzat de politja al pit): el MPU-6050 va a T9 i el cordó elàstic va de C3 a S1 (~650 mm en repòs, PENDENT mesura Aguado amb ohmímetre). El protocol declara dos mòduls EMG (dorsal ample esquerre i dret), però el maquinari actual té un sol canal EMG (A0) i el gateway en publica un (`emg_lat_left`, `SPINE_GATEWAY_EMG_STREAM_ID` / `SPINE_GATEWAY_EMG_SIDE`) fins que Polfer confirmi el maquinari (#13). El format de trama sèrie no canvia. Nota permanent d'elèctrodes: vegeu el protocol als contractes.

### Alimentació del mòdul EMG (a confirmar)

Els pins del mòdul són `+Vs`, `GND`, `-Vs` i `SIG`. És molt probable que el mòdul necessiti
**alimentació dual** (+Vs i -Vs) i que `SIG` pugui superar els 5 V. Res d'això està verificat:
**a confirmar amb el multímetre** abans de connectar `SIG` a la placa (tensió d'alimentació
admesa, rang real de `SIG` en repòs i amb contracció màxima).

L'entrada A0 de l'ATmega328P no tolera més de Vcc (5 V) ni tensions negatives: la sortida cap a A0
s'ha de limitar a 5 V (per exemple divisor resistiu més un díode Zener o de clamp a 5 V) si el
multímetre mostra que `SIG` surt de 0..5 V.

També està sense confirmar si el mòdul lliura envolvent o senyal crua: el firmware mostreja A0 tal qual
i no ho distingeix; el gateway ho declara amb `SPINE_GATEWAY_EMG_SIGNAL_KIND` (per defecte `envelope`).

## Protocol

Línies ASCII acabades en `\n`. Cada línia és `<cos>*<XX>`, amb `XX` el CRC-8/MAXIM del cos
(polinomi reflectit 0x8C, init 0, sense xorout) en 2 hex majúscula. Comprovació: `CRC8("123456789") = 0xA1`.

| Línia | Quan | Contingut |
|---|---|---|
| `H,1,<boot_id u32>,<fw_version>,<millis u32>` | En arrencar (l'Uno es reinicia en obrir el port) | `fw_version` = `0.1.0` |
| `T,<boot_id>,<millis>` | Cada 1000 ms | |
| `D,E,<frame_seq u16>,<t0_ms u32>,100,<b64>` | Cada 100 ms | 100 × uint16 LE (`analogRead`, 0..1023) a 1000 Hz |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | Cada 100 ms | 10 × (ax, ay, az, gx, gy, gz) int16 LE a 100 Hz |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Cada 100 ms | 2 × uint16 LE a 20 Hz |
| `# ...` | Esporàdica | Depuració: `#imu ok`/`#imu absent` en arrencar, `#imu up`/`lost`/`late`, `#overrun`, `#adc busy`. Sense CRC: el gateway les registra al log a nivell info (`arduino: ...`) i no les compta com a línia dolenta |

- `frame_seq` és independent per tipus, comença a 0 i dona la volta a 65536.
- `t0_ms` és el `millis()` de l'instant de la **primera mostra** del lot (es pren a la ISR, no en emetre).
- El MPU-6050 es configura amb `PWR_MGMT_1=0`, DLPF 44 Hz (`CONFIG=0x03`), ±500 dps (`GYRO_CONFIG=0x08`) i ±4 g (`ACCEL_CONFIG=0x08`).
- Base64 estàndard amb padding.

Exemples (vectors daurats, els mateixos que comprova el test):

```
H,1,2893746120,0.1.0,1234*15
T,2893746120,5000*76
D,E,7,1000,4,AAABAAAC/wM=*09
D,I,3,2000,2,ZACc/wAgAAD///9/AIAFAPv/QQC//+gD*E6
D,S,65535,4294967295,2,/wEAAg==*E8
```

### Comportaments a tenir en compte

- **MPU absent en arrencar:** no s'emeten trames `I`, es continua amb `E`, `S` i `T`, i s'avisa amb
  `#imu absent`. Cada segon es reintenta la configuració; si respon, apareix `#imu up` i comencen
  les trames `I`. El gateway veurà el stream `I` com a `stalled`. El mateix passa si el MPU es perd
  en marxa (més de 20 lectures fallides seguides, `#imu lost`).
- **Lot sense emetre quan arriba el següent (overrun):** es descarta el nou, es compta, s'avisa una
  vegada amb `#overrun` i el seu `frame_seq` es consumeix, així que el host veu un salt de `frame_seq`.
- **IMU amb `loop()` endarrerit:** si s'acumulen períodes de 10 ms sense llegir, es llegeix una vegada i aquesta
  mostra es repeteix als forats (cada forat conserva la seva posició al lot; res no es desplaça). S'avisa amb `#imu late`.
- **`boot_id`:** u32 que canvia a cada arrencada. S'obté barrejant (MurmurHash3 fmix32) un
  comptador d'arrencades guardat a l'EEPROM[0..3], el bit baix de 32 lectures de l'ADC a l'A3 flotant
  i `micros()`.
- **Si el host no llegeix:** l'Arduino no es bloqueja; el xip USB-sèrie descarta el que no es recull
  (comportament del pont, a confirmar a la placa concreta).

## Compilar amb Docker

Des de l'arrel del repo:

```bash
docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build
```

Executa, en aquest ordre, i falla si qualsevol falla:

1. Test de paritat al host (gcc): `protocol.h` ha de reproduir els vectors de `tests/golden_lines.txt`
   byte a byte i `CRC8("123456789") == 0xA1`.
2. `arduino-cli compile --fqbn arduino:avr:uno --warnings all firmware/spine_wearable`, que imprimeix
   l'ús de flash i RAM.

La imatge fixa `arduino-cli` 1.6.0 (sha256 verificat) i el core `arduino:avr@1.8.6`. Sense llibreries de
tercers: només `Wire` i `EEPROM` del core. El servei munta `firmware/` des del host, així que no cal
reconstruir la imatge en editar el codi (sí en canviar el `Dockerfile`: afegeix `--build`).

El context de build és l'arrel del repo (`context: ..` al compose). Si Docker falla en llegir-lo per
carpetes il·legibles (a Windows, restes de directoris temporals amb permisos trencats), elimina-les o
construeix des d'un clon net.

## Pujar amb l'Arduino IDE

1. Obrir `firmware/spine_wearable/spine_wearable.ino` (la carpeta s'ha de dir igual que el `.ino`).
2. Eines > Placa: **Arduino Uno** o **Arduino Nano** (en un Nano, el processador segons el seu
   bootloader: ATmega328P o ATmega328P (Old Bootloader)).
3. Triar el port i prémer Puja.
4. Monitor sèrie a **250000 baud** per veure les línies. Tancar-lo abans d'arrencar el gateway: el port és exclusiu.

El cablejat, la prova amb la placa real i els avisos de seguretat pas a pas són al
[README del gateway](../spine_monitor/gateway/README.ca.md).
