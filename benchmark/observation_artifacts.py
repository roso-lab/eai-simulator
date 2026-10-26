"""Content-addressed observation payload storage.

Only the evaluator/runtime sees the byte store.  Participant receipts carry a
digest and metadata; they never carry local paths or ``file://`` references.
"""
from __future__ import annotations

import hashlib
from typing import Mapping


class ObservationArtifactStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, payload: bytes) -> str:
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("artifact payload must be bytes")
        data = bytes(payload)
        digest = hashlib.sha256(data).hexdigest()
        self._objects.setdefault(digest, data)
        return digest

    def get(self, digest: str) -> bytes:
        if digest not in self._objects:
            raise KeyError(digest)
        return self._objects[digest]

    def manifest(self) -> Mapping[str, int]:
        return {digest: len(data) for digest, data in self._objects.items()}


__all__ = ["ObservationArtifactStore"]
