"""Standalone OpenUSD + injected tensor views; no Isaac application/solver."""

from copy import deepcopy
import math
from types import SimpleNamespace

import pytest
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from demo.benchmark.goal_native_occupancy import collect_native_occupancy
from demo.benchmark.goal_live_occupancy import check_live_segment


class View:
    def __init__(self, paths, poses):
        self.link_paths = paths
        self.poses = poses
        self.reads = 0

    def get_link_transforms(self):
        self.reads += 1
        return deepcopy(self.poses)


def cube_body(stage, path):
    body = UsdGeom.Xform.Define(stage, path)
    body.AddTranslateOp().Set(Gf.Vec3d(1000, 2000, 3000))  # stale USD/Fabric pose
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    shape = UsdGeom.Cube.Define(stage, path + "/collision")
    shape.CreateSizeAttr(1)
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    return body, shape


@pytest.fixture
def data():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    cube_body(stage, "/World/A/base")
    cube_body(stage, "/World/A/link")
    view = View([["/World/A/base", "/World/A/link"]],
                [[[0, 0, 1, 0, 0, 0, 1], [0, 2, 1, 0, 0, 0, 1]]])
    return dict(stage=stage, scene=SimpleNamespace(articulations={"robot": SimpleNamespace(root_physx_view=view)}, rigid_objects={}),
                bindings=[dict(id="A", scene_key="robot", instance_index=0, root_path="/World/A", activity="idle")],
                context=dict(scene_id="episode-1", snapshot_id="frame-10", source_id="test-host",
                             observed_at_s=10., physics_step=10, frame_id="USD_world", meters_per_unit=1.,
                             stage_identifier=stage.GetRootLayer().identifier, physx_view_frame="USD_world",
                             readback_frozen=True, kinematics_current=True))


def collect(data, **kwargs):
    return collect_native_occupancy(data["stage"], data["scene"], robot_bindings=data["bindings"],
                                    readback_context=data["context"], now_s=10.05, **kwargs)


def test_all_idle_bodies_use_tensor_poses_without_stage_edits(data):
    before = data["stage"].GetRootLayer().ExportToString()
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    assert report["expected_inventory"][0]["activity"] == "idle"
    assert len(report["snapshot"]["bodies"]) == 2
    assert report["snapshot"]["bodies"][0]["position_xyz_m"] == [0, 0, 1]
    assert report["occupancy"]["entities"][0]["world_aabb_m"] == [[-.5, -.5, .5], [.5, 2.5, 1.5]]
    assert data["scene"].articulations["robot"].root_physx_view.reads == 1
    assert data["stage"].GetRootLayer().ExportToString() == before
    assert report["stage_writes"] == 0
    assert not report["measurement_authenticity_validated"]
    assert len(report["source_files"]) == 2


def test_body_and_collider_scale_translation_and_live_rotation(data):
    stage = data["stage"]
    UsdGeom.Xformable(stage.GetPrimAtPath("/World/A/base")).AddScaleOp().Set(Gf.Vec3f(2, 3, 4))
    shape = UsdGeom.Xformable(stage.GetPrimAtPath("/World/A/base/collision"))
    shape.AddTranslateOp().Set(Gf.Vec3d(1, 0, 0))
    shape.AddScaleOp().Set(Gf.Vec3f(2, 1, .5))
    data["scene"].articulations["robot"].root_physx_view.poses[0][0] = [0, 0, 1, 0, 0, math.sqrt(.5), math.sqrt(.5)]
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    body = report["snapshot"]["bodies"][0]
    assert body["local_collision_bounds_m"] == [[0, -1.5, -1], [4, 1.5, 1]]
    assert body["orientation_wxyz"] == pytest.approx([math.sqrt(.5), 0, 0, math.sqrt(.5)])
    box = report["occupancy"]["entities"][0]["bodies"][0]["world_aabb_m"]
    assert box[0] == pytest.approx([-1.5, 0, 0])
    assert box[1] == pytest.approx([1.5, 4, 2])


