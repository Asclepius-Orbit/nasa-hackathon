"""Line protocol between the wearable Arduino and the gateway (USB serial).

Pure logic: no I/O, no clock and no asyncio. The firmware implements the same format in C++; the golden vectors in
``tests/data/protocol_vectors.txt`` are the common reference for both sides.

Format: ASCII lines terminated by ``\\n`` (``\\r\\n`` is tolerated) of the form ``<body>*<XX>``, where ``XX`` are two
hex digits (the encoder emits them in uppercase; the parser also accepts lowercase) with the CRC-8/MAXIM of the bytes of the
body, that is, everything before the last ``*``. Bodies:

- ``H,1,<boot_id>,<fw_version>,<millis>``: hello after boot. ``1`` is the protocol version.
- ``T,<boot_id>,<millis>``: 1 s tick, anchor of the device clock.
- ``D,<kind>,<frame_seq>,<t0_ms>,<n>,<b64>``: batch of ``n`` samples from one sensor. ``kind`` is ``E`` (EMG), ``I`` (IMU)
  or ``S`` (elastic cord). ``frame_seq`` is a u16 counter per kind that wraps at 65536. ``t0_ms`` is the
  ``millis()`` of the first sample: u32, wraps every ~49.7 days of continuous operation, so whoever
  converts to wall time must anchor it with the ticks and not compare it at all across wraps.
  ``b64`` is standard base64 with padding of a little-endian binary payload: E and S are ``n`` uint16 (ADC counts;
  the 10-bit range is not validated here, only the 16-bit width), I are ``n`` × 6 int16 in the order ax, ay, az, gx, gy, gz.

``parse_line`` never raises: any line that is not a valid message returns a ``BadLine`` with its reason.
"""

import base64
import binascii
import struct
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 1
MAX_LINE_LENGTH = 1024
"""Maximum length of a line without its terminator; protects against noise from an unsynchronised serial port."""

U16_MAX = 0xFFFF
U32_MAX = 0xFFFFFFFF
I16_MIN = -0x8000
I16_MAX = 0x7FFF
SEQ_MODULUS = 0x10000
SEQ_HALF = SEQ_MODULUS // 2
IMU_AXES = 6

_DIGITS = frozenset("0123456789")
# Characters allowed in the firmware version: printable ASCII without space, comma or asterisk.
_FIRMWARE_CHARS = frozenset(chr(c) for c in range(0x21, 0x7F)) - {",", "*"}
_HEX = frozenset("0123456789abcdefABCDEF")
# Codec names as module constants: mutmut does not mutate the module, and "ascii" -> "ASCII" would be an equivalent mutant.
_ASCII = "ascii"
_LATIN1 = "latin-1"

type ImuSample = tuple[int, int, int, int, int, int]
"""A raw MPU-6050 sample: ax, ay, az, gx, gy, gz."""


class FrameKind(StrEnum):
    """Sensor of a ``D`` batch; the value is the protocol letter."""

    EMG = "E"
    IMU = "I"
    CORD = "S"


class BadLineReason(StrEnum):
    """Why a line is not a valid message."""

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
    """Firmware greeting after boot; ``boot_id`` identifies the boot."""

    version: int
    boot_id: int
    firmware_version: str
    millis: int


@dataclass(frozen=True, slots=True)
class Tick:
    """1 s tick with the device ``millis()``."""

    boot_id: int
    millis: int


@dataclass(frozen=True, slots=True)
class Frame:
    """Batch of samples from one sensor. EMG and cord: tuple of integers; IMU: tuple of ``ImuSample``."""

    kind: FrameKind
    frame_seq: int
    t0_ms: int
    samples: tuple[int, ...] | tuple[ImuSample, ...]


@dataclass(frozen=True, slots=True)
class BadLine:
    """Rejected line; ``raw`` is the received line (non-ASCII bytes are escaped with ``\\xNN``)."""

    reason: BadLineReason
    raw: str


type Message = Hello | Tick | Frame


class _Rejected(Exception):
    """Internal rejection signal; ``parse_line`` turns it into ``BadLine`` and it never leaves the module."""

    def __init__(self, reason: BadLineReason) -> None:
        super().__init__(reason)
        self.reason = reason


# --- CRC ---


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM (Dallas/1-Wire): reflected polynomial 0x8C, init 0x00, no xorout. ``crc8(b"123456789") == 0xA1``."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


# --- parser ---


def parse_line(line: str | bytes) -> Message | BadLine:
    """Interprets an Arduino line; never raises.

    A single terminator is removed (``\\n``, ``\\r\\n`` or none). The ``MAX_LINE_LENGTH`` limit is applied afterwards
    and before any other check, so as not to work on long garbage.
    """
    raw = line.decode(_ASCII, "backslashreplace") if isinstance(line, bytes) else line
    # latin-1 maps one character to each byte: it preserves the length and leaves non-ASCII bytes for ``isascii``.
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
    """Unsigned decimal integer of ASCII digits only (no ``+``, ``-``, ``_`` or spaces) and at most ``maximum``."""
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


# --- encoder (mirror of the firmware; used by the emulator and the tests) ---


def encode_hello(hello: Hello) -> str:
    """``H`` line without terminator. Raises ``ValueError`` if any field does not fit in the protocol."""
    _check_range("version", hello.version, 0, U32_MAX)
    _check_range("boot_id", hello.boot_id, 0, U32_MAX)
    _check_range("millis", hello.millis, 0, U32_MAX)
    if not _valid_firmware(hello.firmware_version):
        raise ValueError(f"firmware_version must be non-empty printable ASCII without space, ',' or '*': {hello.firmware_version!r}")
    return _with_crc(f"H,{hello.version},{hello.boot_id},{hello.firmware_version},{hello.millis}")


def encode_tick(tick: Tick) -> str:
    """``T`` line without terminator. Raises ``ValueError`` if any field does not fit in u32."""
    _check_range("boot_id", tick.boot_id, 0, U32_MAX)
    _check_range("millis", tick.millis, 0, U32_MAX)
    return _with_crc(f"T,{tick.boot_id},{tick.millis}")


def encode_frame(frame: Frame) -> str:
    """``D`` line without terminator. Raises ``ValueError`` if a field or a sample does not fit, or if they do not match the kind."""
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


# --- batch sequence ---


def frame_gap(prev_seq: int | None, seq: int) -> int:
    """Batches lost between the previous ``frame_seq`` and the current one of the same kind, wrapping at 65536.

    Returns 0 if ``seq`` is the next one or if there is no previous (``None``), and ``k`` if ``k`` batches are missing. The modular distance
    is interpreted as signed in (-32768, 32768]: a forward jump of more than 32768 is indistinguishable from a
    regression and is treated as such. A negative result means the sequence did not advance: -1 is a duplicate
    (``seq == prev_seq``) and ``-(k + 1)`` a regression of ``k``; the caller decides (discard, or resynchronise if there was a
    reboot, which is detected by ``boot_id`` and not here). Result range: [-32768, 32767].
    """
    if prev_seq is None:
        return 0
    delta = (seq - prev_seq) % SEQ_MODULUS
    if delta > SEQ_HALF:
        delta -= SEQ_MODULUS
    return delta - 1
