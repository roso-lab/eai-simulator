"""Focused CPU contract tests for requested-target v2 disk execution."""

from copy import deepcopy

import pytest
from EAI_assets.humans.path_follower import PathFollowerOutput

from demo.city_benchmark.assembly_support import (
    EXECUTION_PROFILE,
    FOOTPRINT_SCHEMA,
    FOOTPRINT_VERSION,
    PROFILE as SUPPORT_PROFILE,
)
from demo.city_benchmark.guide_admission import (
    CityGuideAdmissionError,
    canonical_state_sha256,
)
from demo.city_benchmark.live_witness import CityDemoRuntimeWitnessAdapter
from demo.city_benchmark.tests.test_requested_target_guidance import (
    _Actor,
    _direct_provider,
    _region_readback,
    requested_service,
)
from demo.city_benchmark.scene.usd_scene import CityNativeRegionReader


IDS = tuple(f"assembly_candidate_{index:03d}" for index in range(1, 5))


def _footprint_profile():
    geometries = [{
        "region_id": region_id,
        "footprint": "disk",
        "center_xy_m": [10.0 * index, 0.0],
        "radius_m": 0.75,
        "support_plane_z_m": 0.3,
        "capacity": 1,
    } for index, region_id in enumerate(IDS, 1)]
    candidates = [{**row, "geometry_sha256": canonical_state_sha256(row)}
                  for row in geometries]
    return {
        "schema": FOOTPRINT_SCHEMA,
        "version": FOOTPRINT_VERSION,
        "profile": EXECUTION_PROFILE,
        "semantic_map_sha256": "a" * 64,
        "candidate_geometry_sha256": canonical_state_sha256({
            "candidates": geometries}),
        "candidates": candidates,
    }


class _SupportReader:
    def __init__(self, service, profile):
        self.service = service
        self.profile = profile
        self.calls = 0
        self.mode = "supported"

    def __call__(self):
        self.calls += 1
        now = float(self.service.city.elapsed)
        source = self.service.city.city_service_source_id
        captured = now
        status = "sampled_supported"
        reason = None
        if self.mode == "unsupported":
            status, reason = "unsupported", "support_sample_missing"
        elif self.mode == "unknown":
            status, reason = "unknown", "support_read_failed:test"
        elif self.mode == "source_mismatch":
            source = "wrong-source"
        elif self.mode == "stale":
            captured = now - 0.1
        return {
            "schema": "eai.city.assembly_support",
            "version": 1,
            "profile": SUPPORT_PROFILE,
            "episode_id": self.service.city.episode_id,
            "source_id": source,
            "captured_native_s": captured,
            "native_clock_after_s": captured,
            "same_tick": True,
            "semantic_map_sha256": self.profile["semantic_map_sha256"],
            "candidate_geometry_sha256": self.profile["candidate_geometry_sha256"],
            "candidates": [{**deepcopy(row), "status": status, "reason": reason}
                           for row in self.profile["candidates"]],
            "execution_profile_enabled": False,
        }


