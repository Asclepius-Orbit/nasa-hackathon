/*
 * spine_wearable - Firmware del wearable (Arduino Uno / Nano, ATmega328P, 16 MHz, 2 KB RAM).
 *
 * Muestrea 3 sensores y emite SIEMPRE por USB serie (250000 baud, 8N1) el protocolo de
 * chunks v1 (ver protocol.h y firmware/README.md). No espera ordenes del host.
 *
 *   EMG    A0  analogico (envolvente del Muscle Sensor V3)   1000 Hz  lote cada 100 ms: 100 x u16
 *   Cordon A1  divisor resistivo (Adafruit 519 + 10 kOhm)      20 Hz  lote cada 100 ms:   2 x u16
 *   MPU6050 I2C 0x68 (A4=SDA, A5=SCL, 400 kHz)                100 Hz lote cada 100 ms:  10 x 6 x i16
 *
 * ---------------------------------------------------------------------------------------
 * PRESUPUESTO DE TIEMPO (16 MHz, 1 ms de periodo = 16000 ciclos)
 *   - Timer1 CTC, prescaler 64, OCR1A = 249  ->  16e6 / 64 / 250 = 1000 Hz exactos.
 *   - ADC con prescaler 64 (250 kHz): una conversion = 13 ciclos de ADC = 52 us.
 *     Cada ms hay 1 conversion (EMG); cada 50 ms hay 4 (EMG + descarte + cordon + descarte):
 *     208 us en el peor caso, un 21 % de un ms. Nadie espera a la conversion: la ISR del
 *     timer la dispara y la ISR del ADC la recoge (ADIE), asi que analogRead() NO bloquea
 *     el resto de interrupciones (el TX serie usa UDRE: a 250000 baud un byte dura 40 us,
 *     un analogRead de 52..104 us dentro de una ISR dejaria huecos en la linea).
 *   - Coste de CPU de las ISR: Timer1 ~5 us + ADC ~4 us por conversion  =>  ~1-3 % de CPU.
 *   - TX serie (UDRE) ~4 us por byte x ~5 KB/s  =>  ~2 %.
 *   - Lectura I2C del MPU (14 bytes a 400 kHz): ~0.4 ms cada 10 ms (~4 %), en loop().
 *   (Estimaciones por ciclos de instruccion; no medidas en placa.)
 *
 * RAM (arduino-cli lo informa al compilar): lotes 400 + 240 + 8 = 648 B; el resto es el
 * core (Serial 2 x 64 B, Wire ~100 B) y las variables de estado.
 *
 * TX bloqueante: Serial.write bloquea si el buffer TX (64 B) se llena. A 250000 baud salen
 * ~25 KB/s y el caudal medio de este protocolo es ~5 KB/s (E ~2.7 KB/s, I ~1.9 KB/s), asi que
 * un lote entero (~300 B) tarda ~12 ms en salir y no hay problema. Si el host no lee, el
 * Arduino NO se bloquea: su UART transmite igual a 250000 baud hacia el chip USB-serie
 * (ATmega16U2 / CH340), que descarta lo que el host no recoge. (A confirmar en la placa
 * concreta: es comportamiento del puente, no de este firmware.)
 * Mientras espera hueco en el buffer TX, el sink atiende las lecturas del MPU (ver abajo),
 * de modo que escribir un lote largo no hace perder muestras del IMU.
 * ---------------------------------------------------------------------------------------
 */

#include <EEPROM.h>
#include <Wire.h>
#include <avr/interrupt.h>
#include <avr/io.h>
#include <util/atomic.h>

#include "protocol.h"

// ---------------------------------------------------------------------------------------
// Configuracion
// ---------------------------------------------------------------------------------------
static const uint32_t SERIAL_BAUD = 250000UL;

static const uint8_t PIN_NOISE = A3;  // flotante: solo semilla del boot_id

static const uint8_t ADC_CH_EMG = 0;     // A0
static const uint8_t ADC_CH_STRAIN = 1;  // A1

static const uint8_t EMG_BATCH = 100;        // 1000 Hz x 100 ms
static const uint8_t IMU_BATCH = 10;         // 100 Hz x 100 ms
static const uint8_t STRAIN_BATCH = 2;       // 20 Hz x 100 ms
static const uint8_t IMU_SAMPLE_BYTES = 12;  // 6 x int16

static const uint8_t TICKS_PER_STRAIN = 50;  // ticks de 1 ms -> 20 Hz
static const uint8_t TICKS_PER_IMU = 10;     // ticks de 1 ms -> 100 Hz
static const uint16_t TICKS_PER_T = 1000;    // ticks de 1 ms -> 1 Hz

