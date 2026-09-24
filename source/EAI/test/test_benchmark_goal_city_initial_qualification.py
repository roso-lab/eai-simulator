"""Focused CPU checks for evaluator-only City initial qualification capture."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_autonomous import build_city_autonomous_contract
from demo.benchmark.goal_city_initial_qualification import (
    capture_city_initial_qualification,
)
from demo.benchmark.provenance import canonical_sha256


PERSON_IDS = tuple(f"affected_person_{index:02d}" for index in range(4))
VEHICLE_IDS = tuple(f"following_vehicle_{index:02d}" for index in range(6))


class _ClockCity:
    def __init__(self, service, readings=(10.0, 10.0)):
        self._episode_service = service
        self._readings = list(readings)
        self.read_count = 0

    @property
    def elapsed(self):
        index = min(self.read_count, len(self._readings) - 1)
        self.read_count += 1
        return self._readings[index]


class _Service:
    guidance_profile = "requested_target_v1"

    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def assembly_affordances(self):
        self.calls += 1
        return deepcopy(self.raw)

    def forbidden_mutator(self):  # pragma: no cover - documents the boundary
        raise AssertionError("capture attempted a mutating service call")


def _observation(*, captured=10.0, episode="native-episode", source="isaac.city.runtime"):
    entities = [
        {"id": person_id, "kind": "person", "position_xyz": [float(index), 1.0, .3],
         "episode_id": episode, "source_id": source,
         "private_target": "DO_NOT_COPY"}
        for index, person_id in enumerate(PERSON_IDS)
    ]
    entities.extend(
        {"id": vehicle_id, "kind": "vehicle", "position_xyz": [float(index), 2.0, .2],
         "following_incident": True, "is_accident_vehicle": False,
         "episode_id": episode, "source_id": source,
         "private_assignment": "DO_NOT_COPY"}
        for index, vehicle_id in enumerate(VEHICLE_IDS)
    )
    return {
        "id": "initial-observation", "episode_id": episode, "source_id": source,
        "captured_at_s": captured, "delivered_at_s": captured,
        "valid_until_s": captured + 1.0, "entities": entities,
        "author_plan": "DO_NOT_COPY",
    }


def _raw(public, *, episode="native-episode", source="isaac.city.runtime"):
    regions = sorted(row["id"] for row in public["public_world"]["regions"]
                     if row["kind"] == "assembly_candidate")
    candidates = []
    for index, region_id in enumerate(regions):
        candidates.append({
            "region_id": region_id, "min": [index * 4.0, 0.0, .25],
            "max": [index * 4.0 + 2.0, 2.0, 2.2], "capacity": 1,
            "occupancy_count": 0, "reservation_count": 0,
            "occupant_ids": [], "reservation_ids": [], "status": "measured",
            "reason_codes": [], "bound_person_ids": ["DO_NOT_COPY"],
            "native_readback": {"private": "DO_NOT_COPY", "region_metadata": {
                "capacity": 1,
                "derivation": "author_candidate_radius_and_bound_person_envelope_containment",
                "bound_person_ids": ["DO_NOT_COPY"],
            }},
        })
    routes = []
    for person_index, person_id in enumerate(PERSON_IDS):
        for region_index, region_id in enumerate(regions):
            admissible = person_index == region_index
            routes.append({
                "person_id": person_id, "region_id": region_id,
                "status": "admissible" if admissible else "blocked",
                "reason_codes": [] if admissible else ["route_static_blocked"],
                "length_m": float(person_index + region_index + 1),
                "estimated_travel_s": float(person_index + region_index + 1) / 2,
                "evidence_ref": f"native:{person_id}:{region_id}",
                "state_sha256": f"{person_index + region_index + 1:064x}"[-64:],
                "proposal_waypoints_m": ["DO_NOT_COPY"],
                "route_readback": {"private": "DO_NOT_COPY"},
            })
    return {
        "schema": "eai.city.assembly_affordance", "version": 1,
        "profile": "requested_target_v1", "episode_id": episode, "source_id": source,
        "captured_at_s": 10.0, "valid_until_s": 11.0,
        "semantic_map_sha256": "a" * 64, "private_binding": "DO_NOT_COPY",
        "candidates": candidates, "routes": routes,
    }


def _context(public, *, observation=None, raw=None, readings=(10.0, 10.0)):
    observation = _observation() if observation is None else observation
    raw = _raw(public) if raw is None else raw
    service = _Service(raw)
    host = SimpleNamespace(observation=lambda ref: deepcopy(observation))
    adapter = SimpleNamespace(episode_id="native-episode", source_id="isaac.city.runtime")
    runner = SimpleNamespace(host=host, adapter=adapter,
                             _observation_ref="initial-observation")
    city = _ClockCity(service, readings=readings)
    return {"city": city, "runner": runner,
            "guidance_observation": "initial-observation"}, service


def _capture(ctx, public):
    return capture_city_initial_qualification(
        ctx, public, episode_id="goal-episode", clock_epoch_s=10.0)


def _v2_context():
    from source.EAI.test.test_benchmark_goal_city_assembly_v2 import _fixture

    public, raw, _ = _fixture()
    raw.update(episode_id="native-episode", source_id="isaac.city.runtime")
    for candidate in raw["candidates"]:
        footprint = {key: deepcopy(candidate[key]) for key in (
            "region_id", "footprint", "center_xy_m", "radius_m",
            "support_plane_z_m", "capacity", "geometry_sha256")}
        support = {"profile": "city_static_assembly_support_v1",
                   "episode_id": raw["episode_id"], "source_id": raw["source_id"],
                   "captured_native_s": raw["captured_at_s"],
                   "semantic_map_sha256": raw["semantic_map_sha256"],
                   "candidate_geometry_sha256": raw["candidate_geometry_sha256"],
                   "geometry_sha256": candidate["geometry_sha256"],
                   "status": "sampled_supported", "evidence_ref": candidate["support_evidence_ref"],
                   "execution_profile_enabled": False}
        candidate["native_readback"] = {
            "source": "native_city_assembly_footprint_reader", "episode_id": raw["episode_id"],
            "observed_time_s": raw["captured_at_s"], "footprint": footprint,
            "support": support, "status": "measured", "private_paths": "DO_NOT_COPY"}
    ctx, service = _context(public, raw=raw)
    service.guidance_profile = raw["profile"]
    return ctx, service, public


def test_initial_v2_binds_independent_geometry_and_support_without_oracle_promotion():
    ctx, service, public = _v2_context()
    before = deepcopy(service.raw)
    result = _capture(ctx, public)
    affordance = result["measurements"]["assembly_affordance"]
    assert service.raw == before and service.calls == 1
    assert affordance["profile"] == service.guidance_profile
    assert affordance["version"] == 2 and affordance["measured_pair_count"] == 16
    assert affordance["exact_pair_record_coverage"] is True
    assert affordance["admissible_graph_matching"]["cardinality"] == 4
    assert result["quality_issues"] == []
    assert "author_derived_candidate_geometry_not_person_independent" not in result["limitation_codes"]
    assert "sampled_support_not_continuous_surface_coverage" in result["limitation_codes"]
    assert result["hard_predicate_coverage"]["assembly_candidate_geometry_capacity"]["status"] == "measured_initial"
    assert result["hard_predicate_coverage"]["person_safe_assembly_arrival"]["status"] == "execution_only"
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None
    assert not any(result[key] for key in ("platform_valid", "scoreable", "execution_validated",
                                           "business_outcomes_validated"))
    assert "DO_NOT_COPY" not in json.dumps(result)
    # A collector does not activate execution; its flag is not a runtime profile.
    assert before["candidates"][0]["native_readback"]["support"]["execution_profile_enabled"] is False


@pytest.mark.parametrize("case", ["profile", "readback_time", "support_source", "support_time",
                                  "support_geometry", "support_ref", "readback_footprint"])
def test_initial_v2_bad_native_support_joins_remain_platform_failures(case):
    ctx, service, public = _v2_context()
    raw = service.raw
    readback = raw["candidates"][0]["native_readback"]
    if case == "profile":
        service.guidance_profile = "requested_target_v1"
    elif case == "readback_time":
        readback["observed_time_s"] -= .02
    elif case == "support_source":
        readback["support"]["source_id"] = "foreign"
    elif case == "support_time":
        readback["support"]["captured_native_s"] -= .02
    elif case == "support_geometry":
        readback["support"]["geometry_sha256"] = "f" * 64
    elif case == "support_ref":
        readback["support"]["evidence_ref"] = "unjoined"
    else:
        readback["footprint"]["radius_m"] = 2.
    result = _capture(ctx, public)
    assert result["measurements"]["assembly_affordance"]["status"] == "unavailable"
    assert "assembly_affordance_reader_failed" in {row["code"] for row in result["quality_issues"]}
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None


def test_initial_v2_unknown_support_retains_all_records_without_geometry_qualification():
    ctx, service, public = _v2_context()
    candidate = service.raw["candidates"][0]
    candidate.update(status="unavailable", support_status="unknown",
                     reason_codes=["target_support_unknown"])
    candidate["native_readback"].update(status="unknown")
    candidate["native_readback"]["support"].update(status="unknown")
    for route in service.raw["routes"]:
        if route["region_id"] == candidate["region_id"]:
            route.update(status="unavailable", reason_codes=["target_support_unknown"],
                         length_m=None, estimated_travel_s=None)
    result = _capture(ctx, public)
    affordance = result["measurements"]["assembly_affordance"]
    assert affordance["recorded_pair_count"] == 16 and affordance["measured_pair_count"] == 12
    assert affordance["route_status_counts"]["unavailable"] == 4
    assert result["hard_predicate_coverage"]["assembly_candidate_geometry_capacity"]["status"] == "qualification_limited"
    assert "initial_assembly_support_or_occupancy_unavailable" in {
        row["code"] for row in result["quality_issues"]}
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None


def test_capture_is_initial_read_only_exact_and_never_promotes_release():
    public, _ = build_city_autonomous_contract()
    ctx, service = _context(public)
    before_raw = deepcopy(service.raw)
    result = _capture(ctx, public)

    assert result["schema"] == "eai.benchmark.city_initial_qualification"
    assert result["version"] == 1
    assert result["boundary"] == "before_participant_initialize"
    assert result["measurement_scope"] == "initial_only"
    assert result["public_task_sha256"] == canonical_sha256(public)
    assert result["native_episode_id"] == "native-episode"
    assert result["source_id"] == "isaac.city.runtime"
    assert result["captured_native_s"] == 10.0
    assert result["captured_at_s"] == 0.0
    assert result["measurements"]["native_clock"] == {
        "before_s": 10.0, "after_s": 10.0, "stable": True}
    assert result["measurements"]["observation"]["person_ids"] == list(PERSON_IDS)
    assert result["measurements"]["observation"]["following_vehicle_ids"] == list(VEHICLE_IDS)
    affordance = result["measurements"]["assembly_affordance"]
    assert affordance["measured_pair_count"] == affordance["expected_pair_count"] == 16
    assert affordance["admissible_graph_matching"] == {
        "cardinality": 4,
        "semantics": "diagnostic_one_proposal_record_per_pair_capacity_one_only",
        "alternative_complete": False,
    }
    assert {row["derivation"] for row in affordance["candidates"]} == {
        "author_candidate_radius_and_bound_person_envelope_containment"}
    assert service.calls == 1 and service.raw == before_raw
    assert ctx["city"].read_count == 2
    assert "DO_NOT_COPY" not in json.dumps(result)
    coverage = result["hard_predicate_coverage"]
    assert coverage["responder_route_and_arrival"]["outcome_id"] == "response_dispatch"
    assert coverage["responder_capability_manifest"]["entity_ids"] == [
        "carter_1", "go2_1", "iris_1"]
    assert coverage["incident_report_capability"]["entity_ids"] == ["iris_1"]
    assert set(PERSON_IDS) | {
        f"assembly_candidate_{index:03d}" for index in range(1, 5)
    } == set(coverage["person_candidate_route_proposals"]["entity_ids"])
    assert coverage["person_safe_assembly_arrival"]["required_producers"] == [
        "CityEpisodeEvidenceHost.record_person_readback"]
    assert all(row["required_producers"] for row in coverage.values())
    assert set(public_outcome["id"] for public_outcome in public["required_outcomes"]) \
        <= {row["outcome_id"] for row in coverage.values()}
    assert {row["status"] for row in coverage.values()} <= {
        "measured_initial", "declared_only", "execution_only", "unavailable",
        "qualification_limited"}
    assert result["quality_issues"] == []
    assert "author_derived_candidate_geometry_not_person_independent" in result["limitation_codes"]
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    for flag in ("platform_valid", "execution_validated",
                 "business_outcomes_validated", "scoreable"):
        assert result[flag] is False


def test_missing_double_readers_return_quality_gaps_instead_of_raising():
    public, _ = build_city_autonomous_contract()
    ctx, _service = _context(public)
    ctx["runner"].host = SimpleNamespace()
    ctx["city"]._episode_service = SimpleNamespace(guidance_profile="requested_target_v1")
    result = _capture(ctx, public)
    codes = {row["code"] for row in result["quality_issues"]}
    assert codes == {"initial_observation_reader_missing", "assembly_affordance_reader_missing"}
    # People cannot be bound, so the collector does not attempt or fabricate pair evidence.
    assert result["measurements"]["assembly_affordance"]["status"] == "unavailable"
    assert result["feasibility_label"] is None


@pytest.mark.parametrize("mutation, expected", [
    ("source", "initial_observation_malformed"),
    ("future", "initial_observation_malformed"),
    ("expired", "initial_observation_malformed"),
    ("entity_source", "initial_observation_malformed"),
    ("partial_roster", "affected_person_roster_incomplete"),
    ("partial_pairs", "assembly_affordance_reader_failed"),
    ("affordance_source", "assembly_affordance_reader_failed"),
])
def test_source_roster_and_pair_mutations_fail_closed(mutation, expected):
    public, _ = build_city_autonomous_contract()
    observation, raw = _observation(), _raw(public)
    if mutation == "source":
        observation["source_id"] = "foreign"
    elif mutation == "future":
        observation["captured_at_s"] = observation["delivered_at_s"] = 11.0
    elif mutation == "expired":
        observation["valid_until_s"] = 10.0
    elif mutation == "entity_source":
        observation["entities"][0]["source_id"] = "foreign"
    elif mutation == "partial_roster":
        observation["entities"] = [row for row in observation["entities"]
                                   if row["id"] != PERSON_IDS[-1]]
        raw["routes"] = [row for row in raw["routes"] if row["person_id"] != PERSON_IDS[-1]]
    elif mutation == "partial_pairs":
        raw["routes"].pop()
    else:
        raw["source_id"] = "foreign"
    ctx, _ = _context(public, observation=observation, raw=raw)
    result = _capture(ctx, public)
    codes = {row["code"] for row in result["quality_issues"]}
    assert expected in codes
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None
    statuses = {row["status"] for row in result["hard_predicate_coverage"].values()}
    assert statuses <= {"measured_initial", "declared_only", "execution_only",
                        "unavailable", "qualification_limited"}
    if mutation == "partial_roster":
        affordance = result["measurements"]["assembly_affordance"]
        assert affordance["expected_pair_count"] == 16
        assert affordance["exact_pair_record_coverage"] is False
    if mutation == "expired":
        coverage = result["hard_predicate_coverage"]
        assert coverage["affected_person_roster"]["status"] == "unavailable"
        assert coverage["person_candidate_route_proposals"]["status"] == "unavailable"


@pytest.mark.parametrize("malformation", ["null_candidate", "string_metadata"])
def test_malformed_nested_affordance_rows_are_quality_gaps(malformation):
    public, _ = build_city_autonomous_contract()
    raw = _raw(public)
    if malformation == "null_candidate":
        raw["candidates"][0] = None
    else:
        raw["candidates"][0]["native_readback"]["region_metadata"] = "bad"
    ctx, _ = _context(public, raw=raw)

    result = _capture(ctx, public)

    assert result["measurements"]["assembly_affordance"]["status"] == "unavailable"
    assert "assembly_affordance_reader_failed" in {
        row["code"] for row in result["quality_issues"]}


def test_clock_change_is_a_quality_gap_and_not_a_task_label():
    public, _ = build_city_autonomous_contract()
    ctx, _ = _context(public, readings=(10.0, 10.25))
    result = _capture(ctx, public)
    assert result["measurements"]["native_clock"]["stable"] is False
    assert "native_clock_changed_during_capture" in {
        row["code"] for row in result["quality_issues"]}
    assert result["hard_predicate_coverage"]["affected_person_roster"]["status"] == "unavailable"
    assert all(row["status"] != "measured_initial"
               for row in result["hard_predicate_coverage"].values())
    assert result["feasibility_label"] is None and result["platform_valid"] is False


def test_unavailable_pair_records_are_never_counted_as_measurements():
    public, _ = build_city_autonomous_contract()
    raw = _raw(public)
    for route in raw["routes"][:3]:
        route.update(status="unavailable", reason_codes=["route_measurement_unknown"],
                     length_m=None, estimated_travel_s=None)
    ctx, _ = _context(public, raw=raw)
    result = _capture(ctx, public)
    affordance = result["measurements"]["assembly_affordance"]
    assert affordance["recorded_pair_count"] == affordance["expected_pair_count"] == 16
    assert affordance["measured_pair_count"] == 13
    assert affordance["route_status_counts"]["unavailable"] == 3
    assert affordance["status"] == "qualification_limited"
    assert affordance["admissible_graph_matching"]["semantics"] == "diagnostic_one_proposal_record_per_pair_capacity_one_only"
    assert affordance["exact_pair_record_coverage"] is True
    assert result["hard_predicate_coverage"]["person_candidate_route_proposals"]["status"] == "qualification_limited"


def test_wrong_task_is_a_programming_error():
    public, _ = build_city_autonomous_contract()
    ctx, _ = _context(public)
    wrong = deepcopy(public)
    wrong["task_id"] = "CITY-GOAL-NATIVE-02"
    with pytest.raises(ValueError, match="exact frozen v3 CITY-GOAL-NATIVE-01"):
        _capture(ctx, wrong)


def test_same_id_task_mutation_fails_before_any_native_reader_call():
    public, _ = build_city_autonomous_contract()
    ctx, service = _context(public)
    changed = deepcopy(public)
    changed["budgets"]["simulation_s"] += 1.0
    with pytest.raises(ValueError, match="exact frozen v3 CITY-GOAL-NATIVE-01"):
        _capture(ctx, changed)
    assert ctx["city"].read_count == 0
    assert service.calls == 0
