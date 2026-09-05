"""Factory Fire Rescue adapters for the generic physics replay protocol."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping

import numpy as np

from tools.physics_replay.components import CallbackComponent
from tools.physics_replay.coordinator import ReplayCoordinator
from tools.physics_replay.tape import NoiseTape
from tools.physics_replay.types import TargetSpec


_TASK_FIELDS = (
    "task_id",
    "task_type",
    "subtask_name",
    "subtask_colour",
    "target_xy",
    "waypoints",
    "priority",
    "arrival_target_yaw",
)
_NAV_DICT_FIELDS = (
    "robot_waypoints",
    "robot_waypoint_indices",
    "waiting_for_task",
    "is_patrolling",
    "arrived_flags",
    "arrival_target_yaw",
    "align_on_arrival_pending",
)
_NAV_SET_FIELDS = (
    "hold_position",
    "suppress_patrol_agents",
    "manual_override_agents",
    "suppress_patrol_exempt",
    "_last_plan_failed_agents",
    "events",
)
_NAV_SCALAR_FIELDS = (
    "suppress_patrol_after_emos",
    "all_stopped",
    "key5_active",
    "carter1_obstacle_pause",
)
_OBSTACLE_TIMER_FIELDS = (
    "start_time",
    "block_detected_time",
    "approval_wait_start",
    "removal_start_time",
    "drag_start_time",
)
_DYNAMIC_OBSTACLE_PHASES = {
    "RESCUE_DISPATCHED",
    "REMOVING_OBSTACLE",
    "DRAGGING",
    "CARTER_RESUMING",
    "RESCUE_DWELL",
    "RESCUE_RETURNING",
}


def _to_numpy(value: Any) -> np.ndarray:
    detached = value.detach() if hasattr(value, "detach") else value
    moved = detached.cpu() if hasattr(detached, "cpu") else detached
    return np.asarray(moved).copy()


def _restore_array(existing: Any, value: Any) -> Any:
    array = np.asarray(value)
    if isinstance(existing, np.ndarray):
        existing[...] = array
        return existing
    if hasattr(existing, "copy_"):
        try:
            import torch
        except ModuleNotFoundError:
            return array.copy()
        existing.copy_(torch.as_tensor(array, dtype=existing.dtype, device=existing.device))
        return existing
    return array.copy()


def _task_to_dict(task: Any) -> dict[str, Any]:
    payload = {field: deepcopy(getattr(task, field)) for field in _TASK_FIELDS}
    payload["target_xy"] = list(payload["target_xy"])
    payload["waypoints"] = deepcopy(list(payload["waypoints"]))
    return payload


def _active_pick(value: Any) -> bool:
    return bool(value is not None and getattr(value, "active", False))


def _ur5_is_active(ur5: Any) -> bool:
    return any(
        (
            bool(getattr(ur5, "_ext_active", False)),
            bool(getattr(ur5, "is_carrying_extinguisher", False)),
            _active_pick(getattr(ur5, "_button_press_pick", None)),
            _active_pick(getattr(ur5, "_obstacle_rescue_reach_pick", None)),
            _active_pick(getattr(ur5, "scout_1_pick", None)),
            _active_pick(getattr(ur5, "m20_1_pick", None)),
        )
    )


def validate_fire_rescue_checkpoint(nav: Any, obstacle: Any, ur5: Any) -> None:
    if getattr(nav, "_pending_plan", None) is not None:
        raise ValueError("cannot checkpoint Fire Rescue while a pending plan exists")
    phase = getattr(getattr(obstacle, "state", None), "phase", None)
    phase_name = str(getattr(phase, "name", phase or ""))
    if phase_name in _DYNAMIC_OBSTACLE_PHASES:
        raise ValueError(f"cannot checkpoint dynamic obstacle phase {phase_name}")
    if _ur5_is_active(ur5):
        raise ValueError("cannot checkpoint while a UR5 controller is active")


class NavigationReplayComponent:
    """Checkpoint stable navigation queues and control state."""

    name = "fire_rescue.navigation"
    version = 1
    priority = 40

    def __init__(self, nav: Any, *, task_factory: Callable[..., Any] | None = None):
        self.nav = nav
        self._task_factory = task_factory
        if self._task_factory is None:
            for queue in getattr(nav, "task_queues", {}).values():
                task = queue.peek()
                if task is not None:
                    self._task_factory = type(task)
                    break

    def capture_state(self) -> Mapping[str, Any]:
        if getattr(self.nav, "_pending_plan", None) is not None:
            raise ValueError("navigation component has a pending plan")
        queues = {
            str(name): [_task_to_dict(task) for task in queue.all_tasks()]
            for name, queue in getattr(self.nav, "task_queues", {}).items()
        }
        state: dict[str, Any] = {"task_queues": queues}
        for field in _NAV_DICT_FIELDS:
            if hasattr(self.nav, field):
                state[field] = deepcopy(getattr(self.nav, field))
        for field in _NAV_SET_FIELDS:
            if hasattr(self.nav, field):
                state[field] = sorted(deepcopy(set(getattr(self.nav, field))))
        for field in _NAV_SCALAR_FIELDS:
            if hasattr(self.nav, field):
                state[field] = deepcopy(getattr(self.nav, field))
        state["robot_commands"] = {
            str(name): _to_numpy(value)
            for name, value in getattr(self.nav, "robot_commands", {}).items()
        }
        return state

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping) or not isinstance(state.get("task_queues"), Mapping):
            raise ValueError("navigation snapshot must contain task_queues")
        if self._task_factory is None and any(state["task_queues"].values()):
            raise ValueError("navigation snapshot requires a RobotTask factory")
        current_queues = set(getattr(self.nav, "task_queues", {}))
        if set(state["task_queues"]) != current_queues:
            raise ValueError("navigation task queue names do not match")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        for name, tasks in state["task_queues"].items():
            queue = self.nav.task_queues[name]
            queue.clear()
            for payload in tasks:
                values = dict(payload)
                values["target_xy"] = tuple(values["target_xy"])
                values["waypoints"] = deepcopy(list(values["waypoints"]))
                queue.push_back(self._task_factory(**values))
        for field in _NAV_DICT_FIELDS:
            if field in state:
                setattr(self.nav, field, deepcopy(state[field]))
        for field in _NAV_SET_FIELDS:
            if field in state:
                setattr(self.nav, field, set(deepcopy(state[field])))
        for field in _NAV_SCALAR_FIELDS:
            if field in state:
                setattr(self.nav, field, deepcopy(state[field]))
        for name, value in state.get("robot_commands", {}).items():
            existing = getattr(self.nav, "robot_commands", {}).get(name)
            self.nav.robot_commands[name] = _restore_array(existing, value)


class ObstacleReplayComponent:
    """Checkpoint the pre-drag obstacle manager state with rebased timers."""

    name = "fire_rescue.obstacle"
    version = 1
    priority = 50

    def __init__(self, obstacle: Any):
        self.obstacle = obstacle

    def capture_state(self) -> Mapping[str, Any]:
        state_obj = self.obstacle.state
        phase = getattr(state_obj, "phase", None)
        phase_name = str(getattr(phase, "name", phase or ""))
        if phase_name in _DYNAMIC_OBSTACLE_PHASES:
            raise ValueError(f"cannot capture dynamic obstacle phase {phase_name}")
        now = time.time()
        values = {}
        elapsed = {}
        for name, value in vars(state_obj).items():
            if name == "phase":
                continue
            if name in _OBSTACLE_TIMER_FIELDS:
                elapsed[name] = None if not value else max(0.0, now - float(value))
            else:
                values[name] = deepcopy(value)
        return {
            "phase": phase_name,
            "state": values,
            "elapsed": elapsed,
            "rescue_candidates": deepcopy(getattr(self.obstacle, "rescue_candidates", [])),
            "completed_robots": sorted(
                getattr(self.obstacle, "_rescue_completed_robots", set())
            ),
            "dropped_robots": sorted(
                getattr(self.obstacle, "_extinguisher_dropped_robots", set())
            ),
        }

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping) or not isinstance(state.get("state"), Mapping):
            raise ValueError("obstacle snapshot must contain state")
        if str(state.get("phase", "")) in _DYNAMIC_OBSTACLE_PHASES:
            raise ValueError("dynamic obstacle snapshots are not supported")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        state_obj = self.obstacle.state
        phase_type = type(getattr(state_obj, "phase", None))
        phase_name = str(state["phase"])
        if hasattr(phase_type, "__members__") and phase_name in phase_type.__members__:
            state_obj.phase = phase_type[phase_name]
        for name, value in state["state"].items():
            setattr(state_obj, name, deepcopy(value))
        now = time.time()
        for name, elapsed in state.get("elapsed", {}).items():
            setattr(state_obj, name, 0.0 if elapsed is None else now - float(elapsed))
        self.obstacle.rescue_candidates = deepcopy(state.get("rescue_candidates", []))
        self.obstacle._rescue_completed_robots = set(state.get("completed_robots", []))
        self.obstacle._extinguisher_dropped_robots = set(state.get("dropped_robots", []))


class Ur5IdleReplayComponent:
    """Checkpoint the supported idle subset of the Fire Rescue UR5 manager."""

    name = "fire_rescue.ur5_idle"
    version = 1
    priority = 60

    _FIELDS = (
        "scout1_arm_stage",
        "_ext_robot_name",
        "_ext_active",
        "_ext_grabbed",
        "is_carrying_extinguisher",
        "_ext_drop_positions",
    )

    def __init__(self, ur5: Any):
        self.ur5 = ur5

    def capture_state(self) -> Mapping[str, Any]:
        if _ur5_is_active(self.ur5):
            raise ValueError("active UR5 controllers cannot be checkpointed")
        return {
            field: deepcopy(getattr(self.ur5, field))
            for field in self._FIELDS
            if hasattr(self.ur5, field)
        }

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError("UR5 idle snapshot must be a mapping")
        if bool(state.get("_ext_active", False)) or bool(
            state.get("is_carrying_extinguisher", False)
        ):
            raise ValueError("UR5 snapshot is not idle")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        reset = getattr(self.ur5, "reset", None)
        if callable(reset):
            reset()
        for field, value in state.items():
            setattr(self.ur5, field, deepcopy(value))


def build_fire_rescue_target_specs(
    base_env: Any, possible_agents: list[str] | tuple[str, ...]
) -> tuple[TargetSpec, ...]:
    """Build root targets for the four mobile robots in the Factory scene."""

    articulations = getattr(getattr(base_env, "scene", None), "articulations", {})
    specs: list[TargetSpec] = []
    selected_names = [
        name
        for name in articulations
        if name in possible_agents or str(name).endswith("_arm")
    ]
    for name in selected_names:
        robot = articulations.get(name)
        if robot is None:
            continue
        path = str(getattr(getattr(robot, "cfg", None), "prim_path", "")).strip()
        path = path.replace("/World/{ENV_REGEX_NS}", "/World/envs/env_0")
        path = path.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
        path = path.replace("env_.*", "env_0").replace("env_*", "env_0")
        path = path.rstrip("/") or f"/World/envs/env_0/{name}"
        channels = ["pose", "velocity"]
        data = getattr(robot, "data", None)
        if hasattr(data, "joint_pos") and hasattr(data, "joint_vel"):
            channels.append("joint_state")
        if hasattr(data, "joint_pos_target") and hasattr(data, "joint_vel_target"):
            channels.append("joint_target")
        specs.append(TargetSpec(path, tuple(channels)))
    if not specs:
        raise ValueError("Factory replay found no mobile robot articulation targets")
    return tuple(specs)


class FireRescueReplayVerifier:
    """Run a short deterministic fact/replay comparison for Factory Rescue."""

    def __init__(
        self,
        coordinator: ReplayCoordinator,
        *,
        output_dir: str | Path,
        verify_steps: int = 50,
        success_predicate: Callable[[], bool] | None = None,
        noise_seed: int = 0,
    ):
        if int(verify_steps) <= 0:
            raise ValueError("verify_steps must be positive")
        self.coordinator = coordinator
        self.output_dir = Path(output_dir)
        self.verify_steps = int(verify_steps)
        self.success_predicate = success_predicate or (lambda: False)
        self.noise_seed = int(noise_seed)
        self._noise: NoiseTape | None = None
        self._assignments: Mapping[str, Any] = {}
        self._recorded_steps = 0
        self._active = False
        self.complete = False
        self.result: dict[str, Any] = {}

    @property
    def active(self) -> bool:
        return self._active

    def start(self, *, assignments: Mapping[str, Any]) -> None:
        if self._active or self.complete:
            raise RuntimeError("Fire Rescue replay verification has already started")
        self.coordinator.capture_checkpoint(0)
        self._assignments = deepcopy(dict(assignments))
        self._noise = NoiseTape.recording(seed=self.noise_seed)
        self._recorded_steps = 0
        self._active = True

    def before_step(self, actions: Mapping[str, Any]) -> None:
        if not self._active:
            raise RuntimeError("Fire Rescue replay verification is not active")
        assert self._noise is not None
        self._noise.sample(
            "fire_rescue/verification",
            self._recorded_steps,
            lambda rng: rng.random(1),
        )
        self.coordinator.record_before_step(
            actions,
            noise=self._noise.values_for_step(self._recorded_steps),
        )

    def after_step(
        self,
        *,
        applied_actions: Mapping[str, Any] | None = None,
        phase: str | None = None,
    ) -> dict[str, Any] | None:
        if not self._active:
            raise RuntimeError("Fire Rescue replay verification is not active")
        events = ()
        if self._recorded_steps == 0:
            events = (
                {
                    "type": "assignments",
                    "assignments": deepcopy(dict(self._assignments)),
                },
            )
        self.coordinator.record_after_step(
            applied_actions=applied_actions,
            phase=phase,
            events=events,
        )
        self._recorded_steps += 1
        if self._recorded_steps < self.verify_steps:
            return None
        return self._finish()

    def _finish(self) -> dict[str, Any]:
        assert self._noise is not None
        self.coordinator.close_recording()
        factual_success = bool(self.success_predicate())
        restore = self.coordinator.restore(0)
        replay_success = False
        noise_ok = False
        drift: dict[str, dict[str, float]] = {}
        replayed_steps = 0
        if restore.ok:
            assert self.coordinator.tape is not None
            replay_values = {
                step: self.coordinator.tape.step(step).noise
                for step in range(self.verify_steps)
            }
            replay_noise = NoiseTape.replaying(replay_values, seed=self.noise_seed)
            self.coordinator.noise_consumer = lambda replay_step: self._consume_noise(
                replay_noise, replay_step
            )
            for step in range(self.verify_steps):
                self.coordinator.replay_step(step)
                replayed_steps += 1
            noise_ok = True
            replay_success = bool(self.success_predicate())
            drift = self.coordinator.compare_state(self.verify_steps)

        threshold = 1e-3
        drift_ok = all(
            float(values.get("max_abs", float("inf"))) <= threshold
            for values in drift.values()
        )
        recorded_steps = (
            self.coordinator.tape.require_range(0, self.verify_steps)
            if self.coordinator.tape is not None
            else ()
        )
        precomputed_required = any(
            bool(step.applied_actions_recorded) for step in recorded_steps
        )
        precomputed_steps = set(
            getattr(self.coordinator, "precomputed_action_steps", ())
        )
        precomputed_complete = (
            not precomputed_required
            or precomputed_steps == set(range(self.verify_steps))
        )
        self.result = {
            "verify_steps": self.verify_steps,
            "tape_coverage": self._recorded_steps / self.verify_steps,
            "replayed_steps": replayed_steps,
            "precomputed_action_steps": list(
                getattr(self.coordinator, "precomputed_action_steps", ())
            ),
            "precomputed_action_required": precomputed_required,
            "precomputed_action_coverage": (
                len(precomputed_steps) / self.verify_steps
                if precomputed_required
                else 1.0
            ),
            "noise_coverage": 1.0 if noise_ok else 0.0,
            "factual_success": factual_success,
            "replay_success": replay_success,
            "success_predicate_equal": factual_success == replay_success,
            "restore": restore.to_dict(),
            "drift": drift,
            "drift_threshold": threshold,
            "passed": bool(
                restore.ok
                and noise_ok
                and factual_success == replay_success
                and drift_ok
                and precomputed_complete
            ),
            "assignments": deepcopy(dict(self._assignments)),
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.output_dir / ".verification.json.tmp"
        temporary.write_text(
            json.dumps(self.result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.output_dir / "verification.json")
        self._active = False
        self.complete = True
        return deepcopy(self.result)

    @staticmethod
    def _consume_noise(noise_tape: NoiseTape, replay_step: Any) -> None:
        for key in sorted(replay_step.noise):
            if "#" not in key:
                raise ValueError(f"invalid noise key: {key}")
            channel, _ = key.rsplit("#", 1)
            noise_tape.sample(
                channel,
                replay_step.step_index,
                lambda rng: np.zeros_like(replay_step.noise[key]),
            )
        noise_tape.assert_step_consumed(replay_step.step_index)
