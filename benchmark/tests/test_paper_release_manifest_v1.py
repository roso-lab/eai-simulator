from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmark.paper_release_manifest_v1 import (
    PaperReleaseManifestError,
    REQUIRED_ROLES,
    RELEASE_MANIFEST_SCHEMA,
    STATISTICAL_REPORT_SET_SCHEMA,
    build_paper_release_manifest,
    verify_paper_release_manifest,
)
from benchmark.paper_result_index_v1 import (
    FORMAL_GATES_FALSE,
    INDEX_MANIFEST_SCHEMA,
    STATISTICAL_EVENT_LEDGER_SCHEMA,
    STATISTICAL_BINDING_SCHEMA,
    STATISTICAL_INPUT_SCHEMA,
    STATISTICAL_REPORT_SCHEMA,
    canonical_json_bytes,
    encode_reducer_output,
    result_key_digest,
    reducer_implementation_digest,
    reducer_method_for_score,
    reducer_payload_digest,
    reducer_run_links,
    statistical_input_digest,
)


PAPER = "a" * 40


def write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def ref(root: Path, relative: str) -> dict:
    data = (root / relative).read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def identity(score_id: str, implementation_id: str = "impl-1") -> dict:
    value = {
        "implementation_id": implementation_id,
        "comparison_condition_id": "condition-1",
        "task_family": "aggregate",
        "domain": "aggregate",
        "outcome_id": score_id,
        "estimator_version": "estimator-v1",
    }
    for field in (
        "primitive", "difficulty", "factor_pair", "factor_level_a", "factor_level_b",
        "checkpoint_round", "event_round", "untreated_history_id", "event_target",
        "availability_operation", "notification_policy", "restoration_policy", "event",
        "horizon", "source_direction", "target_direction", "oriented_endpoint_scale",
        "scale_regime", "team_composition", "branch",
    ):
        value[field] = {"status": "not_applicable", "reason": "registered aggregate result"}
    return value


def support() -> dict:
    return {
        "generated_base_instances": 10,
        "infrastructure_valid_base_instances": 10,
        "metric_eligible_base_instances": 10,
        "paired_instances": {"status": "observed", "value": 1},
        "common_support_instances": {"status": "observed", "value": 1},
        "planned_coverage_fraction": 1.0,
        "observed_coverage_fraction": 1.0,
        "repeated_trajectories": 30,
        "event_count": {"status": "observed", "value": 1},
        "competing_event_count": {"status": "observed", "value": 0},
        "censoring_count": {"status": "observed", "value": 0},
        "numerator": 8,
        "denominator": 10,
    }


