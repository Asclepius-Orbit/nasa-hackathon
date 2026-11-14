"""Arduino falso del wearable: habla el protocolo de líneas por TCP, para probar el gateway sin placa.

El gateway real lee un puerto serie de pyserial; con ``socket://host:puerto`` ese puerto es una conexión TCP, y el emulador es
el servidor al otro lado. Uso con el gateway (dos terminales, con un Mosquitto en 127.0.0.1:1883)::

    uv run spine-arduino-emulator --port 5000
    SPINE_GATEWAY_SERIAL_URL=socket://127.0.0.1:5000 uv run spine-gateway

Opciones de fallo: ``--no-imu`` (como el firmware sin MPU-6050), ``--drop-every N`` (cada N-ésimo lote no sale pero gasta su
``frame_seq``, como un overrun) y ``--reboot-after S`` (reinicio del dispositivo a los S segundos de encendido). En Docker, el
perfil ``gateway-demo`` de ``app/docker-compose.yml`` levanta emulador y gateway juntos.

Dos capas:

- ``EmulatedArduino``: núcleo puro, sin sockets ni reloj de pared. Dado el tiempo de dispositivo devuelve las líneas debidas,
  siempre con ``protocol.encode_*`` (el formato lo define ``protocol``, no este módulo). Como el firmware real, repite el hello
  junto a cada tick: pyserial ``socket://`` vacía el buffer de entrada justo al abrir el puerto (igual que un Uno, que se
  reinicia al abrirlo), así que nunca se puede suponer que el primer hello llegue; un hello repetido con el mismo ``boot_id`` es
  inocuo para el gateway.
- ``serve``: servidor asyncio que atiende un cliente cada vez y le da un dispositivo nuevo (como un Arduino reseteado por DTR:
  ``boot_id`` nuevo, secuencias y ``millis`` a cero). Marca el ritmo con un ``sleep`` inyectable.

Las señales son sintéticas y deterministas (``random.Random(seed)``): EMG de reposo con una contracción suave cada 4 s, IMU con
1 g en Z y un balanceo de gyro_x de 4 s (lo que cuenta el backend para las repeticiones) y un cordón elástico que oscila despacio.
"""

import argparse
import asyncio
import contextlib
import logging
import math
import random
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any

from spine_gateway.assembler import SAMPLES_PER_BATCH
from spine_gateway.protocol import (
    PROTOCOL_VERSION,
    SEQ_MODULUS,
    U16_MAX,
    U32_MAX,
    Frame,
    FrameKind,
    Hello,
    ImuSample,
    Tick,
    encode_frame,
    encode_hello,
    encode_tick,
)

_log = logging.getLogger(__name__)

FIRMWARE_VERSION = "0.1.0-emu"
STEP_MS = 100
"""Periodo de los lotes ``D`` (los tres kinds salen a la vez, como el firmware)."""
TICK_MS = 1000
"""Periodo del tick (y del hello repetido)."""
SAMPLE_PERIOD_MS = {FrameKind.EMG: 1, FrameKind.IMU: 10, FrameKind.CORD: 50}
"""1000 Hz, 100 Hz y 20 Hz."""

ADC_MAX = 1023
EMG_MIN, EMG_MAX = 0, ADC_MAX
CORD_MIN, CORD_MAX = 1, ADC_MAX - 1
"""Rangos de las cuentas del ADC: el cordón nunca llega a los carriles (un corto o un circuito abierto no es una señal)."""
INT16_MAX = 0x7FFF
INT16_MIN = -0x8000

EMG_REST = 20.0
EMG_NOISE = 5.0
EMG_PEAK_RANGE = (500.0, 700.0)
EMG_PERIOD_MS = 4000
EMG_ACTIVE_START_MS = 2000
EMG_ACTIVE_MS = 1000
"""Cada 4 s, de 2 s a 3 s, una campana de coseno elevado desde el reposo hasta el pico y de vuelta."""

ACCEL_1G = 8192
"""1 g en cuentas con el MPU-6050 a ±4 g."""
ACCEL_NOISE = 40.0
GYRO_NOISE = 30.0
GYRO_AMPLITUDE_RANGE = (3000.0, 6000.0)
GYRO_PERIOD_MS = 4000

CORD_CENTRE = 400.0
CORD_SWING = 90.0
CORD_NOISE = 10.0
CORD_PERIOD_MS = 8000

BOOT_ID_MULTIPLIER = 1664525
BOOT_ID_INCREMENT = 1013904223
"""Congruencial lineal de Numerical Recipes módulo 2^32: como el incremento es impar y ``a - 1`` par, nunca tiene punto fijo."""

