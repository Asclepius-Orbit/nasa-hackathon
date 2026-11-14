"""Emulador del Arduino: núcleo puro (líneas por tiempo de dispositivo), servidor TCP y CLI.

El núcleo no usa sockets ni reloj de pared: el test le da los milisegundos de dispositivo. El servidor se prueba con TCP real
en localhost (puerto efímero) y un ``sleep`` que solo cede el control; el reloj y el ``sleep`` los controla ``DeviceClock``.
"""

import asyncio
import contextlib
import hashlib
import runpy
import statistics
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from spine_gateway import emulator
from spine_gateway.emulator import EmulatedArduino
from spine_gateway.protocol import BadLine, Frame, FrameKind, Hello, Message, Tick, parse_line

SAMPLES = {FrameKind.EMG: 100, FrameKind.IMU: 10, FrameKind.CORD: 2}


def parsed(lines: list[str]) -> list[Message | BadLine | str]:
    """Cada línea ya interpretada; los comentarios ``#`` se dejan como texto (los parsers del gateway los descartan)."""
    return [line if line.startswith("#") else parse_line(line) for line in lines]


def frames(lines: list[str], kind: FrameKind | None = None) -> list[Frame]:
    return [m for m in parsed(lines) if isinstance(m, Frame) and (kind is None or m.kind is kind)]


def run(lines_seconds: float, **options: Any) -> tuple[EmulatedArduino, list[str]]:
    arduino = EmulatedArduino(**options)
    return arduino, arduino.lines_until(round(lines_seconds * 1000))


# --- núcleo: hello, ticks y lotes ---


def test_the_first_call_emits_the_hello_of_the_boot() -> None:
    arduino = EmulatedArduino(seed=3, boot_id=1234)
    (hello,) = parsed(arduino.lines_until(0))
    assert hello == Hello(version=1, boot_id=1234, firmware_version=emulator.FIRMWARE_VERSION, millis=0)


def test_every_line_is_a_valid_protocol_line() -> None:
    _, lines = run(5)
    assert lines and not any(isinstance(m, BadLine) for m in parsed(lines))
    assert all("\n" not in line and "\r" not in line for line in lines)


def test_each_100_ms_emits_one_batch_per_sensor_with_the_firmware_sizes() -> None:
    arduino = EmulatedArduino()
    arduino.lines_until(0)
    batch = frames(arduino.lines_until(100))
    assert [(f.kind, len(f.samples), f.frame_seq, f.t0_ms) for f in batch] == [
        (FrameKind.EMG, 100, 0, 0),
        (FrameKind.IMU, 10, 0, 0),
        (FrameKind.CORD, 2, 0, 0),
    ]
    assert all(len(sample) == 6 for sample in batch[1].samples if isinstance(sample, tuple))
    second = frames(arduino.lines_until(200))
    assert [(f.frame_seq, f.t0_ms) for f in second] == [(1, 100)] * 3


def test_nothing_is_due_before_the_next_100_ms() -> None:
    arduino = EmulatedArduino()
    arduino.lines_until(100)
    assert arduino.lines_until(199) == []
    assert len(frames(arduino.lines_until(200))) == 3


def test_a_late_caller_gets_every_missed_step_in_order() -> None:
    arduino = EmulatedArduino()
    arduino.lines_until(0)
    batch = frames(arduino.lines_until(300), FrameKind.EMG)
    assert [(f.frame_seq, f.t0_ms) for f in batch] == [(0, 0), (1, 100), (2, 200)]


def test_asking_again_for_the_same_or_an_earlier_time_emits_nothing() -> None:
    arduino = EmulatedArduino()
    assert arduino.lines_until(500)
    assert arduino.lines_until(500) == []
    assert arduino.lines_until(100) == []


