"""Restaurant native async backend tests over a deterministic fake fleet.

These tests are the Isaac-free tier of the Stage63 R1 native async-concurrency
validation: the real :class:`RestaurantAsyncExecutionBackend` is driven over a
deterministic fake ``RestaurantFleetController`` (same duck-typed protocol,
including the fleet's per-agent active-navigation mutex), the real derived
Restaurant deployment contract, and the real ``FormalGoalEpisodeRunner`` async
loop.  Native/Isaac evidence is produced separately on the ljy diagnostic
host; nothing here claims it.
"""

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pytest

from demo.benchmark.goal_concurrency_evidence import (
    GOAL_CONCURRENCY_EVIDENCE_SCHEMA,
    ConcurrencyEvidenceRefused,
    ConcurrencyProbeRecorder,
    PostTerminationExecutionRefused,
    ResourceConflictExecutionRefused,
    build_concurrency_evidence_report,
)
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_restaurant_async_backend import (
    BACKEND_ID,
    NativeAsyncPairAdapter,
    RestaurantAsyncExecutionBackend,
    build_parallel_patrol_plan,
)
from demo.benchmark.goal_restaurant_deployment_v2 import (
    build_restaurant_deployment_v2,
)

CONTRACT = build_restaurant_deployment_v2()
PUBLIC_TASK = CONTRACT["public_task"]
DEPLOYMENT = CONTRACT["deployment"]
ROUTES = {row["id"]: row for row in PUBLIC_TASK["public_world"]["routes"]}
BINDINGS = DEPLOYMENT["region_bindings"]
SPAWN_REGIONS = DEPLOYMENT["spawn_regions"]
SPEED_M_S = 1.0
ARRIVAL_TOLERANCE_M = 0.05


class FakeNativeFleet:
    """Deterministic stand-in for the RestaurantFleetController protocol."""

    def __init__(self, positions: Mapping[str, list[float]]):
        self.control_dt_s = 0.02
        self._agents = {
            public_id: {"position_m": list(xyz)}
            for public_id, xyz in positions.items()}
        self._operations: dict[str, dict[str, Any]] = {}
        self._active_by_agent: dict[str, str] = {}
        self._step = 10_000
        self._time_s = 0.0

    @property
    def time_s(self) -> float:
        return self._time_s

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "fake.fleet.snapshot", "version": 1,
            "physics_step": self._step,
            "time_s": self._time_s,
            "agents": {
                public_id: {
                    "public_agent_id": public_id,
                    "position_m": list(sample["position_m"]),
                    "physics_step": self._step,
                    "time_s": self._time_s,
                } for public_id, sample in self._agents.items()},
            "operations": deepcopy(self._operations),
        }

    def plan_route(self, public_agent_id: str, route_id: str,
                   region_id: str) -> dict[str, Any]:
        if public_agent_id not in self._agents:
            raise ValueError("unknown fake fleet agent")
        declared = ROUTES.get(route_id)
        if declared is None or declared["to_region_id"] != region_id:
            raise ValueError("navigation route is absent from public topology")
        binding = BINDINGS.get(declared["from_region_id"], {})
        center = binding.get("center_xyz_m", binding.get("center_xy_m"))
        if center is None or math.dist(
                self._agents[public_agent_id]["position_m"][:len(center)],
                center) > float(binding["tolerance_m"]):
            raise RuntimeError("measured root is outside the requested route origin")
        goal = BINDINGS[region_id].get(
            "center_xyz_m", [*BINDINGS[region_id]["center_xy_m"], 0.0])
        return {
            "route_id": route_id, "public_agent_id": public_agent_id,
            "goal_binding": {"source": "fake", "region_id": region_id},
            "goal_xyz_m": list(goal),
        }

    def begin_navigation(self, operation_id: str, route: Mapping[str, Any],
                         *, deadline_s: float) -> dict[str, Any]:
        if operation_id in self._operations:
            raise ValueError("operation id already exists")
        agent = route["public_agent_id"]
        if agent in self._active_by_agent:
            raise ValueError("Restaurant agent already has active navigation")
        operation = {
            "operation_id": operation_id, "route_id": route["route_id"],
            "public_agent_id": agent, "status": "active",
            "started_at_s": self._time_s, "deadline_s": float(deadline_s),
            "goal_binding": deepcopy(dict(route["goal_binding"])),
            "goal_xyz_m": list(route["goal_xyz_m"]),
            "terminal_reason": None,
        }
        self._operations[operation_id] = operation
        self._active_by_agent[agent] = operation_id
        return deepcopy(operation)

    def cancel_navigation(self, operation_id: str) -> dict[str, Any]:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise KeyError("unknown operation")
        if operation["status"] == "active":
            operation["status"] = "cancelled"
            operation["terminal_reason"] = "cancel_requested"
            operation["completed_at_s"] = self._time_s
            self._active_by_agent.pop(operation["public_agent_id"], None)
        return deepcopy(operation)

    def step_once(self) -> dict[str, Any]:
        self._step += 1
        self._time_s = round(self._time_s + self.control_dt_s, 9)
        for operation in self._operations.values():
            if operation["status"] != "active":
                continue
            agent = self._agents[operation["public_agent_id"]]
            position = agent["position_m"]
            goal = operation["goal_xyz_m"]
            delta = [goal[i] - position[i] for i in range(3)]
            distance = math.dist(position, goal)
            if distance <= ARRIVAL_TOLERANCE_M:
                operation["status"] = "arrived"
                operation["terminal_reason"] = "measured_goal_tolerance"
                operation["completed_at_s"] = self._time_s
                self._active_by_agent.pop(operation["public_agent_id"], None)
                continue
            if self._time_s + self.control_dt_s > operation["deadline_s"]:
                operation["status"] = "failed"
                operation["terminal_reason"] = "navigation_deadline"
                operation["completed_at_s"] = self._time_s
                self._active_by_agent.pop(operation["public_agent_id"], None)
                continue
            advance = min(SPEED_M_S * self.control_dt_s, distance)
            position[0] += delta[0] / distance * advance
            position[1] += delta[1] / distance * advance
            position[2] += delta[2] / distance * advance
        return self.snapshot()


