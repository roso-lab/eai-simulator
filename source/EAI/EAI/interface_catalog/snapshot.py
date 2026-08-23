from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SNAPSHOT_SCHEMA_VERSION = 1


@contextmanager
def _snapshot_lock(target: Path) -> Iterator[None]:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def build_snapshot(
    *,
    env_name: str,
    interfaces: list[dict[str, Any]],
    robots: list[dict[str, Any]],
    scene_key: str | None = None,
    pid: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    timestamp = time.time() if now is None else now
    snapshot = {
        "version": SNAPSHOT_SCHEMA_VERSION,
        "pid": os.getpid() if pid is None else pid,
        "env_name": env_name,
        "created_at": timestamp,
        "heartbeat_at": timestamp,
        "robots": robots,
        "interfaces": interfaces,
    }
    if scene_key is not None:
        snapshot["scene_key"] = scene_key
    return snapshot


def write_snapshot(path: Path | str, snapshot: dict[str, Any]) -> None:
    target = Path(path)
    with _snapshot_lock(target):
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def read_snapshot(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read runtime snapshot {target}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported runtime snapshot: {target}")
    return payload


def snapshot_age_seconds(snapshot: dict[str, Any], *, now: float | None = None) -> float:
    current = time.time() if now is None else now
    return max(0.0, current - float(snapshot.get("heartbeat_at", snapshot.get("created_at", current))))


def refresh_snapshot(
    path: Path | str,
    snapshot: dict[str, Any],
    *,
    robots: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    updated = dict(snapshot)
    if robots is not None:
        updated["robots"] = robots
    updated["heartbeat_at"] = time.time() if now is None else now
    write_snapshot(path, updated)
    return updated


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def remove_snapshot(path: Path | str, *, pid: int | None = None) -> bool:
    target = Path(path)
    owner = os.getpid() if pid is None else pid
    with _snapshot_lock(target):
        try:
            snapshot = read_snapshot(target)
        except ValueError:
            return False
        if int(snapshot.get("pid", -1)) != owner:
            return False
        target.unlink()
        return True


def remove_stale_snapshot(path: Path | str, *, pid: int | None = None) -> bool:
    target = Path(path)
    with _snapshot_lock(target):
        try:
            snapshot = read_snapshot(target)
            owner = int(snapshot.get("pid", -1))
        except (TypeError, ValueError):
            return False
        if pid is not None and owner != pid:
            return False
        if _process_is_running(owner):
            return False
        target.unlink()
        return True
