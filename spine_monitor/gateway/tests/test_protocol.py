"""Protocolo de línea Arduino -> gateway: CRC, vectores dorados, cada razón de BadLine, rangos y frame_gap."""

import base64
import struct
from dataclasses import replace
from pathlib import Path

import pytest
from spine_gateway.protocol import (
    MAX_LINE_LENGTH,
    PROTOCOL_VERSION,
    BadLine,
    BadLineReason,
    Frame,
    FrameKind,
    Hello,
    Tick,
    crc8,
    encode_frame,
    encode_hello,
    encode_tick,
    frame_gap,
    parse_line,
)

VECTORS = (Path(__file__).parent / "data" / "protocol_vectors.txt").read_text(encoding="ascii").splitlines()

GOLDEN: tuple[Hello | Tick | Frame, ...] = (
    Hello(version=1, boot_id=2893746120, firmware_version="0.1.0", millis=1234),
    Tick(boot_id=2893746120, millis=5000),
    Frame(kind=FrameKind.EMG, frame_seq=7, t0_ms=1000, samples=(0, 1, 512, 1023)),
    Frame(
        kind=FrameKind.IMU,
        frame_seq=3,
        t0_ms=2000,
        samples=((100, -100, 8192, 0, -1, 32767), (-32768, 5, -5, 65, -65, 1000)),
    ),
    Frame(kind=FrameKind.CORD, frame_seq=65535, t0_ms=4294967295, samples=(511, 512)),
)


def line(body: str) -> str:
    """Añade el sufijo *XX correcto a un cuerpo arbitrario (para construir líneas con CRC válido)."""
    return f"{body}*{crc8(body.encode('ascii')):02X}"


def reason(result: object) -> BadLineReason:
    assert isinstance(result, BadLine), result
    return result.reason


# --- CRC ---


def test_crc8_check_vector() -> None:
    assert crc8(b"123456789") == 0xA1


def test_crc8_of_empty_is_init_zero() -> None:
    assert crc8(b"") == 0x00


def test_crc8_single_bytes() -> None:
    # CRC-8/MAXIM de un byte: tabla conocida (0x01 -> 0x5E, 0x80 -> 0x8C, 0xFF -> 0x35).
    assert (crc8(b"\x01"), crc8(b"\x80"), crc8(b"\xff")) == (0x5E, 0x8C, 0x35)


# --- vectores dorados ---


def test_vectors_file_has_the_five_vectors() -> None:
    assert len(VECTORS) == len(GOLDEN) == 5


@pytest.mark.parametrize(("text", "expected"), list(zip(VECTORS, GOLDEN, strict=True)))
def test_golden_vector_parses(text: str, expected: Hello | Tick | Frame) -> None:
    assert parse_line(text) == expected
    assert parse_line(text.encode("ascii") + b"\n") == expected


@pytest.mark.parametrize(("text", "message"), list(zip(VECTORS, GOLDEN, strict=True)))
def test_golden_vector_encodes(text: str, message: Hello | Tick | Frame) -> None:
    match message:
        case Hello():
            assert encode_hello(message) == text
        case Tick():
            assert encode_tick(message) == text
        case Frame():
            assert encode_frame(message) == text


def test_protocol_version_is_one() -> None:
    assert PROTOCOL_VERSION == 1


def test_frame_kind_letters() -> None:
    assert (FrameKind.EMG.value, FrameKind.IMU.value, FrameKind.CORD.value) == ("E", "I", "S")


# --- terminadores, mayúsculas/minúsculas, tipos de entrada ---


@pytest.mark.parametrize("suffix", ["\n", "\r\n", ""])
def test_line_terminators_are_tolerated(suffix: str) -> None:
    assert parse_line(VECTORS[1] + suffix) == GOLDEN[1]
    assert parse_line((VECTORS[1] + suffix).encode("ascii")) == GOLDEN[1]


