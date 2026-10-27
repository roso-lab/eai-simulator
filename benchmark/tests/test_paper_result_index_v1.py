from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmark.paper_result_index_v1 import (
    CELL_MAP_SCHEMA,
    CONDITION_REGISTER_SCHEMA,
    FORMAL_GATES_FALSE,
    IMPLEMENTATION_REGISTER_SCHEMA,
    PaperResultIndexError,
    REQUIRED_DIRECTORY_SCHEMA,
    ROW_SCHEMA,
    STATISTICAL_BINDING_SCHEMA,
    STATISTICAL_REPORT_SCHEMA,
    build_result_rows,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    canonical_digest,
    read_result_index,
    resolve_manuscript_cell,
    result_key_digest,
    validate_manuscript_cell_map,
    validate_required_directory,
    validate_result_row,
    verify_manuscript_cells,
    verify_result_index,
    write_result_index,
)


PAPER = "a" * 40
HEX = "b" * 64


def tagged(value: str) -> dict:
    return {"status": "value", "value": value}


def na(reason: str = "registered structural N/A") -> dict:
    return {"status": "not_applicable", "reason": reason}


def identity(*, outcome: str = "D2.nominal", condition: str = "condition-1") -> dict:
    result = {
        "implementation_id": "impl-1",
        "comparison_condition_id": condition,
        "task_family": "warehouse",
        "domain": "service",
        "outcome_id": outcome,
        "estimator_version": "estimator-v1",
    }
    for field in (
        "primitive", "difficulty", "factor_pair", "factor_level_a", "factor_level_b",
        "checkpoint_round", "event_round", "untreated_history_id", "event_target",
        "availability_operation", "notification_policy", "restoration_policy", "event",
        "horizon", "source_direction", "target_direction", "oriented_endpoint_scale",
        "scale_regime", "team_composition", "branch",
    ):
        result[field] = tagged(f"{field}-value")
    return result


def implementation_register() -> dict:
    return {
        "schema": IMPLEMENTATION_REGISTER_SCHEMA,
        "paper_commit": PAPER,
        "implementations": [{
            "implementation_id": "impl-1",
            "display_name": "Implementation 1",
            "primary_order": 0,
            "source_digest": "1" * 64,
            "model_digest": "2" * 64,
            "adapter_digest": "3" * 64,
            "runtime_digest": "4" * 64,
            "cognition": "centralized",
            "comparison_group": "primary",
            "information_rights": "registered observations",
            "training_access": "none",
            "decomposition_policy": "native",
            "supported_task_subsets": ["warehouse"],
            "supported_disturbances": ["nominal"],
        }],
    }


def condition_register() -> dict:
    return {
        "schema": CONDITION_REGISTER_SCHEMA,
        "paper_commit": PAPER,
        "conditions": [{
            "condition_id": "condition-1",
            "implementation_id": "impl-1",
            "prompt_digest": "5" * 64,
            "model_digest": "6" * 64,
            "tool_digest": "7" * 64,
            "initial_memory_digest": "8" * 64,
            "adapter_digest": "9" * 64,
            "observation_rights": "registered observations",
            "budget_policy": "fixed budget",
            "runtime_policy": "isolated",
            "reset_policy": "episode reset",
        }],
    }


def support() -> dict:
    return {
        "generated_base_instances": 10,
        "infrastructure_valid_base_instances": 9,
        "metric_eligible_base_instances": 8,
        "paired_instances": 8,
        "common_support_instances": 8,
        "planned_coverage_fraction": 1.0,
        "observed_coverage_fraction": 0.8,
        "repeated_trajectories": 24,
        "event_count": 4,
        "competing_event_count": 1,
        "censoring_count": 2,
        "numerator": 6,
        "denominator": 8,
    }


def execution() -> dict:
    return {
        "episode_reset_method": "fresh process",
        "permitted_history_digest": "c" * 64,
        "initialization_configuration_digest": "d" * 64,
        "execution_audit_version": "audit-v1",
    }


def extension() -> dict:
    return {"kind": "not_applicable", "reason": "registered structural N/A for D2"}


