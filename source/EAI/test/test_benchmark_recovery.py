"""Conditional recovery and independently attributable observation evidence."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from demo.benchmark.observations import ObservationStore
from demo.benchmark.scheduling import EpisodeScheduler
from demo.factory_benchmark.evaluation import coverage_member_exit_metrics, scheduling_metrics


def _plan():
    base = {"team": [], "resources": ["storage"], "object": None,
            "target_ref": "storage_site", "observation_ref": "storage_site", "timeout_s": 20.0}
    return [
        {**base, "node": "watch", "agent": "primary", "skill": "wait", "duration_s": 10.0,
         "prerequisites": []},
        {**base, "node": "arrive", "agent": "backup", "skill": "navigate",
         "target_ref": "backup_view", "recovery_for": "watch", "prerequisites": ["watch"]},
        {**base, "node": "resume", "agent": "backup", "skill": "wait", "duration_s": 2.0,
         "target_ref": "backup_view", "recovery_for": "watch", "prerequisites": ["watch", "arrive"]},
        {**base, "node": "consumer", "agent": "observer", "skill": "wait", "duration_s": 1.0,
         "prerequisites": ["watch"]},
    ]


def test_recovery_waits_for_all_prerequisites_and_complete_window():
    scheduler = EpisodeScheduler(_plan())
    assert scheduler.advance(0) == ["watch"]
    scheduler.fail("watch", "member_exit:primary", time_s=2)
    assert scheduler.advance(2) == ["arrive"]
    assert scheduler.states["resume"]["status"] == "pending"
    assert scheduler.states["consumer"]["status"] == "pending"
    scheduler.set_time(3)
    scheduler.complete("arrive")
    assert not scheduler.recovered_by
    assert scheduler.advance(3) == ["resume"]
    scheduler.set_time(5)
    scheduler.complete("resume")
    assert scheduler.recovered_by == {"watch": ["arrive", "resume"]}
    assert scheduler.advance(5) == ["consumer"]
    assert scheduler.states["watch"]["finished_at_s"] == 2
    assert scheduler.states["watch"]["status"] == "failed"


def test_success_skips_recovery_without_counting_it_as_throughput():
    scheduler = EpisodeScheduler(_plan())
    scheduler.advance(0)
    scheduler.complete("watch")
    assert scheduler.advance(1) == ["consumer"]
    scheduler.complete("consumer")
    assert scheduler.states["arrive"]["status"] == "skipped"
    assert scheduler.states["resume"]["status"] == "skipped"
    assert not scheduler.recovered_by
    assert scheduler.satisfied_nodes == {entry["node"] for entry in _plan()}
    metrics = scheduling_metrics(scheduler.report(), required_nodes=scheduler.entries)
    assert metrics["validated"]
    assert metrics["throughput_count"] == 2


def test_failed_replacement_cancels_consumers_and_releases_resources():
    scheduler = EpisodeScheduler(_plan())
    scheduler.advance(0)
    scheduler.fail("watch", "member_exit:primary", time_s=2)
    scheduler.advance(2)
    scheduler.fail("arrive", "navigation_failed", time_s=3)
    assert scheduler.advance(3) == []
    assert not scheduler.recovered_by
    assert scheduler.states["resume"]["status"] == "cancelled"
    assert scheduler.states["consumer"]["status"] == "cancelled"
    assert scheduler.report()["reservations"] == {}
    assert not scheduling_metrics(scheduler.report(), required_nodes=scheduler.entries)["validated"]


def test_recovery_claim_needs_complete_scheduler_evidence():
    metrics = scheduling_metrics(
        {"states": {"watch": {"status": "failed"}}, "recovered_nodes": ["watch"]},
        required_nodes=["watch"], recovered_nodes=["watch"],
    )
    assert not metrics["validated"]
    assert metrics["invalid_recovery_claims"] == ["watch"]


@pytest.fixture
def recovered_episode():
    plan = _plan()[:3]
    scheduler = EpisodeScheduler(plan)
    scheduler.advance(0)
    scheduler.fail("watch", "member_exit:primary", time_s=2)
    scheduler.advance(2)
    scheduler.set_time(3)
    scheduler.complete("arrive")
    scheduler.advance(3)
    scheduler.set_time(5)
    scheduler.complete("resume")
    events = [{"event": "member_exit", "fault_id": "exit_1", "time_s": 2,
               "agent": "primary", "replacement_agent": "backup", "affected_nodes": ["watch"],
               "affected_node_statuses": {"watch": "running"}}]
    observations = ObservationStore()
    for node, agent, timestamp in (("watch", "primary", 1), ("resume", "backup", 3),
                                    ("resume", "backup", 4), ("resume", "backup", 5)):
        identifier = f"{node}_{timestamp}"
        observations.publish_frame(
            observation_id=identifier, producer=agent, sensor="camera", sensor_config={"node": node},
            timestamp_s=timestamp, frame_id=f"{agent}/camera", target_ref="storage_site",
            position_m=[0, 0, 1], frame=np.zeros((2, 2, 3), dtype=np.uint8), valid_for_s=5,
        )
        events.append({"event": "coverage_observation", "node": node, "agent": agent,
                       "observation_id": identifier, "target_ref": "storage_site",
                       "observation_source": "live_isaac_rgb_frame"})
    return dict(scheduling=scheduler.report(), execution_events=events,
                fault_events=[{"id": "exit_1", "kind": "member_exit", "time_s": 2,
                               "agent": "primary", "replacement_agent": "backup"}],
                plan=plan, observation_report=observations.report())


def test_recovery_latency_ends_at_first_replacement_frame(recovered_episode):
    metrics = coverage_member_exit_metrics(**recovered_episode)
    assert metrics["validated"]
    recovery = metrics["recoveries"][0]
    assert recovery["dispatch_latency_s"] == 0
    assert recovery["recovery_latency_s"] == 1
    assert recovery["freshness_gap_by_site_s"] == {"storage_site": 2}
    assert recovery["first_replacement_observation_ids"] == ["resume_3"]


@pytest.mark.parametrize("mutation", [
    "no_exit", "duplicate_exit", "pending_original", "wrong_producer", "wrong_node",
    "no_capture", "future", "no_frames", "window_pending", "short_window", "forged_recovery",
])
def test_recovery_evidence_fails_closed(recovered_episode, mutation):
    evidence = deepcopy(recovered_episode)
    if mutation == "no_exit":
        evidence["execution_events"].pop(0)
    elif mutation == "duplicate_exit":
        evidence["execution_events"].append(deepcopy(evidence["execution_events"][0]))
    elif mutation == "pending_original":
        evidence["execution_events"][0]["affected_node_statuses"]["watch"] = "pending"
    elif mutation in {"wrong_producer", "wrong_node", "future", "no_capture"}:
        for observation in evidence["observation_report"]["observations"]:
            if observation["producer"] != "backup":
                continue
            if mutation == "wrong_producer":
                observation["producer"] = "primary"
            elif mutation == "wrong_node":
                observation["sensor_config"]["node"] = "arrive"
            elif mutation == "future":
                observation["timestamp_s"] = 100
            else:
                observation["quality"]["frame_acquired"] = False
    elif mutation == "no_frames":
        evidence["observation_report"]["observations"] = []
    elif mutation == "window_pending":
        evidence["scheduling"]["states"]["resume"]["status"] = "pending"
    elif mutation == "short_window":
        evidence["scheduling"]["states"]["resume"]["finished_at_s"] = 4
    else:
        evidence["scheduling"]["recovered_by"] = {"watch": ["arrive"]}
    assert not coverage_member_exit_metrics(**evidence)["validated"]


def test_recovery_gap_obeys_freshness_bound(recovered_episode):
    assert not coverage_member_exit_metrics(**recovered_episode, freshness_bound_s=1)["validated"]


def test_render_period_preserves_physics_and_control_rates():
    from types import SimpleNamespace
    from demo.benchmark.runtime import configure_render_period

    config = SimpleNamespace(sim=SimpleNamespace(dt=0.005, render_interval=4), decimation=4)
    configure_render_period(config, period_s=0.2)
    assert config.sim.render_interval == 40
    assert config.sim.dt == 0.005
    assert config.decimation == 4
    with pytest.raises(ValueError):
        configure_render_period(config, period_s=float("nan"))


def test_maintained_dropout_routes_and_preserves_branch_in_compilation():
    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.geometry import provider_map, validate_geometry, validate_connectivity
    from demo.benchmark.runtime import build_plan, planning_template
    from demo.factory_benchmark.tasks import validate_factory_scenario

    root = Path(__file__).resolve().parents[3]
    if not (root / "usd/scene/factory/factory_map.yaml").exists():
        pytest.skip("Factory map is not installed")
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/coverage_01_dropout.json")
    validate_factory_scenario(scenario, "F-COV-01")
    occupancy = provider_map()
    validate_geometry(scenario, occupancy)
    validate_connectivity(scenario, occupancy)
    plan = build_plan(scenario, occupancy, planning_template("factory", occupancy))
    recovery = [node for node in plan if node.get("recovery_for") == "storage_watch"]
    assert {node["skill"] for node in recovery} == {"navigate", "wait"}
    assert all(node["target_ref"] == "storage_backup_view" for node in recovery)
    assert all(node["observation_ref"] == "storage_site" for node in recovery)


def test_inactive_recovery_navigation_is_excluded_from_terminal_predicates():
    from EAI.scenario_spec.evaluation import derive_success_predicates, json_key
    from demo.benchmark.runtime import summarize_scheduled_trajectory

    data = {
        "composition": {"entities": [], "targets": [
            {"id": "site", "position": [0, 0, 0], "radius_m": 0.5},
            {"id": "view", "position": [2, 0, 0], "radius_m": 0.5},
        ]},
        "phase_graph": {"nodes": [
            {"id": "normal", "skill_key": "navigate", "preferred_agent": "primary",
             "target_ref": "site", "prerequisites": []},
            {"id": "recovery", "skill_key": "navigate", "preferred_agent": "backup",
             "target_ref": "view", "prerequisites": ["normal"], "recovery_for": "normal"},
        ]},
    }
    trajectory = [{"step": 1, "satisfied": {
        json_key(predicate): predicate["args"][0] == "primary"
        for predicate in derive_success_predicates(data)
    }}]
    skipped = {"states": {"normal": {"status": "succeeded"}, "recovery": {"status": "skipped"}}}
    assert summarize_scheduled_trajectory(data, trajectory, skipped)["verdict"] == "success"
    skipped["states"]["recovery"]["status"] = "failed"
    assert summarize_scheduled_trajectory(data, trajectory, skipped)["verdict"] == "failure"


@pytest.mark.parametrize("mutation", ["no_window", "same_view", "wrong_subject", "no_camera", "wrong_member"])
def test_recovery_admission_requires_physical_separation_and_full_window(mutation):
    from EAI.scenario_spec import load_scenario
    from demo.factory_benchmark.tasks import validate_factory_scenario

    root = Path(__file__).resolve().parents[3]
    data = load_scenario(root / "demo/factory_benchmark/scenarios/coverage_01_dropout.json")
    by_id = {node["id"]: node for node in data["phase_graph"]["nodes"]}
    if mutation == "no_window":
        by_id["storage_takeover_watch"].pop("recovery_for")
    elif mutation == "same_view":
        for node in (by_id["storage_takeover_arrive"], by_id["storage_takeover_watch"]):
            node["target_ref"] = "storage_site"
    elif mutation == "wrong_subject":
        by_id["storage_takeover_watch"]["observation_ref"] = "dock_site"
    elif mutation == "no_camera":
        data["robots"][-1]["attachments"] = []
    else:
        data["fault_events"][0]["replacement_agent"] = "simple_car_3"
    with pytest.raises(ValueError, match="F-COV-01"):
        validate_factory_scenario(data, "F-COV-01")
