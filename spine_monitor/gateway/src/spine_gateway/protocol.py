"""Protocolo de línea entre el Arduino del wearable y el gateway (USB serie).

Lógica pura: sin I/O, sin reloj y sin asyncio. El firmware implementa el mismo formato en C++; los vectores dorados de
``tests/data/protocol_vectors.txt`` son la referencia común de ambos lados.

Formato: líneas ASCII terminadas en ``\\n`` (se tolera ``\\r\\n``) de la forma ``<cuerpo>*<XX>``, donde ``XX`` son dos
dígitos hex (el encoder los emite en mayúscula; el parser acepta también minúscula) con el CRC-8/MAXIM de los bytes del
cuerpo, es decir, todo lo que hay antes del último ``*``. Cuerpos:

- ``H,1,<boot_id>,<fw_version>,<millis>``: hello tras el arranque. ``1`` es la versión del protocolo.
- ``T,<boot_id>,<millis>``: tick de 1 s, ancla del reloj del dispositivo.
- ``D,<kind>,<frame_seq>,<t0_ms>,<n>,<b64>``: lote de ``n`` muestras de un sensor. ``kind`` es ``E`` (EMG), ``I`` (IMU)
  o ``S`` (cordón elástico). ``frame_seq`` es un contador u16 por kind que da la vuelta en 65536. ``t0_ms`` son los
  ``millis()`` de la primera muestra: u32, da la vuelta cada ~49,7 días de funcionamiento continuo, así que quien
  convierta a tiempo de pared debe anclarlo con los ticks y no compararlo en absoluto entre vueltas.
  ``b64`` es base64 estándar con padding de un payload binario little-endian: E y S son ``n`` uint16 (cuentas de ADC;
  aquí no se valida el rango de 10 bits, solo el ancho de 16), I son ``n`` × 6 int16 en orden ax, ay, az, gx, gy, gz.

``parse_line`` nunca lanza: cualquier línea que no sea un mensaje válido devuelve un ``BadLine`` con su razón.
"""

import base64
import binascii
import struct
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 1
MAX_LINE_LENGTH = 1024
"""Longitud máxima de una línea sin su terminador; protege del ruido de un puerto serie sin sincronizar."""

U16_MAX = 0xFFFF
U32_MAX = 0xFFFFFFFF
I16_MIN = -0x8000
I16_MAX = 0x7FFF
SEQ_MODULUS = 0x10000
SEQ_HALF = SEQ_MODULUS // 2
IMU_AXES = 6

_DIGITS = frozenset("0123456789")
# Caracteres permitidos en la versión de firmware: ASCII imprimible sin espacio, coma ni asterisco.
_FIRMWARE_CHARS = frozenset(chr(c) for c in range(0x21, 0x7F)) - {",", "*"}
_HEX = frozenset("0123456789abcdefABCDEF")
# Nombres de codec como constantes de módulo: mutmut no muta el módulo, y "ascii" -> "ASCII" sería un mutante equivalente.
_ASCII = "ascii"
_LATIN1 = "latin-1"

type ImuSample = tuple[int, int, int, int, int, int]
"""Una muestra cruda del MPU-6050: ax, ay, az, gx, gy, gz."""


class FrameKind(StrEnum):
    """Sensor de un lote ``D``; el valor es la letra del protocolo."""

    EMG = "E"
    IMU = "I"
    CORD = "S"


