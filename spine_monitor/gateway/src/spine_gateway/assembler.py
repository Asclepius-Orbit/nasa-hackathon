"""Ensamblado de los lotes ``D`` del Arduino en chunks MQTT v1 (``EmgChunk``/``ImuChunk``/``StretchChunk``).

Lógica pura y síncrona: sin I/O, sin reloj (``received_at`` y ``session_id`` los pone quien llama) y sin aleatoriedad.

Unidades (contrato ``telemetry/stream-chunk``):

- EMG: el tipo de señal es configurable (``emg_signal_kind``): el módulo (clon del Muscle Sensor V3) probablemente
  entrega la envolvente (default), pero no está confirmado con la placa (issue #13). Sin bloque ``filter``. mV en la
  entrada del ADC: ``(counts - offset) / (2^bits - 1) · vref · 1000 / gain``, con ``offset = 0`` para ``rectified`` y
  ``envelope`` y ``offset = (2^bits - 1) / 2`` para ``raw`` (señal bipolar). SUPOSICIÓN NO VERIFICADA con hardware:
  que la señal cruda llega polarizada a Vref/2 (``emg_offset_counts``).
- IMU: sensibilidades nominales de la hoja de datos del MPU-6050 (``ACCEL_LSB_PER_G``, ``GYRO_LSB_PER_DPS``).
- Cordón: ``adc_counts`` siempre. ``resistance_ohm`` por el divisor (Rs entre 5 V y A1, cordón entre A1 y GND):
  ``Rc = Rs · counts / (max - counts)``. En 0 (corto) o en el máximo (circuito abierto) no es calculable y, como
  ``number_array`` no admite ``null`` y todas las columnas llevan ``n_samples`` entradas, ese chunk no publica
  ``resistance_ohm`` (ni ``length_mm``, que sale de ella). ``length_mm`` solo con una calibración ``stretch``: curva
  lineal por tramos con recorte a sus extremos fuera de rango (flag ``out_of_calibration``); ``calibration_id`` va
  solo cuando hay ``length_mm``.

Calidad (``assessed_by: gateway``): ``score = muestras sin problema / muestras``; ``clipping`` si una muestra satura:
EMG en el máximo del ADC (``raw``, centrada: también en 0, los dos extremos son saturación; ``rectified`` y
``envelope``: 0 es el músculo en reposo, un valor sano), cordón en 0 (corto) o en el
máximo (además ``lead_off``: circuito abierto) e IMU en -32768 o 32767 en algún eje del sensor.
Tras lotes perdidos, ``packet_gap`` y el score queda como mucho en ``PACKET_GAP_SCORE``.

``seq`` va por (boot_id, stream) desde 0 y lo comparten sesión y fondo. Un lote perdido consume seq (``1 + hueco``) y
se declara en ``dropped_samples``; un duplicado o retroceso se descarta sin tocar nada (``Discarded``).

Fondo (fuera de sesión, solo IMU y cordón): ``BackgroundAccumulator`` junta 10 lotes contiguos y publica un chunk de
1 s a 10 Hz con la media de cada lote. Un hueco tira la ventana parcial (el tiempo no sería uniforme), consume un seq
por cada ventana de 1 s que tocan la parte tirada y lo perdido (``ceil((parcial + hueco) / 10)``) y la ventana nueva
empieza en el siguiente lote recibido. ``reset`` (inicio o fin de sesión) tira la ventana parcial sin consumir seq.

Límite conocido: ``t0_ms`` son los ``millis()`` u32 del Arduino y dan la vuelta a los ~49,7 días de funcionamiento
continuo; ``t0_device_ms`` volvería a empezar sin cambiar ``boot_id``. Se acepta para la misión de demostración.
"""

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from functools import reduce
from types import MappingProxyType
from typing import Any

from spine_contracts import SCHEMA_VERSION, EmgChunk, ImuChunk, QualityFlag, StretchCalibration, StretchChunk
from spine_contracts.models.telemetry.stream_chunk_schema import SignalKind

from spine_gateway.protocol import I16_MAX, I16_MIN, Frame, FrameKind, ImuSample, frame_gap
from spine_gateway.settings import GatewaySettings

