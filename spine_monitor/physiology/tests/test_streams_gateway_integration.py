"""Las tres señales juntas por el camino del gateway simulado (FakeGateway) con tiempo virtual y MQTT falso (#131).

Comprueba lo que solo se ve con los tres streams a la vez: frecuencias y tamaño de chunk (1000/100/20 Hz en 100 ms; 10 Hz
en 1 s para el fondo, sin EMG), continuidad de ``seq`` y de ``t0_device_ms``, validez de cada chunk contra su schema, procedencia
``simulator``, metadatos de calibración y de adquisición, reproducibilidad por semilla, y que EMG, giróscopo y cordón comparten
la fase del ejercicio. Los umbrales de fase son del modelo (hip.): no hay EMG real con el que compararlos.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import spine_physiology
import yaml
from scipy.signal import find_peaks
from spine_contracts import topics, validation
from spine_physiology import MissionGenerator
from spine_physiology.config import ExerciseProtocol, MissionConfig
from spine_physiology.device import SimDevice, plan_device_live
from spine_physiology.envelope import EnvelopeKind
from spine_physiology.gateway.app import FakeGateway
from spine_physiology.gateway.settings import GatewaySettings
from spine_physiology.signals import rep_schedule

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin carpetas hermanas
EXAMPLE = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
AST, DEV = "AST-01", "sim-01"
SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01"
PROTOCOL = "lat_pulldown_standing_v1"
RATES = {"emg": 1000.0, "imu": 100.0, "stretch": 20.0}
PER_CHUNK = {"emg": 100, "imu": 10, "stretch": 2}  # firmware: lotes de 100 ms
SESSION_SECONDS = 40.0  # lead-in de 10 s + 8 reps completas (3,6 s de periodo): suficiente para la fase, y rápido


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Client:
    """Cliente MQTT falso: guarda lo publicado, ya decodificado."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict, bool]] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        assert qos == 1
        self.published.append((topic, json.loads(payload), retain))

    async def subscribe(self, topic: str, qos: int) -> None:
        return None

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
            yield await self.queue.get()

    def chunks(self, session_id: str | None = None) -> list[dict]:
        return [p for t, p, _ in self.published if "/stream/" in t and (session_id is None or p["session_id"] == session_id)]

    def calibrations(self) -> list[tuple[str, dict]]:
        return [(t, p) for t, p, _ in self.published if "/calibration/" in t]