static const uint8_t MPU_ADDR = 0x68;
static const uint8_t MPU_REG_ACCEL_OUT = 0x3B;  // 14 bytes: accel XYZ, temp, gyro XYZ (big-endian)
static const uint8_t MPU_REG_PWR_MGMT_1 = 0x6B;
static const uint8_t MPU_REG_CONFIG = 0x1A;
static const uint8_t MPU_REG_GYRO_CONFIG = 0x1B;
static const uint8_t MPU_REG_ACCEL_CONFIG = 0x1C;

// Fallos de lectura I2C consecutivos tolerados antes de dar el IMU por perdido (~0.2 s).
static const uint8_t IMU_MAX_FAILS = 20;
static const uint16_t IMU_RETRY_MS = 1000;

static const int EEPROM_BOOT_COUNTER_ADDR = 0;  // contador de arranques (uint32, 4 B)

// ---------------------------------------------------------------------------------------
// Lotes con doble buffer
// ---------------------------------------------------------------------------------------
// Una mitad se llena (ISR o loop) mientras la otra, ya completa, se emite desde loop().
// frame_seq se asigna al COMPLETAR el lote, incluidos los descartados por overrun, asi que
// un lote perdido se ve en el host como un salto de frame_seq.
struct Batcher {
    uint8_t *mem;         // 2 mitades de n * elem bytes
    uint8_t n;            // muestras por lote
    uint8_t elem;         // bytes por muestra
    uint8_t pos;          // siguiente muestra a escribir en la mitad que se llena
    uint8_t fill;         // mitad que se llena (0 / 1)
    uint8_t pending;      // mitad completa a la espera de emitirse (valida si ready)
    volatile bool ready;  // hay un lote completo sin emitir
    uint32_t t0[2];       // millis() de la primera muestra de cada mitad
    uint16_t seq[2];      // frame_seq de cada mitad
    uint16_t next_seq;    // frame_seq del proximo lote que se complete (da la vuelta en 65536)
    uint8_t overruns;     // lotes descartados (satura en 255)
    bool warned;          // ya se aviso por '#' del primer overrun
};

static uint8_t emgMem[2 * EMG_BATCH * 2];
static uint8_t imuMem[2 * IMU_BATCH * IMU_SAMPLE_BYTES];
static uint8_t strainMem[2 * STRAIN_BATCH * 2];

static Batcher emg = {emgMem, EMG_BATCH, 2, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};
static Batcher imu = {imuMem, IMU_BATCH, IMU_SAMPLE_BYTES, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};
static Batcher strain = {strainMem, STRAIN_BATCH, 2, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};

// Anade una muestra (elem bytes ya en little-endian). Seguro desde ISR: cada lote tiene un
// unico escritor y solo loop() baja `ready`.
static void batchPush(Batcher *b, const uint8_t *sample, uint32_t nowMs)
{
    uint8_t p = b->pos;
    if (p == 0) {
        b->t0[b->fill] = nowMs;
    }
    uint8_t *dst = b->mem + ((uint16_t)b->fill * b->n + p) * b->elem;
    for (uint8_t i = 0; i < b->elem; i++) {
        dst[i] = sample[i];
    }
    p++;
    if (p >= b->n) {
        p = 0;
        if (b->ready) {
            // El lote anterior sigue sin emitirse: se descarta este (se reutiliza la mitad
            // que se llenaba) pero su frame_seq se consume para que el host vea el hueco.
            if (b->overruns < 255) {
                b->overruns++;
            }
            b->next_seq++;
        } else {
            b->seq[b->fill] = b->next_seq++;
            b->pending = b->fill;
            b->fill ^= 1;
            b->ready = true;
        }
    }
    b->pos = p;
}

// ---------------------------------------------------------------------------------------
// Estado compartido ISR <-> loop
// ---------------------------------------------------------------------------------------
enum AdcState : uint8_t {
    ADC_IDLE = 0,
    ADC_EMG_REAL,      // conversion del EMG que se guarda
    ADC_STRAIN_DUMMY,  // primera conversion tras cambiar a A1: se descarta
    ADC_STRAIN_REAL,   // conversion del cordon que se guarda
    ADC_EMG_DUMMY      // primera conversion tras volver a A0: se descarta
};

static const uint8_t ADCSRA_RUN = _BV(ADEN) | _BV(ADIE) | _BV(ADPS2) | _BV(ADPS1);  // /64
static const uint8_t ADMUX_BASE = _BV(REFS0);                                          // AVcc

