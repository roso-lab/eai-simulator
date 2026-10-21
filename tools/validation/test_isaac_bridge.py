from __future__ import annotations

import json
import io
import sys
import time
import types
import urllib.error
from pathlib import Path

import pytest
from urllib.request import Request, urlopen
import threading
from tools.topology_authoring.map_project import MapProject
from tools.topology_authoring.map_server import make_server

from tools.topology_authoring.isaac_bridge import (
    IsaacBridgeError,
    IsaacSelectionBridge,
    IsaacSelectionWindow,
    point_selection_payload,
    prim_selection_payload,
)


def test_optional_isaac_window_uses_ascii_labels(monkeypatch):
    rendered = {"windows": [], "labels": [], "buttons": []}

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Window:
        def __init__(self, title, **_kwargs):
            rendered["windows"].append(title)
            self.frame = Context()

    ui = types.ModuleType("omni.ui")
    ui.Window = Window
    ui.VStack = lambda **_kwargs: Context()
    ui.Label = lambda label: rendered["labels"].append(label)
    ui.Button = lambda label, **_kwargs: rendered["buttons"].append(label)
    omni = sys.modules.get("omni") or types.ModuleType("omni")
    omni.__path__ = []
    omni.ui = ui
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.ui", ui)

    IsaacSelectionWindow(IsaacSelectionBridge()).show()

    assert rendered["windows"] == ["Isaac Semantic Map Bridge"]
    assert "Surface Point Mode" in rendered["buttons"]
    assert "Asset Selection Mode" in rendered["buttons"]
    assert all(value.isascii() for values in rendered.values() for value in values)


class _Layer:
    def __init__(self, path):
        self.realPath = str(path)
        self.identifier = str(path)


class _Stage:
    def __init__(self, path, *, axis="Z", scale=0.01):
        self._metadata = {"upAxis": axis, "metersPerUnit": scale}
        self._layer = _Layer(path)
        self.prims = {}

    def GetMetadata(self, key):
        return self._metadata.get(key)

    def GetRootLayer(self):
        return self._layer

    def GetPrimAtPath(self, path):
        return self.prims.get(path)


class _Prim:
    def __init__(self, path):
        self.path = path

    def IsValid(self):
        return True


class _Vec:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, index):
        return self.values[index]


class _Range:
    def GetMin(self):
        return _Vec((-1.0, 2.0, 3.0))

    def GetMax(self):
        return _Vec((5.0, 8.0, 13.0))


class _Bound:
    def ComputeAlignedBox(self):
        return _Range()

    def GetRange(self):
        raise AssertionError("GetRange omits the world bounding-box transform")


def _install_fake_pxr(monkeypatch):
    pxr = types.ModuleType("pxr")
    usd = types.ModuleType("pxr.Usd")
    usd.TimeCode = types.SimpleNamespace(Default=lambda: object())
    usdgeom = types.ModuleType("pxr.UsdGeom")
    usdgeom.Tokens = types.SimpleNamespace(default_="default")
    usdgeom.GetStageUpAxis = lambda stage: stage.GetMetadata("upAxis")
    usdgeom.GetStageMetersPerUnit = lambda stage: stage.GetMetadata("metersPerUnit")
    usdgeom.BBoxCache = lambda *_args: types.SimpleNamespace(
        ComputeWorldBound=lambda _prim: _Bound())
    pxr.Usd, pxr.UsdGeom = usd, usdgeom
    monkeypatch.setitem(sys.modules, "pxr", pxr)
    monkeypatch.setitem(sys.modules, "pxr.Usd", usd)
    monkeypatch.setitem(sys.modules, "pxr.UsdGeom", usdgeom)


def _install_fake_omni_usd(monkeypatch, context):
    omni = types.ModuleType("omni")
    omni.__path__ = []
    usd = types.ModuleType("omni.usd")
    usd.StageEventType = types.SimpleNamespace(SELECTION_CHANGED=23)
    usd.get_context = lambda: context
    omni.usd = usd
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.usd", usd)


def test_point_selection_converts_usd_units_and_preserves_exact_hit(tmp_path, monkeypatch):
    _install_fake_pxr(monkeypatch)
    stage = _Stage(tmp_path / "scene.usd", scale=0.01)
    stage.prims["/World/Chair"] = _Prim("/World/Chair")
    payload = point_selection_payload(stage, (12, -4, 3), prim_path="/World/Chair")
    assert payload["position_xyz"] == [0.12, -0.04, 0.03]
    assert payload["prim_paths"] == ["/World/Chair"]
    assert payload["up_axis"] == "Z"


