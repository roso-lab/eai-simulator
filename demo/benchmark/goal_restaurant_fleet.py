"""Full-pool Restaurant native fleet control substrate.

This module binds the maintained eight public anchors to one eight-robot
``EAI-Interactive-v0`` selection and advances that scene one control tick at a
time.  It owns the sole ``session.env.step`` call: per-agent navigation work
only computes commands, while every tick supplies a command for every robot.

The substrate reports measured route progress.  It does not implement a goal
participant, readiness transport or business evaluator, and it never promotes
handler registration or a measured arrival to benchmark validation.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
ENV_BUILDER_PATH = ROOT / "source/EAI_hmrs/EAI_hmrs/env_builder.py"
CONTROLLER_ROOT = ROOT / "source/EAI_assets/EAI_assets/controller"
TRUSTED_GOAL_SOURCE = "trusted_public_region_geometry"
ROUTE_SCHEMA = "eai.restaurant.measured_fleet_route"
RESTAURANT_TASK_ID = "RESTAURANT-GOAL-01"
RESTAURANT_PUBLIC_TASK_SHA256 = (
    "e34fe98668acece2234fda06140f867840f58c272e11216430151ce86eac9084"
)
EPISODE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_EPISODE_WARMUP_TICKS = 10_000
CLOSED_VALIDATION_FLAGS = {
    "execution_validated": False,
    "business_outcomes_validated": False,
    "feasibility_oracle_verified": False,
    "release_ready": False,
    "scoreable": False,
}

# Full-fleet identities are derived from the actual ordered selection.  Do not
# copy candidate_admission._CANDIDATES[*].runtime_agent: those are aliases for
# one-robot probes and deliberately collapse the second M20/Iris onto *_1.
FLEET_CONTROLLER_REGISTRY: dict[str, dict[str, str]] = {
    "restaurant_carter_1": {
        "native_agent_id": "carter_1", "spawn_key": "carter_1",
        "robot_type": "carter", "controller_cfg": "CARTER_DIFF_CFG",
        "command_profile": "carter_velocity",
        "controller_source": "traditional/carter_diff/carter_diff.py",
    },
    "restaurant_carter_2": {
        "native_agent_id": "carter_2", "spawn_key": "carter_2",
        "robot_type": "carter", "controller_cfg": "CARTER_DIFF_CFG",
        "command_profile": "carter_velocity",
        "controller_source": "traditional/carter_diff/carter_diff.py",
    },
    "restaurant_m20_1": {
        "native_agent_id": "m20_1", "spawn_key": "m20_1",
        "robot_type": "m20", "controller_cfg": "M20_NAV_RSL_CFG",
        "command_profile": "ground_goal_hold",
        "controller_source": "rl/m20_nav_rsl/m20_nav_rsl.py",
    },
    "restaurant_m20_2": {
        "native_agent_id": "m20_2", "spawn_key": "m20_2",
        "robot_type": "m20", "controller_cfg": "M20_NAV_RSL_CFG",
        "command_profile": "ground_goal_hold",
        "controller_source": "rl/m20_nav_rsl/m20_nav_rsl.py",
    },
    "restaurant_go2_1": {
        "native_agent_id": "go2_1", "spawn_key": "go2_1",
        "robot_type": "go2", "controller_cfg": "GO2_VELOCITY_RSL_CFG",
        "command_profile": "ground_velocity",
        "controller_source": "rl/go2_rsl_rl/go2_rsl_rl.py",
    },
    "restaurant_lite3_1": {
        "native_agent_id": "lite3_1", "spawn_key": "lite3_1",
        "robot_type": "lite3", "controller_cfg": "LITE3_VELOCITY_RSL_CFG",
        "command_profile": "ground_velocity",
        "controller_source": "rl/lite3_rsl_rl/lite3_rsl_rl.py",
    },
    "restaurant_iris_1": {
        "native_agent_id": "iris_1", "spawn_key": "iris_1",
        "robot_type": "iris", "controller_cfg": "PEGASUS_IRIS_POSITION_CFG",
        "command_profile": "aerial_position",
        "controller_source": "traditional/pegasus_multirotor/pegasus_multirotor.py",
    },
    "restaurant_iris_2": {
        "native_agent_id": "iris_2", "spawn_key": "iris_2",
        "robot_type": "iris", "controller_cfg": "PEGASUS_IRIS_POSITION_CFG",
        "command_profile": "aerial_position",
        "controller_source": "traditional/pegasus_multirotor/pegasus_multirotor.py",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _controller_declarations(path: Path) -> dict[str, tuple[str, str]]:
    """Read the literal env-builder registry without importing Isaac Lab."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name)
               and target.id == "CONTROLLER_CFG_IMPORTS" for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, dict):
                break
            return value
    raise RuntimeError("env_builder.py has no literal CONTROLLER_CFG_IMPORTS registry")


