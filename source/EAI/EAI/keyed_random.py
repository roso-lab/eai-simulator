# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause

"""Order-independent pseudo-random values for reproducible episode branches."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable

import numpy as np


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("random key components must be finite JSON values") from exc


def keyed_seed(seed: int, process: str, entity: Any) -> int:
    """Return a stable per-entity seed without depending on roster order."""
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not isinstance(process, str) or not process.strip():
        raise ValueError("process must be a nonempty string")
    material = _canonical([seed, process, entity]).encode("utf-8")
    return int.from_bytes(sha256(material).digest()[:16], "big")


class KeyedRandom:
    """Stateless NumPy draws keyed by process, entity and logical event."""

    def __init__(self, seed: int, process: str):
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        if not isinstance(process, str) or not process.strip():
            raise ValueError("process must be a nonempty string")
        self.seed = seed
        self.process = process

    def _generator(self, entity: Any, event_key: Any) -> np.random.Generator:
        material = _canonical([self.seed, self.process, entity, event_key]).encode("utf-8")
        derived_seed = int.from_bytes(sha256(material).digest()[:16], "big")
        return np.random.default_rng(derived_seed)

    def sample(self, entity: Any, event_key: Any,
               sampler: Callable[[np.random.Generator], Any]) -> Any:
        """Run ``sampler`` against the event's isolated generator."""
        if not callable(sampler):
            raise TypeError("sampler must be callable")
        return sampler(self._generator(entity, event_key))

    def normal(self, entity: Any, event_key: Any, loc: Any = 0.0,
               scale: Any = 1.0, size: Any = None) -> Any:
        return self._generator(entity, event_key).normal(loc, scale, size)

    def uniform(self, entity: Any, event_key: Any, low: Any = 0.0,
                high: Any = 1.0, size: Any = None) -> Any:
        return self._generator(entity, event_key).uniform(low, high, size)

    def bernoulli(self, entity: Any, event_key: Any, probability: float) -> bool:
        probability = float(probability)
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be between zero and one")
        return bool(self._generator(entity, event_key).random() < probability)


__all__ = ["KeyedRandom", "keyed_seed"]