def test_each_second_repeats_the_hello_and_emits_the_tick_before_that_boundary_batch() -> None:
    arduino = EmulatedArduino(boot_id=77)
    arduino.lines_until(900)
    messages = parsed(arduino.lines_until(1000))
    assert messages[:2] == [Hello(1, 77, emulator.FIRMWARE_VERSION, 1000), Tick(77, 1000)]
    assert [m.kind for m in messages[2:] if isinstance(m, Frame)] == [FrameKind.EMG, FrameKind.IMU, FrameKind.CORD]
    _, lines = run(3.5, boot_id=77)
    ticks = [m for m in parsed(lines) if isinstance(m, Tick)]
    assert ticks == [Tick(77, 1000), Tick(77, 2000), Tick(77, 3000)]
    hellos = [m for m in parsed(lines) if isinstance(m, Hello)]
    assert [h.millis for h in hellos] == [0, 1000, 2000, 3000] and {h.boot_id for h in hellos} == {77}


def test_frame_seq_and_t0_are_consecutive_per_kind() -> None:
    _, lines = run(2)
    for kind in FrameKind:
        batch = frames(lines, kind)
        assert [f.frame_seq for f in batch] == list(range(20)) and [f.t0_ms for f in batch] == [100 * i for i in range(20)]
        assert all(len(f.samples) == SAMPLES[kind] for f in batch)


def test_frame_seq_wraps_at_u16() -> None:
    _, lines = run(0.3, start_seq=0xFFFE)
    for kind in FrameKind:
        assert [f.frame_seq for f in frames(lines, kind)] == [0xFFFE, 0xFFFF, 0]


def test_the_default_first_frame_seq_is_zero_and_an_explicit_one_is_honoured() -> None:
    assert frames(run(0.1)[1])[0].frame_seq == 0
    assert frames(run(0.1, start_seq=40)[1])[0].frame_seq == 40


# --- núcleo: fallos ---


def test_drop_every_skips_each_nth_batch_of_every_kind_but_consumes_its_frame_seq() -> None:
    _, lines = run(1.1, drop_every=3)
    for kind in FrameKind:
        batch = frames(lines, kind)
        assert [f.frame_seq for f in batch] == [0, 1, 3, 4, 6, 7, 9, 10]
        assert [f.t0_ms for f in batch] == [100 * s for s in (0, 1, 3, 4, 6, 7, 9, 10)]


def test_the_batches_that_survive_a_drop_carry_the_same_samples_as_without_it() -> None:
    clean = {(f.kind, f.frame_seq): f for f in frames(run(1.1)[1])}
    kept = frames(run(1.1, drop_every=4)[1])
    assert kept and all(clean[(f.kind, f.frame_seq)] == f for f in kept)
    assert {(f.kind, f.frame_seq) for f in kept} == {k for k in clean if (k[1] + 1) % 4}


def test_without_drop_every_nothing_is_dropped() -> None:
    assert len(frames(run(1)[1])) == 30


def test_without_an_imu_no_imu_frames_are_emitted_and_imu_absent_is_said_once() -> None:
    _, lines = run(3, imu_present=False)
    assert not frames(lines, FrameKind.IMU)
    assert len(frames(lines, FrameKind.EMG)) == len(frames(lines, FrameKind.CORD)) == 30
    assert lines.count("#imu absent") == 1
    assert parsed(lines)[1] == "#imu absent"  # justo tras el hello
    assert "#imu absent" not in run(3)[1]


def test_the_imu_drop_counter_is_independent_of_the_other_kinds_when_there_is_no_imu() -> None:
    _, lines = run(0.6, imu_present=False, drop_every=2)
    assert [f.frame_seq for f in frames(lines, FrameKind.EMG)] == [0, 2, 4]


