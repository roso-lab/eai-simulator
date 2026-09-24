from copy import deepcopy

from demo.benchmark.goal_reader_house import (
    HOUSE_DEVELOPMENT_SCOPE,
    HOUSE_NATIVE_SCOPE,
    build_house_registration,
    build_house_state_contract,
    house_development_marker,
    make_house_source_readers,
    make_house_state_provider,
    query_house_development_case,
)
from demo.benchmark.goal_remaining_feasibility_oracle import (
    formal_oracle_release_review,
)
from demo.benchmark.goal_trusted_state import ground_state


def test_house_registration_and_contract_freeze_round_trip():
    registration = build_house_registration()
    contract = build_house_state_contract(registration)
    assert registration["validation"]["scope"] == HOUSE_DEVELOPMENT_SCOPE
    assert contract["scene_key"] == "modern_house_nvd"
    assert contract["scoreable"] is False
    assert contract["registration_sha256"] == registration["registration_sha256"]


def test_house_development_scope_is_not_release_ready():
    registration = build_house_registration()
    review = formal_oracle_release_review(registration, [])
    assert review["oracle_release_ready"] is False
    marker = house_development_marker()
    assert marker["infrastructure_blockers"] == ["house_native_admission_pending"]
    assert marker["release_eligible"] is False


def test_house_native_scope_requires_native_evidence():
    try:
        build_house_registration(validation_scope=HOUSE_NATIVE_SCOPE,
                                 evidence_refs=("dev:house-model-review-v1",))
    except ValueError:
        pass
    else:
        raise AssertionError("native scope accepted development evidence")


def test_house_full_team_initial_is_deployable():
    result = query_house_development_case("full_team_initial")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"
    assert result["platform_valid"] is True
    assert result["development_track"]["scene_key"] == "modern_house_nvd"
    assert result["formal_statistics"] is False


def test_house_carrier_exit_before_completion_is_not_deployable():
    # Books still on the floor and the chair still blocking: the only carry
    # capability left with the member, so the full solver must exhaust every
    # legal alternative before answering not_deployable.
    result = query_house_development_case("carrier_exits_early")
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"
    assert result["explored_state_count"] >= 1


def test_house_books_intermediate_state_is_groundable():
    registration = build_house_registration()
    contract = build_house_state_contract(registration)
    provider = make_house_state_provider(
        "house_books_intermediate", {"chair_blocking", "books_staged"},
        robots=[{"id": "house_carter_1", "capabilities": ["carry", "navigate"],
                 "payload_kg": 40},
                {"id": "house_go2_1", "capabilities": ["navigate"], "payload_kg": 5}],
        resources={"time_budget": 8})
    readers = make_house_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation)
               for source in readers}
    grounding = ground_state(contract, registration,
                             measurement_id="house_books_intermediate",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "verified"
    assert grounding["evidence_refs"]


def test_house_capability_no_longer_necessary_stays_deployable():
    # Chair restored and books shelved: no remaining predicate needs the
    # carry capability, so the carrier's exit must not change deployability.
    result = query_house_development_case("carrier_exits_after_belongings_done")
    assert result["feasibility_label"] == "deployable"


def test_house_navigator_exit_before_access_restored_is_not_deployable():
    # Access restoration still needs a navigating unit; with only the carrier
    # left the corridor clearance predicate can never be measured true.
    result = query_house_development_case("navigator_exits_before_access_restored")
    assert result["feasibility_label"] == "not_deployable"


def test_house_exhausted_time_budget_is_not_deployable():
    result = query_house_development_case("budget_exhausted")
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"


def test_house_malformed_readback_fails_closed_without_label():
    registration = build_house_registration()
    contract = build_house_state_contract(registration)
    provider = make_house_state_provider(
        "house_initial", {"chair_blocking", "books_on_floor"},
        robots=[{"id": "house_carter_1", "capabilities": ["carry", "navigate"],
                 "payload_kg": 40}], resources={"time_budget": 8})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    readers = make_house_source_readers()
    packets = {source: readers[source]({}, observation) for source in readers}
    # Drop one predicate from a physical readback: incomplete measurement.
    packets["house_physx_objects"]["predicates"].pop("chair_blocking")
    grounding = ground_state(contract, registration, measurement_id="house_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["scoreable"] is False


def test_house_native_probe_that_lies_about_state_fails_grounding():
    registration = build_house_registration()
    contract = build_house_state_contract(registration)
    provider = make_house_state_provider(
        "house_initial", {"chair_blocking", "books_on_floor"},
        robots=[{"id": "house_carter_1", "capabilities": ["carry", "navigate"],
                 "payload_kg": 40}], resources={"time_budget": 8})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    lying_probe = {"house_physx_objects": lambda state: {
        "chair_blocking": False, "chair_restored": True,
        "books_on_floor": False, "books_staged": False, "books_shelved": True}}
    readers = make_house_source_readers(lying_probe)
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(contract, registration, measurement_id="house_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert "predicate measurement disagrees" in grounding["quality_failure"]


def test_house_entity_version_mismatch_fails_grounding():
    registration = build_house_registration()
    contract = build_house_state_contract(registration)
    provider = make_house_state_provider(
        "house_initial", {"chair_blocking", "books_on_floor"},
        robots=[{"id": "house_carter_1", "capabilities": ["carry", "navigate"],
                 "payload_kg": 40}], resources={"time_budget": 8},
        entity_versions={entity: 2 for entity in contract["entity_ids"]})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    readers = make_house_source_readers()
    packets = {source: readers[source]({}, observation) for source in readers}
    tampered = deepcopy(observation["state"])
    tampered["entity_versions"] = {entity: 1
                                   for entity in contract["entity_ids"]}
    grounding = ground_state(contract, registration, measurement_id="house_initial",
                             state=tampered, readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["quality_failure"]


def test_house_unknown_development_case_is_rejected():
    try:
        query_house_development_case("teleport_through_wall")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown case id accepted")