BATCH_MS = 100
"""Duración de un lote del firmware."""
SAMPLES_PER_BATCH: Mapping[FrameKind, int] = MappingProxyType({FrameKind.EMG: 100, FrameKind.IMU: 10, FrameKind.CORD: 2})
SAMPLE_RATE_HZ: Mapping[FrameKind, float] = MappingProxyType({kind: n * 1000 / BATCH_MS for kind, n in SAMPLES_PER_BATCH.items()})
"""EMG 1000 Hz, IMU 100 Hz, cordón 20 Hz."""
BACKGROUND_FRAMES = 10
"""Lotes contiguos por chunk de fondo (1 s)."""
BACKGROUND_SAMPLE_RATE_HZ = 1000 / BATCH_MS
"""Una muestra (la media) por lote: 10 Hz."""
PACKET_GAP_SCORE = 0.7
"""Score máximo del chunk que sigue a lotes perdidos (mismo valor que el ejemplo de misión del simulador)."""

# Sensibilidad nominal del MPU-6050 por rango configurado (hoja de datos, tablas 6.1 y 6.2).
ACCEL_LSB_PER_G: Mapping[int, float] = MappingProxyType({2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0})
GYRO_LSB_PER_DPS: Mapping[int, float] = MappingProxyType({250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4})

_IMU_COLUMNS = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")
_ACCEL_AXES = slice(0, 3)
_GYRO_AXES = slice(3, 6)

type Chunk = EmgChunk | ImuChunk | StretchChunk
type Samples = tuple[int, ...] | tuple[ImuSample, ...]


# --- conversiones ---


def max_counts(adc_bits: int) -> int:
    """Lectura máxima del ADC: ``2^bits - 1``."""
    return (1 << adc_bits) - 1


def counts_to_mv(counts: float, *, adc_bits: int, vref_v: float, gain: float) -> float:
    """mV en la entrada del ADC divididos por la ganancia del módulo. Admite medias (float)."""
    return counts / max_counts(adc_bits) * vref_v * 1000.0 / gain


def emg_offset_counts(settings: GatewaySettings) -> float:
    """Cuentas que se restan al EMG antes de convertir a mV: media escala si es ``raw``, 0 si no.

    SUPOSICIÓN NO VERIFICADA con hardware: la señal cruda llega polarizada a Vref/2, así que tras restar la media
    escala queda bipolar. Una envolvente o una señal rectificada es unipolar y no lleva offset.
    """
    return max_counts(settings.adc_bits) / 2 if settings.emg_signal_kind is SignalKind.RAW else 0.0


def _lsb(table: Mapping[int, float], sensor: str, value: int) -> float:
    lsb = table.get(value)
    if lsb is None:
        raise ValueError(f"unsupported {sensor} range: {value} (MPU-6050 supports {', '.join(map(str, table))})")
    return lsb


def accel_to_g(raw: int, range_g: int) -> float:
    """Cuenta cruda del acelerómetro a g con la sensibilidad nominal del rango; ``ValueError`` si no es 2/4/8/16."""
    return raw / _lsb(ACCEL_LSB_PER_G, "accel", range_g)


def gyro_to_dps(raw: int, range_dps: int) -> float:
    """Cuenta cruda del giróscopo a °/s; ``ValueError`` si el rango no es 250/500/1000/2000."""
    return raw / _lsb(GYRO_LSB_PER_DPS, "gyro", range_dps)


def counts_to_resistance(counts: float, *, adc_bits: int, series_resistor_ohm: float) -> float | None:
    """Resistencia del cordón por el divisor; ``None`` en 0 o desde el máximo (corto o circuito abierto)."""
    full = max_counts(adc_bits)
    if not 0 < counts < full:
        return None
    return series_resistor_ohm * counts / (full - counts)