static volatile AdcState adcState = ADC_IDLE;
static volatile bool adcWithStrain = false;  // este tick tambien toca muestrear el cordon
static volatile uint32_t adcNowMs = 0;       // millis() del tick que dispara la conversion
static volatile uint8_t adcSkipped = 0;      // ticks con el ADC aun ocupado (no deberia pasar)

static volatile uint8_t imuDue = 0;     // periodos de 10 ms pendientes de leer
static volatile uint32_t imuDueMs = 0;  // millis() del periodo pendiente mas antiguo
static volatile bool tickDue = false;
static volatile uint32_t tickMs = 0;

// Avisos de depuracion levantados por imuService y emitidos solo desde loop() (nunca en
// medio de una linea de datos).
static const uint8_t DBG_IMU_LOST = 1;
static const uint8_t DBG_IMU_UP = 2;
static const uint8_t DBG_IMU_LATE = 4;
static volatile uint8_t dbgFlags = 0;

static uint32_t bootId = 0;

// ---------------------------------------------------------------------------------------
// ISR de muestreo
// ---------------------------------------------------------------------------------------
ISR(TIMER1_COMPA_vect)
{
    static uint8_t divStrain = 0;
    static uint8_t divImu = 0;
    static uint16_t divT = 0;
    uint32_t now = millis();

    if (adcState != ADC_IDLE) {
        if (adcSkipped < 255) {
            adcSkipped++;  // el ADC no ha terminado en 1 ms: no deberia pasar
        }
    } else {
        adcNowMs = now;
        if (++divStrain >= TICKS_PER_STRAIN) {
            divStrain = 0;
            adcWithStrain = true;
        } else {
            adcWithStrain = false;
        }
        adcState = ADC_EMG_REAL;
        ADCSRA = ADCSRA_RUN | _BV(ADSC);
    }

    // IMU: solo se anota que toca leer (la lectura I2C se hace en loop()).
    if (++divImu >= TICKS_PER_IMU) {
        divImu = 0;
        if (imuDue == 0) {
            imuDueMs = now;
        }
        if (imuDue < 255) {
            imuDue++;
        }
    }

    // Tick de 1 s.
    if (++divT >= TICKS_PER_T) {
        divT = 0;
        tickMs = now;
        tickDue = true;
    }
}

ISR(ADC_vect)
{
    uint16_t value = ADC;
    uint8_t sample[2];

    switch (adcState) {
    case ADC_EMG_REAL:
        sp_put_le16(sample, value);
        batchPush(&emg, sample, adcNowMs);
        if (adcWithStrain) {
            ADMUX = ADMUX_BASE | ADC_CH_STRAIN;
            adcState = ADC_STRAIN_DUMMY;
            ADCSRA = ADCSRA_RUN | _BV(ADSC);
        } else {
            adcState = ADC_IDLE;
        }
        break;
    case ADC_STRAIN_DUMMY:
        adcState = ADC_STRAIN_REAL;
        ADCSRA = ADCSRA_RUN | _BV(ADSC);
        break;
    case ADC_STRAIN_REAL:
        sp_put_le16(sample, value);
        batchPush(&strain, sample, adcNowMs);
        ADMUX = ADMUX_BASE | ADC_CH_EMG;
        adcState = ADC_EMG_DUMMY;
        ADCSRA = ADCSRA_RUN | _BV(ADSC);
        break;
    case ADC_EMG_DUMMY:
    default:
        adcState = ADC_IDLE;
        break;
    }
}

// ---------------------------------------------------------------------------------------
// MPU-6050
// ---------------------------------------------------------------------------------------
static bool imuOk = false;
static uint8_t imuFails = 0;
static uint8_t imuLast[IMU_SAMPLE_BYTES];  // ultima muestra valida
static uint32_t imuLastTry = 0;