def test_lowercase_crc_hex_is_accepted() -> None:
    text = VECTORS[3]
    assert text.endswith("*E6")
    assert parse_line(text[:-2] + "e6") == GOLDEN[3]


def test_only_one_terminator_is_stripped() -> None:
    # Un \n de más (línea vacía pegada) o un \r suelto delante no forman parte de la tolerancia.
    assert reason(parse_line(VECTORS[1] + "\n\n")) is BadLineReason.NO_CRC
    assert reason(parse_line(VECTORS[1] + "\r\r\n")) is BadLineReason.NO_CRC


# --- razones de BadLine ---


@pytest.mark.parametrize("raw", ["", "\n", "\r\n", b"", b"\n", b"\r\n"])
def test_empty(raw: str | bytes) -> None:
    assert reason(parse_line(raw)) is BadLineReason.EMPTY


def test_bad_line_keeps_raw_text() -> None:
    assert parse_line("T,1,2*00\n") == BadLine(reason=BadLineReason.BAD_CRC, raw="T,1,2*00\n")
    assert parse_line(b"T,1,2*00\r\n") == BadLine(reason=BadLineReason.BAD_CRC, raw="T,1,2*00\r\n")


def test_non_ascii_bytes() -> None:
    result = parse_line(b"T,1,\xff*00")
    assert result == BadLine(reason=BadLineReason.NOT_ASCII, raw="T,1,\\xff*00")


def test_non_ascii_text() -> None:
    assert reason(parse_line(line("T,1,2") + "é")) is BadLineReason.NOT_ASCII


def test_too_long() -> None:
    body = "T,1," + "0" * (MAX_LINE_LENGTH - 7)
    at_limit = line(body)
    assert len(at_limit) == MAX_LINE_LENGTH
    assert parse_line(at_limit + "\r\n") == Tick(boot_id=1, millis=0)
    over = line(body + "0")
    assert len(over) == MAX_LINE_LENGTH + 1
    assert reason(parse_line(over)) is BadLineReason.TOO_LONG
    assert reason(parse_line(over.encode("ascii"))) is BadLineReason.TOO_LONG


def test_max_line_length_is_1024() -> None:
    assert MAX_LINE_LENGTH == 1024


def test_too_long_is_checked_before_ascii() -> None:
    assert reason(parse_line("é" * (MAX_LINE_LENGTH + 1))) is BadLineReason.TOO_LONG


@pytest.mark.parametrize("raw", ["T,1,2", "T,1,2*", "T,1,2*1", "T,1,2*123", "T,1,2*G0", "T,1,2*0G", "T,1,2* 1", "T,1,2*+1"])
def test_no_crc(raw: str) -> None:
    assert reason(parse_line(raw)) is BadLineReason.NO_CRC


def test_bad_crc() -> None:
    good = line("T,1,2")
    wrong = (int(good[-2:], 16) ^ 0x01) & 0xFF
    assert reason(parse_line(f"T,1,2*{wrong:02X}")) is BadLineReason.BAD_CRC


@pytest.mark.parametrize("body", ["X,1,2", "h,1,2,0.1,3", "t,1,2", "d,E,1,2,1,AAA=", ",1,2", "TT,1,2", "HT,1,2"])
def test_unknown_type(body: str) -> None:
    assert reason(parse_line(line(body))) is BadLineReason.UNKNOWN_TYPE


def test_empty_body_with_crc_is_unknown_type() -> None:
    assert reason(parse_line(line(""))) is BadLineReason.UNKNOWN_TYPE


@pytest.mark.parametrize("version", ["0", "2", "10", "01x"])
def test_unsupported_or_bad_version(version: str) -> None:
    result = parse_line(line(f"H,{version},1,0.1.0,2"))
    expected = BadLineReason.BAD_FIELD if version == "01x" else BadLineReason.UNSUPPORTED_VERSION
    assert reason(result) is expected


def test_version_with_leading_zero_is_one() -> None:
    assert parse_line(line("H,01,1,0.1.0,2")) == Hello(version=1, boot_id=1, firmware_version="0.1.0", millis=2)


