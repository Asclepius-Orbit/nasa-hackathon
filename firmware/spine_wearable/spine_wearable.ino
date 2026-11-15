/*
 * spine_wearable - Wearable firmware (Arduino Uno / Nano, ATmega328P, 16 MHz, 2 KB RAM).
 *
 * Samples 3 sensors and ALWAYS emits the v1 chunk protocol over USB serial (9600 baud,
 * 8N1) (see protocol.h and firmware/README.md). It does not wait for host commands.
 *
 *   EMG    A0  analog (Muscle Sensor V3 envelope)            1000 Hz  batch every 100 ms: 100 x u16
 *   Cord   A1  resistive divider (Adafruit 519 + 10 kOhm)      20 Hz  batch every 100 ms:   2 x u16
 *   MPU6050 I2C 0x68 (A4=SDA, A5=SCL, 400 kHz)                100 Hz batch every 100 ms:  10 x 6 x i16
 *
 * ---------------------------------------------------------------------------------------
 * TIME BUDGET (16 MHz, 1 ms period = 16000 cycles)
 *   - Timer1 CTC, prescaler 64, OCR1A = 249  ->  16e6 / 64 / 250 = exactly 1000 Hz.
 *   - ADC with prescaler 64 (250 kHz): one conversion = 13 ADC cycles = 52 us.
 *     Every ms there is 1 conversion (EMG); every 50 ms there are 4 (EMG + discard + cord + discard):
 *     208 us in the worst case, 21 % of a ms. Nobody waits for the conversion: the timer
 *     ISR triggers it and the ADC ISR collects it (ADIE), so analogRead() does NOT block
 *     the other interrupts (the serial TX uses UDRE: at 9600 baud one byte lasts ~1.04 ms,
 *     much longer than an analogRead of 52..104 us).
 *   - ISR CPU cost: Timer1 ~5 us + ADC ~4 us per conversion  =>  ~1-3 % of CPU.
 *   - Serial TX (UDRE) ~4 us per byte x ~5 KB/s  =>  ~2 %.
 *   - MPU I2C read (14 bytes at 400 kHz): ~0.4 ms every 10 ms (~4 %), in loop().
 *   (Estimates from instruction cycles; not measured on the board.)
 *
 * RAM (arduino-cli reports it when compiling): batches 400 + 240 + 8 = 648 B; the rest is
 * the core (Serial 2 x 64 B, Wire ~100 B) and the state variables.
 *
 * Blocking TX: Serial.write blocks if the TX buffer (64 B) fills up. At 9600 baud ~0.96 KB/s
 * go out, LESS than the average throughput of this protocol (~5 KB/s: E ~2.7 KB/s, I ~1.9 KB/s):
 * a whole batch (~300 B) takes ~312 ms to go out, longer than its 100 ms period, so the TX
 * would saturate and samples would be lost. If the host does not read, the Arduino does NOT
 * block: its UART still transmits at 9600 baud to the USB-serial chip (ATmega16U2 / CH340),
 * which discards what the host does not pick up. (To be confirmed on the specific board: it
 * is bridge behaviour, not this firmware's.)
 * While it waits for room in the TX buffer, the sink serves the MPU reads (see below),
 * so writing a long batch does not lose IMU samples.
 * ---------------------------------------------------------------------------------------
 */

#include <EEPROM.h>
#include <Wire.h>
#include <avr/interrupt.h>
#include <avr/io.h>
#include <util/atomic.h>

#include "protocol.h"

// ---------------------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------------------
static const uint32_t SERIAL_BAUD = 9600UL;

static const uint8_t PIN_NOISE = A3;  // floating: only the boot_id seed

static const uint8_t ADC_CH_EMG = 0;     // A0
static const uint8_t ADC_CH_STRAIN = 1;  // A1

static const uint8_t EMG_BATCH = 100;        // 1000 Hz x 100 ms
static const uint8_t IMU_BATCH = 10;         // 100 Hz x 100 ms
static const uint8_t STRAIN_BATCH = 2;       // 20 Hz x 100 ms
static const uint8_t IMU_SAMPLE_BYTES = 12;  // 6 x int16

static const uint8_t TICKS_PER_STRAIN = 50;  // 1 ms ticks -> 20 Hz
static const uint8_t TICKS_PER_IMU = 10;     // 1 ms ticks -> 100 Hz
static const uint16_t TICKS_PER_T = 1000;    // 1 ms ticks -> 1 Hz

static const uint8_t MPU_ADDR = 0x68;
static const uint8_t MPU_REG_ACCEL_OUT = 0x3B;  // 14 bytes: accel XYZ, temp, gyro XYZ (big-endian)
static const uint8_t MPU_REG_PWR_MGMT_1 = 0x6B;
static const uint8_t MPU_REG_CONFIG = 0x1A;
static const uint8_t MPU_REG_GYRO_CONFIG = 0x1B;
static const uint8_t MPU_REG_ACCEL_CONFIG = 0x1C;

// Consecutive I2C read failures tolerated before declaring the IMU lost (~0.2 s).
static const uint8_t IMU_MAX_FAILS = 20;
static const uint16_t IMU_RETRY_MS = 1000;

