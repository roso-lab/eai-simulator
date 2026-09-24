from copy import deepcopy

from demo.benchmark.goal_alternative_path_certificate import (
    build_alternative_path_certificate,
)
from demo.benchmark.goal_reader_warehouse import (
    build_warehouse_development_certificate,
    build_warehouse_registration,
    build_warehouse_state_contract,
    make_warehouse_source_readers,
    make_warehouse_state_provider,
    query_warehouse_development_case,
    warehouse_development_marker,
)
from demo.benchmark.goal_trusted_state import ground_state


def _verified_certificate(registration):
    return build_alternative_path_certificate(
        "warehouse_nvd", registration,
        path_classes=deepcopy(
            build_warehouse_development_certificate(registration)["path_classes"]),
        covered_measurement_ids=[point["id"] for point in
                                 registration["measurement_points"]],
        uncovered_path_policy="treated_as_uncovered",
        independent_review="verified",
        evidence_refs=["native:warehouse-path-review-v1"])


def test_warehouse_registration_and_contract_round_trip():
    registration = build_warehouse_registration()
    contract = build_warehouse_state_contract(registration)
    assert contract["scene_key"] == "warehouse_nvd"
    assert contract["scoreable"] is False
    assert warehouse_development_marker()["infrastructure_blockers"] == [
        "warehouse_native_admission_pending"]


def test_warehouse_strong_single_robot_alternative_is_deployable():
    result = query_warehouse_development_case("strong_single_robot_path")
    assert result["feasibility_label"] == "deployable"
    assert result["alternative_path_gate"] == "not_required"


def test_warehouse_dual_light_robot_alternative_is_deployable():
    result = query_warehouse_development_case("dual_light_robot_path")
    assert result["feasibility_label"] == "deployable"


def test_warehouse_insufficient_dollies_downgrades_without_verified_review():
    # With the development (pending) certificate a solver not_deployable is
    # a platform failure, not a task truth.
    result = query_warehouse_development_case("insufficient_dollies")
    assert result["feasibility_label"] is None
    assert result["oracle_status"] == "failed"
    assert result["quality_failure"] == "certificate_review_pending"


def test_warehouse_insufficient_dollies_with_verified_certificate():
    registration = build_warehouse_registration()
    contract = build_warehouse_state_contract(registration)
    result = query_warehouse_development_case(
        "insufficient_dollies", registration=registration,
        state_contract=contract, certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"
    assert result["alternative_path_gate"] == "verified"
    assert result["formal_statistics"] is False


def test_warehouse_no_carry_capability_with_verified_certificate():
    registration = build_warehouse_registration()
    result = query_warehouse_development_case(
        "no_carry_capability_left", registration=registration,
        state_contract=build_warehouse_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"


def test_warehouse_grabber_exit_after_crate_at_dock_stays_deployable():
    # The intermediate state: wood crate stably at the dock, carton already
    # receipted, corridor open.  Only the wood receipt is outstanding, and a
    # receipt does not need the grabber that already left.
    result = query_warehouse_development_case("grabber_exits_after_crate_at_dock")
    assert result["feasibility_label"] == "deployable"


def test_warehouse_at_dock_intermediate_state_grounds_explicitly():
    registration = build_warehouse_registration()
    contract = build_warehouse_state_contract(registration)
    provider = make_warehouse_state_provider(
        "warehouse_at_dock_before_receipt",
        {"wood_crate_at_dock", "carton_receipted", "corridor_open"},
        robots=[{"id": "warehouse_go2_1",
                 "capabilities": ["navigate", "dock_handoff"], "payload_kg": 5}],
        resources={"shift_time_budget": 8, "dollies": 2})
    readers = make_warehouse_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(
        contract, registration, measurement_id="warehouse_at_dock_before_receipt",
        state=observation["state"], readbacks=packets)
    assert grounding["status"] == "verified"
    # Carrying predicate satisfied while the wood receipt is still false is
    # visible in the independent readback, not only in the state facts.
    physx = packets["warehouse_physx_objects"]["predicates"]
    assert physx["wood_crate_at_dock"] is True
    assert packets["warehouse_dock_receipts"]["predicates"]["wood_crate_receipted"] is False


def test_warehouse_light_unit_cannot_finish_without_handoff_capability():
    # Light unit alone can move the carton but nobody can issue receipts.
    registration = build_warehouse_registration()
    result = query_warehouse_development_case(
        "light_only_cannot_take_wood_path", registration=registration,
        state_contract=build_warehouse_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"


def test_warehouse_malformed_readback_fails_closed():
    registration = build_warehouse_registration()
    contract = build_warehouse_state_contract(registration)
    provider = make_warehouse_state_provider(
        "warehouse_initial", {"wood_crate_stored", "carton_stored"},
        robots=[{"id": "warehouse_m20_1",
                 "capabilities": ["carry", "dock_handoff"], "payload_kg": 40}],
        resources={"shift_time_budget": 8, "dollies": 2})
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    readers = make_warehouse_source_readers()
    packets = {source: readers[source]({}, observation) for source in readers}
    packets["warehouse_dock_receipts"]["source_version"] = "tampered-v9"
    grounding = ground_state(contract, registration,
                             measurement_id="warehouse_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["quality_failure"]
