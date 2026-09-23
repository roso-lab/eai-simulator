"""Completeness gate tests for the five evaluator-only native oracles."""

from copy import deepcopy

from demo.benchmark.goal_native_oracle_registry import (
    EXPECTED_SCENES,
    audit_native_oracle_registry,
)
from demo.benchmark.provenance import canonical_sha256


def _sealed(value, field):
    value[field] = canonical_sha256({key: item for key, item in value.items() if key != field})
    return value


def _bundle(scene):
    task = f"task-{scene}"
    registration = {
        "schema": "eai.benchmark.remaining_feasibility_registration",
        "version": 1,
        "visibility": "evaluator_only",
        "task_sha256": task,
        "model": {"fact_ids": ["done"], "terminal_all": ["done"],
                   "resource_bounds": {}, "transitions": []},
        "model_sha256": "model",
        "measurement_points": [{"id": f"{scene}:initial", "trigger": "initial_deployment",
                                "required_true": [], "required_false": [], "varying_facts": ["done"]}],
        "validation": {"status": "verified", "scope": "native_remaining_task",
                        "evidence_refs": [f"native://{scene}/certificate"]},
    }
    _sealed(registration, "registration_sha256")
    contract = {
        "schema": "eai.benchmark.trusted_state_contract",
        "version": 1,
        "visibility": "evaluator_only",
        "scene_key": scene,
        "task_sha256": task,
        "registration_sha256": registration["registration_sha256"],
        "predicate_bindings": {}, "outcome_ids": [], "outcome_predicates": {},
        "source_versions": {"reader": "v1"}, "entity_ids": ["episode"],
        "asset_manifest_sha256": "a" * 64, "skill_contract_sha256": "b" * 64,
        "execution_validated": False, "business_outcomes_validated": False,
        "scoreable": False,
    }
    _sealed(contract, "contract_sha256")
    measurement_id = registration["measurement_points"][0]["id"]
    review = {
        "task_sha256": task,
        "registration_sha256": registration["registration_sha256"],
        "contract_sha256": contract["contract_sha256"],
        "oracle_release_ready": True,
        "results": [{
            "measurement_id": measurement_id,
            "feasibility_label": "deployable",
            "oracle_status": "verified",
            "validation_scope": "native_remaining_task",
            "evidence_refs": [f"native://{scene}/{measurement_id}"],
            "registration_sha256": registration["registration_sha256"],
            "contract_sha256": contract["contract_sha256"],
        }],
    }
    return {"scene_key": scene, "registration": registration,
            "state_contract": contract, "release_review": review}


def _all():
    return [_bundle(scene) for scene in EXPECTED_SCENES]


def test_all_five_scenes_with_binary_native_results_are_release_ready():
    report = audit_native_oracle_registry(_all())
    assert report["oracle_release_ready"] is True
    assert report["missing_scenes"] == []
    assert all(row["status"] == "verified" for row in report["scenes"].values())
    assert report["scoreable"] is False


def test_missing_scene_is_explicit_and_fail_closed():
    report = audit_native_oracle_registry(_all()[:-1])
    assert report["oracle_release_ready"] is False
    assert report["missing_scenes"] == ["restaurant"]
    assert "restaurant" not in report["scenes"]


def test_authoring_only_validation_scope_is_invalid():
    rows = _all()
    row = rows[0]
    row["registration"]["validation"]["scope"] = "declared_contract_only"
    # The old seal must not be silently accepted after a scope change.
    report = audit_native_oracle_registry(rows)
    assert report["oracle_release_ready"] is False
    assert "registration_hash_mismatch" in report["scenes"]["factory"]["invalid"]


def test_hash_drift_is_invalid_even_when_review_claims_ready():
    rows = _all()
    rows[1]["registration"]["task_sha256"] = "drifted-task"
    report = audit_native_oracle_registry(rows)
    assert report["oracle_release_ready"] is False
    assert "registration_hash_mismatch" in report["scenes"]["warehouse_nvd"]["invalid"]


def test_failed_measurement_does_not_become_a_third_truth():
    rows = _all()
    rows[2]["release_review"]["oracle_release_ready"] = False
    rows[2]["release_review"]["results"][0]["oracle_status"] = "failed"
    rows[2]["release_review"]["results"][0]["feasibility_label"] = None
    report = audit_native_oracle_registry(rows)
    assert report["oracle_release_ready"] is False
    assert report["scenes"]["modern_house_nvd"]["status"] == "invalid"
    assert "modern_house_nvd:initial" in report["scenes"]["modern_house_nvd"]["failed_measurement_ids"]
    assert all(row["feasibility_label"] not in {"unverified"}
               for row in rows[2]["release_review"]["results"])


def test_duplicate_scene_and_duplicate_measurement_fail_closed():
    rows = _all()
    rows.append(deepcopy(rows[0]))
    rows[1]["release_review"]["results"].append(
        deepcopy(rows[1]["release_review"]["results"][0]))
    report = audit_native_oracle_registry(rows)
    assert report["oracle_release_ready"] is False
    assert "factory" in report["duplicate_scenes"]
    assert "warehouse_nvd:initial" in report["duplicate_measurements"]
