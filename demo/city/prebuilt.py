"""Per-process handoff from the EAI scene spawn callback to the episode runtime."""
from __future__ import annotations

_PREBUILT = None


def publish(value) -> None:
    global _PREBUILT
    _PREBUILT = value


def consume():
    return _PREBUILT


def clear() -> None:
    global _PREBUILT
    _PREBUILT = None
