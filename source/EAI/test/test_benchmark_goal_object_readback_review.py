"""Negative object evidence cases, independent of Isaac/runtime output files."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_object_readback_review import review_object_snapshot
from demo.benchmark.provenance import file_sha256


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    source = tmp_path/"object.usda"
    source.write_text("#usda 1.0\n")
    digest = file_sha256(source)
    monkeypatch.setattr("demo.benchmark.goal_object_readback_review._asset_path", lambda spec, deployment: source)
    root = "/World/envs/env_0/goal_object_1"
    point = {"id": "grip", "position_m": [0., 0., .15], "interface": "fixed_gripper_v1"}
    spec = {"public_id": "carton", "runtime_id": "goal_object_1", "prim_path": root,
            "physical_properties": {"mass_kg": 3., "size_m": [.4,.3,.3], "grasp_points": [point]}}
    deployment = {"sha256": "fixture", "object_instances": [spec],
                  "scene_overlay": {"source_files": {str(source): digest}}}
    row = {"public_id": "carton", "runtime_id": spec["runtime_id"], "prim_path": root,
           "scoreable": False, "status": "measured", "mismatches": [], "unknowns": [],
           "collision_cooking_validated": False, "grasp_execution_validated": False,
           "usd": {"rigid_body_enabled": True, "kinematic_enabled": False, "authored_mass_kg": 3.,
                   "local_mesh_bounds_m": {"min": [-.2,-.15,-.15], "max": [.2,.15,.15],
                       "size": [.4,.3,.3], "center": [0.,0.,0.], "meshes": [root+"/Geometry/mesh"]},
                   "collisions": [{"prim_path": root+"/Geometry/mesh", "enabled": True, "approximation": "convexHull"}]},
           "physx": {"status": "measured", "source": "root_physx_view", "prim_path": root,
                     "mass_kg": 3., "position_m": [0.,0.,.15], "rotation_xyzw": [0.,0.,0.,1.]},
           "fixed_points": [{"id": "grip", "prim_path": root+"/GraspPoints/grip", "status": "usd_measured",
                             "position_local_m": point["position_m"], "declared_interface": point["interface"],
                             "physical_connector_verified": False}],
           "native_source": {"path": str(source), "sha256": digest, "status": "composed_reference_verified"}}
    snapshot = {"schema": "eai.goal_native_object_readback", "version": 1, "deployment_sha256": "fixture",
                "stage_identifier": "anon:fixture", "scoreable": False, "up_axis": "Z", "meters_per_unit": 1., "objects": [row]}
    return deployment, deepcopy(snapshot)


def test_configuration_readback_has_narrow_scope(evidence):
    result = review_object_snapshot(*evidence, stage_identifier="anon:fixture")
    assert result[0]["configuration_readback_validated"]
    assert result[0]["actual_mass_kg"] == 3.


@pytest.mark.parametrize("field", ["mass", "pose", "rotation", "extent", "collision", "point", "interface", "alias", "source", "claim", "duplicate"])
def test_rehashed_status_cannot_substitute_measurements(evidence, field):
    deployment, snapshot = evidence
    row = snapshot["objects"][0]
    if field == "mass": row["physx"]["mass_kg"] = .1
    elif field == "pose": row["physx"]["position_m"][0] = float("nan")
    elif field == "rotation": row["physx"]["rotation_xyzw"] = [0.,0.,0.,0.]
    elif field == "extent": row["usd"]["local_mesh_bounds_m"]["max"][0] = 10.
    elif field == "collision": row["usd"]["collisions"] = []
    elif field == "point": row["fixed_points"][0]["position_local_m"][0] = 1.
    elif field == "interface": row["fixed_points"][0]["declared_interface"] = "other"
    elif field == "alias": row["physx"]["prim_path"] += "_other"
    elif field == "source": row["native_source"]["sha256"] = "changed"
    elif field == "claim": row["grasp_execution_validated"] = True
    else: snapshot["objects"].append(deepcopy(row))
    with pytest.raises(ValueError): review_object_snapshot(deployment, snapshot, stage_identifier="anon:fixture")


def test_missing_native_object_is_explicit_incomplete_result(evidence):
    deployment, snapshot = evidence
    snapshot["objects"][0].update(status="blocked", reason="goal_object_prim_missing")
    result = review_object_snapshot(deployment, snapshot, stage_identifier="anon:fixture")
    assert result == [{"public_id": "carton", "configuration_readback_validated": False, "reason": "goal_object_prim_missing"}]


def test_other_frozen_asset_is_not_this_object(evidence, tmp_path):
    deployment, snapshot = evidence
    other = tmp_path/"other.usda"
    other.write_text("#usda 1.0\n")
    digest = file_sha256(other)
    deployment["scene_overlay"]["source_files"][str(other)] = digest
    snapshot["objects"][0]["native_source"].update(path=str(other), sha256=digest)
    with pytest.raises(ValueError, match="native source"):
        review_object_snapshot(deployment, snapshot, stage_identifier="anon:fixture")


@pytest.mark.parametrize("actual", [None, "", "anon:other"])
def test_object_stage_must_match_independent_manifest(evidence, actual):
    deployment, snapshot = evidence
    snapshot["stage_identifier"] = actual
    with pytest.raises(ValueError, match="frame mismatch"):
        review_object_snapshot(deployment, snapshot, stage_identifier="anon:fixture")