def _spawn_positions() -> dict[str, list[float]]:
    positions = {}
    for public_id, region_id in SPAWN_REGIONS.items():
        binding = BINDINGS[region_id]
        center = binding.get("center_xyz_m", binding.get("center_xy_m"))
        # The real fleet reports 3-D roots; companion ground disks are 2-D.
        xyz = [float(value) for value in center]
        if len(xyz) == 2:
            xyz.append(0.0)
        positions[public_id] = xyz
    return positions


def _backend(fleet: FakeNativeFleet, *, episode_id: str = "async-backend-test"):
    recorder = ConcurrencyProbeRecorder(
        episode_id=episode_id, backend_id=BACKEND_ID)
    backend = RestaurantAsyncExecutionBackend(
        fleet=fleet, public_task=PUBLIC_TASK, deployment=DEPLOYMENT,
        episode_id=episode_id, recorder=recorder)
    return recorder, backend


def _nav_task(task_id: str, agent: str, route_id: str, region_id: str, *,
              start_s: float, duration_s: float) -> dict[str, Any]:
    return {
        "task_id": task_id, "skill": "ground_navigation",
        "arguments": {"region_id": region_id, "route_ids": [route_id]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": [agent], "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": start_s, "duration_s": duration_s},
    }


def _execute(backend, task, *, report=None):
    trusted = report if report is not None else {
        "session": {"current_time_s": backend._fleet.time_s,
                    "deployment_assessment": {"terminated": False},
                    "tasks": {task["task_id"]: {
                        "status": "pending", "dispatch_action_id": f"a-{task['task_id']}"}}}}
    return backend.execute(task, episode_id=backend._episode_id,
                           trusted_report=trusted)


def _drive(backend, *, target_s: float, max_rounds: int = 4000):
    while backend.in_flight_task_ids() and max_rounds > 0:
        result = backend.advance_idle(target_s)
        if result["idle_ticks"] == 0:
            break
        max_rounds -= result["idle_ticks"]
    return backend.completed_deliveries


# ----------------------------------------------------------------------
# Capability declaration and initial state
# ----------------------------------------------------------------------
def test_backend_declares_async_capability_and_validates_construction():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    assert backend.supports_async_execution is True
    assert isinstance(backend.completed_deliveries, list)
    assert callable(backend.advance_idle)
    mismatched = ConcurrencyProbeRecorder(episode_id="x", backend_id="other")
    with pytest.raises(ValueError, match="backend_id"):
        RestaurantAsyncExecutionBackend(
            fleet=fleet, public_task=PUBLIC_TASK, deployment=DEPLOYMENT,
            episode_id="x", recorder=mismatched)
    with pytest.raises(ValueError, match="recorder"):
        RestaurantAsyncExecutionBackend(
            fleet=fleet, public_task=PUBLIC_TASK, deployment=DEPLOYMENT,
            episode_id="x", recorder="not-a-recorder")  # type: ignore[arg-type]


def test_initial_robot_regions_use_designated_spawn_membership():
    fleet = FakeNativeFleet(_spawn_positions())
    _, backend = _backend(fleet)
    regions = backend._initial_robot_regions(fleet.snapshot())
    assert regions == SPAWN_REGIONS
    # Displacing one robot outside its designated disk removes only its entry.
    fleet._agents["restaurant_carter_2"]["position_m"] = [50.0, 50.0, 0.0]
    regions = backend._initial_robot_regions(fleet.snapshot())
    assert regions.get("restaurant_carter_1") == "restaurant_spawn_carter_1"
    assert "restaurant_carter_2" not in regions


# ----------------------------------------------------------------------
# Component level: native-shaped navigation over the fake fleet
# ----------------------------------------------------------------------
def test_parallel_navigation_in_flight_then_real_terminal_receipts():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    kitchen = _nav_task("nav-kitchen", "restaurant_carter_1",
                        "restaurant_spawn_carter_1_to_kitchen_service",
                        "kitchen_service", start_s=0.0, duration_s=45.0)
    dining = _nav_task("nav-dining", "restaurant_carter_2",
                       "restaurant_spawn_carter_2_to_dining_service",
                       "dining_service", start_s=0.0, duration_s=45.0)
    first = _execute(backend, kitchen)
    second = _execute(backend, dining)
    assert first["status"] == second["status"] == "in_flight"
    assert backend.in_flight_task_ids() == ["nav-dining", "nav-kitchen"]
    deliveries = _drive(backend, target_s=45.0)
    assert backend.in_flight_task_ids() == []
    by_task = {row["receipt"]["task_id"]: row for row in deliveries}
    assert set(by_task) == {"nav-kitchen", "nav-dining"}
    for task_id, delivery in by_task.items():
        assert delivery["status"] == "succeeded"
        receipt = delivery["receipt"]
        assert receipt["task_id"] == task_id
        assert receipt["backend_id"] == BACKEND_ID
        assert receipt["episode_id"] == backend._episode_id
        assert receipt["action_id"] == f"a-{task_id}"
        assert receipt["completed_at_s"] == delivery["physical_completed_at_s"]
        assert receipt["evidence_refs"]
        assert delivery["disposition"] == "native_navigation_arrival"
    # Distances 4.01 m / 5.02 m at 1 m/s: both complete within the window.
    times = sorted(row["physical_completed_at_s"] for row in by_task.values())
    assert 3.5 < times[0] < 4.5 and 4.5 < times[1] < 5.5

    evidence = build_concurrency_evidence_report(
        recorder, task_id=PUBLIC_TASK["task_id"],
        scene_key=PUBLIC_TASK["mission"]["scene_key"])
    assert evidence["schema"] == GOAL_CONCURRENCY_EVIDENCE_SCHEMA
    assert evidence["execution_counts"] == {
        "total": 2, "closed": 2, "in_flight_open": 0}
    (pair,) = evidence["overlap_pairs"]
    assert pair["tasks"] == ["nav-dining", "nav-kitchen"]
    assert pair["overlap_start_s"] == 0.0
    assert pair["overlap_duration_s"] == round(times[0], 6)
    assert pair["statuses"] == {"nav-kitchen": "succeeded", "nav-dining": "succeeded"}
    assert set(pair["evidence_refs"]) == {
        row["receipt"]["receipt_ref"] for row in by_task.values()}
    assert evidence["release"] is False and evidence["scoreable"] is False

    archive = backend.report()
    assert archive["supports_async_execution"] is True
    assert archive["execution_counts"]["in_flight_open"] == 0
    assert len(archive["terminal_records"]) == 2
    assert archive["platform_gaps"] == []
    journal = {row["task_id"]: row for row in archive["delivery_journal"]}
    assert all(row["physical_completed_at_s"] == row["staged_at_s"]
               for row in journal.values())


def test_native_fleet_mutex_refusal_recorded_on_shared_recorder():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    # Put carter_1 at the kitchen center so both probe routes share its origin.
    kitchen_center = BINDINGS["kitchen_service"]["center_xy_m"]
    fleet._agents["restaurant_carter_1"]["position_m"] = [
        kitchen_center[0], kitchen_center[1], 0.0]
    holder = _nav_task("probe-holder", "restaurant_carter_1",
                       "restaurant_kitchen_to_staging", "restaurant_staging",
                       start_s=0.0, duration_s=45.0)
    refused = _nav_task("probe-refused", "restaurant_carter_1",
                        "restaurant_kitchen_service_to_spawn_carter_1",
                        "restaurant_spawn_carter_1", start_s=0.0, duration_s=45.0)
    started = _execute(backend, holder)
    assert started["status"] == "in_flight"
    with pytest.raises(ResourceConflictExecutionRefused):
        _execute(backend, refused)
    assert "probe-refused" not in backend.in_flight_task_ids()

    deliveries = _drive(backend, target_s=45.0)
    (delivery,) = [row for row in deliveries
                   if row["receipt"]["task_id"] == "probe-holder"]
    assert delivery["status"] == "succeeded"

    evidence = build_concurrency_evidence_report(
        recorder, task_id=PUBLIC_TASK["task_id"],
        scene_key=PUBLIC_TASK["mission"]["scene_key"])
    conflicts = evidence["resource_conflicts"]
    assert conflicts["status"] == "triggered"
    (detail,) = conflicts["details"]
    assert detail["task_id"] == "probe-refused"
    assert detail["resource_key"] == "agent:restaurant_carter_1"
    assert detail["held_by"] == "probe-holder"
    assert detail["layer"] == "native_fleet_mutex"
    intervals = evidence["resource_intervals"]["agent:restaurant_carter_1"]
    assert [row["task_id"] for row in intervals] == ["probe-holder"]
    assert evidence["execution_counts"] == {
        "total": 1, "closed": 1, "in_flight_open": 0}


def test_post_termination_execute_refused_and_recorded():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    task = _nav_task("nav-after-stop", "restaurant_carter_1",
                     "restaurant_spawn_carter_1_to_kitchen_service",
                     "kitchen_service", start_s=0.0, duration_s=45.0)
    terminated = {"session": {
        "current_time_s": 0.0,
        "deployment_assessment": {"terminated": True}, "tasks": {}}}
    with pytest.raises(PostTerminationExecutionRefused):
        backend.execute(task, episode_id=backend._episode_id,
                        trusted_report=terminated)
    assert backend.in_flight_task_ids() == []
    evidence = build_concurrency_evidence_report(recorder)
    category = evidence["post_termination_execution_attempts"]
    assert category["status"] == "triggered"
    (detail,) = category["details"]
    assert detail["task_id"] == "nav-after-stop"
    assert detail["operation"] == "execute"


def test_observe_and_relay_are_platform_gaps_without_a_provider():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    observe = {
        "task_id": "obs-1", "skill": "observe",
        "arguments": {"region_id": "kitchen_service", "subject_ref":
                      "kitchen_service", "observation_mode":
                      "service_ground_readiness"},
        "required_capabilities": ["observe"],
        "assigned_agents": ["restaurant_carter_1"], "prerequisites": [],
        "evidence_refs": [], "execution": {"start_s": 0.0, "duration_s": 10.0},
    }
    relay = {
        "task_id": "relay-1", "skill": "relay",
        "arguments": {"message_kind": "restaurant_readiness_receipt",
                      "region_id": "kitchen_service"},
        "required_capabilities": ["relay"],
        "assigned_agents": ["restaurant_carter_1"], "prerequisites": [],
        "evidence_refs": [], "execution": {"start_s": 0.0, "duration_s": 10.0},
    }
    result = _execute(backend, observe)
    assert result["status"] == "failed"
    assert result["receipt"]["status"] == "failed"
    result = _execute(backend, relay)
    assert result["status"] == "failed"
    gaps = [row["reason"] for row in backend.report()["platform_gaps"]]
    assert "native_observation_provider_unavailable" in gaps
    assert "relay_transport_not_wired_into_async_backend" in gaps
    evidence = build_concurrency_evidence_report(recorder)
    assert evidence["execution_counts"]["closed"] == 2
    assert all(row["status"] == "failed"
               for row in evidence["executions"])


def test_cancel_in_flight_stops_navigation_and_closes_interval():
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet)
    task = _nav_task("nav-cancel", "restaurant_carter_1",
                     "restaurant_spawn_carter_1_to_kitchen_service",
                     "kitchen_service", start_s=0.0, duration_s=45.0)
    assert _execute(backend, task)["status"] == "in_flight"
    backend.advance_idle(2.0)
    result = backend.cancel(
        task, episode_id=backend._episode_id, trusted_report={
            "session": {"current_time_s": backend._fleet.time_s,
                        "deployment_assessment": {"terminated": False},
                        "tasks": {"nav-cancel": {
                            "status": "cancel_requested",
                            "dispatch_action_id": "a-nav-cancel",
                            "safety_stop": {"requested": True}}}}})
    assert result["status"] == "cancelled"
    assert result["receipt"]["action_id"] == "a-nav-cancel"
    assert backend.in_flight_task_ids() == []
    (delivery,) = backend.completed_deliveries
    assert delivery["status"] == "cancelled"
    evidence = build_concurrency_evidence_report(recorder)
    (execution,) = evidence["executions"]
    assert execution["status"] == "cancelled"
    cancels = evidence["cancellations_other"]
    assert cancels and cancels[0]["outcome"] == "safety_stop_while_in_flight"