@pytest.mark.parametrize(
    "body",
    [
        "H,1,1,0.1.0",  # falta un campo
        "H,1,1,0.1.0,2,3",  # sobra un campo
        "H,1,,0.1.0,2",
        "H,1,1,,2",  # firmware vacío
        "H,1,1,0.1 0,2",  # espacio en la versión de firmware
        "H,1,1,0.1*0,2",  # asterisco dentro del cuerpo
        "H,1,1,0.1\t0,2",
        "H,1,1,0.1\x7f0,2",
        "H,1,-1,0.1.0,2",
        "H,1,+1,0.1.0,2",
        "H,1,1_0,0.1.0,2",
        "H,1, 1,0.1.0,2",
        "H,1,4294967296,0.1.0,2",  # boot_id > u32
        "H,1,1,0.1.0,4294967296",  # millis > u32
        "H,,1,0.1.0,2",
        "T",
        "T,1",
        "T,1,2,3",
        "T,4294967296,2",
        "T,1,4294967296",
        "T,a,2",
        "T,1,",
    ],
)
def test_bad_field_hello_tick(body: str) -> None:
    assert reason(parse_line(line(body))) is BadLineReason.BAD_FIELD


@pytest.mark.parametrize(
    "body",
    [
        "D,E,1,2,1",  # falta el payload
        "D,E,1,2,1,AAA=,x",
        "D,X,1,2,1,AAA=",  # kind desconocido
        "D,,1,2,1,AAA=",
        "D,EE,1,2,1,AAA=",
        "D,e,1,2,1,AAA=",
        "D,E,65536,2,1,AAA=",  # frame_seq > u16
        "D,E,-1,2,1,AAA=",
        "D,E,1,4294967296,1,AAA=",  # t0 > u32
        "D,E,1,2,0,",  # n = 0
        "D,E,1,2,0,AAA=",
        "D,E,1,2,x,AAA=",
        "D,E,1,2,,AAA=",
    ],
)
def test_bad_field_frame(body: str) -> None:
    assert reason(parse_line(line(body))) is BadLineReason.BAD_FIELD


@pytest.mark.parametrize("b64", ["AAA", "AA=A", "AA A=", "AAA!", "AAAAA", "=AAA"])
def test_bad_base64(b64: str) -> None:
    assert reason(parse_line(line(f"D,E,1,2,1,{b64}"))) is BadLineReason.BAD_BASE64


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize(
    ("kind", "n", "size"),
    [
        ("E", 1, 0),
        ("E", 1, 1),
        ("E", 1, 3),
        ("E", 2, 2),
        ("S", 1, 1),
        ("S", 1, 4),
        ("I", 1, 2),
        ("I", 1, 11),
        ("I", 1, 13),
        ("I", 2, 12),
        ("I", 1, 24),
    ],
)
def test_bad_length(kind: str, n: int, size: int) -> None:
    assert reason(parse_line(line(f"D,{kind},1,2,{n},{b64(bytes(size))}"))) is BadLineReason.BAD_LENGTH


def test_empty_payload_is_bad_length() -> None:
    assert reason(parse_line(line("D,E,1,2,1,"))) is BadLineReason.BAD_LENGTH


@pytest.mark.parametrize(("kind", "size"), [("E", 2), ("S", 2), ("I", 12)])
def test_exact_length_is_accepted(kind: str, size: int) -> None:
    assert isinstance(parse_line(line(f"D,{kind},1,2,1,{b64(bytes(size))}")), Frame)


def test_bad_line_reason_values() -> None:
    assert {r.value for r in BadLineReason} == {
        "empty",
        "too_long",
        "not_ascii",
        "no_crc",
        "bad_crc",
        "unknown_type",
        "bad_field",
        "unsupported_version",
        "bad_base64",
        "bad_length",
    }


# --- fronteras de rango ---