def test_mesh_points_bound_geometry_not_authored_extent(data):
    stage = data["stage"]
    path = "/World/A/base/collision"
    stage.RemovePrim(path)
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([(-1, -2, -3), (2, -2, -3), (0, 4, -3), (0, 0, 5)])
    mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3])
    mesh.CreateExtentAttr([(-100, -100, -100), (100, 100, 100)])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    body = report["snapshot"]["bodies"][0]
    assert body["local_collision_bounds_m"] == [[-1, -2, -3], [2, 4, 5]]
    assert body["bounds_source"]["collider_measurements"][0]["geometry"]["point_count"] == 4


@pytest.mark.parametrize("kind,axis,expected", [
    ("Capsule", "X", [2.5, .5, .5]), ("Capsule", "Y", [.5, 2.5, .5]),
    ("Capsule", "Z", [.5, .5, 2.5]), ("Cylinder", "Y", [.5, 2, .5]),
    ("Cone", "Z", [.5, .5, 2]), ("Sphere", None, [.5, .5, .5]),
])
def test_native_primitive_dimensions_and_axis(data, kind, axis, expected):
    stage = data["stage"]
    path = "/World/A/base/collision"
    stage.RemovePrim(path)
    shape = getattr(UsdGeom, kind).Define(stage, path)
    shape.CreateRadiusAttr(.5)
    if axis:
        shape.CreateHeightAttr(4)
        shape.CreateAxisAttr(axis)
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    assert report["snapshot"]["bodies"][0]["local_collision_bounds_m"] == [[-x for x in expected], expected]


def test_instance_proxy_colliders_are_measured_and_not_prototype_paths(data):
    stage = data["stage"]
    asset = Usd.Stage.CreateInMemory()
    cube_body(asset, "/Asset/base")
    cube_body(asset, "/Asset/link")
    stage.RemovePrim("/World/A")
    instance = UsdGeom.Xform.Define(stage, "/World/A").GetPrim()
    instance.GetReferences().AddReference(asset.GetRootLayer().identifier, "/Asset")
    instance.SetInstanceable(True)
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    body = report["snapshot"]["bodies"][0]
    assert body["collision_paths"] == ["/World/A/base/collision"]
    assert body["bounds_source"]["collider_measurements"][0]["instance_proxy"]


@pytest.mark.parametrize("mutation", ["missing_link", "extra_link", "bad_pose", "wrong_root", "omit_instance", "omit_articulation"])
def test_incomplete_tensor_or_binding_inventory_fails_closed(data, mutation):
    view = data["scene"].articulations["robot"].root_physx_view
    if mutation == "missing_link":
        view.link_paths[0].pop(); view.poses[0].pop()
    elif mutation == "extra_link":
        view.link_paths[0].append("/World/A/ghost"); view.poses[0].append([0, 0, 0, 0, 0, 0, 1])
    elif mutation == "bad_pose":
        view.poses[0][0] = [0, 0, 0, 0, 0, 0, 0]
    elif mutation == "wrong_root":
        data["bindings"][0]["root_path"] = "/World"
        cube_body(data["stage"], "/World/other/body")
    elif mutation == "omit_instance":
        view.link_paths.append(view.link_paths[0][:]); view.poses.append(deepcopy(view.poses[0]))
    else:
        data["scene"].articulations["other"] = data["scene"].articulations["robot"]
    report = collect(data)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None and report["expected_inventory"] == []


@pytest.mark.parametrize("key,value", [
    ("physx_view_frame", None), ("frame_id", "environment_local"), ("meters_per_unit", .01),
    ("readback_frozen", False), ("kinematics_current", False), ("physics_step", None),
    ("observed_at_s", 9), ("observed_at_s", 11), ("stage_identifier", "other"), ("source_id", ""),
])
def test_context_never_assumes_global_fresh_current_state(data, key, value):
    data["context"][key] = value
    report = collect(data)
    assert report["status"] == "blocked"
    assert data["scene"].articulations["robot"].root_physx_view.reads == 0


