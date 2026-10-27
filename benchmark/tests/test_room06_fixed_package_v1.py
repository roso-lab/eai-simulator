from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import benchmark.room06_fixed_package_v1 as package


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inputs(tmp_path: Path):
    semantic_root = tmp_path / "map"
    semantic_root.mkdir()
    (semantic_root / "manifest.json").write_text("{}\n", encoding="utf-8")
    usd = tmp_path / "room_06.usda"
    usd.write_bytes(b"#usda 1.0\ndef Xform \"World\" {}\n")
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps({
        "schema_id": "heterocoord.robot_runtime_roster",
        "robots": [
            {"instance_id": "hc-rt-010", "initial_state_binding": {"initial_state_digest": "a" * 64}},
            {"instance_id": "hc-rt-011", "initial_state_binding": {"initial_state_digest": "b" * 64}},
        ],
    }), encoding="utf-8")
    scene_digest = _sha(usd.read_bytes())
    binding = {
        "schema": "heterocoord.room06-runtime-preflight-input.v1",
        "scene_key": "living_room_06",
        "scene_asset_digest": scene_digest,
        "semantic_map_identity": {"save_id": "s", "semantic_binding_digest": "b", "topology_digest": "t"},
        "transition_handoff_node_id": "caller-must-not-win",
        "selected_robot_ids": ["hc-rt-010", "hc-rt-011"],
        "cross_floor_robot_ids": ["hc-rt-010"],
        "robot_roster_digest": _sha(roster.read_bytes()),
        "task_object_bindings": {"schema": "heterocoord.task-object-bindings.v1", "scene_digest": scene_digest, "objects": []},
        "formal_gates": copy.deepcopy(package.FORMAL_GATES),
    }
    setup = {
        "schema": "heterocoord.room06-task-setup.v1",
        "task_id": "liv-t-04",
        "objects": [
            {"entity_id": "book_batch_01", "definition": {"kind": "books", "count": 6}},
            {"entity_id": "heavy_object", "definition": {"kind": "logical_load", "class": "heavy"}},
        ],
    }
    deployment = {
        "schema": package.DEPLOYMENT_SCHEMA,
        "robots": [
            {"instance_id": "hc-rt-010", "initial_state_digest": "a" * 64, "position_m": [0, 0, 0], "orientation_wxyz": [1, 0, 0, 0]},
            {"instance_id": "hc-rt-011", "initial_state_digest": "b" * 64, "position_m": [1, 0, 0], "orientation_wxyz": [1, 0, 0, 0]},
        ],
    }
    catalog = {"schema_version": "heterocoord.task-catalog.v1", "anchors": [{"anchor_id": "liv-t-04"}]}
    artifact = {"status": "bound_pending_replay", "paper_sha": package.PAPER_SHA, "contracts": [{"anchor_id": "liv-t-04"}]}
    return usd, roster, binding, setup, deployment, catalog, artifact, scene_digest


def _patch_upstreams(monkeypatch, scene_digest: str):
    monkeypatch.setattr(package, "_validate_catalog", lambda value: None)
    monkeypatch.setattr(
        package,
        "require_room06_runtime_preflight",
        lambda **kwargs: {
            "schema": "heterocoord.room06-runtime-preflight.v1",
            "preflight_passed": True,
            "runtime_binding_digest": "c" * 64,
            "transition_handoff_node_id": "transition:stairs",
            "formal_gates": copy.deepcopy(package.FORMAL_GATES),
        },
    )
    monkeypatch.setattr(package, "_select_contract", lambda artifact, catalog: (
        {
            "anchor_id": "liv-t-04",
            "paper_sha": package.PAPER_SHA,
            "status": "candidate_contract_only",
            "oracle_contract": {"oracle_ref": "tools.benchmark_branch.oracle_predicates:judge_anchor"},
        },
        package.canonical_digest({
            "anchor_id": "liv-t-04",
            "paper_sha": package.PAPER_SHA,
            "status": "candidate_contract_only",
            "oracle_contract": {"oracle_ref": "tools.benchmark_branch.oracle_predicates:judge_anchor"},
        }),
    ))
    monkeypatch.setattr(
        package,
        "build_case_manifest",
        lambda **kwargs: {
            "schema": "heterocoord.case-manifest.v1",
            "manifest_digest": "e" * 64,
            "case_id": kwargs["binding"]["case_id"],
            "plan_digest": kwargs["binding"]["plan_digest"],
            "catalog_digest": kwargs["binding"]["catalog_digest"],
            "contract": {"contract_digest": kwargs["binding"]["contract"]["contract_digest"]},
        },
    )


def test_build_derives_transition_and_keeps_missing_d6_diagnostic(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map",
        scene_usd=usd,
        robot_roster=roster,
        preflight_binding=binding,
        task_setup=setup,
        deployment_state=deployment,
        catalog=catalog,
        contract_artifact=artifact,
        diagnostic_inputs=True,
    )
    assert result["plan"]["room06_runtime_binding"]["transition_handoff_node_id"] == "transition:stairs"
    assert result["plan"]["room06_runtime_binding"]["runtime_binding_digest"] == "c" * 64
    assert result["plan"]["oracle_obligation_binding"] == package.ORACLE_OBLIGATION_BINDING
    assert result["plan"]["d6_reference_ready"] is False
    assert result["readiness"]["export_mode"] == "measured_unqualified"
    assert result["readiness"]["formal_gates"] == package.FORMAL_GATES
    assert package.verify_room06_fixed_package(result)["package_digest"] == result["package_digest"]