def resistance_to_length(resistance: float, points: Sequence[tuple[float, float]]) -> tuple[float, bool]:
    """Longitud por interpolación lineal por tramos sobre puntos (resistance_ohm, length_mm) y si cae dentro.

    Los puntos se ordenan por resistencia. Fuera del rango de la curva se recorta a la longitud del extremo más
    cercano y se devuelve ``False`` (el llamador lo marca ``out_of_calibration``); no se extrapola porque la respuesta
    del cordón no es lineal. El resultado de cada tramo se acota a sus extremos para que sea monótono en float.
    """
    curve = sorted(points)
    if resistance < curve[0][0]:
        return curve[0][1], False
    if resistance > curve[-1][0]:
        return curve[-1][1], False
    i = bisect_right([r for r, _ in curve], resistance)
    if i == len(curve):
        return curve[-1][1], True
    (r0, l0), (r1, l1) = curve[i - 1], curve[i]
    value = l0 + (resistance - r0) / (r1 - r0) * (l1 - l0)
    return min(max(value, min(l0, l1)), max(l0, l1)), True


# --- calidad ---


@dataclass(frozen=True, slots=True)
class _Quality:
    score: float = 1.0
    flags: frozenset[QualityFlag] = frozenset()

    def worst(self, other: _Quality) -> _Quality:
        return _Quality(min(self.score, other.score), self.flags | other.flags)

    def contract(self) -> dict[str, Any]:
        # Orden de los flags: el del enum del contrato, para que la salida sea determinista.
        return {"score": self.score, "flags": [f.value for f in QualityFlag if f in self.flags], "assessed_by": "gateway"}


_GAP = _Quality(PACKET_GAP_SCORE, frozenset({QualityFlag.PACKET_GAP}))


def _fraction(bad: Iterable[frozenset[QualityFlag]], total: int) -> _Quality:
    """Calidad de un canal a partir de los flags de cada muestra problemática (las limpias no se pasan)."""
    problems = list(bad)
    if not problems:
        return _Quality()
    return _Quality((total - len(problems)) / total, frozenset().union(*problems))


_CLIPPING = frozenset({QualityFlag.CLIPPING})
_OPEN = frozenset({QualityFlag.CLIPPING, QualityFlag.LEAD_OFF})
_OUT = frozenset({QualityFlag.OUT_OF_CALIBRATION})


def _emg_quality(counts: tuple[int, ...], full: int, kind: SignalKind) -> dict[str, _Quality]:
    # Cruda (centrada): 0 y el máximo son saturación. Rectificada o envolvente: cerca de 0 es el músculo en reposo y
    # no es un problema de la señal; solo satura en el máximo.
    low = 0 if kind is SignalKind.RAW else -1
    return {"emg": _fraction((_CLIPPING for c in counts if c <= low or c >= full), len(counts))}


def _cord_quality(counts: tuple[int, ...], full: int) -> dict[str, _Quality]:
    return {"adc_counts": _fraction((_OPEN if c >= full else _CLIPPING for c in counts if c <= 0 or c >= full), len(counts))}


def _imu_quality(samples: tuple[ImuSample, ...]) -> dict[str, _Quality]:
    def clipped(axes: slice) -> _Quality:
        return _fraction((_CLIPPING for s in samples if any(v in (I16_MIN, I16_MAX) for v in s[axes])), len(samples))

    return {"accel": clipped(_ACCEL_AXES), "gyro": clipped(_GYRO_AXES)}


# --- forma de las muestras ---


def scalar_samples(kind: FrameKind, samples: Samples) -> tuple[int, ...]:
    """Muestras de un lote EMG o de cordón como enteros; ``TypeError`` si son tuplas IMU (error de programación)."""
    values = tuple(s for s in samples if isinstance(s, int))
    if len(values) != len(samples):
        raise TypeError(f"{kind.name} frame with samples of the wrong shape")
    return values


def _imu_samples(samples: Samples) -> tuple[ImuSample, ...]:
    values = tuple(s for s in samples if isinstance(s, tuple))
    if len(values) != len(samples):
        raise TypeError(f"{FrameKind.IMU.name} frame with samples of the wrong shape")
    return values


def _mean(values: Iterable[int]) -> int:
    """Media entera de enteros, redondeo exacto al par en los empates (``round`` de ``Fraction``): 2.5 -> 2, 3.5 -> 4."""
    items = list(values)
    return round(Fraction(sum(items), len(items)))


# --- resultados ---