class BadLineReason(StrEnum):
    """Por qué una línea no es un mensaje válido."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    NOT_ASCII = "not_ascii"
    NO_CRC = "no_crc"
    BAD_CRC = "bad_crc"
    UNKNOWN_TYPE = "unknown_type"
    BAD_FIELD = "bad_field"
    UNSUPPORTED_VERSION = "unsupported_version"
    BAD_BASE64 = "bad_base64"
    BAD_LENGTH = "bad_length"


@dataclass(frozen=True, slots=True)
class Hello:
    """Saludo del firmware tras arrancar; ``boot_id`` identifica el arranque."""

    version: int
    boot_id: int
    firmware_version: str
    millis: int


@dataclass(frozen=True, slots=True)
class Tick:
    """Tick de 1 s con los ``millis()`` del dispositivo."""

    boot_id: int
    millis: int


@dataclass(frozen=True, slots=True)
class Frame:
    """Lote de muestras de un sensor. EMG y cordón: tupla de enteros; IMU: tupla de ``ImuSample``."""

    kind: FrameKind
    frame_seq: int
    t0_ms: int
    samples: tuple[int, ...] | tuple[ImuSample, ...]


@dataclass(frozen=True, slots=True)
class BadLine:
    """Línea rechazada; ``raw`` es la línea recibida (los bytes no ASCII van escapados con ``\\xNN``)."""

    reason: BadLineReason
    raw: str


type Message = Hello | Tick | Frame


class _Rejected(Exception):
    """Señal interna de rechazo; ``parse_line`` la convierte en ``BadLine`` y nunca sale del módulo."""

    def __init__(self, reason: BadLineReason) -> None:
        super().__init__(reason)
        self.reason = reason


# --- CRC ---


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM (Dallas/1-Wire): polinomio reflejado 0x8C, init 0x00, sin xorout. ``crc8(b"123456789") == 0xA1``."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


# --- parser ---


def parse_line(line: str | bytes) -> Message | BadLine:
    """Interpreta una línea del Arduino; nunca lanza.

    Se quita un único terminador (``\\n``, ``\\r\\n`` o ninguno). El límite ``MAX_LINE_LENGTH`` se aplica después
    y antes que cualquier otra comprobación, para no trabajar sobre basura larga.
    """
    raw = line.decode(_ASCII, "backslashreplace") if isinstance(line, bytes) else line
    # latin-1 asigna un carácter a cada byte: conserva la longitud y deja los bytes no ASCII para ``isascii``.
    text = line.decode(_LATIN1) if isinstance(line, bytes) else line
    try:
        return _parse(_strip_terminator(text))
    except _Rejected as rejected:
        return BadLine(reason=rejected.reason, raw=raw)


def _strip_terminator(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    return line


def _parse(line: str) -> Message:
    if not line:
        raise _Rejected(BadLineReason.EMPTY)
    if len(line) > MAX_LINE_LENGTH:
        raise _Rejected(BadLineReason.TOO_LONG)
    if not line.isascii():
        raise _Rejected(BadLineReason.NOT_ASCII)
    body, star, crc_text = line.rpartition("*")
    if not star or len(crc_text) != 2 or not _HEX.issuperset(crc_text):
        raise _Rejected(BadLineReason.NO_CRC)
    if int(crc_text, 16) != crc8(body.encode(_ASCII)):
        raise _Rejected(BadLineReason.BAD_CRC)
    kind, *fields = body.split(",")
    match kind:
        case "H":
            return _parse_hello(fields)
        case "T":
            return _parse_tick(fields)
        case "D":
            return _parse_frame(fields)
        case _:
            raise _Rejected(BadLineReason.UNKNOWN_TYPE)


def _expect(fields: list[str], count: int) -> None:
    if len(fields) != count:
        raise _Rejected(BadLineReason.BAD_FIELD)


def _uint(text: str, maximum: int) -> int:
    """Entero decimal sin signo de solo dígitos ASCII (sin ``+``, ``-``, ``_`` ni espacios) y como mucho ``maximum``."""
    if not text or not _DIGITS.issuperset(text):
        raise _Rejected(BadLineReason.BAD_FIELD)
    value = int(text)
    if value > maximum:
        raise _Rejected(BadLineReason.BAD_FIELD)
    return value


def _parse_hello(fields: list[str]) -> Hello:
    _expect(fields, 4)
    version_text, boot_text, firmware, millis_text = fields
    version = _uint(version_text, U32_MAX)
    if version != PROTOCOL_VERSION:
        raise _Rejected(BadLineReason.UNSUPPORTED_VERSION)
    if not _valid_firmware(firmware):
        raise _Rejected(BadLineReason.BAD_FIELD)
    return Hello(version=version, boot_id=_uint(boot_text, U32_MAX), firmware_version=firmware, millis=_uint(millis_text, U32_MAX))


def _parse_tick(fields: list[str]) -> Tick:
    _expect(fields, 2)
    boot_text, millis_text = fields
    return Tick(boot_id=_uint(boot_text, U32_MAX), millis=_uint(millis_text, U32_MAX))


def _parse_frame(fields: list[str]) -> Frame:
    _expect(fields, 5)
    kind_text, seq_text, t0_text, n_text, payload_text = fields
    try:
        kind = FrameKind(kind_text)
    except ValueError:
        raise _Rejected(BadLineReason.BAD_FIELD) from None
    frame_seq = _uint(seq_text, U16_MAX)
    t0_ms = _uint(t0_text, U32_MAX)
    n = _uint(n_text, U32_MAX)
    if n == 0:
        raise _Rejected(BadLineReason.BAD_FIELD)
    try:
        payload = base64.b64decode(payload_text, validate=True)
    except binascii.Error:
        raise _Rejected(BadLineReason.BAD_BASE64) from None
    width = IMU_AXES if kind is FrameKind.IMU else 1
    if len(payload) != 2 * width * n:
        raise _Rejected(BadLineReason.BAD_LENGTH)
    if kind is FrameKind.IMU:
        values = struct.unpack(f"<{n * IMU_AXES}h", payload)
        samples: tuple[int, ...] | tuple[ImuSample, ...] = tuple(_imu_sample(values, i) for i in range(n))
    else:
        samples = struct.unpack(f"<{n}H", payload)
    return Frame(kind=kind, frame_seq=frame_seq, t0_ms=t0_ms, samples=samples)


def _imu_sample(values: tuple[int, ...], index: int) -> ImuSample:
    ax, ay, az, gx, gy, gz = values[index * IMU_AXES : (index + 1) * IMU_AXES]
    return (ax, ay, az, gx, gy, gz)


def _valid_firmware(text: str) -> bool:
    return bool(text) and _FIRMWARE_CHARS.issuperset(text)


# --- encoder (espejo del firmware; lo usan el emulador y los tests) ---


def encode_hello(hello: Hello) -> str:
    """Línea ``H`` sin terminador. Lanza ``ValueError`` si algún campo no cabe en el protocolo."""
    _check_range("version", hello.version, 0, U32_MAX)
    _check_range("boot_id", hello.boot_id, 0, U32_MAX)
    _check_range("millis", hello.millis, 0, U32_MAX)
    if not _valid_firmware(hello.firmware_version):
        raise ValueError(f"firmware_version must be non-empty printable ASCII without space, ',' or '*': {hello.firmware_version!r}")
    return _with_crc(f"H,{hello.version},{hello.boot_id},{hello.firmware_version},{hello.millis}")


def encode_tick(tick: Tick) -> str:
    """Línea ``T`` sin terminador. Lanza ``ValueError`` si algún campo no cabe en u32."""
    _check_range("boot_id", tick.boot_id, 0, U32_MAX)
    _check_range("millis", tick.millis, 0, U32_MAX)
    return _with_crc(f"T,{tick.boot_id},{tick.millis}")


def encode_frame(frame: Frame) -> str:
    """Línea ``D`` sin terminador. Lanza ``ValueError`` si un campo o una muestra no cabe, o si no casan con el kind."""
    _check_range("frame_seq", frame.frame_seq, 0, U16_MAX)
    _check_range("t0_ms", frame.t0_ms, 0, U32_MAX)
    if not frame.samples:
        raise ValueError("a frame needs at least one sample")
    if frame.kind is FrameKind.IMU:
        flat: list[int] = []
        for sample in frame.samples:
            if not isinstance(sample, tuple) or len(sample) != IMU_AXES:
                raise ValueError(f"IMU samples must be 6-tuples: {sample!r}")
            for value in sample:
                _check_range("IMU sample", value, I16_MIN, I16_MAX)
            flat.extend(sample)
        payload = struct.pack(f"<{len(flat)}h", *flat)
    else:
        scalars: list[int] = []
        for sample in frame.samples:
            if not isinstance(sample, int):
                raise ValueError(f"{frame.kind.name} samples must be integers: {sample!r}")
            _check_range(f"{frame.kind.name} sample", sample, 0, U16_MAX)
            scalars.append(sample)
        payload = struct.pack(f"<{len(scalars)}H", *scalars)
    encoded = base64.b64encode(payload).decode(_ASCII)
    return _with_crc(f"D,{frame.kind.value},{frame.frame_seq},{frame.t0_ms},{len(frame.samples)},{encoded}")


def _check_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} out of range [{minimum}, {maximum}]: {value}")


def _with_crc(body: str) -> str:
    return f"{body}*{crc8(body.encode(_ASCII)):02X}"


# --- secuencia de lotes ---


def frame_gap(prev_seq: int | None, seq: int) -> int:
    """Lotes perdidos entre el ``frame_seq`` anterior y el actual de un mismo kind, con vuelta en 65536.

    Devuelve 0 si ``seq`` es el siguiente o si no hay anterior (``None``), y ``k`` si faltan ``k`` lotes. La distancia
    modular se interpreta con signo en (-32768, 32768]: un salto hacia delante de más de 32768 no se distingue de un
    retroceso y se trata como tal. Un resultado negativo significa que la secuencia no avanzó: -1 es un duplicado
    (``seq == prev_seq``) y ``-(k + 1)`` un retroceso de ``k``; el llamador decide (descartar, o resincronizar si hubo
    reinicio, que se detecta por ``boot_id`` y no aquí). Rango del resultado: [-32768, 32767].
    """
    if prev_seq is None:
        return 0
    delta = (seq - prev_seq) % SEQ_MODULUS
    if delta > SEQ_HALF:
        delta -= SEQ_MODULUS
    return delta - 1
