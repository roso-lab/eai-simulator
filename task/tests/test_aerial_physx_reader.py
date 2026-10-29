from types import SimpleNamespace
import pytest

from EAI.physics import aerial_pose_reader as aerial


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


def test_pick_place_transport_bridge_is_removed_from_aerial_io():
    assert not hasattr(aerial, "PhysxAerialTransportBridge")
    assert "FixedJoint" not in aerial.__doc__
