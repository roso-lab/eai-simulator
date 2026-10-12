from __future__ import annotations

from argparse import Namespace
import json
import math
from pathlib import Path
import sys
import types
from types import MappingProxyType

import pytest

from tools.topology_authoring import author
from tools.topology_authoring.adapter import SceneSource
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import save_draft
from tools.topology_authoring import ui as authoring_ui
from tools.topology_authoring import viewport


class _DigestAdapter:
    def __init__(self, source: SceneSource):
        self.source = source
        self.loaded_stage = None

    def load_source(self, _asset_root):
        return self.source

    def load_stage(self, source):
        self.loaded_stage = source

    def source_digests(self, source):
        assert source.payload.get("semantic") == {"targets": ["goal"]}
        return {}

    def semantic_targets(self, _source):
        return []

    def validate_polyline(self, _polyline, _profile, _source):
        return []


def test_adapter_factory_fallback_uses_signature_and_preserves_internal_typeerror(monkeypatch):
    module = types.ModuleType("_test_topology_adapter_factory")

    class NoSceneKey:
        pass

    class BrokenForScene:
        def __init__(self, scene_key="default"):
            if scene_key == "requested":
                raise TypeError("invalid scene configuration")

    module.NoSceneKey = NoSceneKey
    module.BrokenForScene = BrokenForScene
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert isinstance(author._load(f"{module.__name__}:NoSceneKey", "requested"), NoSceneKey)
    with pytest.raises(TypeError, match="invalid scene configuration"):
        author._load(f"{module.__name__}:BrokenForScene", "requested")


def test_semantic_map_copies_immutable_adapter_payload(tmp_path):
    original_payload = MappingProxyType({"existing": 1})
    adapter = _DigestAdapter(SceneSource("warehouse", tmp_path, original_payload))

    source, document = author.build_document(
        adapter,
        tmp_path,
        {"targets": ["goal"]},
    )

    assert dict(original_payload) == {"existing": 1}
    assert source.payload == {"existing": 1, "semantic": {"targets": ["goal"]}}
    assert document.scene_key == "warehouse"


def test_build_document_rejects_source_for_a_different_requested_scene(tmp_path):
    adapter = _DigestAdapter(SceneSource("factory", tmp_path, {}))

    with pytest.raises(ValueError, match="differs from requested scene"):
        author.build_document(
            adapter,
            tmp_path,
            requested_scene_key="warehouse",
        )


def test_headless_validation_errors_return_failure(monkeypatch, tmp_path, capsys):
    class InvalidAdapter(_DigestAdapter):
        def source_digests(self, _source):
            return {}

    adapter = InvalidAdapter(SceneSource("invalid/key", tmp_path, {}))
    monkeypatch.setattr(author, "_load", lambda *_args: adapter)
    args = Namespace(
        adapter="unused:Adapter",
        scene_key="invalid/key",
        asset_root=tmp_path,
        semantic_map=None,
        generate=None,
    )

    assert author._headless(args) == 1
    assert json.loads(capsys.readouterr().out)["validation_errors"] == [
        "scene_key is invalid"
    ]


def _install_fake_simulation_app(monkeypatch):
    apps = []

    class FakeSimulationApp:
        def __init__(self, config):
            self.config = config
            self.closed = False
            apps.append(self)

        def is_running(self):
            return False

        def update(self):
            raise AssertionError("the fake app is never running")

        def close(self):
            self.closed = True

    isaacsim = types.ModuleType("isaacsim")
    isaacsim.SimulationApp = FakeSimulationApp
    monkeypatch.setitem(sys.modules, "isaacsim", isaacsim)
    return apps


def _gui_args(tmp_path):
    return Namespace(
        adapter="unused:Adapter",
        scene_key="warehouse",
        asset_root=tmp_path,
        semantic_map=None,
        output_root=tmp_path,
    )


