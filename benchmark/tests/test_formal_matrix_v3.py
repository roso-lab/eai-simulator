from __future__ import annotations

from benchmark.formal_matrix_v3 import BaselineQualificationV3, FormalCaseV3, baseline_release_status, formal_case_status
from copy import deepcopy

from algorithm.heterocoord.registry import (
    IMPLEMENTATION_REGISTER_REQUIRED_FIELDS,
    PRIMARY_SYSTEM_COLUMNS,
    REGISTRY,
    primary_registry_records,
)
import hashlib
import pytest


def _case(case_id="c1", status="certified", oracle="deployable"):
    digest = lambda label: hashlib.sha256(label.encode()).hexdigest()
    kwargs = {} if status != "certified" else {
        "catalog_digest": digest("catalog"), "contract_digest": digest("contract"),
        "oracle_proof_digest": digest("proof"), "artifact_manifest_digest": digest("manifest"),
        "common_start_digest": digest("start"), "physical_evidence_digest": digest("physical"),
    }
    return FormalCaseV3("family", case_id, "anchor", "production", status, oracle, "scene", "roster", "topology", "evaluator", **kwargs)


def test_planned_oracle_cases_remain_na():
    result = formal_case_status([_case(status="planned", oracle="not_yet_certified")], min_cases_per_family=1)
    assert result["status"] == "na"


def test_family_floor_is_explicit():
    cases = [_case(str(index)) for index in range(30)]
    assert formal_case_status(cases)["status"] == "ready"


def test_certified_case_requires_bound_contract_oracle_artifacts_and_physics():
    with pytest.raises(ValueError, match="catalog_digest"):
        FormalCaseV3("family", "case", "anchor", "production", "certified", "deployable",
                     "scene", "roster", "topology", "evaluator")
    with pytest.raises(ValueError, match="terminal oracle status"):
        _case(oracle="not_yet_certified")


def _baseline_for_record(record):
    algorithm_id = record["algorithm_id"]
    digest = lambda label: hashlib.sha256(f"{algorithm_id}/{label}".encode()).hexdigest()
    return BaselineQualificationV3(
        algorithm_id,
        record["source_reference"],
        record["adapter_version"],
        "registered-checkpoint",
        "qualified",
        "observation.v3",
        ("C0",),
        *(digest(label) for label in ("system", "task", "team", "budget", "obs", "manifest")),
        record["job_system_id"],
        record["comparison_condition_id"],
        record["information_rights_group"],
        record["code_version"],
        record["source_version"],
        record["model_version"],
    )


def _current_baselines():
    return [_baseline_for_record(item) for item in primary_registry_records()]


def _complete_future_register():
    rows = [deepcopy(item) for item in REGISTRY]
    for item in rows:
        if item["algorithm_id"] not in PRIMARY_SYSTEM_COLUMNS:
            continue
        suffix = item["algorithm_id"]
        item.update({
            "source_reference": f"registered-source::{suffix}",
            "code_version": f"code::{suffix}",
            "source_version": f"source::{suffix}",
            "model_version": f"model::{suffix}",
            "adapter_version": f"adapter::{suffix}",
            "runtime_information": f"qualified-runtime::{suffix}",
            "information_rights": f"frozen-rights::{item['comparison_group']}",
            "information_rights_group": f"rights-group::{item['comparison_group']}",
            "registration_status": "complete",
            "paper_column_coverage_status": "full",
            "comparison_condition_status": "frozen",
        })
        unresolved = ("N/A", "pending", "not_registered", "release_digest_required", "not_yet", "not_qualified")
        for field in IMPLEMENTATION_REGISTER_REQUIRED_FIELDS:
            value = item[field]
            if isinstance(value, list):
                if any(any(token in entry for token in unresolved) for entry in value):
                    item[field] = [f"registered::{field}"]
            elif isinstance(value, str) and any(token in value for token in unresolved):
                item[field] = f"registered::{field}"
    return tuple(rows)


def test_baseline_requires_adapter_checkpoint_physics_and_registered_condition():
    missing = BaselineQualificationV3("Gosrich", "official", None, None, "unsupported", None, ())
    assert baseline_release_status([missing])["status"] == "na"
    assert baseline_release_status([_current_baselines()[-1]])["status"] == "na"


def test_current_exact_roster_remains_na_while_registered_future_roster_can_reach_ready():
    current = baseline_release_status(_current_baselines())
    assert current["status"] == "na"
    assert current["reason"] == "primary_qualification_incomplete"
    assert not any(item["rankable"] for item in current["baselines"].values())

    future = _complete_future_register()
    by_id = {item["algorithm_id"]: item for item in future}
    rows = [_baseline_for_record(by_id[item]) for item in PRIMARY_SYSTEM_COLUMNS]
    result = baseline_release_status(rows, implementation_register=future)
    assert result["status"] == "ready"
    assert result["reason"] == "exact_primary_roster_qualified"
    assert result["expected_order"] == list(PRIMARY_SYSTEM_COLUMNS)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda rows: rows[:-1],
        lambda rows: rows + [BaselineQualificationV3(
            "additional-system", "source", "adapter", "checkpoint", "qualified", "obs", ("C0",)
        )],
        lambda rows: rows[:1] + rows[2:3] + rows[1:2] + rows[3:],
        lambda rows: rows[:4] + [BaselineQualificationV3(
            "Qrainbow", "source", "adapter", "checkpoint", "qualified", "obs", ("C0",)
        )] + rows[5:],
    ),
)
def test_primary_roster_missing_extra_reordered_or_qrainbow_is_na(mutate):
    rows = _current_baselines()
    result = baseline_release_status(mutate(rows))
    assert result["status"] == "na"
    assert result["reason"] == "primary_roster_mismatch"


def test_duplicate_primary_identity_fails_closed():
    rows = _current_baselines()
    with pytest.raises(ValueError, match="duplicate baseline identity"):
        baseline_release_status(rows + [rows[-1]])


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_reference", "unregistered-source"),
        ("adapter_id", "unregistered-adapter"),
        ("comparison_condition_id", "unregistered-condition"),
        ("information_rights_group", "unregistered-rights"),
        ("code_version", "unregistered-code"),
        ("source_version", "unregistered-version"),
        ("model_version", "unregistered-model"),
    ),
)
def test_primary_baseline_rejects_identity_mismatch(field, value):
    future = _complete_future_register()
    record = next(item for item in future if item["algorithm_id"] == "TeamWeaver-adapted-to-EAI")
    ready = _baseline_for_record(record)
    assert ready.rankable_against(record)
    mutated = BaselineQualificationV3(**{**ready.__dict__, field: value})
    assert not mutated.rankable_against(record)