def restaurant_fleet_source_manifest(
    *, controller_root: str | Path | None = None,
    env_builder_path: str | Path = ENV_BUILDER_PATH,
) -> dict[str, Any]:
    """Validate full-fleet controller declarations and retain source hashes."""
    builder = Path(env_builder_path).expanduser().resolve()
    declarations = _controller_declarations(builder)
    root = Path(controller_root or os.environ.get("EAI_CONTROLLER_ROOT")
                or CONTROLLER_ROOT).expanduser().resolve()
    controllers: dict[str, dict[str, str]] = {}
    source_hashes = {str(builder): _sha256(builder)}
    for public_id, binding in FLEET_CONTROLLER_REGISTRY.items():
        cfg = binding["controller_cfg"]
        expected = (binding["controller_source"], cfg)
        if declarations.get(cfg) != expected:
            raise RuntimeError(
                f"Restaurant fleet controller declaration drifted for {cfg}")
        source = (root / binding["controller_source"]).resolve()
        if not source.is_file():
            raise RuntimeError(
                f"Restaurant fleet controller source is unavailable for {public_id}: {source}")
        digest = _sha256(source)
        source_hashes[str(source)] = digest
        controllers[public_id] = {
            "controller_cfg": cfg,
            "controller_source": str(source),
            "controller_source_sha256": digest,
        }
    return {
        "schema": "eai.restaurant.fleet_controller_sources",
        "version": 1,
        "env_builder_path": str(builder),
        "controllers": controllers,
        "source_files": dict(sorted(source_hashes.items())),
        "runtime_prerequisites": {
            "controller_weights": "not_captured_by_python_source_manifest",
            "provider_assets": "requires_standard_asset_preflight",
            "validated": False,
        },
        **CLOSED_VALIDATION_FLAGS,
    }


def _validate_maintained_bindings() -> None:
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.restaurant_benchmark import config
    from demo.restaurant_benchmark.candidate_admission import _CANDIDATES

    task = build_goal_task_package("restaurant")["public_task"]
    public_ids = tuple(row["id"] for row in task["candidate_robot_pool"])
    expected = tuple(FLEET_CONTROLLER_REGISTRY)
    expected_spawns = {
        public_id: list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["spawn_key"]])
        for public_id, binding in FLEET_CONTROLLER_REGISTRY.items()
    }
    if (task.get("task_id") != "RESTAURANT-GOAL-01"
            or len(task["candidate_robot_pool"]) != 8
            or public_ids != expected
            or task.get("budgets", {}).get("max_active_agents") != 8
            or task.get("public_world", {}).get("robot_spawn_positions")
            != expected_spawns):
        raise RuntimeError("Restaurant public full8 order drifted from fleet bindings")
    if tuple(config.BENCHMARK_ROBOT_SPAWN_XYZ_M) != tuple(
            binding["spawn_key"] for binding in FLEET_CONTROLLER_REGISTRY.values()):
        raise RuntimeError("Restaurant maintained anchor order drifted")
    if set(config.BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS) != set(
            config.BENCHMARK_ROBOT_SPAWN_XYZ_M):
        raise RuntimeError("Restaurant full8 anchors lost route-level admission evidence")
    for public_id, binding in FLEET_CONTROLLER_REGISTRY.items():
        if binding["robot_type"] == "carter":
            continue
        candidate = _CANDIDATES.get(public_id)
        if (candidate is None
                or candidate["spawn_key"] != binding["spawn_key"]
                or candidate["robot_type"] != binding["robot_type"]
                or candidate["controller_cfg"] != binding["controller_cfg"]):
            raise RuntimeError(f"Restaurant maintained candidate drifted for {public_id}")


def build_restaurant_full_fleet_selection() -> dict[str, Any]:
    """Build the exact eight-anchor selection without narrowing the task pool."""
    from demo.restaurant_benchmark import config

    _validate_maintained_bindings()
    # This also checks Carter, which is absent from the single-candidate table.
    restaurant_fleet_source_manifest()
    robots = []
    for binding in FLEET_CONTROLLER_REGISTRY.values():
        robots.append({
            "type": binding["robot_type"],
            "controller": {"mode": "default", "cfg": binding["controller_cfg"]},
            "attachments": [],
            "spawn_pose": {
                "position": list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[
                    binding["spawn_key"]]),
                "rotation": [1.0, 0.0, 0.0, 0.0],
            },
        })
    return {
        "version": 1,
        "task_name": "restaurant_full_fleet_native",
        "scene_key": config.BENCHMARK_SCENE_KEY,
        "robots": robots,
    }


def _finite_vector(values: Any, *, minimum: int, label: str) -> list[float]:
    raw = values.tolist() if hasattr(values, "tolist") else list(values)
    result = [float(value) for value in raw]
    if len(result) < minimum or not all(math.isfinite(value) for value in result):
        raise RuntimeError(f"{label} is missing or non-finite")
    return result


def _normalized_quaternion(root: list[float]) -> tuple[float, float, float, float]:
    w, x, y, z = root[3:7]
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if (not math.isfinite(norm)
            or not math.isclose(norm, 1.0, rel_tol=0, abs_tol=1e-3)):
        raise RuntimeError("Restaurant fleet root quaternion must be normalized")
    return w / norm, x / norm, y / norm, z / norm


def _yaw(root: list[float]) -> float:
    w, x, y, z = _normalized_quaternion(root)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _tilt(root: list[float]) -> float:
    _w, x, y, _z = _normalized_quaternion(root)
    cosine = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(cosine)


def _point_segment_distance(point, start, end) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    ratio = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(px - (sx + ratio * dx), py - (sy + ratio * dy))


def _route_deviation(point, polyline) -> float:
    return min(_point_segment_distance(point, a, b)
               for a, b in zip(polyline[:-1], polyline[1:]))


def _polyline_point(polyline, arc_s: float):
    remaining = arc_s
    for start, end in zip(polyline[:-1], polyline[1:]):
        distance = math.dist(start, end)
        if remaining <= distance:
            if distance == 0:
                return start
            ratio = remaining / distance
            return (start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio)
        remaining -= distance
    return polyline[-1]


