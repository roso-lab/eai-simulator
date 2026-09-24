from demo.benchmark.goal_reader_city import (
    CITY_DEVELOPMENT_SCOPE,
    CITY_ENTITY_IDS,
    CITY_NATIVE_SCOPE,
    build_city_registration,
    build_city_state_contract,
    city_development_marker,
    make_city_source_readers,
    make_city_state_provider,
    query_city_development_case,
)
from demo.benchmark.goal_remaining_feasibility_oracle import (
    formal_oracle_release_review,
)
from demo.benchmark.goal_trusted_state import ground_state


def test_city_registration_and_contract_freeze_round_trip():
    registration = build_city_registration()
    contract = build_city_state_contract(registration)
    assert registration["validation"]["scope"] == CITY_DEVELOPMENT_SCOPE
    assert contract["scene_key"] == "city"
    assert contract["scoreable"] is False
    assert set(contract["entity_ids"]) == set(CITY_ENTITY_IDS)


def test_city_development_scope_is_not_release_ready():
    registration = build_city_registration()
    review = formal_oracle_release_review(registration, [])
    assert review["oracle_release_ready"] is False
    marker = city_development_marker(evidence_class="diagnostic")
    assert marker["infrastructure_blockers"] == ["city_native_admission_pending"]
    assert marker["release_eligible"] is False


def test_city_native_scope_requires_native_evidence():
    try:
        build_city_registration(validation_scope=CITY_NATIVE_SCOPE)
    except ValueError:
        pass
    else:
        raise AssertionError("native scope accepted development evidence")


def test_city_full_team_initial_is_deployable():
    result = query_city_development_case("full_team_initial")
    assert result["feasibility_label"] == "deployable"
    assert result["oracle_status"] == "verified"
    assert result["development_track"]["scene_key"] == "city"
    assert result["formal_statistics"] is False


def test_city_ground_alternative_survives_aerial_exit():
    result = query_city_development_case("aerial_exits_ground_remains")
    assert result["feasibility_label"] == "deployable"


def test_city_ground_only_capability_change_is_deployable():
    result = query_city_development_case("ground_only_capability_change")
    assert result["feasibility_label"] == "deployable"


def test_city_ground_exit_is_not_rescued_by_guiding_alternative_alone():
    # The aerial unit can still account for people, but traffic protection
    # has no aerial provider: the solver answers over every terminal
    # predicate, so the guiding alternative alone cannot rescue the task.
    result = query_city_development_case("ground_exits_aerial_remains")
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"


def test_city_no_guide_capability_left_is_not_deployable():
    # With neither ground guidance nor aerial service available and people
    # still unaccounted, the solver must exhaust both legal alternatives and
    # answer not_deployable through the full transition model.
    result = query_city_development_case("no_guide_capability_left")
    assert result["feasibility_label"] == "not_deployable"
    assert result["oracle_status"] == "verified"


def test_city_guides_exit_after_people_accounted_stays_deployable():
    # People are already accounted for: guidance capability is no longer
    # necessary, and one traffic unit can still finish the remaining service.
    result = query_city_development_case("guides_exit_after_people_accounted")
    assert result["feasibility_label"] == "deployable"


def test_city_exhausted_dispatch_budget_is_not_deployable():
    result = query_city_development_case("budget_exhausted")
    assert result["feasibility_label"] == "not_deployable"


def test_city_milestone_state_grounds_with_entity_versions():
    registration = build_city_registration()
    contract = build_city_state_contract(registration)
    provider = make_city_state_provider(
        "city_people_accounted", {"incident_assessed", "people_accounted"},
        robots=[{"id": "city_carter_1",
                 "capabilities": ["assess", "ground_guide", "traffic_control"],
                 "payload_kg": 40}], resources={"dispatch_budget": 6})
    readers = make_city_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation) for source in readers}
    grounding = ground_state(contract, registration,
                             measurement_id="city_people_accounted",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "verified"
    assert grounding["state_contract_sha256"] == contract["contract_sha256"]


def test_city_missing_source_readback_fails_closed():
    registration = build_city_registration()
    contract = build_city_state_contract(registration)
    provider = make_city_state_provider(
        "city_initial", set(),
        robots=[{"id": "city_carter_1",
                 "capabilities": ["assess", "ground_guide", "traffic_control"],
                 "payload_kg": 40}], resources={"dispatch_budget": 6})
    readers = make_city_source_readers()
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packets = {source: readers[source]({}, observation) for source in readers}
    del packets["city_people_tracking"]
    grounding = ground_state(contract, registration,
                             measurement_id="city_initial",
                             state=observation["state"], readbacks=packets)
    assert grounding["status"] == "failed"
    assert grounding["quality_failure"]


def test_city_native_probe_bridge_replaces_synthetic_sample():
    registration = build_city_registration()
    contract = build_city_state_contract(registration)
    provider = make_city_state_provider(
        "city_initial", set(),
        robots=[{"id": "city_carter_1",
                 "capabilities": ["assess", "ground_guide", "traffic_control"],
                 "payload_kg": 40}], resources={"dispatch_budget": 6})
    probes = {"city_incident_system": lambda state: {"incident_assessed": False}}
    readers = make_city_source_readers(probes)
    observation = provider({"assessment_id": "a1", "assessment_epoch": 0})
    packet = readers["city_incident_system"]({}, observation)
    assert packet["predicates"] == {"incident_assessed": False}
    assert packet["evidence_refs"] == ["city:city_incident_system:city_initial"]
