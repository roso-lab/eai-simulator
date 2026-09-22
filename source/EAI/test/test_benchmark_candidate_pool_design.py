import pytest

from demo.benchmark.candidate_pool_design import (
    apply_pool_profile,
    build_capability_signature_necessity_certificate,
    capability_signature,
    pool_design_summary,
    validate_capability_signature_necessity_certificate,
    validate_pool_design_summary,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_spec


def test_expanded_profile_preserves_legacy_default_and_adds_selection_space():
    for scene in ("factory", "warehouse_nvd", "modern_house_nvd", "city"):
        legacy = build_goal_task_spec(scene)
        expanded = build_goal_task_spec(scene, pool_profile="expanded_v1")
        assert len(legacy["candidate_robot_pool"]) in {3, 4}
        assert len(expanded["candidate_robot_pool"]) >= 8
        assert expanded["budgets"]["max_active_agents"] == legacy["budgets"]["max_active_agents"]
        assert {row["id"] for row in legacy["candidate_robot_pool"]} < {
            row["id"] for row in expanded["candidate_robot_pool"]
        }


def test_pool_design_groups_by_public_capability_signature_and_freezes_cost_proxy():
    public = build_goal_task_spec("warehouse_nvd", pool_profile="expanded_v1")
    summary = pool_design_summary(public, "expanded_v1")
    assert summary["candidate_count"] == 8
    assert summary["counterfactual_search"] == "capability_signature_sampling"
    assert summary["cost_semantics"] == "frozen_authoring_proxy_per_active_robot"
    assert summary["execution_validated"] is False
    assert summary["scoreable"] is False
    assert sum(summary["capability_signature_counts"].values()) == 8
    assert set(summary["activation_costs"]) == {
        row["id"] for row in public["candidate_robot_pool"]
    }
    assert all(isinstance(capability_signature(row), str)
               for row in public["candidate_robot_pool"])
    assert validate_pool_design_summary(summary, public)["validated"]


def test_apply_pool_profile_does_not_mutate_the_legacy_public_task():
    public = build_goal_task_spec("factory")
    clone = apply_pool_profile(public, "expanded_v1")
    assert len(public["candidate_robot_pool"]) == 4
    assert len(clone["candidate_robot_pool"]) == 8


def test_signature_certificate_covers_stable_representative_teams_and_defaults_unknown():
    public = build_goal_task_spec("warehouse_nvd", pool_profile="expanded_v1")
    certificate = pool_design_summary(public, "expanded_v1")["necessity_certificate"]
    assert certificate["status"] == "unknown"
    assert certificate["scope"] == "capability_signature_declaration_sampling_only"
    assert certificate["unknown_reason"] == "execution_evidence_pending"
    assert certificate["physical_execution_validated"] is False
    assert certificate["whole_task_necessity_validated"] is False
    assert certificate["search_scope"]["strategy"] == (
        "all_signature_multisets_up_to_max_active_agents")
    assert certificate["search_scope"]["representative_team_count"] == len(
        certificate["representative_teams"])
    assert all(team["result"] == "unknown" for team in certificate["representative_teams"])
    assert certificate == build_capability_signature_necessity_certificate(
        public, "expanded_v1")


def test_signature_certificate_records_a_declared_compatible_counterexample():
    public = build_goal_task_spec("warehouse_nvd", pool_profile="expanded_v1")
    baseline = build_capability_signature_necessity_certificate(public, "expanded_v1")
    team = baseline["representative_teams"][0]
    certificate = build_capability_signature_necessity_certificate(
        public, "expanded_v1",
        evaluations={team["team_key"]: {
            "result": "declared_compatible", "evidence_refs": ["audit:counterexample"]
        }})
    assert certificate["status"] == "disproven"
    assert certificate["counterexample"]["team_key"] == team["team_key"]
    assert certificate["evidence_refs"] == ["audit:counterexample"]
    assert validate_capability_signature_necessity_certificate(certificate, public)["validated"]


def test_signature_certificate_requires_complete_evidence_for_validated_status():
    public = build_goal_task_spec("factory", pool_profile="expanded_v1")
    baseline = build_capability_signature_necessity_certificate(public, "expanded_v1")
    evaluations = {
        team["team_key"]: {
            "result": "declared_incompatible", "evidence_refs": [f"audit:{index}"]
        }
        for index, team in enumerate(baseline["representative_teams"])
    }
    certificate = build_capability_signature_necessity_certificate(
        public, "expanded_v1", evaluations=evaluations)
    assert certificate["status"] == "validated"
    assert certificate["unknown_reason"] is None


def test_signature_certificate_rejects_forged_physical_or_whole_task_claims():
    public = build_goal_task_spec("city", pool_profile="expanded_v1")
    certificate = pool_design_summary(public, "expanded_v1")["necessity_certificate"]
    for field in ("physical_execution_validated", "whole_task_necessity_validated"):
        forged = dict(certificate)
        forged[field] = True
        with pytest.raises(ValueError, match="physical|whole-task"):
            validate_capability_signature_necessity_certificate(forged, public)


def test_pool_profile_is_bound_to_the_frozen_candidate_ids():
    legacy = build_goal_task_spec("warehouse_nvd")
    expanded = build_goal_task_spec("warehouse_nvd", pool_profile="expanded_v1")
    with pytest.raises(ValueError, match="frozen profile"):
        pool_design_summary(legacy, "expanded_v1")
    with pytest.raises(ValueError, match="frozen profile"):
        pool_design_summary(expanded, "legacy_v1")


def test_signature_certificate_rejects_duplicate_representative_rows():
    public = build_goal_task_spec("factory", pool_profile="expanded_v1")
    certificate = pool_design_summary(public, "expanded_v1")["necessity_certificate"]
    forged = dict(certificate)
    forged["representative_teams"] = list(certificate["representative_teams"]) + [
        certificate["representative_teams"][0]
    ]
    with pytest.raises(ValueError, match="coverage"):
        validate_capability_signature_necessity_certificate(forged, public)


def test_unknown_signature_result_cannot_smuggle_unresolved_evidence():
    public = build_goal_task_spec("city", pool_profile="expanded_v1")
    baseline = pool_design_summary(public, "expanded_v1")["necessity_certificate"]
    team = baseline["representative_teams"][0]
    with pytest.raises(ValueError, match="unknown.*evidence"):
        build_capability_signature_necessity_certificate(
            public, "expanded_v1",
            evaluations={team["team_key"]: {
                "result": "unknown", "evidence_refs": ["pending:unresolved"]
            }})