def _profile_thresholds(profile: str) -> dict[str, float | int]:
    from demo.restaurant_benchmark import navigation_probe, route_admission

    if profile == "aerial_position":
        return {
            "final_tolerance_m": route_admission.AERIAL_FINAL_TOLERANCE_M,
            "maximum_route_deviation_m": route_admission.AERIAL_MAXIMUM_ROUTE_DEVIATION_M,
            "maximum_tilt_rad": route_admission.AERIAL_MAXIMUM_TILT_RAD,
            "minimum_altitude_m": route_admission.MINIMUM_FLIGHT_ALTITUDE_M,
            "stall_window_steps": route_admission.AERIAL_STALL_WINDOW_STEPS,
            "max_steps": route_admission.AERIAL_MAXIMUM_STEPS,
            "flight_speed_m_s": route_admission.AERIAL_FLIGHT_SPEED_M_S,
        }
    if profile == "ground_goal_hold":
        return {
            "final_tolerance_m": route_admission.GOAL_FINAL_TOLERANCE_M,
            "maximum_route_deviation_m": route_admission.MAXIMUM_ROUTE_DEVIATION_M,
            "maximum_tilt_rad": route_admission.GOAL_MAXIMUM_TILT_RAD,
            "stall_window_steps": route_admission.GOAL_STALL_WINDOW_STEPS,
            "max_steps": route_admission.GOAL_MAXIMUM_STEPS,
            "advance_radius_m": route_admission.GOAL_ADVANCE_RADIUS_M,
            "advance_step_m": route_admission.GOAL_ADVANCE_STEP_M,
        }
    if profile == "ground_velocity":
        return {
            "final_tolerance_m": route_admission.FINAL_TOLERANCE_M,
            "intermediate_tolerance_m": route_admission.INTERMEDIATE_TOLERANCE_M,
            "maximum_route_deviation_m": route_admission.MAXIMUM_ROUTE_DEVIATION_M,
            "maximum_tilt_rad": route_admission.MAXIMUM_TILT_RAD,
            "stall_window_steps": route_admission.STALL_WINDOW_STEPS,
            "max_steps": route_admission.MAX_STEPS,
            "speed_m_s": route_admission.SPEED_M_S,
            "turn_gain": route_admission.TURN_GAIN,
            "minimum_distance_scale": route_admission.MINIMUM_COMMAND_DISTANCE_SCALE,
        }
    if profile == "carter_velocity":
        return {
            "final_tolerance_m": navigation_probe.FINAL_TOLERANCE_M,
            "intermediate_tolerance_m": navigation_probe.INTERMEDIATE_TOLERANCE_M,
            "maximum_route_deviation_m": navigation_probe.MAXIMUM_ROUTE_DEVIATION_M,
            "maximum_tilt_rad": navigation_probe.MAXIMUM_TILT_RAD,
            "stall_window_steps": navigation_probe.STALL_WINDOW_STEPS,
            "max_steps": navigation_probe.MAX_STEPS,
            "speed_m_s": navigation_probe.SPEED_M_S,
            "turn_gain": navigation_probe.TURN_GAIN,
        }
    raise ValueError(f"unsupported Restaurant command profile: {profile}")


