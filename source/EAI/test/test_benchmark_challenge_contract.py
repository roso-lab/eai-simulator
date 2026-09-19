"""Public/private benchmark challenge boundary tests."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.challenge_contract import (
    validate_hidden_evaluator,
    validate_participant_action,
    validate_participant_api,
    validate_participant_event,
    validate_public_task_spec,
    write_challenge_bundle,
)
from demo.factory_benchmark.challenge import compile_challenge


def _bundle():
    return compile_challenge()


def test_factory_public_artifact_contains_no_evaluator_truth_or_reference_answer():
    bundle = _bundle()
    public = bundle["public_task"]
    assert validate_public_task_spec(public)["validated"]
    encoded = json.dumps(public, sort_keys=True)
    for forbidden in (
        "preferred_agent", "inventory_label_id", "expected_marker_id", "ground_truth",
        "reference_assignment", "reference_decision", "baseline_decision",
    ):
        assert forbidden not in encoded
    route = next(item for item in public["objectives"] if item["id"] == "route_confirmation")
    assert "target_ref" not in route
    assert route["target_candidates"] == ["assembly_site", "dock_site"]
    semantic = public["semantic_task"]
    assert semantic["task_family"] == "production_exception_response"
    assert len(semantic["factors"]) >= 3
    assert set(semantic["team_lifecycle"]["events"]) >= {"member_join", "member_exit", "capability_change"}
    assert set(semantic["communication"]["conditions"]) >= {
        "immediate", "delayed", "dropped", "bandwidth_limited", "stale"}
    assert "semantic_task" in bundle["hidden_evaluator"]["scenario_snapshot"]
    skeleton = public["business_rules"]["task_skeleton"]
    assert skeleton["schema"] == "eai.benchmark.public_task_skeleton"
    assert skeleton["causal_predecessors"] == {
        "ground_approach": ["aerial_view"],
        "ground_view": ["ground_approach"],
        "route_confirmation": ["ground_view"],
    }
    assert set(skeleton["required_objective_roles"]) == {
        "aerial_view", "ground_approach", "ground_view", "route_confirmation"}
    assert "allocate" not in public["business_rules"]["inventory_destination_rule"]["participant_choice"]
    assert "reassign" not in public["business_rules"]["inventory_destination_rule"]["participant_choice"]


def test_participant_requirements_do_not_require_preexisting_e6_records():
    bundle = _bundle()
    requirements = bundle["public_task"]["participant_requirements"]
    assert requirements
    assert all("required_protocols" not in item for item in requirements)
    assert all("required_evidence_tier" not in item for item in requirements)
    qa = bundle["hidden_evaluator"]["benchmark_qa_requirements"]
    assert all(item["required_protocols"] for item in qa)
    assert all(item["required_evidence_tier"] == "E6" for item in qa)
    status = bundle["participant_api"]["implementation_status"]
    assert status["isaac_participant_adapter"] == "f_ins_dispatch_programmatic_v1"
    assert status["pending_action_types"] == ["allocate", "reassign"]


def test_public_validation_rejects_nested_truth_and_assignment_leaks():
    public = _bundle()["public_task"]
    for leak in (
        {"ground_truth": {"marker": 23}},
        {"nested": [{"preferred_agent": "carter_1"}]},
        {"reference_decision": {"target_ref": "dock_site"}},
    ):
        broken = deepcopy(public)
        broken["business_rules"]["leak"] = leak
        with pytest.raises(ValueError, match="leaks evaluator or baseline"):
            validate_public_task_spec(broken)


def test_contract_artifacts_are_hash_bound_and_hidden_evaluator_is_separate(tmp_path):
    bundle = _bundle()
    public = bundle["public_task"]
    assert validate_participant_api(bundle["participant_api"], task_spec=public)["validated"]
    assert validate_hidden_evaluator(bundle["hidden_evaluator"], task_spec=public)["validated"]
    paths = write_challenge_bundle(bundle, tmp_path / "challenge")
    assert paths["public_task"].parent.name == "release"
    assert paths["participant_api"].parent.name == "release"
    assert paths["baseline_manifest"].parent.name == "release"
    assert paths["hidden_evaluator"].parent.name == "evaluator"
    assert not (paths["public_task"].parent / "hidden_evaluator.json").exists()
    with pytest.raises(FileExistsError):
        write_challenge_bundle(bundle, tmp_path / "challenge")


def test_participant_action_and_event_envelopes_reject_unknown_scope():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    event = {
        "schema": "eai.benchmark.participant_event", "version": 1,
        "sequence": 3, "time_s": 2.0, "type": "member_exit",
        "payload": {"agent_id": "carter_1", "affected_node_ids": ["route_confirmation"]},
    }
    assert validate_participant_event(event, api=api) == event
    leaked_event = deepcopy(event)
    leaked_event["payload"]["hidden_fault_time_s"] = 9.0
    with pytest.raises(ValueError, match="undeclared fields"):
        validate_participant_event(leaked_event, api=api)
    action = {
        "schema": "eai.benchmark.participant_action", "version": 1,
        "action_id": "action-1", "submitted_at_s": 3.0, "type": "navigate_goal",
        "payload": {"node_id": "route_confirmation", "agent_id": "carter_1",
                    "target_ref": "dock_site"},
    }
    assert validate_participant_action(action, api=api, task_spec=public) == action
    leaked_action = deepcopy(action)
    leaked_action["payload"]["reference_decision"] = "dock_site"
    with pytest.raises(ValueError, match="undeclared fields"):
        validate_participant_action(leaked_action, api=api, task_spec=public)
    broken = deepcopy(action)
    broken["payload"]["agent_id"] = "answer_robot"
    with pytest.raises(ValueError, match="unknown agent"):
        validate_participant_action(broken, api=api, task_spec=public)
    broken = deepcopy(action)
    broken["payload"]["target_ref"] = "secret_station"
    with pytest.raises(ValueError, match="unknown target"):
        validate_participant_action(broken, api=api, task_spec=public)
    broken = deepcopy(action)
    broken["type"] = "reassign"
    broken["payload"] = {"node_id": "route_confirmation", "agent_ids": ["carter_1"],
                         "reason": "test"}
    with pytest.raises(ValueError, match="not allowed"):
        validate_participant_action(broken, api=api, task_spec=public)


def test_baseline_manifest_describes_solvers_without_embedding_outputs():
    manifest = _bundle()["baseline_manifest"]
    assert {solver["id"] for solver in manifest["solvers"]} == {"immediate", "wait_for_message"}
    assert all("assignment" not in solver and "decision" not in solver for solver in manifest["solvers"])
    assert manifest["role"].startswith("optional published reference solvers")