def test_reboot_changes_the_boot_id_and_restarts_seq_and_millis() -> None:
    arduino = EmulatedArduino(boot_id=500, reboot_after_s=1.5)
    messages = parsed(arduino.lines_until(2500))
    hellos = [(i, m) for i, m in enumerate(messages) if isinstance(m, Hello)]
    first, reboot = hellos[0][1], next(m for _, m in hellos if m.boot_id != 500)
    assert first.boot_id == 500 and reboot.millis == 0 and reboot.boot_id == emulator.next_boot_id(500)
    start = messages.index(reboot)
    before = [m for m in messages[:start] if isinstance(m, Frame)]
    after = [m for m in messages[start:] if isinstance(m, Frame)]
    assert before[-3].frame_seq == 13 and before[-1].t0_ms == 1300  # el último lote completo antes de los 1500 ms
    for kind in FrameKind:
        assert [(f.frame_seq, f.t0_ms) for f in after if f.kind is kind] == [(i, 100 * i) for i in range(10)]
    ticks_after = [m for m in messages[start:] if isinstance(m, Tick)]
    assert ticks_after == [Tick(emulator.next_boot_id(500), 1000)] and arduino.boot_id == emulator.next_boot_id(500)


def test_after_the_reboot_the_clock_counts_from_zero_and_it_reboots_only_once() -> None:
    arduino = EmulatedArduino(boot_id=9, reboot_after_s=1.0)
    messages = parsed(arduino.lines_until(6000))
    assert {m.boot_id for m in messages if isinstance(m, Hello)} == {9, emulator.next_boot_id(9)}
    assert arduino.millis == 5000  # 6000 ms de encendido menos el reinicio a los 1000


def test_a_reboot_before_the_first_batch_is_honoured() -> None:
    arduino = EmulatedArduino(boot_id=9, reboot_after_s=0.05)
    messages = parsed(arduino.lines_until(300))
    assert [m.boot_id for m in messages if isinstance(m, Hello)] == [9, emulator.next_boot_id(9)]
    assert [f.t0_ms for f in frames(arduino.lines_until(300)) if f.kind is FrameKind.EMG] == []


def test_a_reboot_without_an_imu_says_imu_absent_again() -> None:
    _, lines = run(2, imu_present=False, reboot_after_s=1.0)
    assert lines.count("#imu absent") == 2


def test_a_reboot_restarts_the_drop_counter_too() -> None:
    _, lines = run(2.5, drop_every=3, reboot_after_s=1.0)
    messages = parsed(lines)
    boot2 = next(i for i, m in enumerate(messages) if isinstance(m, Hello) and m.millis == 0 and m != messages[0])
    emg_after = [m.frame_seq for m in messages[boot2:] if isinstance(m, Frame) and m.kind is FrameKind.EMG]
    assert emg_after[:4] == [0, 1, 3, 4]  # el contador de lotes también vuelve a cero


# --- núcleo: determinismo, arranque y validación ---


def test_the_same_seed_gives_the_same_lines_and_another_seed_different_ones() -> None:
    assert run(4, seed=5)[1] == run(4, seed=5)[1]
    assert run(4, seed=5)[1] != run(4, seed=6)[1]


def test_the_boot_id_is_derived_from_the_seed_unless_given() -> None:
    assert EmulatedArduino(seed=1).boot_id == EmulatedArduino(seed=1).boot_id == emulator.initial_boot_id(1)
    assert len({emulator.initial_boot_id(s) for s in range(20)}) == 20
    assert EmulatedArduino(seed=1, boot_id=0).boot_id == 0
    assert 0 <= emulator.initial_boot_id(99) <= 0xFFFFFFFF


@pytest.mark.parametrize("boot_id", [0, 1, 0xFFFFFFFF, 123456789])
def test_next_boot_id_always_differs_and_fits_u32(boot_id: int) -> None:
    following = emulator.next_boot_id(boot_id)
    assert following != boot_id and 0 <= following <= 0xFFFFFFFF
    assert emulator.next_boot_id(boot_id) == following


INVALID_OPTIONS = [
    ({"drop_every": -1}, "drop_every must be >= 0: -1"),
    ({"reboot_after_s": 0}, "reboot_after_s must be > 0: 0"),
    ({"reboot_after_s": -2.0}, "reboot_after_s must be > 0: -2.0"),
    ({"boot_id": -1}, "boot_id must fit u32: -1"),
    ({"boot_id": 0x100000000}, "boot_id must fit u32: 4294967296"),
    ({"start_seq": -1}, "start_seq must fit u16: -1"),
    ({"start_seq": 0x10000}, "start_seq must fit u16: 65536"),
]


