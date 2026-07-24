"""Shared gate for OpenAI-compatible local LLM requests."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional


_RADXA_LOCK = threading.RLock()


def is_radxa_local_base_url(base_url: Optional[str]) -> bool:
    value = str(base_url or "").lower()
    return "192.168.31.107:8910" in value or "127.0.0.1:8910" in value or "localhost:8910" in value


@contextmanager
def radxa_local_request_gate(base_url: Optional[str]) -> Iterator[None]:
    if is_radxa_local_base_url(base_url):
        with _RADXA_LOCK:
            yield
    else:
        yield
