"""MQTT topics: asclepius/v1/crew/{astronaut_id}/device/{device_id}/...

Ids are validated before they are put in a topic, so an id can never smuggle in a `/`, `+` or `#`
and change which topic a message goes to.
"""

import re
from typing import NamedTuple

ROOT = "asclepius/v1"

# Mirror the patterns of common.schema.json (a test keeps them in sync).
ASTRONAUT_ID = re.compile(r"AST-[0-9]{2,3}")
DEVICE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")
STREAM_ID = re.compile(r"[a-z0-9][a-z0-9_]{1,31}")

SUBSCRIBE_ALL_STREAMS = f"{ROOT}/crew/+/device/+/stream/+"
SUBSCRIBE_ALL_STATUS = f"{ROOT}/crew/+/device/+/status"
SUBSCRIBE_ALL_ACKS = f"{ROOT}/crew/+/device/+/ack"
SUBSCRIBE_ALL_CLOCKS = f"{ROOT}/crew/+/device/+/clock"
SUBSCRIBE_ALL_CALIBRATIONS = f"{ROOT}/crew/+/device/+/calibration/+"


class StreamTopic(NamedTuple):
    astronaut_id: str
    device_id: str
    stream_id: str


def _checked(kind: str, value: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


def _device(astronaut_id: str, device_id: str) -> str:
    return (
        f"{ROOT}/crew/{_checked('astronaut_id', astronaut_id, ASTRONAUT_ID)}"
        f"/device/{_checked('device_id', device_id, DEVICE_ID)}"
    )


def stream(astronaut_id: str, device_id: str, stream_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/stream/{_checked('stream_id', stream_id, STREAM_ID)}"


def status(astronaut_id: str, device_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/status"


def cmd(astronaut_id: str, device_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/cmd"


def ack(astronaut_id: str, device_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/ack"


def clock(astronaut_id: str, device_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/clock"


def calibration(astronaut_id: str, device_id: str, stream_id: str) -> str:
    return f"{_device(astronaut_id, device_id)}/calibration/{_checked('stream_id', stream_id, STREAM_ID)}"


_STREAM_TOPIC = re.compile(
    rf"{re.escape(ROOT)}/crew/(?P<astronaut_id>{ASTRONAUT_ID.pattern})"
    rf"/device/(?P<device_id>{DEVICE_ID.pattern})"
    rf"/stream/(?P<stream_id>{STREAM_ID.pattern})"
)


def parse_stream(topic: str) -> StreamTopic:
    """Ids of a stream topic. Raises ValueError for any other topic."""
    match = _STREAM_TOPIC.fullmatch(topic)
    if match is None:
        raise ValueError(f"not a stream topic: {topic!r}")
    return StreamTopic(**match.groupdict())


_CALIBRATION_TOPIC = re.compile(
    rf"{re.escape(ROOT)}/crew/(?P<astronaut_id>{ASTRONAUT_ID.pattern})"
    rf"/device/(?P<device_id>{DEVICE_ID.pattern})"
    rf"/calibration/(?P<stream_id>{STREAM_ID.pattern})"
)


def parse_calibration(topic: str) -> StreamTopic:
    """Ids of a calibration topic. Raises ValueError for any other topic."""
    match = _CALIBRATION_TOPIC.fullmatch(topic)
    if match is None:
        raise ValueError(f"not a calibration topic: {topic!r}")
    return StreamTopic(**match.groupdict())