class _DiskReader:
    def __init__(self):
        self.route_calls = []
        self.contained = set()
        self.occupants = set()
        self.foreign = {}
        self.block_tall = False

    def read_route(self, route_id, waypoints, *, half_extents_m, episode_id,
                   time_s, moving_entity_id=None):
        half = tuple(float(value) for value in half_extents_m)
        self.route_calls.append((route_id, tuple(tuple(row) for row in waypoints), half))
        static_blocked = self.block_tall and half[2] > 1.5
        target = tuple(tuple(row) for row in waypoints)[-1]
        body = {"min": [target[axis] - half[axis] for axis in range(3)],
                "max": [target[axis] + half[axis] for axis in range(3)]}
        dynamic = {
            identity: envelope for identity, envelope in self.foreign.items()
            if isinstance(envelope, dict) and set(envelope) >= {"min", "max"}
            and all(float(body["min"][axis]) <= float(envelope["max"][axis])
                    and float(envelope["min"][axis]) <= float(body["max"][axis])
                    for axis in range(3))
        }
        blocked = static_blocked or bool(dynamic)
        return {
            "route_id": route_id,
            "episode_id": episode_id,
            "observed_time_s": float(time_s),
            "status": "measured",
            "clear": not blocked,
            "static_hits": ([{"collision": "/World/Canopy"}]
                            if static_blocked else []),
            "dynamic_envelopes": dynamic,
        }

    def read_assembly_footprint(self, footprint, *, episode_id, time_s,
                                support, affected_person_ids):
        occupants = sorted(self.occupants & set(affected_person_ids))
        status = ("measured" if support["status"] == "sampled_supported"
                  else "unsupported" if support["status"] == "unsupported"
                  else "unknown")
        return {
            "region_id": footprint["region_id"],
            "episode_id": episode_id,
            "observed_time_s": float(time_s),
            "status": status,
            "support_status": support["status"],
            "occupant_ids": occupants,
            "contained_body_ids": sorted(self.contained & set(affected_person_ids)),
            "dynamic_envelopes": {identity: {
                "min": [float(footprint["center_xy_m"][0]) - 0.2,
                        float(footprint["center_xy_m"][1]) - 0.2, 0.2],
                "max": [float(footprint["center_xy_m"][0]) + 0.2,
                        float(footprint["center_xy_m"][1]) + 0.2, 1.4],
            } for identity in occupants},
            "untrusted_dynamic_envelopes": deepcopy(self.foreign),
            "all_dynamic_envelopes": {},
        }


@pytest.fixture
def v2_service(requested_service):
    service = requested_service
    people = service.city.people
    for index in (3, 4):
        identity = f"p{index}"
        people.actors[identity] = _Actor((0.0, float(index), 0.3))
        service.people_roster._rows[identity] = {
            **deepcopy(service.people_roster._rows["p2"]),
            "id": identity,
            "assembly_region_id": f"private-{identity}",
            "assembly_waypoints": [(900.0 + index, 900.0, 0.3)],
        }
    service.city.elapsed = 5.0
    service.region_reader = _DiskReader()
    service._world_samples = [{
        "time_s": 5.0,
        "regions": {region_id: _region_readback({"capacity": 1}) for region_id in IDS},
        "people": {person_id: {"position_xyz": list(service.city.people.position(person_id))}
                   for person_id in service.affected_person_roster()},
        "vehicles": {},
    }]
    service._person_service_starts = {}
    service._pending_guidance_proposals = {}
    service._active_guidance_targets = {}
    service._guidance_profile = None
    service._assembly_footprint_profile = None
    service._assembly_support_reader = None
    service._assembly_support_cache = None
    profile = _footprint_profile()
    support = _SupportReader(service, profile)
    service.enable_requested_target_guidance(
        route_provider=_direct_provider,
        semantic_map_sha256="a" * 64,
        profile=EXECUTION_PROFILE,
        assembly_footprint_profile=profile,
        assembly_support_reader=support)
    service.test_support = support
    return service


def _admit(service, person, target=IDS[0]):
    return service.admit(
        "guide_people", (person,), "road", target, None, service.city.elapsed)


def test_v2_all16_is_side_effect_free_and_author_permutation_invariant(v2_service):
    service = v2_service
    first = service.assembly_affordances()
    assert first["version"] == 2 and first["profile"] == EXECUTION_PROFILE
    assert len(first["candidates"]) == 4 and len(first["routes"]) == 16
    assert first["assembly_support_measurement"]["schema"] == \
        "eai.city.assembly_support"
    assert len(first["assembly_support_measurement"]["candidates"]) == 4
    assert all(row["radius_m"] == 0.75 and row["capacity"] == 1
               and row["support_plane_z_m"] == 0.3 for row in first["candidates"])
    assert not service._pending_guidance_proposals
    assert not service._active_guidance_targets
    assert service.test_support.calls == 1

    for index, row in enumerate(service.people_roster._rows.values()):
        row["assembly_region_id"] = f"author-permuted-{index}"
        row["assembly_waypoints"] = [(1000.0 + index, -1000.0, 99.0)]
    second = service.assembly_affordances()
    assert second == first
    assert service.test_support.calls == 1  # same native tick is shared

    service.city.elapsed += 0.1
    service.assembly_affordances()
    assert service.test_support.calls == 2  # a new tick reacquires support