class RestaurantFleetController:
    """One-tick full-fleet command composer over a shared physical clock."""

    POSITION_GAIN = 2.0
    YAW_GAIN = 1.5
    CARTER_HOLD_MAX_LINEAR = 0.06
    CARTER_HOLD_MAX_ANGULAR = 0.30

    def __init__(self, session: Any, *, products_root: str | Path):
        import torch

        if getattr(session, "num_envs", None) != 1:
            raise ValueError("Restaurant full fleet requires one native environment")
        self.session = session
        self.products_root = Path(products_root).expanduser().resolve()
        self._torch = torch
        self._public_to_native = {
            public_id: binding["native_agent_id"]
            for public_id, binding in FLEET_CONTROLLER_REGISTRY.items()
        }
        native_ids = tuple(self._public_to_native.values())
        if tuple(session.possible_agents) != native_ids:
            raise ValueError("native Restaurant agents do not match ordered full8 bindings")
        articulations = session.base_env.scene.articulations
        if set(articulations) != set(native_ids):
            raise ValueError("native Restaurant scene does not contain exactly the full8 fleet")
        self._physics_dt = float(session.base_env.sim.get_physics_dt())
        self.control_dt_s = float(session.base_env.step_dt)
        if (not math.isfinite(self._physics_dt) or self._physics_dt <= 0
                or not math.isfinite(self.control_dt_s) or self.control_dt_s <= 0):
            raise ValueError("Restaurant fleet clock intervals must be finite and positive")
        ratio = self.control_dt_s / self._physics_dt
        self._physics_steps_per_control = round(ratio)
        if (self._physics_steps_per_control < 1
                or not math.isclose(ratio, self._physics_steps_per_control,
                                    rel_tol=0, abs_tol=1e-9)):
            raise ValueError("Restaurant control step must be an integral physics-step multiple")
        self._origin_step = self._read_step()
        self._last_step = self._origin_step
        self._operations: dict[str, dict[str, Any]] = {}
        self._active_by_agent: dict[str, str] = {}
        self._episode_begin_attempted = False
        self._episode_metadata_value: dict[str, Any] | None = None
        self._pre_episode_public_activity: set[str] = set()
        self._route_seal_key = os.urandom(32)
        self._hold_targets: dict[str, list[float]] = {}
        self._hold_yaws: dict[str, float] = {}
        initial = self._read_states()
        for public_id, sample in initial.items():
            self._hold_targets[public_id] = list(sample["root_state_w"][:3])
            self._hold_yaws[public_id] = float(sample["yaw_rad"])

    def _record_public_activity(self, name: str) -> None:
        if self._episode_metadata_value is None:
            self._pre_episode_public_activity.add(name)

    def _read_step(self) -> int:
        value = self.session.base_env.sim.current_time_step_index
        if type(value) is not int or value < 0:
            raise RuntimeError("Restaurant native physics step must be a nonnegative integer")
        return value

    def _check_clock(self) -> int:
        if not math.isclose(float(self.session.base_env.sim.get_physics_dt()),
                            self._physics_dt, rel_tol=0, abs_tol=0):
            raise RuntimeError("Restaurant native physics interval changed within the episode")
        step = self._read_step()
        if step < self._last_step:
            raise RuntimeError("Restaurant native physics clock moved backwards")
        if step > self._last_step:
            raise RuntimeError("Restaurant native physics clock advanced outside fleet control")
        self._last_step = step
        return step

    @property
    def time_s(self) -> float:
        return (self._check_clock() - self._origin_step) * self._physics_dt

    def _read_states(self) -> dict[str, dict[str, Any]]:
        step = self._check_clock() if hasattr(self, "_last_step") else self._read_step()
        time_s = (step - self._origin_step) * self._physics_dt
        samples = {}
        for public_id, native_id in self._public_to_native.items():
            tensor = self.session.base_env.scene.articulations[
                native_id].data.root_state_w[0]
            root = _finite_vector(tensor.detach().cpu(), minimum=13,
                                  label=f"{public_id} root state")
            samples[public_id] = {
                "public_agent_id": public_id,
                "native_agent_id": native_id,
                "physics_step": step,
                "time_s": time_s,
                "root_state_w": root,
                "position_m": root[:3],
                "yaw_rad": _yaw(root),
                "tilt_rad": _tilt(root),
            }
        if self._read_step() != step:
            raise RuntimeError("Restaurant simulator stepped during fleet state capture")
        return samples

    def snapshot(self) -> dict[str, Any]:
        self._record_public_activity("snapshot")
        states = self._read_states()
        result = {
            "schema": "eai.restaurant.native_fleet_snapshot",
            "version": 1,
            "physics_step": next(iter(states.values()))["physics_step"],
            "time_s": next(iter(states.values()))["time_s"],
            "agents": states,
            "operations": deepcopy(self._operations),
            **CLOSED_VALIDATION_FLAGS,
        }
        if self._episode_metadata_value is not None:
            result["episode"] = {
                key: deepcopy(value)
                for key, value in self._episode_metadata_value.items()
                if key != "post_warmup_sample"
            }
        return result

    def episode_metadata(self) -> dict[str, Any]:
        """Return the frozen post-warmup epoch or fail before it exists."""
        if self._episode_metadata_value is None:
            raise RuntimeError("Restaurant post-warmup episode has not begun")
        return deepcopy(self._episode_metadata_value)

    def begin_episode(self, episode_id: str, *, warmup_ticks: int) -> dict[str, Any]:
        """Warm all eight controllers once and freeze the participant time origin."""
        from demo.benchmark.goal_driven_tasks import build_goal_task_package
        from demo.benchmark.provenance import canonical_sha256

        if self._episode_begin_attempted:
            raise RuntimeError("Restaurant episode lifecycle may begin exactly once")
        if (not isinstance(episode_id, str)
                or EPISODE_ID_PATTERN.fullmatch(episode_id) is None):
            raise ValueError("Restaurant episode id is not a safe nonempty token")
        if (isinstance(warmup_ticks, bool) or not isinstance(warmup_ticks, int)
                or not 1 <= warmup_ticks <= MAX_EPISODE_WARMUP_TICKS):
            raise ValueError(
                f"Restaurant warmup ticks must be an integer from 1 to "
                f"{MAX_EPISODE_WARMUP_TICKS}")
        if self._pre_episode_public_activity:
            activity = ", ".join(sorted(self._pre_episode_public_activity))
            raise RuntimeError(
                f"Restaurant episode must begin before public fleet activity: {activity}")
        if self._operations or self._active_by_agent:
            raise RuntimeError("Restaurant episode cannot begin with navigation operations")

        public = build_goal_task_package("restaurant")["public_task"]
        task_sha256 = canonical_sha256(public)
        if (public.get("task_id") != RESTAURANT_TASK_ID
                or task_sha256 != RESTAURANT_PUBLIC_TASK_SHA256):
            raise RuntimeError("Restaurant public task identity drifted before episode warmup")

        archive_path = self.products_root / f"restaurant_native_episode_{episode_id}.json"
        if archive_path.exists():
            raise FileExistsError(f"Restaurant episode archive already exists: {archive_path}")
        self.products_root.mkdir(parents=True, exist_ok=True)
        self._episode_begin_attempted = True

        original_targets = deepcopy(self._hold_targets)
        original_yaws = deepcopy(self._hold_yaws)
        old_origin_step = self._origin_step
        pre_warmup = self._read_states()
        warmup: list[dict[str, Any]] = []
        for index in range(warmup_ticks):
            if (self._hold_targets != original_targets
                    or self._hold_yaws != original_yaws):
                raise RuntimeError("Restaurant hold targets changed during episode warmup")
            warmup.append(self._step_once(activity=None, warmup_index=index))
        if (self._hold_targets != original_targets
                or self._hold_yaws != original_yaws):
            raise RuntimeError("Restaurant hold targets changed during episode warmup")

        post_step = self._check_clock()
        self._origin_step = post_step
        post_warmup = self._read_states()
        if (any(sample["physics_step"] != post_step or sample["time_s"] != 0.0
                for sample in post_warmup.values())):
            raise RuntimeError("Restaurant post-warmup sample did not rebase to native t=0")
        archive = {
            "schema": "eai.restaurant.native_episode_warmup_archive",
            "version": 1,
            "episode_id": episode_id,
            "task_id": RESTAURANT_TASK_ID,
            "public_task_sha256": task_sha256,
            "old_epoch": {
                "origin_physics_step": old_origin_step,
                "pre_warmup_physics_step": next(iter(pre_warmup.values()))[
                    "physics_step"],
                "pre_warmup_time_s": next(iter(pre_warmup.values()))["time_s"],
                "pre_warmup_agents": pre_warmup,
            },
            "warmup_ticks": warmup,
            "post_warmup_sample": {
                "physics_step": post_step,
                "time_s": 0.0,
                "agents": post_warmup,
            },
            "hold_targets_before_warmup": original_targets,
            "hold_yaws_before_warmup": original_yaws,
            "controller_instances_preserved": True,
            "post_warmup_sample_measured": True,
            "warmup_stability_certified": False,
            **CLOSED_VALIDATION_FLAGS,
        }
        with archive_path.open("x", encoding="utf-8") as stream:
            json.dump(archive, stream, ensure_ascii=False, indent=2,
                      sort_keys=True, allow_nan=False)
            stream.write("\n")
        archive_sha256 = _sha256(archive_path)
        self._episode_metadata_value = {
            "schema": "eai.restaurant.native_episode_epoch",
            "version": 1,
            "lifecycle_state": "post_warmup",
            "native_initial_state_ready": True,
            "episode_id": episode_id,
            "task_id": RESTAURANT_TASK_ID,
            "public_task_sha256": task_sha256,
            "warmup_ticks": warmup_ticks,
            "origin_physics_step": post_step,
            "origin_time_s": 0.0,
            "post_warmup_sample": deepcopy(archive["post_warmup_sample"]),
            "archive": {"path": str(archive_path), "sha256": archive_sha256},
            "post_warmup_sample_measured": True,
            "warmup_stability_certified": False,
            **CLOSED_VALIDATION_FLAGS,
        }
        return self.episode_metadata()

    def _region_goal(self, public_agent_id: str, region_id: str) -> dict[str, Any]:
        """Resolve only maintained public-region geometry inside the host."""
        from demo.benchmark.goal_driven_tasks import build_goal_task_package
        from demo.restaurant_benchmark import config, route_admission

        task = build_goal_task_package("restaurant")["public_task"]
        regions = {row["id"]: row for row in task["public_world"]["regions"]}
        region = regions.get(region_id)
        if region is None:
            raise ValueError("Restaurant route names an unknown public region")
        binding = FLEET_CONTROLLER_REGISTRY[public_agent_id]
        mode = ("aerial_navigation" if binding["command_profile"] == "aerial_position"
                else "ground_navigation")
        if mode not in region["access_modes"]:
            raise ValueError("Restaurant agent mobility cannot access the public region")
        if region_id == "kitchen_service":
            xy = route_admission.KITCHEN_SERVICE_XY_M
            goal = [xy[0], xy[1], 0.0]
        elif region_id == "dining_service":
            xy = route_admission.DINING_SERVICE_XY_M
            goal = [xy[0], xy[1], 0.0]
        elif region_id == "restaurant_overview":
            goal = list(route_admission.OVERVIEW_ANCHOR_XYZ_M)
        elif region_id == "restaurant_staging":
            anchor = config.BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["spawn_key"]]
            goal = [anchor[0], anchor[1], anchor[2] if mode == "aerial_navigation" else 0.0]
        else:
            # Standby regions have public declarations but no frozen native
            # geometry.  Treat that as a platform gap rather than inventing a
            # coordinate from a participant request.
            raise ValueError("Restaurant public region has no maintained native goal binding")
        return {"source": TRUSTED_GOAL_SOURCE, "region_id": region_id,
                "goal_xyz_m": [float(value) for value in goal]}

    def _route_seal(self, route: Mapping[str, Any]) -> str:
        body = {key: value for key, value in route.items() if key != "route_seal"}
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hmac.new(self._route_seal_key, encoded, hashlib.sha256).hexdigest()

    def plan_route(self, public_agent_id: str, route_id: str,
                   region_id: str) -> dict[str, Any]:
        """Plan from the current measured root to trusted public-region geometry."""
        from EAI.scenario_spec.geometry import FactoryMap
        from EAI.scenario_spec.oracle import InflatedGrid
        from demo.benchmark.runtime import route_waypoints
        from demo.restaurant_benchmark import route_admission

        self._record_public_activity("plan_route")
        if public_agent_id not in FLEET_CONTROLLER_REGISTRY:
            raise ValueError("Restaurant route agent is outside the full8 pool")
        if not isinstance(route_id, str) or not route_id.strip():
            raise ValueError("Restaurant route id must be nonempty")
        if not isinstance(region_id, str) or not region_id.strip():
            raise ValueError("Restaurant route region id must be nonempty")
        goal_binding = self._region_goal(public_agent_id, region_id)
        goal = list(goal_binding["goal_xyz_m"])
        measured = self._read_states()[public_agent_id]
        start = measured["position_m"]
        occupancy = FactoryMap(self.products_root / "restaurant_benchmark_map.yaml")
        grid = InflatedGrid(occupancy, route_admission.PLANNING_RADIUS_M)
        cells = grid.path(start[:2], goal[:2])
        if cells is None:
            raise RuntimeError(f"Restaurant measured route is not passable: {route_id}")
        waypoints = route_waypoints(grid, cells, goal[:2], preserve_goal=True)
        if not waypoints:
            waypoints = [tuple(goal[:2])]
        profile = FLEET_CONTROLLER_REGISTRY[public_agent_id]["command_profile"]
        points = [start[:2], *waypoints]
        route = {
            "schema": ROUTE_SCHEMA,
            "version": 1,
            "route_id": route_id,
            "public_agent_id": public_agent_id,
            "native_agent_id": measured["native_agent_id"],
            "command_profile": profile,
            "goal_binding": deepcopy(dict(goal_binding)),
            "start_physics_step": measured["physics_step"],
            "start_time_s": measured["time_s"],
            "start_root_state_w": list(measured["root_state_w"]),
            "start_xy_m": list(start[:2]),
            "goal_xyz_m": goal,
            "map_sha256": occupancy.sha256,
            "route_cells": [list(cell) for cell in cells],
            "waypoints_xy_m": [list(point) for point in waypoints],
            "planned_length_m": sum(math.dist(a, b)
                                    for a, b in zip(points[:-1], points[1:])),
            "thresholds": _profile_thresholds(profile),
            **CLOSED_VALIDATION_FLAGS,
        }
        route["route_seal"] = self._route_seal(route)
        return route

    def begin_navigation(self, operation_id: str, route: Mapping[str, Any], *,
                         deadline_s: float) -> dict[str, Any]:
        self._record_public_activity("begin_navigation")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("Restaurant navigation operation id must be nonempty")
        if operation_id in self._operations:
            raise ValueError("Restaurant navigation operation id already exists")
        if (not isinstance(deadline_s, (int, float)) or isinstance(deadline_s, bool)
                or not math.isfinite(float(deadline_s)) or float(deadline_s) <= self.time_s):
            raise ValueError("Restaurant navigation deadline must advance native time")
        if (not isinstance(route, Mapping) or route.get("schema") != ROUTE_SCHEMA
                or route.get("version") != 1
                or route.get("goal_binding", {}).get("source") != TRUSTED_GOAL_SOURCE):
            raise ValueError("Restaurant navigation requires a measured trusted route")
        seal = route.get("route_seal")
        if (not isinstance(seal, str)
                or not hmac.compare_digest(seal, self._route_seal(route))):
            raise ValueError("Restaurant navigation route integrity check failed")
        public_id = route.get("public_agent_id")
        if public_id not in FLEET_CONTROLLER_REGISTRY:
            raise ValueError("Restaurant navigation route has an unknown full8 agent")
        if public_id in self._active_by_agent:
            raise ValueError("Restaurant agent already has active navigation")
        binding = FLEET_CONTROLLER_REGISTRY[public_id]
        if (route.get("native_agent_id") != binding["native_agent_id"]
                or route.get("command_profile") != binding["command_profile"]
                or route.get("thresholds") != _profile_thresholds(binding["command_profile"])):
            raise ValueError("Restaurant navigation route binding or thresholds drifted")
        states = self._read_states()
        measured = states[public_id]
        if (route.get("start_physics_step") != measured["physics_step"]
                or route.get("start_root_state_w") != measured["root_state_w"]):
            raise ValueError("Restaurant route start is not the current measured root")
        waypoints = route.get("waypoints_xy_m")
        if (not isinstance(waypoints, list) or not waypoints
                or any(len(point) != 2 or not all(math.isfinite(float(v)) for v in point)
                       for point in waypoints)):
            raise ValueError("Restaurant route has invalid waypoints")
        goal = _finite_vector(route.get("goal_xyz_m", ()), minimum=3,
                              label="Restaurant route goal")[:3]
        polyline = [tuple(float(v) for v in route["start_xy_m"]),
                    *(tuple(float(v) for v in point) for point in waypoints)]
        operation = {
            "operation_id": operation_id,
            "route_id": str(route["route_id"]),
            "public_agent_id": public_id,
            "native_agent_id": binding["native_agent_id"],
            "command_profile": binding["command_profile"],
            "status": "active",
            "started_at_s": measured["time_s"],
            "deadline_s": float(deadline_s),
            "start_physics_step": measured["physics_step"],
            "start_root_state_w": list(measured["root_state_w"]),
            "goal_binding": deepcopy(dict(route["goal_binding"])),
            "goal_xyz_m": goal,
            "polyline_xy_m": [list(point) for point in polyline],
            "waypoint_index": 0,
            "carrot_arc_s_m": 0.0,
            "total_length_m": sum(math.dist(a, b)
                                  for a, b in zip(polyline[:-1], polyline[1:])),
            "control_steps": 0,
            "last_progress_step": 0,
            "best_progress_distance_m": None,
            "best_progress_heading_rad": None,
            "progress_waypoint_index": None,
            "progress_carrot_arc_s_m": None,
            "maximum_route_deviation_m": 0.0,
            "maximum_tilt_rad": 0.0,
            "last_root_state_w": list(measured["root_state_w"]),
            "terminal_reason": None,
            **CLOSED_VALIDATION_FLAGS,
        }
        self._operations[operation_id] = operation
        self._active_by_agent[public_id] = operation_id
        return deepcopy(operation)

    def cancel_navigation(self, operation_id: str) -> dict[str, Any]:
        self._record_public_activity("cancel_navigation")
        operation = self._operations.get(operation_id)
        if operation is None:
            raise KeyError("unknown Restaurant navigation operation")
        if operation["status"] != "active":
            return deepcopy(operation)
        state = self._read_states()[operation["public_agent_id"]]
        self._finish(operation, "cancelled", "cancel_requested", state)
        return deepcopy(operation)

    def _finish(self, operation: dict[str, Any], status: str, reason: str,
                state: Mapping[str, Any]) -> None:
        operation["status"] = status
        operation["terminal_reason"] = reason
        operation["completed_at_s"] = float(state["time_s"])
        operation["completed_physics_step"] = int(state["physics_step"])
        operation["last_root_state_w"] = list(state["root_state_w"])
        public_id = operation["public_agent_id"]
        self._active_by_agent.pop(public_id, None)
        self._hold_targets[public_id] = list(state["position_m"])
        self._hold_yaws[public_id] = float(state["yaw_rad"])

    def _hold_command(self, public_id: str, state: Mapping[str, Any]):
        torch = self._torch
        binding = FLEET_CONTROLLER_REGISTRY[public_id]
        profile = binding["command_profile"]
        target = self._hold_targets[public_id]
        command = torch.zeros((self.session.num_envs, 3), device=self.session.device,
                              dtype=torch.float32)
        if profile == "aerial_position":
            command[:, :] = torch.tensor([target], device=self.session.device,
                                         dtype=torch.float32)
        elif profile == "ground_goal_hold":
            command[:, :] = torch.tensor([[target[0], target[1], 0.0]],
                                         device=self.session.device, dtype=torch.float32)
        elif profile == "carter_velocity":
            x, y = state["position_m"][:2]
            dx, dy = target[0] - x, target[1] - y
            position_heading = (math.atan2(dy, dx) - state["yaw_rad"] + math.pi) % (
                2 * math.pi) - math.pi
            linear = (self.POSITION_GAIN * math.hypot(dx, dy)
                      * math.cos(position_heading))
            linear = max(-self.CARTER_HOLD_MAX_LINEAR,
                         min(self.CARTER_HOLD_MAX_LINEAR, linear))
            yaw_error = (self._hold_yaws[public_id] - state["yaw_rad"] + math.pi) % (
                2 * math.pi) - math.pi
            angular = self.YAW_GAIN * yaw_error
            angular = max(-self.CARTER_HOLD_MAX_ANGULAR,
                          min(self.CARTER_HOLD_MAX_ANGULAR, angular))
            command[0, 0] = linear
            command[0, 2] = angular
        # Legged velocity controllers use the admitted zero-command station
        # behavior; M20 and Iris must never receive zero placeholders.
        return command

    def _active_command(self, operation: dict[str, Any], state: Mapping[str, Any]):
        from demo.benchmark.runtime import steering_command, velocity_policy_command

        torch = self._torch
        profile = operation["command_profile"]
        thresholds = _profile_thresholds(profile)
        command = torch.zeros((self.session.num_envs, 3), device=self.session.device,
                              dtype=torch.float32)
        xy = tuple(float(value) for value in state["position_m"][:2])
        polyline = [tuple(point) for point in operation["polyline_xy_m"]]
        goal = operation["goal_xyz_m"]
        if profile in {"carter_velocity", "ground_velocity"}:
            waypoints = polyline[1:]
            index = int(operation["waypoint_index"])
            while index < len(waypoints):
                tolerance = (thresholds["final_tolerance_m"]
                             if index == len(waypoints) - 1
                             else thresholds["intermediate_tolerance_m"])
                if math.dist(xy, waypoints[index]) > tolerance:
                    break
                index += 1
            operation["waypoint_index"] = index
            if index >= len(waypoints):
                target = tuple(goal[:2])
            else:
                target = waypoints[index]
            distance = math.dist(xy, target)
            desired = math.atan2(target[1] - xy[1], target[0] - xy[0])
            heading = (desired - state["yaw_rad"] + math.pi) % (2 * math.pi) - math.pi
            if profile == "carter_velocity":
                linear, angular = steering_command(
                    distance=distance, heading_error=heading,
                    speed=thresholds["speed_m_s"], k_turn=thresholds["turn_gain"])
                command[0, 0] = linear
                command[0, 2] = angular
            else:
                linear, strafe, angular = velocity_policy_command(
                    distance=distance, heading_error=heading,
                    speed=thresholds["speed_m_s"], k_turn=thresholds["turn_gain"],
                    minimum_distance_scale=thresholds["minimum_distance_scale"])
                command[0, 0] = linear
                command[0, 1] = strafe
                command[0, 2] = angular
            return command
        if profile == "ground_goal_hold":
            arc_s = float(operation["carrot_arc_s_m"])
            carrot = _polyline_point(polyline, arc_s)
            if (arc_s < operation["total_length_m"]
                    and math.dist(xy, carrot) <= thresholds["advance_radius_m"]):
                arc_s = min(operation["total_length_m"],
                            arc_s + thresholds["advance_step_m"])
                operation["carrot_arc_s_m"] = arc_s
                carrot = _polyline_point(polyline, arc_s)
            command[0, 0] = carrot[0]
            command[0, 1] = carrot[1]
            return command
        if profile == "aerial_position":
            arc_s = min(operation["total_length_m"],
                        float(operation["carrot_arc_s_m"])
                        + thresholds["flight_speed_m_s"] * self.control_dt_s)
            operation["carrot_arc_s_m"] = arc_s
            carrot = _polyline_point(polyline, arc_s)
            command[0, 0] = carrot[0]
            command[0, 1] = carrot[1]
            command[0, 2] = goal[2]
            return command
        raise RuntimeError(f"unsupported active command profile: {profile}")

    def _post_step(self, operation: dict[str, Any], state: Mapping[str, Any]) -> None:
        if operation["status"] != "active":
            return
        thresholds = _profile_thresholds(operation["command_profile"])
        operation["control_steps"] += 1
        operation["last_root_state_w"] = list(state["root_state_w"])
        xy = tuple(float(value) for value in state["position_m"][:2])
        goal = operation["goal_xyz_m"]
        distance = (math.dist(state["position_m"], goal)
                    if operation["command_profile"] == "aerial_position"
                    else math.dist(xy, goal[:2]))
        operation["distance_to_goal_m"] = distance
        deviation = _route_deviation(
            xy, [tuple(point) for point in operation["polyline_xy_m"]])
        operation["maximum_route_deviation_m"] = max(
            float(operation["maximum_route_deviation_m"]), deviation)
        operation["maximum_tilt_rad"] = max(
            float(operation["maximum_tilt_rad"]), float(state["tilt_rad"]))
        if (deviation > thresholds["maximum_route_deviation_m"]
                or state["tilt_rad"] > thresholds["maximum_tilt_rad"]):
            self._finish(operation, "failed", "route_envelope_exceeded", state)
            return
        if (operation["command_profile"] == "aerial_position"
                and state["position_m"][2] < thresholds["minimum_altitude_m"]):
            self._finish(operation, "failed", "aerial_altitude_floor_failed", state)
            return
        if distance <= thresholds["final_tolerance_m"]:
            self._finish(operation, "arrived", "measured_goal_tolerance", state)
            return
        profile = operation["command_profile"]
        polyline = [tuple(point) for point in operation["polyline_xy_m"]]
        progress_distance = distance
        progress_heading = None
        reset_progress = False
        if profile in {"carter_velocity", "ground_velocity"}:
            waypoints = polyline[1:]
            waypoint_index = min(int(operation["waypoint_index"]), len(waypoints) - 1)
            target = waypoints[waypoint_index]
            progress_distance = math.dist(xy, target)
            desired = math.atan2(target[1] - xy[1], target[0] - xy[0])
            progress_heading = abs(
                (desired - state["yaw_rad"] + math.pi) % (2 * math.pi) - math.pi)
            if operation["progress_waypoint_index"] != waypoint_index:
                operation["progress_waypoint_index"] = waypoint_index
                reset_progress = True
        elif profile == "ground_goal_hold":
            carrot_arc = float(operation["carrot_arc_s_m"])
            progress_distance = math.dist(xy, _polyline_point(polyline, carrot_arc))
            if operation["progress_carrot_arc_s_m"] != carrot_arc:
                operation["progress_carrot_arc_s_m"] = carrot_arc
                reset_progress = True
        elif profile == "aerial_position":
            carrot_arc = float(operation["carrot_arc_s_m"])
            if carrot_arc < float(operation["total_length_m"]):
                # Advancing the commanded carrot along an admitted detour is
                # progress even when distance to the final goal increases.
                if operation["progress_carrot_arc_s_m"] != carrot_arc:
                    operation["progress_carrot_arc_s_m"] = carrot_arc
                    reset_progress = True
                carrot = _polyline_point(polyline, carrot_arc)
                progress_distance = math.dist(xy, carrot)

        best = operation["best_progress_distance_m"]
        best_heading = operation["best_progress_heading_rad"]
        distance_improved = best is None or progress_distance + 0.005 < best
        heading_improved = (progress_heading is not None
                            and (best_heading is None
                                 or progress_heading + 0.01 < best_heading))
        if reset_progress or distance_improved or heading_improved:
            operation["best_progress_distance_m"] = progress_distance
            operation["best_progress_heading_rad"] = progress_heading
            operation["last_progress_step"] = operation["control_steps"]
        if operation["control_steps"] - operation["last_progress_step"] > thresholds[
                "stall_window_steps"]:
            self._finish(operation, "failed", "navigation_stalled", state)
            return
        if operation["control_steps"] >= thresholds["max_steps"]:
            self._finish(operation, "failed", "navigation_step_limit", state)
            return
        if state["time_s"] >= operation["deadline_s"] - 1e-9:
            self._finish(operation, "failed", "navigation_deadline", state)

    def _step_once(self, *, activity: str | None,
                   warmup_index: int | None = None) -> dict[str, Any]:
        if activity is not None:
            self._record_public_activity(activity)
        before = self._read_states()
        before_step = self._check_clock()
        commands = {}
        submitted = {}
        for public_id, binding in FLEET_CONTROLLER_REGISTRY.items():
            operation_id = self._active_by_agent.get(public_id)
            operation = self._operations.get(operation_id) if operation_id else None
            if (operation is not None
                    and self.time_s + self.control_dt_s > operation["deadline_s"] + 1e-9):
                self._finish(operation, "failed", "navigation_deadline", before[public_id])
                operation = None
            command = (self._active_command(operation, before[public_id])
                       if operation is not None
                       else self._hold_command(public_id, before[public_id]))
            native_id = binding["native_agent_id"]
            commands[native_id] = command
            submitted[public_id] = command[0].detach().cpu().tolist()
        if set(commands) != set(self.session.possible_agents):
            raise RuntimeError("Restaurant fleet tick omitted a native controller")
        self.session.env.step(commands)
        after_step = self._read_step()
        if after_step - before_step != self._physics_steps_per_control:
            raise RuntimeError("Restaurant native clock did not advance one control tick")
        self._last_step = after_step
        after = self._read_states()
        active_at_start = list(self._active_by_agent.values())
        for operation_id in active_at_start:
            operation = self._operations[operation_id]
            self._post_step(operation, after[operation["public_agent_id"]])
        result = {
            "schema": "eai.restaurant.native_fleet_tick",
            "version": 1,
            "start_physics_step": before_step,
            "physics_step": after_step,
            "time_s": (after_step - self._origin_step) * self._physics_dt,
            "submitted_commands": submitted,
            "agents": after,
            "operations": deepcopy(self._operations),
            **CLOSED_VALIDATION_FLAGS,
        }
        if warmup_index is not None:
            result["warmup_index"] = warmup_index
            result["post_warmup_stability_certified"] = False
        return result

    def step_once(self) -> dict[str, Any]:
        """Advance exactly one full-fleet control tick and return raw progress."""
        return self._step_once(activity="step_once")

    def advance_idle_once(self) -> dict[str, Any]:
        """Advance one tick; active operations, if any, remain incremental."""
        return self.step_once()
