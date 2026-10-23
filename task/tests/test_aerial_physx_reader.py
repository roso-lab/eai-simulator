from types import SimpleNamespace
from contextlib import nullcontext
import sys

import pytest

from task.executors import aerial_pick_place as aerial


def test_live_reader_releases_native_backends_on_timeline_stop(monkeypatch):
    callbacks, invalidated = [], []
    simulation = SimpleNamespace(_backend=object(), invalidate=lambda: invalidated.append(True))
    view = SimpleNamespace(_backend=object())
    stream = SimpleNamespace(create_subscription_to_pop_by_type=lambda kind, fn, order: callbacks.append(fn))
    timeline = SimpleNamespace(TimelineEventType=SimpleNamespace(STOP=2),
        get_timeline_interface=lambda: SimpleNamespace(get_timeline_event_stream=lambda: stream))
    original = aerial.importlib.import_module
    monkeypatch.setattr(aerial.importlib, "import_module", lambda name: timeline if name == "omni.timeline" else original(name))
    reader = aerial.LivePhysxPoseReader(object())
    reader._views["/body"] = (simulation, view)
    callbacks[0](None)
    reader.close()
    assert invalidated == [True]
    assert simulation._backend is None and view._backend is None
    assert reader._views == {}
    with pytest.raises(ValueError, match="stopped session"):
        reader("/body")


def test_native_close_failure_still_releases_every_backend():
    reader = aerial.LivePhysxPoseReader.__new__(aerial.LivePhysxPoseReader)
    calls = []
    failure = RuntimeError("first view invalidation failed")
    def invalidate_first():
        calls.append("first")
        raise failure
    first = SimpleNamespace(_backend=object(), invalidate=invalidate_first)
    second = SimpleNamespace(_backend=object(), invalidate=lambda: calls.append("second"))
    first_view, second_view = SimpleNamespace(_backend=object()), SimpleNamespace(_backend=object())
    reader._views = {"/first": (first, first_view), "/second": (second, second_view)}
    with pytest.raises(RuntimeError) as caught:
        reader.close()
    assert caught.value is failure
    assert calls == ["first", "second"] and reader._views == {}
    assert all(x._backend is None for x in (first, second, first_view, second_view))


def test_owned_joint_is_disabled_and_flushed_before_deletion(monkeypatch):
    events = []
    prim = SimpleNamespace(GetAttribute=lambda name: SimpleNamespace(
        IsValid=lambda: True, Set=lambda enabled: events.append(("enabled", enabled))))
    stage = SimpleNamespace(
        GetPrimAtPath=lambda path: prim if not any(e == "remove" for e in events) else None,
        RemovePrim=lambda path: events.append("remove") or True)
    monkeypatch.setitem(sys.modules, "pxr", SimpleNamespace(
        Usd=SimpleNamespace(EditContext=lambda *args: nullcontext())))
    bridge = aerial.PhysxAerialTransportBridge.__new__(aerial.PhysxAerialTransportBridge)
    bridge.stage, bridge._edit_target = stage, "session"
    bridge._owned, bridge._flush = {"/joint"}, lambda: events.append("flush")
    bridge._remove("/joint")
    assert events == [("enabled", False), "flush", "remove", "flush"]
    assert not bridge._owned


def test_native_deletion_flush_failure_keeps_owned_path_for_cleanup(monkeypatch):
    exists, flushes = [True], []
    stage = SimpleNamespace(
        GetPrimAtPath=lambda path: SimpleNamespace(GetAttribute=lambda name: None) if exists[0] else None,
        RemovePrim=lambda path: exists.__setitem__(0, False) or True)
    monkeypatch.setitem(sys.modules, "pxr", SimpleNamespace(
        Usd=SimpleNamespace(EditContext=lambda *args: nullcontext())))
    bridge = aerial.PhysxAerialTransportBridge.__new__(aerial.PhysxAerialTransportBridge)
    bridge.stage, bridge._edit_target, bridge._owned = stage, "session", {"/joint"}
    def flush():
        flushes.append(True)
        if len(flushes) == 2:
            raise RuntimeError("native deletion pending")
    bridge._flush = flush
    with pytest.raises(RuntimeError, match="pending"):
        bridge._remove("/joint")
    assert not exists[0] and "/joint" in bridge._owned
    bridge._remove("/joint")
    assert not bridge._owned
