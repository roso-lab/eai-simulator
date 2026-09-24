"""Terminal body diagnostics preserve failed facts without changing outcomes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_episode import (
    _terminal_body_evidence, await_assembly_arrivals,
)
from demo.city_benchmark.guide_admission import REQUESTED_TARGET_V2_PROFILE


class NoHistoryCopy(list):
    def __deepcopy__(self, memo):
        raise AssertionError("terminal archive must not duplicate trajectory history")


def _fixture(profile=REQUESTED_TARGET_V2_PROFILE):
    world = {"dynamic_envelopes": {"p": {
        "prim_path": "/World/P", "min": [0, 0, 0], "max": [1, 1, 2]}},
        "contained_body_ids": [], "status": "measured"}
    readback = {
        "person_id": "p", "assembly_region_id": "region", "position_xyz": [0, 0, 0],
        "path_finished": True, "inside_assembly": True, "arrived": False,
        "full_body_inside_assembly": False, "safe_route_and_assembly": False,
        "trajectory_samples": NoHistoryCopy([{"time_s": 1}, {"time_s": 200}]),
        "world_readback": world, "trajectory_evidence_ref": "native:trajectory:p",
        "safety_evidence_ref": "native:safety:p",
    }
    footprint = {"region_id": "region", "footprint": "disk", "radius_m": .75}
    service = SimpleNamespace(
        guidance_profile=profile, _v2_footprints=lambda: {"region": footprint},
        person_readback=lambda _: readback, assembly_regions={},
        people_controller=SimpleNamespace(
            motion_readback=lambda _: {"path_finished": True},
            readback=lambda *_: readback))
    city = SimpleNamespace(
        elapsed=200., stage=object(), episode_id="native-episode",
        city_service_source_id="native-source", _episode_service=service)
    return city, service, readback, footprint


def test_terminal_capture_preserves_failure_and_same_tick_without_history_copy(monkeypatch):
    from demo.city_benchmark import body_geometry_diagnostic as module
    city, service, readback, footprint = _fixture()
    calls = []

    def capture(stage, **kwargs):
        calls.append((stage, kwargs))
        return {"status": "measured", "private_mesh": "EVALUATOR_ONLY"}

    monkeypatch.setattr(module, "capture_body_geometry", capture)
    result = _terminal_body_evidence(city, service, "p", readback)
    assert calls == [(city.stage, {
        "prim_path": "/World/P", "person_id": "p", "episode_id": "native-episode",
        "source_id": "native-source", "captured_native_s": 200., "footprint": footprint})]
    terminal = result["terminal_readback"]
    assert terminal["arrived"] is False
    assert terminal["inside_assembly"] is True
    assert terminal["full_body_inside_assembly"] is False
    assert "entered_hazard" not in terminal
    assert terminal["trajectory_sample_count"] == 2
    assert "trajectory_samples" not in terminal
    terminal["world_readback"]["contained_body_ids"].append("p")
    assert readback["world_readback"]["contained_body_ids"] == []
    assert city.elapsed == 200.


@pytest.mark.parametrize("profile", [None, "requested_target_v1", REQUESTED_TARGET_V2_PROFILE])
def test_arrival_archives_v2_once_and_never_changes_failed_recording(monkeypatch, profile):
    from demo.city_benchmark import body_geometry_diagnostic as module
    city, service, readback, _ = _fixture(profile)
    captures, recordings = [], []
    monkeypatch.setattr(module, "capture_body_geometry", lambda *a, **kw: (
        captures.append(kw) or {"status": "unknown", "reason_codes": ["missing_geometry"]}))
    service.arrival_readback = lambda _: {"arrived": False}
    runner = SimpleNamespace(
        record_person_arrival=lambda *a, **kw: (
            recordings.append(kw) or {"safe_route_and_assembly": False}),
        review=lambda _: {"evidence": {"actors": []}})
    guide = {"kind": "guide", "request": {"request_id": "request", "submitted_time_s": 0.},
             "assembly_region_id": "region", "person_ids": ["p"]}
    log = [guide, deepcopy(guide)]  # Multiple guide receipts still sample one person once.
    ctx = {"city": city, "roster_ids": ["p"], "runner": runner,
           "person_start": {"p": [10, 0, 0]}}
    await_assembly_arrivals(ctx, log)
    assert len(recordings) == 2 and all(r["arrived"] is False for r in recordings)
    assert all(r["arrived"] is False and r["arrival_time_s"] is None for r in log[2:])
    assert all("entered_hazard" not in r for r in log[2:])
    if profile == REQUESTED_TARGET_V2_PROFILE:
        assert len(captures) == 1
        assert all(r["body_geometry_diagnostic"]["status"] == "unknown" for r in log[2:])
        assert all(r["terminal_readback"]["path_finished"] is True for r in log[2:])
    else:
        assert not captures
        assert all("terminal_readback" not in r for r in log[2:])


def test_missing_fake_native_inputs_are_unknown_without_affecting_readback():
    city, service, readback, _ = _fixture()
    city.stage = None
    del city.episode_id
    result = _terminal_body_evidence(city, service, "p", readback)
    assert result["body_geometry_diagnostic"]["status"] == "unknown"
    assert result["terminal_readback"]["arrived"] is False


def test_person_completely_outside_target_keeps_native_prim(monkeypatch):
    from demo.city_benchmark import body_geometry_diagnostic as module
    city, service, readback, _ = _fixture()
    world = readback["world_readback"]
    world["all_dynamic_envelopes"] = world.pop("dynamic_envelopes")
    world["dynamic_envelopes"] = {}
    calls = []
    monkeypatch.setattr(module, "capture_body_geometry", lambda *a, **kw: (
        calls.append(kw) or {"status": "measured"}))
    result = _terminal_body_evidence(city, service, "p", readback)
    assert calls[0]["prim_path"] == "/World/P"
    assert result["terminal_readback"]["arrived"] is False
