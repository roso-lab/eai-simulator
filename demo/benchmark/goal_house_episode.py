"""Formal House goal episode entry, copied from the City driver shape.

The witness plan is the episode plan. Skills that do not yet have a House
physical adapter fail with a factual receipt after the session has stepped;
they are not reported as business success. ``scoreable`` stays false.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .goal_driven_contract import validate_goal_plan
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner


HOUSE_EPISODE_PLAN_ID = "house_native_goal_episode_v1"
HOUSE_BACKEND_ID = "house_native_goal_backend"
_PENDING_REASON = "house_native_skill_adapter_pending"
INDOOR_EPISODE_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")


def _yaw(state: list[float]) -> float:
    import math
    w, qx, qy, qz = state[3:7]
    return math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _root_state(session: Any, runtime_id: str) -> list[float]:
    return session.base_env.scene.articulations[runtime_id].data.root_state_w[0].detach().cpu().tolist()


def _seek_command(state: list[float], target_xy: tuple[float, float],
                  speed_limit: float, stop_radius: float, yaw_limit: float = 0.3) -> list[float]:
    """Turn-then-drive. M20's policy does not track a lateral velocity command."""
    import math
    dx, dy = float(target_xy[0]) - state[0], float(target_xy[1]) - state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    if abs(error) > 0.35:
        return [0.0, 0.0, max(-yaw_limit * 1.5, min(yaw_limit * 1.5, 1.5 * error))]
    forward = speed_limit * max(0.2, math.cos(error))
    return [forward, 0.0, max(-yaw_limit, min(yaw_limit, 1.5 * error))]


def _region_xy(deployment: Mapping[str, Any] | None, region_id: str) -> tuple[float, float] | None:
    if deployment is None:
        return None
    for row in deployment.get("region_bindings") or []:
        if row.get("id") != region_id:
            continue
        center = (row.get("candidate_geometry") or {}).get("center_m")
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            return float(center[0]), float(center[1])
    return None


def _action(identifier: str, kind: str, payload: Mapping[str, Any],
            submitted_at_s: float) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": float(submitted_at_s),
            "type": kind, "payload": deepcopy(dict(payload))}


def build_house_episode_plan(package: Mapping[str, Any]) -> dict[str, Any]:
    """Use the first authored witness as the serial native plan."""
    witnesses = package["hidden_evaluator"]["witness_plans"]
    if not witnesses:
        raise ValueError("house package has no witness plan")
    plan = deepcopy(witnesses[0]["plan"])
    plan["plan_id"] = HOUSE_EPISODE_PLAN_ID
    validate_goal_plan(plan, task_spec=package["public_task"])
    return plan


def house_runtime_state(public_task: Mapping[str, Any]) -> dict[str, Any]:
    """A legal initial snapshot using the first region each mobility can enter."""
    regions = public_task["public_world"]["regions"]
    aerial = next(row["id"] for row in regions if "aerial_navigation" in row["access_modes"])
    ground = next(row["id"] for row in regions
                  if "ground_navigation" in row["access_modes"] or "stair_traversal" in row["access_modes"])
    robots = {robot["id"]: aerial if robot["mobility"] == "aerial" else ground
              for robot in public_task["candidate_robot_pool"]}
    objects = {}
    for item in public_task["public_world"]["objects"]:
        initial = (item.get("physical_properties") or {}).get("initial_region_id")
        objects[item["id"]] = {"region_id": initial if isinstance(initial, str) else ground}
    return {"robot_regions": robots, "object_states": objects}


class HouseWitnessParticipant:
    """Dispatch the House witness plan one skill at a time."""

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "house-native-goal-witness", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            return [_action("house_witness_select", "select_team",
                            {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                    _action("house_witness_plan", "submit_plan",
                            {"plan": deepcopy(self._plan)}, 0.0)]
        for item in events:
            if item["type"] == "action_result" and item["payload"].get("status") in {
                    "succeeded", "failed", "cancelled"}:
                self._terminal.add(item["payload"]["action_id"])
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"house_witness_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [_action(f"house_witness_dispatch_{index}", "dispatch_skill",
                        {"task_id": tasks[index]["task_id"], "evidence_refs": []}, now)]

    def close(self) -> None:
        pass


