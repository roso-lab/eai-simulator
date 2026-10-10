"""Session backend seam for scheduler-aware benchmark execution.

The collector owns branch identity and ledgers.  A session backend owns the
simulator/application and evaluator-facing runtime.  This module intentionally
contains no Isaac or benchmark imports so an upstream package can implement it
without changing the collector.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol


EmitRow = Callable[[str, Mapping[str, Any]], None]


class SessionBackend(Protocol):
    """Lifecycle contract for one scene-owned simulator session.

    ``run_branch`` must return an explicit mapping containing at least
    ``completed`` and ``status``.  Missing or ambiguous responses are recorded
    as ``missing_response`` by the collector; process exit is never treated as
    physical success.
    """

    def open_scene(self, scene_id: str, *, scheduler: Any) -> Any: ...

    def warmup(self, session: Any, *, ticks: int) -> None: ...

    def open_branch(self, session: Any, job: Any, *, lease: Any) -> Any: ...

    def run_branch(self, branch_runtime: Any, job: Any, *, emit: EmitRow) -> Mapping[str, Any]: ...

    def close_branch(self, branch_runtime: Any) -> None: ...

    def close_scene(self, session: Any) -> None: ...


def backend_method(backend: Any, name: str) -> Callable[..., Any] | None:
    """Return an optional backend method without imposing inheritance."""

    value = getattr(backend, name, None)
    return value if callable(value) else None


__all__ = ["EmitRow", "SessionBackend", "backend_method"]
