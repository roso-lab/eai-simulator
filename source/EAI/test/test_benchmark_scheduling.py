"""Pure tests for atomic resource reservations and simulation-time deadlines."""

import pytest

from demo.benchmark.scheduling import EpisodeScheduler, node_resources


def _entry(identifier, agent, prerequisites=(), timeout_s=5.0, resources=()):
    return {"node": identifier, "agent": agent, "team": [], "object": None,
            "prerequisites": list(prerequisites), "timeout_s": timeout_s,
            "resources": list(resources)}


def test_same_robot_nodes_are_serialized():
    first = _entry("first", "carter_1")
    second = _entry("second", "carter_1")
    scheduler = EpisodeScheduler([first, second])
    assert scheduler.advance(0.0) == ["first"]
    scheduler.complete("first")
    assert scheduler.advance(0.1) == ["second"]


def test_team_and_object_resources_are_atomic():
    entry = _entry("joint", "m20_1", resources=("dock_north",))
    entry["team"] = ["m20_1", "carter_1"]
    entry["object"] = "crate_1"
    assert node_resources(entry) == frozenset({
        "robot:m20_1", "robot:carter_1", "object:crate_1", "shared:dock_north"})


def test_waiting_resource_can_time_out_and_release_is_reported():
    first = _entry("first", "carter_1")
    second = _entry("second", "carter_1", timeout_s=0.5)
    scheduler = EpisodeScheduler([first, second])
    scheduler.advance(0.0)
    assert scheduler.advance(0.6) == ["first"]
    assert scheduler.states["second"]["status"] == "timed_out"
    assert scheduler.report()["reservations"] == {"robot:carter_1": "first"}
    scheduler.cancel_remaining("episode_timeout")
    report = scheduler.report()
    assert report["reservations"] == {}
    assert any(event["status"] == "timed_out" for event in report["events"])


def test_invalid_deadline_is_rejected():
    with pytest.raises(ValueError, match="timeout_s"):
        EpisodeScheduler([_entry("bad", "carter_1", timeout_s=0.0)])