static bool mpuWrite(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

// Despierta el MPU y lo configura: DLPF 44 Hz, +-500 dps, +-4 g.
static bool mpuConfigure()
{
    return mpuWrite(MPU_REG_PWR_MGMT_1, 0x00) && mpuWrite(MPU_REG_CONFIG, 0x03) &&
           mpuWrite(MPU_REG_GYRO_CONFIG, 0x08) && mpuWrite(MPU_REG_ACCEL_CONFIG, 0x08);
}

// Lectura en rafaga (14 bytes big-endian) -> 12 bytes little-endian: ax ay az gx gy gz.
// Se descarta la temperatura (bytes 6 y 7).
static bool mpuRead(uint8_t *out)
{
    uint8_t raw[14];
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(MPU_REG_ACCEL_OUT);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }
    if (Wire.requestFrom(MPU_ADDR, (uint8_t)14, (uint8_t)true) != 14) {
        return false;
    }
    for (uint8_t i = 0; i < 14; i++) {
        raw[i] = (uint8_t)Wire.read();
    }
    for (uint8_t axis = 0; axis < 3; axis++) {
        out[axis * 2] = raw[axis * 2 + 1];          // accel: byte bajo
        out[axis * 2 + 1] = raw[axis * 2];          // accel: byte alto
        out[6 + axis * 2] = raw[8 + axis * 2 + 1];  // gyro: byte bajo
        out[6 + axis * 2 + 1] = raw[8 + axis * 2];  // gyro: byte alto
    }
    return true;
}

// Atiende los periodos de IMU pendientes. Se llama desde loop() y desde el sink serie (para
// que escribir un lote largo no retrase la lectura). NO escribe nada por serie.
//
// Si loop() llega tarde y se acumulan `due` periodos, se lee UNA vez y esa muestra se repite
// en los `due` huecos (retencion de orden cero): cada hueco conserva su posicion en el lote
// (t0 + 10 ms * i) y no se desplaza ninguna muestra posterior. Se avisa con '#'.
// Un fallo de lectura suelto se trata igual (se repite la ultima muestra valida).
static void imuService()
{
    if (imuDue == 0) {
        return;
    }
    uint8_t due;
    uint32_t ms;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        due = imuDue;
        ms = imuDueMs;
        imuDue = 0;
    }
    if (!imuOk) {
        return;  // sin IMU: los periodos se descartan y no se emiten tramas I
    }
    if (mpuRead(imuLast)) {
        imuFails = 0;
    } else if (++imuFails > IMU_MAX_FAILS) {
        // IMU perdido: se deja de emitir I, se descarta el lote a medias y se reintenta
        // la configuracion cada IMU_RETRY_MS desde loop().
        imuOk = false;
        imu.pos = 0;
        dbgFlags |= DBG_IMU_LOST;
        return;
    }
    if (due > 1) {
        dbgFlags |= DBG_IMU_LATE;
    }
    for (uint8_t k = 0; k < due; k++) {
        batchPush(&imu, imuLast, ms + (uint32_t)TICKS_PER_IMU * k);
    }
}

// Reintento de configuracion cuando el IMU falta (arranque sin MPU o perdido en marcha).
static void imuRetry()
{
    if (imuOk) {
        return;
    }
    uint32_t now = millis();
    if (now - imuLastTry < IMU_RETRY_MS) {
        return;
    }
    imuLastTry = now;
    if (mpuConfigure()) {
        imuOk = true;
        imuFails = 0;
        imu.pos = 0;
        dbgFlags |= DBG_IMU_UP;
    }
}

// ---------------------------------------------------------------------------------------
// Salida serie
// ---------------------------------------------------------------------------------------
// Sink del protocolo. Mientras espera hueco en el buffer TX atiende el IMU. Solo se usa desde
// el contexto de loop(): nunca desde una ISR, asi que dos lineas no se mezclan.
static void serialSink(void *, char c)
{
    imuService();
    while (Serial.availableForWrite() == 0) {
        imuService();
    }
    Serial.write((uint8_t)c);
}

static void emitBatch(char kind, Batcher *b)
{
    bool have;
    uint8_t idx = 0;
    uint32_t t0 = 0;
    uint16_t seq = 0;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        have = b->ready;
        if (have) {
            idx = b->pending;
            t0 = b->t0[idx];
            seq = b->seq[idx];
        }
    }
    if (!have) {
        return;
    }
    sp_emit_data(serialSink, nullptr, kind, seq, t0, b->n, b->mem + (uint16_t)idx * b->n * b->elem,
                 (size_t)b->n * b->elem);
    b->ready = false;  // libera la mitad: ya puede volver a llenarse
}

static void reportOverrun(char kind, Batcher *b)
{
    if (b->overruns != 0 && !b->warned) {
        b->warned = true;
        Serial.print(F("#overrun "));
        Serial.write((uint8_t)kind);
        Serial.print(F(": lote descartado, frame_seq salta\n"));
    }
}

