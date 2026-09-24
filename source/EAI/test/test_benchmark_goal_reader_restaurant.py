"""Offline contract tests for the Restaurant remaining-task reader (no Isaac/ROS/torch)."""

from copy import deepcopy

from demo.benchmark.goal_alternative_path_certificate import (
    build_alternative_path_certificate,
)
from demo.benchmark.goal_native_probe_runner import (
    OFFLINE_PROBE_MODE,
    build_probe_harness,
    probe_collection_plan,
)
from demo.benchmark.goal_reader_restaurant import (
    RESTAURANT_DEV_CASES,
    RESTAURANT_DEVELOPMENT_SCOPE,
    RESTAURANT_ENTITY_IDS,
    RESTAURANT_FACT_IDS,
    RESTAURANT_MEASUREMENT_POINTS,
    RESTAURANT_NATIVE_BLOCKER,
    RESTAURANT_NATIVE_SCOPE,
    RESTAURANT_SCENE_KEY,
    RESTAURANT_SOURCE_VERSIONS,
    build_restaurant_development_certificate,
    build_restaurant_registration,
    build_restaurant_state_contract,
    make_restaurant_source_readers,
    make_restaurant_state_provider,
    query_restaurant_development_case,
    restaurant_development_marker,
)
from demo.benchmark.goal_remaining_feasibility_oracle import (
    formal_oracle_release_review,
)
from demo.benchmark.goal_trusted_state import ground_state

_ORACLE_FIELDS = ("feasibility_label", "oracle_status", "alternative_path_gate",
                  "platform_valid", "quality_failure", "measurement_id",
                  "validation_scope", "explored_state_count")


def _verified_certificate(registration):
    return build_alternative_path_certificate(
        RESTAURANT_SCENE_KEY, registration,
        path_classes=deepcopy(
            build_restaurant_development_certificate(registration)["path_classes"]),
        covered_measurement_ids=[point["id"] for point in
                                 registration["measurement_points"]],
        uncovered_path_policy="treated_as_uncovered",
        independent_review="verified",
        evidence_refs=["native:restaurant-path-review-v1"])


def _point_facts(measurement_id):
    point = next(p for p in RESTAURANT_MEASUREMENT_POINTS
                 if p["id"] == measurement_id)
    return set(point["required_true"])


def _fake_native_provider(*, facts, time_s, entity_versions=None,
                          clock_epoch="offline-episode-1",
                          clock_id="offline-native-clock"):
    versions = dict(entity_versions
                    or {entity: 1 for entity in RESTAURANT_ENTITY_IDS})

    def provide(context):
        return {
            "time_s": time_s,
            "entity_versions": dict(versions),
            "true_facts": sorted(facts),
            "clock_epoch": clock_epoch,
            "clock_id": clock_id,
        }
    return provide


def test_restaurant_registration_and_contract_freeze_round_trip():
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    assert registration["validation"]["scope"] == RESTAURANT_DEVELOPMENT_SCOPE
    assert contract["scene_key"] == RESTAURANT_SCENE_KEY
    assert contract["scoreable"] is False
    assert contract["registration_sha256"] == registration["registration_sha256"]
    # Outcome bindings mirror the public package: each zone outcome binds its
    # arrival/support predicate plus its current observation; the receipt
    # outcome binds the business receipt predicate.
    assert sorted(contract["outcome_predicates"]) == [
        "dining_service_inspected", "kitchen_service_inspected",
        "readiness_receipt_committed"]
    assert contract["outcome_predicates"]["kitchen_service_inspected"] == [
        "kitchen_arrived", "kitchen_observed"]


def test_restaurant_development_scope_is_not_release_ready():
    registration = build_restaurant_registration()
    review = formal_oracle_release_review(registration, [])
    assert review["oracle_release_ready"] is False
    marker = restaurant_development_marker()
    assert marker["infrastructure_blockers"] == [RESTAURANT_NATIVE_BLOCKER]
    assert RESTAURANT_NATIVE_BLOCKER == "restaurant_native_candidate_evidence_pending"
    assert marker["release_eligible"] is False
    assert marker["scoreable"] is False


def test_restaurant_native_scope_requires_native_evidence():
    try:
        build_restaurant_registration(
            validation_scope=RESTAURANT_NATIVE_SCOPE,
            evidence_refs=("dev:restaurant-model-review-v1",))
    except ValueError:
        pass
    else:
        raise AssertionError("native scope accepted development evidence")
    native = build_restaurant_registration(
        validation_scope=RESTAURANT_NATIVE_SCOPE,
        evidence_refs=("native:restaurant-model-review-v1",))
    assert native["validation"]["scope"] == RESTAURANT_NATIVE_SCOPE


