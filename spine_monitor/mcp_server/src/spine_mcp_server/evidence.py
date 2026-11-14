"""Offline, deterministic lexical-vector evidence index; no network access."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

from pydantic import BaseModel


class EvidenceUnavailable(ValueError):
    """The prepared offline evidence resources are missing or invalid."""


class Passage(BaseModel):
    id: str
    title: str
    url: str
    locator: str
    text: str
    limitations: str


class EvidenceResult(BaseModel):
    matches: list[Passage]
    truncated: bool


_STOPWORDS = frozenset("a an and are as at be by for from in is it of on or that the this to was were with".split())


def _tokens(text: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return Counter(term for term in re.findall(r"[^\W_]+", normalized) if term not in _STOPWORDS)


def _corpus_bytes(corpus: Path) -> bytes:
    """Use the same bytes after a Git checkout with LF or CRLF line endings."""
    return corpus.read_bytes().replace(b"\r\n", b"\n")


def build_index(corpus: Path, output: Path) -> None:
    """Prepare a reproducible sparse term-frequency vector index from the local corpus."""
    raw = _corpus_bytes(corpus)
    passages = [Passage.model_validate(item) for item in json.loads(raw)]
    index = {"corpus_sha256": hashlib.sha256(raw).hexdigest(), "vectors": [_tokens(p.text) for p in passages]}
    output.write_text(json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


class EvidenceIndex:
    def __init__(self, index: Path, corpus: Path | None = None) -> None:
        self.index = index
        self.corpus = corpus if corpus is not None else index.with_name("corpus.json")

    @classmethod
    def default(cls) -> EvidenceIndex:
        directory = Path(os.environ.get("SPINE_EVIDENCE_DIR") or Path(__file__).with_name("evidence_data"))
        return cls(directory / "index.json", directory / "corpus.json")

    def search(self, query: str, limit: int) -> list[Passage]:
        try:
            raw = _corpus_bytes(self.corpus)
            data = json.loads(self.index.read_text(encoding="utf-8"))
            passages = [Passage.model_validate(item) for item in json.loads(raw)]
            vectors = data["vectors"]
            if data["corpus_sha256"] != hashlib.sha256(raw).hexdigest() or len(vectors) != len(passages):
                raise ValueError("index does not match corpus")
            if not isinstance(vectors, list) or any(
                not isinstance(vector, dict) or any(
                    not isinstance(term, str) or type(count) is not int or count < 0
                    for term, count in vector.items()
                ) for vector in vectors
            ):
                raise ValueError("invalid index vectors")
            q = _tokens(query)
            scored = []
            for passage, vector in zip(passages, vectors, strict=True):
                dot = sum(count * vector.get(term, 0) for term, count in q.items())
                norm = math.sqrt(sum(value * value for value in vector.values()))
                if dot and norm:
                    scored.append((dot / norm, passage))
            scored.sort(key=lambda item: (-item[0], item[1].id))
            return [passage for _, passage in scored[:limit]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise EvidenceUnavailable(f"offline evidence index unavailable or invalid: {error}") from error