// Lineas de depuracion ('#'): solo desde aqui, entre lineas de datos.
static void flushDebug()
{
    static bool adcWarned = false;
    uint8_t flags;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        flags = dbgFlags;
        dbgFlags = 0;
    }
    if (flags & DBG_IMU_LOST) {
        Serial.print(F("#imu lost: sin tramas I hasta que responda\n"));
    }
    if (flags & DBG_IMU_UP) {
        Serial.print(F("#imu up\n"));
    }
    if (flags & DBG_IMU_LATE) {
        Serial.print(F("#imu late: muestra repetida en los periodos perdidos\n"));
    }
    if (adcSkipped != 0 && !adcWarned) {
        adcWarned = true;
        Serial.print(F("#adc busy: tick sin conversion\n"));
    }
    reportOverrun('E', &emg);
    reportOverrun('I', &imu);
    reportOverrun('S', &strain);
}

// ---------------------------------------------------------------------------------------
// boot_id
// ---------------------------------------------------------------------------------------
// Mezcla de bits de MurmurHash3 (fmix32): biyectiva, reparte cualquier diferencia de entrada.
static uint32_t mix32(uint32_t h)
{
    h ^= h >> 16;
    h *= 0x85EBCA6BUL;
    h ^= h >> 13;
    h *= 0xC2B2AE35UL;
    h ^= h >> 16;
    return h;
}

// boot_id = fmix32( contador_EEPROM * 0x9E3779B1  XOR  ruido(A3 flotante)  XOR  micros() ).
//  - contador en EEPROM[0..3], +1 en cada arranque: dos arranques consecutivos no repiten
//    entrada aunque ruido y micros() coincidieran (EEPROM.put solo escribe los bytes que
//    cambian; el 328P aguanta ~100000 ciclos por celda). EEPROM en blanco = 0xFFFFFFFF.
//  - 32 lecturas del ADC de un pin flotante (bit menos significativo) con micros() entre
//    medias: aporta entropia fisica.
//  - micros() en el momento de arrancar.
// No es criptografico: solo debe distinguir arranques (colision entre dos ~2^-32).
static uint32_t makeBootId()
{
    uint32_t counter;
    EEPROM.get(EEPROM_BOOT_COUNTER_ADDR, counter);
    counter++;
    EEPROM.put(EEPROM_BOOT_COUNTER_ADDR, counter);

    pinMode(PIN_NOISE, INPUT);
    uint32_t noise = 0;
    for (uint8_t i = 0; i < 32; i++) {
        noise = (noise << 1) | (uint32_t)(analogRead(PIN_NOISE) & 1);
        delayMicroseconds((uint16_t)(3 + (micros() & 7)));
    }
    return mix32((counter * 0x9E3779B1UL) ^ noise ^ micros());
}

// ---------------------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------------------
void setup()
{
    Serial.begin(SERIAL_BAUD);

    bootId = makeBootId();  // usa analogRead: antes de tomar el control del ADC

    // ADC: AVcc de referencia, A0 y A1 sin buffer digital (menos ruido), prescaler 64 e
    // interrupcion. A partir de aqui NO se debe usar analogRead(): el ADC es de las ISR.
    DIDR0 = _BV(ADC0D) | _BV(ADC1D);
    ADMUX = ADMUX_BASE | ADC_CH_EMG;
    // Conversion de descarte tras cambiar de canal (A3 -> A0). Con ADIE puesto la ISR del ADC
    // la recoge con adcState == ADC_IDLE y no hace nada.
    ADCSRA = ADCSRA_RUN | _BV(ADSC);
    while (ADCSRA & _BV(ADSC)) {
    }

    // IMU. Si no responde: no se emiten tramas I, se sigue con E y S y se reintenta cada 1 s.
    Wire.begin();
    Wire.setClock(400000UL);
    Wire.setWireTimeout(3000UL, true);  // un bus colgado no cuelga el firmware
    imuOk = mpuConfigure();
    imuLastTry = millis();

    sp_emit_hello(serialSink, nullptr, bootId, SP_FW_VERSION, millis());
    Serial.print(imuOk ? F("#imu ok\n") : F("#imu absent: sin tramas I\n"));

    // Timer1: CTC, prescaler 64, OCR1A = 249 -> 1 kHz.
    noInterrupts();
    TCCR1A = 0;
    TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
    OCR1A = 249;
    TCNT1 = 0;
    TIMSK1 = _BV(OCIE1A);
    interrupts();
}

void loop()
{
    imuService();
    imuRetry();
    flushDebug();

    bool tick;
    uint32_t tms;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        tick = tickDue;
        tms = tickMs;
        tickDue = false;
    }
    if (tick) {
        sp_emit_tick(serialSink, nullptr, bootId, tms);
    }

    emitBatch('E', &emg);
    emitBatch('I', &imu);
    emitBatch('S', &strain);
}
