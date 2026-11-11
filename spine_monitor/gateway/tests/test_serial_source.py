"""Fuente serie del gateway: troceo por líneas, marcas de tiempo, cortes y reconexión con backoff, sin puerto real."""

import asyncio
import itertools
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
import serial
from spine_gateway.ports import LinkDown, LinkUp, SerialEvent, SerialLine
from spine_gateway.serial_source import MAX_PENDING_BYTES, READ_TIMEOUT_S, SerialLineSource, SerialPort, open_serial_port

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakePort:
    """Puerto de mentira: reparte una lista de lecturas programadas (bytes, o excepción que se lanza)."""

    def __init__(self, script: list[bytes | Exception], read_delay: float = 0.0) -> None:
        self._script = list(script)
        self._read_delay = read_delay
        self.closed = 0
        self.sizes: list[int] = []
        self.read_threads: set[int] = set()
        self.reading = False
        self.reading_at_close: bool | None = None
        self.close_error: OSError | None = None

    @property
    def in_waiting(self) -> int:
        head = self._script[0] if self._script else b""
        return len(head) if isinstance(head, bytes) else 0

    def read(self, size: int) -> bytes:
        self.sizes.append(size)
        self.read_threads.add(threading.get_ident())
        self.reading = True
        try:
            time.sleep(self._read_delay or 0.001)  # el timeout corto del puerto real; evita girar en caliente al agotar el guion
            if not self._script:
                return b""
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            self.reading = False

    def close(self) -> None:
        self.reading_at_close = self.reading
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