@pytest.mark.parametrize("mutation", ["unknown_xform", "cooked_mesh", "point_instancer", "reset_stack", "animated_shape", "reflected_body"])
def test_unknown_geometry_is_not_replaced_with_declared_size_or_skipped(data, mutation):
    stage = data["stage"]
    path = "/World/A/base/collision"
    prim = stage.GetPrimAtPath(path)
    data["bindings"][0]["body_size"] = [1, 1, 1]
    if mutation == "unknown_xform":
        stage.RemovePrim(path)
        prim = UsdGeom.Xform.Define(stage, path).GetPrim()
        UsdPhysics.CollisionAPI.Apply(prim)
    elif mutation == "cooked_mesh":
        stage.RemovePrim(path)
        prim = UsdGeom.Mesh.Define(stage, path).GetPrim()
        UsdPhysics.CollisionAPI.Apply(prim)
        UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr("convexDecomposition")
    elif mutation == "point_instancer":
        UsdGeom.PointInstancer.Define(stage, "/World/A/instances")
    elif mutation == "reset_stack":
        UsdGeom.Xformable(prim).SetResetXformStack(True)
    elif mutation == "animated_shape":
        attr = UsdGeom.Cube(prim).GetSizeAttr()
        attr.Set(1, 1); attr.Set(2, 2)
    else:
        UsdGeom.Xformable(stage.GetPrimAtPath("/World/A/base")).AddScaleOp().Set(Gf.Vec3f(-1, 1, 1))
    report = collect(data)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None
    if mutation != "point_instancer":
        assert len(report["body_audit"]) == 2
        assert report["body_audit"][0]["status"] == "blocked"
        assert report["body_audit"][1]["status"] == "measured"


@pytest.mark.parametrize("disabled", [False, True])
def test_loaded_noncolliding_link_keeps_live_audit_but_no_occupancy_box(data, disabled):
    prim = data["stage"].GetPrimAtPath("/World/A/link/collision")
    if disabled:
        UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
    else:
        prim.RemoveAPI(UsdPhysics.CollisionAPI)
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    assert len(report["body_audit"]) == 2
    audit = report["body_audit"][1]
    assert audit["status"] == "known_noncolliding"
    assert audit["occupancy"] == "empty"
    assert audit["position_xyz_m"] == [0, 2, 1]
    assert audit["collision_paths"] == []
    assert audit["disabled_collision_paths"] == (["/World/A/link/collision"] if disabled else [])
    assert len(report["expected_inventory"]) == 1
    assert report["expected_inventory"][0]["body_ids"] == ["/World/A/base"]
    assert [b["prim_path"] for b in report["snapshot"]["bodies"]] == ["/World/A/base"]


def test_no_collision_api_on_body_still_collects_enabled_child_colliders(data):
    assert not data["stage"].GetPrimAtPath("/World/A/link").HasAPI(UsdPhysics.CollisionAPI)
    report = collect(data)
    assert report["status"] == "current_occupancy"
    assert report["body_audit"][1]["status"] == "measured"
    assert report["snapshot"]["bodies"][1]["collision_paths"] == ["/World/A/link/collision"]


def test_unloaded_geometry_cannot_be_classified_noncolliding(data):
    asset = Usd.Stage.CreateInMemory()
    cube_body(asset, "/Asset/base")
    root = data["stage"].GetPrimAtPath("/World/A/link")
    root.GetPayloads().AddPayload(asset.GetRootLayer().identifier, "/Asset/base")
    data["stage"].Unload(root.GetPath())
    assert not root.IsLoaded()
    report = collect(data)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None
    assert "unloaded" in " ".join(report["blockers"])


