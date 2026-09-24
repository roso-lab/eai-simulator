from demo.benchmark.development_track_marker import FACTORY_FIXTURE_HASH_MISMATCH
from demo.benchmark.goal_reader_factory import (
    build_factory_development_certificate,
    build_factory_public_task,
    build_factory_registration,
    build_factory_state_contract,
    factory_development_marker,
    make_factory_source_readers,
    make_factory_state_provider,
    query_factory_development_case,
)
from demo.benchmark.goal_trusted_state import ground_state


def _verified_certificate(registration):
    from copy import deepcopy

    from demo.benchmark.goal_alternative_path_certificate import (
        build_alternative_path_certificate,
    )

    return build_alternative_path_certificate(
        "factory", registration,
        path_classes=deepcopy(
            build_factory_development_certificate(registration)["path_classes"]),
        covered_measurement_ids=[point["id"] for point in
                                 registration["measurement_points"]],
        uncovered_path_policy="treated_as_uncovered",
        independent_review="verified",
        evidence_refs=["native:factory-path-review-v1"])


def test_factory_registration_and_contract_round_trip():
    registration = build_factory_registration()
    contract = build_factory_state_contract(registration)
    assert contract["scene_key"] == "factory"
    assert contract["scoreable"] is False
    # The public task keeps its formal release gate separate from this
    # contract-only backend.
    assert build_factory_public_task()["mission"]["scene_key"] == "factory"


def test_factory_marker_is_the_hash_mismatch_itself():
    marker = factory_development_marker(evidence_class="diagnostic")
    assert marker["asset_status"] == "development_unverified"
    assert marker["infrastructure_blockers"] == [FACTORY_FIXTURE_HASH_MISMATCH]
    assert marker["release_eligible"] is False


def test_factory_full_team_initial_is_deployable():
    result = query_factory_development_case("full_team_initial")
    assert result["feasibility_label"] == "deployable"
    assert result["development_track"]["infrastructure_blockers"] == [
        FACTORY_FIXTURE_HASH_MISMATCH]


def test_factory_observer_exit_downgrades_under_pending_certificate():
    result = query_factory_development_case("observer_exits_before_confirmation")
    assert result["feasibility_label"] is None
    assert result["oracle_status"] == "failed"
    assert result["quality_failure"] == "certificate_review_pending"


def test_factory_observer_exit_with_verified_certificate_is_not_deployable():
    registration = build_factory_registration()
    result = query_factory_development_case(
        "observer_exits_before_confirmation", registration=registration,
        state_contract=build_factory_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"
    assert result["alternative_path_gate"] == "verified"


def test_factory_corridor_capability_exit_before_service_is_not_deployable():
    registration = build_factory_registration()
    result = query_factory_development_case(
        "corridor_capability_exits_before_service", registration=registration,
        state_contract=build_factory_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"


def test_factory_navigation_exit_after_service_stays_deployable():
    # Response area already served: navigation is no longer necessary for
    # the remaining maintenance handover, which needs relay only.
    result = query_factory_development_case("navigation_exits_after_service_served")
    assert result["feasibility_label"] == "deployable"


def test_factory_budget_exhausted_with_verified_certificate():
    registration = build_factory_registration()
    result = query_factory_development_case(
        "budget_exhausted", registration=registration,
        state_contract=build_factory_state_contract(registration),
        certificate=_verified_certificate(registration))
    assert result["feasibility_label"] == "not_deployable"


def test_factory_service_vehicle_has_no_fabricated_carry_requirement():
    registration = build_factory_registration()
    serve = next(t for t in registration["model"]["transitions"]
                 if t["id"] == "serve_response_area")
    assert all(slot["requires_all"] == ["navigate"] for slot in serve["capability_slots"])
    assert all("carry" not in slot["requires_all"]
               for t in registration["model"]["transitions"]
               for slot in t["capability_slots"])


def test_factory_readers_report_observation_service_and_handover_kinds():
    registration = build_factory_registration()
    contract = build_factory_state_contract(registration)
    provider = make_factory_state_provider(
        "factory_initial", {"anomaly_pending_observation"},
        robots=[{"id": "factory_carter_1",
                 "capabilities": ["observe", "navigate"], "payload_kg": 40}],
        resources={"shift_time_budget": 6})
    readers = make_factory_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(contract, registration,
                             measurement_id="factory_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "verified"
    kinds = {binding["source_id"]: binding["evidence_kind"]
             for binding in contract["predicate_bindings"].values()}
    assert kinds["factory_anomaly_observation"] == "observation_record"
    assert kinds["factory_service_arrival"] == "physical_completion"
    assert kinds["factory_maintenance_handover"] == "business_receipt"


def test_factory_contract_only_backend_never_claims_native_verified():
    registration = build_factory_registration()
    assert registration["validation"]["scope"] == "synthetic_contract_development"
    certificate = build_factory_development_certificate(registration)
    assert certificate["independent_review"] == "pending"
    assert certificate["release_eligible"] is False
