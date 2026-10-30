"""Run the db-CBS native core vendored and built inside EAI Simulator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Iterable

import yaml


DEFAULT_PLAN_DT = 0.1
DEFAULT_DBCBS_CONFIG: dict[str, Any] = {
    "delta_0": 0.5,
    "delta_rate": 0.9,
    "num_primitives_0": 1000,
    "num_primitives_rate": 1.5,
    "alpha": 0.5,
    "filter_duplicates": True,
    "heuristic1": "reverse-search",
    "heuristic1_delta": 1.0,
}


@dataclass(frozen=True)
class PlanarAgent:
    """One named double-integrator planning agent."""

    name: str
    start: tuple[float, float]
    goal: tuple[float, float]
    radius: float = 0.15

    def __post_init__(self) -> None:
        if not math.isfinite(self.radius) or self.radius <= 0.0:
            raise ValueError(f"db-CBS agent {self.name!r} must have a positive radius")


@dataclass(frozen=True)
class PlanSample:
    """One double-integrator state in planner coordinates."""

    t: float
    x: float
    y: float
    vx: float
    vy: float


@dataclass(frozen=True)
class DbcbsPlan:
    """Named synchronized trajectories returned by db-CBS."""

    trajectories: dict[str, tuple[PlanSample, ...]]
    cost: float | None = None
    dt: float = DEFAULT_PLAN_DT

    def __post_init__(self) -> None:
        if self.dt <= 0.0:
            raise ValueError("dt must be positive.")
        if any(not samples for samples in self.trajectories.values()):
            raise ValueError("Every db-CBS trajectory must contain at least one sample.")

    @property
    def duration(self) -> float:
        return max(
            (samples[-1].t for samples in self.trajectories.values()), default=0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost": self.cost,
            "duration": self.duration,
            "dt": self.dt,
            "trajectories": {
                name: [asdict(sample) for sample in samples]
                for name, samples in self.trajectories.items()
            },
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def build_problem(
    agents: Iterable[PlanarAgent],
    *,
    workspace_min: tuple[float, float],
    workspace_max: tuple[float, float],
    obstacles: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build a db-CBS YAML problem for planar double-integrator motion."""

    agent_list = tuple(agents)
    if not agent_list:
        raise ValueError("At least one db-CBS agent is required.")
    if len({agent.name for agent in agent_list}) != len(agent_list):
        raise ValueError("db-CBS agent names must be unique.")
    if any(workspace_min[index] >= workspace_max[index] for index in range(2)):
        raise ValueError("workspace_min must be below workspace_max on both axes.")

    return {
        "environment": {
            "min": [float(workspace_min[0]), float(workspace_min[1])],
            "max": [float(workspace_max[0]), float(workspace_max[1])],
            "obstacles": list(obstacles),
        },
        "robots": [
            {
                "type": "double_integrator_0",
                "start": [float(agent.start[0]), float(agent.start[1]), 0.0, 0.0],
                "goal": [float(agent.goal[0]), float(agent.goal[1]), 0.0, 0.0],
                "radius": float(agent.radius),
            }
            for agent in agent_list
        ],
    }


def discover_dbcbs_root() -> Path:
    """Resolve the native db-CBS source and build bundled with this package."""

    root = Path(__file__).resolve().parent / "native"
    required = (
        root / "build" / "db_cbs",
        root / "motions" / "double_integrator_0_sorted.msgpack",
        root / "dynoplan" / "dynobench",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "EAI db-CBS is not built; missing: "
            + ", ".join(missing)
            + ". Run algorithm/multi_robot_navigation/build_native.sh "
            "in env_isaaclab."
        )
    return root


def _parse_result(
    path: Path,
    agents: tuple[PlanarAgent, ...],
    *,
    dt: float = DEFAULT_PLAN_DT,
) -> DbcbsPlan:
    _validate_dbcbs_dt(dt)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    results = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(agents):
        raise ValueError(
            f"Expected {len(agents)} result trajectories in {path}, got "
            f"{0 if not isinstance(results, list) else len(results)}."
        )

    trajectories: dict[str, tuple[PlanSample, ...]] = {}
    for agent, result in zip(agents, results, strict=True):
        raw_states = result.get("states") if isinstance(result, dict) else None
        if not isinstance(raw_states, list) or not raw_states:
            raise ValueError(f"Trajectory for {agent.name} has no states.")
        samples = []
        for index, state in enumerate(raw_states):
            if not isinstance(state, list) or len(state) < 4:
                raise ValueError(f"Invalid state {index} for {agent.name}: {state!r}")
            values = tuple(float(value) for value in state[:4])
            if not all(math.isfinite(value) for value in values):
                raise ValueError(
                    f"Non-finite state {index} for {agent.name}: {state!r}"
                )
            samples.append(PlanSample(index * dt, *values))
        trajectories[agent.name] = tuple(samples)

    raw_cost = payload.get("cost") if isinstance(payload, dict) else None
    return DbcbsPlan(
        trajectories=trajectories,
        dt=float(dt),
        cost=float(raw_cost) if raw_cost is not None else None,
    )