def test_unloaded_child_payload_blocks_even_with_other_measurable_colliders(data):
    asset = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(asset, "/Payload")
    large = UsdGeom.Cube.Define(asset, "/Payload/large")
    large.CreateSizeAttr(4)
    UsdPhysics.CollisionAPI.Apply(large.GetPrim())
    stage = data["stage"]
    path = "/World/A/base/payload"
    payload = UsdGeom.Xform.Define(stage, path).GetPrim()
    payload.GetPayloads().AddPayload(asset.GetRootLayer().identifier, "/Payload")
    loaded = collect(data)
    assert loaded["status"] == "current_occupancy", loaded["blockers"]
    body = loaded["snapshot"]["bodies"][0]
    assert set(body["collision_paths"]) == {"/World/A/base/collision", path + "/large"}
    assert body["local_collision_bounds_m"] == [[-2, -2, -2], [2, 2, 2]]
    stage.Unload(path)
    assert stage.GetPrimAtPath("/World/A").IsLoaded()
    assert stage.GetPrimAtPath("/World/A/base").IsLoaded()
    assert not payload.IsLoaded()
    before, load_set = stage.GetRootLayer().ExportToString(), stage.GetLoadSet()
    report = collect(data)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None
    assert report["expected_inventory"] == []
    assert "unloaded" in " ".join(report["blockers"])
    assert stage.GetLoadSet() == load_set and not payload.IsLoaded()
    assert stage.GetRootLayer().ExportToString() == before


def test_entirely_noncolliding_entity_is_never_silently_removed(data):
    for path in ("/World/A/base/collision", "/World/A/link/collision"):
        data["stage"].GetPrimAtPath(path).RemoveAPI(UsdPhysics.CollisionAPI)
    report = collect(data)
    assert [a["status"] for a in report["body_audit"]] == ["known_noncolliding"] * 2
    assert report["status"] == "blocked"
    assert "entirely_noncolliding_entity_not_representable" in report["blockers"][0]
    assert report["snapshot"] is None


def hold(data):
    stage = data["stage"]
    cube_body(stage, "/World/tool")
    joint = UsdPhysics.FixedJoint.Define(stage, "/World/joints/hold")
    joint.CreateBody0Rel().SetTargets(["/World/A/base"])
    joint.CreateBody1Rel().SetTargets(["/World/tool"])
    data["scene"].rigid_objects["tool"] = SimpleNamespace(root_physx_view=SimpleNamespace(
        prim_paths=["/World/tool"], get_transforms=lambda: [[1, 0, 1, 0, 0, 0, 1]]))
    binding = dict(id="tool", scene_key="tool", instance_index=0, root_path="/World/tool", holder_id="A", joint_path="/World/joints/hold")
    read = dict(data["context"], kind="physx_fixed_joint", joint_path=binding["joint_path"],
                body0="/World/A/base", body1="/World/tool", enabled=True, broken=False)
    return binding, read


def test_held_object_requires_joint_runtime_readback_and_its_own_live_pose(data):
    binding, sample = hold(data)
    absent = collect(data, held_bindings=[binding])
    assert absent["status"] == "blocked"
    assert "USD authorship is insufficient" in absent["blockers"][0]
    report = collect(data, held_bindings=[binding], joint_reader=lambda path: sample)
    assert report["status"] == "current_occupancy", report["blockers"]
    assert report["snapshot"]["bodies"][-1]["position_xyz_m"] == [1, 0, 1]
    assert report["snapshot"]["attachments"][0]["kind"] == "fixed_attachment_readback"
    assert report["expected_inventory"][-1]["holder_id"] == "A"


@pytest.mark.parametrize("mutation", ["unlisted", "broken", "wrong_endpoint", "stale", "disabled", "wrong_kind"])
def test_joint_identity_and_status_fail_closed(data, mutation):
    binding, sample = hold(data)
    if mutation == "unlisted":
        report = collect(data)
    else:
        if mutation == "broken": sample["broken"] = True
        elif mutation == "wrong_endpoint": sample["body0"] = "/World/A/link"
        elif mutation == "stale": sample["physics_step"] = 9
        elif mutation == "disabled": UsdPhysics.FixedJoint(data["stage"].GetPrimAtPath(binding["joint_path"])).CreateJointEnabledAttr(False)
        else: sample["kind"] = "attachment_request"
        report = collect(data, held_bindings=[binding], joint_reader=lambda path: sample)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None


