# Sensor sources for the realistic simulation

Reviewed on 2026-11-14 (access date of every URL). Inventory of sources for
parameterising the simulator of the wearable's three sensors (Arduino Uno):
conductive elastic cord, EMG module and IMU. It is not a validated set of
measurements or a clinical standard. Every figure carries its source and a
category:

- **datasheet**: declared by the manufacturer or the seller.
- **firmware/gateway config**: set by this repository's code.
- **own measurement**: measured by the team with their setup.
- **unmeasured assumption**: working hypothesis with no verified backing.

Anything that could not be verified is stated explicitly and marked as a
hypothesis. No values were digitised from charts. Manufacturer datasheets were
consulted in third-party copies (see each row), not on the manufacturer's
official website.

## Component identification

| Sensor | Identification | Status |
|---|---|---|
| Elastic cord | [Adafruit 519](https://www.adafruit.com/product/519), carbon-black-loaded rubber cord, 2 mm diameter, ~1 m. | Verified on the manufacturer's page. |
| EMG module | Purchased from [amazon.es B0CP5F9WTN](https://www.amazon.es/dp/B0CP5F9WTN). The firmware README calls it Muscle Sensor V3 (SIG output). | **Not verified.** The Amazon listing returned HTTP 503 and a search by ASIN did not find it. The actual model, gain and supply voltage of the purchased unit are not asserted. The module is defective and there are no reliable measurements. |
| IMU | [Amazon B07BVXN2GP](https://www.amazon.es/dp/B07BVXN2GP), identified by the team as an MPU-6050 (GY-521-type board). | The Amazon listing returned HTTP 503; the identification is the team's, it could not be confirmed on the listing. The figures below are for the chip, not for the specific board. |

## Known facts from the repository

| Fact | Value | Category | Where |
|---|---|---|---|
| EMG sampling rate | 1000 Hz (A0, `analogRead` 0..1023) | firmware config | `firmware/README.md`, pin and protocol table |
| IMU sampling rate | 100 Hz, 6 axes int16 | firmware config | `firmware/README.md` |
| Cord sampling rate | 20 Hz (A1) | firmware config | `firmware/README.md` |
| Batches | 100 ms: 100 EMG samples, 10 IMU, 2 cord | firmware config | `firmware/README.md`, `D,E/I/S` protocol |
| MPU-6050 configuration | `PWR_MGMT_1=0`, DLPF 44 Hz (`CONFIG=0x03`), ±500 dps (`GYRO_CONFIG=0x08`), ±4 g (`ACCEL_CONFIG=0x08`) | firmware/gateway config | `firmware/README.md`; `spine_monitor/gateway/src/spine_gateway/settings.py` (`imu_gyro_range_dps=500`, `imu_dlpf_hz=44`) |
| Cord divider | 5 V, 10 kΩ, midpoint at A1, cord to GND | firmware/gateway config | `firmware/README.md`; `settings.py` (`stretch_series_resistor_ohm=10000`) |
| EMG signal type | `envelope` by default, "assumed, not confirmed with the module" | unmeasured assumption | `firmware/README.md`; `settings.py` (`emg_signal_kind`, `emg_gain=1.0`) |
| Cord: placement | C3 to S1, ~650 mm at rest, still to be measured | unmeasured assumption | `firmware/README.md` |
| Serial | 9600 baud, 8N1 | firmware config | `firmware/README.md` |

### Cord bench measurements

Contacts 10 cm apart. Total length in cm and resistance in Ω (three readings per
length; the column order is that of the team's log and its meaning has not been
inferred).

| Total length (cm) | Ω (readings) |
|---|---|
| 100 | 630 / 780 / 795 |
| 110 | 1020 / 1165 / 1125 |
| 120 | 1110 / 1255 / 1260 |
| 130 | 1270 / 1320 / 1300 |
| 140 | 1350 / 1355 / 1350 |

Category: own measurement (bench, one cord). Two observations, without further
inference: resistance grows sublinearly with length, and the first reading at
each length is the lowest except at 140 cm. There is no measurement of
temperature, stretch speed or reading order.

### Reference on the back

Contacts 50 cm apart: 4500 Ω standing straight and 5650 Ω at maximum flexion.
Category: own measurement, informative and per astronaut. The bench setup and
the back setup cannot be linked: different contact length, possibly a different
cord and a different initial tension. Do not interpolate between the two tables.

## Adafruit 519 conductive cord

| Aspect | Data | Category | Source |
|---|---|---|---|
| Resistance at rest | 350-400 Ω/inch, i.e. 140-160 Ω/cm (as stated on the page). Example: 6 in ~ 2.1 kΩ. | datasheet | [Adafruit 519](https://www.adafruit.com/product/519) |
| Stretched | 6 in to 10 in reads ~3.5 kΩ (example on the page). It can be stretched 50-70 %. | datasheet | same |
| Non-linearity | "The response is not really linear". | datasheet (qualitative) | same |
| Lot-to-lot variation | Resistance "can vary from lot to lot". No figure. | datasheet (qualitative) | same |
| Hysteresis and recovery | After release, it takes "a minute or two" to recover its length. Hysteresis is not quantified and creep is not mentioned. | datasheet (qualitative) | same |
| Intended use | Stretch-motion detection, not precision measurement. Kit with a 10 kΩ 1 % resistor. | datasheet | same |
| Divider and ADC | V = 5 V · R / (R + 10 kΩ); counts = V/5 V · 1023. Calculated examples: 630 Ω -> ~61; 1350 Ω -> ~122; 4500 Ω -> ~317; 5650 Ω -> ~369. | calculated from config | `settings.py` and 10-bit ADC |

Contrast with the bench: the ~140-160 Ω/cm on the page imply ~14-16 kΩ for
100 cm, whereas the bench gives 0.6-0.8 kΩ for 100 cm total and the back gives
4.5 kΩ at 50 cm of contacts. The team's measurements agree neither with the
manufacturer's figure nor with each other per cm, which indicates that
resistance depends on how the contacts are made and on the specific cord. The
simulator must not use the manufacturer's figure to reproduce the bench.

No verified source: amount of hysteresis, creep, temperature drift and
resistance noise. They are hypotheses; see parameters.

## EMG module

| Aspect | Data | Category | Source |
|---|---|---|---|
| Reference family | Muscle Sensor V3 (Advancer Technologies). It is the reference cited by the firmware; the purchased unit is not confirmed. | datasheet of a candidate model | [User manual v3 (4 Feb 2013)](https://cdn.sparkfun.com/datasheets/Sensors/Biometric/Muscle%20Sensor%20v3%20Users%20Manual.pdf) |
| Supply | ±3 V min., ±5 V typ., ±30 V max. (dual supply, e.g. two 9 V batteries). Retailers list ±5 V typ. | datasheet (candidate model) | same; [Pololu 2726](https://www.pololu.com/product/2726) |
| Gain | G = 207 · (X / 1 kΩ), X being the potentiometer; 0.002x min., 10,350x typ., 20,700x max. | datasheet | manual v3, electrical specifications table |
| Output | Not raw EMG: "amplified, rectified and smoothed" signal (envelope), from 0 V to +Vs. | datasheet | manual v3, section "RAW EMG vs Rectified & Smoothed EMG" |
| Differential input voltage | 0 mV min.; 2-5 mV typ.; +Vs/Gain max. | datasheet | manual v3 |
| Noise, CMRR, input impedance, bandwidth | **Not verified** in the copy read (not extracted). | hypothesis | - |
| Purchased module | Defective, no reliable measurements. Neither raw, nor envelope, nor real gain can be declared. | own measurement (negative) | team decision |

Interface note: with a ±Vs supply the output can exceed the 5 V the Uno's ADC
tolerates. The firmware README already warns about this (limit to 5 V). If the
module were a later MyoWare, its supply is a single voltage (2.9-5.7 V
according to a retailer), but it has not been confirmed that it is one
([search results](https://core-electronics.com.au/advancer-technologies-muscle-sensor-v3.html)).

### Surface EMG in general (literature)

| Aspect | Data | Category | Source |
|---|---|---|---|
| Amplitude | 50 µV to 100 mV (citing Pancholi and Joshi, 2018). The raw signal is usually in the order of µV to a few mV. | literature (wide range) | [Sun et al., Front. Syst. Neurosci. 2022](https://www.frontiersin.org/articles/10.3389/fnsys.2022.893275) |
| Frequency band | 10-500 Hz according to that review. 20-450 Hz is commonly used (e.g. studies filtering at 20-450 Hz and sampling at 1515 Hz). | literature | same; search results at [arXiv 2309.04770](https://arxiv.org/pdf/2309.04770) |
| Mains and motion artifacts | The raw signal is "inevitably" mixed with mains interference and motion artifacts. No magnitude. 50 Hz in Europe. | literature (qualitative) | review by Sun et al. 2022 |
| Baseline drift and ECG contamination | **Not verified with a figure** in the sources read. Treated as known phenomena, not quantified. | hypothesis | - |
| Median frequency drop with fatigue | The median falls, roughly linearly in time in sustained contractions; amplitude rises at constant force. No percentage. | literature (qualitative) | review by Sun et al. 2022; [arXiv physics/0611120](https://arxiv.org/pdf/physics/0611120) |
| Energy concentration at 50-150 Hz | **Not verified**; it only appears as a noise metric in one paper. | hypothesis | - |
| SENIAM recommendations (inter-electrode distance, etc.) | Not consulted: the SENIAM server returned a certificate error. | not verified | [seniam.org](https://www.seniam.org/) |

For a value to use in the simulator, the amplitude range in mV of a specific
back muscle with that electrode is not backed by any source read here.

## MPU-6050 IMU

Source of every figure in this section: InvenSense, *MPU-6000/MPU-6050
Product Specification*, PS-MPU-6000A-00, revision 3.4, 19 Aug 2013. The
[copy on mikrocontroller.net](https://www.mikrocontroller.net/attachment/236856/MPU-6050.pdf)
(52-page PDF) was read. Text extraction of the specification tables came out
with scrambled cells, so the pairing of values and units was done with that in
mind, and values marked (*) must be checked against the original datasheet
before being taken as final. Category: datasheet (of the chip, VDD 2.375-3.46 V,
25 °C).

| Aspect | Data |
|---|---|
| Gyroscope sensitivity | 131 / 65.5 / 32.8 / 16.4 LSB/(°/s) for ±250 / ±500 / ±1000 / ±2000 °/s. At ±500 °/s: **65.5 LSB/(°/s)**. |
| Accelerometer sensitivity | 16384 / 8192 / 4096 / 2048 LSB/g for ±2 / ±4 / ±8 / ±16 g. At ±4 g: **8192 LSB/g**. |
| ADC word | 16 bits, two's complement. |
| Gyroscope sensitivity tolerance | ±3 % initial at 25 °C (*); variation with temperature ±2 % (*); non-linearity 0.2 % (*). |
| Gyroscope initial offset (ZRO) | ±20 °/s at 25 °C; variation with temperature ±20 °/s (-40 to +85 °C). |
| Gyroscope noise | 0.05 °/s rms total (*); 0.033 °/s rms from 1 to 10 Hz (*); spectral density 0.005 °/s/√Hz at 10 Hz (*). |
| Accelerometer offset (zero g) | ±50 mg on X and Y, ±80 mg on Z (*) with initial calibration; change with temperature ±35 mg X/Y and ±60 mg Z, from 0 to 70 °C (*). Sensitivity drift with temperature ±0.02 %/°C. |
| Accelerometer noise | Power spectral density 400 µg/√Hz (at 10 Hz, ±2 g, 1 kHz). |
| Temperature sensor | 340 LSB/°C, offset -521 LSB at 35 °C. |
| Low-pass filter range | 5-256 Hz (gyroscope) and 5-260 Hz (accelerometer); programmable output rate 4-8000 Hz (gyroscope). |
| DLPF at 44 Hz | The firmware's 44 Hz setting is `CONFIG=0x03`. The exact per-axis bandwidth (accelerometer 44 Hz, gyroscope 42 Hz) and the delay are in the register map (document RM-MPU-6000A), which was **not consulted**; it remains a hypothesis until verified. |

Warning: these figures are for the chip. The board sold on Amazon may include a
regulator and pull-ups, and it has not been checked that the chip is genuine.
The noise specifications depend on the datasheet's configuration; with a 44 Hz
DLPF and 100 Hz sampling the resulting rms noise has not been calculated from a
source.

## Arduino Uno analog-to-digital converter

| Aspect | Data | Category | Source |
|---|---|---|---|
| Resolution | 10 bits, values 0-1023 | firmware config | `firmware/README.md` (`analogRead` 0..1023) |
| Reference | 5 V (count = V/5 · 1023, ~4.9 mV/LSB) | hypothesis, no source read | the Arduino `analogRead` page returned HTTP 404 in this review |
| Maximum rate | ~10 kHz (~100 µs per reading) | hypothesis, no source read | same (recalled from Arduino documentation, not verified) |
| Absolute ADC accuracy | ATmega328P datasheet not consulted | not verified | - |

## Parameter table for the simulator

Status: spec = datasheet; config = firmware/gateway; measured = own
measurement; hyp. = hypothesis with no verified backing.

### Common

| Name | Default value | Unit | Status | Source |
|---|---|---|---|---|
| `adc_bits` | 10 | bits | config | firmware README |
| `adc_vref` | 5.0 | V | hyp. | Uno ADC, no source read |
| `batch_ms` | 100 | ms | config | firmware README |

### Cord (`stretch`)

| Name | Default value | Unit | Status | Source |
|---|---|---|---|---|
| `stretch_rate_hz` | 20 | Hz | config | firmware README |
| `series_resistor_ohm` | 10000 | Ω | config | `settings.py` |
| `r_curve_bench` | 100-140 cm table (see above) | Ω | measured | team bench |
| `r_back_straight` / `r_back_flexion` | 4500 / 5650 (contacts at 50 cm) | Ω | measured (per astronaut) | team's back setup |
| `r_per_cm_manufacturer` | 140-160 | Ω/cm | spec | Adafruit 519 (does not match the bench) |
| `max_stretch_fraction` | 0.5-0.7 | fraction | spec | Adafruit 519 |
| `recovery_time_s` | 60-120 | s | spec (qualitative) | "a minute or two" |
| `hysteresis_fraction` | 0.05 | fraction | hyp. | no quantitative source |
| `creep_fraction` | 0.02 | fraction | hyp. | no quantitative source |
| `lot_variation_fraction` | 0.10 | fraction | hyp. | Adafruit only says it exists |
| `read_noise_ohm` | 5 | Ω | hyp. | repeated bench readings differ by up to ~150 Ω between readings of the same length; it is not pure noise |

### EMG (`emg`)

| Name | Default value | Unit | Status | Source |
|---|---|---|---|---|
| `emg_rate_hz` | 1000 | Hz | config | firmware README |
| `signal_kind` | envelope | enum | hyp. | not confirmed with the module; gateway default |
| `gain` | 1.0 | - | config | `settings.py` `emg_gain`; the module's real gain is unknown |
| `band_hz` | 20-450 | Hz | literature | Sun et al. 2022 and studies using that band |
| `mains_hz` | 50 | Hz | hyp. | European mains, no magnitude figure |
| `mains_amplitude`, `baseline_wander`, `ecg_leak` | 0 (off) | - | hyp. | no verified figure |
| `fatigue_median_freq_drop` | 0 (off) | % | hyp. | only qualitative in the literature |
| `envelope_full_scale` | 0-1023 counts | counts | config | 10-bit ADC |

### IMU (`imu`)

| Name | Default value | Unit | Status | Source |
|---|---|---|---|---|
| `imu_rate_hz` | 100 | Hz | config | firmware README |
| `accel_range_g` | 4 | g | config | firmware README |
| `gyro_range_dps` | 500 | dps | config | `settings.py` |
| `dlpf_hz` | 44 | Hz | config | `settings.py` |
| `accel_lsb_per_g` | 8192 | LSB/g | spec | MPU-6050 datasheet rev. 3.4 |
| `gyro_lsb_per_dps` | 65.5 | LSB/dps | spec | same |
| `gyro_noise_density` | 0.005 (*) | dps/√Hz | spec | same, to be checked |
| `accel_noise_density` | 400 | µg/√Hz | spec | same |
| `gyro_bias_max` | 20 | dps | spec (maximum, not typical) | same |
| `accel_bias_max` | 0.05 (X/Y), 0.08 (Z) (*) | g | spec (maximum) | same, to be checked |
| `temp_drift` | gyro ±20 dps, accel ±0.02 %/°C | - | spec | same |

## Open validation limits

- The purchased EMG module is defective: there is no real EMG data, and it is
  unknown whether it outputs a raw or envelope signal, nor its gain or supply.
- The Amazon listings for the EMG and the IMU could not be opened (HTTP 503):
  exact model and seller unconfirmed; the identification of the EMG as Muscle
  Sensor V3 is the firmware README's.
- Cord: the manufacturer's figure agrees neither with the bench nor with the
  back; hysteresis, creep, lot variation and temperature drift have no figure.
  Bench and back cannot be linked, and only one cord has been measured.
- EMG literature: sources read with figures for baseline, ECG, mains and the
  percentage drop of the median frequency are missing; SENIAM could not be
  opened.
- MPU-6050: table values marked (*) because of scrambled extraction; DLPF 44 Hz
  (bandwidth and delay) unverified in the register map.
- Uno ADC: reference, maximum rate and accuracy have no source read (404).
- No figure in this document validates that the simulator reproduces the real
  hardware; it serves to expose parameters with their provenance.
