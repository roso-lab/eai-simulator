"""Canonical deployment configuration and readback rejection tests."""

from copy import deepcopy
from pathlib import Path

import pytest

from demo.benchmark.goal_deployment import (
    compile_goal_deployment, validate_goal_deployment, verify_configuration_readback,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.provenance import canonical_sha256, file_sha256


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    public = build_goal_task_spec("warehouse_nvd")
    source = tmp_path / "source.txt"
    source.write_text("source-v1")
    world = public["public_world"]
    overlay = {
        "scene_key": "warehouse_nvd", "public_task_sha256": canonical_sha256(public),
        "source_verification_requested": True,
        "object_bindings": [{"id": r["id"], "public_object": deepcopy(r),
                             "status": "proposed", "deployment_directive": deepcopy(r["physical_properties"])}
                            for r in world["objects"]],
        "region_bindings": [{"id": r["id"], "public_region": deepcopy(r), "status": "proposed"} for r in world["regions"]],
        "route_bindings": [{"id": r["id"], "public_route": deepcopy(r), "status": "blocked"} for r in world["routes"]],
        "robot_spawns": [{"id": r["id"], "public_robot": deepcopy(r), "status": "proposed"} for r in public["candidate_robot_pool"]],
        "source_files": {str(source): file_sha256(source)}, "blockers": ["fixture-not-scene-bound"],
    }
    # Isolated author proposal source; do not require workstation maps/assets.
    from demo.benchmark import goal_deployment
    frozen = deepcopy(overlay)
    monkeypatch.setattr(goal_deployment, "_rebuild_overlay", lambda public, overlay: deepcopy(frozen))
    return public, overlay, source


def rehash(value):
    value["sha256"] = canonical_sha256({k: v for k, v in value.items() if k != "sha256"})


def readback(deployment):
    rows = []
    for kind, name, field in (("robot", "robot_instances", "physical_profile"),
                              ("object", "object_instances", "physical_properties")):
        for row in deployment[name]:
            rows.append({"id": row["runtime_id"], "public_id": row["public_id"], "kind": kind,
                         "entity_identity": "reference:" + row["runtime_id"], "prim_path": row["prim_path"],
                         field: deepcopy(row[field])})
            if kind == "robot":
                rows[-1].update({key: deepcopy(row[key]) for key in (
                    "type", "command_namespace", "backend_state_key", "public_declaration")})
    return {"deployment_sha256": deployment["sha256"], "entities": rows}


def test_distinct_same_type_members_and_frozen_object_configuration(inputs):
    public, overlay, _ = inputs
    deployment = compile_goal_deployment(public, overlay)
    assert validate_goal_deployment(deployment)["validated"]
    robots = deployment["robot_instances"]
    assert [row["runtime_id"] for row in robots] == ["iris_1", "m20_1", "carter_1", "m20_2"]
    for field in ("runtime_id", "prim_path", "command_namespace", "backend_state_key"):
        assert len({row[field] for row in robots}) == len(robots)
    assert deployment["object_instances"][0]["physical_properties"]["mass_kg"] == 12.
    assert deployment["review"]["scoreable"] is deployment["review"]["physical_execution_validated"] is False
    assert verify_configuration_readback(deployment, readback(deployment))["validated"]


@pytest.mark.parametrize("kind", ["robot", "object", "region", "route"])
def test_missing_binding_cannot_drop_unconvenient_public_item(inputs, kind):
    public, overlay, _ = inputs
    key = {"robot": "robot_spawns", "object": "object_bindings", "region": "region_bindings", "route": "route_bindings"}[kind]
    overlay[key].pop()
    with pytest.raises(ValueError, match="exact public inventory"):
        compile_goal_deployment(public, overlay)


def test_old_mass_directive_cannot_override_frozen_task(inputs):
    public, overlay, _ = inputs
    overlay["object_bindings"][0]["deployment_directive"]["mass_kg"] = 60.
    with pytest.raises(ValueError, match="frozen physical properties"):
        compile_goal_deployment(public, overlay)


@pytest.mark.parametrize("mutation", ["alias", "mass", "readiness", "geometry"])
def test_rehashed_configuration_tampering_is_rejected(inputs, mutation):
    public, overlay, _ = inputs
    deployment = compile_goal_deployment(public, overlay)
    if mutation == "alias":
        deployment["robot_instances"][3]["runtime_id"] = "m20_1"
    elif mutation == "mass":
        deployment["object_instances"][0]["physical_properties"]["mass_kg"] = 60.
    elif mutation == "readiness":
        deployment["review"]["physical_execution_validated"] = True
    else:
        deployment["scene_overlay"]["region_bindings"][0]["status"] = "physically_verified"
    rehash(deployment)
    with pytest.raises(ValueError):
        validate_goal_deployment(deployment)


def test_stale_source_is_not_repaired_by_rehashing_configuration(inputs):
    public, overlay, source = inputs
    deployment = compile_goal_deployment(public, overlay)
    source.write_text("source-v2")
    with pytest.raises(ValueError, match="source missing or changed"):
        validate_goal_deployment(deployment)


@pytest.mark.parametrize("mutation", ["alias", "mass", "profile", "unknown", "hash", "prim", "object_id",
                                     "command_namespace", "backend_state_key", "capabilities"])
def test_readback_must_match_distinct_actual_entities_and_public_parameters(inputs, mutation):
    public, overlay, _ = inputs
    deployment = compile_goal_deployment(public, overlay)
    observed = readback(deployment)
    rows = observed["entities"]
    if mutation == "alias":
        rows[3]["entity_identity"] = rows[1]["entity_identity"]
    elif mutation == "mass":
        rows[-2]["physical_properties"]["mass_kg"] = 60.
    elif mutation == "profile":
        rows[1]["physical_profile"]["effective_payload_kg"] = 100.
    elif mutation == "unknown":
        rows.pop()
    elif mutation == "hash":
        observed["deployment_sha256"] = "0" * 64
    elif mutation == "prim":
        rows[3]["prim_path"] = rows[1]["prim_path"]
    elif mutation in {"command_namespace", "backend_state_key"}:
        rows[3][mutation] = rows[1][mutation]
    elif mutation == "capabilities":
        rows[1]["public_declaration"]["public_capabilities"] = []
    else:
        rows[-1]["public_id"] = rows[-2]["public_id"]
    with pytest.raises(ValueError):
        verify_configuration_readback(deployment, observed)


def test_public_route_capacity_cannot_be_overridden_in_overlay(inputs):
    public, overlay, _ = inputs
    overlay["route_bindings"][0]["public_route"]["capacity"] = 99
    with pytest.raises(ValueError, match="public declaration"):
        compile_goal_deployment(public, overlay)


def test_boolean_does_not_alias_numeric_public_capacity(inputs):
    public, overlay, _ = inputs
    row = next(r for r in overlay["route_bindings"] if r["public_route"]["capacity"] == 1)
    row["public_route"]["capacity"] = True
    with pytest.raises(ValueError, match="public declaration"):
        compile_goal_deployment(public, overlay)