def test_iris_style_internal_joint_mesh_endpoint_resolves_to_measured_body(data):
    stage = data["stage"]
    path = "/World/A/base/collision"
    stage.RemovePrim(path)
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([(-1, -1, -1), (1, -1, -1), (0, 1, -1), (0, 0, 1)])
    mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/A/link/joint0")
    joint.CreateBody0Rel().SetTargets([path])
    joint.CreateBody1Rel().SetTargets(["/World/A/link"])
    before = stage.GetRootLayer().ExportToString()
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    assert report["joint_audit"] == [{
        "joint_path": "/World/A/link/joint0", "status": "internal", "endpoints": [
            {"target_path": path, "body_path": "/World/A/base"},
            {"target_path": "/World/A/link", "body_path": "/World/A/link"}]}]
    assert stage.GetRootLayer().ExportToString() == before


def test_joint_to_different_measured_robot_remains_external(data):
    stage = data["stage"]
    cube_body(stage, "/World/A_other/base")
    view = View([["/World/A_other/base"]], [[[2, 0, 1, 0, 0, 0, 1]]])
    data["scene"].articulations["other"] = SimpleNamespace(root_physx_view=view)
    data["bindings"].append(dict(id="B", scene_key="other", instance_index=0,
                                 root_path="/World/A_other", activity="idle"))
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/A/link/joint0")
    joint.CreateBody0Rel().SetTargets(["/World/A/base/collision"])
    joint.CreateBody1Rel().SetTargets(["/World/A_other/base/collision"])
    report = collect(data)
    assert report["status"] == "blocked"
    assert "unlisted external attachment/joint" in " ".join(report["blockers"])
    assert report["joint_audit"][0]["endpoints"][1]["body_path"] == "/World/A_other/base"
    assert report["snapshot"] is None


@pytest.mark.parametrize("case", ["missing", "orphan_in_root", "unmeasured_body", "disabled_nearest_body", "empty_pair"])
def test_unknown_joint_endpoint_is_not_absorbed_by_robot_path_prefix(data, case):
    stage = data["stage"]
    target = "/World/A/missing"
    if case == "orphan_in_root":
        target = str(UsdGeom.Xform.Define(stage, "/World/A/orphan").GetPath())
    elif case in ("unmeasured_body", "disabled_nearest_body"):
        # Similar lexical prefix, but a separate rigid body absent from the live roster.
        cube_body(stage, "/World/A_extra/body")
        target = "/World/A_extra/body/collision"
        if case == "disabled_nearest_body":
            UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath("/World/A_extra/body")).CreateRigidBodyEnabledAttr(False)
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/A/link/joint0")
    joint.CreateBody0Rel().SetTargets(["/World/A/base/collision"])
    if case != "empty_pair":
        joint.CreateBody1Rel().SetTargets([target])
    report = collect(data)
    assert report["status"] == "blocked"
    assert report["snapshot"] is None and report["expected_inventory"] == []
    assert "joint endpoint" in " ".join(report["blockers"]) or "endpoint pair" in " ".join(report["blockers"])
    assert report["joint_audit"][0]["status"] == "blocked"


def test_multiple_instances_keep_idle_robot_as_dynamic_obstacle(data):
    stage = data["stage"]
    cube_body(stage, "/World/B/base")
    cube_body(stage, "/World/B/link")
    view = data["scene"].articulations["robot"].root_physx_view
    view.link_paths.append(["/World/B/base", "/World/B/link"])
    view.poses.append([[2, 0, 1, 0, 0, 0, 1], [2, 2, 1, 0, 0, 0, 1]])
    data["bindings"][0]["activity"] = "active"
    data["bindings"].append(dict(id="B", scene_key="robot", instance_index=1, root_path="/World/B", activity="idle"))
    report = collect(data)
    assert report["status"] == "current_occupancy", report["blockers"]
    candidate = check_live_segment(report["snapshot"], expected_inventory=report["expected_inventory"],
                                  moving_robot_id="A", translation_xyz_m=[4, 0, 0], now_s=10.05)
    assert candidate["status"] == "blocked"
    assert {hit["obstacle_entity_id"] for hit in candidate["dynamic_intersections"]} == {"B"}
    assert view.reads == 1
