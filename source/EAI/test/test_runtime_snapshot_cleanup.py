from __future__ import annotations

import os

from EAI.interface_catalog.snapshot import (
    build_snapshot,
    remove_snapshot,
    remove_stale_snapshot,
    write_snapshot,
)


def _write_runtime_snapshot(path, *, pid: int) -> None:
    write_snapshot(
        path,
        build_snapshot(
            env_name="keyboard",
            interfaces=[],
            robots=[],
            pid=pid,
            now=1.0,
        ),
    )


def test_remove_snapshot_keeps_different_live_owner(tmp_path):
    path = tmp_path / "runtime_interfaces.json"
    _write_runtime_snapshot(path, pid=os.getpid())

    assert remove_snapshot(path, pid=os.getpid() + 1) is False
    assert path.exists()


def test_remove_stale_snapshot_removes_dead_owner(tmp_path):
    path = tmp_path / "runtime_interfaces.json"
    _write_runtime_snapshot(path, pid=999_999_999)

    assert remove_stale_snapshot(path) is True
    assert not path.exists()


def test_remove_stale_snapshot_keeps_live_owner(tmp_path):
    path = tmp_path / "runtime_interfaces.json"
    _write_runtime_snapshot(path, pid=os.getpid())

    assert remove_stale_snapshot(path) is False
    assert path.exists()

