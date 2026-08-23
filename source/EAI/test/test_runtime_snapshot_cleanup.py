from __future__ import annotations

import os
import threading
import time

import EAI.interface_catalog.snapshot as snapshot_module
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


def test_remove_stale_snapshot_requires_expected_owner(tmp_path):
    path = tmp_path / "runtime_interfaces.json"
    _write_runtime_snapshot(path, pid=999_999_999)

    assert remove_stale_snapshot(path, pid=999_999_998) is False
    assert path.exists()


def test_stale_cleanup_and_new_writer_are_serialized(tmp_path, monkeypatch):
    path = tmp_path / "runtime_interfaces.json"
    _write_runtime_snapshot(path, pid=999_999_999)
    writer_started = threading.Event()
    writer_finished = threading.Event()
    writer = None

    def start_concurrent_writer(owner):
        nonlocal writer
        assert owner == 999_999_999

        def publish_live_snapshot():
            writer_started.set()
            _write_runtime_snapshot(path, pid=os.getpid())
            writer_finished.set()

        writer = threading.Thread(target=publish_live_snapshot)
        writer.start()
        assert writer_started.wait(timeout=1)
        time.sleep(0.01)
        assert not writer_finished.is_set()
        return False

    monkeypatch.setattr(snapshot_module, "_process_is_running", start_concurrent_writer)
    assert remove_stale_snapshot(path, pid=999_999_999) is True
    assert writer is not None
    writer.join(timeout=1)
    assert writer_finished.is_set()
    assert path.exists()

