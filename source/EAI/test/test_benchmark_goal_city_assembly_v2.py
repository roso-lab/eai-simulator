"""Versioned public disk measurements preserve choices and the evidence boundary."""
from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_city_assembly import (
    SUPPORT_FOOTPRINT_PROFILE, project_assembly_affordances,
)
from demo.benchmark.goal_city_autonomous import build_city_autonomous_contract
from demo.city_benchmark.guide_admission import canonical_state_sha256


def _fixture():
    public, _ = build_city_autonomous_contract()
    centres = [(-35., 51.25), (.5, 74.75), (60., 44.), (35., -65.25)]
    regions = sorted(row["id"] for row in public["public_world"]["regions"]
                     if row["kind"] == "assembly_candidate")
    people = {f"affected_person_{i:02d}" for i in range(4)}
    geometries = [{"region_id": identity, "footprint": "disk", "center_xy_m": list(xy),
                   "radius_m": .75, "support_plane_z_m": .3, "capacity": 1}
                  for identity, xy in zip(regions, centres)]
    candidates = [{**geometry, "geometry_sha256": canonical_state_sha256(geometry),
                   "occupancy_count": 0, "reservation_count": 0, "status": "measured",
                   "reason_codes": [], "support_status": "sampled_supported",
                   "support_evidence_ref": "support:" + geometry["region_id"],
                   "occupant_ids": [], "reservation_ids": [],
                   "native_readback": {"private_path": "DO_NOT_PUBLISH"},
                   "bound_person_ids": ["DO_NOT_PUBLISH"]} for geometry in geometries]
    raw = {"schema": "eai.city.assembly_affordance", "version": 2,
           "profile": SUPPORT_FOOTPRINT_PROFILE, "episode_id": "episode",
           "source_id": "native-source", "captured_at_s": 10., "valid_until_s": 11.,
           "semantic_map_sha256": "a" * 64,
           "candidate_geometry_sha256": canonical_state_sha256({"candidates": geometries}),
           "candidates": candidates,
           "routes": [{"person_id": person, "region_id": region, "status": "admissible",
                       "reason_codes": [], "length_m": 12., "estimated_travel_s": 4.,
                       "state_sha256": "b" * 64, "evidence_ref": f"route:{person}:{region}",
                       "proposal": {"private_waypoints": "DO_NOT_PUBLISH"}}
                      for person in sorted(people) for region in regions]}
    return public, raw, people


def _project(public, raw, people):
    return project_assembly_affordances(
        raw, public, native_episode_id="episode", native_source_id="native-source",
        clock_epoch_s=10., observed_person_ids=people, captured_at_s=10.)


def test_v2_all_choices_fixed_disks_private_fields_and_order_invariance():
    public, raw, people = _fixture()
    before = deepcopy(raw)
    result = _project(public, raw, people)
    assert raw == before
    assert result["profile"] == SUPPORT_FOOTPRINT_PROFILE and result["version"] == 2
    assert result["captured_at_s"] == 0. and result["valid_until_s"] == 1.
    assert len(result["routes"]) == 16 and len(result["candidates"]) == 4
    assert "DO_NOT_PUBLISH" not in json.dumps(result)
    for candidate in result["candidates"]:
        assert candidate["radius_m"] == .75 and candidate["capacity"] == 1
        assert candidate["support_plane_z_m"] == .3
        assert not {"min_m", "max_m", "occupant_ids", "reservation_ids", "native_readback"} & candidate.keys()
    raw["routes"].reverse()
    raw["candidates"].reverse()
    for candidate in raw["candidates"]:
        candidate["bound_person_ids"] = ["different private author assignment"]
    assert _project(public, raw, people) == result


@pytest.mark.parametrize("field,value", [
    ("version", 1), ("version", True), ("profile", "future_profile"),
    ("episode_id", "foreign"), ("source_id", "foreign"),
    ("captured_at_s", 9.99), ("valid_until_s", 10.),
    ("semantic_map_sha256", "invalid"), ("candidate_geometry_sha256", "c" * 64),
])
def test_v2_rejects_wrong_binding_profile_clock_and_hash(field, value):
    public, raw, people = _fixture()
    raw[field] = value
    with pytest.raises(ValueError):
        _project(public, raw, people)