static const int EEPROM_BOOT_COUNTER_ADDR = 0;  // boot counter (uint32, 4 B)

// ---------------------------------------------------------------------------------------
// Double-buffered batches
// ---------------------------------------------------------------------------------------
// One half fills (ISR or loop) while the other, already complete, is emitted from loop().
// frame_seq is assigned when the batch COMPLETES, including those dropped by overrun, so
// a lost batch shows up on the host as a jump in frame_seq.
struct Batcher {
    uint8_t *mem;         // 2 halves of n * elem bytes
    uint8_t n;            // samples per batch
    uint8_t elem;         // bytes per sample
    uint8_t pos;          // next sample to write in the half being filled
    uint8_t fill;         // half being filled (0 / 1)
    uint8_t pending;      // complete half waiting to be emitted (valid if ready)
    volatile bool ready;  // there is a complete batch not yet emitted
    uint32_t t0[2];       // millis() of the first sample of each half
    uint16_t seq[2];      // frame_seq of each half
    uint16_t next_seq;    // frame_seq of the next batch to complete (wraps at 65536)
    uint8_t overruns;     // dropped batches (saturates at 255)
    bool warned;          // the first overrun was already reported via '#'
};

static uint8_t emgMem[2 * EMG_BATCH * 2];
static uint8_t imuMem[2 * IMU_BATCH * IMU_SAMPLE_BYTES];
static uint8_t strainMem[2 * STRAIN_BATCH * 2];

static Batcher emg = {emgMem, EMG_BATCH, 2, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};
static Batcher imu = {imuMem, IMU_BATCH, IMU_SAMPLE_BYTES, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};
static Batcher strain = {strainMem, STRAIN_BATCH, 2, 0, 0, 0, false, {0, 0}, {0, 0}, 0, 0, false};

// Adds a sample (elem bytes already little-endian). Safe from an ISR: each batch has a
// single writer and only loop() lowers `ready`.
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
            // The previous batch is still not emitted: this one is dropped (the half being
            // filled is reused) but its frame_seq is consumed so the host sees the gap.
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
// State shared ISR <-> loop
// ---------------------------------------------------------------------------------------
enum AdcState : uint8_t {
    ADC_IDLE = 0,
    ADC_EMG_REAL,      // EMG conversion that is kept
    ADC_STRAIN_DUMMY,  // first conversion after switching to A1: discarded
    ADC_STRAIN_REAL,   // cord conversion that is kept
    ADC_EMG_DUMMY      // first conversion after switching back to A0: discarded
};

static const uint8_t ADCSRA_RUN = _BV(ADEN) | _BV(ADIE) | _BV(ADPS2) | _BV(ADPS1);  // /64
static const uint8_t ADMUX_BASE = _BV(REFS0);                                          // AVcc

static volatile AdcState adcState = ADC_IDLE;
static volatile bool adcWithStrain = false;  // this tick the cord must also be sampled
static volatile uint32_t adcNowMs = 0;       // millis() of the tick that triggers the conversion
static volatile uint8_t adcSkipped = 0;      // ticks with the ADC still busy (should not happen)

static volatile uint8_t imuDue = 0;     // 10 ms periods pending to be read
static volatile uint32_t imuDueMs = 0;  // millis() of the oldest pending period
static volatile bool tickDue = false;
static volatile uint32_t tickMs = 0;

// Debug notices raised by imuService and emitted only from loop() (never in the middle
// of a data line).
static const uint8_t DBG_IMU_LOST = 1;
static const uint8_t DBG_IMU_UP = 2;
static const uint8_t DBG_IMU_LATE = 4;
static volatile uint8_t dbgFlags = 0;

static uint32_t bootId = 0;