@pytest.mark.parametrize(("options", "message"), INVALID_OPTIONS)
def test_invalid_options_are_rejected(options: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError) as error:
        EmulatedArduino(**options)
    assert str(error.value) == message


def test_the_extreme_valid_options_are_accepted() -> None:
    EmulatedArduino(boot_id=0xFFFFFFFF, start_seq=0xFFFF, drop_every=1, reboot_after_s=0.001)


# --- núcleo: señales sintéticas ---


def samples_of(kind: FrameKind, seconds: float, **options: Any) -> list[Any]:
    out: list[Any] = []
    for frame in frames(run(seconds, **options)[1], kind):
        out.extend(frame.samples)
    return out


def test_every_value_is_inside_its_range_over_a_minute() -> None:
    lines = run(60)[1]
    emg = [s for f in frames(lines, FrameKind.EMG) for s in f.samples]
    cord = [s for f in frames(lines, FrameKind.CORD) for s in f.samples]
    imu = [v for f in frames(lines, FrameKind.IMU) for sample in f.samples if isinstance(sample, tuple) for v in sample]
    assert all(0 <= v <= 1023 for v in emg) and all(0 < v < 1023 for v in cord)
    assert all(-0x8000 <= v <= 0x7FFF for v in imu)
    assert len(emg) == 60000 and len(cord) == 1200 and len(imu) == 6000 * 6


def test_the_emg_rests_near_20_counts_and_contracts_every_4_seconds_up_to_500_700() -> None:
    emg = samples_of(FrameKind.EMG, 12, seed=2)
    rest = [v for t, v in enumerate(emg) if t % 4000 < 1900 or t % 4000 >= 3100]
    assert 15 <= statistics.mean(rest) <= 25 and max(rest) <= 25 and min(rest) >= 15
    peaks = [max(emg[4000 * c : 4000 * (c + 1)]) for c in range(3)]
    assert all(500 <= p <= 730 for p in peaks)
    centre = [emg[4000 * c + 2500] for c in range(3)]
    assert all(c >= 480 for c in centre) and max(centre) - min(centre) <= 15  # la misma contracción cada ciclo
    assert emg[4000 * 1 + 2000] <= 40 and emg[4000 * 2 + 3000] <= 40  # sube y baja suave desde el reposo
    assert 100 < emg[2250] < 600  # a media subida


def test_the_contraction_peak_depends_on_the_seed_inside_the_range() -> None:
    peaks = {max(samples_of(FrameKind.EMG, 4, seed=s)) for s in range(12)}
    assert len(peaks) > 6 and all(500 <= p <= 730 for p in peaks)


def test_the_imu_reads_1g_on_z_with_a_small_noise_and_a_4_second_gyro_x_swing() -> None:
    imu = samples_of(FrameKind.IMU, 12, seed=4)
    ax, ay, az, gx, gy, gz = (list(axis) for axis in zip(*imu, strict=True))
    assert all(abs(v) <= 40 for v in ax + ay) and all(abs(v - 8192) <= 40 for v in az)
    assert abs(statistics.mean(az) - 8192) < 10 and abs(statistics.mean(ax)) < 10
    assert all(abs(v) <= 30 for v in gy + gz)
    amplitude = max(gx)
    assert 3000 <= amplitude <= 6030 and abs(min(gx) + amplitude) <= 70 and statistics.pstdev(ax) > 5
    # sinusoide de 4 s (100 muestras/s): máximo a 1 s, cruce a 2 s, mínimo a 3 s, de nuevo máximo a 5 s.
    assert abs(gx[100] - amplitude) <= 100 and abs(gx[300] + amplitude) <= 100 and abs(gx[200]) <= 100 and abs(gx[500] - amplitude) <= 100
    assert abs(gx[0]) <= 100 and abs(gx[400]) <= 100 and statistics.pstdev(gy) > 5 and statistics.pstdev(gz) > 5