class FakeOpener:
    """Opener inyectable: cada apertura consume un resultado programado (puerto o excepción); sin guion, falla."""

    def __init__(self, outcomes: list[SerialPort | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, baud: int) -> SerialPort:
        self.calls.append((url, baud))
        if not self._outcomes:
            raise OSError("sin más salidas programadas")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Clock:
    """Reloj determinista: cada llamada avanza un segundo."""

    def __init__(self) -> None:
        self._ticks = itertools.count()

    def __call__(self) -> datetime:
        return T0 + timedelta(seconds=next(self._ticks))


class Sleeps:
    """`sleep` inyectado: registra las esperas pedidas y no duerme."""

    def __init__(self) -> None:
        self.requested: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        await asyncio.sleep(0)


def make_source(
    opener: Callable[[str, int], SerialPort],
    sleep: Callable[[float], Awaitable[None]] | None = None,
    *,
    initial: float = 1.0,
    factor: float = 2.0,
    cap: float = 10.0,
) -> tuple[SerialLineSource, Sleeps]:
    sleeps = Sleeps()
    source = SerialLineSource(
        "fake://port",
        115200,
        clock=Clock(),
        sleep=sleep or sleeps,
        opener=opener,
        reconnect_initial_s=initial,
        reconnect_factor=factor,
        reconnect_max_s=cap,
    )
    return source, sleeps


async def take(source: SerialLineSource, count: int) -> list[SerialEvent]:
    events: list[SerialEvent] = []
    gen = source.events()
    try:
        async with asyncio.timeout(5):
            async for event in gen:
                events.append(event)
                if len(events) == count:
                    break
    finally:
        await gen.aclose()
    return events


def collect(source: SerialLineSource, count: int) -> list[SerialEvent]:
    return asyncio.run(take(source, count))


def lines(events: list[SerialEvent]) -> list[bytes]:
    return [e.data for e in events if isinstance(e, SerialLine)]


def kinds(events: list[SerialEvent]) -> list[str]:
    return [type(e).__name__ for e in events]


def test_emits_link_up_then_one_line_per_terminator() -> None:
    source, _ = make_source(FakeOpener([FakePort([b"AB\n", b"CD\n"])]))
    events = collect(source, 3)
    assert events[0] == LinkUp(T0)
    assert kinds(events) == ["LinkUp", "SerialLine", "SerialLine"]
    assert lines(events) == [b"AB", b"CD"]


def test_reads_ask_for_what_is_waiting_and_at_least_one_byte() -> None:
    port = FakePort([b"HELLO\n", b"", b"A\n"])
    source, _ = make_source(FakeOpener([port]))
    collect(source, 3)
    assert port.sizes[:3] == [6, 1, 2]


def test_opener_receives_url_and_baud() -> None:
    opener = FakeOpener([FakePort([b"A\n"])])
    source, _ = make_source(opener)
    collect(source, 2)
    assert opener.calls == [("fake://port", 115200)]


def test_line_split_across_reads_and_timeouts_is_reassembled() -> None:
    source, _ = make_source(FakeOpener([FakePort([b"HE", b"", b"LL", b"", b"O\n"])]))
    assert lines(collect(source, 2)) == [b"HELLO"]


def test_crlf_is_tolerated_and_empty_lines_are_ignored() -> None:
    source, _ = make_source(FakeOpener([FakePort([b"A\r\n\n\r\nB\r", b"\nC\n"])]))
    assert lines(collect(source, 4)) == [b"A", b"B", b"C"]


def test_only_one_trailing_carriage_return_is_removed() -> None:
    source, _ = make_source(FakeOpener([FakePort([b"A\r\r\n"])]))
    assert lines(collect(source, 2)) == [b"A\r"]


def test_received_at_is_taken_when_bytes_are_read_not_when_consumed() -> None:
    # LinkUp consume la marca 0; cada lectura (incluida la que devuelve b"") toma una marca al leer.
    source, _ = make_source(FakeOpener([FakePort([b"A\nB\n", b"", b"C\n"])]))
    events = collect(source, 4)
    assert events[1:] == [
        SerialLine(b"A", T0 + timedelta(seconds=1)),
        SerialLine(b"B", T0 + timedelta(seconds=1)),
        SerialLine(b"C", T0 + timedelta(seconds=3)),
    ]


def test_reads_happen_off_the_event_loop_thread() -> None:
    port = FakePort([b"A\n"])
    source, _ = make_source(FakeOpener([port]))
    collect(source, 2)
    assert port.read_threads
    assert threading.get_ident() not in port.read_threads


def test_unsynchronised_noise_beyond_limit_is_dropped_without_closing_port() -> None:
    port = FakePort([b"x" * (MAX_PENDING_BYTES + 1), b"OK\n"])
    opener = FakeOpener([port])
    source, sleeps = make_source(opener)
    assert lines(collect(source, 2)) == [b"OK"]
    assert len(opener.calls) == 1
    assert port.closed == 1  # solo el cierre final al parar el consumidor
    assert sleeps.requested == []


def test_pending_buffer_at_limit_is_kept() -> None:
    port = FakePort([b"x" * MAX_PENDING_BYTES, b"\n"])
    source, _ = make_source(FakeOpener([port]))
    assert lines(collect(source, 2)) == [b"x" * MAX_PENDING_BYTES]


def test_noise_accumulated_over_several_reads_is_dropped() -> None:
    half = MAX_PENDING_BYTES // 2 + 1
    port = FakePort([b"x" * half, b"y" * half, b"OK\n"])
    source, _ = make_source(FakeOpener([port]))
    assert lines(collect(source, 2)) == [b"OK"]


def test_read_error_closes_port_emits_link_down_and_reconnects() -> None:
    first = FakePort([b"A\n", serial.SerialException("device disconnected")])
    second = FakePort([b"B\n"])
    source, sleeps = make_source(FakeOpener([first, second]))
    events = collect(source, 5)
    assert kinds(events) == ["LinkUp", "SerialLine", "LinkDown", "LinkUp", "SerialLine"]
    down = events[2]
    assert isinstance(down, LinkDown)
    assert "device disconnected" in down.reason
    assert "SerialException" in down.reason
    assert first.closed == 1
    assert sleeps.requested == [1.0]


def test_partial_line_is_discarded_on_reconnect() -> None:
    first = FakePort([b"par", OSError("gone")])
    second = FakePort([b"tial\n"])
    source, _ = make_source(FakeOpener([first, second]))
    assert lines(collect(source, 4)) == [b"tial"]


def test_open_failures_back_off_exponentially_up_to_cap_and_report_one_link_down() -> None:
    failures: list[SerialPort | Exception] = [serial.SerialException(f"no port {i}") for i in range(5)]
    source, sleeps = make_source(FakeOpener([*failures, FakePort([b"A\n"])]))
    events = collect(source, 3)
    assert kinds(events) == ["LinkDown", "LinkUp", "SerialLine"]
    down = events[0]
    assert isinstance(down, LinkDown)
    assert down.at == T0
    assert "no port 0" in down.reason
    assert sleeps.requested == [1.0, 2.0, 4.0, 8.0, 10.0]


def test_backoff_resets_after_a_successful_open_and_each_outage_reports_link_down() -> None:
    plan: list[SerialPort | Exception] = [
        FakePort([OSError("cut 1")]),
        OSError("busy"),
        OSError("busy"),
        FakePort([OSError("cut 2")]),
        FakePort([b"Z\n"]),
    ]
    source, sleeps = make_source(FakeOpener(plan))
    events = collect(source, 6)
    assert kinds(events) == ["LinkUp", "LinkDown", "LinkUp", "LinkDown", "LinkUp", "SerialLine"]
    assert sleeps.requested == [1.0, 2.0, 4.0, 1.0]


def test_backoff_factor_one_keeps_constant_delay() -> None:
    outcomes: list[SerialPort | Exception] = [OSError("x"), OSError("x"), OSError("x"), FakePort([b"A\n"])]
    source, sleeps = make_source(FakeOpener(outcomes), factor=1.0, initial=0.5, cap=9.0)
    collect(source, 3)
    assert sleeps.requested == [0.5, 0.5, 0.5]


def test_close_failure_does_not_stop_the_reconnection() -> None:
    first = FakePort([OSError("cut")])
    first.close_error = OSError("already closed")
    source, _ = make_source(FakeOpener([first, FakePort([b"A\n"])]))
    assert lines(collect(source, 4)) == [b"A"]
    assert first.closed == 1


def test_cancelling_while_waiting_for_a_retry_stops_at_once() -> None:
    async def scenario() -> list[float]:
        waiting = asyncio.Event()
        requested: list[float] = []

        async def blocked_sleep(seconds: float) -> None:
            requested.append(seconds)
            waiting.set()
            await asyncio.Event().wait()  # no vuelve nunca: solo la cancelación lo saca

        source, _ = make_source(FakeOpener([OSError("down")]), blocked_sleep)

        async def consume() -> None:
            async for _ in source.events():
                pass

        task = asyncio.create_task(consume())
        async with asyncio.timeout(5):
            await waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return requested

    assert asyncio.run(scenario()) == [1.0]


def test_cancelling_during_a_read_waits_for_it_then_closes_the_port() -> None:
    port = FakePort([], read_delay=0.05)

    async def scenario() -> None:
        source, _ = make_source(FakeOpener([port]))
        up = asyncio.Event()

        async def consume() -> None:
            async for event in source.events():
                if isinstance(event, LinkUp):
                    up.set()

        task = asyncio.create_task(consume())
        async with asyncio.timeout(5):
            await up.wait()
            while not port.reading:
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert port.closed == 1
    assert port.reading_at_close is False  # el hilo ya había terminado de leer cuando se cerró


def test_open_serial_port_defaults_use_short_timeout_loopback() -> None:
    port = open_serial_port("loop://", 9600)
    try:
        assert port.in_waiting == 0
        assert isinstance(port, serial.SerialBase)
        assert (port.baudrate, port.timeout) == (9600, READ_TIMEOUT_S)
        assert port.read(1) == b""  # el timeout corto devuelve vacío, no cuelga
    finally:
        port.close()


@pytest.mark.integration
def test_real_pyserial_loopback_delivers_written_bytes_as_lines() -> None:
    opened: list[serial.SerialBase] = []

    def opener(url: str, baud: int) -> SerialPort:
        port = open_serial_port(url, baud)
        assert isinstance(port, serial.SerialBase)
        opened.append(port)
        return port

    async def scenario() -> list[SerialEvent]:
        source = SerialLineSource("loop://", 250000, clock=lambda: T0, opener=opener, reconnect_initial_s=0.01, reconnect_factor=2.0, reconnect_max_s=0.1)
        events: list[SerialEvent] = []
        gen = source.events()
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, LinkUp):
                        assert opened[0].write(b"EMG,1\r\nIMU,2\n") == 13
                    if len(events) == 3:
                        break
        finally:
            await gen.aclose()
        return events

    assert asyncio.run(scenario()) == [LinkUp(T0), SerialLine(b"EMG,1", T0), SerialLine(b"IMU,2", T0)]
