"""Pure diagnostics checks for approach and native attachment phase boundaries."""

from copy import deepcopy
from types import ModuleType, SimpleNamespace
import sys

import pytest


pytest.importorskip("pxr.Usd")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from demo.benchmark import goal_attachment_probe
from demo.benchmark import goal_native_attachment as attachment
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.goal_house_episode import HouseNativeGoalBackend


def _task(*, paired=False, mode_id=None):
    if paired:
        return {
            "task_id": "carry-crate",
            "skill": "cooperative_carry",
            "assigned_agents": ["warehouse_ground_1", "warehouse_ground_2"],
            "arguments": {
                "object_id": "order_crate_01",
                "handling_mode_id": mode_id or "paired_support",
                "carrier_bindings": [
                    {"agent_id": "warehouse_ground_1", "point_id": "support_left"},
                    {"agent_id": "warehouse_ground_2", "point_id": "support_right"},
                ],
                "destination_region_id": "dock_buffer",
            },
        }
    return {
        "task_id": "pick-carton",
        "skill": "pick",
        "assigned_agents": ["warehouse_ground_1"],
        "arguments": {
            "object_id": "order_carton_01",
            "pickup_region_id": "rack_aisle_a",
            "handling_mode_id": mode_id or "single_grip",
            "carrier_bindings": [
                {"agent_id": "warehouse_ground_1", "point_id": "center_grip"},
            ],
        },
    }


@pytest.fixture
def native_scene():
    public = build_goal_task_spec("warehouse_nvd")
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    samples = {}
    bindings = {"objects": {}, "members": {}}

    def rigid(path, position, mass):
        root = UsdGeom.Xform.Define(stage, path)
        root.AddTranslateOp().Set(Gf.Vec3d(100, 200, 300))
        api = UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
        api.CreateRigidBodyEnabledAttr().Set(True)
        api.CreateKinematicEnabledAttr().Set(False)
        UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr().Set(mass)
        UsdPhysics.CollisionAPI.Apply(root.GetPrim()).CreateCollisionEnabledAttr().Set(True)
        samples[path] = {
            "source": "physx",
            "body_path": path,
            "active": True,
            "physics_step": 10,
            "position_m": list(position),
            "rotation_wxyz": [1, 0, 0, 0],
            "mass_kg": mass,
        }

    for obj in public["public_world"]["objects"]:
        path = "/World/Objects/" + obj["id"]
        rigid(path, [0, 0, 0.3], obj["physical_properties"]["mass_kg"])
        bindings["objects"][obj["id"]] = {"root_path": path, "body_path": path}
        for point in obj["physical_properties"]["grasp_points"]:
            fixed = UsdGeom.Xform.Define(stage, path + "/GraspPoints/" + point["id"])
            fixed.AddTranslateOp().Set(Gf.Vec3d(*point["position_m"]))
            fixed.GetPrim().SetCustomDataByKey("goal_interface", point["interface"])
    for index, robot in enumerate(public["candidate_robot_pool"]):
        root_path = "/World/Robots/" + robot["id"]
        UsdGeom.Xform.Define(stage, root_path)
        body_path = root_path + "/base"
        rigid(body_path, [0, -0.3 if index % 2 else 0.3, 0.55], 10)
        bindings["members"][robot["id"]] = {
            "root_path": root_path,
            "body_path": body_path,
        }
    deployment = {
        "robot_instances": [
            {"public_id": robot["id"], "runtime_id": robot["id"] + "_runtime"}
            for robot in public["candidate_robot_pool"]
        ]
    }
    return {
        "public": public,
        "stage": stage,
        "samples": samples,
        "bindings": bindings,
        "deployment": deployment,
        "active": {robot["id"] for robot in public["candidate_robot_pool"]},
    }


def _snapshot(data):
    return data["stage"].GetRootLayer().ExportToString()