def test_the_gyro_amplitude_depends_on_the_seed_inside_the_range() -> None:
    amplitudes = {samples_of(FrameKind.IMU, 2, seed=s)[100][3] for s in range(12)}  # gx a 1 s: el máximo de la sinusoide
    assert len(amplitudes) > 6 and all(2900 <= a <= 6100 for a in amplitudes)


def test_the_cord_oscillates_slowly_around_400() -> None:
    cord = samples_of(FrameKind.CORD, 16, seed=6)  # 20 Hz
    assert all(300 <= v <= 500 for v in cord)
    assert 380 <= statistics.mean(cord) <= 420 and max(cord) - min(cord) > 120
    assert statistics.pstdev(cord[:2]) < 30 and cord[40] > 440 and cord[120] < 360  # máximo a 2 s, mínimo a 6 s (periodo de 8 s)


def test_clamp_keeps_values_inside_the_bounds_and_rounds() -> None:
    assert emulator.clamp(5.4, 0, 10) == 5 and emulator.clamp(5.6, 0, 10) == 6
    assert emulator.clamp(-3.0, 0, 10) == 0 and emulator.clamp(99.0, 0, 10) == 10
    assert emulator.clamp(0.0, 0, 10) == 0 and emulator.clamp(10.0, 0, 10) == 10
    assert emulator.clamp(-0x9000, -0x8000, 0x7FFF) == -0x8000 and emulator.clamp(0x9000, -0x8000, 0x7FFF) == 0x7FFF


@pytest.mark.golden
def test_golden_signal_digest_for_seed_0() -> None:
    """Huella de 10 s de líneas con ``seed=0``: cualquier cambio de señal (o de formato) debe ser deliberado."""
    lines = EmulatedArduino(seed=0).lines_until(10_000)
    digest = hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()
    assert (len(lines), digest) == (EXPECTED_LINES, EXPECTED_DIGEST)


EXPECTED_LINES = 321
EXPECTED_DIGEST = "baa300ec52acecef8f30d4c6b39ca238d7746080d719c7379daf831779451f30"


# --- servidor TCP ---


class DeviceClock:
    """Reloj monótono y ``sleep`` de mentira: cada espera adelanta el reloj lo pedido (más ``lag``) y solo cede el control."""

    def __init__(self, lag: float = 0.0) -> None:
        self.now = 1000.0
        self.lag = lag
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.lag
        await asyncio.sleep(0)


async def read_lines(reader: asyncio.StreamReader, count: int) -> list[str]:
    out = []
    for _ in range(count):
        raw = await asyncio.wait_for(reader.readline(), 5)
        assert raw.endswith(b"\n")
        out.append(raw.decode("ascii").rstrip("\n"))
    return out


async def with_server(scenario: Callable[[int], Awaitable[None]], **options: Any) -> None:
    ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    options.setdefault("sleep", DeviceClock().sleep)
    task = asyncio.create_task(emulator.serve("127.0.0.1", 0, on_listening=ready.set_result, **options))
    try:
        await scenario(await asyncio.wait_for(ready, 5))
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)


def go(scenario: Callable[[int], Awaitable[None]], **options: Any) -> None:
    asyncio.run(with_server(scenario, **options))


def test_the_server_speaks_the_protocol_to_a_tcp_client() -> None:
    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        lines = await read_lines(reader, 70)
        writer.close()
        messages = parsed(lines)
        assert isinstance(messages[0], Hello) and not any(isinstance(m, BadLine) for m in messages)
        assert [f.frame_seq for f in frames(lines, FrameKind.EMG)][:5] == [0, 1, 2, 3, 4]

    go(scenario)


def test_the_server_paces_the_device_at_100_ms_per_step_with_the_injected_sleep() -> None:
    clock = DeviceClock()

    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await read_lines(reader, 40)
        writer.close()

    go(scenario, sleep=clock.sleep, clock=clock)
    assert len(clock.sleeps) >= 10 and clock.sleeps[:10] == pytest.approx([0.1] * 10)