def run_dbcbs(
    *,
    agents: Iterable[PlanarAgent],
    workspace_min: tuple[float, float],
    workspace_max: tuple[float, float],
    obstacles: Iterable[dict[str, Any]] = (),
    dt: float = DEFAULT_PLAN_DT,
    timeout: float = 180.0,
    config: dict[str, Any] | None = None,
) -> DbcbsPlan:
    """Run db-CBS and return its optimized multi-agent trajectory."""

    _validate_dbcbs_dt(dt)
    root = discover_dbcbs_root()
    agent_tuple = tuple(agents)
    problem = build_problem(
        agent_tuple,
        workspace_min=workspace_min,
        workspace_max=workspace_max,
        obstacles=obstacles,
    )
    planner_config = dict(DEFAULT_DBCBS_CONFIG)
    if config:
        planner_config.update(config)

    with tempfile.TemporaryDirectory(prefix="eai-dbcbs-") as temp_dir:
        temp = Path(temp_dir)
        problem_path = temp / "problem.yaml"
        config_path = temp / "config.yaml"
        discrete_path = temp / "discrete.yaml"
        joint_path = temp / "joint.yaml"
        optimized_path = temp / "optimized.yaml"
        log_path = temp / "planner.log"
        problem_path.write_text(
            yaml.safe_dump(problem, sort_keys=False), encoding="utf-8"
        )
        config_path.write_text(
            yaml.safe_dump(planner_config, sort_keys=False), encoding="utf-8"
        )
        command = [
            str(root / "build" / "db_cbs"),
            "--input",
            str(problem_path),
            "--output",
            str(discrete_path),
            "--joint",
            str(joint_path),
            "--optimization",
            str(optimized_path),
            "--cfg",
            str(config_path),
        ]
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                result = subprocess.run(
                    command,
                    cwd=root / "build",
                    env=_dbcbs_subprocess_environment(root),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=max(1.0, float(timeout)),
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            tail = _log_tail(log_path)
            raise TimeoutError(
                f"db-CBS exceeded the {timeout:.1f}s planning timeout.\n{tail}"
            ) from exc

        if result.returncode != 0 or not optimized_path.is_file():
            tail = _log_tail(log_path)
            raise RuntimeError(
                f"db-CBS failed with exit code {result.returncode}.\n{tail}"
            )
        plan = _parse_result(optimized_path, agent_tuple, dt=dt)
        _validate_pairwise_clearance(
            plan, {agent.name: agent.radius for agent in agent_tuple}
        )
        return plan


def _validate_pairwise_clearance(
    plan: DbcbsPlan,
    radii: dict[str, float],
    *,
    tolerance: float = 1.0e-6,
) -> None:
    """Reject optimized trajectories that violate continuous disc clearance."""

    names = tuple(plan.trajectories)
    minimum_clearance = math.inf
    minimum_distance = math.inf
    minimum_pair: tuple[str, str] | None = None
    minimum_time = 0.0
    required_at_minimum = 0.0
    for left_index, left_name in enumerate(names[:-1]):
        left = plan.trajectories[left_name]
        for right_name in names[left_index + 1 :]:
            right = plan.trajectories[right_name]
            required = float(radii[left_name]) + float(radii[right_name])
            interval_count = max(len(left), len(right)) - 1
            if interval_count <= 0:
                distance = math.hypot(left[0].x - right[0].x, left[0].y - right[0].y)
                candidates = ((distance, 0.0),)
            else:
                candidates = (
                    _segment_pair_distance(left, right, interval, plan.dt)
                    for interval in range(interval_count)
                )
            for distance, timestamp in candidates:
                clearance = distance - required
                if clearance < minimum_clearance:
                    minimum_clearance = clearance
                    minimum_distance = distance
                    minimum_pair = (left_name, right_name)
                    minimum_time = timestamp
                    required_at_minimum = required

    if minimum_pair is not None and minimum_clearance + tolerance < 0.0:
        raise RuntimeError(
            "db-CBS optimized trajectory violates robot footprint clearance: "
            f"{minimum_pair[0]} vs {minimum_pair[1]} at {minimum_time:.2f}s, "
            f"distance={minimum_distance:.4f}, required={required_at_minimum:.4f}"
        )


def _segment_pair_distance(
    left: tuple[PlanSample, ...],
    right: tuple[PlanSample, ...],
    interval: int,
    dt: float,
) -> tuple[float, float]:
    left0 = left[min(interval, len(left) - 1)]
    left1 = left[min(interval + 1, len(left) - 1)]
    right0 = right[min(interval, len(right) - 1)]
    right1 = right[min(interval + 1, len(right) - 1)]
    rx = left0.x - right0.x
    ry = left0.y - right0.y
    vx = (left1.x - left0.x) - (right1.x - right0.x)
    vy = (left1.y - left0.y) - (right1.y - right0.y)
    speed_squared = vx * vx + vy * vy
    fraction = (
        0.0
        if speed_squared <= 1.0e-18
        else max(0.0, min(1.0, -(rx * vx + ry * vy) / speed_squared))
    )
    return math.hypot(rx + fraction * vx, ry + fraction * vy), (
        interval + fraction
    ) * dt


def _validate_dbcbs_dt(dt: float) -> None:
    if not math.isclose(float(dt), DEFAULT_PLAN_DT, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(
            "The bundled db-CBS double_integrator_0 model has a fixed 0.1 s "
            f"timestep; got dt={dt!r}."
        )


def _dbcbs_subprocess_environment(root: Path) -> dict[str, str]:
    """Load db-CBS only from EAI's package and active Python environment."""

    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        root / "build/ompl",
        Path(sys.prefix) / "lib" / python_version / "site-packages/cmeel.prefix/lib",
    ]
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        conda_root = Path(conda_prefix)
        candidates.append(conda_root / "lib")
        candidates.extend(
            (conda_root / "lib").glob("python*/site-packages/cmeel.prefix/lib")
        )

    loader_paths: list[str] = []
    for path in candidates:
        resolved = str(path.resolve())
        if path.is_dir() and resolved not in loader_paths:
            loader_paths.append(resolved)
    for item in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
        if item and item not in loader_paths:
            loader_paths.append(item)

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(loader_paths)
    return env


def _log_tail(path: Path, line_count: int = 30) -> str:
    if not path.is_file():
        return "Planner produced no log."
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:])
