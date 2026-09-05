from dataclasses import dataclass, field
from enum import Enum, auto
from types import SimpleNamespace

import numpy as np
import pytest

from demo.fire_rescue.runtime.replay import (
    FireRescueReplayVerifier,
    NavigationReplayComponent,
    ObstacleReplayComponent,
    Ur5IdleReplayComponent,
    validate_fire_rescue_checkpoint,
)
from tools.physics_replay.coordinator import CoordinatorReport
from tools.physics_replay.tape import ReplayStep
from tools.physics_replay.types import RestoreReport


@dataclass
class FakeTask:
    task_id: str
    task_type: str
    subtask_name: str
    subtask_colour: str
    target_xy: tuple[float, float]
    waypoints: list = field(default_factory=list)
    priority: int = 0
    arrival_target_yaw: float | None = None


class FakeQueue:
    def __init__(self):
        self.values = []

    def push_back(self, task):
        self.values.append(task)

    def clear(self):
        self.values.clear()

    def peek(self):
        return self.values[0] if self.values else None

    def all_tasks(self):
        return list(self.values)


class FakePhase(Enum):
    MONITORING = auto()
    DRAGGING = auto()


def _fake_nav():
    queue = FakeQueue()
    queue.push_back(FakeTask("emos_m20_1", "emos", "transport", "yellow", (1.0, 2.0)))
    return SimpleNamespace(
        task_queues={"m20_1": queue},
        robot_waypoints={"m20_1": [(1.0, 2.0, 0.5)]},
        robot_waypoint_indices={"m20_1": 0},
        waiting_for_task={"m20_1": False},
        is_patrolling={"m20_1": False},
        arrived_flags={"m20_1": False},
        arrival_target_yaw={"m20_1": None},
        align_on_arrival_pending={"m20_1": False},
        robot_commands={"m20_1": np.array([[0.5, 0.0, 0.1]], dtype=np.float32)},
        hold_position=set(),
        suppress_patrol_agents=set(),
        manual_override_agents=set(),
        suppress_patrol_exempt=set(),
        _last_plan_failed_agents=set(),
        events=set(),
        _pending_plan=None,
        suppress_patrol_after_emos=True,
    )


def _fake_ur5():
    return SimpleNamespace(
        _ext_active=False,
        _ext_grabbed=False,
        _button_press_pick=None,
        _obstacle_rescue_reach_pick=None,
        scout_1_pick=SimpleNamespace(active=False),
        m20_1_pick=SimpleNamespace(active=False),
        reset=lambda: None,
    )


def test_navigation_component_round_trips_task_queues_and_waypoints():
    nav = _fake_nav()
    component = NavigationReplayComponent(nav, task_factory=FakeTask)
    snapshot = component.capture_state()
    nav.task_queues["m20_1"].clear()
    nav.robot_waypoints["m20_1"] = []
    nav.robot_commands["m20_1"][:] = 0.0

    component.restore_state(snapshot)

    assert nav.task_queues["m20_1"].peek().task_id == "emos_m20_1"
    assert nav.robot_waypoints["m20_1"] == [(1.0, 2.0, 0.5)]
    np.testing.assert_array_equal(
        nav.robot_commands["m20_1"], np.array([[0.5, 0.0, 0.1]], dtype=np.float32)
    )


def test_safe_checkpoint_rejects_pending_plan_and_active_ur5():
    nav = _fake_nav()
    obstacle = SimpleNamespace(state=SimpleNamespace(phase=FakePhase.MONITORING))
    ur5 = _fake_ur5()
    nav._pending_plan = object()
    with pytest.raises(ValueError, match="pending plan"):
        validate_fire_rescue_checkpoint(nav, obstacle, ur5)
    nav._pending_plan = None
    ur5._ext_active = True
    with pytest.raises(ValueError, match="UR5"):
        validate_fire_rescue_checkpoint(nav, obstacle, ur5)


def test_safe_checkpoint_rejects_dynamic_obstacle_phase():
    obstacle = SimpleNamespace(state=SimpleNamespace(phase=FakePhase.DRAGGING))
    with pytest.raises(ValueError, match="obstacle"):
        validate_fire_rescue_checkpoint(_fake_nav(), obstacle, _fake_ur5())


def test_obstacle_component_rebases_wall_clock_state(monkeypatch):
    monkeypatch.setattr("demo.fire_rescue.runtime.replay.time.time", lambda: 100.0)
    obstacle = SimpleNamespace(
        state=SimpleNamespace(
            phase=FakePhase.MONITORING,
            start_time=90.0,
            block_detected_time=0.0,
            approval_wait_start=0.0,
            removal_start_time=0.0,
            drag_start_time=0.0,
            obstacle_spawned=False,
        ),
        rescue_candidates=[],
        _rescue_completed_robots=set(),
        _extinguisher_dropped_robots=set(),
    )
    component = ObstacleReplayComponent(obstacle)
    state = component.capture_state()
    obstacle.state.start_time = 0.0
    monkeypatch.setattr("demo.fire_rescue.runtime.replay.time.time", lambda: 200.0)

    component.restore_state(state)

    assert obstacle.state.start_time == 190.0
    assert obstacle.state.phase is FakePhase.MONITORING


def test_ur5_idle_component_rejects_active_controller():
    ur5 = _fake_ur5()
    component = Ur5IdleReplayComponent(ur5)
    component.validate_state(component.capture_state())
    ur5.scout_1_pick.active = True
    with pytest.raises(ValueError, match="UR5"):
        component.capture_state()


def test_fire_rescue_verifier_records_restores_and_replays(tmp_path):
    events = []

    class FakeTape:
        def step(self, step):
            return ReplayStep(
                step,
                {"robot": np.array([step], dtype=np.float32)},
                {},
                {"fire_rescue/verification#0": np.array([0.5])},
                (),
            )

        def require_range(self, start, count):
            return tuple(self.step(step) for step in range(start, start + count))

    class FakeCoordinator:
        tape = FakeTape()
        registry = SimpleNamespace(components=())
        recorder = SimpleNamespace(targets=())

        def capture_checkpoint(self, step):
            events.append(("checkpoint", step))

        def record_before_step(self, actions, commands=None, noise=None):
            events.append(("before", actions, noise))

        def record_after_step(self, applied_actions=None, phase=None, events=()):
            pass

        def close_recording(self):
            return SimpleNamespace(manifest=SimpleNamespace(environment={}))

        def restore(self, step):
            events.append(("restore", step))
            return CoordinatorReport(
                step, RestoreReport("physical_state_only", step)
            )

        def replay_step(self, step):
            events.append(("replay", step))

        def compare_state(self, step):
            return {"target_0/pose": {"max_abs": 0.0, "rmse": 0.0}}

    verifier = FireRescueReplayVerifier(
        FakeCoordinator(),
        output_dir=tmp_path,
        verify_steps=2,
        success_predicate=lambda: False,
        noise_seed=3,
    )
    verifier.start(assignments={"m20_1": {"colour": "yellow"}})
    for step in range(2):
        verifier.before_step({"robot": np.array([step], dtype=np.float32)})
        verifier.after_step(phase="assigned")

    assert verifier.complete
    assert verifier.result["passed"] is True
    assert [event[0] for event in events] == [
        "checkpoint",
        "before",
        "before",
        "restore",
        "replay",
        "replay",
    ]
    assert (tmp_path / "verification.json").is_file()