@pytest.mark.parametrize("mode", ["unknown", "source_mismatch", "stale"])
def test_unknown_support_dominates_other_measured_blocks(v2_service, mode):
    service = v2_service
    service.test_support.mode = mode
    service.region_reader.foreign = {"robot-1": {}}
    service.region_reader.occupants = {"p2"}
    service.city.elapsed += 0.1
    record = service.assembly_affordances()

    assert all(row["status"] == "unavailable"
               and row["support_status"] == "unknown"
               and "target_support_unknown" in row["reason_codes"]
               for row in record["candidates"])
    assert all(row["status"] == "unavailable" for row in record["routes"])
    assert len(record["routes"]) == 16


def test_v2_reservation_is_selected_request_only_idempotent_and_released(v2_service):
    service = v2_service
    service.assembly_affordances()
    assert not service._pending_guidance_proposals

    assert _admit(service, "p1") is True
    assert _admit(service, "p1") is True
    candidate = service._candidate_affordance(IDS[0])
    assert candidate["reservation_ids"] == ["p1"]
    assert candidate["reservation_count"] == 1
    assert candidate["status"] == "measured"
    measured = service.assembly_affordances()
    own = next(row for row in measured["routes"]
               if (row["person_id"], row["region_id"]) == ("p1", IDS[0]))
    other = next(row for row in measured["routes"]
                 if (row["person_id"], row["region_id"]) == ("p2", IDS[0]))
    assert own["status"] == "admissible"
    assert other["status"] == "blocked"
    with pytest.raises(CityGuideAdmissionError) as conflict:
        _admit(service, "p2")
    assert "target_capacity_full" in conflict.value.reason_codes
    assert conflict.value.infrastructure_failure is False

    service.guide_person("p1", target_region_id=IDS[0])
    assert service._pending_guidance_proposals == {}
    assert service._active_guidance_targets == {"p1": IDS[0]}
    service.stop_person("p1")
    assert service._pending_guidance_proposals == {}
    assert service._active_guidance_targets == {}


def test_body_height_does_not_resize_disk_and_overhead_collision_is_pair_specific(v2_service):
    service = v2_service
    service.people_roster._rows["p1"]["body_half_extents_m"] = [0.2, 0.25, 2.0]
    service.people_roster._rows["p2"]["body_half_extents_m"] = [0.2, 0.25, 0.8]
    service.region_reader.block_tall = True

    record = service.assembly_affordances()
    tall = next(row for row in record["routes"]
                if (row["person_id"], row["region_id"]) == ("p1", IDS[0]))
    short = next(row for row in record["routes"]
                 if (row["person_id"], row["region_id"]) == ("p2", IDS[0]))
    assert tall["status"] == "blocked" and tall["reason_codes"] == ["route_static_blocked"]
    assert short["status"] == "admissible"
    assert all(row["radius_m"] == 0.75 for row in record["candidates"])


