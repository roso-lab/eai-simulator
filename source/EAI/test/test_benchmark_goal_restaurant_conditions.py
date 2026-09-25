"""Exact Restaurant treatment binding and honest shared-pool support checks."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_conditions import (
    OBSERVATION_SCHEMA,
    build_restaurant_task_conditions,
    resolve_restaurant_condition_event,
    validate_restaurant_task_conditions,
)
from demo.benchmark.provenance import canonical_sha256


def _contract():
    # These are explicit diagnostic transport inputs, not the task treatment or C0.
    return {
        "pools": {
            "backhaul_uplink": {
                "capacity_bps": 1_000_000.0, "fixed_delay_s": 0.04, "ttl_s": 30.0,
            },
            "backhaul_downlink": {
                "capacity_bps": 1_000_000.0, "fixed_delay_s": 0.04, "ttl_s": 30.0,
            },
            "p2p": {
                "capacity_bps": 1_000_000.0, "fixed_delay_s": 0.04, "ttl_s": 30.0,
            },
        },
    }


def _source_binding(level="immediate"):
    package = build_goal_task_package(
        "restaurant", factor_id="communication_quality", factor_level=level)
    conditions = build_restaurant_task_conditions(
        public_task=package["public_task"],
        hidden_evaluator=package["hidden_evaluator"],
        communication_contract=_contract(),
    )
    return package, conditions


def _observation(*, producer="restaurant_carter_1", consumer="restaurant_iris_1",
                 published_at_s=4.0):
    message_id = "episode:restaurant-observation:obs-1"
    return {
        "schema": OBSERVATION_SCHEMA,
        "version": 1,
        "observation_id": "obs-1",
        "message_id": message_id,
        "producer_agent_id": producer,
        "consumer_agent_id": consumer,
        "published_at_s": published_at_s,
        "channel": "observation",
        "message_class": "restaurant_service_observation",
        "pool": "backhaul_uplink",
        "direction": "uplink",
        "provenance": {
            "kind": "goal_session_transport_join_reference",
            "producer_task_id": "observe-kitchen",
            "consumer_task_id": "consume-kitchen",
            "session_event_ref": "session:event:17",
            "transport_message_id": message_id,
        },
    }


@pytest.mark.parametrize(
    "level,expected",
    [
        ("immediate", {"operation": "deliver", "delay_s": 0.0,
                       "drop": False, "bytes_per_window": 8192,
                       "window_s": 1.0, "publication_validity_s": 30.0}),
        ("delayed", {"operation": "deliver", "delay_s": 5.0,
                     "drop": False, "bytes_per_window": 8192,
                     "window_s": 1.0, "publication_validity_s": 30.0}),
        ("dropped", {"operation": "drop", "delay_s": 0.0,
                     "drop": True, "bytes_per_window": 8192,
                     "window_s": 1.0, "publication_validity_s": 30.0}),
        ("bandwidth_limited", {"operation": "deliver", "delay_s": 0.0,
                               "drop": False, "bytes_per_window": 64,
                               "window_s": 1.0, "publication_validity_s": 30.0}),
        ("stale", {"operation": "deliver", "delay_s": 5.0,
                   "drop": False, "bytes_per_window": 8192,
                   "window_s": 1.0, "publication_validity_s": 1.0}),
    ],
)
def test_binding_copies_each_authoritative_hidden_treatment(level, expected):
    package, conditions = _source_binding(level)
    treatment = conditions["treatment"]
    assert treatment == {
        "id": "cross_mobility_observation_delivery",
        "trigger": "first_cross_mobility_observation_before_first_business_commit",
        **expected,
    }
    schedule = package["hidden_evaluator"]["hidden_event_schedule"]
    assert conditions["authoritative_hidden_event_schedule"] == schedule
    assert conditions["hidden_event_schedule_sha256"] == canonical_sha256(schedule)
    assert conditions["factor"] == {"id": "communication_quality", "level": level}


def test_native02_binding_proves_canonical_source_and_deployment_chain():
    from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2

    source = build_goal_task_package("restaurant")
    deployment = build_restaurant_deployment_v2()
    conditions = build_restaurant_task_conditions(
        public_task=deployment["public_task"],
        hidden_evaluator=source["hidden_evaluator"],
        communication_contract=_contract(),
        deployment_companion=deployment["deployment"],
    )
    identity = conditions["task_identity"]
    assert identity["relationship"] == "deployment_derived_from_canonical_source"
    assert identity["source_task_id"] == "RESTAURANT-GOAL-01"
    assert identity["source_public_task_sha256"] == canonical_sha256(source["public_task"])
    assert identity["effective_task_id"] == "RESTAURANT-GOAL-NATIVE-02"
    assert identity["effective_public_task_sha256"] == canonical_sha256(
        deployment["public_task"])
    assert identity["deployment_companion_sha256"] == canonical_sha256(
        deployment["deployment"])
    assert validate_restaurant_task_conditions(
        conditions,
        public_task=deployment["public_task"],
        hidden_evaluator=source["hidden_evaluator"],
        communication_contract=_contract(),
        deployment_companion=deployment["deployment"],
    ) == conditions


def test_binding_rejects_treatment_label_content_drift():
    package = build_goal_task_package(
        "restaurant", factor_id="communication_quality", factor_level="delayed")
    hidden = deepcopy(package["hidden_evaluator"])
    hidden["hidden_event_schedule"]["communication_events"][0]["delay_s"] = 0.0
    with pytest.raises(ValueError, match="canonical Restaurant source package"):
        build_restaurant_task_conditions(
            public_task=package["public_task"], hidden_evaluator=hidden,
            communication_contract=_contract())


def test_binding_rejects_foreign_task_schedule_and_deployment():
    package = build_goal_task_package("restaurant")
    foreign_task = deepcopy(package["public_task"])
    foreign_task["task_id"] = "RESTAURANT-FOREIGN"
    with pytest.raises(ValueError, match="neither the canonical source nor NATIVE-02"):
        build_restaurant_task_conditions(
            public_task=foreign_task, hidden_evaluator=package["hidden_evaluator"],
            communication_contract=_contract())

    from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
    deployment = build_restaurant_deployment_v2()
    foreign_companion = deepcopy(deployment["deployment"])
    foreign_companion["original_public_task_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="foreign"):
        build_restaurant_task_conditions(
            public_task=deployment["public_task"],
            hidden_evaluator=package["hidden_evaluator"],
            communication_contract=_contract(),
            deployment_companion=foreign_companion)


def test_exact_initial_contract_is_required_during_resolution():
    _package, conditions = _source_binding()
    foreign = _contract()
    foreign["pools"]["backhaul_uplink"]["fixed_delay_s"] = 0.05
    with pytest.raises(ValueError, match="differs from the condition binding"):
        resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=None,
            first_business_commit_at_s=None, communication_contract=foreign)


def test_ground_only_or_absent_observation_does_not_trigger():
    _package, conditions = _source_binding()
    absent = resolve_restaurant_condition_event(
        conditions, cross_mobility_observation=None,
        first_business_commit_at_s=None, communication_contract=_contract())
    assert absent["status"] == "not_triggered"
    assert absent["reason"] == "no_cross_mobility_observation"

    ground = resolve_restaurant_condition_event(
        conditions,
        cross_mobility_observation=_observation(consumer="restaurant_m20_1"),
        first_business_commit_at_s=None, communication_contract=_contract())
    assert ground["status"] == "not_triggered"
    assert ground["reason"] == "same_mobility_observation"
    assert ground["application_status"] == "not_applied"


def test_valid_cross_mobility_trigger_fires_once_but_application_is_unsupported():
    _package, conditions = _source_binding("delayed")
    first = resolve_restaurant_condition_event(
        conditions, cross_mobility_observation=_observation(),
        first_business_commit_at_s=5.0, communication_contract=_contract())
    assert first["status"] == "unsupported"
    assert first["trigger_status"] == "triggered"
    assert first["application_status"] == "unsupported"
    assert first["runtime_mapping_status"] == "unsupported_runtime_mapping"
    assert first["source_provenance_status"] == "unverified"
    assert first["effect_status"] == "unverified"
    assert first["target"]["scope"] == "single_future_publication"
    assert first["target"]["selector"]["message_id"] == \
        _observation()["message_id"]
    assert first["treatment"] == conditions["treatment"]

    second = resolve_restaurant_condition_event(
        conditions, cross_mobility_observation=_observation(published_at_s=4.5),
        first_business_commit_at_s=5.0, communication_contract=_contract(),
        prior_events=[first])
    assert second["status"] == "not_triggered"
    assert second["reason"] == "first_trigger_already_resolved"

    tampered = deepcopy(first)
    tampered["trigger_status"] = "not_triggered"
    with pytest.raises(ValueError, match="foreign or malformed"):
        resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=_observation(),
            first_business_commit_at_s=5.0, communication_contract=_contract(),
            prior_events=[tampered])


def test_observation_at_or_after_first_business_commit_does_not_trigger():
    _package, conditions = _source_binding()
    for published in (5.0, 6.0):
        event = resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=_observation(
                published_at_s=published),
            first_business_commit_at_s=5.0, communication_contract=_contract())
        assert event["status"] == "not_triggered"
        assert event["reason"] == "observation_not_before_first_business_commit"


def test_malformed_business_commit_time_is_rejected_even_without_observation():
    _package, conditions = _source_binding()
    with pytest.raises(ValueError, match="first_business_commit_at_s"):
        resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=None,
            first_business_commit_at_s=float("nan"),
            communication_contract=_contract())


def test_positive_trigger_requires_trusted_session_transport_provenance():
    _package, conditions = _source_binding()
    observation = _observation()
    observation["provenance"]["kind"] = "participant_assertion"
    with pytest.raises(ValueError, match="provenance kind is unsupported"):
        resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=observation,
            first_business_commit_at_s=None, communication_contract=_contract())


def test_resolution_never_mutates_shared_pool_or_unrelated_traffic():
    _package, conditions = _source_binding("bandwidth_limited")
    runtime = CommunicationRuntime(_contract())
    runtime.publish(
        message_id="unrelated", producer_id="robot:restaurant_carter_2",
        recipients=["cloud"], channel="telemetry", estimate={"value": 1},
        valid_until_s=20.0, pool="backhaul_uplink", direction="uplink")
    before = runtime.report()
    event = resolve_restaurant_condition_event(
        conditions, cross_mobility_observation=_observation(),
        first_business_commit_at_s=None,
        communication_contract=before["initial_contract"])
    after = runtime.report()
    assert event["status"] == "unsupported"
    assert after == before
    assert not any(row["event"] == "channel_condition_changed"
                   for row in after["ledger"])


def test_units_c0_and_all_qualification_flags_stay_honest():
    _package, conditions = _source_binding()
    assert conditions["runtime_units"] == {
        "treatment_bytes_per_window": "bytes_per_window",
        "treatment_window_s": "seconds",
        "legacy_runtime_capacity_bps": "bytes_per_second",
        "conversion_applied": False,
    }
    assert conditions["c0_status"] == "not_declared"
    assert conditions["c0_capacity_bps"] is None
    assert conditions["initial_communication_contract"]["pools"][
        "backhaul_uplink"]["capacity_bps"] == 1_000_000.0
    assert conditions["treatment"]["bytes_per_window"] == 8192
    assert conditions["runtime_support"]["global_pool_mutation_allowed"] is False
    for flag in (
        "execution_validated", "business_outcomes_validated",
        "task_exact_communication_treatment_validated", "release_eligible", "scoreable",
    ):
        assert conditions[flag] is False


def test_tampered_binding_is_rejected_before_event_resolution():
    _package, conditions = _source_binding()
    conditions["runtime_support"]["targeted_message_policy_supported"] = True
    with pytest.raises(ValueError, match="malformed or has drifted"):
        resolve_restaurant_condition_event(
            conditions, cross_mobility_observation=None,
            first_business_commit_at_s=None, communication_contract=_contract())
