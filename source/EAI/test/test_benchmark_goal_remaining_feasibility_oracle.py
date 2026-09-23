"""Binary remaining-task truth, with separate preregistration/quality failures."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_remaining_feasibility_oracle import (
    freeze_oracle_registration, query_remaining_feasibility, formal_oracle_release_review,
    RegisteredRemainingTaskOracle,
)
from demo.benchmark.provenance import canonical_sha256


def slot(skill, payload=0):
    return {"requires_all": [skill], "min_payload_kg": payload}


def transition(name, before, after, slots, energy):
    return {"id": name, "requires_all": before, "requires_absent": after,
            "adds": after, "deletes": before, "capability_slots": slots,
            "resource_costs": {"energy_units": energy}}


@pytest.fixture
def registration():
    # A fully specified small reference world. This is NOT a native Warehouse
    # model certificate: the real object/route/services grounding is separate.
    facts = ["at_rack", "at_intermediate", "at_dock", "receipt"]
    model = {"fact_ids": facts, "terminal_all": ["receipt"],
        "resource_bounds": {"energy_units": 5}, "transitions": [
            transition("paired_from_rack", ["at_rack"], ["at_dock"], [slot("carry", 6), slot("carry", 6)], 2),
            transition("solo_from_rack", ["at_rack"], ["at_dock"], [slot("pick", 12)], 3),
            transition("paired_from_intermediate", ["at_intermediate"], ["at_dock"], [slot("carry", 6), slot("carry", 6)], 1),
            transition("handoff", ["at_dock"], ["receipt"], [slot("handoff")], 1),
        ]}
    points = [{"id": name, "trigger": trigger, "required_true": [fact],
               "required_false": [f for f in facts if f != fact], "varying_facts": []}
              for name, trigger, fact in [
                  ("initial", "initial_deployment", "at_rack"),
                  ("before_transport_loss", "member_exit", "at_rack"),
                  ("after_transport_loss", "member_exit", "at_dock"),
                  ("intermediate_loss", "capability_change", "at_intermediate"),
                  ("already_complete", "milestone_state_change", "receipt")]]
    return freeze_oracle_registration(model, task_sha256="task", measurement_points=points,
        validation={"status": "verified", "scope": "exact_reference_world_only",
                    "evidence_refs": ["reviewed-reference-transition-system"]})


def robot(name, skills, payload=0):
    return {"id": name, "capabilities": skills, "payload_kg": payload}


def query(registration, point, facts, robots, *, energy=5, **kwargs):
    state = {"true_facts": facts, "resources": {"energy_units": energy},
             "robots": robots, "max_active_agents": 2, "roster_version": 1,
             "capability_versions": {r["id"]: 1 for r in robots}, "time_s": 4.0}
    grounding = {"status": "verified", "evidence_refs": ["reference-state-check"],
                 "state_sha256": canonical_sha256(state)}
    args = dict(measurement_id=point, episode_id="test-episode", assessment_id="a1",
                assessment_epoch=1, task_sha256="task", state=state, grounding=grounding)
    args.update(kwargs)
    return query_remaining_feasibility(registration, **args)


def test_docked_crate_needs_only_handoff_after_grasp_provider_exits(registration):
    result = query(registration, "after_transport_loss", ["at_dock"], [robot("clerk", ["handoff"])])
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"
    assert "plan" not in result and "assigned_agents" not in result


def test_untransported_crate_without_any_legal_carrier_is_not_deployable(registration):
    result = query(registration, "before_transport_loss", ["at_rack"], [robot("clerk", ["handoff"])])
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"


def test_alternative_solo_team_and_serial_resource_reuse_are_searched(registration):
    solo = [robot("heavy", ["pick", "handoff"], 12)]
    assert query(registration, "initial", ["at_rack"], solo)["feasibility_label"] == "deployable"
    pair = [robot("a", ["carry", "handoff"], 6), robot("b", ["carry"], 6)]
    assert query(registration, "initial", ["at_rack"], pair)["feasibility_label"] == "deployable"
    assert query(registration, "initial", ["at_rack"], pair[:1])["feasibility_label"] == "not_deployable"


def test_intermediate_state_resources_and_payload_change_remaining_paths(registration):
    pair = [robot("a", ["carry", "handoff"], 6), robot("b", ["carry"], 6)]
    assert query(registration, "intermediate_loss", ["at_intermediate"], pair, energy=2)["feasibility_label"] == "deployable"
    assert query(registration, "initial", ["at_rack"], pair, energy=2)["feasibility_label"] == "not_deployable"
    pair[1]["payload_kg"] = 5
    assert query(registration, "intermediate_loss", ["at_intermediate"], pair)["feasibility_label"] == "not_deployable"


def test_terminal_predicate_satisfied_does_not_need_lost_abilities(registration):
    assert query(registration, "already_complete", ["receipt"], [])["feasibility_label"] == "deployable"


@pytest.mark.parametrize("kwargs", [
    {"grounding": {"status": "failed"}}, {"task_sha256": "other-task"},
    {"measurement_id": "chosen_after_seeing_model_answer"},
])
def test_quality_failure_is_not_a_third_task_truth(registration, kwargs):
    result = query(registration, "initial", ["at_rack"], [], **kwargs)
    assert result["feasibility_label"] is None
    assert result["oracle_status"] == "failed"
    assert result["platform_valid"] is False


def test_unregistered_physical_state_is_excluded_deterministically(registration):
    result = query(registration, "initial", ["at_dock"], [])
    assert result["oracle_status"] == "failed"
    assert result["quality_failure"] == "state_outside_preregistered_domain"


def test_mutation_and_search_timeout_never_become_infeasible_labels(registration):
    changed = deepcopy(registration)
    changed["model"]["transitions"] = []
    assert query(changed, "initial", ["at_rack"], [])["quality_failure"] == "registration_changed"
    pair = [robot("a", ["carry", "handoff"], 6), robot("b", ["carry"], 6)]
    result = query(registration, "initial", ["at_rack"], pair, max_states=1)
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None


def test_reference_solver_pass_is_not_native_release_admission(registration):
    result = query(registration, "after_transport_loss", ["at_dock"], [robot("clerk", ["handoff"])])
    review = formal_oracle_release_review(registration, [result])
    assert review["oracle_release_ready"] is False
    assert len(review["missing_measurement_ids"]) == 5
    assert review["scoreable"] is False


def test_runner_hook_evaluates_before_decision_and_does_not_expose_actions_to_grounder(registration):
    received = []
    state = {"true_facts": ["at_dock"], "resources": {"energy_units": 5},
             "robots": [robot("clerk", ["handoff"])], "max_active_agents": 2,
             "roster_version": 1, "capability_versions": {"clerk": 1}, "time_s": 4.0}
    def provider(snapshot):
        received.append(snapshot)
        return {"measurement_id": "after_transport_loss", "state": state,
                "grounding": {"status": "verified", "evidence_refs": ["trusted-reference"],
                              "state_sha256": canonical_sha256(state)}}
    oracle = RegisteredRemainingTaskOracle(registration, provider)
    trusted = {"session": {"episode_id": "episode", "current_time_s": 4.0,
        "runtime_state": {"objects": "actual-trusted-snapshot"},
        "actions": [{"decision": "must-not-reach-grounder"}],
        "deployment_assessment": {"status": "required", "assessment_id": "a1",
            "assessment_epoch": 1, "public_task_sha256": "task", "runtime_state": None}}}
    result = oracle("episode", trusted)
    assert result["feasibility_label"] == "deployable" and result["oracle_status"] == "verified"
    assert received[0]["runtime_state"] == trusted["session"]["runtime_state"]
    assert "must-not-reach-grounder" not in str(received)
    trusted["session"]["deployment_assessment"]["status"] = "deployable"
    with pytest.raises(ValueError, match="before declaration"):
        oracle("episode", trusted)
