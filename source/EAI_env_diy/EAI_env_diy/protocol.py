"""Result protocol between the Isaac authoring subprocess and simulator.py."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


_IN_PROCESS_CALLBACK = None
_IN_PROCESS_INITIAL_SELECTION = None
_IN_PROCESS_ERROR = None


def set_in_process_callback(callback, *, initial_selection=None, error: str | None = None) -> None:
    global _IN_PROCESS_CALLBACK, _IN_PROCESS_INITIAL_SELECTION, _IN_PROCESS_ERROR
    _IN_PROCESS_CALLBACK = callback
    _IN_PROCESS_INITIAL_SELECTION = initial_selection
    _IN_PROCESS_ERROR = error


def take_in_process_callback():
    return _IN_PROCESS_CALLBACK


def in_process_restore_context() -> tuple[dict[str, Any] | None, str | None]:
    return _IN_PROCESS_INITIAL_SELECTION, _IN_PROCESS_ERROR


def clear_in_process_callback() -> None:
    global _IN_PROCESS_CALLBACK, _IN_PROCESS_INITIAL_SELECTION, _IN_PROCESS_ERROR
    _IN_PROCESS_CALLBACK = None
    _IN_PROCESS_INITIAL_SELECTION = None
    _IN_PROCESS_ERROR = None


@dataclass(frozen=True)
class AuthoringResult:
    status: str
    action: str
    selection: dict[str, Any] | None = None
    saved_task: dict[str, Any] | None = None
    saved_path: str | None = None
    error: str | None = None

    @classmethod
    def cancelled(cls) -> "AuthoringResult":
        return cls(status="cancelled", action="cancel")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: str | Path, *, overwrite: bool = True) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open("w" if overwrite else "x", encoding="utf-8") as stream:
                stream.write(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        except FileExistsError:
            pass
        return output


def write_cancelled_if_missing(path: str | Path) -> Path:
    """Record shutdown cancellation without replacing a completed/failed result."""
    return AuthoringResult.cancelled().write(path, overwrite=False)