class DiscardReason(StrEnum):
    """Por qué un lote no produce chunk."""

    NO_BOOT = "no_boot"
    """Llegó antes de ningún hello: no hay ``boot_id`` con el que publicarlo."""
    UNEXPECTED_SAMPLE_COUNT = "unexpected_sample_count"
    """``n`` no es el del lote de 100 ms del kind: la frecuencia de muestreo no sería la declarada. Cuenta como perdido."""
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    NOT_BACKGROUND_STREAM = "not_background_stream"
    """EMG fuera de sesión: no se publica en fondo (su ``frame_seq`` sí se sigue)."""


@dataclass(frozen=True, slots=True)
class Discarded:
    kind: FrameKind
    frame_seq: int
    reason: DiscardReason


@dataclass(frozen=True, slots=True)
class Pending:
    """Lote absorbido por la ventana de fondo; el chunk sale al completar 10 lotes."""

    kind: FrameKind
    frame_seq: int


# --- fondo ---


@dataclass(frozen=True, slots=True)
class BackgroundWindow:
    """10 lotes contiguos reducidos a su media: ``samples`` tiene una muestra entera por lote."""

    t0_ms: int
    samples: Samples
    frames: tuple[Frame, ...]
    after_gap: bool
    """Hubo lotes perdidos entre la ventana anterior (o el último ``reset``) y esta."""


@dataclass(frozen=True, slots=True)
class BackgroundStep:
    lost_windows: int
    """Seq que el hueco de este lote consume (ventanas de 1 s perdidas); 0 si no hubo hueco."""
    window: BackgroundWindow | None