def test_y_up_and_stale_source_are_rejected(tmp_path):
    stage = _Stage(tmp_path / "scene.usd", axis="Y")
    with pytest.raises(IsaacBridgeError, match="Z-up"):
        point_selection_payload(stage, (0, 0, 0))
    stage = _Stage(tmp_path / "scene.usd")
    with pytest.raises(IsaacBridgeError, match="does not match"):
        point_selection_payload(stage, (0, 0, 0), source_usd=str(tmp_path / "other.usd"))


def test_prim_selection_uses_world_bbox_and_metre_scale(tmp_path, monkeypatch):
    _install_fake_pxr(monkeypatch)
    stage = _Stage(tmp_path / "scene.usd", scale=0.5)
    stage.prims["/World/Table"] = _Prim("/World/Table")
    payload = prim_selection_payload(stage, ["/World/Table"])
    assert payload["position_xyz"] == [1.0, 2.5, 4.0]
    assert payload["bbox_min_xyz"] == [-0.5, 1.0, 1.5]
    assert payload["bbox_max_xyz"] == [2.5, 4.0, 6.5]


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def test_transport_fetches_context_and_posts_latest_selection_without_blocking():
    requests = []

    def opener(request, timeout):
        requests.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None, timeout))
        if request.get_method() == "GET":
            return _Response({"scene_key": "office", "source_usd": "/tmp/office.usd",
                              "levels": [], "csrf_token": "token"})
        return _Response({"selection": json.loads(request.data)})

    bridge = IsaacSelectionBridge("http://127.0.0.1:9999", "office", opener=opener)
    bridge.start()
    deadline = time.time() + 1.0
    while bridge.status()["state"] != "connected" and time.time() < deadline:
        time.sleep(0.01)
    assert bridge.status()["state"] == "connected"
    bridge._enqueue({"selection_kind": "point", "position_xyz": [1, 2, 3], "prim_paths": [],
                     "source_usd": "/tmp/office.usd", "up_axis": "Z", "timestamp": 1})
    bridge._enqueue({"selection_kind": "point", "position_xyz": [4, 5, 6], "prim_paths": [],
                     "source_usd": "/tmp/office.usd", "up_axis": "Z", "timestamp": 2})
    deadline = time.time() + 1.0
    while not any(row[0] == "POST" for row in requests) and time.time() < deadline:
        time.sleep(0.01)
    bridge.stop()
    posts = [row[2] for row in requests if row[0] == "POST"]
    assert posts
    assert posts[-1]["position_xyz"] == [4, 5, 6]
    assert posts[-1]["scene_key"] == "office"
    assert posts[-1]["csrf_token"] == "token"
    assert posts[-1]["session_id"] == bridge.session_id
    assert posts[-1]["sequence"] == 2


def test_acknowledged_cursor_waits_for_post_and_matches_current_context():
    post_started = threading.Event()
    release_post = threading.Event()
    post_count = 0

    def opener(request, timeout):
        nonlocal post_count
        if request.get_method() == "GET":
            return _Response({"scene_key": "office", "source_usd": "/tmp/office.usd",
                              "csrf_token": "token", "scene_generation": 1,
                              "context_generation": 1, "active_domain": "aerial",
                              "active_layer": "aerial"})
        post_count += 1
        body = json.loads(request.data)
        if post_count == 1:
            post_started.set()
            release_post.wait(1.0)
        return _Response({"selection": body})

    bridge = IsaacSelectionBridge("http://127.0.0.1:9999", "office", opener=opener)
    bridge.start()
    try:
        deadline = time.time() + 1.0
        while bridge.status()["state"] != "connected" and time.time() < deadline:
            time.sleep(0.01)
        bridge._enqueue({"selection_kind": "prim", "prim_paths": ["/World/Desk"],
                         "position_xyz": [1, 2, 3], "source_usd": "/tmp/office.usd",
                         "up_axis": "Z", "timestamp": 1})
        assert post_started.wait(1.0)
        assert bridge.status()["pending"]
        assert bridge.acknowledged_selection_cursor() is None
        release_post.set()
        deadline = time.time() + 1.0
        cursor = None
        while cursor is None and time.time() < deadline:
            cursor = bridge.acknowledged_selection_cursor()
            time.sleep(0.01)
        assert cursor["sequence"] == 1
        assert cursor["context_generation"] == 1

        bridge.update_context({"scene_key": "office", "scene_generation": 1,
                               "context_generation": 2, "active_domain": "aerial",
                               "active_layer": "aerial"})
        assert bridge.acknowledged_selection_cursor() is None
        bridge._enqueue({"selection_kind": "prim", "prim_paths": ["/World/Desk"],
                         "position_xyz": [1, 2, 3], "source_usd": "/tmp/office.usd",
                         "up_axis": "Z", "timestamp": 2})
        assert bridge.acknowledged_selection_cursor() is None
        deadline = time.time() + 1.0
        cursor = None
        while cursor is None and time.time() < deadline:
            cursor = bridge.acknowledged_selection_cursor()
            time.sleep(0.01)
        assert cursor["sequence"] == 2
        assert cursor["context_generation"] == 2
    finally:
        release_post.set()
        bridge.stop()


