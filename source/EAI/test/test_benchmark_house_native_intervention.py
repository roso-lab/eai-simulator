"""Offline Modern House intervention/probe tests; no Isaac/ROS launch.

The probe host is a synthetic USD composition plus injected PhysX I/O,
mirroring the standalone-pxr harness used by the demo-native adapter tests.
Planning tests run against the real provider occupancy map. None of these
tests start a simulator, download assets, or claim business execution.
"""

import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

from pxr import Gf, Usd, UsdGeom, UsdPhysics
import pytest

from demo.benchmark.reference_domain_providers import ModernHousePathReferenceProvider
from demo.modern_house_benchmark import intervention_plan as plan
from demo.modern_house_benchmark import native_adapters as native
from EAI.scenario_spec.geometry import provider_map


class Tensor:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class Scene(dict):
    pass


class Query:
    occluder = None
    missing_target = False
    obstacle = None
    self_hit = None

    def raycast_all(self, origin, direction, distance, callback, both):
        if self.self_hit:
            callback({"collision": self.self_hit, "distance": .001})
        if not self.missing_target:
            callback({"collision": self.occluder or self.object_path + "/Geometry/Mesh",
                      "distance": distance * .9})
        return bool(self.self_hit or not self.missing_target)

    def overlap_box(self, *args):
        return 0

    def sweep_box_all(self, half, origin, rotation, direction, distance, callback, both):
        if self.obstacle:
            callback({"collision": self.obstacle, "rigid_body": self.obstacle, "distance": distance / 2})
            return True
        return False

    def raycast_closest(self, origin, direction, distance):
        if direction == (0., 0., -1.):
            return {"hit": True, "collision": "/Floor", "position": [*origin[:2], .25],
                    "normal": [0., 0., 1.], "distance": .05}
        return {"hit": not self.missing_target, "collision": self.occluder or self.object_path + "/Geometry/Mesh",
                "distance": distance * .9}


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "validate_goal_deployment", lambda _: None)
    source = tmp_path / "Assets" / "books.usda"
    source.parent.mkdir()
    asset = Usd.Stage.CreateNew(str(source))
    UsdGeom.SetStageMetersPerUnit(asset, 1.)
    UsdGeom.SetStageUpAxis(asset, "Z")
    root = UsdGeom.Xform.Define(asset, "/Asset")
    asset.SetDefaultPrim(root.GetPrim())
    mesh = UsdGeom.Mesh.Define(asset, "/Asset/Mesh")
    mesh.CreatePointsAttr().Set([Gf.Vec3f(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    mesh.CreateFaceVertexCountsAttr().Set([4] * 6)
    mesh.CreateFaceVertexIndicesAttr().Set([0, 2, 3, 1, 4, 5, 7, 6, 0, 1, 5, 4, 2, 6, 7, 3, 0, 4, 6, 2, 1, 3, 7, 5])
    asset.GetRootLayer().Save()
    physical = {"size_m": [.2, .2, .2], "mass_kg": 1., "grasp_points": []}
    row = {"public_id": "resident_bookstack_01", "runtime_id": "goal_object_1",
           "prim_path": "/World/envs/env_0/goal_object_1", "physical_properties": physical,
           "scene_binding": {"status": "proposed", "deployment_directive": {
               **physical, "position_m": [1., 1., .5], "rotation_wxyz": [1., 0., 0., 0.],
               "asset": {"asset_key": "mh_bookstack", "kind": "native_usd_asset_with_public_parameter_overrides",
                         "usd_rel": "package/Assets/books.usda"}}}}
    deployment = {"scene_key": native.SCENE_KEY, "sha256": "a" * 64, "object_instances": [row],
                  "scene_overlay": {"source_files": {str(source): native.file_sha256(source)}}}
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, "Z")
    native.goal_native_objects._author_object(stage, row["prim_path"],
                                              native.goal_native_objects._prepare(row, deployment))
    floor = UsdGeom.Cube.Define(stage, "/Floor")
    floor.GetSizeAttr().Set(1.)
    floor.AddTranslateOp().Set((0., 0., .125))
    floor.AddScaleOp().Set((10., 10., .25))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim()).CreateCollisionEnabledAttr(True)
    pose = [1., 1., .5, 0., 0., 0., 1.]
    view = SimpleNamespace(prim_paths=[row["prim_path"]], get_masses=lambda: [[1.]],
                           get_transforms=lambda: [pose])
    scene = Scene(goal_object_1=SimpleNamespace(root_physx_view=view))
    scene.rigid_objects = scene
    observer = [0., 0., 2., 0., 0., 0., 1.]
    scene.articulations = {"iris_1": SimpleNamespace(root_physx_view=SimpleNamespace(
        get_root_transforms=lambda: Tensor([observer]), link_paths=[["/Iris/base"]],
        get_link_transforms=lambda: Tensor([[observer]])))}
    sim = SimpleNamespace(current_time_step_index=20, current_time=1.,
                          physics_sim_view=SimpleNamespace(update_articulations_kinematic=lambda: None))
    session = SimpleNamespace(base_env=SimpleNamespace(scene=scene, sim=sim),
                              env_cfg=SimpleNamespace(sim=SimpleNamespace(enable_scene_query_support=True)))
    occupancy = SimpleNamespace(sha256="b" * 64, path="host-map.yaml")
    physx = ModuleType("omni.physx")
    query = Query()
    query.object_path = row["prim_path"]
    interface = SimpleNamespace(is_readback_suppressed=lambda: False)
    physx.get_physx_interface = lambda: interface
    physx.get_physx_scene_query_interface = lambda: query
    omni = ModuleType("omni")
    omni.physx = physx
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.physx", physx)
    return SimpleNamespace(session=session, stage=stage, deployment=deployment, occupancy=occupancy,
                           query=query, episode_id="episode-1",
                           sensor=SimpleNamespace(capture_camera_frame=lambda agent: __import__("numpy").ones((3, 4, 3), dtype="uint8")),
                           points=[[0., 0., .25], [2., 0., .25]], observer=observer)


def _probe(host, tmp_path, *, phase="before", closeout_time_s=None, name=None):
    return native.probe_native_backends(
        session=host.session, stage=host.stage, deployment=host.deployment,
        occupancy=host.occupancy, sensor_manager=host.sensor, agent="iris_1",
        episode_id=host.episode_id, item_codes={"resident_bookstack_01": "personal_book_stack"},
        max_range_m=5., points=host.points, phase=phase, closeout_time_s=closeout_time_s,
        output_path=tmp_path / f"{name or phase}.json")


def test_before_phase_defaults_to_the_entry_scan_node(host, tmp_path):
    report = _probe(host, tmp_path)
    assert report["phase"] == "before"
    assert report["status"] == "measured"
    assert report["scoreable"] is False and report["business_outcomes_validated"] is False
    assert report["detector"]["claims"]
    samples = report["path_provenance"]["samples"]
    assert [sample["phase"] for sample in samples] == ["before"]
    assert "closeout" not in report


def test_after_phase_without_closeout_is_rejected(host, tmp_path):
    report = _probe(host, tmp_path, phase="after")
    assert report["status"] == "unknown"
    assert report["phase"] == "after"
    assert any("requires an observed closeout" in blocker for blocker in report["blockers"])
    assert report["path_provenance"]["samples"] == []


def test_after_phase_injects_closeout_and_measures_downstream_frame(host, tmp_path):
    report = _probe(host, tmp_path, phase="after", closeout_time_s=.5)
    assert report["status"] == "measured", report["blockers"]
    assert report["closeout"] == {"node_id": "hall_release", "time_s": .5,
                                  "injection": "domain_runtime_event_v1_node_completed"}
    assert report["path"]["status"] == "measured"
    samples = report["path_provenance"]["samples"]
    assert [sample["phase"] for sample in samples] == ["after"]
    assert samples[0]["sim_time_s"] > report["closeout"]["time_s"]


def test_after_frame_at_or_before_closeout_is_rejected(host, tmp_path):
    report = _probe(host, tmp_path, phase="after", closeout_time_s=1., name="same-time")
    assert report["status"] == "unknown"
    assert any("after closeout" in blocker for blocker in report["blockers"])


def test_unknown_phase_fails_closed(host, tmp_path):
    report = _probe(host, tmp_path, phase="mid_intervention")
    assert report["status"] == "unknown"
    assert any("unknown probe phase" in blocker for blocker in report["blockers"])


def test_occupancy_drift_and_incomplete_phases_block_collection(host):
    path = native.IsaacResidentPathBackend(points=host.points, session=host.session,
                                           stage=host.stage, occupancy=host.occupancy,
                                           episode_id=host.episode_id)
    provider = ModernHousePathReferenceProvider(path_backend=path)
    task = {"scene": {"scene_key": native.SCENE_KEY},
            "domain_requirements": {"items": [{"id": "books", "item_code": "personal_book_stack",
                                               "quantity": 1, "consumer_node": "diagnostic_only"}]},
            "objectives": [{"id": "room_entry_scan", "target_ref": "entry_scan_zone"},
                           {"id": "hall_release", "target_ref": "diagnostic_only"},
                           {"id": "room_condition_scan", "target_ref": "room_review_zone",
                            "prerequisites": ["hall_release"]}]}
    provider.bind_runtime({"schema": "eai.benchmark.domain_runtime_binding_v1",
                           "scene_key": native.SCENE_KEY, "public_task": task,
                           "base_env": host.session.base_env, "occupancy": host.occupancy})
    with pytest.raises(ValueError, match="both designated"):
        provider.collect({"schema": "eai.benchmark.domain_collector_context_v1",
                          "scene_key": native.SCENE_KEY, "phase": "episode_finalized", "time_s": 5.})
    host.occupancy.sha256 = "c" * 64
    record, pixels = native.capture_native_observation(
        host.session, __import__("demo.benchmark.observations", fromlist=["ObservationStore"]).ObservationStore(),
        host.sensor, episode_id=host.episode_id,
        observation_id="drift-frame", agent="iris_1", target_ref="entry_scan_zone")
    context = {"schema": "eai.benchmark.domain_frame_v1", "scene_key": native.SCENE_KEY,
               "observation_id": record["observation_id"], "observation": record,
               "frame": pixels, "node_id": "room_entry_scan", "time_s": record["timestamp_s"]}
    with pytest.raises(ValueError, match="occupancy hash changed"):
        provider.process_frame(context)
    assert path.provenance()["samples"] == []


def test_parking_plan_prefers_the_authored_cell_and_falls_back_to_free_cells():
    occupancy = provider_map(scene_key="modern_house_nvd")
    preferred = plan.plan_furniture_parking(occupancy, [5., 15.], [.9, .85])
    assert preferred["admitted"] and preferred["preferred_admitted"]
    assert preferred["position_xy_m"] == [5., 15.]
    assert preferred["map_sha256"] == occupancy.sha256
    # A proposal planted on the west wall must move to the nearest free cell.
    blocked = plan.plan_furniture_parking(occupancy, [2.3, 4.8], [.9, .85])
    assert blocked["admitted"] and not blocked["preferred_admitted"]
    assert blocked["basis"] == "nearest_occupancy_free_cell_from_authored_preference"
    assert blocked["displacement_m"] > 0.
    assert occupancy.rectangle_free(*blocked["position_xy_m"],
                                    blocked["half_extent_m"], blocked["half_extent_m"])
    tiny = plan.plan_furniture_parking(occupancy, [2.3, 4.8], [.9, .85], search_radius_m=.05)
    assert tiny["admitted"] is False


def test_corridor_distance_and_withdrawal_targets():
    occupancy = provider_map(scene_key="modern_house_nvd")
    corridor = [[4., 3.5], [3.5, 3.5], [3.5, 9.5], [3.5, 12.3], [4.5, 12.3]]
    assert plan.corridor_distance([4., 3.5], corridor) <= 0.
    assert plan.corridor_distance([3.5, 9.5], corridor) <= 0.
    assert plan.corridor_distance([8., 3.7], corridor) > 3.
    robots = [{"public_id": "house_ground_1", "position_xy": [4., 3.5], "body_size_xy": [.9, .55]},
              {"public_id": "house_ground_2", "position_xy": [6., 4.1], "body_size_xy": [.8, .65]},
              {"public_id": "house_ground_3", "position_xy": [8., 3.7], "body_size_xy": [.9, .55]}]
    result = plan.plan_withdrawal_targets(occupancy, corridor, robots, parking_xy=[5., 15.])
    assert result["admitted"]
    targets = {row["public_id"]: row for row in result["targets"]}
    for robot in robots:
        row = targets[robot["public_id"]]
        assert row["admitted"] and row["position_xy_m"]
        assert plan.corridor_distance(row["position_xy_m"], corridor) >= row["required_corridor_clearance_m"]
        half = plan.circumscribed_half_extent(robot["body_size_xy"])
        assert occupancy.rectangle_free(*row["position_xy_m"], half, half)
    positions = [tuple(row["position_xy_m"]) for row in result["targets"]]
    assert len(positions) == len(set(positions))
    parking = positions[0]
    assert __import__("math").dist(parking, [5., 15.]) >= 0.9


def test_intervention_record_timing_contract():
    occupancy_sha = "d" * 64
    record = plan.new_intervention_record({"parking": {"admitted": True}}, occupancy_sha)
    assert record["scoreable"] is False
    assert record["scope"] == "author_side_diagnostic_intervention_not_business_execution"
    assert [row["event"] for row in record["events"]] == list(plan.INTERVENTION_EVENT_ORDER)
    assert all(row["physics_step"] is None for row in record["events"])
    assert not plan.intervention_record_complete(record)
    with pytest.raises(ValueError, match="unknown intervention event"):
        plan.record_intervention_event(record, "business_success", physics_step=1, time_s=1.)
    for index, name in enumerate(plan.INTERVENTION_EVENT_ORDER):
        plan.record_intervention_event(record, name, physics_step=10 * (index + 1),
                                       time_s=.5 * (index + 1))
    with pytest.raises(ValueError, match="already recorded"):
        plan.record_intervention_event(record, plan.INTERVENTION_EVENT_ORDER[0],
                                       physics_step=1, time_s=1.)
    assert not plan.intervention_record_complete(record)  # occupancy assertion still open
    record["occupancy_sha256"]["before_probe"] = occupancy_sha
    record["occupancy_sha256"]["after_closeout"] = occupancy_sha
    record["occupancy_sha256"]["asserted_unchanged"] = True
    assert plan.intervention_record_complete(record)
    record["events"][-1]["time_s"] = 0.  # timestamps must not regress
    assert not plan.intervention_record_complete(record)


def test_probe_report_hashes_and_archives_strict_json(host, tmp_path):
    report = _probe(host, tmp_path, name="strict")
    text = (tmp_path / "strict.json").read_text()
    assert json.loads(text) == report
    assert report["sha256"] == hashlib.sha256(
        json.dumps({k: v for k, v in report.items() if k != "sha256"},
                   sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
