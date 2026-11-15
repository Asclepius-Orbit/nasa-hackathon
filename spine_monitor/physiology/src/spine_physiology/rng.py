"""Hierarchical, bit-for-bit reproducible randomness.

Each branch of the tree is identified by a tuple of keys, by convention
`(astronaut_id, day, slot, stream_id, purpose)` truncated at the needed level, for example:
- `("AST-01", 12, "latent")`                      latent of day 12
- `("AST-01", 12, "lat_pulldown", "emg_lat_left", "signal")`  signal of a stream in a session
- `("AST-01", 12, "device")`                      plan of the day's boots/restarts
- `("AST-01", 12, "pre_spine_check")`             slot of the spine_check right before the exercise (session id; no "plan" branch: it has no jitter)

The seed of a branch depends only on (global seed, keys): any branch regenerates on its own, without
walking the previous ones, and the same whatever the sink or the speed. UUIDs and boot_ids are also
derived from the seed, in a space separate from the generators (they share no state).
"""

import hashlib
import uuid

import numpy as np

type Key = str | int

# Each key becomes a 128-bit integer with the top bit set: so it always takes 4 32-bit words
# in the SeedSequence and two different key tuples are never concatenated the same way.
_HIGH_BIT = 1 << 127


def _key_word(key: Key) -> int:
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        raise TypeError(f"rng keys must be str or int, got {type(key).__name__}: {key!r}")
    tag = "i" if isinstance(key, int) else "s"
    digest = hashlib.blake2b(f"{tag}:{key}".encode(), digest_size=16).digest()
    return int.from_bytes(digest, "big") | _HIGH_BIT


def seed_sequence(seed: int, *keys: Key) -> np.random.SeedSequence:
    """SeedSequence of branch `keys` under the global seed `seed` (integer >= 0)."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a non-negative int, got {seed!r}")
    return np.random.SeedSequence(entropy=seed, spawn_key=tuple(_key_word(k) for k in keys))


def generator(seed: int, *keys: Key) -> np.random.Generator:
    """numpy generator (PCG64) of branch `keys`."""
    return np.random.Generator(np.random.PCG64(seed_sequence(seed, *keys)))


def derive_uuid(seed: int, *keys: Key) -> str:
    """Lowercase UUID v4 (the contract's `uuid` pattern) determined by (seed, keys)."""
    words = seed_sequence(seed, "uuid", *keys).generate_state(4, dtype=np.uint32)
    return str(uuid.UUID(bytes=words.astype(">u4").tobytes(), version=4))


def derive_boot_id(seed: int, *keys: Key) -> int:
    """32-bit boot_id (0..2^32-1) determined by (seed, keys)."""
    return int(seed_sequence(seed, "boot_id", *keys).generate_state(1, dtype=np.uint32)[0])


class RngTree:
    """Shortcut to avoid carrying the seed around: `tree.rng(*keys)`, `tree.uuid(*keys)`, `tree.boot_id(*keys)`."""

    def __init__(self, seed: int) -> None:
        seed_sequence(seed)  # validates the seed
        self.seed = seed

    def rng(self, *keys: Key) -> np.random.Generator:
        return generator(self.seed, *keys)

    def uuid(self, *keys: Key) -> str:
        return derive_uuid(self.seed, *keys)

    def boot_id(self, *keys: Key) -> int:
        return derive_boot_id(self.seed, *keys)