// ---------------------------------------------------------------------------------------
// Sampling ISR
// ---------------------------------------------------------------------------------------
ISR(TIMER1_COMPA_vect)
{
    static uint8_t divStrain = 0;
    static uint8_t divImu = 0;
    static uint16_t divT = 0;
    uint32_t now = millis();

    if (adcState != ADC_IDLE) {
        if (adcSkipped < 255) {
            adcSkipped++;  // the ADC has not finished in 1 ms: should not happen
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

    // IMU: only note that a read is due (the I2C read is done in loop()).
    if (++divImu >= TICKS_PER_IMU) {
        divImu = 0;
        if (imuDue == 0) {
            imuDueMs = now;
        }
        if (imuDue < 255) {
            imuDue++;
        }
    }

    // 1 s tick.
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
static uint8_t imuLast[IMU_SAMPLE_BYTES];  // last valid sample
static uint32_t imuLastTry = 0;

static bool mpuWrite(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

// Wakes up the MPU and configures it: DLPF 44 Hz, +-500 dps, +-4 g.
static bool mpuConfigure()
{
    return mpuWrite(MPU_REG_PWR_MGMT_1, 0x00) && mpuWrite(MPU_REG_CONFIG, 0x03) &&
           mpuWrite(MPU_REG_GYRO_CONFIG, 0x08) && mpuWrite(MPU_REG_ACCEL_CONFIG, 0x08);
}

// Burst read (14 bytes big-endian) -> 12 bytes little-endian: ax ay az gx gy gz.
// The temperature is discarded (bytes 6 and 7).
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
        out[axis * 2] = raw[axis * 2 + 1];          // accel: low byte
        out[axis * 2 + 1] = raw[axis * 2];          // accel: high byte
        out[6 + axis * 2] = raw[8 + axis * 2 + 1];  // gyro: low byte
        out[6 + axis * 2 + 1] = raw[8 + axis * 2];  // gyro: high byte
    }
    return true;
}

// Services the pending IMU periods. Called from loop() and from the serial sink (so that
// writing a long batch does not delay the read). It does NOT write anything to serial.
//
// If loop() arrives late and `due` periods accumulate, it reads ONCE and that sample is
// repeated in the `due` slots (zero-order hold): each slot keeps its position in the batch
// (t0 + 10 ms * i) and no later sample is shifted. A '#' notice is emitted.
// An isolated read failure is treated the same way (the last valid sample is repeated).
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
        return;  // no IMU: the periods are discarded and no I frames are emitted
    }
    if (mpuRead(imuLast)) {
        imuFails = 0;
    } else if (++imuFails > IMU_MAX_FAILS) {
        // IMU lost: stop emitting I, discard the partial batch and retry the
        // configuration every IMU_RETRY_MS from loop().
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

// Configuration retry when the IMU is missing (boot without MPU or lost while running).
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
// Serial output
// ---------------------------------------------------------------------------------------
// Protocol sink. While waiting for room in the TX buffer it services the IMU. Only used from
// the loop() context: never from an ISR, so two lines are never interleaved.
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
    b->ready = false;  // frees the half: it can be filled again
}

static void reportOverrun(char kind, Batcher *b)
{
    if (b->overruns != 0 && !b->warned) {
        b->warned = true;
        Serial.print(F("#overrun "));
        Serial.write((uint8_t)kind);
        Serial.print(F(": batch dropped, frame_seq jumps\n"));
    }
}

// Debug lines ('#'): only from here, between data lines.
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
        Serial.print(F("#imu lost: no I frames until it responds\n"));
    }
    if (flags & DBG_IMU_UP) {
        Serial.print(F("#imu up\n"));
    }
    if (flags & DBG_IMU_LATE) {
        Serial.print(F("#imu late: sample repeated in the missed periods\n"));
    }
    if (adcSkipped != 0 && !adcWarned) {
        adcWarned = true;
        Serial.print(F("#adc busy: tick without conversion\n"));
    }
    reportOverrun('E', &emg);
    reportOverrun('I', &imu);
    reportOverrun('S', &strain);
}

// ---------------------------------------------------------------------------------------
// boot_id
// ---------------------------------------------------------------------------------------
// MurmurHash3 bit mixer (fmix32): bijective, spreads any input difference.
static uint32_t mix32(uint32_t h)
{
    h ^= h >> 16;
    h *= 0x85EBCA6BUL;
    h ^= h >> 13;
    h *= 0xC2B2AE35UL;
    h ^= h >> 16;
    return h;
}

// boot_id = fmix32( EEPROM_counter * 0x9E3779B1  XOR  noise(floating A3)  XOR  micros() ).
//  - counter in EEPROM[0..3], +1 on every boot: two consecutive boots never repeat the
//    input even if noise and micros() coincided (EEPROM.put only writes the bytes that
//    change; the 328P endures ~100000 cycles per cell). Blank EEPROM = 0xFFFFFFFF.
//  - 32 ADC reads of a floating pin (least significant bit) with micros() in between:
//    contributes physical entropy.
//  - micros() at boot time.
// Not cryptographic: it only needs to tell boots apart (collision between two ~2^-32).
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

    bootId = makeBootId();  // uses analogRead: before taking control of the ADC

    // ADC: AVcc reference, A0 and A1 without digital buffer (less noise), prescaler 64 and
    // interrupt. From here on analogRead() must NOT be used: the ADC belongs to the ISRs.
    DIDR0 = _BV(ADC0D) | _BV(ADC1D);
    ADMUX = ADMUX_BASE | ADC_CH_EMG;
    // Dummy conversion after switching channel (A3 -> A0). With ADIE set, the ADC ISR
    // picks it up with adcState == ADC_IDLE and does nothing.
    ADCSRA = ADCSRA_RUN | _BV(ADSC);
    while (ADCSRA & _BV(ADSC)) {
    }

    // IMU. If it does not respond: no I frames are emitted, E and S continue, retry every 1 s.
    Wire.begin();
    Wire.setClock(400000UL);
    Wire.setWireTimeout(3000UL, true);  // a hung bus does not hang the firmware
    imuOk = mpuConfigure();
    imuLastTry = millis();

    sp_emit_hello(serialSink, nullptr, bootId, SP_FW_VERSION, millis());
    Serial.print(imuOk ? F("#imu ok\n") : F("#imu absent: no I frames\n"));

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