def test_range_limits_are_inclusive() -> None:
    assert parse_line(line("T,4294967295,4294967295")) == Tick(boot_id=4294967295, millis=4294967295)
    assert parse_line(line("T,0,0")) == Tick(boot_id=0, millis=0)
    frame = parse_line(line(f"D,E,65535,4294967295,1,{b64(b'\xff\xff')}"))
    assert frame == Frame(kind=FrameKind.EMG, frame_seq=65535, t0_ms=4294967295, samples=(65535,))


def test_emg_is_not_range_checked_against_adc_bits() -> None:
    # El parser no sabe de ADC de 10 bits: solo exige 16 bits sin signo.
    assert parse_line(line(f"D,S,0,0,1,{b64(struct.pack('<H', 4096))}")) == Frame(kind=FrameKind.CORD, frame_seq=0, t0_ms=0, samples=(4096,))


def test_imu_samples_are_signed_little_endian() -> None:
    payload = struct.pack("<6h", -1, 1, -32768, 32767, 256, -256)
    assert parse_line(line(f"D,I,0,0,1,{b64(payload)}")) == Frame(kind=FrameKind.IMU, frame_seq=0, t0_ms=0, samples=((-1, 1, -32768, 32767, 256, -256),))


def test_emg_samples_are_unsigned_little_endian() -> None:
    assert parse_line(line(f"D,E,0,0,2,{b64(b'\x01\x02\xff\xff')}")) == Frame(kind=FrameKind.EMG, frame_seq=0, t0_ms=0, samples=(0x0201, 65535))


# --- encoder: validación ---


U32_RANGE = "[0, 4294967295]"
U16_RANGE = "[0, 65535]"
I16_RANGE = "[-32768, 32767]"
FW_MESSAGE = "firmware_version must be non-empty printable ASCII without space, ',' or '*': "


