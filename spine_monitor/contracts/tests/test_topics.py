import json
from pathlib import Path

import pytest

from spine_contracts import topics

COMMON = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "v1" / "common.schema.json").read_text(encoding="utf-8"))


def test_topic_layout():
    assert topics.stream("AST-01", "wb-01", "emg_lat_left") == "asclepius/v1/crew/AST-01/device/wb-01/stream/emg_lat_left"
    assert topics.status("AST-01", "wb-01") == "asclepius/v1/crew/AST-01/device/wb-01/status"
    assert topics.cmd("AST-01", "wb-01") == "asclepius/v1/crew/AST-01/device/wb-01/cmd"
    assert topics.ack("AST-01", "wb-01") == "asclepius/v1/crew/AST-01/device/wb-01/ack"
    assert topics.clock("AST-01", "wb-01") == "asclepius/v1/crew/AST-01/device/wb-01/clock"


def test_wildcard_subscriptions():
    assert topics.SUBSCRIBE_ALL_STREAMS == "asclepius/v1/crew/+/device/+/stream/+"
    assert topics.SUBSCRIBE_ALL_STATUS == "asclepius/v1/crew/+/device/+/status"


def test_parse_stream_round_trip():
    topic = topics.stream("AST-02", "sim-01", "imu_l3")

    assert topics.parse_stream(topic) == ("AST-02", "sim-01", "imu_l3")
    assert topics.parse_stream(topic).stream_id == "imu_l3"


@pytest.mark.parametrize(
    "topic",
    [
        "asclepius/v1/crew/AST-01/device/wb-01/status",
        "asclepius/v2/crew/AST-01/device/wb-01/stream/imu_l3",
        "asclepius/v1/crew/AST-01/device/wb-01/stream/imu_l3/extra",
        "asclepius/v1/crew/AST-01/device/wb-01/stream/imu_l3\n",
        "asclepius/v1/crew/+/device/+/stream/+",
    ],
)
def test_parse_stream_rejects_other_topics(topic):
    with pytest.raises(ValueError):
        topics.parse_stream(topic)


@pytest.mark.parametrize("bad", ["AST-01/x", "+", "#", "AST-01\n", "", "Neil Armstrong", "AST-1"])
def test_ids_cannot_change_the_topic(bad):
    with pytest.raises(ValueError):
        topics.status(bad, "wb-01")


@pytest.mark.parametrize("bad", ["wb-01/other", "+", "#", "WB-01", "x"])
def test_device_and_stream_ids_are_checked(bad):
    with pytest.raises(ValueError):
        topics.cmd("AST-01", bad)
    with pytest.raises(ValueError):
        topics.stream("AST-01", "wb-01", bad)


@pytest.mark.parametrize(
    ("pattern", "definition"),
    [(topics.ASTRONAUT_ID, "astronaut_id"), (topics.DEVICE_ID, "device_id"), (topics.STREAM_ID, "stream_id")],
)
def test_id_patterns_match_the_schema(pattern, definition):
    schema_pattern = COMMON["$defs"][definition]["pattern"]

    assert schema_pattern == f"^{pattern.pattern}$"
