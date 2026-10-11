"""Generic EAI routing tests; these fixtures are not native simulation evidence."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import GroundPose
from algorithm.skills.tests.test_ground_navigation import _request, _topology
from task.executors.ground_navigation import (
    GroundNavigationExecutor, read_ground_pose, read_simulation_time,
)


def _forbidden(*args, **kwargs):
    raise AssertionError("ground navigation attempted a map, OBB, DWA or collision query")


class _Env:
    num_envs = 1
    step_dt = 0.1

    def __init__(self):
        self.sim = SimpleNamespace(current_time=0.0)
        self.scene = {
            robot: SimpleNamespace(data=SimpleNamespace(
                root_pos_w=[[0.0, 0.0, 0.0]], root_quat_w=[[1.0, 0.0, 0.0, 0.0]]))
            for robot in ("carter_1", "go2_1")
        }
        self.commands = []
        self.freeze_time = False
        self.fail_all = self.fail_motion = self.stale = False
        self.validations = []
        # These entries deliberately raise immediately if a caller uses them.
        for name in (
            "occupancy_map", "geometry", "obb", "dwa", "local_costmap",
            "ground_robot_radius", "ground_motion_limits", "dynamic_obstacle_snapshot",
            "static_collision_checker", "native_collision_checker", "local_plan",
        ):
            setattr(self, name, _forbidden)

    def step(self, actions):
        robot, rows = next(iter(actions.items()))
        command = list(rows[0])
        if self.fail_all or (self.fail_motion and any(command)):
            raise RuntimeError("private controller failure")
        self.commands.append((robot, command))
        if not self.freeze_time:
            self.sim.current_time += self.step_dt

    def validate(self, sequence, motion):
        self.validations.append((sequence, motion))
        if self.stale and motion:
            raise ValueError("private stale snapshot")

    def pose(self, position, yaw=0.0, robot="carter_1"):
        self.scene[robot].data.root_pos_w = [list(position)]
        self.scene[robot].data.root_quat_w = [[math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]]


def _executor(env=None, **kwargs):
    env = _Env() if env is None else env
    return env, GroundNavigationExecutor(
        env, topology=_topology(), world_state_validator=env.validate,
        action_factory=lambda _env, _robot, velocity: [list(velocity)], **kwargs)


def test_dispatch_and_five_intents_apply_body_commands_and_reset_pid():
    env, executor = _executor()
    queued = executor.dispatch(_request())
    assert queued.state["execution_state"] == "holding"
    assert env.commands[-1] == ("carter_1", [0.0, 0.0, 0.0])
    waiting = executor.control(_request("continue"))
    assert waiting.state["reason_code"] == "awaiting_start"
    moving = executor.control(_request())
    assert moving.state["reason_code"] == "pid_follow_nodes"
    assert env.commands[-1][1] == [0.5, 0.0, 0.0]
    tracker = executor.provider._tasks["navigate"].tracker
    env.freeze_time = True
    env.sim.current_time = executor._last_times["navigate"]
    held = executor.control(_request("hold"))
    assert held.state["status"] == "pending" and tracker.node_index == 1
    assert tracker.previous_error is None and env.commands[-1][1] == [0.0, 0.0, 0.0]
    env.freeze_time = False
    env.sim.current_time += 0.1
    executor.control(_request("continue"))
    env.pose((1.8, 0.0, 0.0))
    replanned = executor.control(_request("replan"))
    assert executor.provider._tasks["navigate"].tracker is not tracker
    assert replanned.private_evidence["route_selection"]["topology_node_ids"] == ["b", "c"]
    stopped = executor.control(_request("stop"))
    assert stopped.state["status"] == "cancelled"
    assert stopped.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]
    assert env.validations[-1] == (1, False)
    with pytest.raises(SkillContractError):
        executor.control(_request("continue"))


def test_pid_uses_actual_simulation_time_between_pose_samples():
    env, executor = _executor()
    env.pose((0.0, 0.0, 0.0), yaw=0.2)
    executor.dispatch(_request())
    executor.control(_request())
    assert env.commands[-1][1][2] == pytest.approx(-0.4)
    env.sim.current_time = 0.6
    env.pose((0.0, 0.0, 0.0), yaw=0.1)
    executor.control(_request("continue"))
    assert env.commands[-1][1][2] == pytest.approx(-0.18)


def test_folded_node_route_arrival_and_terminal_zero_use_actual_root_pose():
    env, executor = _executor()
    executor.dispatch(_request(target_yaw=math.pi))
    executor.control(_request())
    env.pose((2.0, 0.0, 1.0))
    turn = executor.control(_request("continue"))
    assert turn.private_evidence["node_index"] == 2
    assert env.commands[-1][1] == [0.0, 0.0, 1.0]
    env.pose((2.0, 0.0, 1.0), yaw=math.pi / 2)
    executor.control(_request("continue"))
    assert env.commands[-1][1][0] == 0.5
    env.pose((2.0, 1.71, 8.0), yaw=-math.pi)
    arrived = executor.control(_request("continue", target_yaw=0.0))
    assert arrived.state["status"] == "succeeded"
    assert arrived.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]
    public = json.dumps(arrived.to_mapping())
    assert all(key not in public for key in (
        "route_selection", "topology_node_ids", "execution_samples", "position_m", "yaw_rad",
        "command_applied", "zero_command_applied"))
    assert arrived.plan.metadata["physical_outcome"] == "external_evaluator_required"


def test_tasks_have_independent_pid_time_and_command_evidence():
    env, executor = _executor()
    for task, robot in (("one", "carter_1"), ("two", "go2_1")):
        env.pose((0.0, 0.0, 0.0), yaw=0.2, robot=robot)
        executor.dispatch(_request(task_id=task, robot_id=robot))
        executor.control(_request(task_id=task, robot_id=robot))
    first, second = (executor.provider._tasks[key].tracker for key in ("one", "two"))
    assert first is not second
    held = executor.control(_request("hold", task_id="one"))
    assert first.previous_error is None and second.previous_error is not None
    assert len(held.private_evidence["execution_samples"]) == 1
    assert held.private_evidence["execution_sample_count"] == 3
    assert len(executor._samples["two"]) == 2


def test_unreachable_route_applies_zero_without_other_navigation_products():
    env, executor = _executor()
    executor.topology["edges"] = []
    executor.dispatch(_request())
    failed = executor.control(_request())
    assert failed.state["reason_code"] == "route_unreachable"
    assert failed.state["status"] == "failed"
    assert failed.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]


def test_timeout_is_terminal_and_zero():
    env, executor = _executor(timeout_s=0.15)
    executor.dispatch(_request())
    executor.control(_request())
    failed = executor.control(_request("continue"))
    assert failed.state["status"] == "failed" and failed.state["reason_code"] == "timeout"
    assert failed.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]


def test_movement_horizon_cannot_overrun_timeout_without_stopping():
    env, executor = _executor(timeout_s=0.25)
    executor.dispatch(_request(horizon_ticks=10))
    failed = executor.control(_request(horizon_ticks=10))
    assert failed.state["reason_code"] == "timeout"
    assert failed.private_evidence["zero_command_applied"]
    assert len([command for _, command in env.commands if any(command)]) == 2


@pytest.mark.parametrize("failure", ["stale", "pose", "time", "selector"])
def test_state_pose_time_or_selector_failure_applies_terminal_zero(failure):
    env, executor = _executor()
    executor.dispatch(_request())
    executor.control(_request())
    if failure == "stale":
        env.stale = True
    elif failure == "pose":
        env.scene["carter_1"].data.root_pos_w[0][0] = math.nan
    elif failure == "time":
        env.sim.current_time = math.nan
    else:
        executor.topology = lambda _robot: (_ for _ in ()).throw(RuntimeError("private topology"))
    failed = executor.control(_request("replan" if failure == "selector" else "continue"))
    assert failed.state["status"] == "failed"
    assert failed.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]
    assert "private topology" not in json.dumps(failed.to_mapping())


def test_failed_motion_application_falls_back_to_acknowledged_zero():
    env, executor = _executor()
    executor.dispatch(_request())
    env.fail_motion = True
    failed = executor.control(_request())
    assert failed.state["status"] == "failed"
    assert failed.private_evidence["zero_command_applied"]
    assert failed.private_evidence["execution_error_type"] == "RuntimeError"
    assert env.commands[-1][1] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("intent", ["continue", "hold", "stop"])
def test_failed_zero_application_is_private_and_cannot_certify_stopping(intent):
    env, executor = _executor()
    executor.dispatch(_request())
    executor.control(_request())
    env.fail_all = True
    failed = executor.control(_request(intent))
    assert failed.state["status"] == "failed"
    assert failed.plan.commands[0].payload["velocity"] == [0.0, 0.0, 0.0]
    assert failed.private_evidence["command_applied"] is False
    assert failed.private_evidence["zero_command_applied"] is False
    assert failed.private_evidence["zero_command_error_type"] == "RuntimeError"
    with pytest.raises(SkillContractError):
        executor.control(_request(intent))


def test_bad_world_state_at_dispatch_is_rejected_before_any_controller_action():
    env, executor = _executor()
    executor._validate = lambda *_: (_ for _ in ()).throw(ValueError("invalid snapshot"))
    with pytest.raises(ValueError):
        executor.dispatch(_request())
    assert env.commands == [] and executor.provider.active_task_ids == ()


def test_malformed_control_for_an_active_task_stops_its_own_robot():
    env, executor = _executor()
    executor.dispatch(_request())
    executor.control(_request())
    failed = executor.control(_request("continue", world_state_seq=-1))
    assert failed.state["status"] == "failed"
    assert failed.private_evidence["zero_command_applied"]
    assert env.commands[-1] == ("carter_1", [0.0, 0.0, 0.0])


def test_default_native_pose_reader_keeps_xyz_separate_and_normalizes_quaternion():
    env = _Env()
    env.pose((1.0, 2.0, 3.0), yaw=math.pi / 2)
    env.scene["carter_1"].data.root_quat_w[0] = [
        item * 3 for item in env.scene["carter_1"].data.root_quat_w[0]]
    pose = read_ground_pose(env, "carter_1")
    assert pose.position_m == (1.0, 2.0, 3.0)
    assert pose.yaw_rad == pytest.approx(math.pi / 2)
    env.scene["carter_1"].data.root_quat_w = [[0.0, 0.0, 0.0, 0.0]]
    with pytest.raises(SkillContractError):
        read_ground_pose(env, "carter_1")
    env.num_envs = 2
    with pytest.raises(SkillContractError):
        read_ground_pose(env, "carter_1")


def test_default_simulation_time_reader_uses_native_time_or_physics_steps():
    assert read_simulation_time(_Env()) == 0.0
    assert read_simulation_time(SimpleNamespace(common_step_counter=4, step_dt=0.025)) == 0.1
    with pytest.raises(SkillContractError):
        read_simulation_time(SimpleNamespace())


def test_advance_hook_receives_velocity_actions_without_launching_a_simulator():
    env = _Env()
    calls = []
    def advance(actions):
        calls.append(actions)
        env.step(actions)
    _, executor = _executor(env, advance_hook=advance)
    executor.dispatch(_request())
    executor.control(_request())
    assert calls[-1] == {"carter_1": [[0.5, 0.0, 0.0]]}


def test_default_torch_action_and_native_tensor_pose_boundary():
    torch = pytest.importorskip("torch")
    env = _Env()
    env.device = "cpu"
    data = env.scene["carter_1"].data
    data.root_pos_w = torch.tensor([[0.0, 0.0, 3.0]])
    data.root_quat_w = torch.tensor([[1.0, 0.0, 0.0, 0.0]])
    actions = []
    def advance(batch):
        command = batch["carter_1"]
        assert command.shape == (1, 3)
        assert command.dtype == torch.float32 and command.device.type == "cpu"
        actions.append(command.tolist())
        env.sim.current_time += 0.1
    executor = GroundNavigationExecutor(
        env, topology=_topology(), world_state_validator=env.validate, advance_hook=advance)
    executor.dispatch(_request())
    moving = executor.control(_request())
    assert actions == [[[0.0, 0.0, 0.0]], [[0.5, 0.0, 0.0]]]
    assert moving.private_evidence["execution_samples"][0]["position_m"] == [0.0, 0.0, 3.0]