def test_caller_transition_literal_cannot_override_fresh_preflight(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map",
        scene_usd=usd,
        robot_roster=roster,
        preflight_binding=binding,
        task_setup=setup,
        deployment_state=deployment,
        catalog=catalog,
        contract_artifact=artifact,
        diagnostic_inputs=True,
    )
    assert result["plan"]["room06_runtime_binding"]["transition_handoff_node_id"] != binding["transition_handoff_node_id"]


def test_deployment_requires_exact_selected_ids_and_finite_state(tmp_path):
    _, _, binding, _, deployment, *_ = _inputs(tmp_path)
    deployment["robots"][0]["instance_id"] = "unexpected"
    with pytest.raises(package.Room06FixedPackageError, match="deployment robot IDs"):
        package._deployment(deployment, binding["selected_robot_ids"])


def test_package_digest_and_d3_binding_are_fail_closed(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map",
        scene_usd=usd,
        robot_roster=roster,
        preflight_binding=binding,
        task_setup=setup,
        deployment_state=deployment,
        catalog=catalog,
        contract_artifact=artifact,
        diagnostic_inputs=True,
    )
    tampered = copy.deepcopy(result)
    tampered["plan"]["oracle_obligation_binding"]["obligations"].append({"obligation_id": "hold", "relation": "P3", "predicate_ids": ["heavy_carry_joint_hold"]})
    with pytest.raises(package.Room06FixedPackageError, match="package_digest"):
        package.verify_room06_fixed_package(tampered)


def test_source_byte_change_invalidates_package(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map",
        scene_usd=usd,
        robot_roster=roster,
        preflight_binding=binding,
        task_setup=setup,
        deployment_state=deployment,
        catalog=catalog,
        contract_artifact=artifact,
        diagnostic_inputs=True,
    )
    usd.write_bytes(usd.read_bytes() + b"# changed\n")
    with pytest.raises(package.Room06FixedPackageError, match="source bytes changed"):
        package.verify_room06_fixed_package(result)


def test_d6_reference_is_per_instance_and_content_bound(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    reference = {
        "calibration": [
            {"instance_id": "hc-rt-011", "reference_bits": 20.0, "reference_time": 4.0},
            {"instance_id": "hc-rt-010", "reference_bits": 10.0, "reference_time": 2.0},
        ],
        "calibration_digest": "f" * 64,
    }
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map", scene_usd=usd, robot_roster=roster,
        preflight_binding=binding, task_setup=setup, deployment_state=deployment,
        catalog=catalog, contract_artifact=artifact, d6_reference=reference,
        diagnostic_inputs=True,
    )
    assert result["plan"]["d6_reference_ready"] is True
    assert result["readiness"]["export_mode"] == "production"
    assert [row["instance_id"] for row in result["d6_reference"]["calibration"]] == ["hc-rt-010", "hc-rt-011"]
    assert package.verify_room06_fixed_package(result)["package_digest"] == result["package_digest"]


def test_deployment_digest_must_match_roster(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    deployment["robots"][0]["initial_state_digest"] = "c" * 64
    _patch_upstreams(monkeypatch, scene_digest)
    with pytest.raises(package.Room06FixedPackageError, match="does not match robot roster"):
        package.build_room06_fixed_package(
            semantic_root=tmp_path / "map", scene_usd=usd, robot_roster=roster,
            preflight_binding=binding, task_setup=setup, deployment_state=deployment,
            catalog=catalog, contract_artifact=artifact,
            diagnostic_inputs=True,
        )


def test_verifier_rejects_rehashed_nested_identity_or_provenance(tmp_path, monkeypatch):
    usd, roster, binding, setup, deployment, catalog, artifact, scene_digest = _inputs(tmp_path)
    _patch_upstreams(monkeypatch, scene_digest)
    result = package.build_room06_fixed_package(
        semantic_root=tmp_path / "map", scene_usd=usd, robot_roster=roster,
        preflight_binding=binding, task_setup=setup, deployment_state=deployment,
        catalog=catalog, contract_artifact=artifact,
        diagnostic_inputs=True,
    )
    tampered = copy.deepcopy(result)
    tampered["plan"]["selected_robot_ids"] = ["hc-rt-011", "hc-rt-010"]
    tampered["package_digest"] = package.canonical_digest({k: v for k, v in tampered.items() if k != "package_digest"})
    with pytest.raises(package.Room06FixedPackageError, match="selected_robot_ids"):
        package.verify_room06_fixed_package(tampered)

    tampered = copy.deepcopy(result)
    tampered["source_provenance"].pop()
    tampered["package_digest"] = package.canonical_digest({k: v for k, v in tampered.items() if k != "package_digest"})
    with pytest.raises(package.Room06FixedPackageError, match="exactly one deployment_state"):
        package.verify_room06_fixed_package(tampered)

    tampered = copy.deepcopy(result)
    tampered["source_provenance"].append(copy.deepcopy(tampered["source_provenance"][0]))
    tampered["package_digest"] = package.canonical_digest({k: v for k, v in tampered.items() if k != "package_digest"})
    with pytest.raises(package.Room06FixedPackageError, match="duplicate"):
        package.verify_room06_fixed_package(tampered)
