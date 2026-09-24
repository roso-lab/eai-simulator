"""Focused tests for independently projected autonomous City progress."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_autonomous import (
    CityAcceptanceProgressReader,
    evaluate_city_partial_acceptance,
)


PUBLIC_OUTCOMES = (
    "incident_assessed",
    "exposed_people_accounted_for",
    "secondary_traffic_protected",
    "response_access_preserved",
)


def _complete_sections():
    return {
        "incident_assessed": {
            "observation_ref": "observation-1",
            "fresh_at_decision": True,
            "source_visible_to_participant": True,
            "observation_delivery_time_s": 1.0,
            "decision_time_s": 2.0,
        },
        "responder_dispatch": {
            "selected_agent_id": "go2_1",
            "agent_active_at_dispatch": True,
            "capability_admitted": True,
            "route_admitted": True,
            "dispatch_time_s": 2.0,
            "arrival_time_s": 5.0,
        },
        "people": [{
            "person_id": "person-1",
            "observation_ref": "observation-1",
            "guidance_acknowledged": True,
            "route_admitted": True,
            "entered_hazard": False,
            "arrived_at_admitted_assembly": True,
            "guidance_request_time_s": 3.0,
            "guidance_time_s": 4.0,
            "arrival_time_s": 8.0,
        }],
        "traffic": [{
            "vehicle_id": "vehicle-1",
            "following_incident": True,
            "control_acknowledged": True,
            "trajectory_sample_after_ack": True,
            "entered_exclusion_boundary": False,
            "request_time_s": 3.0,
            "ack_time_s": 4.0,
            "trajectory_check_time_s": 9.0,
        }],
        "incident_report": {
            "observation_refs": ["observation-1"],
            "receipt_received": True,
            "submit_time_s": 3.0,
            "receipt_time_s": 4.0,
        },
        "response_access": {
            "continuous_access_check": True,
            "route_admitted": True,
            "hazard_free_throughout": True,
            "check_start_time_s": 3.0,
            "check_end_time_s": 5.0,
        },
    }


def _pending_sections():
    envelope = _complete_sections()
    envelope["incident_assessed"]["decision_time_s"] = None
    envelope["responder_dispatch"]["dispatch_time_s"] = None
    envelope["responder_dispatch"]["arrival_time_s"] = None
    envelope["people"][0]["guidance_time_s"] = None
    envelope["people"][0]["arrival_time_s"] = None
    envelope["traffic"][0]["ack_time_s"] = None
    envelope["traffic"][0]["trajectory_check_time_s"] = None
    envelope["incident_report"]["receipt_time_s"] = None
    envelope["response_access"]["check_end_time_s"] = None
    return envelope


def _evaluate(envelope):
    return evaluate_city_partial_acceptance(
        envelope,
        expected_affected_person_ids={"person-1"},
        expected_following_vehicle_ids={"vehicle-1"},
        outcome_ids=PUBLIC_OUTCOMES,
    )


@pytest.mark.parametrize("outcome_id,complete_sections", [
    ("incident_assessed", ("incident_assessed", "responder_dispatch")),
    ("exposed_people_accounted_for", ("people",)),
    ("secondary_traffic_protected", ("traffic",)),
    ("response_access_preserved", ("incident_report", "response_access")),
])
def test_completed_milestone_survives_unrelated_pending_services(
        outcome_id, complete_sections):
    envelope = _pending_sections()
    complete = _complete_sections()
    for section in complete_sections:
        envelope[section] = complete[section]

    result = _evaluate(envelope)

    assert result["predicate_vector"][outcome_id] is True
    assert result["partial_acceptance_reviews"][outcome_id]["status"] == "satisfied"
    for other_id in set(PUBLIC_OUTCOMES) - {outcome_id}:
        assert result["predicate_vector"][other_id] is None


def test_incident_and_dispatch_are_independent_but_dispatch_is_archived_hard_condition():
    envelope = _pending_sections()
    envelope["incident_assessed"] = _complete_sections()["incident_assessed"]

    result = _evaluate(envelope)

    assert result["predicate_vector"]["incident_assessed"] is True
    assert result["hard_conditions"]["response_dispatch"] is None
    assert result["partial_acceptance_reviews"]["response_dispatch"]["status"] == "pending"
    assert result["whole_task_completion_available"]["evidence_available"] is False
    assert result["whole_task_completion_available"]["mission_success"] is None


def test_all_public_progress_does_not_bypass_pending_dispatch_hard_condition():
    envelope = _complete_sections()
    envelope["responder_dispatch"]["dispatch_time_s"] = None
    envelope["responder_dispatch"]["arrival_time_s"] = None

    result = _evaluate(envelope)

    assert all(value is True for value in result["predicate_vector"].values())
    assert result["hard_conditions"]["response_dispatch"] is None
    availability = result["whole_task_completion_available"]
    assert availability["all_public_predicates_satisfied"] is True
    assert availability["evidence_available"] is False
    assert availability["mission_success"] is None


def test_dispatch_ignores_pending_incident_decision_but_checks_delivery_order():
    envelope = _pending_sections()
    envelope["responder_dispatch"] = _complete_sections()["responder_dispatch"]

    result = _evaluate(envelope)

    assert envelope["incident_assessed"]["decision_time_s"] is None
    assert result["hard_conditions"]["response_dispatch"] is True
    dispatch_projection = result["partial_acceptance_reviews"]["response_dispatch"]
    assert dispatch_projection["projection"]["incident_assessed"] == {
        "observation_delivery_time_s": 1.0,
    }

    envelope["responder_dispatch"]["dispatch_time_s"] = 0.5
    result = _evaluate(envelope)
    assert result["hard_conditions"]["response_dispatch"] is None
    failures = result["partial_acceptance_reviews"]["response_dispatch"][
        "review"]["outcomes"]["response_dispatch"]["failure_codes"]
    assert "responder_dispatch_before_observation_delivery" in failures


@pytest.mark.parametrize("incident", [None, {}])
def test_dispatch_cannot_validate_without_incident_delivery_dependency(incident):
    envelope = _pending_sections()
    envelope["responder_dispatch"] = _complete_sections()["responder_dispatch"]
    if incident is None:
        del envelope["incident_assessed"]
    else:
        envelope["incident_assessed"] = incident

    result = _evaluate(envelope)
    dispatch = result["partial_acceptance_reviews"]["response_dispatch"]

    assert result["hard_conditions"]["response_dispatch"] is None
    assert dispatch["status"] == "pending"
    assert dispatch["review"] is None
    assert dispatch["missing_dependencies"] == [
        "incident_assessed.observation_delivery_time_s"]


def test_all_pending_evidence_emits_no_positive_predicate_or_hard_condition():
    result = _evaluate(_pending_sections())

    assert result["predicate_vector"] == dict.fromkeys(PUBLIC_OUTCOMES)
    assert result["hard_conditions"] == {"response_dispatch": None}
    assert all(row["status"] == "pending"
               for row in result["partial_acceptance_reviews"].values())


@pytest.mark.parametrize("outcome_ids", [(), ("incident_assessed",)])
def test_subset_replay_never_claims_the_full_public_vector_complete(outcome_ids):
    result = evaluate_city_partial_acceptance(
        _complete_sections(),
        expected_affected_person_ids={"person-1"},
        expected_following_vehicle_ids={"vehicle-1"},
        outcome_ids=outcome_ids,
    )

    availability = result["whole_task_completion_available"]
    assert availability["complete_public_predicate_set_requested"] is False
    assert availability["all_public_predicates_satisfied"] is False
    assert availability["evidence_available"] is False


@pytest.mark.parametrize("bad_time", [True, "bad", -1.0, float("nan"), float("inf")])
def test_malformed_nonnull_timestamp_is_not_hidden_by_pending_evidence(bad_time):
    envelope = _pending_sections()
    envelope["traffic"][0]["request_time_s"] = bad_time

    with pytest.raises(ValueError, match="traffic\\[0\\]\\.request_time_s"):
        _evaluate(envelope)


def test_late_otherwise_complete_evidence_is_not_satisfied():
    envelope = _pending_sections()
    envelope["traffic"] = _complete_sections()["traffic"]
    envelope["traffic"][0].update(
        request_time_s=175.0,
        ack_time_s=176.0,
        trajectory_check_time_s=181.0,
    )

    result = _evaluate(envelope)
    review = result["partial_acceptance_reviews"]["secondary_traffic_protected"]

    assert review["review"]["outcomes"]["secondary_traffic_protected"][
        "success"] is True
    assert review["budget_decision"]["status"] == "exceeded"
    assert result["predicate_vector"]["secondary_traffic_protected"] is None


def test_people_roster_must_be_complete_unique_and_bound():
    envelope = _pending_sections()
    envelope["people"] = _complete_sections()["people"]

    result = evaluate_city_partial_acceptance(
        envelope,
        expected_affected_person_ids={"person-1", "person-2"},
        expected_following_vehicle_ids={"vehicle-1"},
        outcome_ids=PUBLIC_OUTCOMES,
    )
    failures = result["partial_acceptance_reviews"][
        "exposed_people_accounted_for"]["review"]["outcomes"][
            "exposed_people_accounted_for"]["failure_codes"]

    assert result["predicate_vector"]["exposed_people_accounted_for"] is None
    assert "person:person-2:evidence_missing_from_affected_roster" in failures

    duplicate = deepcopy(envelope)
    duplicate["people"].append(deepcopy(duplicate["people"][0]))
    result = _evaluate(duplicate)
    failures = result["partial_acceptance_reviews"][
        "exposed_people_accounted_for"]["review"]["outcomes"][
            "exposed_people_accounted_for"]["failure_codes"]
    assert result["predicate_vector"]["exposed_people_accounted_for"] is None
    assert "duplicate_affected_person_evidence" in failures


@pytest.mark.parametrize("start,end,expected_failure", [
    (4.5, 5.0, "continuous_access_does_not_cover_receipt"),
    (4.0, 4.0, "continuous_access_interval_empty"),
])
def test_response_access_requires_report_receipt_coverage(
        start, end, expected_failure):
    envelope = _pending_sections()
    complete = _complete_sections()
    envelope["incident_report"] = complete["incident_report"]
    envelope["response_access"] = complete["response_access"]
    envelope["response_access"].update(
        check_start_time_s=start,
        check_end_time_s=end,
    )

    result = _evaluate(envelope)
    review = result["partial_acceptance_reviews"]["response_access_preserved"]

    assert result["predicate_vector"]["response_access_preserved"] is None
    assert expected_failure in review["review"]["outcomes"][
        "response_access_preserved"]["failure_codes"]


def test_response_access_requires_both_report_and_access_sections():
    envelope = _pending_sections()
    envelope["incident_report"] = _complete_sections()["incident_report"]
    del envelope["response_access"]

    result = _evaluate(envelope)

    review = result["partial_acceptance_reviews"]["response_access_preserved"]
    assert review["status"] == "not_satisfied"
    assert "continuous_access_evidence_missing" in review["review"]["outcomes"][
        "response_access_preserved"]["failure_codes"]


def test_dispatch_preserves_first_responder_arrival_deadline():
    envelope = _pending_sections()
    envelope["responder_dispatch"] = _complete_sections()["responder_dispatch"]
    envelope["responder_dispatch"]["arrival_time_s"] = 38.0

    result = _evaluate(envelope)

    dispatch = result["partial_acceptance_reviews"]["response_dispatch"]
    assert result["hard_conditions"]["response_dispatch"] is None
    assert "first_responder_arrival_deadline_exceeded" in dispatch["review"][
        "outcomes"]["response_dispatch"]["failure_codes"]


@pytest.mark.parametrize("outcome_id,section,field,value", [
    ("incident_assessed", "incident_assessed", "decision_time_s", 17.0),
    ("exposed_people_accounted_for", "people", "guidance_time_s", 24.0),
    ("exposed_people_accounted_for", "people", "arrival_time_s", 65.0),
    ("secondary_traffic_protected", "traffic", "ack_time_s", 14.0),
    ("secondary_traffic_protected", "traffic", "trajectory_check_time_s", 8.0),
    ("response_access_preserved", "incident_report", "receipt_time_s", 19.0),
])
def test_projected_milestones_preserve_relative_deadlines(
        outcome_id, section, field, value):
    envelope = _pending_sections()
    complete = _complete_sections()
    if section in ("people", "traffic"):
        envelope[section] = complete[section]
        envelope[section][0][field] = value
    else:
        envelope[section] = complete[section]
        envelope[section][field] = value
        if section == "incident_report":
            envelope["response_access"] = complete["response_access"]

    result = _evaluate(envelope)

    assert result["predicate_vector"][outcome_id] is None
    assert result["partial_acceptance_reviews"][outcome_id]["status"] == "not_satisfied"


def test_reader_archives_replayable_projection_reviews_and_predicate_vector(
        tmp_path, monkeypatch):
    from demo.benchmark import goal_city_autonomous as autonomous

    envelope = _pending_sections()
    envelope["people"] = _complete_sections()["people"]
    monkeypatch.setattr(
        autonomous, "build_acceptance_evidence",
        lambda *_args, **_kwargs: deepcopy(envelope))
    ctx = {
        "city": SimpleNamespace(elapsed=12.0),
        "roster_ids": ["person-1"],
        "vehicle_ids": ["vehicle-1"],
    }
    trusted_report = {
        "session": {
            "business_outcomes": [{"id": outcome_id}
                                  for outcome_id in PUBLIC_OUTCOMES],
        },
    }
    reader = CityAcceptanceProgressReader([], ctx, tmp_path)

    emitted = reader(
        episode_id="episode-replay",
        boundary="after_skill",
        trusted_report=trusted_report,
    )
    reader.write()
    archived = json.loads(
        (tmp_path / "city_progress_sources.json").read_text(encoding="utf-8"))
    sample = archived["samples"][0]
    replay = evaluate_city_partial_acceptance(
        sample["acceptance_envelope"],
        expected_affected_person_ids=set(sample["expected_affected_person_ids"]),
        expected_following_vehicle_ids=set(sample["expected_following_vehicle_ids"]),
        outcome_ids=tuple(sample["predicate_vector"]),
    )

    assert archived["version"] == 2
    assert sample["acceptance_envelope"] == envelope
    assert replay["partial_acceptance_reviews"] == sample["partial_acceptance_reviews"]
    assert replay["predicate_vector"] == sample["predicate_vector"]
    assert replay["hard_conditions"] == sample["hard_conditions"]
    assert replay["whole_task_completion_available"] == sample[
        "whole_task_completion_available"]
    assert {key: row["satisfied"] for key, row in emitted["predicates"].items()} == (
        sample["predicate_vector"])


def test_malformed_reader_sample_is_archived_as_infrastructure_failure(
        tmp_path, monkeypatch):
    from demo.benchmark import goal_city_autonomous as autonomous

    envelope = _pending_sections()
    envelope["people"][0]["guidance_request_time_s"] = float("inf")
    monkeypatch.setattr(
        autonomous, "build_acceptance_evidence",
        lambda *_args, **_kwargs: deepcopy(envelope))
    ctx = {
        "city": SimpleNamespace(elapsed=12.0),
        "roster_ids": ["person-1"],
        "vehicle_ids": ["vehicle-1"],
    }
    reader = CityAcceptanceProgressReader([], ctx, tmp_path)

    with pytest.raises(RuntimeError, match="progress evaluation failed"):
        reader(
            episode_id="episode-malformed",
            boundary="initial",
            trusted_report={"session": {"business_outcomes": [
                {"id": outcome_id} for outcome_id in PUBLIC_OUTCOMES]}},
        )
    reader.write()
    sample = json.loads(
        (tmp_path / "city_progress_sources.json").read_text(encoding="utf-8"))[
            "samples"][0]

    assert sample["error"].startswith("ValueError:")
    assert sample["partial_acceptance_reviews"] is None
    assert sample["predicate_vector"] is None
    assert sample["acceptance_envelope_special_floats"] == [{
        "path": ["people", 0, "guidance_request_time_s"],
        "value": "inf",
    }]
    with pytest.raises(ValueError, match="guidance_request_time_s"):
        evaluate_city_partial_acceptance(
            sample["acceptance_envelope"],
            expected_affected_person_ids={"person-1"},
            expected_following_vehicle_ids={"vehicle-1"},
        )