def directory(*, disposition: str = "required", allow_range: bool = True) -> dict:
    return {
        "schema": REQUIRED_DIRECTORY_SCHEMA,
        "paper_commit": PAPER,
        "entries": [{
            "identity": identity(),
            "execution": execution(),
            "support": support(),
            "dimension_extension": extension(),
            "disposition": disposition,
            "reason": "registered required paper cell" if disposition == "required" else f"registered {disposition}",
            "allow_range_display": allow_range,
        }],
    }


def empty_bindings() -> dict:
    return {
        "schema": STATISTICAL_BINDING_SCHEMA,
        "paper_commit": PAPER,
        "scorebook_digest": HEX,
        "bindings": [],
    }


def ref(root: Path, relative: str) -> dict:
    data = (root / relative).read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def bound_report(root: Path, *, status: str = "valid", diagnostic: bool = False) -> dict:
    key_digest = result_key_digest(identity())
    input_digest = "e" * 64
    write_json(root, "evidence/input.json", {"input_digest": input_digest, "payload": "registered"})
    ledger = b"sealed event ledger\n"
    (root / "evidence/ledger.bin").write_bytes(ledger)
    if status == "valid":
        estimate = {"status": "valid", "point": 75.0, "lower": 70.0, "upper": 80.0, "interval_method": "cluster_percentile", "reason": "observed_estimate"}
    elif status == "range":
        estimate = {"status": "range", "point": None, "lower": 60.0, "upper": 80.0, "interval_method": "identification_range", "reason": "observed_partial_identification"}
    else:
        estimate = {"status": status, "point": 99.0, "lower": 98.0, "upper": 100.0, "interval_method": "diagnostic_only", "reason": "numerical_instability"}
    report = {
        "schema": "heterocoord.results-pipeline.v1" if diagnostic else STATISTICAL_REPORT_SCHEMA,
        "paper_commit": PAPER,
        "scorebook_digest": HEX,
        "input_digest": input_digest,
        "implementation_id": "impl-1",
        "comparison_condition_id": "condition-1",
        "result_key_digest": key_digest,
        "score_id": "D2",
        "outcome_id": "D2.nominal",
        "estimator_version": "estimator-v1",
        "evidence_scope": "diagnostic_only" if diagnostic else "paper_result_candidate",
        "formal_gates": dict(FORMAL_GATES_FALSE),
        "estimate": estimate,
        "support": support(),
        "event_ledger_digest": hashlib.sha256(ledger).hexdigest(),
    }
    write_json(root, "evidence/report.json", report)
    binding = {
        "result_key_digest": key_digest,
        "identity": identity(),
        "implementation_id": "impl-1",
        "comparison_condition_id": "condition-1",
        "score_id": "D2",
        "outcome_id": "D2.nominal",
        "estimator_version": "estimator-v1",
        "expected_input_digest": input_digest,
        "report_ref": ref(root, "evidence/report.json"),
        "input_ref": ref(root, "evidence/input.json"),
        "event_ledger_ref": ref(root, "evidence/ledger.bin"),
    }
    return {
        "schema": STATISTICAL_BINDING_SCHEMA,
        "paper_commit": PAPER,
        "scorebook_digest": HEX,
        "bindings": [binding],
    }


def build_rows(tmp_path: Path, *, bindings: dict | None = None, required: dict | None = None):
    return build_result_rows(
        implementation_register=implementation_register(),
        condition_register=condition_register(),
        required_directory=required or directory(),
        statistical_report_binding=bindings or empty_bindings(),
        root=tmp_path,
    )