IMU_ABSENT = "#imu absent"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Textos de la CLI como constantes de módulo: mutmut no muta el módulo y un cambio de redacción no es un cambio de comportamiento.
PROG = "spine-arduino-emulator"
DESCRIPTION = "Arduino falso del wearable por TCP (para SPINE_GATEWAY_SERIAL_URL=socket://host:puerto)"
HELP_HOST = "interfaz de escucha (0.0.0.0 en Docker)"
HELP_PORT = "puerto TCP (por defecto %(default)s)"
HELP_SEED = "semilla de las señales y del boot_id"
HELP_NO_IMU = "como el firmware sin MPU-6050: sin tramas I y con '#imu absent'"
HELP_DROP_EVERY = "cada N-ésimo lote no se emite pero gasta su frame_seq (0: ninguno)"
HELP_REBOOT_AFTER = "reinicia el dispositivo a los S segundos de encendido"


def initial_boot_id(seed: int) -> int:
    """``boot_id`` del primer arranque, derivado de la semilla (otro generador: no altera las señales)."""
    return random.Random(f"boot-id:{seed}").getrandbits(32)


def next_boot_id(boot_id: int) -> int:
    """``boot_id`` del arranque siguiente; siempre distinto del actual."""
    return (boot_id * BOOT_ID_MULTIPLIER + BOOT_ID_INCREMENT) & U32_MAX


def clamp(value: float, low: int, high: int) -> int:
    """Entero más cercano a ``value`` dentro de ``[low, high]``."""
    return max(low, min(high, round(value)))


def _emg_envelope(t_ms: int) -> float:
    """0..1: la forma de la contracción en el instante ``t_ms`` del arranque."""
    phase = t_ms % EMG_PERIOD_MS - EMG_ACTIVE_START_MS
    if 0 <= phase < EMG_ACTIVE_MS:
        return 0.5 * (1 - math.cos(2 * math.pi * phase / EMG_ACTIVE_MS))
    return 0.0


def _unit_wave(t_ms: int, period_ms: int) -> float:
    return math.sin(2 * math.pi * t_ms / period_ms)