class HouseNativeGoalBackend:
    """Step an open House session and record an honest skill receipt.

    ``session is None`` is the offline contract test: time advances by the
    declared duration and every skill fails closed as adapter-pending.
    A live session steps physics, reads goal objects for ``observe``, and
    still fails skills whose motion adapter is not bound.
    """

    def __init__(self, public_task: Mapping[str, Any], *, session: Any = None,
                 deployment: Mapping[str, Any] | None = None):
        self.public_task = public_task
        self.session = session
        self.deployment = deployment
        self.time_s = 0.0
        self.evidence: list[dict[str, Any]] = []

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        row = ledger["tasks"][task["task_id"]]
        if (ledger["episode_id"] != episode_id or row["status"] != "pending"
                or row["task"] != task or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native house task does not match the pending GoalSession dispatch")
        duration = float(task["execution"]["duration_s"])
        window_end = float(task["execution"]["start_s"]) + duration
        observed = None
        if self.session is None:
            self.time_s = max(self.time_s, window_end)
        else:
            import torch
            dt = float(self.session.base_env.step_dt)
            steps = max(1, min(30, int(duration / dt) if dt else 1))
            agents = list(self.session.possible_agents)
            for _ in range(steps):
                actions = {agent: torch.zeros((1, 3), device=self.session.device) for agent in agents}
                self.session.env.step(actions)
            # The ledger follows the plan window. A short physics step must not
            # dispatch the next skill before its half-open window opens.
            self.time_s = max(self.time_s, window_end, float(self.session.base_env.sim.current_time))
            if self.deployment is not None and task["skill"] == "observe":
                from demo.benchmark.goal_native_objects import read_goal_objects
                import omni.usd
                stage = omni.usd.get_context().get_stage()
                observed = read_goal_objects(stage, self.session.base_env.scene, self.deployment)
        status, reason = self._skill_result(task, observed)
        self.evidence.append({
            "task_id": task["task_id"], "skill": task["skill"], "status": status,
            "reason": reason, "time_s": self.time_s,
        })
        receipt = {
            "backend_id": HOUSE_BACKEND_ID,
            "receipt_ref": f"{HOUSE_BACKEND_ID}:{episode_id}:{task['task_id']}",
            "task_id": task["task_id"],
            "action_id": row["dispatch_action_id"],
            "status": status,
            "completed_at_s": self.time_s,
            "episode_id": episode_id,
            "evidence_refs": [f"house:{task['task_id']}:{status}"],
        }
        return {"status": status, "completed_at_s": self.time_s, "receipt": receipt,
                "runtime_state": house_runtime_state(self.public_task)}

    def _skill_result(self, task: Mapping[str, Any], observed: Any) -> tuple[str, str | None]:
        skill = task["skill"]
        if self.session is None:
            if skill == "observe":
                return "succeeded", None
            return "failed", _PENDING_REASON
        if skill == "observe":
            return ("succeeded", None) if observed else ("failed", "observe_objects_unreadable")
        if skill in {"ground_navigation", "aerial_navigation", "stair_traversal"}:
            return self._drive_to_region(task)
        if skill in {"pick", "place", "handoff", "cooperative_carry"}:
            return self._manipulate(task)
        return "failed", _PENDING_REASON

    def _drive_to_region(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        import math
        import torch
        arguments = task["arguments"]
        region_id = arguments.get("exit_region_id") or arguments.get("region_id")
        if not isinstance(region_id, str):
            return "failed", "navigation_region_missing"
        target = _region_xy(self.deployment, region_id)
        if target is None:
            return "failed", f"navigation_region_unbound:{region_id}"
        public_ids = list(task["assigned_agents"])
        runtime_ids = [self._runtime_id(agent_id) for agent_id in public_ids]
        if any(item is None for item in runtime_ids):
            return "failed", "navigation_runtime_id_missing"
        session = self.session
        stop = 2.0 if task["skill"] == "aerial_navigation" else 1.2
        last_distance = None
        for _ in range(900):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            last_distance = max(math.hypot(state[0] - target[0], state[1] - target[1])
                                for state in states.values())
            if last_distance <= stop:
                return "succeeded", None
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                if task["skill"] == "aerial_navigation":
                    goal = torch.tensor([[target[0], target[1], state[2]]],
                                        device=session.device, dtype=torch.float32)
                    session.base_env.set_command(runtime_id, "goal_position", goal)
                    actions[runtime_id] = goal
                else:
                    command = _seek_command(state, target, 0.35, stop)
                    session.base_env.set_command(
                        runtime_id, "velocity",
                        torch.tensor([command], device=session.device, dtype=torch.float32))
                    actions[runtime_id][0] = torch.tensor(command, device=session.device)
            session.env.step(actions)
        return "failed", f"navigation_target_not_reached:{last_distance:.2f}m"

    def _manipulate(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        import omni.usd
        from demo.benchmark.goal_attachment_probe import LiveBodies
        from demo.benchmark.goal_native_attachment import (
            commit_attachment, detach_attachment, prepare_attachment)
        arguments = task["arguments"]
        bindings = list(arguments.get("carrier_bindings") or [])
        carriers = {row["agent_id"]: row["point_id"] for row in bindings
                    if isinstance(row, Mapping)}
        if not carriers or not isinstance(arguments.get("object_id"), str):
            return "failed", "manipulation_arguments_incomplete"
        stage = omni.usd.get_context().get_stage()
        try:
            bodies = LiveBodies(self.session.base_env.scene, self.deployment)
            prepared = prepare_attachment(
                stage, self.public_task, object_id=arguments["object_id"],
                mode_id=arguments.get("handling_mode_id", "single_grip"),
                carriers=carriers, active_member_ids=list(carriers),
                body_bindings=bodies.bindings, pose_provider=bodies)
            if task["skill"] == "place":
                return "failed", "place_requires_an_existing_hold"
            receipt = commit_attachment(
                stage, prepared, active_member_ids=list(carriers), pose_provider=bodies)
        except Exception as exc:
            return "failed", f"manipulation_rejected:{type(exc).__name__}"
        destination = arguments.get("destination_region_id") or arguments.get("placement_region_id")
        moved = ("succeeded", None)
        if isinstance(destination, str):
            moved = self._drive_to_region({
                **task, "skill": "ground_navigation",
                "arguments": {"region_id": destination},
            })
        try:
            detach_attachment(stage, receipt)
        except Exception as exc:
            return "failed", f"manipulation_detach_rejected:{type(exc).__name__}"
        return moved

    def _runtime_id(self, public_id: str) -> str | None:
        if self.deployment is None:
            return None
        for row in self.deployment["robot_instances"]:
            if row["public_id"] == public_id:
                return row["runtime_id"]
        return None


def run_house_episode_offline(output_root: str | Path, *,
                              roster_profile: str = "bound_native_v2",
                              scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Run an indoor witness through GoalSession without Isaac."""
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    plan = build_house_episode_plan(package)
    public = package["public_task"]
    root = Path(output_root)
    backend = HouseNativeGoalBackend(public)
    runner = FormalGoalEpisodeRunner(
        public, package["participant_api"], HouseWitnessParticipant(plan), backend,
        output_root=root / "goal_episode")
    result = runner.run(initial_state=house_runtime_state(public))
    summary = {
        "episode_id": result["episode_id"],
        "error": result["error"],
        "turns": result["turns"],
        "skill_receipt_count": result["skill_receipt_count"],
        "task_id": public["task_id"],
        "scene_key": scene_key,
        "roster_profile": roster_profile,
        "pending_skills": [row["skill"] for row in backend.evidence if row["status"] != "succeeded"],
        "scoreable": False,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "house_episode_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_house_deployment(roster_profile: str = "bound_native_v2",
                           scene_key: str = "modern_house_nvd"):
    """Compile an indoor deployment, allowing the opt-in seven-robot roster."""
    from .goal_deployment import compile_goal_deployment
    from .goal_indoor_deployment import build_indoor_deployment_overlay
    if scene_key not in INDOOR_EPISODE_SCENES:
        raise ValueError(f"unsupported indoor episode scene: {scene_key}")
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    extended = roster_profile != "legacy_v1"
    overlay = build_indoor_deployment_overlay(
        package["public_task"], verify_sources=False,
        allow_extended_roster=extended)
    return package, compile_goal_deployment(package["public_task"], overlay)


def house_selection(deployment: Mapping[str, Any]) -> dict[str, Any]:
    from EAI.hmrs_env.env_diy.catalog import default_controller_cfg
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
    robots = []
    for row in deployment["robot_instances"]:
        directive = row["spawn_binding"]["deployment_directive"]
        robots.append({
            "type": row["type"],
            "controller": {"mode": "default", "cfg": default_controller_cfg(row["type"])},
            "spawn_pose": {"position": list(directive["position_m"]),
                           "rotation": list(directive["rotation_wxyz"])},
            "attachments": [],
        })
    selection = {"version": 1, "scene_key": deployment["scene_key"],
                 "task_name": f"goal_episode_{deployment['scene_key']}", "robots": robots}
    interactive_selection_from_dict(selection)
    return selection


def prepare_house_episode(output_root: str | Path, *, device: str = "cpu",
                          roster_profile: str = "bound_native_v2",
                          scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Write the launch inputs. Isaac starts only in the worker process."""
    package, deployment = build_house_deployment(roster_profile, scene_key=scene_key)
    selection = house_selection(deployment)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=False)
    request = {"schema": "eai.benchmark.goal_house_episode_request", "version": 1,
               "device": device, "roster_profile": roster_profile,
               "task_id": package["public_task"]["task_id"]}
    for name, value in (("request.json", request), ("deployment.json", deployment),
                        ("selection.json", selection), ("package.json", package)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return request


def _run_house_worker(root: Path) -> dict[str, Any]:
    """Open Kit in this process. Callers must not have imported pxr already."""
    import torch
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must be loaded by the simulator application, not worker preflight")
    from simulator import SimulatorLaunchConfig, open_simulator_session
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    public = package["public_task"]
    plan = build_house_episode_plan(package)

    def configure(env_cfg):
        from demo.benchmark.goal_native_objects import configure_goal_objects
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0
        configure_goal_objects(env_cfg, deployment)

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=7,
        headless=True, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=configure)
    with open_simulator_session(config) as session:
        agents = list(session.possible_agents)
        for _ in range(10):
            actions = {agent: torch.zeros((1, 3), device=session.device) for agent in agents}
            session.env.step(actions)
        backend = HouseNativeGoalBackend(public, session=session, deployment=deployment)
        runner = FormalGoalEpisodeRunner(
            public, package["participant_api"], HouseWitnessParticipant(plan), backend,
            output_root=root / "goal_episode")
        result = runner.run(initial_state=house_runtime_state(public))
        summary = {
            "episode_id": result["episode_id"],
            "error": result["error"],
            "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "task_id": public["task_id"],
            "roster_profile": request["roster_profile"],
            "device": request["device"],
            "possible_agents": agents,
            "pending_skills": [row["skill"] for row in backend.evidence
                               if row["status"] != "succeeded"],
            "scoreable": False,
        }
        (root / "house_episode_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def run_house_episode(output_root: str | Path, *, device: str = "cpu",
                      roster_profile: str = "bound_native_v2",
                      scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Prepare inputs and run the indoor session in a clean worker process."""
    import subprocess
    root = Path(output_root)
    prepare_house_episode(root, device=device, roster_profile=roster_profile,
                          scene_key=scene_key)
    command = [sys.executable, "-m", "demo.benchmark.goal_house_episode",
               "--worker", "--output-root", str(root)]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], check=False)
    summary_path = root / "house_episode_summary.json"
    if completed.returncode != 0 or not summary_path.is_file():
        raise RuntimeError(f"house episode worker failed with code {completed.returncode}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scene", choices=INDOOR_EPISODE_SCENES, default="modern_house_nvd")
    parser.add_argument("--roster", choices=("legacy_v1", "bound_native_v2"), default="bound_native_v2")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--offline", action="store_true",
                        help="run the GoalSession contract without Isaac")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _run_house_worker(args.output_root)
        return 0
    if args.offline:
        summary = run_house_episode_offline(
            args.output_root, roster_profile=args.roster, scene_key=args.scene)
    else:
        summary = run_house_episode(
            args.output_root, device=args.device, roster_profile=args.roster,
            scene_key=args.scene)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    stopped_on_unbound_skill = "prerequisite" in (summary["error"] or "")
    ran = summary["skill_receipt_count"] >= 1 and (
        summary["error"] is None or stopped_on_unbound_skill)
    return 0 if ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