def test_missing_row_round_trip_is_explicit_and_all_gates_false(tmp_path: Path):
    rows = build_rows(tmp_path)
    assert rows[0]["estimate"] == {
        "entry_state": "missing",
        "status": "incomplete",
        "point": None,
        "lower": None,
        "upper": None,
        "interval_method": None,
        "reason": "missing_registered_evidence",
    }
    index = tmp_path / "paper-result-index.v1.jsonl"
    manifest = write_result_index(
        rows,
        index,
        implementation_register=implementation_register(),
        condition_register=condition_register(),
        required_directory=directory(),
        root=tmp_path,
    )
    assert manifest["formal_gates"] == FORMAL_GATES_FALSE
    verified = verify_result_index(
        index,
        Path(str(index) + ".manifest.json"),
        root=tmp_path,
        required_directory=directory(),
        implementation_register=implementation_register(),
        condition_register=condition_register(),
    )
    assert verified == rows == read_result_index(index)


@pytest.mark.parametrize(
    ("disposition", "state", "status"),
    [("unsupported", "unsupported", "na"), ("structural_na", "structural_na", "na")],
)
def test_registered_absence_states_are_preserved(tmp_path: Path, disposition: str, state: str, status: str):
    required = directory(disposition=disposition, allow_range=False)
    row = build_rows(tmp_path, required=required)[0]
    assert (row["estimate"]["entry_state"], row["estimate"]["status"]) == (state, status)


def test_valid_bound_report_populates_observed_row(tmp_path: Path):
    rows = build_rows(tmp_path, bindings=bound_report(tmp_path))
    assert rows[0]["estimate"]["entry_state"] == "observed"
    assert rows[0]["estimate"]["point"] == 75.0
    assert rows[0]["evidence"]["source_scope"] == "paper_result_candidate"


def test_identification_range_is_not_valid_or_incomplete(tmp_path: Path):
    rows = build_rows(tmp_path, bindings=bound_report(tmp_path, status="range"))
    estimate = rows[0]["estimate"]
    assert estimate["entry_state"] == "observed_range"
    assert estimate["status"] == "range"
    assert estimate["point"] is None
    assert estimate["interval_method"] == "identification_range"
    with pytest.raises(PaperResultIndexError, match="not allowed"):
        build_rows(
            tmp_path,
            bindings=bound_report(tmp_path, status="range"),
            required=directory(allow_range=False),
        )


def test_unstable_discards_numbers_and_becomes_incomplete(tmp_path: Path):
    row = build_rows(tmp_path, bindings=bound_report(tmp_path, status="unstable"))[0]
    assert row["estimate"] == {
        "entry_state": "incomplete",
        "status": "incomplete",
        "point": None,
        "lower": None,
        "upper": None,
        "interval_method": None,
        "reason": "incomplete_unstable",
    }


def test_results_pipeline_and_diagnostic_only_are_never_paper_evidence(tmp_path: Path):
    with pytest.raises(PaperResultIndexError, match="not a statistical paper report"):
        build_rows(tmp_path, bindings=bound_report(tmp_path, diagnostic=True))


def test_binding_recomputes_report_bytes_and_rejects_stale_digest(tmp_path: Path):
    bindings = bound_report(tmp_path)
    (tmp_path / "evidence/report.json").write_bytes(b"{}\n")
    with pytest.raises(PaperResultIndexError, match="does not match bytes"):
        build_rows(tmp_path, bindings=bindings)


def test_binding_rejects_caller_authored_result_key_digest(tmp_path: Path):
    bindings = bound_report(tmp_path)
    bindings["bindings"][0]["result_key_digest"] = "f" * 64
    with pytest.raises(PaperResultIndexError, match="caller-authored or stale"):
        build_rows(tmp_path, bindings=bindings)


def test_one_report_cannot_populate_two_full_result_keys(tmp_path: Path):
    bindings = bound_report(tmp_path)
    required = directory()
    second_entry = deepcopy(required["entries"][0])
    second_entry["identity"]["outcome_id"] = "D3.aggregate"
    required["entries"].append(second_entry)
    second_binding = deepcopy(bindings["bindings"][0])
    second_binding["identity"] = deepcopy(second_entry["identity"])
    second_binding["outcome_id"] = "D3.aggregate"
    second_binding["result_key_digest"] = result_key_digest(second_binding["identity"])
    bindings["bindings"].append(second_binding)
    with pytest.raises(PaperResultIndexError, match="cannot bind multiple result keys"):
        build_rows(tmp_path, bindings=bindings, required=required)