def test_foreign_xy_inventory_uses_actual_3d_collision_without_a_ceiling(v2_service):
    service = v2_service
    high_drone = {"min": [9.8, -0.2, 5.0], "max": [10.2, 0.2, 6.0]}
    ground_body = {"min": [9.8, -0.2, 0.2], "max": [10.2, 0.2, 1.4]}
    service.region_reader.foreign = {"drone": high_drone}

    record = service.assembly_affordances()
    candidate = next(row for row in record["candidates"] if row["region_id"] == IDS[0])
    pair = next(row for row in record["routes"]
                if (row["person_id"], row["region_id"]) == ("p1", IDS[0]))
    assert candidate["status"] == "measured"
    assert candidate["native_readback"]["untrusted_dynamic_envelopes"] == {
        "drone": high_drone}
    assert pair["status"] == "admissible"

    service.city.elapsed = 5.1
    service.region_reader.foreign = {"robot": ground_body}
    blocked_record = service.assembly_affordances()
    blocked_pair = next(row for row in blocked_record["routes"]
                        if (row["person_id"], row["region_id"]) == ("p1", IDS[0]))
    assert blocked_pair["status"] == "blocked"
    assert blocked_pair["reason_codes"] == ["route_dynamic_blocked"]

    service.city.elapsed = 5.2
    service.region_reader.foreign = {"drone": high_drone}
    _admit(service, "p1")
    service.guide_person("p1", target_region_id=IDS[0])
    service._world_samples.append({
        "time_s": 5.2, "regions": {},
        "people": {person_id: {
            "position_xyz": list(service.city.people.position(person_id)),
        } for person_id in service.affected_person_roster()},
        "vehicles": {}})
    target = (10.0, 0.0, 0.3)
    service.city.people._actor("p1")._pending_pose = PathFollowerOutput(
        position=target, yaw=0.0, speed=0.0, locomotion="walk", finished=True)
    service.city.elapsed = 6.0
    service._world_samples.append({
        "time_s": 6.0, "regions": {},
        "people": {person_id: {
            "position_xyz": list(service.city.people.position(person_id)),
            "route_readback": {"status": "measured", "clear": True},
        } for person_id in service.affected_person_roster()},
        "vehicles": {}})
    service.region_reader.occupants = {"p1"}
    service.region_reader.contained = {"p1"}
    service.region_reader.foreign = {"drone": high_drone}
    assert service.person_readback("p1")["safe_route_and_assembly"] is True

    service.city.elapsed = 6.1
    service._world_samples.append(deepcopy(service._world_samples[-1]))
    service._world_samples[-1]["time_s"] = 6.1
    service.region_reader.foreign = {"robot": ground_body}
    blocked = service.person_readback("p1")
    assert blocked["arrived"] is True
    assert blocked["safe_route_and_assembly"] is False


def test_v2_arrival_requires_complete_xy_body_and_3d_safe_history(v2_service):
    service = v2_service
    _admit(service, "p1")
    service.guide_person("p1", target_region_id=IDS[0])
    target = (10.0, 0.0, 0.3)
    service.city.people._actor("p1")._pending_pose = PathFollowerOutput(
        position=target, yaw=0.0, speed=0.0, locomotion="walk", finished=True)
    service.city.elapsed = 6.0
    service._world_samples.append({
        "time_s": 6.0,
        "regions": {},
        "people": {person_id: {
            "position_xyz": list(service.city.people.position(person_id)),
            "route_readback": {"status": "measured", "clear": True},
        } for person_id in service.affected_person_roster()},
        "vehicles": {},
    })
    service.region_reader.occupants = {"p1"}
    service.region_reader.contained = {"p1"}
    service.city.people._actor("p1")._pending_pose = PathFollowerOutput(
        position=target, yaw=0.0, speed=0.0, locomotion="walk", finished=False)
    before_finish = service.person_readback("p1")
    assert before_finish["arrived"] is False
    assert before_finish["safe_route_and_assembly"] is False
    service.city.people._actor("p1")._pending_pose = PathFollowerOutput(
        position=target, yaw=0.0, speed=0.0, locomotion="walk", finished=True)
    result = service.person_readback("p1")
    assert result["inside_assembly"] is True
    assert result["full_body_inside_assembly"] is True
    assert result["arrived"] is True and result["safe_route_and_assembly"] is True

    service.city.elapsed = 6.1
    service._world_samples.append(deepcopy(service._world_samples[-1]))
    service._world_samples[-1]["time_s"] = 6.1
    service.region_reader.contained.clear()
    result = service.person_readback("p1")
    assert result["inside_assembly"] is True
    assert result["full_body_inside_assembly"] is False
    assert result["arrived"] is False and result["safe_route_and_assembly"] is False


