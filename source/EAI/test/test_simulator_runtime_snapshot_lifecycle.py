from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator


class FakeAtexit:
    def __init__(self):
        self.callbacks = []

    def register(self, callback):
        self.callbacks.append(callback)

    def unregister(self, callback):
        self.callbacks.remove(callback)


class FakeSignal:
    SIGINT = 2
    SIGTERM = 15
    SIG_DFL = 0
    SIG_IGN = 1

    def __init__(self, handlers=None):
        self.handlers = dict(handlers or {self.SIGINT: self.SIG_DFL, self.SIGTERM: self.SIG_DFL})

    def getsignal(self, signum):
        return self.handlers[signum]

    def signal(self, signum, handler):
        previous = self.handlers[signum]
        self.handlers[signum] = handler
        return previous


def test_lifecycle_signal_cleans_once_then_chains_old_handler(tmp_path):
    events = []

    def old_handler(signum, frame):
        events.append(("old", signum, frame))

    fake_signal = FakeSignal({FakeSignal.SIGINT: old_handler, FakeSignal.SIGTERM: FakeSignal.SIG_DFL})
    fake_atexit = FakeAtexit()
    path = tmp_path / "runtime.json"

    with simulator._runtime_snapshot_lifecycle(
        path,
        pid=123,
        atexit_module=fake_atexit,
        signal_module=fake_signal,
        remove_snapshot_func=lambda value, *, pid: events.append(("remove", value, pid)),
    ):
        installed = fake_signal.handlers[FakeSignal.SIGINT]
        installed(FakeSignal.SIGINT, "frame")
        fake_atexit.callbacks[0]()

    assert events == [("remove", path, 123), ("old", FakeSignal.SIGINT, "frame")]
    assert fake_signal.handlers[FakeSignal.SIGINT] is old_handler
    assert fake_signal.handlers[FakeSignal.SIGTERM] == FakeSignal.SIG_DFL
    assert fake_atexit.callbacks == []


def test_lifecycle_default_sigint_is_not_swallowed(tmp_path):
    removed = []
    fake_signal = FakeSignal()

    with pytest.raises(KeyboardInterrupt):
        with simulator._runtime_snapshot_lifecycle(
            tmp_path / "runtime.json",
            pid=456,
            atexit_module=FakeAtexit(),
            signal_module=fake_signal,
            remove_snapshot_func=lambda value, *, pid: removed.append((value, pid)),
        ):
            fake_signal.handlers[FakeSignal.SIGINT](FakeSignal.SIGINT, None)

    assert removed == [(tmp_path / "runtime.json", 456)]


def test_main_installs_snapshot_guard_before_interfaces_menu():
    source = Path(simulator.__file__).read_text(encoding="utf-8")
    publish = source.index("snapshot_path, runtime_snapshot = _publish_runtime_interface_snapshot(")
    guard = source.index("snapshot_lifecycle.__enter__()", publish)
    menu = source.index('if getattr(args_cli, "interfaces_menu", False):', publish)
    loop_try = source.index("        try:", guard)

    assert publish < guard < loop_try < menu


def test_lifecycle_preserves_replaced_handler(tmp_path):
    fake_signal = FakeSignal()
    replacement = lambda signum, frame: None

    with simulator._runtime_snapshot_lifecycle(
        tmp_path / "runtime.json",
        atexit_module=FakeAtexit(),
        signal_module=fake_signal,
        remove_snapshot_func=lambda value, *, pid: None,
    ):
        fake_signal.handlers[FakeSignal.SIGTERM] = replacement

    assert fake_signal.handlers[FakeSignal.SIGTERM] is replacement