def test_wrong_implementation_condition_pair_fails_closed():
    row = {
        "schema": ROW_SCHEMA,
        "identity": identity(condition="condition-other"),
        "execution": execution(),
        "support": support(),
        "estimate": {"entry_state": "missing", "status": "incomplete", "point": None, "lower": None, "upper": None, "interval_method": None, "reason": "missing_registered_evidence"},
        "evidence": {"event_ledger_digest": None, "input_ref": None, "report_ref": None, "source_scope": "none"},
        "dimension_extension": extension(),
    }
    with pytest.raises(PaperResultIndexError, match="unknown implementation or condition"):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())


def test_duplicate_required_key_and_duplicate_jsonl_key_fail_closed(tmp_path: Path):
    required = directory()
    required["entries"].append(deepcopy(required["entries"][0]))
    with pytest.raises(PaperResultIndexError, match="duplicate key"):
        validate_required_directory(required, implementation_register=implementation_register(), condition_register=condition_register())
    row = build_rows(tmp_path)[0]
    with pytest.raises(PaperResultIndexError, match="duplicate result key"):
        canonical_jsonl_bytes([row, row])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_nonfinite_and_bool_as_number_fail_closed(tmp_path: Path, bad):
    row = deepcopy(build_rows(tmp_path)[0])
    row["support"]["numerator"] = bad
    with pytest.raises(PaperResultIndexError):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())


def test_nonobserved_numeric_fields_and_empty_na_are_rejected(tmp_path: Path):
    row = deepcopy(build_rows(tmp_path)[0])
    row["estimate"]["point"] = 0.0
    with pytest.raises(PaperResultIndexError, match="must not contain numerical"):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())
    row = deepcopy(build_rows(tmp_path)[0])
    row["identity"]["primitive"] = {"status": "not_applicable", "reason": ""}
    with pytest.raises(PaperResultIndexError, match="non-empty trimmed"):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())


def test_d5_and_d6_extensions_reject_missing_identity_and_global_fallback(tmp_path: Path):
    row = deepcopy(build_rows(tmp_path)[0])
    row["dimension_extension"] = {"kind": "D5", "family_id": "f"}
    with pytest.raises(PaperResultIndexError, match="schema mismatch"):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())
    row = deepcopy(build_rows(tmp_path)[0])
    row["dimension_extension"] = {
        "kind": "D6",
        "reference_coordinator_version": "v1",
        "calibration_batch": "batch-1",
        "per_instance_reference_scales": [],
        "reference_bits": 1.0,
    }
    with pytest.raises(PaperResultIndexError, match="schema mismatch"):
        validate_result_row(row, implementation_register=implementation_register(), condition_register=condition_register())


def cell_map() -> dict:
    return {
        "schema": CELL_MAP_SCHEMA,
        "paper_commit": PAPER,
        "column_order": ["primary"],
        "cells": [{
            "cell_id": "table-1:r1:primary",
            "row_id": "table-1:r1",
            "column_id": "primary",
            "selector": {
                "implementation_id": "impl-1",
                "comparison_condition_id": "condition-1",
                "outcome_id": "D2.nominal",
                "estimator_version": "estimator-v1",
            },
            "display_transform": "percent_1dp",
            "allow_range": True,
        }],
    }


def test_cell_map_resolves_exactly_one_and_rejects_zero_or_many(tmp_path: Path):
    rows = build_rows(tmp_path)
    mapping = cell_map()
    assert verify_manuscript_cells(mapping, rows, required_directory=directory()) == rows
    missing = deepcopy(mapping["cells"][0])
    missing["selector"]["outcome_id"] = "D9"
    with pytest.raises(PaperResultIndexError, match="resolved to 0"):
        resolve_manuscript_cell(missing, rows)
    with pytest.raises(PaperResultIndexError, match="resolved to 2"):
        resolve_manuscript_cell(mapping["cells"][0], [rows[0], deepcopy(rows[0])])