@pytest.mark.parametrize("case", ["radius", "center", "plane", "capacity", "missing_candidate",
                                  "missing_pair", "duplicate_pair", "foreign_person",
                                  "private_reason", "negative_count", "bool_count"])
def test_v2_rejects_geometry_and_matrix_corruption(case):
    public, raw, people = _fixture()
    candidate, route = raw["candidates"][0], raw["routes"][0]
    if case in {"radius", "center", "plane", "capacity"}:
        field, value = {"radius": ("radius_m", 1.), "center": ("center_xy_m", [0., 0.]),
                        "plane": ("support_plane_z_m", .4), "capacity": ("capacity", 2)}[case]
        candidate[field] = value
    elif case == "missing_candidate":
        raw["candidates"].pop()
    elif case == "missing_pair":
        raw["routes"].pop()
    elif case == "duplicate_pair":
        raw["routes"][-1] = deepcopy(route)
    elif case == "foreign_person":
        route["person_id"] = "foreign-person"
    elif case == "private_reason":
        route["reason_codes"] = ["DO_NOT_PUBLISH"]
    elif case == "negative_count":
        candidate["occupancy_count"] = -1
    else:
        candidate["reservation_count"] = True
    with pytest.raises(ValueError):
        _project(public, raw, people)


@pytest.mark.parametrize("support,status,reason", [
    ("unknown", "unavailable", "target_support_unknown"),
    ("unsupported", "blocked", "target_support_unsupported"),
])
def test_v2_support_failures_preserve_all_pairs_and_do_not_become_admissible(support, status, reason):
    public, raw, people = _fixture()
    candidate = raw["candidates"][0]
    candidate.update(support_status=support, status=status, reason_codes=[reason])
    for route in raw["routes"]:
        if route["region_id"] == candidate["region_id"]:
            route.update(status=status, reason_codes=[reason], length_m=None, estimated_travel_s=None)
    result = _project(public, raw, people)
    assert len(result["routes"]) == 16
    assert result["candidates"][0]["support_status"] == support
    raw["routes"][0]["status"] = "admissible"
    with pytest.raises(ValueError, match="contradicts"):
        _project(public, raw, people)


def test_v2_unknown_support_dominates_known_obstruction():
    public, raw, people = _fixture()
    candidate = raw["candidates"][0]
    candidate.update(support_status="unknown", status="blocked",
                     reason_codes=["target_support_unknown", "target_capacity_full"])
    with pytest.raises(ValueError, match="unknown support"):
        _project(public, raw, people)


def test_v2_full_candidate_still_admits_same_occupant_and_reservation_identity():
    public, raw, people = _fixture()
    candidate = raw["candidates"][0]
    claimant = sorted(people)[0]
    candidate.update(status="blocked", reason_codes=["target_capacity_full"],
                     occupant_ids=[claimant], reservation_ids=[claimant],
                     occupancy_count=1, reservation_count=1)
    for route in raw["routes"]:
        if route["region_id"] == candidate["region_id"] and route["person_id"] != claimant:
            route.update(status="blocked", reason_codes=["target_capacity_full"])
    result = _project(public, raw, people)
    assert result["routes"][0]["status"] == "admissible"
    assert "occupant_ids" not in json.dumps(result)
    # A distinct reservation actually exceeds capacity; same-claimant exception
    # must not hide either that conflict or an independent physical obstruction.
    candidate["reservation_ids"] = [sorted(people)[1]]
    with pytest.raises(ValueError, match="contradicts"):
        _project(public, raw, people)
    candidate["reservation_ids"] = [claimant]
    candidate["reason_codes"].append("route_dynamic_blocked")
    with pytest.raises(ValueError, match="contradicts"):
        _project(public, raw, people)


@pytest.mark.parametrize("identities", [["foreign-person"], ["affected_person_00"] * 2, None])
def test_v2_rejects_unknown_duplicate_or_missing_capacity_identities(identities):
    public, raw, people = _fixture()
    raw["candidates"][0]["occupant_ids"] = identities
    raw["candidates"][0]["occupancy_count"] = len(identities) if identities is not None else 0
    with pytest.raises(ValueError, match="capacity identities"):
        _project(public, raw, people)
