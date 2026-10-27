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
    STATISTICAL_REPORT_SCHEMA,
    canonical_json_bytes,
    result_key_digest,
)


PAPER = "a" * 40


def write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def ref(root: Path, relative: str) -> dict:
    data = (root / relative).read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def identity(score_id: str) -> dict:
    value = {
        "implementation_id": "impl-1",
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
        "paired_instances": 10,
        "common_support_instances": 10,
        "planned_coverage_fraction": 1.0,
        "observed_coverage_fraction": 1.0,
        "repeated_trajectories": 30,
        "event_count": 10,
        "competing_event_count": 0,
        "censoring_count": 0,
        "numerator": 8,
        "denominator": 10,
    }


def release_fixture(root: Path) -> dict[str, str]:
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
    for index in range(1, 7):
        score_id = f"D{index}"
        result_identity = identity(score_id)
        key = result_key_digest(result_identity)
        input_digest = hashlib.sha256(f"input-{score_id}".encode()).hexdigest()
        report_path = f"evidence/{score_id}/report.json"
        input_path = f"evidence/{score_id}/input.json"
        ledger_path = f"evidence/{score_id}/ledger.bin"
        write_json(root, input_path, {"schema": "heterocoord.statistical-input.v1", "paper_commit": PAPER, "score_id": score_id, "input_digest": input_digest})
        ledger = root / ledger_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_bytes(f"sealed-{score_id}\n".encode())
        write_json(root, report_path, {
            "schema": STATISTICAL_REPORT_SCHEMA,
            "paper_commit": PAPER,
            "scorebook_digest": scorebook_digest,
            "input_digest": input_digest,
            "implementation_id": "impl-1",
            "comparison_condition_id": "condition-1",
            "result_key_digest": key,
            "score_id": score_id,
            "outcome_id": score_id,
            "estimator_version": "estimator-v1",
            "evidence_scope": "paper_result_candidate",
            "formal_gates": dict(FORMAL_GATES_FALSE),
            "estimate": {"status": "valid", "point": 80.0, "lower": 75.0, "upper": 85.0, "interval_method": "cluster_percentile", "reason": "observed_estimate"},
            "support": support(),
            "event_ledger_digest": hashlib.sha256(ledger.read_bytes()).hexdigest(),
        })
        report_ref = ref(root, report_path)
        bindings.append({
            "result_key_digest": key,
            "identity": result_identity,
            "implementation_id": "impl-1",
            "comparison_condition_id": "condition-1",
            "score_id": score_id,
            "outcome_id": score_id,
            "estimator_version": "estimator-v1",
            "expected_input_digest": input_digest,
            "report_ref": report_ref,
            "input_ref": ref(root, input_path),
            "event_ledger_ref": ref(root, ledger_path),
        })
        report_set.append({"score_id": score_id, "report_ref": report_ref})
    binding_path = "manifests/statistical-report-binding.v1.json"
    write_json(root, binding_path, {
        "schema": "heterocoord.statistical-report-binding.v1",
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
        "row_count": 6,
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
    report = tmp_path / "evidence/D4/report.json"
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
    with pytest.raises(PaperReleaseManifestError, match="input identity mismatch"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_result_index_and_index_manifest_identity_mismatch_is_rejected(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    manifest_path = tmp_path / dependencies["result_index_manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["index"]["sha256"] = "f" * 64
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(PaperReleaseManifestError, match="index manifest disagree"):
        build_paper_release_manifest(root=tmp_path, paper_commit=PAPER, dependencies=dependencies, destination=tmp_path / "release.json")


def test_statistical_report_set_must_be_exact_d1_through_d6(tmp_path: Path):
    dependencies = release_fixture(tmp_path)
    report_set_path = tmp_path / dependencies["statistical_reports"]
    report_set = json.loads(report_set_path.read_text())
    report_set["reports"].pop()
    report_set_path.write_bytes(canonical_json_bytes(report_set))
    with pytest.raises(PaperReleaseManifestError, match="exactly six"):
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