def _backend(data):
    session = SimpleNamespace(base_env=SimpleNamespace(scene=object()))
    return HouseNativeGoalBackend(
        data["public"], session=session, deployment=data["deployment"])


def _install_runtime_fakes(monkeypatch, data, bodies):
    usd = ModuleType("omni.usd")
    usd.get_context = lambda: SimpleNamespace(get_stage=lambda: data["stage"])
    omni = ModuleType("omni")
    omni.__path__ = []
    omni.usd = usd
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    monkeypatch.setattr(goal_attachment_probe, "LiveBodies", lambda _scene, _deployment: bodies)


class _Bodies:
    def __init__(self, data):
        self.bindings = data["bindings"]
        self.samples = data["samples"]

    def __call__(self, path):
        return deepcopy(self.samples[path])


@pytest.mark.parametrize("paired", [False, True], ids=["pick", "cooperative_carry"])
def test_approach_failure_blocks_prepare_and_commit_for_all_manipulation_modes(
        native_scene, monkeypatch, paired):
    backend = _backend(native_scene)
    bodies = _Bodies(native_scene)
    _install_runtime_fakes(monkeypatch, native_scene, bodies)
    object_id = _task(paired=paired)["arguments"]["object_id"]
    object_path = native_scene["bindings"]["objects"][object_id]["body_path"]
    bodies.samples[object_path]["position_m"] = [0, 0, 0.3]
    runtime_ids = {
        row["public_id"]: row["runtime_id"] for row in native_scene["deployment"]["robot_instances"]
    }
    roots = {
        runtime_ids["warehouse_ground_1"]: [3.0, 0.0, 0.55, 1, 0, 0, 0],
        runtime_ids["warehouse_ground_2"]: [4.0, 0.0, 0.55, 1, 0, 0, 0],
    }
    monkeypatch.setattr(
        "demo.benchmark.goal_house_episode._root_state",
        lambda _session, runtime_id: list(roots[runtime_id]),
    )

    approached = []

    def fail_approach(runtime, goals, *, arrived, label, allow_reverse):
        approached.append((tuple(runtime), label, allow_reverse))
        backend._last_navigation_diagnostics = {
            "status": "failed",
            "agents": {
                runtime_id: {
                    "final_root_pose_m": list(roots[runtime_id][:3]),
                    "reverse_applied": False,
                    "stall_sign_flip_applied": False,
                }
                for runtime_id in runtime
            },
        }
        assert not arrived({runtime_id: roots[runtime_id] for runtime_id in runtime})
        return "failed", f"navigation_target_not_reached:{label}:3.25m"

    calls = []
    monkeypatch.setattr(backend, "_follow_ground", fail_approach)
    monkeypatch.setattr(attachment, "prepare_attachment", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(attachment, "commit_attachment", lambda *args, **kwargs: calls.append("commit"))

    status, reason = backend._manipulate(_task(paired=paired))

    assert status == "failed"
    assert reason.startswith(f"approach_object_not_reached:{object_id}:")
    assert len(approached) == 1
    assert len(approached[0][0]) == (2 if paired else 1)
    assert approached[0][2] is False
    assert calls == []
    assert backend._last_execution_diagnostics["failure_stage"] == "approach_object"
    assert backend._last_execution_diagnostics["reach_check_entered"] is False


def test_approach_reports_dynamic_object_center_without_changing_cached_gate_decision(
        native_scene, monkeypatch):
    backend = _backend(native_scene)
    runtime_id = next(
        row["runtime_id"] for row in native_scene["deployment"]["robot_instances"]
        if row["public_id"] == "warehouse_ground_1")
    monkeypatch.setattr(
        "demo.benchmark.goal_house_episode._root_state",
        lambda _session, _runtime_id: [4.0, 0.0, 0.55, 1, 0, 0, 0],
    )

    class MovingObjectBodies:
        bindings = {
            "objects": {"order_carton_01": {"body_path": "/World/Objects/order_carton_01"}}
        }

        def __init__(self):
            self.calls = 0

        def __call__(self, _path):
            self.calls += 1
            center = [0.0, 0.0, 0.3] if self.calls == 1 else [3.0, 0.0, 0.3]
            return {"position_m": center}

    bodies = MovingObjectBodies()

    def finish_at_cached_gate(runtime, goals, *, arrived, label, allow_reverse):
        del goals, label
        final = [1.0, 0.0, 0.55, 1, 0, 0, 0]
        assert runtime == [runtime_id]
        assert allow_reverse is False
        assert arrived({runtime_id: final}) is True
        backend._last_navigation_diagnostics = {
            "status": "succeeded",
            "agents": {
                runtime_id: {
                    "final_root_pose_m": final[:3],
                    "reverse_applied": False,
                    "stall_sign_flip_applied": False,
                }
            },
        }
        return "succeeded", None

    monkeypatch.setattr(backend, "_follow_ground", finish_at_cached_gate)

    status, reason = backend._approach_object(
        "order_carton_01", ["warehouse_ground_1"], bodies)

    diagnostics = backend._last_execution_diagnostics
    row = diagnostics["carriers"][0]
    assert (status, reason) == ("succeeded", None)
    assert diagnostics["object_center_m"] == [0.0, 0.0]
    assert diagnostics["final_object_center_m"] == [3.0, 0.0]
    assert diagnostics["object_center_displacement_xy_m"] == pytest.approx(3.0)
    assert diagnostics["approach_gate_passed"] is True
    assert diagnostics["all_carriers_within_gate_at_final_sample"] is True
    assert diagnostics["failure_agent_ids"] == []
    assert row["distance_to_gate_center_m"] == pytest.approx(1.0)
    assert row["distance_to_object_center_m"] == pytest.approx(2.0)
    assert row["within_approach_gate"] is True
    assert row["within_live_center_radius"] is False


def test_early_prepare_error_does_not_claim_reach_check(native_scene, monkeypatch):
    backend = _backend(native_scene)
    bodies = _Bodies(native_scene)
    _install_runtime_fakes(monkeypatch, native_scene, bodies)
    monkeypatch.setattr(backend, "_approach_object", lambda *args: ("succeeded", None))
    commits = []
    monkeypatch.setattr(attachment, "commit_attachment", lambda *args, **kwargs: commits.append(True))
    before = _snapshot(native_scene)

    status, reason = backend._manipulate(_task(mode_id="unknown_mode"))

    diagnostics = backend._last_execution_diagnostics
    assert status == "failed"
    assert reason == "manipulation_rejected:ValueError:unknown public handling mode"
    assert diagnostics["failure_stage"] == "prepare_attachment"
    assert diagnostics["failure_reason_raw"] == "unknown public handling mode"
    assert diagnostics["reach_check_entered"] is False
    assert diagnostics["reach_checks"] == {
        "prepare_attachment": [], "commit_attachment": []}
    assert commits == []
    assert _snapshot(native_scene) == before


def test_prepare_reach_failure_records_rejecting_member_and_raw_reason(
        native_scene, monkeypatch):
    backend = _backend(native_scene)
    bodies = _Bodies(native_scene)
    _install_runtime_fakes(monkeypatch, native_scene, bodies)
    monkeypatch.setattr(backend, "_approach_object", lambda *args: ("succeeded", None))
    member_path = native_scene["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    bodies.samples[member_path]["position_m"] = [5.0, 0.0, 0.55]
    commits = []
    monkeypatch.setattr(attachment, "commit_attachment", lambda *args, **kwargs: commits.append(True))
    before = _snapshot(native_scene)

    status, reason = backend._manipulate(_task())

    diagnostics = backend._last_execution_diagnostics
    checks = diagnostics["reach_checks"]
    assert status == "failed"
    assert reason.endswith("current PhysX root-to-fixed-point distance exceeds reach")
    assert diagnostics["failure_stage"] == "prepare_attachment"
    assert diagnostics["failure_reason_raw"] == (
        "current PhysX root-to-fixed-point distance exceeds reach")
    assert diagnostics["reach_check_entered"] is True
    assert checks["commit_attachment"] == []
    assert len(checks["prepare_attachment"]) == 1
    measured = checks["prepare_attachment"][0]
    assert measured["agent_id"] == "warehouse_ground_1"
    assert measured["root_position_m"] == [5.0, 0.0, 0.55]
    assert measured["fixed_point_world_m"] == pytest.approx([0.0, 0.0, 0.45])
    assert measured["distance_3d_m"] > measured["reach_m"]
    assert measured["within_reach"] is False
    assert commits == []
    assert _snapshot(native_scene) == before


def test_commit_reach_failure_is_attributed_after_successful_prepare(
        native_scene, monkeypatch):
    backend = _backend(native_scene)
    member_path = native_scene["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    object_paths = {
        row["body_path"] for row in native_scene["bindings"]["objects"].values()
    }

    class MovingBodies(_Bodies):
        def __init__(self, data):
            super().__init__(data)
            self.phase = 0

        def __call__(self, path):
            if path in object_paths:
                self.phase += 1
            sample = deepcopy(self.samples[path])
            sample["physics_step"] = 9 + self.phase
            if path == member_path and self.phase == 2:
                sample["position_m"] = [5.0, 0.0, 0.55]
            return sample

    bodies = MovingBodies(native_scene)
    _install_runtime_fakes(monkeypatch, native_scene, bodies)
    monkeypatch.setattr(backend, "_approach_object", lambda *args: ("succeeded", None))
    before = _snapshot(native_scene)

    status, reason = backend._manipulate(_task())

    diagnostics = backend._last_execution_diagnostics
    prepare_checks = diagnostics["reach_checks"]["prepare_attachment"]
    commit_checks = diagnostics["reach_checks"]["commit_attachment"]
    assert status == "failed"
    assert reason.endswith("current PhysX root-to-fixed-point distance exceeds reach")
    assert diagnostics["failure_stage"] == "commit_attachment"
    assert diagnostics["reach_check_entered"] is True
    assert len(prepare_checks) == len(commit_checks) == 1
    assert prepare_checks[0]["within_reach"] is True
    assert prepare_checks[0]["physics_step"] == 10
    assert commit_checks[0]["within_reach"] is False
    assert commit_checks[0]["physics_step"] == 11
    assert diagnostics["failure_reason_raw"] == (
        "current PhysX root-to-fixed-point distance exceeds reach")
    assert _snapshot(native_scene) == before


def test_reach_diagnostics_are_observational_and_stop_at_first_rejected_slot(native_scene):
    member_path = native_scene["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    native_scene["samples"][member_path]["position_m"] = [5.0, 0.0, 0.55]
    carriers = {
        "warehouse_ground_1": "support_left",
        "warehouse_ground_2": "support_right",
    }
    before = _snapshot(native_scene)
    outcomes = []
    diagnostics = []
    for sink in (None, diagnostics):
        with pytest.raises(ValueError) as caught:
            attachment.prepare_attachment(
                native_scene["stage"], native_scene["public"],
                object_id="order_crate_01", mode_id="paired_support",
                carriers=carriers, active_member_ids=native_scene["active"],
                body_bindings=native_scene["bindings"],
                pose_provider=lambda path: deepcopy(native_scene["samples"][path]),
                reach_diagnostics=sink,
            )
        outcomes.append(str(caught.value))
        assert _snapshot(native_scene) == before

    assert outcomes == [
        "current PhysX root-to-fixed-point distance exceeds reach",
        "current PhysX root-to-fixed-point distance exceeds reach",
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0]["agent_id"] == "warehouse_ground_1"
    assert diagnostics[0]["point_id"] == "support_left"
    assert diagnostics[0]["within_reach"] is False
    assert all(row["agent_id"] != "warehouse_ground_2" for row in diagnostics)
