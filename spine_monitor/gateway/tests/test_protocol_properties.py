"""Propiedades del protocolo de línea: ida y vuelta, parse_line nunca lanza y detección de corrupción de un carácter."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_gateway.protocol import (
    BadLine,
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

pytestmark = pytest.mark.property

U32 = st.integers(0, 0xFFFFFFFF)
U16 = st.integers(0, 0xFFFF)
I16 = st.integers(-0x8000, 0x7FFF)
FIRMWARE = st.text(st.characters(min_codepoint=0x21, max_codepoint=0x7E, exclude_characters=",*"), min_size=1, max_size=16)

hellos = st.builds(Hello, version=st.just(1), boot_id=U32, firmware_version=FIRMWARE, millis=U32)
ticks = st.builds(Tick, boot_id=U32, millis=U32)
scalar_frames = st.builds(
    Frame,
    kind=st.sampled_from([FrameKind.EMG, FrameKind.CORD]),
    frame_seq=U16,
    t0_ms=U32,
    samples=st.lists(U16, min_size=1, max_size=40).map(tuple),
)
imu_frames = st.builds(
    Frame,
    kind=st.just(FrameKind.IMU),
    frame_seq=U16,
    t0_ms=U32,
    samples=st.lists(st.tuples(I16, I16, I16, I16, I16, I16), min_size=1, max_size=20).map(tuple),
)
messages = st.one_of(hellos, ticks, scalar_frames, imu_frames)


def encode(message: Hello | Tick | Frame) -> str:
    match message:
        case Hello():
            return encode_hello(message)
        case Tick():
            return encode_tick(message)
        case Frame():
            return encode_frame(message)


@given(messages, st.sampled_from(["", "\n", "\r\n"]))
def test_roundtrip(message: Hello | Tick | Frame, terminator: str) -> None:
    text = encode(message)
    assert parse_line(text + terminator) == message
    assert parse_line((text + terminator).encode("ascii")) == message


@given(messages)
def test_encoded_line_is_canonical(message: Hello | Tick | Frame) -> None:
    # parse -> encode devuelve la misma línea: el encoder es la forma canónica que emite el firmware.
    text = encode(message)
    parsed = parse_line(text)
    assert not isinstance(parsed, BadLine)
    assert encode(parsed) == text
    body, star, crc = text.rpartition("*")
    assert star == "*"
    assert crc == f"{crc8(body.encode('ascii')):02X}"


@given(st.text(max_size=1100))
def test_parse_text_never_raises(raw: str) -> None:
    result = parse_line(raw)
    assert isinstance(result, Hello | Tick | Frame | BadLine)


@given(st.binary(max_size=1100))
def test_parse_bytes_never_raises(raw: bytes) -> None:
    result = parse_line(raw)
    assert isinstance(result, Hello | Tick | Frame | BadLine)


@given(st.data())
def test_body_with_valid_crc_never_raises(data: st.DataObject) -> None:
    # Cuerpos arbitrarios con CRC correcto: ejercita el análisis de campos más allá del filtro de CRC.
    alphabet = st.sampled_from(list("HTDEIS,0123456789+/=-AZaz.* \t"))
    body = data.draw(st.text(alphabet, max_size=80))
    result = parse_line(f"{body}*{crc8(body.encode('ascii')):02X}")
    assert isinstance(result, Hello | Tick | Frame | BadLine)


@given(messages, st.data())
def test_single_char_corruption_is_never_accepted_as_the_original(message: Hello | Tick | Frame, data: st.DataObject) -> None:
    # CRC-8 detecta toda ráfaga de error de hasta 8 bits, así que alterar un byte del cuerpo siempre cambia el CRC.
    # En el campo CRC solo se excluye el cambio de mayúscula/minúscula de un dígito hex, que es el mismo valor.
    text = encode(message)
    index = data.draw(st.integers(0, len(text) - 1))
    original = text[index]
    replacement = data.draw(st.characters(min_codepoint=0, max_codepoint=0x7F).filter(lambda c: c != original and c.lower() != original.lower()))
    corrupted = text[:index] + replacement + text[index + 1 :]
    assert parse_line(corrupted) != message


@given(U16, U16)
def test_frame_gap_is_consistent_with_modular_distance(prev: int, seq: int) -> None:
    gap = frame_gap(prev, seq)
    assert -0x8000 <= gap <= 0x7FFF
    assert (prev + gap + 1) % 0x10000 == seq


@given(U16, st.integers(0, 0x7FFF))
def test_frame_gap_counts_lost_frames(prev: int, lost: int) -> None:
    assert frame_gap(prev, (prev + lost + 1) % 0x10000) == lost
