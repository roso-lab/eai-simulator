from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest


ORSUS_SOURCE = Path("source/EAI_assets/EAI_assets/sensor/high_sensor/orsus.py")


def _load_functions(*names: str, globals_: dict | None = None) -> dict:
    tree = ast.parse(ORSUS_SOURCE.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(globals_ or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ORSUS_SOURCE), "exec"), namespace)
    return namespace


class _Path(str):
    pass


class _Relationship:
    def __init__(self, result=True):
        self.result = result
        self.targets = None

    def SetTargets(self, targets):
        self.targets = targets
        return self.result


class _Prim:
    def __init__(self, relationship=None, valid=True):
        self.relationship = relationship
        self.valid = valid

    def IsValid(self):
        return self.valid

    def GetRelationship(self, _name):
        return self.relationship

    def CreateRelationship(self, _name):
        self.relationship = _Relationship()
        return self.relationship


class _Stage:
    def __init__(self, prims=None):
        self.prims = dict(prims or {})
        self.removed = []

    def GetPrimAtPath(self, path):
        return self.prims.get(path, _Prim(valid=False))

    def RemovePrim(self, path):
        self.removed.append(path)
        self.prims.pop(path, None)


def _bind(stage, graph="/Graph", chassis="/Robot"):
    return _load_functions(
        "_bind_orsus_odometry_chassis",
        globals_={"Sdf": types.SimpleNamespace(Path=_Path), "RuntimeError": RuntimeError},
    )["_bind_orsus_odometry_chassis"](stage, graph, chassis)


def test_bind_sets_existing_relationship_target():
    relationship = _Relationship()
    stage = _Stage({"/Graph/isaac_compute_odometry_node": _Prim(relationship)})

    _bind(stage)

    assert relationship.targets == [_Path("/Robot")]


def test_bind_creates_missing_relationship():
    prim = _Prim()
    stage = _Stage({"/Graph/isaac_compute_odometry_node": prim})

    _bind(stage)

    assert prim.relationship.targets == [_Path("/Robot")]


def test_bind_rejects_missing_node_and_failed_target_write():
    with pytest.raises(RuntimeError, match="node is missing"):
        _bind(_Stage())

    relationship = _Relationship(result=False)
    stage = _Stage({"/Graph/isaac_compute_odometry_node": _Prim(relationship)})
    with pytest.raises(RuntimeError, match="Failed to bind"):
        _bind(stage)


def test_setup_creates_lidar_without_odometry_graph(monkeypatch):
    graph_path = "/Graph"
    lidar_path = "/Robot/Lidar"
    stage = _Stage({graph_path: _Prim(), lidar_path: _Prim()})
    detached = []
    destroyed = []

    class Writer:
        def detach(self):
            detached.append(True)

    class Controller:
        class Keys:
            CREATE_NODES = "create"
            SET_VALUES = "values"
            CONNECT = "connect"

        @staticmethod
        def edit(*_args):
            return None

    og = types.SimpleNamespace(
        Controller=Controller,
        GraphPipelineStage=types.SimpleNamespace(GRAPH_PIPELINE_STAGE_SIMULATION=1),
    )
    graph_module = types.ModuleType("omni.graph")
    core_module = types.ModuleType("omni.graph.core")
    core_module.Controller = Controller
    core_module.GraphPipelineStage = og.GraphPipelineStage
    omni_module = types.ModuleType("omni")
    omni_module.graph = graph_module
    graph_module.core = core_module
    monkeypatch.setitem(sys.modules, "omni", omni_module)
    monkeypatch.setitem(sys.modules, "omni.graph", graph_module)
    monkeypatch.setitem(sys.modules, "omni.graph.core", core_module)

    lidar_module = types.ModuleType("EAI_assets.sensor.low_sensor.ros_lidar")
    lidar_module._destroy_rtx_lidar_render_product = destroyed.append
    monkeypatch.setitem(sys.modules, "EAI_assets", types.ModuleType("EAI_assets"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor", types.ModuleType("EAI_assets.sensor"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor.low_sensor", types.ModuleType("EAI_assets.sensor.low_sensor"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor.low_sensor.ros_lidar", lidar_module)

    requests = {graph_path: (lidar_path, "/Robot", "/robot")}
    resources = {}
    namespace = _load_functions(
        "_rollback_failed_orsus_ros_graph",
        "setup_pending_orsus_ros_graphs",
        globals_={
            "_orsus_ros_graph_requests": requests,
            "_orsus_ros_resources": resources,
            "_create_orsus_rtx_lidar_publisher": lambda *_args: ("/Render", Writer()),
            "_bind_orsus_odometry_chassis": lambda *_args: (_ for _ in ()).throw(RuntimeError("bind failed")),
            "omni": types.SimpleNamespace(usd=types.SimpleNamespace(get_context=lambda: types.SimpleNamespace(get_stage=lambda: stage))),
        },
    )

    assert namespace["setup_pending_orsus_ros_graphs"]() == 1

    assert detached == []
    assert destroyed == []
    assert stage.removed == []
    assert requests == {}
    assert resources == {graph_path: (lidar_path, "/Render", resources[graph_path][2])}
    edit_payload = ORSUS_SOURCE.read_text(encoding="utf-8")
    setup_source = edit_payload.split("def setup_pending_orsus_ros_graphs", 1)[1].split(
        "def close_orsus_ros_resources", 1
    )[0]
    assert "ROS2PublishOdometry" not in setup_source


def test_rollback_continues_when_cleanup_steps_raise(monkeypatch):
    calls = []

    class Writer:
        def detach(self):
            calls.append("detach")
            raise RuntimeError("detach failed")

    class Stage(_Stage):
        def RemovePrim(self, path):
            calls.append(path)
            if path == "/Graph":
                raise RuntimeError("graph removal failed")
            return super().RemovePrim(path)

    lidar_module = types.ModuleType("EAI_assets.sensor.low_sensor.ros_lidar")

    def destroy(path):
        calls.append(path)
        raise RuntimeError("destroy failed")

    lidar_module._destroy_rtx_lidar_render_product = destroy
    monkeypatch.setitem(sys.modules, "EAI_assets", types.ModuleType("EAI_assets"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor", types.ModuleType("EAI_assets.sensor"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor.low_sensor", types.ModuleType("EAI_assets.sensor.low_sensor"))
    monkeypatch.setitem(sys.modules, "EAI_assets.sensor.low_sensor.ros_lidar", lidar_module)
    stage = Stage({"/Graph": _Prim(), "/Lidar": _Prim()})
    rollback = _load_functions("_rollback_failed_orsus_ros_graph")["_rollback_failed_orsus_ros_graph"]

    rollback(stage, "/Graph", "/Lidar", "/Render", Writer())

    assert calls == ["detach", "/Render", "/Graph", "/Lidar"]
    assert "/Lidar" in stage.removed