def test_v2_live_witness_preserves_requested_target_controller_contract(v2_service):
    callback = lambda person_id: {"entity_id": person_id}
    adapter = CityDemoRuntimeWitnessAdapter(
        v2_service.city, episode_id="episode", source_id="native.city",
        affected_people=v2_service.affected_person_roster(),
        person_controller=callback)
    with pytest.raises(CityGuideAdmissionError) as rejected:
        adapter.guide_person("p1", target_region_id=IDS[0])
    assert rejected.value.infrastructure_failure is True
    assert rejected.value.report["profile"] == EXECUTION_PROFILE
    assert rejected.value.report["controller_target_unsupported"] is True


def test_disk_reader_filters_capacity_occupants_and_retains_foreign_collision():
    footprints = _footprint_profile()["candidates"]
    envelopes = {
        "p1": {"min": [9.8, -0.2, 0.3], "max": [10.2, 0.2, 2.0]},
        "robot-1": {"min": [9.9, -0.1, 0.0], "max": [10.1, 0.1, 1.0]},
        "p2": {"min": [20.0, 0.0, 0.3], "max": [20.2, 0.2, 2.0]},
    }
    reader = CityNativeRegionReader(
        query=object(), regions={}, envelope_reader=lambda: envelopes,
        scene_queries_enabled=True, device="cpu")
    result = reader.read_assembly_footprint(
        footprints[0], episode_id="episode", time_s=1.0,
        support={"status": "sampled_supported", "profile": SUPPORT_PROFILE,
                 "episode_id": "episode", "source_id": "native.city",
                 "captured_native_s": 1.0, "semantic_map_sha256": "a" * 64,
                 "candidate_geometry_sha256": _footprint_profile()[
                     "candidate_geometry_sha256"],
                 "geometry_sha256": footprints[0]["geometry_sha256"],
                 "evidence_ref": "native.city:support:1"},
        affected_person_ids={"p1", "p2"})

    assert result["occupant_ids"] == ["p1"]
    assert result["contained_body_ids"] == ["p1"]
    assert set(result["untrusted_dynamic_envelopes"]) == {"robot-1"}


@pytest.mark.parametrize("mutation", [
    lambda footprint, support: footprint.update(radius_m=0.8),
    lambda footprint, support: footprint.update(geometry_sha256="f" * 64),
    lambda footprint, support: support.pop("source_id"),
    lambda footprint, support: support.update(captured_native_s=0.9),
    lambda footprint, support: support.update(geometry_sha256="f" * 64),
])
def test_disk_reader_rejects_corrupt_or_stale_join(mutation):
    profile = _footprint_profile()
    footprint = deepcopy(profile["candidates"][0])
    support = {"status": "sampled_supported", "profile": SUPPORT_PROFILE,
               "episode_id": "episode", "source_id": "native.city",
               "captured_native_s": 1.0, "semantic_map_sha256": "a" * 64,
               "candidate_geometry_sha256": profile["candidate_geometry_sha256"],
               "geometry_sha256": footprint["geometry_sha256"],
               "evidence_ref": "native.city:support:1"}
    mutation(footprint, support)
    reader = CityNativeRegionReader(
        query=object(), regions={}, envelope_reader=lambda: {},
        scene_queries_enabled=True, device="cpu")
    with pytest.raises(ValueError):
        reader.read_assembly_footprint(
            footprint, episode_id="episode", time_s=1.0, support=support,
            affected_person_ids={"p1"})