def test_stale_selection_409_is_discarded_and_fresh_identity_can_publish():
    requests = []
    post_count = 0

    def opener(request, timeout):
        nonlocal post_count
        body = json.loads(request.data) if request.data else None
        requests.append((request.get_method(), body, timeout))
        if request.get_method() == "GET":
            return _Response({"scene_key": "office", "source_usd": "/tmp/office.usd",
                              "levels": [], "csrf_token": "token",
                              "scene_generation": 1, "context_generation": 1,
                              "active_domain": "ground", "active_layer": "ground"})
        post_count += 1
        if post_count == 1:
            raise urllib.error.HTTPError(
                request.full_url, 409, "stale selection", {},
                io.BytesIO(json.dumps({"error": "stale context"}).encode()))
        return _Response({"selection": body})

    bridge = IsaacSelectionBridge("http://127.0.0.1:9999", "office", opener=opener)
    bridge.start()
    deadline = time.time() + 1.0
    while bridge.status()["state"] != "connected" and time.time() < deadline:
        time.sleep(0.01)
    assert bridge.status()["state"] == "connected"
    bridge._enqueue({"selection_kind": "point", "position_xyz": [1, 2, 3],
                     "prim_paths": [], "source_usd": "/tmp/office.usd", "up_axis": "Z"})
    deadline = time.time() + 1.0
    while post_count < 1 and time.time() < deadline:
        time.sleep(0.01)
    bridge.update_context({"scene_key": "office", "scene_generation": 1,
                           "context_generation": 2, "active_domain": "aerial",
                           "active_layer": "aerial"})
    bridge._enqueue({"selection_kind": "point", "position_xyz": [4, 5, 6],
                     "prim_paths": [], "source_usd": "/tmp/office.usd", "up_axis": "Z"})
    deadline = time.time() + 1.0
    while post_count < 2 and time.time() < deadline:
        time.sleep(0.01)
    status = bridge.status()
    bridge.stop()
    posts = [body for method, body, _timeout in requests if method == "POST"]
    assert len(posts) == 2
    assert posts[0]["position_xyz"] == [1, 2, 3]
    assert posts[1]["position_xyz"] == [4, 5, 6]
    assert posts[1]["context_generation"] == 2
    assert status["state"] == "connected"
    assert status["last_error"] is None


def test_context_scene_mismatch_does_not_silently_remap():
    def opener(request, timeout):
        return _Response({"scene_key": "warehouse", "source_usd": None,
                          "levels": [], "csrf_token": "token"})

    bridge = IsaacSelectionBridge("http://127.0.0.1:9999", "office", opener=opener)
    bridge.start()
    deadline = time.time() + 1.0
    while bridge.status()["state"] != "error" and time.time() < deadline:
        time.sleep(0.01)
    assert bridge.status()["state"] == "error"
    assert "scene_key mismatch" in bridge.status()["last_error"]
    bridge.stop()


def test_bind_selection_uses_stage_selection_event_and_releases_subscription(monkeypatch):
    callbacks = []
    subscription = object()

    class Stream:
        def create_subscription_to_pop(self, callback, name=None):
            callbacks.append((callback, name))
            return subscription

    selection = types.SimpleNamespace(get_selected_prim_paths=lambda: ["/World/Desk"])
    stage = object()
    context = types.SimpleNamespace(
        get_selection=lambda: selection,
        get_stage=lambda: stage,
        get_stage_event_stream=lambda: Stream(),
    )
    _install_fake_omni_usd(monkeypatch, context)
    bridge = IsaacSelectionBridge()
    sent = []
    monkeypatch.setattr(bridge, "send_current_selection", lambda: sent.append(True))

    bridge.bind_selection(context=context)
    assert bridge._selection_subscription is subscription
    assert callbacks[0][1] == "eai.semantic_map.selection_bridge"
    assert len(sent) == 1
    callbacks[0][0](types.SimpleNamespace(type=99))
    assert len(sent) == 1
    callbacks[0][0](types.SimpleNamespace(type=23))
    assert len(sent) == 2

    bridge.unbind_selection()
    assert bridge._selection_subscription is None
    assert bridge._selection is None