# ----------------------------------------------------------------------
# Runner level: the real async FormalGoalEpisodeRunner loop
# ----------------------------------------------------------------------
def test_runner_async_mode_native_backend_parallel_patrol(tmp_path: Path):
    fleet = FakeNativeFleet(_spawn_positions())
    recorder, backend = _backend(fleet, episode_id="runner-native-async-1")
    plan = build_parallel_patrol_plan(window_s=45.0)
    adapter = NativeAsyncPairAdapter(plan)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        PUBLIC_TASK, CONTRACT["participant_api"], adapter, backend,
        output_root=root, episode_id="runner-native-async-1", max_turns=64,
        execution_mode="async", concurrency_recorder=recorder,
    ).run(initial_state={"robot_regions": backend._initial_robot_regions(
        fleet.snapshot()), "object_states": {}})

    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    assert result["execution_mode"] == "async"
    session = json.loads(
        (root / "goal_session_report.json").read_text(encoding="utf-8"))["report"]
    assert {tid: row["status"] for tid, row in session["tasks"].items()} == {
        "nav-kitchen": "succeeded", "nav-dining": "succeeded"}
    receipts = json.loads((root / "skill_receipts.json").read_text(encoding="utf-8"))
    backend.mark_deliveries_drained(float(session["current_time_s"]))
    journal = backend.report()["delivery_journal"]
    assert sorted(row["completed_at_s"] for row in receipts) == sorted(
        row["physical_completed_at_s"] for row in journal)
    assert all(row["drained"] for row in journal)
    assert all(row["drained_at_s"] >= row["physical_completed_at_s"]
               for row in journal)

    evidence = json.loads(
        (root / "concurrency_evidence.json").read_text(encoding="utf-8"))
    assert evidence["schema"] == GOAL_CONCURRENCY_EVIDENCE_SCHEMA
    assert evidence["runner_integration"]["integrated"] is True
    assert evidence["runner_integration"]["error"] is None
    (pair,) = evidence["overlap_pairs"]
    assert pair["tasks"] == ["nav-dining", "nav-kitchen"]
    assert pair["overlap_start_s"] == 0.0
    assert pair["overlap_duration_s"] > 0.0
    assert pair["statuses"] == {"nav-kitchen": "succeeded", "nav-dining": "succeeded"}
    assert pair["evidence_refs"] == sorted(
        row["receipt_ref"] for row in receipts)
    for name in ("resource_conflicts", "cancel_competition",
                 "midflight_member_exits",
                 "post_termination_execution_attempts"):
        assert evidence[name]["status"] == "not_triggered"
    assert evidence["release"] is False and evidence["scoreable"] is False
    assert not (root / "lifecycle_errors.json").exists()