def test_restaurant_solo_serial_path_is_deployable():
    # Single-robot serial substitute: one ground unit arrives at and observes
    # both zones, then relays the unified receipt within the 240 s budget.
    result = query_restaurant_development_case("solo_serial_path")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"
    assert result["platform_valid"] is True
    assert result["explored_state_count"] > 1
    assert result["development_track"]["scene_key"] == RESTAURANT_SCENE_KEY
    assert result["formal_statistics"] is False


def test_restaurant_parallel_dual_unit_path_is_deployable():
    # Multi-robot parallel substitute: two ground members split the zones
    # (Lite3 included, it can inspect) and the Carter relays the receipt.
    result = query_restaurant_development_case("parallel_dual_unit_path")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"


def test_restaurant_kitchen_intermediate_state_is_groundable():
    # Per-zone contrast: kitchen inspected while dining is still pending must
    # ground against all three trusted sources.
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    robots = [{"id": "restaurant_carter_1",
               "capabilities": ["ground_navigation", "observe", "relay"],
               "payload_kg": 40},
              {"id": "restaurant_lite3_1",
               "capabilities": ["ground_navigation", "observe"],
               "payload_kg": 5}]
    provider = make_restaurant_state_provider(
        "restaurant_kitchen_inspected",
        {"kitchen_arrived", "kitchen_observed"},
        robots=robots, resources={"service_time_budget": 8})
    readers = make_restaurant_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(contract, registration,
                             measurement_id="restaurant_kitchen_inspected",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "verified"
    assert grounding["evidence_refs"]


def test_restaurant_relay_exit_after_receipt_committed_stays_deployable():
    # Receipt already committed: every terminal predicate is true, so the
    # relay capability is no longer necessary and a relay-less remaining team
    # (Lite3) must stay deployable.
    result = query_restaurant_development_case("relay_exit_after_receipt_committed")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"


def test_restaurant_navigator_exit_receipt_only_remaining_is_deployable():
    # Both zones inspected and only the receipt outstanding: the receipt is a
    # relay action that requires no navigation, so the sole navigating member
    # may exit while the aerial relay platform (no ground navigation) stays
    # deployable.
    result = query_restaurant_development_case(
        "navigator_exits_receipt_only_remaining")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"


def test_restaurant_navigator_exit_before_inspection_is_not_deployable():
    # Zones unchecked: without ground navigation no inspection predicate can
    # ever be measured true, so the aerial-only remainder is not deployable --
    # and the answer only survives the gate with a verified certificate.
    registration = build_restaurant_registration()
    result = query_restaurant_development_case(
        "navigator_exits_before_inspection", registration=registration,
        state_contract=build_restaurant_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"
    assert result["alternative_path_gate"] == "verified"
    assert result["certificate_sha256"]


def test_restaurant_not_deployable_downgrades_without_verified_certificate():
    # Two downgrade states: a pending review yields a platform failure with a
    # null label, and the default development certificate is exactly that.
    result = query_restaurant_development_case("navigator_exits_before_inspection")
    assert result["feasibility_label"] is None
    assert result["oracle_status"] == "failed"
    assert result["quality_failure"] == "certificate_review_pending"
    assert result["platform_valid"] is False
    assert result["alternative_path_gate"] == "review_pending"


def test_restaurant_lite3_without_relay_cannot_commit_receipt():
    # The decoy difference must survive: Lite3 can navigate and observe but
    # has no relay capability, so alone it can never commit the receipt.
    registration = build_restaurant_registration()
    result = query_restaurant_development_case(
        "lite3_only_cannot_commit_receipt", registration=registration,
        state_contract=build_restaurant_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"
    assert result["alternative_path_gate"] == "verified"


def test_restaurant_serial_budget_infeasible_parallel_remaining():
    # Remaining budget (6 units = 180 s) cannot fund the serial substitute
    # (7 units = 210 s) but still funds the parallel one (4 units = 120 s):
    # deployable through the parallel path only.
    result = query_restaurant_development_case(
        "serial_budget_infeasible_parallel_remaining")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"


def test_restaurant_budget_exhaustion_contrasts():
    registration = build_restaurant_registration()
    certificate = _verified_certificate(registration)
    for case_id in ("solo_serial_budget_exhausted",
                    "budget_exhausted_all_alternatives"):
        result = query_restaurant_development_case(
            case_id, registration=registration,
            state_contract=build_restaurant_state_contract(registration),
            certificate=certificate)
        assert result["feasibility_label"] == "not_deployable", case_id
        assert result["oracle_status"] == "verified", case_id
        assert result["alternative_path_gate"] == "verified", case_id


def test_restaurant_malformed_readback_fails_closed_without_label():
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    provider = make_restaurant_state_provider(
        "restaurant_initial", set(),
        robots=[{"id": "restaurant_carter_1",
                 "capabilities": ["ground_navigation", "observe", "relay"],
                 "payload_kg": 40}],
        resources={"service_time_budget": 8})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    readers = make_restaurant_source_readers()
    packets = {source: readers[source]({}, observation) for source in readers}
    # Drop one predicate from the arrival readback: incomplete measurement.
    packets["restaurant_zone_arrival"]["predicates"].pop("kitchen_arrived")
    grounding = ground_state(contract, registration,
                             measurement_id="restaurant_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["scoreable"] is False


def test_restaurant_lying_probe_fails_grounding():
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    provider = make_restaurant_state_provider(
        "restaurant_initial", set(),
        robots=[{"id": "restaurant_carter_1",
                 "capabilities": ["ground_navigation", "observe", "relay"],
                 "payload_kg": 40}],
        resources={"service_time_budget": 8})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    lying_probe = {"restaurant_readiness_backend": lambda state: {
        "readiness_receipt_committed": True}}
    readers = make_restaurant_source_readers(lying_probe)
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(contract, registration,
                             measurement_id="restaurant_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert "disagrees with state" in grounding["quality_failure"]


def test_restaurant_entity_version_mismatch_fails_grounding():
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    provider = make_restaurant_state_provider(
        "restaurant_initial", set(),
        robots=[{"id": "restaurant_carter_1",
                 "capabilities": ["ground_navigation", "observe", "relay"],
                 "payload_kg": 40}],
        resources={"service_time_budget": 8},
        entity_versions={entity: 3 for entity in contract["entity_ids"]})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    readers = make_restaurant_source_readers()
    packets = {source: readers[source]({}, observation) for source in readers}
    tampered = deepcopy(observation["state"])
    tampered["entity_versions"] = {entity: 1
                                   for entity in contract["entity_ids"]}
    grounding = ground_state(contract, registration,
                             measurement_id="restaurant_initial",
                             state=tampered, readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["quality_failure"]


def test_restaurant_unknown_development_case_is_rejected():
    try:
        query_restaurant_development_case("teleport_to_kitchen")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown case id accepted")


def test_restaurant_registered_in_probe_harness_and_collection_plan():
    harness = build_probe_harness(RESTAURANT_SCENE_KEY)
    assert harness.probe_mode == OFFLINE_PROBE_MODE
    measurement_id = dict(RESTAURANT_DEV_CASES)["solo_serial_path"][0]
    result = harness.run_measurement(
        "solo_serial_path",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(measurement_id), time_s=6.0))
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["scene_key"] == RESTAURANT_SCENE_KEY
    assert result["measurement_id"] == measurement_id
    marker = result["development_track"]
    assert marker["evidence_class"] == "diagnostic"
    assert marker["infrastructure_blockers"] == [RESTAURANT_NATIVE_BLOCKER]
    assert marker["release_eligible"] is False

    plan = probe_collection_plan(RESTAURANT_SCENE_KEY)
    assert plan["native_blockers"] == [RESTAURANT_NATIVE_BLOCKER]
    assert set(plan["sources"]) == set(RESTAURANT_SOURCE_VERSIONS)
    kinds = {}
    measured = []
    for source_id, row in plan["sources"].items():
        assert row["source_version"] == RESTAURANT_SOURCE_VERSIONS[source_id]
        assert row["predicates"] and row["entity_ids"]
        assert set(row["entity_ids"]) <= set(RESTAURANT_ENTITY_IDS)
        kinds[source_id] = row["evidence_kinds"]
        measured.extend(row["predicates"])
    assert sorted(measured) == sorted(RESTAURANT_FACT_IDS)
    assert kinds["restaurant_zone_arrival"] == ["physical_completion"]
    assert kinds["restaurant_service_observations"] == ["observation_record"]
    assert kinds["restaurant_readiness_backend"] == ["business_receipt"]
    assert [p["id"] for p in plan["measurement_points"]] == [
        p["id"] for p in RESTAURANT_MEASUREMENT_POINTS]
    for point in plan["measurement_points"]:
        assert point["facts_to_measure"] == sorted(RESTAURANT_FACT_IDS)
        assert point["required_entity_version_ids"] == sorted(RESTAURANT_ENTITY_IDS)
    assert {case["case_id"] for case in plan["contrast_cases"]} == set(
        RESTAURANT_DEV_CASES)
    for case in plan["contrast_cases"]:
        assert case["measurement_id"] == RESTAURANT_DEV_CASES[case["case_id"]][0]
        assert case["robots"] and case["resources"]


def test_restaurant_native_fake_probe_mode_matches_synthetic_backend():
    harness = build_probe_harness(RESTAURANT_SCENE_KEY)
    for case_id in ("solo_serial_path", "solo_serial_budget_exhausted",
                    "relay_exit_after_receipt_committed"):
        synthetic = query_restaurant_development_case(case_id)
        measurement_id = RESTAURANT_DEV_CASES[case_id][0]
        native = harness.run_measurement(
            case_id,
            native_state_provider=_fake_native_provider(
                facts=_point_facts(measurement_id), time_s=7.0))
        for field in _ORACLE_FIELDS:
            assert native.get(field) == synthetic.get(field), (case_id, field)