def test_map_server_selection_preview_is_ephemeral_and_validates_sequence(tmp_path):
    project = MapProject.create_blank(tmp_path / "map", "office", [-1, -1, 1, 1], .5)
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        import json
        context = json.load(urlopen(url + "/api/isaac/context"))
        payload = {"csrf_token": context["csrf_token"], "scene_key": "office",
                   "selection_kind": "point", "prim_paths": [], "position_xyz": [1, 2, 3],
                   "timestamp": 0, "session_id": "test", "sequence": 1, "up_axis": "Z"}
        response = urlopen(Request(url + "/api/isaac/selection", json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
        assert json.load(response)["selection"]["level_id"] == "ground"
        assert json.load(urlopen(url + "/api/isaac/selection"))["selection"]["position_xyz"] == [1., 2., 3.]
        with pytest.raises(Exception):
            urlopen(Request(url + "/api/isaac/selection", json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
        payload["selection_kind"] = "clear"
        with pytest.raises(Exception):
            urlopen(Request(url + "/api/isaac/selection", json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
        payload["sequence"] = 2
        urlopen(Request(url + "/api/isaac/selection", json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
        assert json.load(urlopen(url + "/api/isaac/selection"))["selection"] is None
        payload["selection_kind"] = "point"
        with pytest.raises(Exception):
            urlopen(Request(url + "/api/isaac/selection", json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_bound_context_switch_rejects_foreign_stage_and_clears_preview(tmp_path, monkeypatch):
    _install_fake_pxr(monkeypatch)
    stage_a = _Stage(tmp_path / "a.usd")
    stage_b = _Stage(tmp_path / "b.usd")
    for stage in (stage_a, stage_b):
        stage.prims["/World/Desk"] = _Prim("/World/Desk")
    selection = types.SimpleNamespace(get_selected_prim_paths=lambda: ["/World/Desk"])
    current = [stage_a]
    context = types.SimpleNamespace(get_stage=lambda: current[0], get_selection=lambda: selection)
    bridge = IsaacSelectionBridge()
    bridge.source_usd = str(tmp_path / "a.usd")
    bridge._selection = selection
    bridge._selection_stage = stage_a
    bridge._selection_context = context
    sent = []
    monkeypatch.setattr(bridge, "_enqueue", lambda value: sent.append(value) or value)
    assert bridge.send_current_selection()["selection_kind"] == "prim"
    current[0] = stage_b
    with pytest.raises(IsaacBridgeError, match="does not match"):
        bridge.send_current_selection()
    assert sent[-1]["selection_kind"] == "clear"
    bridge._point_enabled = True
    bridge._on_point_hit("/World/Desk", (1, 2, 3))
    assert sent[-1]["selection_kind"] == "clear"
    assert "does not match" in bridge.status()["last_error"]
    current[0] = None
    assert bridge.send_current_selection()["selection_kind"] == "clear"


def test_native_surface_hits_on_same_asset_preserve_xyz_and_ignore_prim_events(tmp_path, monkeypatch):
    _install_fake_pxr(monkeypatch)
    stage = _Stage(tmp_path / "stairs.usda", scale=0.01)
    stage.prims["/World/Stairs"] = _Prim("/World/Stairs")
    bridge = IsaacSelectionBridge()
    bridge.source_usd = str(tmp_path / "stairs.usda")
    bridge._selection_context = types.SimpleNamespace(get_stage=lambda: stage)
    bridge._selection_event_type = 7
    bridge._point_enabled = True
    sent = []
    monkeypatch.setattr(bridge, "_enqueue", lambda value: sent.append(value) or value)
    # Native Kit callback supplies the path FIRST, including for the same prim.
    for point in ((100, 200, 0), (100, 500, 300)):
        bridge._on_point_hit("/World/Stairs", _Vec(point))
        bridge._on_stage_event(types.SimpleNamespace(type=7))
    assert [item["position_xyz"] for item in sent] == [[1., 2., 0.], [1., 5., 3.]]
    assert all(item["selection_kind"] == "point" for item in sent)
    bridge._on_point_hit("", (0, 0, 0))
    bridge._on_point_hit("/World/Stairs", None)
    assert len(sent) == 2
    bridge._point_enabled = False
    bridge._on_point_hit("/World/Stairs", (5, 6, 7))
    assert len(sent) == 2


def test_topology_mode_blocks_native_asset_selection_and_invalidates_cursor(monkeypatch):
    bridge = IsaacSelectionBridge()
    sent = []
    monkeypatch.setattr(bridge, "send_clear", lambda: sent.append("clear") or {})
    bridge._last_acknowledged_selection = {"selection_kind": "prim", "prim_paths": ["/World/Desk"]}

    assert bridge.set_asset_selection_enabled(False) is False
    assert bridge._last_acknowledged_selection is None
    assert sent == ["clear"]

    # A late Kit selection event is consumed while topology editing is active;
    # it must not publish a scene object to the binding endpoint.
    bridge._selection_event_type = 7
    bridge._asset_selection_enabled = False
    monkeypatch.setattr(bridge, "send_current_selection", lambda: sent.append("prim"))
    bridge._on_stage_event(types.SimpleNamespace(type=7))
    assert sent == ["clear"]

    assert bridge.set_asset_selection_enabled(True) is True