def test_a_late_server_discounts_the_delay_of_each_sleep_instead_of_drifting() -> None:
    clock = DeviceClock(lag=0.05)  # cada espera dura 50 ms más de lo pedido

    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await read_lines(reader, 120)
        writer.close()

    go(scenario, sleep=clock.sleep, clock=clock)
    assert clock.sleeps[0] == pytest.approx(0.1)
    assert clock.sleeps[1:10] == pytest.approx([0.05] * 9)  # pide solo lo que falta para el paso, no 100 ms más


def test_a_very_late_server_does_not_wait_until_it_catches_up() -> None:
    clock = DeviceClock(lag=0.15)

    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        lines = await read_lines(reader, 120)
        writer.close()
        assert [f.frame_seq for f in frames(lines, FrameKind.EMG)] == list(range(len(frames(lines, FrameKind.EMG))))  # ninguno se salta

    go(scenario, sleep=clock.sleep, clock=clock)
    assert clock.sleeps[0] == pytest.approx(0.1) and set(clock.sleeps[1:10]) == {0.0}


def test_the_options_reach_the_device() -> None:
    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        lines = await read_lines(reader, 40)
        writer.close()
        assert not frames(lines, FrameKind.IMU) and "#imu absent" in lines
        assert [f.frame_seq for f in frames(lines, FrameKind.EMG)][:4] == [0, 2, 4, 6]
        assert parsed(lines)[0].boot_id == EmulatedArduino(seed=9).boot_id  # type: ignore[union-attr]

    go(scenario, seed=9, imu_present=False, drop_every=2)


@pytest.mark.parametrize(("options", "message"), [o for o in INVALID_OPTIONS if "boot_id" not in o[0] and "start_seq" not in o[0]])
def test_the_server_rejects_invalid_options_before_listening(options: dict[str, Any], message: str) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match=message):
            await emulator.serve("127.0.0.1", 0, sleep=DeviceClock().sleep, **options)

    asyncio.run(scenario())


def test_on_listening_reports_the_real_port_of_an_ephemeral_bind() -> None:
    async def scenario(port: int) -> None:
        assert port > 0
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()

    go(scenario)


def test_a_client_that_reconnects_finds_a_device_reset_by_dtr() -> None:
    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = await read_lines(reader, 60)
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        second = await read_lines(reader, 60)
        writer.close()
        h1, h2 = parsed(first)[0], parsed(second)[0]
        assert isinstance(h1, Hello) and isinstance(h2, Hello)
        assert h1.boot_id != h2.boot_id and h2.boot_id == emulator.next_boot_id(h1.boot_id) and h2.millis == 0
        assert [f.frame_seq for f in frames(second, FrameKind.EMG)][:3] == [0, 1, 2]
        assert [f.t0_ms for f in frames(second, FrameKind.EMG)][:3] == [0, 100, 200]
        assert [f.samples for f in frames(second, FrameKind.EMG)][:3] == [f.samples for f in frames(first, FrameKind.EMG)][:3]  # misma señal

    go(scenario)


def test_the_boot_id_after_a_reconnect_follows_the_internal_reboot() -> None:
    async def scenario(port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        first = parsed(await read_lines(reader, 120))
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        second = parsed(await read_lines(reader, 3))
        writer.close()
        boots = [m.boot_id for m in first if isinstance(m, Hello)]
        assert len(set(boots)) == 2  # se reinició a los 0,5 s
        assert [m.boot_id for m in second if isinstance(m, Hello)][0] == emulator.next_boot_id(boots[-1])

    go(scenario, reboot_after_s=0.5)


def test_clients_are_served_one_at_a_time() -> None:
    async def scenario(port: int) -> None:
        reader_a, writer_a = await asyncio.open_connection("127.0.0.1", port)
        await read_lines(reader_a, 5)
        reader_b, writer_b = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader_b.readline(), 0.3)  # B espera a que A suelte el puerto
        writer_a.close()
        await writer_a.wait_closed()
        first = await asyncio.wait_for(reader_b.readline(), 5)
        assert isinstance(parse_line(first.decode("ascii")), Hello)
        writer_b.close()

    go(scenario)


