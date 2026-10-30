"""Aleatoriedad jerárquica y reproducible bit a bit.

Cada rama del árbol se identifica por una tupla de claves, por convención
`(astronaut_id, day, slot, stream_id, purpose)` truncada al nivel que haga falta, por ejemplo:
- `("AST-01", 12, "latent")`                      latente del día 12
- `("AST-01", 12, "lat_pulldown", "emg_lat_left", "signal")`  señal de un stream en una sesión
- `("AST-01", 12, "device")`                      plan de arranques/reinicios del día

La semilla de una rama depende solo de (semilla global, claves): cualquier rama se regenera sola, sin
recorrer las anteriores, e igual sea cual sea el sink o la velocidad. Los UUID y los boot_id también se
derivan de la semilla, en un espacio separado de los generadores (no comparten estado).
"""

import hashlib
import uuid

import numpy as np

type Key = str | int

# Cada clave se convierte en un entero de 128 bits con el bit alto puesto: así siempre ocupa 4 palabras
# de 32 bits en la SeedSequence y dos tuplas de claves distintas nunca se concatenan igual.
_HIGH_BIT = 1 << 127


def _key_word(key: Key) -> int:
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        raise TypeError(f"rng keys must be str or int, got {type(key).__name__}: {key!r}")
    tag = "i" if isinstance(key, int) else "s"
    digest = hashlib.blake2b(f"{tag}:{key}".encode(), digest_size=16).digest()
    return int.from_bytes(digest, "big") | _HIGH_BIT


def seed_sequence(seed: int, *keys: Key) -> np.random.SeedSequence:
    """SeedSequence de la rama `keys` bajo la semilla global `seed` (entero >= 0)."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a non-negative int, got {seed!r}")
    return np.random.SeedSequence(entropy=seed, spawn_key=tuple(_key_word(k) for k in keys))


def generator(seed: int, *keys: Key) -> np.random.Generator:
    """Generador numpy (PCG64) de la rama `keys`."""
    return np.random.Generator(np.random.PCG64(seed_sequence(seed, *keys)))


def derive_uuid(seed: int, *keys: Key) -> str:
    """UUID v4 en minúsculas (patrón `uuid` del contrato) determinado por (seed, keys)."""
    words = seed_sequence(seed, "uuid", *keys).generate_state(4, dtype=np.uint32)
    return str(uuid.UUID(bytes=words.astype(">u4").tobytes(), version=4))


def derive_boot_id(seed: int, *keys: Key) -> int:
    """boot_id de 32 bits (0..2^32-1) determinado por (seed, keys)."""
    return int(seed_sequence(seed, "boot_id", *keys).generate_state(1, dtype=np.uint32)[0])


class RngTree:
    """Atajo para no arrastrar la semilla: `tree.rng(*keys)`, `tree.uuid(*keys)`, `tree.boot_id(*keys)`."""

    def __init__(self, seed: int) -> None:
        seed_sequence(seed)  # valida la semilla
        self.seed = seed

    def rng(self, *keys: Key) -> np.random.Generator:
        return generator(self.seed, *keys)

    def uuid(self, *keys: Key) -> str:
        return derive_uuid(self.seed, *keys)

    def boot_id(self, *keys: Key) -> int:
        return derive_boot_id(self.seed, *keys)
