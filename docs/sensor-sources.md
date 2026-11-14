# Fuentes de los sensores para la simulación realista

Revisado el 2026-11-14 (fecha de acceso de todas las URL). Inventario de fuentes
para parametrizar el simulador de los tres sensores del wearable (Arduino Uno):
cordón elástico conductor, módulo EMG e IMU. No es un conjunto de mediciones
validado ni una norma clínica. Cada cifra lleva su fuente y una categoría:

- **hoja de especificaciones**: la declara el fabricante o el vendedor.
- **config. firmware/gateway**: la fija el código de este repositorio.
- **medición propia**: la midió el equipo con su montaje.
- **suposición sin medir**: hipótesis de trabajo sin respaldo verificado.

Lo que no se pudo verificar se dice expresamente y se marca como hipótesis. No se
han digitalizado valores de gráficas. Las hojas de datos de fabricante se
consultaron en copias de terceros (ver cada fila), no en la web oficial del
fabricante.

## Identificación de los componentes

| Sensor | Identificación | Estado |
|---|---|---|
| Cordón elástico | [Adafruit 519](https://www.adafruit.com/product/519), cordón de goma cargada con negro de humo, 2 mm de diámetro, ~1 m. | Verificado en la página del fabricante. |
| Módulo EMG | Compra en [amazon.es B0CP5F9WTN](https://www.amazon.es/dp/B0CP5F9WTN). El README del firmware lo llama Muscle Sensor V3 (salida SIG). | **No verificado.** La ficha de Amazon devolvió HTTP 503 y una búsqueda por el ASIN no la localizó. No se afirma modelo, ganancia ni tensión de alimentación reales de la unidad comprada. El módulo es defectuoso y no hay mediciones fiables. |
| IMU | [Amazon B07BVXN2GP](https://www.amazon.es/dp/B07BVXN2GP), identificada por el equipo como MPU-6050 (placa tipo GY-521). | La ficha de Amazon devolvió HTTP 503; la identificación es la del equipo, no se pudo confirmar en la ficha. Las cifras de abajo son las del chip, no de la placa concreta. |

## Hechos conocidos del repositorio

| Hecho | Valor | Categoría | Dónde |
|---|---|---|---|
| Velocidad de muestreo EMG | 1000 Hz (A0, `analogRead` 0..1023) | config. firmware | `firmware/README.md`, tabla de pines y protocolo |
| Velocidad de muestreo IMU | 100 Hz, 6 ejes int16 | config. firmware | `firmware/README.md` |
| Velocidad de muestreo cordón | 20 Hz (A1) | config. firmware | `firmware/README.md` |
| Lotes | 100 ms: 100 muestras EMG, 10 IMU, 2 del cordón | config. firmware | `firmware/README.md`, protocolo `D,E/I/S` |
| Configuración MPU-6050 | `PWR_MGMT_1=0`, DLPF 44 Hz (`CONFIG=0x03`), ±500 dps (`GYRO_CONFIG=0x08`), ±4 g (`ACCEL_CONFIG=0x08`) | config. firmware/gateway | `firmware/README.md`; `spine_monitor/gateway/src/spine_gateway/settings.py` (`imu_gyro_range_dps=500`, `imu_dlpf_hz=44`) |
| Divisor del cordón | 5 V, 10 kΩ, punto medio en A1, cordón a GND | config. firmware/gateway | `firmware/README.md`; `settings.py` (`stretch_series_resistor_ohm=10000`) |
| Tipo de señal EMG | `envelope` por defecto, "asumida, no confirmada con el módulo" | suposición sin medir | `firmware/README.md`; `settings.py` (`emg_signal_kind`, `emg_gain=1.0`) |
| Cordón: colocación | C3 a S1, ~650 mm en reposo, pendiente de medir | suposición sin medir | `firmware/README.md` |
| Serie | 250000 baudios, 8N1 | config. firmware | `firmware/README.md` |

### Medidas de banco del cordón

Contactos de 10 cm. Longitud total en cm y resistencia en Ω (tres lecturas por
longitud; el orden de las columnas es el del registro del equipo y no se ha
inferido su significado).

| Longitud total (cm) | Ω (lecturas) |
|---|---|
| 100 | 630 / 780 / 795 |
| 110 | 1020 / 1165 / 1125 |
| 120 | 1110 / 1255 / 1260 |
| 130 | 1270 / 1320 / 1300 |
| 140 | 1350 / 1355 / 1350 |

Categoría: medición propia (banco, un cordón). Dos observaciones, sin más
inferencia: la resistencia crece de forma sublineal con la longitud, y la
primera lectura de cada longitud es la más baja salvo en 140 cm. No hay medición
de temperatura, velocidad de estiramiento ni orden de las lecturas.

### Referencia en la espalda

Contactos a 50 cm: 4500 Ω de pie recto y 5650 Ω en flexión máxima. Categoría:
medición propia, informativa y por astronauta. El montaje de banco y el de
espalda no son enlazables: distinta longitud de contactos, distinto cordón
posible y distinta tensión inicial. No se debe interpolar entre ambas tablas.

## Cordón conductor Adafruit 519

| Aspecto | Dato | Categoría | Fuente |
|---|---|---|---|
| Resistencia en reposo | 350-400 Ω/pulgada, es decir 140-160 Ω/cm (la página lo indica así). Ejemplo: 6 in ~ 2,1 kΩ. | hoja de especificaciones | [Adafruit 519](https://www.adafruit.com/product/519) |
| Estirado | 6 in a 10 in lee ~3,5 kΩ (ejemplo de la página). Se puede estirar un 50-70 %. | hoja de especificaciones | ídem |
| No linealidad | "La respuesta no es realmente lineal". | hoja de especificaciones (cualitativa) | ídem |
| Variación entre lotes | La resistencia "puede variar de un lote a otro". Sin cifra. | hoja de especificaciones (cualitativa) | ídem |
| Histéresis y recuperación | Tras soltar, tarda "uno o dos minutos" en recuperar la longitud. No se cuantifica la histéresis ni se habla de fluencia (creep). | hoja de especificaciones (cualitativa) | ídem |
| Uso previsto | Detección de movimiento de estiramiento, no medida de precisión. Kit con resistencia de 10 kΩ 1 %. | hoja de especificaciones | ídem |
| Divisor y ADC | V = 5 V · R / (R + 10 kΩ); cuentas = V/5 V · 1023. Ejemplos calculados: 630 Ω -> ~61; 1350 Ω -> ~122; 4500 Ω -> ~317; 5650 Ω -> ~369. | cálculo a partir de config. | `settings.py` y ADC de 10 bits |

Contraste con el banco: los ~140-160 Ω/cm de la página implican ~14-16 kΩ para
100 cm, mientras que el banco da 0,6-0,8 kΩ para 100 cm totales y la espalda
4,5 kΩ a 50 cm de contactos. Las medidas del equipo no concuerdan con la cifra
del fabricante (ni entre sí por cm), lo que indica que la resistencia depende de
cómo se hacen los contactos y del cordón concreto. El simulador no debe usar la
cifra del fabricante para reproducir el banco.

Sin fuente verificada: cuantía de histéresis, fluencia, deriva por temperatura y
ruido de resistencia. Son hipótesis; ver parámetros.

## Módulo EMG

| Aspecto | Dato | Categoría | Fuente |
|---|---|---|---|
| Familia de referencia | Muscle Sensor V3 (Advancer Technologies). Es la referencia que cita el firmware; la unidad comprada no está confirmada. | hoja de especificaciones de un modelo candidato | [Manual de usuario v3 (4 feb 2013)](https://cdn.sparkfun.com/datasheets/Sensors/Biometric/Muscle%20Sensor%20v3%20Users%20Manual.pdf) |
| Alimentación | ±3 V mín., ±5 V típ., ±30 V máx. (doble fuente, p. ej. dos pilas de 9 V). Los minoristas indican ±5 V típ. | hoja de especificaciones (modelo candidato) | ídem; [Pololu 2726](https://www.pololu.com/product/2726) |
| Ganancia | G = 207 · (X / 1 kΩ), con X el potenciómetro; 0,002x mín., 10 350x típ., 20 700x máx. | hoja de especificaciones | manual v3, tabla de especificaciones eléctricas |
| Salida | No es EMG crudo: señal "amplificada, rectificada y suavizada" (envolvente), de 0 V a +Vs. | hoja de especificaciones | manual v3, apartado "RAW EMG vs Rectified & Smoothed EMG" |
| Tensión diferencial de entrada | 0 mV mín.; 2-5 mV típ.; +Vs/Ganancia máx. | hoja de especificaciones | manual v3 |
| Ruido, CMRR, impedancia de entrada, ancho de banda | **No verificados** en la copia leída (no se extrajeron). | hipótesis | - |
| Módulo comprado | Defectuoso, sin mediciones fiables. No se puede declarar ni raw ni envolvente ni ganancia real. | medición propia (negativa) | decisión del equipo |

Nota de interfaz: con alimentación de ±Vs la salida puede superar los 5 V que
tolera el ADC del Uno. El README del firmware ya lo advierte (limitar a 5 V).
Si el módulo fuese un MyoWare posterior, su alimentación es de una sola tensión
(2,9-5,7 V según un minorista), pero no se ha confirmado que lo sea
([resultados de búsqueda](https://core-electronics.com.au/advancer-technologies-muscle-sensor-v3.html)).

### EMG de superficie en general (literatura)

| Aspecto | Dato | Categoría | Fuente |
|---|---|---|---|
| Amplitud | 50 µV a 100 mV (cita de Pancholi y Joshi, 2018). La señal bruta suele estar en el orden de µV a unos pocos mV. | literatura (rango amplio) | [Sun et al., Front. Syst. Neurosci. 2022](https://www.frontiersin.org/articles/10.3389/fnsys.2022.893275) |
| Banda de frecuencia | 10-500 Hz según esa revisión. Se emplea con frecuencia 20-450 Hz (p. ej. estudios que filtran a 20-450 Hz y muestrean a 1515 Hz). | literatura | ídem; resultados de búsqueda en [arXiv 2309.04770](https://arxiv.org/pdf/2309.04770) |
| Red eléctrica y artefactos de movimiento | La señal bruta se mezcla "inevitablemente" con interferencia de red y artefactos de movimiento. Sin magnitud. 50 Hz en Europa. | literatura (cualitativa) | revisión de Sun et al. 2022 |
| Deriva de línea base y contaminación por ECG | **No verificadas con cifra** en las fuentes leídas. Se tratan como fenómenos conocidos, no cuantificados. | hipótesis | - |
| Descenso de la frecuencia mediana con fatiga | La mediana cae, de forma aproximadamente lineal en el tiempo en contracciones sostenidas; la amplitud sube a fuerza constante. Sin porcentaje. | literatura (cualitativa) | revisión de Sun et al. 2022; [arXiv physics/0611120](https://arxiv.org/pdf/physics/0611120) |
| Concentración de energía en 50-150 Hz | **No verificada**; solo aparece como métrica de ruido en un artículo. | hipótesis | - |
| Recomendaciones SENIAM (distancia entre electrodos, etc.) | No consultadas: el servidor de SENIAM devolvió un error de certificado. | no verificado | [seniam.org](https://www.seniam.org/) |

Para un valor de uso en el simulador, el rango de amplitud en mV de un músculo
dorsal concreto con ese electrodo no está respaldado por una fuente leída aquí.

## IMU MPU-6050

Fuente de todas las cifras de esta sección: InvenSense, *MPU-6000/MPU-6050
Product Specification*, PS-MPU-6000A-00, revisión 3.4, 19 ago 2013. Se leyó la
[copia de mikrocontroller.net](https://www.mikrocontroller.net/attachment/236856/MPU-6050.pdf)
(PDF de 52 páginas). La extracción del texto de las tablas de especificaciones
salió con las celdas desordenadas, así que el emparejamiento de valores y
unidades se ha hecho con ese desorden en mente y los valores marcados con (*)
deben contrastarse con la hoja original antes de tomarlos como definitivos.
Categoría: hoja de especificaciones (del chip, VDD 2,375-3,46 V, 25 °C).

| Aspecto | Dato |
|---|---|
| Sensibilidad del giroscopio | 131 / 65,5 / 32,8 / 16,4 LSB/(°/s) para ±250 / ±500 / ±1000 / ±2000 °/s. A ±500 °/s: **65,5 LSB/(°/s)**. |
| Sensibilidad del acelerómetro | 16384 / 8192 / 4096 / 2048 LSB/g para ±2 / ±4 / ±8 / ±16 g. A ±4 g: **8192 LSB/g**. |
| Palabra ADC | 16 bits, complemento a dos. |
| Tolerancia de sensibilidad del giroscopio | ±3 % inicial a 25 °C (*); variación con la temperatura ±2 % (*); no linealidad 0,2 % (*). |
| Offset inicial del giroscopio (ZRO) | ±20 °/s a 25 °C; variación con la temperatura ±20 °/s (-40 a +85 °C). |
| Ruido del giroscopio | 0,05 °/s rms total (*); 0,033 °/s rms de 1 a 10 Hz (*); densidad espectral 0,005 °/s/√Hz a 10 Hz (*). |
| Offset del acelerómetro (cero g) | ±50 mg en X e Y, ±80 mg en Z (*) con calibración inicial; cambio con la temperatura ±35 mg X/Y y ±60 mg Z, de 0 a 70 °C (*). Deriva de sensibilidad con la temperatura ±0,02 %/°C. |
| Ruido del acelerómetro | Densidad espectral de potencia 400 µg/√Hz (a 10 Hz, ±2 g, 1 kHz). |
| Sensor de temperatura | 340 LSB/°C, desplazamiento -521 LSB a 35 °C. |
| Rango de filtro paso bajo | 5-256 Hz (giroscopio) y 5-260 Hz (acelerómetro); frecuencia de salida programable 4-8000 Hz (giroscopio). |
| DLPF a 44 Hz | El ajuste 44 Hz del firmware es `CONFIG=0x03`. El ancho de banda exacto por eje (acelerómetro 44 Hz, giroscopio 42 Hz) y el retardo vienen en el mapa de registros (documento RM-MPU-6000A) que **no se consultó**; queda como hipótesis hasta verificarlo. |

Aviso: estas cifras son del chip. La placa vendida en Amazon puede incluir
regulador y pull-ups y no se ha comprobado que el chip sea auténtico. Las
especificaciones de ruido dependen de la configuración de la hoja; con DLPF de
44 Hz y 100 Hz de muestreo el ruido rms resultante no se ha calculado a partir
de fuente.

## Convertidor analógico-digital del Arduino Uno

| Aspecto | Dato | Categoría | Fuente |
|---|---|---|---|
| Resolución | 10 bits, valores 0-1023 | config. firmware | `firmware/README.md` (`analogRead` 0..1023) |
| Referencia | 5 V (cuenta = V/5 · 1023, ~4,9 mV/LSB) | hipótesis sin fuente leída | la página de `analogRead` de Arduino devolvió HTTP 404 en esta revisión |
| Frecuencia máxima | ~10 kHz (~100 µs por lectura) | hipótesis sin fuente leída | ídem (recuerdo de la documentación de Arduino, no verificado) |
| Exactitud absoluta del ADC | No consultada la hoja del ATmega328P | no verificado | - |

## Tabla de parámetros para el simulador

Estado: spec = hoja de especificaciones; config = firmware/gateway; medido =
medición propia; hip. = hipótesis sin respaldo verificado.

### Común

| Nombre | Valor por defecto | Unidad | Estado | Fuente |
|---|---|---|---|---|
| `adc_bits` | 10 | bits | config | firmware README |
| `adc_vref` | 5.0 | V | hip. | ADC del Uno, sin fuente leída |
| `batch_ms` | 100 | ms | config | firmware README |

### Cordón (`stretch`)

| Nombre | Valor por defecto | Unidad | Estado | Fuente |
|---|---|---|---|---|
| `stretch_rate_hz` | 20 | Hz | config | firmware README |
| `series_resistor_ohm` | 10000 | Ω | config | `settings.py` |
| `r_curve_bench` | tabla 100-140 cm (ver arriba) | Ω | medido | banco del equipo |
| `r_back_straight` / `r_back_flexion` | 4500 / 5650 (contactos a 50 cm) | Ω | medido (por astronauta) | espalda del equipo |
| `r_per_cm_manufacturer` | 140-160 | Ω/cm | spec | Adafruit 519 (no concuerda con el banco) |
| `max_stretch_fraction` | 0.5-0.7 | fracción | spec | Adafruit 519 |
| `recovery_time_s` | 60-120 | s | spec (cualitativa) | "uno o dos minutos" |
| `hysteresis_fraction` | 0.05 | fracción | hip. | sin fuente cuantitativa |
| `creep_fraction` | 0.02 | fracción | hip. | sin fuente cuantitativa |
| `lot_variation_fraction` | 0.10 | fracción | hip. | Adafruit solo dice que existe |
| `read_noise_ohm` | 5 | Ω | hip. | las lecturas repetidas del banco difieren hasta ~150 Ω entre lecturas de la misma longitud, no es ruido puro |

### EMG (`emg`)

| Nombre | Valor por defecto | Unidad | Estado | Fuente |
|---|---|---|---|---|
| `emg_rate_hz` | 1000 | Hz | config | firmware README |
| `signal_kind` | envelope | enum | hip. | no confirmado con el módulo; defecto del gateway |
| `gain` | 1.0 | - | config | `settings.py` `emg_gain`; la real del módulo es desconocida |
| `band_hz` | 20-450 | Hz | literatura | Sun et al. 2022 y estudios que usan esa banda |
| `mains_hz` | 50 | Hz | hip. | red europea, sin cifra de magnitud |
| `mains_amplitude`, `baseline_wander`, `ecg_leak` | 0 (apagado) | - | hip. | sin cifra verificada |
| `fatigue_median_freq_drop` | 0 (apagado) | % | hip. | solo cualitativa en la literatura |
| `envelope_full_scale` | 0-1023 cuentas | cuentas | config | ADC 10 bits |

### IMU (`imu`)

| Nombre | Valor por defecto | Unidad | Estado | Fuente |
|---|---|---|---|---|
| `imu_rate_hz` | 100 | Hz | config | firmware README |
| `accel_range_g` | 4 | g | config | firmware README |
| `gyro_range_dps` | 500 | dps | config | `settings.py` |
| `dlpf_hz` | 44 | Hz | config | `settings.py` |
| `accel_lsb_per_g` | 8192 | LSB/g | spec | MPU-6050 datasheet rev. 3.4 |
| `gyro_lsb_per_dps` | 65.5 | LSB/dps | spec | ídem |
| `gyro_noise_density` | 0.005 (*) | dps/√Hz | spec | ídem, contrastar |
| `accel_noise_density` | 400 | µg/√Hz | spec | ídem |
| `gyro_bias_max` | 20 | dps | spec (máximo, no típico) | ídem |
| `accel_bias_max` | 0.05 (X/Y), 0.08 (Z) (*) | g | spec (máximo) | ídem, contrastar |
| `temp_drift` | gyro ±20 dps, accel ±0.02 %/°C | - | spec | ídem |

## Límites de validación abiertos

- El módulo EMG comprado es defectuoso: no hay datos reales de EMG ni se sabe si
  entrega señal cruda o envolvente, su ganancia ni su alimentación.
- Las fichas de Amazon de EMG e IMU no se pudieron abrir (HTTP 503): modelo exacto
  y vendedor sin confirmar; la identificación del EMG como Muscle Sensor V3 es la
  del README del firmware.
- Cordón: la cifra del fabricante no concuerda con el banco ni con la espalda;
  histéresis, fluencia, variación de lote y deriva por temperatura no tienen cifra.
  Banco y espalda no son enlazables, y solo hay un cordón medido.
- Literatura EMG: faltan fuentes leídas con cifras de línea base, ECG, red
  eléctrica y descenso porcentual de la frecuencia mediana; SENIAM no se pudo abrir.
- MPU-6050: valores de tablas marcados (*) por extracción desordenada; DLPF 44 Hz
  (ancho de banda y retardo) sin verificar en el mapa de registros; no se probó la
  placa real.
- ADC del Uno: referencia, velocidad máxima y exactitud sin fuente leída (404).
- Ninguna cifra de este documento valida que el simulador reproduzca el
  hardware real; sirve para exponer parámetros con su procedencia.
