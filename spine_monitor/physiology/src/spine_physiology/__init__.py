"""Generador de datos sintéticos de misión: publica exactamente lo que publicaría el hardware.

Núcleo puro (`MissionGenerator` produce `Envelope`s con objetos de contrato) + sinks (JSONL, MQTT) +
gateway falso interactivo. Toda la configuración sale de un YAML (`load_config`), sin valores
numéricos por defecto en el código.
"""

from spine_physiology.config import MissionConfig, load_config
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.generator import MissionGenerator
from spine_physiology.sessions import SessionRef

__all__ = ["Envelope", "EnvelopeKind", "MissionConfig", "MissionGenerator", "SessionRef", "load_config"]