class BackgroundAccumulator:
    """Ventanas de fondo de un stream IMU o de cordón. ``add`` recibe el hueco ya calculado con ``frame_gap``."""

    def __init__(self, kind: FrameKind) -> None:
        if kind is FrameKind.EMG:
            raise ValueError("no background for EMG: it is only published during sessions")
        self._kind = kind
        self._frames: list[Frame] = []
        self._after_gap = False

    def reset(self) -> None:
        """Tira la ventana parcial y el aviso de hueco pendiente (inicio o fin de sesión, nuevo arranque)."""
        self._frames.clear()
        self._after_gap = False

    def add(self, frame: Frame, gap: int) -> BackgroundStep:
        lost = 0
        if gap > 0:
            lost = -(-(len(self._frames) + gap) // BACKGROUND_FRAMES)
            self._frames.clear()
            self._after_gap = True
        self._frames.append(frame)
        if len(self._frames) < BACKGROUND_FRAMES:
            return BackgroundStep(lost, None)
        frames = tuple(self._frames)
        window = BackgroundWindow(frames[0].t0_ms, self._average(frames), frames, self._after_gap)
        self.reset()
        return BackgroundStep(lost, window)

    def _average(self, frames: tuple[Frame, ...]) -> Samples:
        if self._kind is FrameKind.IMU:
            means: list[ImuSample] = []
            for f in frames:
                samples = _imu_samples(f.samples)
                ax, ay, az, gx, gy, gz = (_mean(s[axis] for s in samples) for axis in range(6))
                means.append((ax, ay, az, gx, gy, gz))
            return tuple(means)
        return tuple(_mean(scalar_samples(self._kind, f.samples)) for f in frames)


# --- ensamblador ---


@dataclass(slots=True)
class _Stream:
    last_frame_seq: int | None = None
    next_seq: int = 0


@dataclass(slots=True)
class _Streams:
    by_kind: dict[FrameKind, _Stream] = field(default_factory=lambda: {kind: _Stream() for kind in FrameKind})


class ChunkAssembler:
    """Estado por stream del gateway: ``seq``, último ``frame_seq`` y ventanas de fondo, por arranque del dispositivo."""

    def __init__(self, settings: GatewaySettings) -> None:
        # Valida los rangos del IMU al construir: un error de configuración no debe esperar al primer lote.
        _lsb(ACCEL_LSB_PER_G, "accel", settings.imu_accel_range_g)
        _lsb(GYRO_LSB_PER_DPS, "gyro", settings.imu_gyro_range_dps)
        self._settings = settings
        self._full = max_counts(settings.adc_bits)
        self._boot_id: int | None = None
        self._streams = _Streams()
        self._background = {kind: BackgroundAccumulator(kind) for kind in (FrameKind.IMU, FrameKind.CORD)}

    @property
    def boot_id(self) -> int | None:
        return self._boot_id

    def on_new_boot(self, boot_id: int) -> bool:
        """Hello del firmware. Con un ``boot_id`` nuevo reinicia seq, ``frame_seq`` y fondo y devuelve ``True``."""
        if boot_id == self._boot_id:
            return False
        self._boot_id = boot_id
        self._streams = _Streams()
        self.reset_background()
        return True

    def reset_background(self) -> None:
        """Inicio o fin de sesión: tira las ventanas de fondo parciales (no consume seq)."""
        for accumulator in self._background.values():
            accumulator.reset()

    def next_seq(self, kind: FrameKind) -> int:
        """Seq que llevará el próximo chunk del stream de ``kind``."""
        return self._streams.by_kind[kind].next_seq

    def session_chunk(
        self, frame: Frame, *, session_id: str, received_at: datetime, stretch_calibration: StretchCalibration | None = None
    ) -> Chunk | Discarded:
        """Un chunk por lote, con la frecuencia del lote. ``stretch_calibration``: la vigente del cordón, si hay."""
        gap = self._admit(frame)
        if isinstance(gap, Discarded):
            return gap
        stream = self._streams.by_kind[frame.kind]
        seq = stream.next_seq + gap
        stream.next_seq = seq + 1
        quality = self._frame_quality(frame.kind, frame.samples)
        if gap:
            quality = {name: q.worst(_GAP) for name, q in quality.items()}
        return self._build(
            frame.kind,
            frame.samples,
            quality,
            base={
                "session_id": session_id,
                "received_at": received_at,
                "seq": seq,
                "t0_device_ms": frame.t0_ms,
                "sample_rate_hz": SAMPLE_RATE_HZ[frame.kind],
                **({"dropped_samples": gap * len(frame.samples)} if gap else {}),
            },
            calibration=stretch_calibration,
        )

    def background_chunk(
        self, frame: Frame, *, session_id: str, received_at: datetime, stretch_calibration: StretchCalibration | None = None
    ) -> Chunk | Pending | Discarded:
        """Fondo: un chunk de 1 s a 10 Hz cada 10 lotes contiguos; mientras tanto ``Pending``. EMG: descartado."""
        gap = self._admit(frame)
        if isinstance(gap, Discarded):
            return gap
        if frame.kind is FrameKind.EMG:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.NOT_BACKGROUND_STREAM)
        stream = self._streams.by_kind[frame.kind]
        step = self._background[frame.kind].add(frame, gap)
        stream.next_seq += step.lost_windows
        window = step.window
        if window is None:
            return Pending(frame.kind, frame.frame_seq)
        # La calidad de la ventana es la peor de sus lotes, canal a canal (los lotes crudos, no las medias).
        per_frame = [self._frame_quality(f.kind, f.samples) for f in window.frames]
        quality = {name: reduce(_Quality.worst, (q[name] for q in per_frame)) for name in per_frame[0]}
        if window.after_gap:
            quality = {name: q.worst(_GAP) for name, q in quality.items()}
        seq = stream.next_seq
        stream.next_seq += 1
        return self._build(
            frame.kind,
            window.samples,
            quality,
            base={
                "session_id": session_id,
                "received_at": received_at,
                "seq": seq,
                "t0_device_ms": window.t0_ms,
                "sample_rate_hz": BACKGROUND_SAMPLE_RATE_HZ,
            },
            calibration=stretch_calibration,
        )

    # --- internos ---

    def _admit(self, frame: Frame) -> int | Discarded:
        """Hueco respecto al lote anterior del kind, o el descarte. Solo un lote admitido mueve el último ``frame_seq``."""
        if self._boot_id is None:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.NO_BOOT)
        if len(frame.samples) != SAMPLES_PER_BATCH[frame.kind]:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.UNEXPECTED_SAMPLE_COUNT)
        stream = self._streams.by_kind[frame.kind]
        gap = frame_gap(stream.last_frame_seq, frame.frame_seq)
        if gap == -1:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.DUPLICATE)
        if gap < 0:
            return Discarded(frame.kind, frame.frame_seq, DiscardReason.OUT_OF_ORDER)
        stream.last_frame_seq = frame.frame_seq
        return gap

    def _frame_quality(self, kind: FrameKind, samples: Samples) -> dict[str, _Quality]:
        if kind is FrameKind.EMG:
            return _emg_quality(scalar_samples(kind, samples), self._full, self._settings.emg_signal_kind)
        if kind is FrameKind.IMU:
            return _imu_quality(_imu_samples(samples))
        return _cord_quality(scalar_samples(kind, samples), self._full)

    def _build(self, kind: FrameKind, samples: Samples, quality: dict[str, _Quality], *, base: dict[str, Any], calibration: StretchCalibration | None) -> Chunk:
        s = self._settings
        header = {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": s.astronaut_id,
            "device_id": s.device_id,
            "source": "device",
            "boot_id": self._boot_id,
            "n_samples": len(samples),
            **base,
        }
        if kind is FrameKind.EMG:
            counts = scalar_samples(kind, samples)
            offset = emg_offset_counts(s)
            return EmgChunk.model_validate(
                {
                    **header,
                    "stream_id": s.emg_stream_id,
                    "stream_kind": "emg",
                    "signal_kind": s.emg_signal_kind.value,
                    "units": {"emg": "mV"},
                    "data": {"emg": [counts_to_mv(c - offset, adc_bits=s.adc_bits, vref_v=s.vref_v, gain=s.emg_gain) for c in counts]},
                    "quality": {"emg": quality["emg"].contract()},
                    "acquisition": {"adc_bits": s.adc_bits, "vref_v": s.vref_v, "gain": s.emg_gain, "input_pin": s.emg_input_pin},
                }
            )
        if kind is FrameKind.IMU:
            imu = _imu_samples(samples)
            columns = {
                name: [accel_to_g(v[axis], s.imu_accel_range_g) if axis < 3 else gyro_to_dps(v[axis], s.imu_gyro_range_dps) for v in imu]
                for axis, name in enumerate(_IMU_COLUMNS)
            }
            return ImuChunk.model_validate(
                {
                    **header,
                    "stream_id": s.imu_stream_id,
                    "stream_kind": "imu",
                    "units": {name: "g" if name.startswith("accel") else "deg/s" for name in _IMU_COLUMNS},
                    "data": columns,
                    "quality": {"accel": quality["accel"].contract(), "gyro": quality["gyro"].contract()},
                    "config": {"accel_range_g": s.imu_accel_range_g, "gyro_range_dps": s.imu_gyro_range_dps, "dlpf_hz": s.imu_dlpf_hz},
                }
            )
        return self._build_stretch(scalar_samples(kind, samples), quality["adc_counts"], header, calibration)

    def _build_stretch(self, counts: tuple[int, ...], adc_quality: _Quality, header: dict[str, Any], calibration: StretchCalibration | None) -> StretchChunk:
        s = self._settings
        units: dict[str, str] = {"adc_counts": "count"}
        data: dict[str, Any] = {"adc_counts": list(counts)}
        quality: dict[str, Any] = {"adc_counts": adc_quality.contract()}
        extra: dict[str, Any] = {}
        resistances = [counts_to_resistance(c, adc_bits=s.adc_bits, series_resistor_ohm=s.stretch_series_resistor_ohm) for c in counts]
        valid = [r for r in resistances if r is not None]
        if len(valid) == len(resistances):
            units["resistance_ohm"] = "ohm"
            data["resistance_ohm"] = valid
            if calibration is not None:
                points = [(p.resistance_ohm, p.length_mm) for p in calibration.curve.points]
                lengths = [resistance_to_length(r, points) for r in valid]
                units["length_mm"] = "mm"
                data["length_mm"] = [length for length, _ in lengths]
                outside = _fraction((_OUT for _, inside in lengths if not inside), len(lengths))
                quality["length_mm"] = adc_quality.worst(outside).contract()
                extra["calibration_id"] = calibration.calibration_id
        return StretchChunk.model_validate(
            {
                **header,
                "stream_id": s.stretch_stream_id,
                "stream_kind": "stretch",
                "units": units,
                "data": data,
                "quality": quality,
                **extra,
                "circuit": {
                    "adc_bits": s.adc_bits,
                    "vref_v": s.vref_v,
                    "series_resistor_ohm": s.stretch_series_resistor_ohm,
                    "input_pin": s.stretch_input_pin,
                },
            }
        )
