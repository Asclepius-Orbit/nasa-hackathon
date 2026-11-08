"""Familia ``imu`` (#44): rango de movimiento, velocidad angular pico y suavidad por plano, y RMS del acelerómetro.

Basada en el giróscopo, que no depende de la gravedad (comparable entre Tierra y microgravedad). Por cada stream
IMU declarado en la sesión, con su ``placement.spine_level`` (nunca fijado aquí):

- Plano -> eje del sensor con ``calibration.imu.mounting`` (marco corporal del simulador: sagital = rotación sobre
  el eje izquierda-derecha, frontal = sobre el anterior, transversal = sobre el craneal). El signo sigue al eje
  (``left``, ``anterior``, ``cranial`` positivos). Sin eje del sensor para un plano: ese plano es ``no_calibration``.
- ``gyro_bias_dps`` de la calibración se resta a cada eje del sensor.
- Por repetición (ventana activa, ``start <= t < end``) y plano: ``rom_deg`` = recorrido (máx − mín) del ángulo
  integrado (cada muestra con el periodo de su chunk) desde
  0 antes de la primera muestra; ``peak_angular_velocity_dps`` = máximo de ``|ω|``;
  ``smoothness_sparc`` = SPARC de ``|ω|`` (solo con todos los chunks del stream a ``SPARC_MIN_SAMPLE_RATE_HZ`` o
  más; si no, ``insufficient_sample_rate``). Sin movimiento SPARC no está definido (NaN -> ``no_data``).
- ``accel_rms_g`` = RMS de la magnitud del acelerómetro tras quitar a cada eje su media en la repetición (la
  componente estática: gravedad y ``accel_bias_g``, que esa resta ya cancela).
- Valor publicado = media sobre las repeticiones con muestras; ``n`` = cuántas. Cada repetición lee el segmento
  (``boot_id``) con más muestras en su ventana: nunca se unen dos arranques.

Motivos de ``skipped`` en orden: ``stream_missing``, la calibración (``no_calibration``/``calibration_expired``),
``no_data`` (sin segmentos con UTC o sin muestras en ninguna repetición), ``too_few_repetitions`` (ninguna). El
de SPARC por frecuencia de muestreo se da antes que estos dos últimos. Nunca lanza.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from spine_contracts import AnatomicalPlane, ImuCalibration, StreamKind
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.calibration_schema import BodyAxis, Mounting
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_contracts.models.domain.session_schema import StreamDeclaration

from spine_engine.features.context import ExtractionContext
from spine_engine.features.ordering import Segment
from spine_engine.features.registry import ComputedFeature, FamilyResult, SkippedFeature
from spine_engine.features.repetitions import RepetitionService, RepWindow
from spine_engine.features.signals import segment_samples

# Origen: catálogo de features (``smoothness_sparc``: "Needs at least ~50 Hz IMU sampling").
SPARC_MIN_SAMPLE_RATE_HZ = 50.0
# Parámetros por defecto de SPARC publicados en Balasubramanian, Melendez-Calderon, Roby-Brami y Burdet (2015),
# "On the analysis of movement smoothness", J. NeuroEng. Rehabil. 12:112: relleno de ceros 2^4, corte del
# espectro en 10 Hz y umbral de amplitud (normalizada) del 5 %.
SPARC_PAD_LEVEL = 4
SPARC_CUTOFF_HZ = 10.0
SPARC_AMPLITUDE_THRESHOLD = 0.05

TEMPLATES = (
    "imu.{spine_level}.{anatomical_plane}.rom_deg",
    "imu.{spine_level}.{anatomical_plane}.peak_angular_velocity_dps",
    "imu.{spine_level}.{anatomical_plane}.smoothness_sparc",
    "imu.{spine_level}.accel_rms_g",
)

_GYRO = ("gyro_x", "gyro_y", "gyro_z")
_ACCEL = ("accel_x", "accel_y", "accel_z")
# Eje corporal (positivo, negativo) de cada plano: el mismo marco que el simulador.
_PLANE_AXES = {
    AnatomicalPlane.SAGITTAL: (BodyAxis.LEFT, BodyAxis.RIGHT),
    AnatomicalPlane.FRONTAL: (BodyAxis.ANTERIOR, BodyAxis.POSTERIOR),
    AnatomicalPlane.TRANSVERSE: (BodyAxis.CRANIAL, BodyAxis.CAUDAL),
}

type Signal = NDArray[np.float64]


def plane_channel(mounting: Mounting, plane: AnatomicalPlane) -> tuple[str, float] | None:
    """Canal del giróscopo que mide la rotación del plano y su signo; ``None`` si el montaje no lo cubre."""
    positive, negative = _PLANE_AXES[plane]
    for channel, axis in (("gyro_x", mounting.sensor_x), ("gyro_y", mounting.sensor_y), ("gyro_z", mounting.sensor_z)):
        if axis is positive:
            return channel, 1.0
        if axis is negative:
            return channel, -1.0
    return None


def range_of_motion_deg(omega_dps: Signal, rate_hz: float | Signal) -> float:
    """Recorrido del ángulo integrado (rectángulos de ``1/rate_hz``), con ángulo 0 antes de la primera muestra.

    ``rate_hz`` puede ser una frecuencia por muestra: cada una se integra con el periodo de su chunk.
    """
    angle = np.concatenate(([0.0], np.cumsum(omega_dps / rate_hz)))
    return float(angle.max() - angle.min())


def peak_angular_velocity_dps(omega_dps: Signal) -> float:
    """Máximo de ``|ω|``."""
    return float(np.abs(omega_dps).max())


def sparc(speed: Signal, rate_hz: float) -> float:
    """SPARC (longitud de arco espectral) de un perfil de velocidad ``>= 0``; ≤ 0, más cerca de 0 = más suave. NaN si es nulo.

    Con velocidades no negativas el máximo del espectro es el de 0 Hz (la suma), así que el tramo sobre el umbral
    empieza siempre en 0 Hz y solo se recorta por arriba, como en la implementación publicada.
    """
    nfft = 2 ** (int(np.ceil(np.log2(len(speed)))) + SPARC_PAD_LEVEL)
    magnitude = np.abs(np.fft.rfft(speed, nfft))
    peak = magnitude.max()
    if peak == 0.0:
        return math.nan
    freqs = np.arange(len(magnitude)) * rate_hz / nfft
    keep = freqs <= SPARC_CUTOFF_HZ
    freqs, magnitude = freqs[keep], magnitude[keep] / peak
    last = np.nonzero(magnitude >= SPARC_AMPLITUDE_THRESHOLD)[0][-1]
    freqs, magnitude = freqs[: last + 1], magnitude[: last + 1]
    return -float(np.sum(np.sqrt((np.diff(freqs) / freqs[last]) ** 2 + np.diff(magnitude) ** 2)))


def accel_rms_g(ax: Signal, ay: Signal, az: Signal) -> float:
    """RMS de la magnitud de la aceleración dinámica (cada eje menos su media)."""
    dynamic = sum((axis - axis.mean()) ** 2 for axis in (ax, ay, az))
    return float(np.sqrt(np.mean(dynamic)))


@dataclass(frozen=True, slots=True)
class _RepSamples:
    rate_hz: float  # el chunk más lento del segmento: el de SPARC
    sample_rates_hz: Signal  # la frecuencia del chunk de cada muestra: la de la integración del ROM
    channels: dict[str, Signal]


class ImuFamily:
    """Features IMU por stream, plano y repetición, a partir de las ventanas del servicio inyectado."""

    family = "imu"
    feature_ids: Sequence[str] = tuple(feature_id for template in TEMPLATES for feature_id in expand_template(template))

    def __init__(self, repetitions: RepetitionService) -> None:
        self.repetitions = repetitions

    def extract(self, context: ExtractionContext) -> FamilyResult:
        computed: list[ComputedFeature] = []
        skipped: list[SkippedFeature] = []
        for stream in context.session.streams:
            if stream.stream_kind is StreamKind.IMU and stream.placement.spine_level is not None:
                result = self._stream(context, stream, stream.placement.spine_level.value)
                computed += result.computed
                skipped += result.skipped
        return FamilyResult(tuple(computed), tuple(skipped))

    def _stream(self, context: ExtractionContext, stream: StreamDeclaration, level: str) -> FamilyResult:
        ids = _ids(level)
        all_ids = [i for plane_ids in ids.values() for i in plane_ids] + [f"imu.{level}.accel_rms_g"]
        stream_id = stream.stream_id
        if context.stream_missing(stream_id):
            return _skip(all_ids, Reason.STREAM_MISSING)
        resolution = context.calibration(stream_id)
        calibration = resolution.calibration
        if resolution.reason is not None or not isinstance(calibration, ImuCalibration):
            return _skip(all_ids, resolution.reason or Reason.NO_CALIBRATION)
        segments = context.segments(stream_id)
        if not segments:
            return _skip(all_ids, Reason.NO_DATA)
        slow = min(c.sample_rate_hz for s in segments for c in s.chunks) < SPARC_MIN_SAMPLE_RATE_HZ
        early = _skip([plane_ids[2] for plane_ids in ids.values()], Reason.INSUFFICIENT_SAMPLE_RATE) if slow else FamilyResult()
        windows = self.repetitions.windows(context).windows
        reps = [found for window in windows if (found := _rep_samples(context, segments, window)) is not None]
        if not reps:
            reason = Reason.NO_DATA if windows else Reason.TOO_FEW_REPETITIONS
            return _merge(early, _skip([i for i in all_ids if i not in {s.feature_id for s in early.skipped}], reason))
        return _merge(early, _compute(stream_id, calibration, ids, reps, level, sparc_ok=not slow))


def _ids(level: str) -> dict[AnatomicalPlane, tuple[str, str, str]]:
    return {
        plane: (f"imu.{level}.{plane.value}.rom_deg", f"imu.{level}.{plane.value}.peak_angular_velocity_dps", f"imu.{level}.{plane.value}.smoothness_sparc")
        for plane in AnatomicalPlane
    }


def _skip(feature_ids: Sequence[str], reason: Reason) -> FamilyResult:
    return FamilyResult(skipped=tuple(SkippedFeature(feature_id, reason) for feature_id in feature_ids))


def _merge(first: FamilyResult, second: FamilyResult) -> FamilyResult:
    return FamilyResult(first.computed + second.computed, first.skipped + second.skipped)


def _rep_samples(context: ExtractionContext, segments: Sequence[Segment], window: RepWindow) -> _RepSamples | None:
    best: _RepSamples | None = None
    for segment in segments:
        found = _segment_rep(context, segment, window)
        if len(found.sample_rates_hz) > (0 if best is None else len(best.sample_rates_hz)):
            best = found
    return best


def _segment_rep(context: ExtractionContext, segment: Segment, window: RepWindow) -> _RepSamples:
    """Muestras de la ventana en un segmento, chunk a chunk para conocer la frecuencia de cada una."""
    rates: list[float] = []
    parts: dict[str, list[Signal]] = {name: [] for name in _GYRO + _ACCEL}
    for chunk in segment.chunks:
        samples = segment_samples(context.clock, replace(segment, chunks=(chunk,)), _GYRO + _ACCEL, window.active)
        rates += [chunk.sample_rate_hz] * len(samples.times)
        for name, values in samples.channels.items():
            parts[name].append(values)
    channels = {name: np.concatenate(values) for name, values in parts.items()}
    return _RepSamples(min(c.sample_rate_hz for c in segment.chunks), np.asarray(rates, dtype=np.float64), channels)


def _compute(
    stream_id: str,
    calibration: ImuCalibration,
    ids: dict[AnatomicalPlane, tuple[str, str, str]],
    reps: Sequence[_RepSamples],
    level: str,
    *,
    sparc_ok: bool,
) -> FamilyResult:
    bias = calibration.gyro_bias_dps
    offsets = {"gyro_x": bias.x, "gyro_y": bias.y, "gyro_z": bias.z}
    n = len(reps)
    computed: list[ComputedFeature] = []
    skipped: list[SkippedFeature] = []

    def add(feature_id: str, per_rep: Sequence[float]) -> None:
        computed.append(ComputedFeature(feature_id, float(np.mean(per_rep)), (stream_id,), n=n))

    for plane, (rom_id, peak_id, sparc_id) in ids.items():
        found = plane_channel(calibration.mounting, plane)
        if found is None:
            skipped += [SkippedFeature(i, Reason.NO_CALIBRATION) for i in (rom_id, peak_id, sparc_id) if sparc_ok or i != sparc_id]
            continue
        channel, sign = found
        omegas = [(sign * (r.channels[channel] - offsets[channel]), r.rate_hz) for r in reps]
        add(rom_id, [range_of_motion_deg(omega, r.sample_rates_hz) for (omega, _), r in zip(omegas, reps, strict=True)])
        add(peak_id, [peak_angular_velocity_dps(omega) for omega, _ in omegas])
        if sparc_ok:
            add(sparc_id, [sparc(np.abs(omega), rate) for omega, rate in omegas])
    add(f"imu.{level}.accel_rms_g", [accel_rms_g(*(r.channels[name] for name in _ACCEL)) for r in reps])
    return FamilyResult(tuple(computed), tuple(skipped))
