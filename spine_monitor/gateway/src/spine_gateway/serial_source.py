"""Fuente real de líneas del Arduino: pyserial en un hilo, troceo por ``\\n`` y reconexión con backoff.

Decisión de concurrencia: cada lectura bloqueante (``to_thread``) es una llamada corta con timeout de 0,1 s, de modo que
no hay hilo propio ni cola entre hilo y loop; el buffer lo toca solo la corrutina ``events()`` (un único escritor) y el
hilo solo devuelve ``(bytes, instante)``. El instante se toma en el hilo, justo al volver la lectura, para que no dependa de
cuándo el consumidor atienda el evento. Si se cancela al consumidor durante una lectura, se espera (como mucho al timeout)
a que el hilo termine antes de cerrar el puerto, para no cerrarlo bajo sus pies ni dejar hilos colgados.

La línea se entrega sin procesar (sin el terminador, tolerando ``\\r\\n``): el parseo y el CRC los hace ``protocol``.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Protocol

import serial

from spine_gateway.ports import LinkDown, LinkUp, SerialEvent, SerialLine

# Bytes pendientes sin ``\n`` a partir de los cuales se considera ruido sin sincronizar y se descartan.
MAX_PENDING_BYTES = 4096
# Timeout (s) de cada lectura: acota lo que tarda el hilo en salir tras cancelar y cuánto se espera para cerrar.
READ_TIMEOUT_S = 0.1


class SerialPort(Protocol):
    """Lo mínimo que se necesita de un puerto serie (``serial.Serial`` lo cumple)."""

    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int = 1, /) -> bytes: ...

    def close(self) -> None: ...


type PortOpener = Callable[[str, int], SerialPort]


def open_serial_port(url: str, baud: int) -> SerialPort:
    """Abre ``COM3``, ``/dev/ttyACM0``, ``socket://host:puerto`` o ``loop://`` con timeout de lectura corto."""
    return serial.serial_for_url(url, baudrate=baud, timeout=READ_TIMEOUT_S)


def _describe(error: OSError) -> str:
    return f"{type(error).__name__}: {error}"


class SerialLineSource:
    """Implementa ``ports.LineSource`` sobre un puerto serie (ver ``events()``)."""

    def __init__(
        self,
        url: str,
        baud: int,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        opener: PortOpener = open_serial_port,
        *,
        reconnect_initial_s: float,
        reconnect_factor: float,
        reconnect_max_s: float,
    ) -> None:
        self._url = url
        self._baud = baud
        self._clock = clock
        self._sleep = sleep
        self._opener = opener
        self._initial = reconnect_initial_s
        self._factor = reconnect_factor
        self._max = reconnect_max_s

    async def events(self) -> AsyncIterator[SerialEvent]:
        """Emite ``LinkUp`` al abrir, ``SerialLine`` por línea y ``LinkDown`` una vez por corte; no termina solo."""
        delay = self._initial
        down_reported = False
        while True:
            port: SerialPort | None = None
            reason: str
            try:
                port = await asyncio.to_thread(self._opener, self._url, self._baud)
                delay = self._initial
                down_reported = False
                yield LinkUp(self._clock())
                pending = bytearray()
                while True:
                    data, at = await self._read_chunk(port)
                    pending += data
                    *complete, pending = pending.split(b"\n")
                    for raw in complete:
                        line = raw.removesuffix(b"\r")
                        if line:
                            yield SerialLine(bytes(line), at)
                    if len(pending) > MAX_PENDING_BYTES:
                        pending.clear()
            except OSError as error:  # SerialException es subclase de OSError
                reason = _describe(error)
            finally:
                if port is not None:
                    with contextlib.suppress(OSError):
                        port.close()
            if not down_reported:
                down_reported = True
                yield LinkDown(self._clock(), reason)
            await self._sleep(delay)
            delay = min(delay * self._factor, self._max)

    async def _read_chunk(self, port: SerialPort) -> tuple[bytes, datetime]:
        read = asyncio.ensure_future(asyncio.to_thread(self._read, port))
        try:
            return await asyncio.shield(read)
        except asyncio.CancelledError:
            # El hilo no se puede interrumpir: se espera a que termine su lectura corta antes de que `events` cierre el puerto.
            await asyncio.wait({read})
            read.exception()  # el shield impide que `read` se cancele; se marca la excepción como recuperada (sin aviso al GC)
            raise

    def _read(self, port: SerialPort) -> tuple[bytes, datetime]:
        data = port.read(max(1, port.in_waiting))
        return data, self._clock()