def release_fixture(
    root: Path,
    *,
    implementations: tuple[str, ...] = ("impl-1",),
    dimensions: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
) -> dict[str, str]:
    (root / "paper-commit.txt").write_text(PAPER + "\n", encoding="utf-8")
    (root / "paper-result-index.v1.jsonl").write_text("{}\n", encoding="utf-8")
    ordinary_roles = REQUIRED_ROLES - {
        "paper_commit",
        "result_index",
        "result_index_manifest",
        "statistical_report_binding",
        "statistical_reports",
    }
    paths: dict[str, str] = {
        "paper_commit": "paper-commit.txt",
        "result_index": "paper-result-index.v1.jsonl",
    }
    for role in sorted(ordinary_roles):
        relative = f"manifests/{role}.json"
        write_json(root, relative, {"schema": f"heterocoord.{role}.v1", "paper_commit": PAPER})
        paths[role] = relative
    scorebook_digest = hashlib.sha256((root / paths["scorebook"]).read_bytes().rstrip(b"\n")).hexdigest()
    bindings = []
    report_set = []
    for implementation_id in implementations:
        for dimension in dimensions:
            score_id = f"D{dimension}"
            result_identity = identity(score_id, implementation_id)
            key = result_key_digest(result_identity)
            report_path = f"evidence/{implementation_id}/{score_id}/report.json"
            input_path = f"evidence/{implementation_id}/{score_id}/input.json"
            ledger_path = f"evidence/{implementation_id}/{score_id}/ledger.json"
            payload = {
                "runs": [{
                    "run_id": f"{implementation_id}-{score_id}-run",
                    "instance_id": f"{implementation_id}-{score_id}-instance",
                    "repeat_id": "1", "evidence_digest": "registered-evidence",
                    "values": [0.8], "status": "observed",
                }],
                "inference": {"alpha": 0.05, "resamples": 100},
            }
            method = reducer_method_for_score(score_id)
            implementation_digest = reducer_implementation_digest(score_id)
            payload_digest = reducer_payload_digest(payload)
            instance_id = payload["runs"][0]["instance_id"]
            run_id = payload["runs"][0]["run_id"]
            write_json(root, ledger_path, {
                "schema": STATISTICAL_EVENT_LEDGER_SCHEMA,
                "paper_commit": PAPER,
                "score_id": score_id,
                "implementation_id": implementation_id,
                "comparison_condition_id": "condition-1",
                "outcome_id": score_id,
                "estimator_version": "estimator-v1",
                "result_key_digest": key,
                "evidence_scope": "paper_result_candidate",
                "reducer_method": method,
                "reducer_implementation_digest": implementation_digest,
                "reducer_payload_digest": payload_digest,
                "run_links": reducer_run_links(payload),
                "support_registry": {
                    "pairing": {"status": "available", "paired_instance_ids": [instance_id]},
                    "common_support": {"status": "available", "common_support_instance_ids": [instance_id]},
                    "events": {"status": "available", "event_run_ids": [run_id], "competing_event_run_ids": [], "censoring_run_ids": []},
                },
            })
            ledger_ref = ref(root, ledger_path)
            reducer_input_digest = hashlib.sha256(f"reducer-{implementation_id}-{score_id}".encode()).hexdigest()
            input_value = {
                "schema": STATISTICAL_INPUT_SCHEMA,
                "paper_commit": PAPER,
                "score_id": score_id,
                "implementation_id": implementation_id,
                "comparison_condition_id": "condition-1",
                "outcome_id": score_id,
                "estimator_version": "estimator-v1",
                "result_key_digest": key,
                "evidence_scope": "paper_result_candidate",
                "reducer_method": method,
                "reducer_implementation_digest": implementation_digest,
                "reducer_payload": payload,
                "reducer_payload_digest": payload_digest,
                "reducer_input_digest": reducer_input_digest,
                "event_ledger_ref": ledger_ref,
            }
            input_value["input_digest"] = statistical_input_digest(input_value)
            input_digest = input_value["input_digest"]
            write_json(root, input_path, input_value)
            write_json(root, report_path, {
                "schema": STATISTICAL_REPORT_SCHEMA,
                "paper_commit": PAPER,
                "scorebook_digest": scorebook_digest,
                "input_digest": input_digest,
                "implementation_id": implementation_id,
                "comparison_condition_id": "condition-1",
                "result_key_digest": key,
                "score_id": score_id,
                "outcome_id": score_id,
                "estimator_version": "estimator-v1",
                "evidence_scope": "paper_result_candidate",
                "formal_gates": dict(FORMAL_GATES_FALSE),
                "estimate": {"status": "valid", "point": 80.0, "lower": 75.0, "upper": 85.0, "interval_method": "cluster_percentile", "reason": "observed_estimate"},
                "support": support(),
                "event_ledger_digest": ledger_ref["sha256"],
                "reducer_method": method,
                "reducer_implementation_digest": implementation_digest,
                "reducer_payload_digest": payload_digest,
                "reducer_input_digest": reducer_input_digest,
                "reducer_output": encode_reducer_output({
                    "score_id": score_id, "paper_commit": PAPER,
                    "scorebook_digest": scorebook_digest,
                    "input_digest": reducer_input_digest, "status": "valid",
                }),
            })
            report_ref = ref(root, report_path)
            bindings.append({
                "result_key_digest": key,
                "identity": result_identity,
                "implementation_id": implementation_id,
                "comparison_condition_id": "condition-1",
                "score_id": score_id,
                "outcome_id": score_id,
                "estimator_version": "estimator-v1",
                "expected_input_digest": input_digest,
                "report_ref": report_ref,
                "input_ref": ref(root, input_path),
                "event_ledger_ref": ledger_ref,
            })
            report_set.append({"result_key_digest": key, "report_ref": report_ref})
    binding_path = "manifests/statistical-report-binding.v1.json"
    write_json(root, binding_path, {
        "schema": STATISTICAL_BINDING_SCHEMA,
        "paper_commit": PAPER,
        "scorebook_digest": scorebook_digest,
        "bindings": bindings,
    })
    report_set_path = "manifests/statistical-report-set.v1.json"
    write_json(root, report_set_path, {
        "schema": STATISTICAL_REPORT_SET_SCHEMA,
        "paper_commit": PAPER,
        "reports": report_set,
    })
    paths["statistical_report_binding"] = binding_path
    paths["statistical_reports"] = report_set_path
    result_entry = ref(root, paths["result_index"])
    index_manifest_path = "manifests/result_index_manifest.json"
    write_json(root, index_manifest_path, {
        "schema": INDEX_MANIFEST_SCHEMA,
        "paper_commit": PAPER,
        "index": {"path": Path(result_entry["path"]).name, "size": result_entry["size"], "sha256": result_entry["sha256"]},
        "row_count": len(bindings),
        "required_directory_digest": ref(root, paths["required_result_directory"])["sha256"],
        "formal_gates": dict(FORMAL_GATES_FALSE),
    })
    paths["result_index_manifest"] = index_manifest_path
    assert set(paths) == REQUIRED_ROLES
    return paths