def hello(**changes: int | str) -> Hello:
    return replace(Hello(version=1, boot_id=0, firmware_version="0.1.0", millis=0), **changes)


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (hello(version=-1), f"version out of range {U32_RANGE}: -1"),
        (hello(version=4294967296), f"version out of range {U32_RANGE}: 4294967296"),
        (hello(boot_id=-1), f"boot_id out of range {U32_RANGE}: -1"),
        (hello(boot_id=4294967296), f"boot_id out of range {U32_RANGE}: 4294967296"),
        (hello(millis=-1), f"millis out of range {U32_RANGE}: -1"),
        (hello(millis=4294967296), f"millis out of range {U32_RANGE}: 4294967296"),
        (hello(firmware_version=""), FW_MESSAGE + "''"),
        (hello(firmware_version="0,1"), FW_MESSAGE + "'0,1'"),
        (hello(firmware_version="0*1"), FW_MESSAGE + "'0*1'"),
        (hello(firmware_version="0 1"), FW_MESSAGE + "'0 1'"),
        (hello(firmware_version="0.1é"), FW_MESSAGE + "'0.1é'"),
    ],
)
def test_encode_hello_rejects_out_of_range(message: Hello, error: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        encode_hello(message)
    assert str(excinfo.value) == error


def test_encode_hello_limits() -> None:
    assert encode_hello(Hello(version=0, boot_id=0, firmware_version="!~", millis=0)) == line("H,0,0,!~,0")
    assert encode_hello(hello(version=4294967295, boot_id=4294967295, millis=4294967295)) == line("H,4294967295,4294967295,0.1.0,4294967295")


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (Tick(boot_id=-1, millis=0), f"boot_id out of range {U32_RANGE}: -1"),
        (Tick(boot_id=4294967296, millis=0), f"boot_id out of range {U32_RANGE}: 4294967296"),
        (Tick(boot_id=0, millis=-1), f"millis out of range {U32_RANGE}: -1"),
        (Tick(boot_id=0, millis=4294967296), f"millis out of range {U32_RANGE}: 4294967296"),
    ],
)
def test_encode_tick_rejects_out_of_range(message: Tick, error: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        encode_tick(message)
    assert str(excinfo.value) == error


def test_encode_tick_limits() -> None:
    assert encode_tick(Tick(boot_id=0, millis=0)) == line("T,0,0")
    assert encode_tick(Tick(boot_id=4294967295, millis=4294967295)) == line("T,4294967295,4294967295")


def emg(**changes: object) -> Frame:
    return replace(Frame(kind=FrameKind.EMG, frame_seq=0, t0_ms=0, samples=(0,)), **changes)


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (emg(frame_seq=-1), f"frame_seq out of range {U16_RANGE}: -1"),
        (emg(frame_seq=65536), f"frame_seq out of range {U16_RANGE}: 65536"),
        (emg(t0_ms=-1), f"t0_ms out of range {U32_RANGE}: -1"),
        (emg(t0_ms=4294967296), f"t0_ms out of range {U32_RANGE}: 4294967296"),
        (emg(samples=()), "a frame needs at least one sample"),
        (emg(samples=(-1,)), f"EMG sample out of range {U16_RANGE}: -1"),
        (emg(kind=FrameKind.CORD, samples=(0, 65536)), f"CORD sample out of range {U16_RANGE}: 65536"),
        (emg(samples=((0, 0, 0, 0, 0, 0),)), "EMG samples must be integers: (0, 0, 0, 0, 0, 0)"),
        (emg(kind=FrameKind.IMU, samples=(0,)), "IMU samples must be 6-tuples: 0"),
        (emg(kind=FrameKind.IMU, samples=((0, 0, 0, 0, 0),)), "IMU samples must be 6-tuples: (0, 0, 0, 0, 0)"),
        (emg(kind=FrameKind.IMU, samples=((0, 0, 0, 0, 0, 32768),)), f"IMU sample out of range {I16_RANGE}: 32768"),
        (emg(kind=FrameKind.IMU, samples=((-32769, 0, 0, 0, 0, 0),)), f"IMU sample out of range {I16_RANGE}: -32769"),
        (emg(kind=FrameKind.IMU, samples=()), "a frame needs at least one sample"),
    ],
)
def test_encode_frame_rejects_invalid(message: Frame, error: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        encode_frame(message)
    assert str(excinfo.value) == error


def test_encode_frame_limits() -> None:
    assert encode_frame(emg()) == line("D,E,0,0,1,AAA=")
    assert encode_frame(emg(kind=FrameKind.CORD, frame_seq=65535, t0_ms=4294967295, samples=(65535, 0))) == line("D,S,65535,4294967295,2,//8AAA==")
    imu = emg(kind=FrameKind.IMU, samples=((-32768, 32767, 0, 0, 0, 0),))
    assert encode_frame(imu) == line(f"D,I,0,0,1,{b64(struct.pack('<6h', -32768, 32767, 0, 0, 0, 0))}")


def test_encode_has_no_newline_and_uppercase_crc() -> None:
    text = encode_tick(Tick(boot_id=0, millis=10))  # CRC con letras hex
    body, _, crc = text.partition("*")
    assert "\n" not in text
    assert crc == f"{crc8(body.encode('ascii')):02X}"
    assert crc == crc.upper()


# --- frame_gap ---


@pytest.mark.parametrize(
    ("prev", "seq", "gap"),
    [
        (None, 0, 0),
        (None, 12345, 0),
        (0, 1, 0),
        (7, 8, 0),
        (7, 10, 2),
        (65535, 0, 0),
        (65534, 1, 2),
        (65535, 5, 5),
        (0, 32768, 32767),  # mayor salto hacia delante representable
        (0, 32769, -32768),  # a partir de aquí se interpreta como retroceso
        (7, 7, -1),  # duplicado
        (7, 6, -2),  # retroceso de 1
        (0, 65535, -2),
        (3, 65535, -5),
    ],
)
def test_frame_gap(prev: int | None, seq: int, gap: int) -> None:
    assert frame_gap(prev, seq) == gap