def test_cancelling_the_server_closes_the_client_connection_cleanly() -> None:
    async def scenario() -> None:
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(emulator.serve("127.0.0.1", 0, sleep=DeviceClock().sleep, on_listening=ready.set_result))
        reader, writer = await asyncio.open_connection("127.0.0.1", await asyncio.wait_for(ready, 5))
        await read_lines(reader, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)  # no se queda esperando a que el cliente cierre
        assert await asyncio.wait_for(reader.read(), 5) is not None  # hasta EOF: el servidor cerró su extremo
        assert reader.at_eof()
        writer.close()

    asyncio.run(scenario())


def test_cancelling_the_server_ends_a_handler_that_is_parked_waiting_for_the_next_step() -> None:
    """``Server.close`` no cierra las conexiones en todas las plataformas: el servidor cancela él mismo a sus manejadores."""

    async def parked(seconds: float) -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(emulator.serve("127.0.0.1", 0, sleep=parked, on_listening=ready.set_result))
        reader, writer = await asyncio.open_connection("127.0.0.1", await asyncio.wait_for(ready, 5))
        await read_lines(reader, 1)  # el manejador ya está esperando su siguiente paso
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert await asyncio.wait_for(reader.read(), 5) == b""  # y su extremo quedó cerrado
        writer.close()

    asyncio.run(scenario())


def test_the_server_stops_listening_when_cancelled() -> None:
    async def scenario() -> None:
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(emulator.serve("127.0.0.1", 0, sleep=DeviceClock().sleep, on_listening=ready.set_result))
        port = await asyncio.wait_for(ready, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)

    asyncio.run(scenario())


def test_the_server_works_without_a_listening_callback() -> None:
    async def scenario() -> None:
        task = asyncio.create_task(emulator.serve("127.0.0.1", 0, sleep=DeviceClock().sleep))
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


# --- CLI ---


def test_the_cli_defaults() -> None:
    args = emulator.parse_args([])
    assert (args.host, args.port, args.seed, args.no_imu, args.drop_every, args.reboot_after) == ("127.0.0.1", 5000, 0, False, 0, None)