def test_gui_closes_app_when_adapter_startup_fails(monkeypatch, tmp_path):
    apps = _install_fake_simulation_app(monkeypatch)
    monkeypatch.setattr(
        author,
        "_load",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("adapter failed")),
    )

    with pytest.raises(RuntimeError, match="adapter failed"):
        author._gui(_gui_args(tmp_path))

    assert apps[0].closed is True
    assert apps[0].config["fast_shutdown"] is False


def test_gui_closes_app_when_window_destroy_fails(monkeypatch, tmp_path):
    apps = _install_fake_simulation_app(monkeypatch)
    adapter = _DigestAdapter(SceneSource("warehouse", tmp_path, {}))
    monkeypatch.setattr(adapter, "source_digests", lambda _source: {})
    monkeypatch.setattr(author, "_load", lambda *_args: adapter)

    class BrokenWindow:
        def __init__(self, *_args, **_kwargs):
            pass

        def destroy(self):
            raise RuntimeError("window destroy failed")

    monkeypatch.setattr(authoring_ui, "TopologyAuthoringWindow", BrokenWindow)

    with pytest.raises(RuntimeError, match="window destroy failed"):
        author._gui(_gui_args(tmp_path))

    assert apps[0].closed is True


def test_gui_closes_app_when_shared_gui_cleanup_fails(monkeypatch, tmp_path):
    apps = _install_fake_simulation_app(monkeypatch)
    adapter = _DigestAdapter(SceneSource("warehouse", tmp_path, {}))
    monkeypatch.setattr(adapter, "source_digests", lambda _source: {})
    monkeypatch.setattr(author, "_load", lambda *_args: adapter)

    class Window:
        def __init__(self, *_args, **_kwargs):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(authoring_ui, "TopologyAuthoringWindow", Window)
    import simulator

    monkeypatch.setattr(
        simulator,
        "_prepare_gui_for_app_close",
        lambda _app: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        author._gui(_gui_args(tmp_path))

    assert apps[0].closed is True


def test_gui_rejects_invalid_source_before_stage_load_and_closes_app(
    monkeypatch, tmp_path
):
    apps = _install_fake_simulation_app(monkeypatch)

    class InvalidAdapter(_DigestAdapter):
        def source_digests(self, _source):
            return {"scene.usd": "not-a-sha256"}

    adapter = InvalidAdapter(SceneSource("warehouse", tmp_path, {}))
    monkeypatch.setattr(author, "_load", lambda *_args: adapter)

    with pytest.raises(ValueError, match="source validation failed"):
        author._gui(_gui_args(tmp_path))

    assert adapter.loaded_stage is None
    assert apps[0].closed is True


def test_window_constructor_destroys_partial_ui_window(monkeypatch, tmp_path):
    destroyed = []

    class Window:
        def __init__(self, *_args, **_kwargs):
            pass

        def destroy(self):
            destroyed.append(True)

    fake_ui = types.ModuleType("omni.ui")
    fake_ui.Window = Window
    fake_omni = types.ModuleType("omni")
    fake_omni.__path__ = []
    fake_omni.ui = fake_ui
    monkeypatch.setitem(sys.modules, "omni", fake_omni)
    monkeypatch.setitem(sys.modules, "omni.ui", fake_ui)
    monkeypatch.setattr(
        authoring_ui.TopologyAuthoringWindow,
        "_build",
        lambda _self: (_ for _ in ()).throw(RuntimeError("build failed")),
    )

    with pytest.raises(RuntimeError, match="build failed"):
        authoring_ui.TopologyAuthoringWindow(
            TopologyDocument("warehouse"),
            object(),
            SceneSource("warehouse", tmp_path, {}),
            tmp_path,
        )

    assert destroyed == [True]


def test_window_destroy_clears_lazy_build_callbacks_and_widget_references():
    events = []

    class Frame:
        def __init__(self, name):
            self.name = name

        def set_build_fn(self, callback):
            assert callback is None
            events.append(f"clear:{self.name}")

    class Window:
        def __init__(self):
            self.frame = Frame("window")

        def destroy(self):
            events.append("window:destroy")

    class Overlay:
        def destroy(self):
            events.append("overlay:destroy")

    instance = authoring_ui.TopologyAuthoringWindow.__new__(
        authoring_ui.TopologyAuthoringWindow
    )
    instance._overlay = Overlay()
    instance._frames = [Frame("selection"), Frame("properties")]
    instance.window = Window()
    instance.status = object()
    instance.object_summary = object()
    instance._ui = object()

    instance.destroy()
    instance.destroy()

    assert events == [
        "overlay:destroy",
        "clear:selection",
        "clear:properties",
        "clear:window",
        "window:destroy",
    ]
    assert instance._overlay is None
    assert instance._frames == []
    assert instance.window is None
    assert instance.status is None
    assert instance.object_summary is None
    assert instance._ui is None


def test_viewport_rejects_empty_or_nonfinite_raycast_results():
    forwarded = []
    overlay = viewport.ViewportOverlay.__new__(viewport.ViewportOverlay)
    overlay._on_left_callback = lambda point, prim_path: forwarded.append(
        (point, prim_path)
    )

    overlay._on_left("", (0.0, 0.0, 0.0))
    overlay._on_left("/World/Ground", (math.nan, 0.0, 0.0))
    overlay._on_left("/World/Ground", (math.inf, 0.0, 0.0))
    overlay._on_left("/World/Ground", (1.0, 2.0, 3.0))

    assert forwarded == [((1.0, 2.0, 3.0), "/World/Ground")]


def test_overlay_rebind_removes_old_stage_overlay(monkeypatch):
    class Prim:
        def IsValid(self):
            return True

    class Stage:
        def __init__(self):
            self.removed = []

        def GetPrimAtPath(self, _path):
            return Prim()

        def RemovePrim(self, path):
            self.removed.append(path)

    old_stage = Stage()
    new_stage = Stage()
    active = {"stage": old_stage}
    monkeypatch.setattr(viewport, "_current_stage", lambda: active["stage"])
    overlay = viewport.TopologyOverlay(TopologyDocument("warehouse"))

    active["stage"] = new_stage
    overlay.rebind_stage()

    assert overlay.stage is new_stage
    assert old_stage.removed == [viewport.OVERLAY_ROOT]


def test_viewport_destroy_explicitly_unregisters_and_releases_callbacks():
    events = []

    class Registration:
        def destroy(self):
            events.append("registration")

    class Overlay:
        def destroy(self):
            events.append("overlay")

    instance = viewport.ViewportOverlay.__new__(viewport.ViewportOverlay)
    instance.document = object()
    instance.overlay = Overlay()
    instance._on_left_callback = lambda *_args: None
    instance._on_right_callback = lambda *_args: None
    instance._registration = Registration()

    instance.destroy()
    instance.destroy()

    assert events == ["registration", "overlay"]
    assert instance.document is None
    assert instance._registration is None
    assert instance.overlay is None
    assert instance._on_left_callback is None
    assert instance._on_right_callback is None


def test_restore_point_reattaches_cached_previous_stage():
    old_stage = object()
    new_stage = object()

    class StageId:
        def __init__(self, valid, value=0):
            self.valid = valid
            self.value = value

        def IsValid(self):
            return self.valid

        def ToLongInt(self):
            return self.value

    class GlobalCache:
        def __init__(self):
            self.erased = []

        def GetId(self, stage):
            assert stage is old_stage
            return StageId(True, 41)

        def Erase(self, stage):
            self.erased.append(stage)

    class PrivateCache:
        def __init__(self):
            self.erased = []

        def Find(self, stage_id):
            assert stage_id.ToLongInt() == 101
            return old_stage

        def Erase(self, stage):
            self.erased.append(stage)

    global_cache = GlobalCache()
    private_cache = PrivateCache()

    class Context:
        current = new_stage

        def get_stage(self):
            return self.current

        def attach_stage_with_callback(self, stage_id, callback):
            assert stage_id == 41
            self.current = old_stage
            callback(True, "")
            return True

    context = Context()
    restore_point = authoring_ui._UsdStageRestorePoint(
        context,
        global_cache,
        StageId(True, 41),
        True,
        private_cache,
        StageId(True, 101),
    )

    assert restore_point.restore() == "restored"
    assert context.current is old_stage
    assert global_cache.erased == []
    assert private_cache.erased == [old_stage]
    assert restore_point.retains_global_entry is True
    assert restore_point.release_inactive_global_entry() is False
    context.current = new_stage
    assert restore_point.release_inactive_global_entry() is True
    assert global_cache.erased == [old_stage]


def test_restore_point_keeps_cache_until_async_callback_without_erasing_foreign_entry():
    old_stage = object()
    new_stage = object()

    class StageId:
        def IsValid(self):
            return True

        def ToLongInt(self):
            return 73

    class GlobalCache:
        def GetId(self, stage):
            assert stage is old_stage
            return StageId()

        def Erase(self, _stage):
            raise AssertionError("a pre-existing cache entry must not be erased")

    class PrivateCache:
        def __init__(self):
            self.erased = []

        def Find(self, _stage_id):
            return old_stage

        def Erase(self, stage):
            self.erased.append(stage)

    class Context:
        current = new_stage
        callback = None

        def get_stage(self):
            return self.current

        def attach_stage_with_callback(self, stage_id, callback):
            assert stage_id == 73
            self.callback = callback
            return True

    context = Context()
    completions = []
    private_cache = PrivateCache()
    restore_point = authoring_ui._UsdStageRestorePoint(
        context,
        GlobalCache(),
        StageId(),
        False,
        private_cache,
        StageId(),
    )

    assert (
        restore_point.restore(completions.append)
        == "scheduled"
    )
    assert completions == []
    context.current = old_stage
    context.callback(True, "")
    assert completions == [True]
    assert private_cache.erased == [old_stage]


def test_reload_caches_old_stage_before_load_and_releases_after_commit(
    monkeypatch, tmp_path
):
    events = []
    old_stage, new_stage = object(), object()

    class StageId:
        def __init__(self, valid, value=0):
            self.valid = valid
            self.value = value

        def IsValid(self):
            return self.valid

        def ToLongInt(self):
            return self.value

    class GlobalCache:
        def __init__(self):
            self.stage_id = StageId(False)

        def GetId(self, stage):
            assert stage is old_stage
            return self.stage_id

        def Insert(self, stage):
            assert stage is old_stage
            events.append("cache_insert")
            self.stage_id = StageId(True, 91)
            return self.stage_id

        def Erase(self, stage):
            assert stage is old_stage
            events.append("global_cache_erase")
            self.stage_id = StageId(False)

    class PrivateCache:
        def Insert(self, stage):
            assert stage is old_stage
            events.append("private_cache_insert")
            return StageId(True, 81)

        def Find(self, stage_id):
            assert stage_id.ToLongInt() == 81
            return old_stage

        def Erase(self, stage):
            assert stage is old_stage
            events.append("private_cache_erase")

    class Context:
        current = old_stage

        def get_stage(self):
            return self.current

    context = Context()
    fake_usd = types.ModuleType("omni.usd")
    fake_usd.get_context = lambda: context
    fake_omni = types.ModuleType("omni")
    fake_omni.__path__ = []
    fake_omni.usd = fake_usd
    pxr = types.ModuleType("pxr")
    pxr.Usd = types.SimpleNamespace(StageCache=PrivateCache)
    pxr.UsdUtils = types.SimpleNamespace(
        StageCache=types.SimpleNamespace(Get=lambda: GlobalCache())
    )
    monkeypatch.setitem(sys.modules, "omni", fake_omni)
    monkeypatch.setitem(sys.modules, "omni.usd", fake_usd)
    monkeypatch.setitem(sys.modules, "pxr", pxr)

    source = SceneSource("warehouse", tmp_path, {})

    class Adapter:
        def load_source(self, _root):
            events.append("load_source")
            return source

        def source_digests(self, _source):
            return {}

        def semantic_targets(self, _source):
            return []

        def validate_polyline(self, _polyline, _profile, _source):
            return []

        def load_stage(self, _source):
            assert events[:2] == ["private_cache_insert", "cache_insert"]
            events.append("load_stage")
            context.current = new_stage

    document = TopologyDocument("warehouse", {})
    original_replace = document.replace_content

    def replace_content(other):
        events.append("document_commit")
        return original_replace(other)

    document.replace_content = replace_content
    window = _bare_window(document, Adapter(), source)
    window._overlay = types.SimpleNamespace(
        rebind_stage=lambda: events.append("overlay_rebind")
    )

    window.load_source()

    assert events == [
        "private_cache_insert",
        "cache_insert",
        "load_source",
        "load_stage",
        "overlay_rebind",
        "document_commit",
        "global_cache_erase",
        "private_cache_erase",
    ]
    assert window._stage_ready is True


def test_restore_reinserts_privately_held_stage_after_global_cache_eviction(
    monkeypatch,
):
    events = []

    class StageId:
        def __init__(self, value=None):
            self.value = value

        def IsValid(self):
            return self.value is not None

        def ToLongInt(self):
            assert self.value is not None
            return self.value

    class Stage:
        def __init__(self):
            self.private_holds = 0
            self.global_holds = 0

        @property
        def alive(self):
            return self.private_holds > 0 or self.global_holds > 0

    old_stage = Stage()
    new_stage = Stage()

    class GlobalCache:
        def __init__(self):
            self.stage = old_stage
            self.stage_id = StageId(41)
            old_stage.global_holds = 1

        def GetId(self, stage):
            assert stage is old_stage
            return self.stage_id if self.stage is stage else StageId()

        def Insert(self, stage):
            assert stage is old_stage
            assert stage.alive
            events.append("global_reinsert")
            self.stage = stage
            self.stage_id = StageId(42)
            stage.global_holds += 1
            return self.stage_id

        def Erase(self, stage):
            assert stage is self.stage
            events.append(("global_erase", self.stage_id.ToLongInt()))
            self.stage = None
            self.stage_id = StageId()
            stage.global_holds -= 1

        def evict_original_entry(self):
            assert self.stage_id.ToLongInt() == 41
            self.stage = None
            self.stage_id = StageId()
            old_stage.global_holds -= 1
            events.append("global_original_evicted")

    class PrivateCache:
        def __init__(self):
            self.stage = None
            self.stage_id = StageId()

        def Insert(self, stage):
            self.stage = stage
            self.stage_id = StageId(101)
            stage.private_holds += 1
            events.append("private_insert")
            return self.stage_id

        def Find(self, stage_id):
            assert stage_id.ToLongInt() == self.stage_id.ToLongInt()
            assert self.stage.alive
            return self.stage

        def Erase(self, stage):
            assert stage is self.stage
            events.append("private_erase")
            stage.private_holds -= 1
            self.stage = None
            self.stage_id = StageId()

    global_cache = GlobalCache()

    class Context:
        def __init__(self):
            self.current = old_stage
            self.callback = None

        def get_stage(self):
            return self.current

        def attach_stage_with_callback(self, stage_id, callback):
            assert stage_id == 42
            assert global_cache.stage is old_stage
            assert old_stage.alive
            events.append(("attach", stage_id))
            self.current = old_stage
            self.callback = callback
            return True

    context = Context()
    fake_usd = types.ModuleType("omni.usd")
    fake_usd.get_context = lambda: context
    fake_omni = types.ModuleType("omni")
    fake_omni.__path__ = []
    fake_omni.usd = fake_usd
    pxr = types.ModuleType("pxr")
    pxr.Usd = types.SimpleNamespace(StageCache=PrivateCache)
    pxr.UsdUtils = types.SimpleNamespace(
        StageCache=types.SimpleNamespace(Get=lambda: global_cache)
    )
    monkeypatch.setitem(sys.modules, "omni", fake_omni)
    monkeypatch.setitem(sys.modules, "omni.usd", fake_usd)
    monkeypatch.setitem(sys.modules, "pxr", pxr)

    restore_point = authoring_ui._capture_usd_stage()
    assert restore_point is not None
    assert events == ["private_insert"]

    global_cache.evict_original_entry()
    context.current = new_stage
    assert old_stage.alive is True

    completions = []
    assert restore_point.restore(completions.append) == "scheduled"
    assert events[-2:] == ["global_reinsert", ("attach", 42)]
    assert completions == []

    context.callback(True, "")

    assert completions == [True]
    assert events[-1:] == ["private_erase"]
    assert ("global_erase", 42) not in events
    assert global_cache.stage is old_stage
    assert global_cache.stage_id.ToLongInt() == 42
    assert old_stage.alive is True

    assert restore_point.release_inactive_global_entry() is False
    context.current = new_stage
    assert restore_point.release_inactive_global_entry() is True
    assert events[-1] == ("global_erase", 42)
    assert old_stage.alive is False


def _bare_window(document, adapter, source, semantic_map=None):
    window = authoring_ui.TopologyAuthoringWindow.__new__(
        authoring_ui.TopologyAuthoringWindow
    )
    window.document = document
    window.adapter = adapter
    window.source = source
    window.semantic_map = semantic_map
    window._stage_ready = True
    window._overlay = None
    window._retained_stage_restore_points = []
    window._last_status = None
    window._set_status = lambda text: setattr(window, "_last_status", text)
    window._refresh = lambda: None
    return window


def test_failed_source_reload_preserves_document_source_and_restores_stage(
    monkeypatch, tmp_path
):
    original_source = SceneSource("warehouse", tmp_path, {"old": True})
    document = TopologyDocument("warehouse", {"scene.usd": "a" * 64})
    document.add_node([1.0, 2.0, 0.0], node_id="keep")
    before = document.to_dict()
    old_stage, replacement_stage = object(), object()

    class Context:
        current = old_stage

        def get_stage(self):
            return self.current

    context = Context()

    class FailingAdapter:
        def load_source(self, _root):
            return SceneSource("warehouse", tmp_path, {})

        def source_digests(self, _source):
            return {"new.usd": "b" * 64}

        def semantic_targets(self, _source):
            return []

        def validate_polyline(self, _polyline, _profile, _source):
            return []

        def load_stage(self, _source):
            context.current = replacement_stage
            raise RuntimeError("stage failed")

    restored = []

    class RestorePoint:
        def current_stage_matches(self):
            return context.get_stage() == old_stage

        def restore(self, callback):
            restored.append(True)
            context.current = old_stage
            callback(True)
            return "restored"

        def release(self):
            raise AssertionError("swapped stages must follow the restore path")

    monkeypatch.setattr(authoring_ui, "_capture_usd_stage", RestorePoint)
    window = _bare_window(document, FailingAdapter(), original_source)

    window.load_source()

    assert document.to_dict() == before
    assert window.source is original_source
    assert restored == [True]
    assert window._last_status == "Source reload failed: stage failed; previous stage restored"


def test_in_place_stage_failure_disables_authoring_until_successful_reload(
    monkeypatch, tmp_path
):
    source = SceneSource("warehouse", tmp_path, {})
    document = TopologyDocument("warehouse", {})
    document.begin_node_preview([1.0, 2.0, 0.0])
    stage = object()
    context = types.SimpleNamespace(get_stage=lambda: stage)

    class FailingAdapter:
        def load_source(self, _root):
            return source

        def source_digests(self, _source):
            return {}

        def semantic_targets(self, _source):
            return []

        def validate_polyline(self, _polyline, _profile, _source):
            return []

        def load_stage(self, _source):
            raise RuntimeError("mutated then failed")

    released = []

    class RestorePoint:
        def current_stage_matches(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(authoring_ui, "_capture_usd_stage", RestorePoint)
    window = _bare_window(document, FailingAdapter(), source)

    window.load_source()
    window.confirm_node()

    assert window._stage_ready is False
    assert released == [True]
    assert document.nodes == []
    assert window._last_status.startswith("Authoring is disabled")


def test_invalid_reloaded_source_is_rejected_before_stage_replacement(
    monkeypatch, tmp_path
):
    original_source = SceneSource("warehouse", tmp_path, {})
    document = TopologyDocument("warehouse", {})
    document.add_node([0.0, 0.0, 0.0], node_id="keep")
    before = document.to_dict()
    load_stage_calls = []
    releases = []

    class Adapter:
        def load_source(self, _root):
            return SceneSource("different", tmp_path, {})

        def source_digests(self, _source):
            return {}

        def semantic_targets(self, _source):
            return []

        def load_stage(self, _source):
            load_stage_calls.append(True)

    class RestorePoint:
        def release(self):
            releases.append(True)

    monkeypatch.setattr(authoring_ui, "_capture_usd_stage", RestorePoint)
    window = _bare_window(document, Adapter(), original_source)

    window.load_source()

    assert load_stage_calls == []
    assert releases == [True]
    assert document.to_dict() == before
    assert window.source is original_source
    assert window._stage_ready is True
    assert window._last_status == (
        "Source reload failed: reloaded source scene 'different' differs from "
        "current scene 'warehouse'"
    )


def test_successful_source_reload_reapplies_semantics_and_replaces_history(
    monkeypatch, tmp_path
):
    original_source = SceneSource("warehouse", tmp_path, {})
    document = TopologyDocument("warehouse", {})
    document.add_node([1.0, 2.0, 0.0], node_id="old")
    document.begin_node_preview([3.0, 4.0, 0.0])
    semantic_map = {"targets": ["goal"]}
    adapter = _DigestAdapter(
        SceneSource("warehouse", tmp_path, MappingProxyType({"fresh": True}))
    )
    monkeypatch.setattr(authoring_ui, "_capture_usd_stage", lambda: None)
    window = _bare_window(document, adapter, original_source, semantic_map)
    rebound = []
    window._overlay = types.SimpleNamespace(rebind_stage=lambda: rebound.append(True))

    window.load_source()

    assert document.nodes == []
    assert document.edges == []
    assert document.pending_node is None
    assert document.undo() is False
    assert window.source.payload == {
        "fresh": True,
        "semantic": semantic_map,
    }
    assert adapter.loaded_stage is window.source
    assert rebound == [True]
    assert window._last_status == "Source reloaded; topology reset to empty"


def test_load_draft_replaces_content_and_clears_history(tmp_path):
    class DraftAdapter:
        def source_digests(self, _source):
            return {}

        def semantic_targets(self, _source):
            return []

        def validate_polyline(self, _polyline, _profile, _source):
            return []

    source = SceneSource("warehouse", tmp_path, {})
    document = TopologyDocument("warehouse", {})
    document.add_node([0.0, 0.0, 0.0], node_id="old")
    document.begin_node_preview([1.0, 1.0, 0.0])
    loaded = TopologyDocument("warehouse", {})
    loaded.add_node([2.0, 2.0, 0.0], node_id="loaded")
    draft_path = (
        tmp_path / "authoring" / "navigation" / "ground" / "topology.draft.json"
    )
    save_draft(draft_path, loaded)
    window = _bare_window(document, DraftAdapter(), source)
    window.output_root = tmp_path

    window.load_draft()

    assert [node["id"] for node in document.nodes] == ["loaded"]
    assert document.pending_node is None
    assert document.undo() is False
    assert window._last_status == "Draft loaded"
