from __future__ import annotations

from benchmark.formal_matrix_v3 import BaselineQualificationV3, FormalCaseV3, baseline_release_status, formal_case_status
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


def test_baseline_requires_adapter_checkpoint_and_physical_qualification():
    missing = BaselineQualificationV3("Gosrich", "official", None, None, "unsupported", None, ())
    assert baseline_release_status([missing])["status"] == "na"
    digest = lambda label: hashlib.sha256(label.encode()).hexdigest()
    ready = BaselineQualificationV3("EMOS", "in_tree", "emos-v3", "cp", "qualified", "obs.v3", ("nominal",), *(digest(label) for label in ("system", "task", "team", "budget", "obs", "manifest")))
    assert baseline_release_status([ready])["status"] == "ready"
