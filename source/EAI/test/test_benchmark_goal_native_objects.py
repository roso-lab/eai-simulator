"""Standalone OpenUSD and injected config/PhysX views; never start Isaac."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from demo.benchmark import goal_native_objects as objects


def _fixture(tmp_path, *, up="Z"):
    source_path = tmp_path / "package" / "Assets" / "crate.usda"
    source_path.parent.mkdir(parents=True)
    stage = Usd.Stage.CreateNew(str(source_path))
    UsdGeom.SetStageUpAxis(stage, up)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())
    root.AddTranslateOp().Set(Gf.Vec3d(7, 11, 3))
    mesh = UsdGeom.Mesh.Define(stage, "/Root/Mesh")
    mesh.CreatePointsAttr().Set([Gf.Vec3f(x, y, z) for x in (10, 30) for y in (20, 60) for z in (0, 20)])
    mesh.CreateFaceVertexCountsAttr().Set([4] * 6)
    mesh.CreateFaceVertexIndicesAttr().Set([0, 2, 3, 1, 4, 5, 7, 6, 0, 1, 5, 4,
                                          2, 6, 7, 3, 0, 4, 6, 2, 1, 3, 7, 5])
    # Deliberately wrong extent: implementation must inspect mesh points.
    mesh.CreateExtentAttr().Set([Gf.Vec3f(-1000), Gf.Vec3f(1000)])
    stage.GetRootLayer().Save()
    physical = {"mass_kg": 12.0, "size_m": [0.9, 0.65, 0.6],
                "initial_region_id": "rack_aisle_a", "handling_modes": [],
                "grasp_points": [{"id": "support_left", "position_m": [0, -0.3, 0.25],
                                  "interface": "support_socket_v1"}]}
    row = {"public_id": "order_crate_01", "runtime_id": "goal_object_1",
           "prim_path": "/World/envs/env_0/goal_object_1", "physical_properties": physical,
           "scene_binding": {"status": "proposed", "deployment_directive": {
               **deepcopy(physical), "position_m": [4.0, 5.0, 0.3],
               "rotation_wxyz": [1.0, 0.0, 0.0, 0.0],
               "asset": {"kind": "native_usd_asset_with_public_parameter_overrides",
                         "asset_key": "wooden_crate_a", "usd_rel": "package/Assets/crate.usda"}}}}
    deployment = {"sha256": "test-deployment", "object_instances": [row],
                  "scene_overlay": {"source_files": {str(source_path): objects._sha(source_path)}}}
    return deployment, row, source_path


def _target():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    return stage


def _scene(row, *, mass=12.0, path=None):
    view = SimpleNamespace(prim_paths=[path or row["prim_path"]],
                           get_masses=lambda: [[mass]],
                           get_transforms=lambda: [[4, 5, 0.3, 0, 0, 0, 1]])
    return {row["runtime_id"]: SimpleNamespace(root_physx_view=view)}


@pytest.mark.parametrize("up", ["Y", "Z"])
def test_native_composed_points_scaled_centered_without_source_mutation(tmp_path, up):
    deployment, row, source = _fixture(tmp_path, up=up)
    before = objects._sha(source)
    plan = objects._prepare(row, deployment)
    stage = _target()
    objects._author_object(stage, row["prim_path"], plan)
    report = objects.read_goal_objects(stage, _scene(row), deployment)["objects"][0]
    assert report["status"] == "measured"
    assert report["usd"]["local_mesh_bounds_m"]["size"] == pytest.approx([0.9, 0.65, 0.6])
    assert report["usd"]["local_mesh_bounds_m"]["center"] == pytest.approx([0, 0, 0], abs=1e-7)
    assert report["usd"]["world_mesh_bounds_m"]["center"] == pytest.approx([4, 5, 0.3])
    assert report["usd"]["authored_mass_kg"] == pytest.approx(12)
    assert report["physx"]["mass_kg"] == 12
    assert all(x["enabled"] and x["approximation"] == "convexHull" for x in report["usd"]["collisions"])
    assert report["fixed_points"][0]["position_world_usd_m"] == pytest.approx([4, 4.7, 0.55])
    assert report["fixed_points"][0]["physical_connector_verified"] is False
    assert report["collision_cooking_validated"] is False
    assert objects._sha(source) == before


def test_configuration_attaches_exact_runtime_ids_with_injected_isaac_config(tmp_path, monkeypatch):
    deployment, row, _ = _fixture(tmp_path)
    monkeypatch.setattr(objects, "_validate", lambda d: None)
    monkeypatch.setattr(objects, "_make_cfg", lambda plan: SimpleNamespace(plan=plan))
    cfg = SimpleNamespace(scene=SimpleNamespace())
    report = objects.configure_goal_objects(cfg, deployment)
    assert report["objects"][0]["status"] == "configured"
    assert cfg.scene.goal_object_1.plan["public_id"] == row["public_id"]
    assert report["physics_execution_validated"] is False
    with pytest.raises(ValueError, match="already exists"):
        objects.configure_goal_objects(cfg, deployment)


def test_factory_missing_native_stays_blocked_without_isaac_import(monkeypatch):
    monkeypatch.setattr(objects, "_validate", lambda d: None)
    monkeypatch.setattr(objects, "_make_cfg", lambda p: pytest.fail("must not configure a proxy"))
    deployment = {"sha256": "factory", "scene_overlay": {"source_files": {}}, "object_instances": [{
        "public_id": "maintenance_service_cart", "runtime_id": "goal_object_1",
        "prim_path": "/World/envs/env_0/goal_object_1", "scene_binding": {
            "deployment_directive": {"asset": {"kind": "procedural_box_author_proposal"}}}}]}
    cfg = SimpleNamespace(scene=SimpleNamespace())
    report = objects.configure_goal_objects(cfg, deployment)
    assert report["objects"][0]["status"] == "blocked"
    assert "no_proxy_substitution" in report["objects"][0]["reason"]
    assert not vars(cfg.scene)


@pytest.mark.parametrize("public_id", ["production_anomaly_marker", "maintenance_service_cart"])
def test_factory_task_owned_mesh_has_exact_binding_and_readback(public_id):
    from demo.benchmark.goal_deployment import build_scene_deployment
    from demo.factory_benchmark.goal_props import FACTORY_GOAL_PROPS

    deployment = build_scene_deployment("factory")
    row = next(item for item in deployment["object_instances"] if item["public_id"] == public_id)
    authored = FACTORY_GOAL_PROPS[public_id]
    plan = objects._prepare(row, deployment)
    assert plan["source_path"] == str(authored["path"])
    assert plan["size_m"] == authored["size_m"]
    assert plan["mass_kg"] == authored["mass_kg"]
    stage = _target()
    objects._author_object(stage, row["prim_path"], plan)
    position = plan["position_m"]
    view = SimpleNamespace(prim_paths=[row["prim_path"]],
                           get_masses=lambda: [[plan["mass_kg"]]],
                           get_transforms=lambda: [[*position, 0, 0, 0, 1]])
    scene = {row["runtime_id"]: SimpleNamespace(root_physx_view=view)}
    readback = objects.read_goal_objects(stage, scene, deployment)["objects"]
    measured = next(item for item in readback if item["public_id"] == public_id)
    assert measured["status"] == "measured"
    assert measured["usd"]["local_mesh_bounds_m"]["size"] == pytest.approx(authored["size_m"])
    assert measured["physx"]["mass_kg"] == authored["mass_kg"]
    assert len(measured["usd"]["collisions"]) >= 2
    assert all(item["enabled"] and item["approximation"] == "convexHull"
               for item in measured["usd"]["collisions"])

    for mutation in ("asset_key", "kind", "usd_rel", "size_m"):
        changed = deepcopy(row)
        if mutation == "size_m":
            changed["physical_properties"]["size_m"] = [1.0, 1.0, 1.0]
        else:
            changed["scene_binding"]["deployment_directive"]["asset"][mutation] = "unbound"
        with pytest.raises(ValueError, match="asset|dimensions|proxy"):
            objects._prepare(changed, deployment)


def test_physx_unknown_never_filled_from_authored_mass(tmp_path):
    deployment, row, _ = _fixture(tmp_path)
    stage = _target()
    objects._author_object(stage, row["prim_path"], objects._prepare(row, deployment))
    report = objects.read_goal_objects(stage, {}, deployment)["objects"][0]
    assert report["status"] == "unknown"
    assert report["usd"]["authored_mass_kg"] == 12
    assert report["physx"]["status"] == "unknown"
    assert "mass_kg" not in report["physx"]


@pytest.mark.parametrize("mutation, expected", [
    ("mass", "physx_mass_differs_from_frozen_mass"),
    ("identity", "physx_body_identity_mismatch"),
    ("collision", "native_mesh_collision_schema_mismatch"),
    ("grasp", "fixed_point_transform_mismatch"),
    ("size", "mesh_size_differs_from_frozen_size"),
])
def test_actual_readback_detects_schema_pose_and_physx_mismatches(tmp_path, mutation, expected):
    deployment, row, _ = _fixture(tmp_path)
    stage = _target()
    objects._author_object(stage, row["prim_path"], objects._prepare(row, deployment))
    scene = _scene(row, mass=60 if mutation == "mass" else 12,
                   path="/WrongBody" if mutation == "identity" else None)
    if mutation == "collision":
        mesh = stage.GetPrimAtPath(row["prim_path"] + "/Geometry/Axis/Native/Mesh")
        UsdPhysics.CollisionAPI(mesh).GetCollisionEnabledAttr().Set(False)
    elif mutation == "grasp":
        prim = stage.GetPrimAtPath(row["prim_path"] + "/GraspPoints/support_left")
        prim.GetAttribute("xformOp:translate").Set(Gf.Vec3d(0, 0, 0))
    elif mutation == "size":
        prim = stage.GetPrimAtPath(row["prim_path"] + "/Geometry")
        prim.GetAttribute("xformOp:scale").Set(Gf.Vec3d(1))
    report = objects.read_goal_objects(stage, scene, deployment)["objects"][0]
    assert report["status"] == "mismatch"
    assert any(expected in reason for reason in report["mismatches"])


def test_source_change_after_prepare_rejected_before_spawn(tmp_path):
    deployment, row, source = _fixture(tmp_path)
    plan = objects._prepare(row, deployment)
    source.write_text(source.read_text() + "\n# changed after measurement\n")
    stage = _target()
    with pytest.raises(ValueError, match="source missing or changed"):
        objects._author_object(stage, row["prim_path"], plan)
    assert not stage.GetPrimAtPath(row["prim_path"])


def test_missing_reference_is_blocked(tmp_path):
    deployment, row, source = _fixture(tmp_path)
    stage = Usd.Stage.Open(str(source))
    stage.GetDefaultPrim().GetReferences().AddReference(str(tmp_path / "missing.usda"))
    stage.GetRootLayer().Save()
    deployment["scene_overlay"]["source_files"][str(source)] = objects._sha(source)
    with pytest.raises(ValueError, match="composition"):
        objects._prepare(row, deployment)


def test_same_size_substitute_cannot_pass_native_readback(tmp_path):
    deployment, row, _ = _fixture(tmp_path)
    stage = _target()
    objects._author_object(stage, row["prim_path"], objects._prepare(row, deployment))
    native_path = row["prim_path"] + "/Geometry/Axis/Native"
    stage.GetPrimAtPath(native_path).GetReferences().ClearReferences()
    UsdGeom.Cube.Define(stage, native_path + "/Proxy")
    report = objects.read_goal_objects(stage, _scene(row), deployment)["objects"][0]
    assert report["status"] == "blocked"
    assert "source-bound asset" in report["reason"]


def test_root_scale_is_not_hidden_by_object_local_measurement(tmp_path):
    deployment, row, _ = _fixture(tmp_path)
    stage = _target()
    root = objects._author_object(stage, row["prim_path"], objects._prepare(row, deployment))
    UsdGeom.Xformable(root).AddScaleOp().Set(Gf.Vec3f(2))
    report = objects.read_goal_objects(stage, _scene(row), deployment)["objects"][0]
    assert report["status"] == "mismatch"
    assert "rigid_root_world_scale_or_shear_is_not_identity" in report["mismatches"]


def test_usd24_composition_fallback_preserves_missing_reference_errors(tmp_path):
    deployment, row, source = _fixture(tmp_path)
    stage = Usd.Stage.Open(str(source))
    class LegacyStage:
        def TraverseAll(self):
            return stage.TraverseAll()
    assert objects._composition_errors(LegacyStage()) == []
    stage.GetDefaultPrim().GetReferences().AddReference(str(tmp_path / "missing.usda"))
    assert objects._composition_errors(LegacyStage())
    assert objects._composition_errors(stage, stage.GetDefaultPrim())