def test_the_cli_reads_every_option() -> None:
    args = emulator.parse_args(["--host", "0.0.0.0", "--port", "6001", "--seed", "42", "--no-imu", "--drop-every", "7", "--reboot-after", "12.5"])
    assert (args.host, args.port, args.seed, args.no_imu, args.drop_every, args.reboot_after) == ("0.0.0.0", 6001, 42, True, 7, 12.5)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--port", "0"], "must be between 1 and 65535: 0"),
        (["--port", "65536"], "must be between 1 and 65535: 65536"),
        (["--port", "x"], "not a number: 'x'"),
        (["--drop-every", "-1"], "must be between 0 and inf: -1"),
        (["--drop-every", "1.5"], "not a number: '1.5'"),
        (["--reboot-after", "0"], "must be between 1e-09 and inf: 0"),
        (["--reboot-after", "-3"], "must be between 1e-09 and inf: -3"),
        (["--reboot-after", "soon"], "not a number: 'soon'"),
        (["--seed", "x"], "invalid int value: 'x'"),
        (["--bogus"], "unrecognized arguments: --bogus"),
    ],
)
def test_the_cli_rejects_invalid_values(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        emulator.parse_args(argv)
    assert exit_.value.code == 2 and message in capsys.readouterr().err


def test_the_cli_accepts_the_limits() -> None:
    args = emulator.parse_args(["--port", "1", "--drop-every", "0", "--reboot-after", "0.001"])
    assert (args.port, args.drop_every, args.reboot_after) == (1, 0, 0.001)
    assert emulator.parse_args(["--port", "65535"]).port == 65535
    assert emulator.parse_args(["--seed", "-4"]).seed == -4


def test_the_cli_help_describes_every_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        emulator.parse_args(["--help"])
    assert exit_.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert text.startswith(f"usage: {emulator.PROG} ") and emulator.DESCRIPTION in text
    for option, help_text in {
        "--host HOST": emulator.HELP_HOST,
        "--port PORT": emulator.HELP_PORT % {"default": emulator.DEFAULT_PORT},
        "--seed SEED": emulator.HELP_SEED,
        "--no-imu": emulator.HELP_NO_IMU,
        "--drop-every DROP_EVERY": emulator.HELP_DROP_EVERY,
        "--reboot-after REBOOT_AFTER": emulator.HELP_REBOOT_AFTER,
    }.items():
        assert option in text and help_text in text
    assert "5000" in text and emulator.PROG == "spine-arduino-emulator"


def test_main_configures_the_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    async def run_(**kwargs: Any) -> None:
        return None

    emulator.main([], run=run_)
    assert calls == [{"level": logging.INFO, "format": emulator.LOG_FORMAT}]
    assert "%(levelname)s" in emulator.LOG_FORMAT


def test_main_runs_the_server_with_the_parsed_options() -> None:
    seen: list[dict[str, Any]] = []

    async def run_(**kwargs: Any) -> None:
        seen.append({**kwargs, "loop": type(asyncio.get_running_loop())})

    emulator.main(["--host", "0.0.0.0", "--port", "6002", "--seed", "3", "--no-imu", "--drop-every", "5", "--reboot-after", "9"], run=run_)
    (kwargs,) = seen
    loop = kwargs.pop("loop")
    assert kwargs == {"host": "0.0.0.0", "port": 6002, "seed": 3, "imu_present": False, "drop_every": 5, "reboot_after_s": 9.0}
    if sys.platform == "win32":
        assert issubclass(loop, asyncio.SelectorEventLoop)


def test_main_with_no_arguments_uses_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["spine-arduino-emulator"])
    seen: list[dict[str, Any]] = []

    async def run_(**kwargs: Any) -> None:
        seen.append(kwargs)

    emulator.main(run=run_)
    assert seen == [{"host": "127.0.0.1", "port": 5000, "seed": 0, "imu_present": True, "drop_every": 0, "reboot_after_s": None}]


def test_main_exits_quietly_on_ctrl_c() -> None:
    async def run_(**kwargs: Any) -> None:
        raise KeyboardInterrupt

    emulator.main([], run=run_)  # no lanza


def test_a_stop_request_ends_main_without_error() -> None:
    """SIGTERM (``docker stop``) cancela la tarea del servidor: ``main`` termina como una parada pedida."""

    async def run_(**kwargs: Any) -> None:
        raise asyncio.CancelledError

    emulator.main([], run=run_)


def test_cancelling_the_outer_task_still_propagates_the_cancellation() -> None:
    """Solo la cancelación que no pidió el propio servidor (SIGTERM) sigue su camino: Ctrl+C no se traga."""

    async def forever(**kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        outer = asyncio.create_task(emulator.run_until_stopped(forever))
        await asyncio.sleep(0.05)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(outer, 5)

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="el bucle de Windows no admite manejadores de señales")
def test_sigterm_stops_the_emulator_cleanly() -> None:
    import signal

    async def forever(**kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        outer = asyncio.create_task(emulator.run_until_stopped(forever))
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(outer, 5)  # vuelve sin excepción: la parada fue pedida
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # el manejador se quita al salir

    asyncio.run(scenario())


def test_running_the_module_calls_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["emulator", "--port", "0"])  # inválido: argparse sale antes de abrir nada
    monkeypatch.delitem(sys.modules, "spine_gateway.emulator", raising=False)
    with pytest.raises(SystemExit) as exit_:
        runpy.run_module("spine_gateway.emulator", run_name="__main__")
    assert exit_.value.code == 2 and "--port" in capsys.readouterr().err


def test_the_sleep_default_is_asyncio_sleep() -> None:
    import inspect

    assert inspect.signature(emulator.serve).parameters["sleep"].default is asyncio.sleep
