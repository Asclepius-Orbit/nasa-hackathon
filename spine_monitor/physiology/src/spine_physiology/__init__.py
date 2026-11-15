"""Synthetic mission data generator: publishes exactly what the hardware would publish.

Pure core (`MissionGenerator` produces `Envelope`s with contract objects) + sinks (JSONL, MQTT) +
interactive fake gateway. All configuration comes from a YAML (`load_config`), with no default
numeric values in the code.
"""

from spine_physiology.config import MissionConfig, load_config
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.generator import MissionGenerator
from spine_physiology.sessions import SessionRef

__all__ = ["Envelope", "EnvelopeKind", "MissionConfig", "MissionGenerator", "SessionRef", "load_config"]