def config(*, quality_faults: bool = False, seed: int | None = None) -> MissionConfig:
    """Ejemplo con chunks de 100 ms, sin pérdidas ni reinicios (y sin fallos de calidad salvo que se pidan)."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["faults"]["device"]["packet_loss"]["probability"] = 0.0
    raw["faults"]["device"]["reboot"]["probability_per_hour"] = 0.0
    if not quality_faults:
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
    if seed is not None:
        raw["seed"] = seed
    return MissionConfig.model_validate(raw)


def run_gateway(
    cfg: MissionConfig,
    *,
    session_s: float,
    warmup_s: float = 2.0,
    streams: list[str] | None = None,
    generator: MissionGenerator | None = None,
) -> Client:
    """Arranca el gateway, deja `warmup_s` de fondo, abre una sesión de ejercicio y la deja correr `session_s`."""

    async def scenario() -> Client:
        clock, client = Clock(), Client()
        gateway = FakeGateway(generator or MissionGenerator(cfg), GatewaySettings(config=EXAMPLE, speed=0.0, start_day=4), clock=clock, astronaut_ids=[AST])

        async def advance(seconds: float) -> None:
            end = clock.now + timedelta(seconds=seconds)
            while True:
                await gateway.tick(client)
                if clock.now >= end:
                    return
                clock.now = min(end, clock.now + timedelta(seconds=0.1))

        await advance(warmup_s)
        command = {
            "schema_version": "1.0.0", "command_id": "c0ffee00-0000-4000-8000-000000000001", "astronaut_id": AST, "device_id": DEV,
            "issued_at": "2026-11-02T09:00:00Z", "type": "start_session", "session_id": SESSION, "session_type": "exercise",
            "protocol_id": PROTOCOL, **({"stream_ids": streams} if streams else {}),
        }  # fmt: skip
        await gateway.handle(client, topics.cmd(AST, DEV), json.dumps(command).encode())
        await advance(session_s)
        return client

    return asyncio.run(scenario())


@pytest.fixture(scope="module")
def session_client() -> Client:
    return run_gateway(config(), session_s=SESSION_SECONDS)


def by_stream(chunks: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in chunks:
        out.setdefault(c["stream_id"], []).append(c)
    return out


# --- frecuencias, tamaños y continuidad ------------------------------------------------------------


@pytest.mark.integration
def test_session_chunks_carry_the_firmware_rates_and_batch_sizes_in_100_ms(session_client):
    # config: firmware 1000/100/20 Hz y lotes de 100 ms = 100/10/2 muestras; cada array de datos coincide con n_samples.
    streams = by_stream(session_client.chunks(SESSION))
    assert set(streams) == {"emg_lat_left", "emg_lat_right", "imu_t9", "stretch_spine"}
    for stream_id, chunks in streams.items():
        kind = chunks[0]["stream_kind"]
        assert len(chunks) > 300
        for c in chunks:
            assert c["stream_kind"] == kind
            assert c["sample_rate_hz"] == RATES[kind] and c["n_samples"] == PER_CHUNK[kind]
            assert all(len(v) == PER_CHUNK[kind] for v in c["data"].values()), stream_id


@pytest.mark.integration
def test_seq_and_device_time_are_continuous_per_stream_across_100_ms_chunks(session_client):
    # Sin pérdidas ni reinicios: seq +1 por chunk y t0_device_ms avanza 100 ms (+-1 ms de redondeo del reloj de
    # dispositivo con deriva de ppm, hip. del modelo de reloj). received_at no retrocede.
    for stream_id, chunks in by_stream(session_client.chunks(SESSION)).items():
        seqs = [c["seq"] for c in chunks]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), stream_id
        steps = np.diff([c["t0_device_ms"] for c in chunks])
        assert steps.min() >= 99 and steps.max() <= 101, (stream_id, steps.min(), steps.max())
        assert abs(steps.mean() - 100.0) < 0.1
        received = [datetime.fromisoformat(c["received_at"]) for c in chunks]
        assert received == sorted(received)


@pytest.mark.integration
def test_background_runs_at_10_hz_in_one_second_chunks_without_emg_and_session_seq_continues_it(session_client):
    # config: fondo = IMU y cordón a 10 Hz en chunks de 1 s (10 muestras); el EMG no se emite fuera de sesión.
    background = [c for c in session_client.chunks() if c["session_id"] != SESSION]
    assert background and {c["stream_id"] for c in background} == {"imu_t9", "stretch_spine"}
    for c in background:
        assert c["sample_rate_hz"] == 10.0 and c["n_samples"] == 10
        assert all(len(v) == 10 for v in c["data"].values())
    for stream_id, chunks in by_stream(background).items():
        assert [c["seq"] for c in chunks] == list(range(len(chunks))), stream_id
    # el seq de la sesión continúa el del fondo del mismo stream (mismo arranque, sin huecos).
    first_session = {s: c[0]["seq"] for s, c in by_stream(session_client.chunks(SESSION)).items()}
    for stream_id in ("imu_t9", "stretch_spine"):
        assert first_session[stream_id] == len(by_stream(background)[stream_id])


# --- contrato, procedencia y metadatos -------------------------------------------------------------


@pytest.mark.contract
def test_every_published_chunk_is_valid_from_the_simulator_with_its_acquisition_metadata(session_client):
    chunks = session_client.chunks()
    assert chunks
    for c in chunks:
        assert validation.errors("telemetry/stream-chunk", c) == [], (c["stream_id"], c["seq"])
        assert c["source"] == "simulator" and c["astronaut_id"] == AST and c["device_id"] == DEV
    for c in session_client.chunks(SESSION):
        if c["stream_kind"] == "emg":
            assert c["signal_kind"] == "raw" and c["units"] == {"emg": "mV"}
            assert c["acquisition"]["adc_bits"] == 12 and c["filter"]["notch_hz"] == 50
        elif c["stream_kind"] == "imu":
            assert c["config"] == {"accel_range_g": 4, "gyro_range_dps": 500, "dlpf_hz": 44.0}
        else:
            assert c["circuit"]["adc_bits"] == 10 and c["circuit"]["series_resistor_ohm"] == 10000.0
            counts = np.array(c["data"]["adc_counts"])
            assert counts.min() >= 1 and counts.max() <= 1022  # el simulador no toca los topes del ADC


@pytest.mark.contract
def test_stretch_chunks_cite_a_calibration_that_was_retained_before_them(session_client):
    retained: dict[str, str] = {}
    cited = 0
    for topic, payload, _ in session_client.published:
        if "/calibration/" in topic:
            retained[topic] = payload["calibration_id"]
            assert validation.errors("domain/calibration", payload) == []
            assert payload["source"] == "simulator"
        elif "/stream/" in topic and payload["stream_kind"] == "stretch":
            assert retained[topics.calibration(AST, DEV, payload["stream_id"])] == payload["calibration_id"]
            cited += 1
    assert cited > 300


# --- semilla ---------------------------------------------------------------------------------------


def _bytes(client: Client) -> bytes:
    return json.dumps(client.chunks(SESSION), sort_keys=True).encode()


@pytest.mark.integration
def test_the_same_seed_gives_the_same_bytes_and_another_seed_a_different_signal():
    a = run_gateway(config(), session_s=6.0)
    b = run_gateway(config(), session_s=6.0)
    c = run_gateway(config(), session_s=6.0, generator=MissionGenerator(config(), seed=7))
    assert _bytes(a) == _bytes(b)
    assert _bytes(a) != _bytes(c)
    emg_a = np.concatenate([x["data"]["emg"] for x in a.chunks(SESSION) if x["stream_id"] == "emg_lat_left"])
    emg_c = np.concatenate([x["data"]["emg"] for x in c.chunks(SESSION) if x["stream_id"] == "emg_lat_left"])
    common = min(emg_a.size, emg_c.size)  # otra semilla cambia también la latencia y, con ella, cuántos chunks caben
    assert common > 5000 and not np.allclose(emg_a[:common], emg_c[:common])


# --- fase compartida --------------------------------------------------------------------------------


def _per_chunk_activity(client: Client) -> dict[str, np.ndarray]:
    """Actividad por chunk de 100 ms alineada por seq: RMS del EMG izquierdo, |gyro_y| medio y longitud media del cordón."""
    streams = by_stream(client.chunks(SESSION))
    n = min(len(v) for v in streams.values())
    emg = np.array([np.sqrt(np.mean(np.square(c["data"]["emg"]))) for c in streams["emg_lat_left"][:n]])
    gyro = np.array([np.mean(np.abs(c["data"]["gyro_y"])) for c in streams["imu_t9"][:n]])
    cord = np.array([np.mean(c["data"]["length_mm"]) for c in streams["stretch_spine"][:n]])
    return {"emg": emg, "gyro": gyro, "cord": cord}


@pytest.mark.integration
def test_emg_gyro_and_cord_share_the_exercise_phase(session_client):
    # Los tres se mueven con las mismas reps (hip. del modelo). EMG y cordón son campanas de la rep (pico a mitad), así
    # que su correlación por chunk es alta; el giróscopo es la derivada (nula a mitad de rep, máxima a 1/4 y 3/4), por
    # eso se compara con una media móvil de 1 s (la rep dura 3 s): la actividad de movimiento envuelve a la del EMG.
    # Umbrales con margen sobre lo observado con esta semilla: emg-cord 0,93 (umbral 0,7), emg-gyro suavizado 0,67 (umbral 0,5);
    # EMG activo/reposo 10x (umbral 5x), gyro 65x (umbral 20x), cordón +22 mm (umbral 5 mm).
    act = _per_chunk_activity(session_client)
    smooth = np.ones(10) / 10.0
    gyro = np.convolve(act["gyro"], smooth, mode="same")
    emg = np.convolve(act["emg"], smooth, mode="same")
    edge = slice(10, -10)
    assert np.corrcoef(act["emg"], act["cord"])[0, 1] > 0.7
    assert np.corrcoef(emg[edge], gyro[edge])[0, 1] > 0.5
    # En reposo (lead-in, antes de la primera rep) los tres están quietos frente al esfuerzo.
    rest, active = slice(5, 90), slice(110, 330)
    assert act["emg"][active].mean() > 5.0 * act["emg"][rest].mean()
    assert act["gyro"][active].mean() > 20.0 * act["gyro"][rest].mean()
    assert act["cord"][active].max() - act["cord"][rest].mean() > 5.0  # mm: 0,6 mm/grado x hasta 30 grados


@pytest.mark.integration
def test_gyro_rep_count_from_the_chunks_matches_the_protocol_schedule(session_client):
    # Rep contada desde los chunks (picos positivos de gyro_y, > 15 dps: el pico del modelo es 1,875 x 30 / 1,5 = 37 dps)
    # frente al calendario del protocolo: las reps cuyo pico de velocidad (a 1/4 de la rep: mitad de la subida minimum-jerk)
    # cae dentro de lo emitido.
    chunks = by_stream(session_client.chunks(SESSION))["imu_t9"]
    gyro = np.concatenate([c["data"]["gyro_y"] for c in chunks])
    peaks, _ = find_peaks(gyro, height=15.0, distance=100)
    protocol = config().protocol(PROTOCOL)
    assert isinstance(protocol, ExerciseProtocol)
    duration = gyro.size / RATES["imu"]
    expected = sum(1 for r in rep_schedule(protocol) if r.start_s + 0.25 * (r.end_s - r.start_s) <= duration - 0.1)
    assert len(peaks) == expected and expected >= 6


# --- verdad -----------------------------------------------------------------------------------------


@pytest.mark.integration
def test_the_live_session_still_announces_its_ground_truth_before_the_chunks():
    cfg = config()
    generator = MissionGenerator(cfg)
    device = SimDevice(cfg, plan_device_live(cfg, cfg.seed, AST, boot_at=T0, horizon=timedelta(hours=1)))
    live = generator.live_session(
        astronaut_id=AST, day=4, device=device, session_id=SESSION, session_type="exercise", protocol_id=PROTOCOL, stream_ids=[], start=T0
    )
    first = next(live)
    assert first.kind is EnvelopeKind.GROUND_TRUTH and first.payload["type"] == "expected_features"
    features = {f["feature_id"]: f["value"] for f in first.payload["features"]}
    protocol = cfg.protocol(PROTOCOL)
    assert isinstance(protocol, ExerciseProtocol)
    assert features["exercise.rep_count"] == protocol.planned_sets * protocol.reps_per_set
    assert {"imu.t9.sagittal.rom_deg", "emg.latissimus_dorsi.left.rms_pct_mvc"} <= set(features)
    assert next(live).kind is EnvelopeKind.STREAM_CHUNK
