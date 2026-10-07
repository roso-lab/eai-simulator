"""Shared gate for OpenAI-compatible local LLM requests.

Serializes requests to single-instance local LLM servers so concurrent
agents do not overwhelm them. Loopback endpoints on the default port are
matched automatically; additional private endpoints can be configured with
the EMOS_LOCAL_LLM_HOSTS environment variable (comma-separated host:port
entries) instead of being hardcoded in the repository.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_LOCAL_LLM_HOSTS_ENV = "EMOS_LOCAL_LLM_HOSTS"
_DEFAULT_LOCAL_LLM_HOSTS = ("127.0.0.1:8910", "localhost:8910")

_RADXA_LOCK = threading.RLock()


def _local_llm_hosts() -> tuple[str, ...]:
    configured = os.environ.get(_LOCAL_LLM_HOSTS_ENV, "")
    extra = tuple(
        entry.strip().lower()
        for entry in configured.split(",")
        if entry.strip()
    )
    return extra + _DEFAULT_LOCAL_LLM_HOSTS


def is_radxa_local_base_url(base_url: Optional[str]) -> bool:
    value = str(base_url or "").lower()
    return any(host in value for host in _local_llm_hosts())


@contextmanager
def radxa_local_request_gate(base_url: Optional[str]) -> Iterator[None]:
    if is_radxa_local_base_url(base_url):
        with _RADXA_LOCK:
            yield
    else:
        yield