def test_cell_map_rejects_column_order_drift_and_unknown_transform():
    mapping = cell_map()
    mapping["column_order"] = ["secondary", "primary"]
    with pytest.raises(PaperResultIndexError, match="column order drift"):
        validate_manuscript_cell_map(mapping, required_directory=directory())
    mapping = cell_map()
    mapping["cells"][0]["display_transform"] = "invented"
    with pytest.raises(PaperResultIndexError, match="unregistered"):
        validate_manuscript_cell_map(mapping, required_directory=directory())


def test_write_is_no_overwrite_and_index_tamper_is_detected(tmp_path: Path):
    rows = build_rows(tmp_path)
    index = tmp_path / "index.jsonl"
    write_result_index(rows, index, implementation_register=implementation_register(), condition_register=condition_register(), required_directory=directory(), root=tmp_path)
    with pytest.raises(PaperResultIndexError, match="overwrite"):
        write_result_index(rows, index, implementation_register=implementation_register(), condition_register=condition_register(), required_directory=directory(), root=tmp_path)
    index.write_bytes(index.read_bytes() + b"\n")
    with pytest.raises(PaperResultIndexError, match="do not match manifest"):
        verify_result_index(index, Path(str(index) + ".manifest.json"), root=tmp_path, required_directory=directory(), implementation_register=implementation_register(), condition_register=condition_register())


@pytest.mark.parametrize(
    ("disposition", "tampered_state", "tampered_status", "tampered_reason"),
    [
        ("unsupported", "missing", "incomplete", "missing_registered_evidence"),
        ("structural_na", "incomplete", "incomplete", "incomplete_tampered"),
        ("required", "unsupported", "na", "registered unsupported"),
    ],
)
def test_rewritten_index_manifest_cannot_override_directory_disposition(
    tmp_path: Path,
    disposition: str,
    tampered_state: str,
    tampered_status: str,
    tampered_reason: str,
):
    required = directory(disposition=disposition, allow_range=False)
    rows = build_rows(tmp_path, required=required)
    index = tmp_path / f"{disposition}.jsonl"
    write_result_index(rows, index, implementation_register=implementation_register(), condition_register=condition_register(), required_directory=required, root=tmp_path)
    tampered = deepcopy(rows[0])
    tampered["estimate"] = {
        "entry_state": tampered_state,
        "status": tampered_status,
        "point": None,
        "lower": None,
        "upper": None,
        "interval_method": None,
        "reason": tampered_reason,
    }
    data = canonical_jsonl_bytes([tampered])
    index.write_bytes(data)
    manifest_path = Path(str(index) + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["index"] = {"path": index.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest["required_directory_digest"] = canonical_digest(required)
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(PaperResultIndexError, match="disagrees with required-directory disposition"):
        verify_result_index(index, manifest_path, root=tmp_path, required_directory=required, implementation_register=implementation_register(), condition_register=condition_register())


def test_index_writer_rejects_destination_escape_and_symlink_parent(tmp_path: Path):
    rows = build_rows(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-index.jsonl"
    with pytest.raises(PaperResultIndexError, match="escapes declared root"):
        write_result_index(rows, outside, implementation_register=implementation_register(), condition_register=condition_register(), required_directory=directory(), root=tmp_path)
    real_parent = tmp_path / "real-output"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-output"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(PaperResultIndexError, match="symlink parent"):
        write_result_index(rows, linked_parent / "index.jsonl", implementation_register=implementation_register(), condition_register=condition_register(), required_directory=directory(), root=tmp_path)


def test_binding_rejects_symlink_and_root_escape(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes(b"{}\n")
    link = tmp_path / "linked.json"
    link.symlink_to(outside)
    bindings = bound_report(tmp_path)
    bindings["bindings"][0]["report_ref"] = {
        "path": "linked.json",
        "size": outside.stat().st_size,
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }
    with pytest.raises(PaperResultIndexError, match="non-symlink"):
        build_rows(tmp_path, bindings=bindings)
    bindings["bindings"][0]["report_ref"]["path"] = "../outside.json"
    with pytest.raises(PaperResultIndexError, match="safe root-relative"):
        build_rows(tmp_path, bindings=bindings)