def test_release_manifest_round_trip_binds_all_nested_evidence(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    destination = tmp_path / "paper-release-manifest.v1.json"
    manifest = build_paper_release_manifest(
        root=tmp_path,
        paper_commit=PAPER,
        dependencies=dependencies,
        destination=destination,
    )
    assert manifest["schema"] == RELEASE_MANIFEST_SCHEMA
    assert manifest["formal_gates"] == FORMAL_GATES_FALSE
    assert len(manifest["bound_evidence"]) == 18
    assert verify_paper_release_manifest(destination, root=tmp_path) == manifest


def test_release_manifest_supports_multi_system_matrix(tmp_path: Path):
    dependencies = release_fixture(tmp_path, implementations=("impl-1", "impl-2"))
    destination = tmp_path / "paper-release-manifest.v1.json"
    manifest = build_paper_release_manifest(
        root=tmp_path,
        paper_commit=PAPER,
        dependencies=dependencies,
        destination=destination,
    )
    assert len(manifest["bound_evidence"]) == 36
    bound_keys = list(manifest["bound_evidence"])
    report_roles = {key.split(":", 1)[1] for key in bound_keys if key.startswith("report:")}
    per_system = {
        implementation: {result_key_digest(identity(f"D{i}", implementation)) for i in range(1, 7)}
        for implementation in ("impl-1", "impl-2")
    }
    assert per_system["impl-1"].isdisjoint(per_system["impl-2"])
    assert report_roles == per_system["impl-1"] | per_system["impl-2"]
    assert verify_paper_release_manifest(destination, root=tmp_path) == manifest


@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_dependency_roles_are_exact(tmp_path: Path, operation: str):
    dependencies = release_fixture(tmp_path)
    if operation == "missing":
        dependencies.pop("scorebook")
    else:
        dependencies["invented"] = "paper-commit.txt"
    with pytest.raises(PaperReleaseManifestError, match="role mismatch"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_root_escape_absolute_and_symlink_are_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    dependencies["scorebook"] = "../outside.json"
    with pytest.raises(PaperReleaseManifestError, match="root-relative"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")
    dependencies = release_fixture(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes(canonical_json_bytes({"paper_commit": PAPER}))
    link = tmp_path / "linked-scorebook.json"
    link.symlink_to(outside)
    dependencies["scorebook"] = "linked-scorebook.json"
    with pytest.raises(PaperReleaseManifestError, match="non-symlink"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_top_level_dependency_mutation_is_detected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    destination = tmp_path / "release.json"
    build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=destination)
    write_json(tmp_path, dependencies["scorebook"], {"schema": "changed", "paper_commit": PAPER})
    with pytest.raises(PaperReleaseManifestError, match="changed after"):
        verify_paper_release_manifest(destination, root=tmp_path)


def test_nested_report_mutation_is_detected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    destination = tmp_path / "release.json"
    build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=destination)
    report = tmp_path / "evidence/impl-1/D4/report.json"
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(PaperReleaseManifestError, match="does not match bytes"):
        verify_paper_release_manifest(destination, root=tmp_path)


def test_wrong_paper_commit_dependency_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    (tmp_path / dependencies["paper_commit"]).write_text("f" * 40 + "\n", encoding="utf-8")
    with pytest.raises(PaperReleaseManifestError, match="contents do not match"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_nested_dependency_with_wrong_paper_commit_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    write_json(tmp_path, dependencies["manuscript_cell_map"], {"schema": "heterocoord.manuscript-cell-map.v1", "paper_commit": "f" * 40})
    with pytest.raises(PaperReleaseManifestError, match="different paper commit"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_scorebook_identity_mismatch_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    binding_path = tmp_path / dependencies["statistical_report_binding"]
    binding = json.loads(binding_path.read_text())
    binding["scorebook_digest"] = "f" * 64
    binding_path.write_bytes(canonical_json_bytes(binding))
    with pytest.raises(PaperReleaseManifestError, match="scorebook digest mismatch"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_input_identity_mismatch_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    binding_path = tmp_path / dependencies["statistical_report_binding"]
    binding = json.loads(binding_path.read_text())
    input_path = binding["bindings"][0]["input_ref"]["path"]
    input_value = json.loads((tmp_path / input_path).read_text())
    input_value["input_digest"] = "f" * 64
    (tmp_path / input_path).write_bytes(canonical_json_bytes(input_value))
    binding["bindings"][0]["input_ref"] = ref(tmp_path, input_path)
    binding_path.write_bytes(canonical_json_bytes(binding))
    with pytest.raises(PaperReleaseManifestError, match="input digest identity mismatch"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_result_index_and_index_manifest_identity_mismatch_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    manifest_path = tmp_path / dependencies["result_index_manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["index"]["sha256"] = "f" * 64
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(PaperReleaseManifestError, match="index manifest disagree"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_report_set_missing_result_key_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"].pop()
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="disagree"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_report_set_extra_result_key_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"].append({"result_key_digest": "f" * 64, "report_ref": deepcopy(report_set["reports"][0]["report_ref"])})
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="disagree"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_report_set_duplicate_result_key_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"].append(deepcopy(report_set["reports"][0]))
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="duplicate result keys"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_binding_duplicate_result_key_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    binding_path = tmp_path / dependencies["statistical_report_binding"]
    binding = json.loads(binding_path.read_text())
    binding["bindings"].append(deepcopy(binding["bindings"][0]))
    binding_path.write_bytes(canonical_json_bytes(binding))
    with pytest.raises(PaperReleaseManifestError, match="duplicate result keys"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_bindings_missing_a_dimension_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path, dimensions=(1, 2, 3, 4, 6))
    with pytest.raises(PaperReleaseManifestError, match="cover D1-D6"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_self_consistent_binding_with_score_id_outside_d1_d6_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    binding_path = tmp_path / dependencies["statistical_report_binding"]
    binding = json.loads(binding_path.read_text())
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    scorebook_digest = hashlib.sha256((tmp_path / dependencies["scorebook"]).read_bytes().rstrip(b"\n")).hexdigest()
    # A seventh, otherwise fully self-consistent binding labelled D7.
    result_identity = identity("D7")
    key = result_key_digest(result_identity)
    input_digest = hashlib.sha256(b"input-impl-1-D7").hexdigest()
    input_path = "evidence/impl-1/D7/input.json"
    ledger_path = "evidence/impl-1/D7/ledger.bin"
    report_path = "evidence/impl-1/D7/report.json"
    write_json(tmp_path, input_path, {"schema": "heterocoord.statistical-input.v1", "paper_commit": PAPER, "score_id": "D7", "input_digest": input_digest})
    (tmp_path / ledger_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ledger_path).write_bytes(b"sealed-impl-1-D7\n")
    write_json(tmp_path, report_path, {
        "schema": STATISTICAL_REPORT_SCHEMA,
        "paper_commit": PAPER,
        "scorebook_digest": scorebook_digest,
        "input_digest": input_digest,
        "implementation_id": "impl-1",
        "comparison_condition_id": "condition-1",
        "result_key_digest": key,
        "score_id": "D7",
        "outcome_id": "D7",
        "estimator_version": "estimator-v1",
        "evidence_scope": "paper_result_candidate",
        "formal_gates": dict(FORMAL_GATES_FALSE),
        "estimate": {"status": "valid", "point": 80.0, "lower": 75.0, "upper": 85.0, "interval_method": "cluster_percentile", "reason": "observed_estimate"},
        "support": support(),
        "event_ledger_digest": hashlib.sha256((tmp_path / ledger_path).read_bytes()).hexdigest(),
    })
    report_ref = ref(tmp_path, report_path)
    binding["bindings"].append({
        "result_key_digest": key,
        "identity": result_identity,
        "implementation_id": "impl-1",
        "comparison_condition_id": "condition-1",
        "score_id": "D7",
        "outcome_id": "D7",
        "estimator_version": "estimator-v1",
        "expected_input_digest": input_digest,
        "report_ref": report_ref,
        "input_ref": ref(tmp_path, input_path),
        "event_ledger_ref": ref(tmp_path, ledger_path),
    })
    binding_path.write_bytes(canonical_json_bytes(binding))
    report_set["reports"].append({"result_key_digest": key, "report_ref": report_ref})
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="outside D1-D6"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_report_set_legacy_score_id_entry_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"][0] = {
        "score_id": "D1",
        "report_ref": report_set["reports"][0]["report_ref"],
    }
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="entry schema mismatch"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_report_set_and_binding_disagreement_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"][0]["report_ref"]["sha256"] = "f" * 64
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="disagree"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_release_manifest_is_no_overwrite(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    destination = tmp_path / "release.json"
    build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=destination)
    with pytest.raises(PaperReleaseManifestError, match="overwrite"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=destination)


def test_release_writer_rejects_destination_escape_and_symlink_parent(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-release.json"
    with pytest.raises(PaperReleaseManifestError, match="escapes declared root"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=outside)
    real_parent = tmp_path / "real-output"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-output"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(PaperReleaseManifestError, match="symlink parent"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=linked_parent / "release.json")


def test_malformed_manifest_and_gate_promotion_fail_closed(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    destination = tmp_path / "release.json"
    manifest = build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=destination)
    malformed = deepcopy(manifest)
    malformed["formal_gates"]["rankable"] = True
    destination.write_bytes(canonical_json_bytes(malformed))
    with pytest.raises(PaperReleaseManifestError, match="must remain false"):
        verify_paper_release_manifest(destination, root=tmp_path)