class EmulatedArduino:
    """Dispositivo falso: ``lines_until(device_ms)`` da las líneas (sin ``\\n``) que ha emitido hasta ese instante.

    ``device_ms`` son los milisegundos desde el encendido y debe ir creciendo; el dispositivo avanza en pasos de 100 ms y lo que
    no ha cumplido un paso entero todavía no se emite. La primera llamada emite el hello (y ``#imu absent`` si no hay IMU).
    ``reboot_after_s`` reinicia el dispositivo una vez, a los S segundos desde el arranque: ``boot_id`` nuevo (``next_boot_id``),
    secuencias y ``millis`` a cero, y el lote de ese paso se pierde. ``start_seq`` es el primer ``frame_seq`` de cada kind
    (sirve para probar la vuelta de u16 sin 6.500 s de señal).
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        boot_id: int | None = None,
        imu_present: bool = True,
        drop_every: int = 0,
        reboot_after_s: float | None = None,
        start_seq: int = 0,
    ) -> None:
        if drop_every < 0:
            raise ValueError(f"drop_every must be >= 0: {drop_every}")
        if reboot_after_s is not None and reboot_after_s <= 0:
            raise ValueError(f"reboot_after_s must be > 0: {reboot_after_s}")
        if boot_id is not None and not 0 <= boot_id <= U32_MAX:
            raise ValueError(f"boot_id must fit u32: {boot_id}")
        if not 0 <= start_seq <= U16_MAX:
            raise ValueError(f"start_seq must fit u16: {start_seq}")
        self._rng = random.Random(seed)
        self._emg_peak = self._rng.uniform(*EMG_PEAK_RANGE)
        self._gyro_amplitude = self._rng.uniform(*GYRO_AMPLITUDE_RANGE)
        self._boot_id = initial_boot_id(seed) if boot_id is None else boot_id
        self._imu_present = imu_present
        self._drop_every = drop_every
        self._reboot_after_ms = None if reboot_after_s is None else reboot_after_s * 1000
        self._start_seq = start_seq
        self._power_ms = 0  # tiempo procesado desde el encendido (múltiplo de STEP_MS)
        self._boot_ms = 0  # el ``millis()`` del arranque en curso
        self._rebooted = False
        self._hello_due = True
        self._seq = dict.fromkeys(FrameKind, start_seq)
        self._batches = dict.fromkeys(FrameKind, 0)

    @property
    def boot_id(self) -> int:
        return self._boot_id

    @property
    def millis(self) -> int:
        """``millis()`` del dispositivo en el último paso procesado."""
        return self._boot_ms

    def lines_until(self, device_ms: int) -> list[str]:
        """Líneas debidas hasta ``device_ms`` (desde el encendido), en el orden en que las emitiría el firmware."""
        out: list[str] = []
        if self._hello_due:
            out += self._boot_lines()
        while self._power_ms + STEP_MS <= device_ms:
            out += self._step()
        return out

    def _boot_lines(self) -> list[str]:
        self._hello_due = False
        lines = [self._hello()]
        if not self._imu_present:
            lines.append(IMU_ABSENT)
        return lines

    def _hello(self) -> str:
        return encode_hello(Hello(PROTOCOL_VERSION, self._boot_id, FIRMWARE_VERSION, self._boot_ms))

    def _step(self) -> list[str]:
        self._power_ms += STEP_MS
        self._boot_ms += STEP_MS
        if self._reboot_due():
            self._reboot()
            return self._boot_lines()
        lines: list[str] = []
        if self._boot_ms % TICK_MS == 0:
            lines += [self._hello(), encode_tick(Tick(self._boot_id, self._boot_ms))]
        t0 = self._boot_ms - STEP_MS
        lines += self._emit(FrameKind.EMG, t0, self._emg_samples(t0))
        if self._imu_present:
            lines += self._emit(FrameKind.IMU, t0, self._imu_samples(t0))
        lines += self._emit(FrameKind.CORD, t0, self._cord_samples(t0))
        return lines

    def _reboot_due(self) -> bool:
        return self._reboot_after_ms is not None and not self._rebooted and self._boot_ms >= self._reboot_after_ms

    def _reboot(self) -> None:
        self._rebooted = True
        self._boot_id = next_boot_id(self._boot_id)
        self._boot_ms = 0
        self._seq = dict.fromkeys(FrameKind, self._start_seq)
        self._batches = dict.fromkeys(FrameKind, 0)
        self._hello_due = True

    def _emit(self, kind: FrameKind, t0: int, samples: tuple[int, ...] | tuple[ImuSample, ...]) -> list[str]:
        """El lote como línea, o nada si le toca el overrun: en ambos casos gasta su ``frame_seq``."""
        seq = self._seq[kind]
        self._seq[kind] = (seq + 1) % SEQ_MODULUS
        self._batches[kind] += 1
        if self._drop_every and self._batches[kind] % self._drop_every == 0:
            return []
        return [encode_frame(Frame(kind, seq, t0, samples))]

    def _noise(self, amplitude: float) -> float:
        return self._rng.uniform(-amplitude, amplitude)

    def _emg_samples(self, t0: int) -> tuple[int, ...]:
        step = SAMPLE_PERIOD_MS[FrameKind.EMG]
        return tuple(
            clamp(EMG_REST + self._emg_peak * _emg_envelope(t0 + i * step) + self._noise(EMG_NOISE), EMG_MIN, EMG_MAX)
            for i in range(SAMPLES_PER_BATCH[FrameKind.EMG])
        )

    def _imu_samples(self, t0: int) -> tuple[ImuSample, ...]:
        step = SAMPLE_PERIOD_MS[FrameKind.IMU]
        return tuple(self._imu_sample(t0 + i * step) for i in range(SAMPLES_PER_BATCH[FrameKind.IMU]))

    def _imu_sample(self, t_ms: int) -> ImuSample:
        ax = clamp(self._noise(ACCEL_NOISE), INT16_MIN, INT16_MAX)
        ay = clamp(self._noise(ACCEL_NOISE), INT16_MIN, INT16_MAX)
        az = clamp(ACCEL_1G + self._noise(ACCEL_NOISE), INT16_MIN, INT16_MAX)
        gx = clamp(self._gyro_amplitude * _unit_wave(t_ms, GYRO_PERIOD_MS) + self._noise(GYRO_NOISE), INT16_MIN, INT16_MAX)
        gy = clamp(self._noise(GYRO_NOISE), INT16_MIN, INT16_MAX)
        gz = clamp(self._noise(GYRO_NOISE), INT16_MIN, INT16_MAX)
        return (ax, ay, az, gx, gy, gz)

    def _cord_samples(self, t0: int) -> tuple[int, ...]:
        step = SAMPLE_PERIOD_MS[FrameKind.CORD]
        return tuple(
            clamp(CORD_CENTRE + CORD_SWING * _unit_wave(t0 + i * step, CORD_PERIOD_MS) + self._noise(CORD_NOISE), CORD_MIN, CORD_MAX)
            for i in range(SAMPLES_PER_BATCH[FrameKind.CORD])
        )


# --- servidor TCP ---

type Sleep = Callable[[float], Awaitable[object]]


async def _serve_client(writer: asyncio.StreamWriter, device: EmulatedArduino, sleep: Sleep, clock: Callable[[], float]) -> None:
    """Emite el dispositivo a ritmo de 100 ms hasta que el cliente se va. El tiempo de dispositivo es ``paso * 100 ms``; el reloj
    solo ajusta la espera (si un ``sleep`` tarda de más, los pasos siguientes salen sin esperar y no se acumula retraso)."""
    start = clock()
    step = 0
    try:
        while True:
            writer.write("".join(f"{line}\n" for line in device.lines_until(step * STEP_MS)).encode("ascii"))
            await writer.drain()
            step += 1
            await sleep(max(0.0, start + step * STEP_MS / 1000 - clock()))
    except OSError:
        _log.info("client gone")  # el cliente cerró (el gateway reintenta): el siguiente verá un dispositivo reiniciado
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def serve(
    host: str,
    port: int,
    *,
    seed: int = 0,
    imu_present: bool = True,
    drop_every: int = 0,
    reboot_after_s: float | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_listening: Callable[[int], None] | None = None,
) -> None:
    """Escucha en ``host:port`` y atiende un cliente cada vez hasta que se cancela.

    Cada cliente recibe un dispositivo nuevo; el ``boot_id`` de cada uno sigue al último del anterior (``next_boot_id``).
    ``on_listening`` recibe el puerto real (útil con ``port=0``). Al cancelar se cierra al cliente en curso y el puerto.
    """

    def new_device(boot_id: int | None) -> EmulatedArduino:
        return EmulatedArduino(seed, boot_id=boot_id, imu_present=imu_present, drop_every=drop_every, reboot_after_s=reboot_after_s)

    boot_id = new_device(None).boot_id  # un dispositivo de prueba: las opciones inválidas fallan ya, no con el primer cliente
    free = asyncio.Lock()  # un cliente cada vez: los demás esperan en el backlog a que el puerto quede libre
    handlers: set[asyncio.Task[None]] = set()

    async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal boot_id
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            async with free:
                device = new_device(boot_id)
                try:
                    await _serve_client(writer, device, sleep, clock)
                finally:
                    boot_id = next_boot_id(device.boot_id)
        finally:
            handlers.discard(task)

    server = await asyncio.start_server(handle, host, port)
    try:
        if on_listening is not None:
            on_listening(server.sockets[0].getsockname()[1])
        _log.info("Arduino emulator listening on %s:%d", host, server.sockets[0].getsockname()[1])
        await server.serve_forever()
    finally:
        server.close()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await server.wait_closed()


# --- CLI ---


def _bounded(minimum: float, kind: type[int] | type[float], maximum: float = math.inf) -> Callable[[str], Any]:
    """Tipo de argparse: número ``kind`` dentro de ``[minimum, maximum]``."""

    def parse(text: str) -> Any:
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}: {text}")
        return value

    return parse


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=PROG, description=DESCRIPTION)
    parser.add_argument("--host", default=DEFAULT_HOST, help=HELP_HOST)
    parser.add_argument("--port", type=_bounded(1, int, 65535), default=DEFAULT_PORT, help=HELP_PORT)
    parser.add_argument("--seed", type=int, default=0, help=HELP_SEED)
    parser.add_argument("--no-imu", action="store_true", help=HELP_NO_IMU)
    parser.add_argument("--drop-every", type=_bounded(0, int), default=0, help=HELP_DROP_EVERY)
    parser.add_argument("--reboot-after", type=_bounded(1e-9, float), default=None, help=HELP_REBOOT_AFTER)
    return parser.parse_args(argv)


async def run_until_stopped(run: Callable[..., Coroutine[Any, Any, None]], **options: Any) -> None:
    """``run`` hasta que acaba o se cancela; SIGTERM (``docker stop``) cuenta como una parada pedida, no como un error."""
    task = asyncio.create_task(run(**options))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):  # Windows no tiene SIGTERM en el bucle
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        _log.info("emulator stopped (SIGTERM)")
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.remove_signal_handler(signal.SIGTERM)


def main(argv: Sequence[str] | None = None, *, run: Callable[..., Coroutine[Any, Any, None]] = serve) -> None:
    """``uv run spine-arduino-emulator``: ver las opciones con ``--help``."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = parse_args(argv)
    options: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "seed": args.seed,
        "imu_present": not args.no_imu,
        "drop_every": args.drop_every,
        "reboot_after_s": args.reboot_after,
    }
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(run_until_stopped(run, **options), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass  # Ctrl+C: ``serve`` ya cerró al cliente y el puerto al cancelarse


if __name__ == "__main__":
    main()
