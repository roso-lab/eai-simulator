"""CPU checks for the opt-in City requested-target guidance profile."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pxr import Usd

from EAI_assets.humans.path_follower import PathFollowerOutput
from demo.city_benchmark.guide_admission import CityGuideAdmissionError
from demo.city_benchmark.live_witness import CityDemoRuntimeWitnessAdapter
from demo.city_benchmark.native_runner import CityNativeServiceRunner
from demo.city_benchmark.scene.people import (
    EpisodeAffectedPeopleController,
    EpisodeAffectedPersonRoster,
)
from demo.city_benchmark.scene.runtime import CityEpisodeServiceRuntime


class _Actor:
    def __init__(self, position):
        self._pending_pose = PathFollowerOutput(
            position=position, yaw=0.0, speed=0.0, locomotion="walk", finished=False)
        self._movement_gate = None
        self._follower = None
        self._movement_mode = "path"

    @property
    def pending_pose(self):
        return self._pending_pose


class _People:
    actor_ids = ("p1", "p2")

    def __init__(self):
        self.actors = {"p1": _Actor((0.0, 0.0, 0.0)),
                       "p2": _Actor((0.0, 2.0, 0.0))}
        self.paused = set()

    def _actor(self, actor_id):
        return self.actors[str(actor_id)]

    def _write_pose(self, actor_id, pose):
        self._actor(actor_id)._pending_pose = pose

    def position(self, actor_id):
        return self._actor(actor_id).pending_pose.position

    def pause(self, actor_id):
        self.paused.add(str(actor_id))

    def resume(self, actor_id):
        self.paused.discard(str(actor_id))

    # The witness adapter checks these legacy endpoints even when a concrete
    # person_controller callback owns the actual action.
    def play_action(self, *args, **kwargs):
        return None

    def set_path_speed(self, *args, **kwargs):
        return None


class _Reader:
    def __init__(self):
        self.mode = "clear"
        self.calls = []
        self.extra_evidence_ref = "native:evidence:a"

    def read_route(self, route_id, waypoints, *, half_extents_m, episode_id,
                   time_s, moving_entity_id=None):
        self.calls.append({
            "route_id": route_id,
            "waypoints": tuple(tuple(float(value) for value in point) for point in waypoints),
            "half_extents_m": tuple(float(value) for value in half_extents_m),
            "episode_id": episode_id,
            "time_s": float(time_s),
            "moving_entity_id": moving_entity_id,
        })
        if self.mode == "raise":
            raise RuntimeError("native reader failed")
        if self.mode == "none":
            return None
        status = "unknown" if self.mode == "unknown" else "measured"
        clear = self.mode not in {"blocked", "dynamic_blocked", "false_empty"}
        return {
            "route_id": route_id,
            "episode_id": episode_id,
            "observed_time_s": float(time_s) - (1.0 if self.mode == "stale" else 0.0),
            "source": "native_city_route_reader",
            "status": status,
            "clear": clear,
            "static_hits": ([{"collision": "/World/Obstacle"}]
                            if self.mode == "blocked" else []),
            "dynamic_envelopes": ({"other": {}}
                                  if self.mode == "dynamic_blocked" else {}),
            "evidence_ref": self.extra_evidence_ref,
        }


def _region_readback(region, *, occupants=(), static=False, status="measured"):
    return {
        "status": status,
        "clear": not static and not occupants,
        "static_hits": ([{"collision": "/World/Obstacle"}] if static else []),
        "dynamic_envelopes": {person_id: {} for person_id in occupants},
        "region_metadata": {"capacity": region["capacity"]},
    }


def _direct_provider(**kwargs):
    current = tuple(kwargs["current_xyz"])
    target = tuple(kwargs["target_xyz"])
    middle = tuple((current[index] + target[index]) * 0.5 for index in range(3))
    return current, middle, target


@pytest.fixture
def requested_service():
    people = _People()
    private = {
        "p1": {
            "speed_mps": 1.0,
            "motion": "walk",
            "assembly_region_id": "r1",
            "assembly_waypoints": [(111.0, 111.0, 0.0)],
            "body_half_extents_m": [0.2, 0.25, 0.8],
            "body_center_offset_m": [0.1, -0.2, 0.9],
        },
        "p2": {
            "speed_mps": 2.0,
            "motion": "jog",
            "assembly_region_id": "r2",
            "assembly_waypoints": [(222.0, 222.0, 0.0)],
            "body_half_extents_m": [0.2, 0.25, 0.8],
            "body_center_offset_m": [0.1, -0.2, 0.9],
        },
    }
    roster = EpisodeAffectedPersonRoster(
        people, private, episode_id="episode", source_id="native.city")
    controller = EpisodeAffectedPeopleController(people, roster)
    regions = {
        "r1": {"min": [9.5, -1.0, -0.05], "max": [10.5, 1.0, 2.0], "capacity": 1},
        "r2": {"min": [19.5, -1.0, -0.05], "max": [20.5, 1.0, 2.0], "capacity": 1},
    }
    city = SimpleNamespace(
        elapsed=5.0,
        episode_id="episode",
        city_service_source_id="native.city",
        people=people,
        stage=Usd.Stage.CreateInMemory(),
    )
    reader = _Reader()
    service = CityEpisodeServiceRuntime.__new__(CityEpisodeServiceRuntime)
    service.city = city
    service.region_reader = reader
    service.assembly_regions = deepcopy(regions)
    service.people_roster = roster
    service.people_controller = controller
    service._world_samples = [{
        "time_s": city.elapsed,
        "regions": {key: _region_readback(value) for key, value in regions.items()},
        "people": {person_id: {"position_xyz": list(people.position(person_id))}
                   for person_id in people.actor_ids},
        "vehicles": {},
    }]
    service._person_service_starts = {}
    service._vehicle_service_starts = {}
    service._pending_guidance_proposals = {}
    service._active_guidance_targets = {}
    service._last_guidance_admission_report = None
    service._guidance_profile = None
    service._requested_target_route_provider = None
    service._semantic_map_sha256 = None
    city._episode_service = service
    service.enable_requested_target_guidance(
        route_provider=_direct_provider, semantic_map_sha256="a" * 64)
    return service


def _route(record, person_id="p1", region_id="r1"):
    return next(row for row in record["routes"]
                if (row["person_id"], row["region_id"]) == (person_id, region_id))


def _admit(service, *people, target="r1"):
    return service.admit(
        "guide_people", tuple(people), "road", target, None, service.city.elapsed)


def test_query_covers_cartesian_product_and_has_no_service_side_effects(requested_service):
    service = requested_service
    before = {
        "active": deepcopy(service._active_guidance_targets),
        "pending": deepcopy(service._pending_guidance_proposals),
        "assignments": deepcopy(service.people_controller._assignments),
        "poses": {person_id: service.city.people.position(person_id)
                  for person_id in service.city.people.actor_ids},
        "regions": deepcopy(service.assembly_regions),
    }
    record = service.assembly_affordances()
    assert [(row["person_id"], row["region_id"]) for row in record["routes"]] == [
        ("p1", "r1"), ("p1", "r2"), ("p2", "r1"), ("p2", "r2")]
    assert all(row["status"] == "admissible" for row in record["routes"])
    assert service._active_guidance_targets == before["active"]
    assert service._pending_guidance_proposals == before["pending"]
    assert service.people_controller._assignments == before["assignments"]
    assert {person_id: service.city.people.position(person_id)
            for person_id in service.city.people.actor_ids} == before["poses"]
    assert service.assembly_regions == before["regions"]


def test_private_target_and_author_waypoint_permutation_does_not_change_requested_profile(
        requested_service):
    service = requested_service
    before = service.assembly_affordances()
    service.people_roster._rows["p1"].update(
        assembly_region_id="r2", assembly_waypoints=[(-900.0, 12.0, 0.0)])
    service.people_roster._rows["p2"].update(
        assembly_region_id="r1", assembly_waypoints=[(800.0, -21.0, 0.0)])
    after = service.assembly_affordances()
    assert after["candidates"] == before["candidates"]
    assert after["routes"] == before["routes"]


def test_non_author_target_flows_from_admission_to_controller_and_arrival(requested_service):
    service = requested_service
    assert service.affected_person_roster()["p1"]["assembly_region_id"] == "r1"
    assert _admit(service, "p1", target="r2") is True
    stored = deepcopy(service._pending_guidance_proposals["p1"]["proposal_waypoints_m"])
    action = service.guide_person("p1", target_region_id="r2")
    assignment = service.people_controller._assignments["p1"]
    assert action["assembly_region_id"] == assignment["assembly_region_id"] == "r2"
    assert assignment["final_waypoint"] == stored[-1] == [20.0, 0.0, 0.0]
    assert service.city.people._actor("p1")._follower._segments[-1].end == tuple(stored[-1])

    actor = service.city.people._actor("p1")
    actor._pending_pose = PathFollowerOutput(
        position=tuple(stored[-1]), yaw=0.0, speed=0.0, locomotion="walk", finished=True)
    arrived = service.people_controller.readback("p1", service.assembly_regions)
    assert arrived["arrived"] is True
    assert arrived["assembly_region_id"] == "r2"


def test_native_sweep_applies_body_center_offset_exactly_once(requested_service):
    service = requested_service
    row = _route(service.assembly_affordances(), "p1", "r1")
    call = next(item for item in service.region_reader.calls
                if item["route_id"] == row["evidence_ref"])
    raw = row["proposal_waypoints_m"]
    offset = row["body_center_offset_m"]
    expected = tuple(tuple(point[index] + offset[index] for index in range(3))
                     for point in raw)
    assert call["waypoints"] == expected
    assert call["waypoints"][0] == pytest.approx((0.1, -0.2, 0.9))


@pytest.mark.parametrize(("provider", "reason"), [
    (lambda **kwargs: None, "route_provider_returned_none"),
    (lambda **kwargs: (_ for _ in ()).throw(RuntimeError("planner failed")),
     "route_provider_contract_violation"),
    (lambda **kwargs: (kwargs["current_xyz"], (999.0, 999.0, 0.0)),
     "route_provider_contract_violation"),
])
def test_provider_failures_are_unavailable_infrastructure(requested_service, provider, reason):
    service = requested_service
    service._requested_target_route_provider = provider
    row = _route(service.assembly_affordances())
    assert row["status"] == "unavailable"
    assert reason in row["reason_codes"]
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1")
    assert rejected.value.infrastructure_failure is True
    assert reason in rejected.value.reason_codes


@pytest.mark.parametrize("mode", ["none", "raise", "unknown", "stale", "false_empty"])
def test_missing_unknown_or_stale_native_measurement_is_infrastructure(
        requested_service, mode):
    service = requested_service
    service.region_reader.mode = mode
    row = _route(service.assembly_affordances())
    assert row["status"] == "unavailable"
    assert "route_measurement_unknown" in row["reason_codes"]
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1")
    assert rejected.value.infrastructure_failure is True


def test_body_outside_registered_target_is_unavailable_infrastructure(requested_service):
    service = requested_service
    service.assembly_regions["r1"]["max"][2] = 1.0
    row = _route(service.assembly_affordances())
    assert row["status"] == "unavailable"
    assert "target_body_outside_region" in row["reason_codes"]
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1")
    assert rejected.value.infrastructure_failure is True
    assert "target_body_outside_region" in rejected.value.reason_codes


def test_measured_blocked_route_is_factual_not_infrastructure(requested_service):
    service = requested_service
    service.region_reader.mode = "blocked"
    row = _route(service.assembly_affordances())
    assert row["status"] == "blocked"
    assert row["reason_codes"] == ["route_static_blocked"]
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1")
    assert rejected.value.infrastructure_failure is False


@pytest.mark.parametrize(("mutation", "reason"), [
    ("static", "target_static_blocked"),
    ("full", "target_capacity_full"),
])
def test_target_static_block_and_capacity_are_factual(requested_service, mutation, reason):
    service = requested_service
    readback = service._world_samples[-1]["regions"]["r1"]
    if mutation == "static":
        readback["static_hits"] = [{"collision": "/World/Obstacle"}]
    else:
        readback["dynamic_envelopes"] = {"p2": {}}
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1")
    assert reason in rejected.value.reason_codes
    assert rejected.value.infrastructure_failure is False


def test_same_person_same_target_claim_counts_once(requested_service):
    service = requested_service
    service._active_guidance_targets["p1"] = "r1"
    service._world_samples[-1]["regions"]["r1"]["dynamic_envelopes"] = {"p1": {}}
    record = service.assembly_affordances()
    assert next(row for row in record["candidates"] if row["region_id"] == "r1")[
        "reason_codes"] == ["target_capacity_full"]
    assert _route(record, "p1", "r1")["status"] == "admissible"
    assert _admit(service, "p1", target="r1") is True


def test_person_reserved_for_other_target_is_rejected(requested_service):
    service = requested_service
    service._active_guidance_targets["p1"] = "r2"
    row = _route(service.assembly_affordances(), "p1", "r1")
    assert row["status"] == "blocked"
    assert "person_reserved_for_other_target" in row["reason_codes"]
    with pytest.raises(CityGuideAdmissionError) as rejected:
        _admit(service, "p1", target="r1")
    assert rejected.value.infrastructure_failure is False


def test_stop_and_partial_failure_rollback_release_requested_reservations(requested_service):
    service = requested_service
    _admit(service, "p1", target="r1")
    service.guide_person("p1", target_region_id="r1")
    assert service._active_guidance_targets == {"p1": "r1"}
    service.stop_person("p1")
    assert service._active_guidance_targets == {}
    assert service._pending_guidance_proposals == {}

    service._active_guidance_targets.update(p1="r1", p2="r2")
    service._pending_guidance_proposals.update(p1={}, p2={})
    adapter = CityDemoRuntimeWitnessAdapter(
        service.city, episode_id="episode", source_id="native.city",
        affected_people=service.affected_person_roster(),
        person_stop=service.stop_person)

    class _Witness:
        def _accept(self, *args, **kwargs):
            return None

        def record_partial_failure(self, *args, **kwargs):
            self.failure = (args, kwargs)

    witness = _Witness()
    adapter._fail_batch(
        witness, "request", ("p1", "p2"), "controller",
        stop=service.stop_person, identity="entity_id", stopped="paused")
    assert service._active_guidance_targets == {}
    assert service._pending_guidance_proposals == {}
    assert witness.failure[1]["attempted_ids"] == ("p1", "p2")


def test_missing_or_stale_proposal_fails_without_author_route_fallback(requested_service):
    service = requested_service
    with pytest.raises(CityGuideAdmissionError) as missing:
        service.guide_person("p1", target_region_id="r2")
    assert missing.value.reason_codes == ("route_proposal_missing",)
    assert service.people_controller._assignments == {}

    _admit(service, "p1", target="r2")
    service.city.elapsed += 0.25
    with pytest.raises(CityGuideAdmissionError) as stale:
        service.guide_person("p1", target_region_id="r2")
    assert stale.value.reason_codes == ("route_proposal_stale",)
    assert service.people_controller._assignments == {}


def test_legacy_controller_keeps_private_route_and_one_argument_callback_compatibility(
        requested_service):
    service = requested_service
    service._guidance_profile = None
    action = service.guide_person("p1")
    assert action["assembly_region_id"] == "r1"
    assert service.people_controller._assignments["p1"]["final_waypoint"] == [111.0, 111.0, 0.0]

    calls = []
    callback = lambda person_id: calls.append(person_id) or {"entity_id": person_id}
    adapter = CityDemoRuntimeWitnessAdapter(
        SimpleNamespace(people=service.city.people), episode_id="episode",
        source_id="native.city", affected_people=service.affected_person_roster(),
        person_controller=callback)
    assert adapter.guide_person("p1", target_region_id="r1")["entity_id"] == "p1"
    assert calls == ["p1"]


def test_requested_profile_rejects_controller_without_target_parameter(requested_service):
    service = requested_service
    callback = lambda person_id: {"entity_id": person_id}
    adapter = CityDemoRuntimeWitnessAdapter(
        service.city, episode_id="episode", source_id="native.city",
        affected_people=service.affected_person_roster(), person_controller=callback)
    with pytest.raises(CityGuideAdmissionError) as rejected:
        adapter.guide_person("p1", target_region_id="r2")
    assert rejected.value.infrastructure_failure is True
    assert rejected.value.report["controller_target_unsupported"] is True


def test_runner_reports_profile_enabled_after_runner_construction(requested_service):
    service = requested_service
    service._guidance_profile = None
    runner = CityNativeServiceRunner.__new__(CityNativeServiceRunner)
    runner.runtime = SimpleNamespace(_episode_service=service)
    runner._initial_guidance_profile = None
    assert runner.guidance_profile is None
    service._guidance_profile = "requested_target_v1"
    assert runner.guidance_profile == "requested_target_v1"


def test_state_fingerprint_tracks_admission_state_but_ignores_evidence_metadata(requested_service):
    service = requested_service
    candidate = service._candidate_affordance("r1")
    baseline = service._requested_route_affordance("p1", "r1", candidate)

    service.region_reader.extra_evidence_ref = "native:evidence:b"
    same = service._requested_route_affordance("p1", "r1", candidate)
    assert same["state_sha256"] == baseline["state_sha256"]

    original_provider = service._requested_target_route_provider
    service._requested_target_route_provider = lambda **kwargs: (
        tuple(kwargs["current_xyz"]), (3.0, 3.0, 0.0), tuple(kwargs["target_xyz"]))
    changed_route = service._requested_route_affordance("p1", "r1", candidate)
    assert changed_route["state_sha256"] != baseline["state_sha256"]
    service._requested_target_route_provider = original_provider

    service.region_reader.mode = "blocked"
    blocked = service._requested_route_affordance("p1", "r1", candidate)
    assert blocked["state_sha256"] != baseline["state_sha256"]
    service.region_reader.mode = "clear"

    capacity = deepcopy(candidate)
    capacity["capacity"] = 2
    assert service._requested_route_affordance("p1", "r1", capacity)[
        "state_sha256"] != baseline["state_sha256"]

    service.people_roster._rows["p1"]["body_half_extents_m"] = [0.3, 0.25, 0.8]
    assert service._requested_route_affordance("p1", "r1", candidate)[
        "state_sha256"] != baseline["state_sha256"]
    service.people_roster._rows["p1"]["body_half_extents_m"] = [0.2, 0.25, 0.8]

    service._semantic_map_sha256 = "b" * 64
    assert service._requested_route_affordance("p1", "r1", candidate)[
        "state_sha256"] != baseline["state_sha256"]
