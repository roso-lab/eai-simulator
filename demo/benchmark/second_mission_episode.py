"""Formal GoalSession entry for each scene's second mission.

Skill execution stays on ``HouseNativeGoalBackend``: native attach, the
heading lock before a laden move, ``guide_people``, and Pepper record skills
are that backend. The measured instruction source is a goal-algorithm adapter.
``--algorithm emos`` asks EMOS for a real LLM assignment over the public robot
pool and dispatches with ``dispatch_skill``. ``--algorithm emos_diagnostic``
is the labeled non-LLM policy and is not a measured decision. The author
witness remains the admission path and is not the team the algorithm must copy.

Without a live session every non-observe skill fails closed as
``house_native_skill_adapter_pending``. That receipt is admission to the
formal runner, not business success. ``scoreable`` stays false, and these
tasks stay outside the 9x9 suite.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping


PENDING_REASON = "house_native_skill_adapter_pending"
BACKEND_ID = "house_native_goal_backend"

# Scene, frozen GOAL-01 runner, and the second mission that now uses this runner.
SCENE_FORMAL_EPISODES = (
    {
        "scene_key": "factory",
        "goal_task_id": "FACTORY-GOAL-01",
        "goal_episode": "demo.benchmark.goal_house_episode.run_house_episode",
        "second_task_id": "FACTORY-FIRE-01",
    },
    {
        "scene_key": "warehouse_nvd",
        "goal_task_id": "WAREHOUSE-GOAL-01",
        "goal_episode": "demo.benchmark.goal_house_episode.run_house_episode",
        "second_task_id": "WAREHOUSE-INBOUND-01",
    },
    {
        "scene_key": "modern_house_nvd",
        "goal_task_id": "MODERN_HOUSE-GOAL-01",
        "goal_episode": "demo.benchmark.goal_house_episode.run_house_episode",
        "second_task_id": "MODERN_HOUSE-DELIVERY-01",
    },
    {
        "scene_key": "city",
        "goal_task_id": "CITY-GOAL-01",
        "goal_episode": "demo.benchmark.goal_city_episode.run_city_episode",
        "second_task_id": "CITY-EXIT-01",
    },
    {
        "scene_key": "restaurant",
        "goal_task_id": "RESTAURANT-GOAL-01",
        "goal_episode": "demo.benchmark.goal_restaurant_episode.run_worker",
        "second_task_id": "RESTAURANT-SERVICE-01",
    },
)


def second_mission_builders() -> dict[str, Callable[[], dict[str, dict[str, Any]]]]:
    from demo.benchmark.city_exit_task import EXIT_TASK_ID, build_city_exit_package
    from demo.benchmark.factory_fire_task import FIRE_TASK_ID, build_factory_fire_package
    from demo.benchmark.house_delivery_task import DELIVERY_TASK_ID, build_house_delivery_package
    from demo.benchmark.restaurant_service_task import SERVICE_TASK_ID, build_restaurant_service_package
    from demo.benchmark.warehouse_inbound_task import INBOUND_TASK_ID, build_warehouse_inbound_package

    return {
        FIRE_TASK_ID: build_factory_fire_package,
        INBOUND_TASK_ID: build_warehouse_inbound_package,
        DELIVERY_TASK_ID: build_house_delivery_package,
        EXIT_TASK_ID: build_city_exit_package,
        SERVICE_TASK_ID: build_restaurant_service_package,
    }


def _action(identifier: str, kind: str, payload: Mapping[str, Any], time_s: float) -> dict[str, Any]:
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": identifier,
        "submitted_at_s": time_s,
        "type": kind,
        "payload": deepcopy(dict(payload)),
    }


def initial_state(public_task: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Place each robot at the start of its first route, without requiring an aerial region."""
    regions = public_task["public_world"]["regions"]
    ground = next(row["id"] for row in regions if "ground_navigation" in row["access_modes"])
    aerial_rows = [row["id"] for row in regions if "aerial_navigation" in row["access_modes"]]
    aerial = aerial_rows[0] if aerial_rows else ground
    robots = {
        robot["id"]: aerial if robot["mobility"] == "aerial" else ground
        for robot in public_task["candidate_robot_pool"]
    }
    routes = {row["id"]: row for row in public_task["public_world"].get("routes") or []}
    seen: set[str] = set()
    for task in plan.get("tasks") or []:
        arguments = task.get("arguments") or {}
        route_ids = arguments.get("route_ids") or []
        origin = routes[route_ids[0]].get("from_region_id") if route_ids and route_ids[0] in routes else None
        if not isinstance(origin, str) and isinstance(arguments.get("region_id"), str):
            origin = arguments["region_id"]
        if not isinstance(origin, str):
            continue
        for agent in task.get("assigned_agents") or []:
            if agent in robots and agent not in seen:
                robots[agent] = origin
                seen.add(agent)
    objects = {}
    for item in public_task["public_world"]["objects"]:
        initial = (item.get("physical_properties") or {}).get("initial_region_id")
        objects[item["id"]] = {"region_id": initial if isinstance(initial, str) else ground}
    return {"robot_regions": robots, "object_states": objects}


class SecondMissionWitness:
    """Submit one author witness and dispatch its skills in order."""

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()
        self._stopped = False

    def manifest(self) -> Mapping[str, Any]:
        return {
            "kind": "participant",
            "id": f"second-mission-witness:{self._plan['plan_id']}",
            "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            return [
                _action("second_mission_select", "select_team",
                        {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                _action("second_mission_plan", "submit_plan", {"plan": deepcopy(self._plan)}, 0.0),
            ]
        for item in events:
            if item["type"] != "action_result":
                continue
            status = item["payload"].get("status")
            if status == "succeeded":
                self._terminal.add(item["payload"]["action_id"])
            elif status in {"failed", "cancelled"}:
                self._stopped = True
        if self._stopped:
            return []
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"second_mission_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [_action(
            f"second_mission_dispatch_{index}", "dispatch_skill",
            {"task_id": tasks[index]["task_id"], "evidence_refs": []}, now,
        )]

    def close(self) -> None:
        pass


def deployment_record_for_disk(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drop the live human runtime before the deployment JSON is written."""
    people = deployment.get("city_exit_people") if isinstance(deployment, dict) else None
    if not isinstance(people, dict) or "runtime" not in people:
        return deployment
    persisted = dict(deployment)
    persisted["city_exit_people"] = {
        "person_ids": list(people.get("person_ids") or []),
        "runtime": "live_stage_runtime",
    }
    return persisted


def run_second_mission_episode(task_id: str, output_root: str | Path, *,
                               session: Any = None,
                               deployment: Mapping[str, Any] | None = None,
                               witness_index: int = 0,
                               algorithm: str = "witness",
                               adapter: Any = None,
                               plan_override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run one second mission through FormalGoalEpisodeRunner and the House skill backend.

    ``algorithm="emos"`` is the measured EMOS LLM path. ``emos_diagnostic`` is
    the labeled deterministic policy and does not count as that measurement.
    An explicit ``adapter`` is any other ``GoalAlgorithmAdapter``; the runner
    does not branch on its class. Measured plans use interface admission.
    ``plan_override`` (GPU-queue matrix handoff) replaces the author witness
    plan with a fixed expanded allocation from the multi-solution matrix; the
    witness participant then submits that plan unchanged.
    """
    from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
    from demo.benchmark.goal_house_episode import HOUSE_BACKEND_ID, HouseNativeGoalBackend, house_runtime_state
    from demo.benchmark.second_mission_deployment import build_second_mission_deployment

    builders = second_mission_builders()
    if task_id not in builders:
        raise ValueError(f"unknown second mission: {task_id}")
    package = builders[task_id]()
    public = package["public_task"]
    witnesses = package["hidden_evaluator"]["witness_plans"]
    if not 0 <= witness_index < len(witnesses):
        raise ValueError("witness index is outside the author witness list")
    plan = witnesses[witness_index]["plan"]
    if plan_override is not None:
        if adapter is not None or algorithm != "witness":
            raise ValueError("plan_override runs the fixed-plan witness path only")
        plan = deepcopy(dict(plan_override))
        # The runner dispatches serially in plan-task order, so a handed-over
        # plan with a parallel authored schedule (witness b and constructed
        # matrix rows) would see later dispatches fall outside their windows
        # after earlier tasks consume the clock. Re-sequence start times in
        # task order; declared durations are untouched and the allocation
        # itself is unchanged.
        clock_s = 0.0
        for task in plan["tasks"]:
            duration_s = float(task["execution"]["duration_s"])
            task["execution"] = {"start_s": clock_s, "duration_s": duration_s}
            clock_s += duration_s
    if deployment is None:
        deployment = build_second_mission_deployment(public, plan)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "second_mission_deployment.json").write_text(
        json.dumps(deployment_record_for_disk(deployment), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    if adapter is not None and algorithm != "witness":
        raise ValueError("pass either an adapter or an algorithm name")
    instruction_source = "author_witness"
    participant = SecondMissionWitness(plan)
    if adapter is not None:
        participant = adapter
        instruction_source = "goal_algorithm_adapter"
    elif algorithm == "emos":
        from demo.benchmark.second_mission_decision import SecondMissionEmosAdapter

        participant = SecondMissionEmosAdapter(plan, use_llm_discussion=True)
        instruction_source = "goal_algorithm_adapter"
    elif algorithm == "emos_diagnostic":
        from demo.benchmark.second_mission_decision import SecondMissionEmosAdapter

        participant = SecondMissionEmosAdapter(plan, use_llm_discussion=False)
        instruction_source = "goal_algorithm_adapter"
    elif algorithm != "witness":
        raise ValueError(f"unknown second-mission algorithm: {algorithm}")
    measured = instruction_source == "goal_algorithm_adapter"
    if measured:
        # Task structure only: the backend's plan copy feeds the pre-joint
        # heading hint (prerequisites -> next region), which reads no agents.
        # The measured team and assignments reach the backend exclusively
        # through the dispatched session tasks.
        structure_plan = deepcopy(plan)
        structure_plan["assigned_team"] = []
        for structure_task in structure_plan["tasks"]:
            structure_task["assigned_agents"] = []
        seed_plan = structure_plan
    else:
        seed_plan = plan
    backend = HouseNativeGoalBackend(
        public, session=session, deployment=deployment, plan=seed_plan)
    runner = FormalGoalEpisodeRunner(
        public, package["participant_api"], participant, backend,
        output_root=root / "goal_episode",
        admission="interface" if measured else "full",
    )
    result = runner.run(initial_state=house_runtime_state(public, seed_plan))
    bound_regions = {
        row["id"] for row in deployment["region_bindings"]
        if isinstance((row.get("candidate_geometry") or {}).get("center_m"), list)
    }
    geometry_admitted = False
    geometry: dict[str, Any] | None = None
    if session is not None and getattr(getattr(session, "base_env", None), "sim", None) is not None:
        from demo.benchmark.gpu_evidence_contract import admit_live_session
        geometry_admitted, geometry = admit_live_session(session)
    summary = {
        "episode_id": result["episode_id"],
        "error": result["error"],
        "turns": result["turns"],
        "skill_receipt_count": result["skill_receipt_count"],
        "task_id": public["task_id"],
        "scene_key": public["mission"]["scene_key"],
        "witness_id": witnesses[witness_index]["id"],
        "plan_id": plan["plan_id"],
        "submitted_plan_id": getattr(participant, "submitted_plan_id", None)
        if instruction_source == "goal_algorithm_adapter" else plan["plan_id"],
        "formal_backend": HOUSE_BACKEND_ID,
        "instruction_source": instruction_source,
        "allocation_source": getattr(participant, "allocation_source", None),
        "discussion_source": getattr(participant, "discussion_source", None),
        "adapter_id": participant.manifest()["id"],
        "adapter_trace": deepcopy(getattr(participant, "trace", [])),
        "skill_results": backend.evidence,
        "region_ids": sorted(bound_regions),
        "physical_episode_bound": session is not None,
        "formal_evidence_device": (
            str(session.device) if session is not None and getattr(session, "device", None) else None
        ),
        "formal_geometry_admitted": geometry_admitted,
        "geometry_readback": geometry,
        "clearance_status": "unknown",
        "cpu_is_formal_evidence": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "benchmark_ready": False,
        "scoreable": False,
    }
    (root / "second_mission_episode_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def bind_restaurant_service_bodies(deployment: dict[str, Any]) -> None:
    """Name the live robot and dish prims LiveBodies will sample."""
    from copy import deepcopy

    from demo.benchmark.goal_driven_fixtures import object_physical_properties

    for row in deployment["robot_instances"]:
        row["prim_path"] = f"/World/envs/env_0/{row['runtime_id']}"
    deployment["object_instances"] = [
        {
            "public_id": public_id,
            "runtime_id": public_id,
            "prim_path": f"/World/envs/env_0/{public_id}",
            "physical_properties": deepcopy(object_physical_properties(public_id)),
        }
        for public_id in ("service_plate_apple_a", "service_plate_apple_b")
    ]


def city_exit_person_path(gathering_xy: tuple[float, float], assembly_xy: tuple[float, float],
                          offset_xy: tuple[float, float], surface_z: float,
                          ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return a start and an assembly point. A path follower needs both."""
    import math
    start = (gathering_xy[0] + offset_xy[0], gathering_xy[1] + offset_xy[1], surface_z)
    goal = (assembly_xy[0] + offset_xy[0], assembly_xy[1] + offset_xy[1], surface_z)
    if math.dist(start, goal) <= 1e-4:
        raise ValueError("city exit person path has no travel")
    return start, goal


def _record_city_exit_people(session: Any, deployment: dict[str, Any]) -> None:
    """Spawn the dismissal roster at the gathering, aimed at the assembly."""
    del session
    try:
        import math

        import omni.usd
        from pxr import Usd, UsdPhysics

        from demo.benchmark.city_exit_task import dismissal_roster
        from demo.benchmark.second_mission_deployment import _city_surface_z
        from EAI_assets.humans.registry import HumanAssetRegistry
        from EAI_assets.humans.stage_runtime import HumanActorConfig, UsdHumanStageRuntime

        centres: dict[str, tuple[float, float]] = {}
        for row in deployment.get("region_bindings") or []:
            center = (row.get("candidate_geometry") or {}).get("center_m")
            if isinstance(center, (list, tuple)) and len(center) >= 2:
                centres[row["id"]] = (float(center[0]), float(center[1]))
        gathering = centres["dismissal_gathering"]
        assembly = centres["dismissal_assembly"]
        offsets = {
            "affected_person_00": (-0.30, -0.30),
            "affected_person_01": (-0.30, 0.30),
            "affected_person_02": (0.30, -0.30),
            "affected_person_03": (0.30, 0.30),
        }
        roster = dismissal_roster()
        if set(roster) != set(offsets):
            raise ValueError("city exit roster does not match the spawned offsets")
        assets = (
            "rp-aaron-rigged-001",
            "rp-amit-rigged-008-yup-t",
            "synbody-0000001",
            "synbody-0000020",
        )
        registry = HumanAssetRegistry.load_default()
        stage = omni.usd.get_context().get_stage()
        runtime = UsdHumanStageRuntime(
            stage, registry, cache_root=registry.human_root / "motions/cache", verify_hashes=True)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for index, person_id in enumerate(roster):
                offset = offsets[person_id]
                surface_z = _city_surface_z((gathering[0] + offset[0], gathering[1] + offset[1]))
                start, goal = city_exit_person_path(gathering, assembly, offset, surface_z)
                yaw = math.atan2(goal[1] - start[1], goal[0] - start[0])
                prim_path = f"/World/CityExitPeople/{person_id}"
                runtime.spawn(HumanActorConfig(
                    actor_id=person_id,
                    asset_id=assets[index],
                    prim_path=prim_path,
                    initial_pose=(start[0], start[1], start[2], yaw),
                    waypoints=(start, goal),
                    speed=1.2,
                    loop=False,
                    locomotion_motion_id="walk",
                ))
                prim = stage.GetPrimAtPath(prim_path)
                for child in Usd.PrimRange(prim):
                    if child.HasAPI(UsdPhysics.RigidBodyAPI):
                        UsdPhysics.RigidBodyAPI(child).CreateRigidBodyEnabledAttr(False)
                    if child.HasAPI(UsdPhysics.CollisionAPI):
                        UsdPhysics.CollisionAPI(child).CreateCollisionEnabledAttr(False)
        deployment["city_exit_people"] = {"runtime": runtime, "person_ids": list(roster)}
        print("city_exit_people", list(roster), flush=True)
    except Exception as exc:
        print(f"city_exit_people_failed {type(exc).__name__}: {exc}", flush=True)


def _record_restaurant_customers(session: Any, deployment: dict[str, Any]) -> None:
    """Spawn the static customers and keep only their measured positions."""
    try:
        import omni.usd
        from demo.restaurant_benchmark.service_layout import service_layout
        from demo.restaurant_benchmark.service_layout_probe import _spawn_customers

        stage = omni.usd.get_context().get_stage()
        _runtime, rows = _spawn_customers(stage, service_layout())
    except Exception as exc:
        print(f"restaurant_customers_failed {type(exc).__name__}: {exc}", flush=True)
        return
    deployment["service_subjects"] = {
        name: {
            "position_m": row["runtime_position_m"],
            "prim_path": row["prim_path"],
        }
        for name, row in rows.items()
        if isinstance(row.get("runtime_position_m"), list)
    }
    print("restaurant_customers", sorted(deployment["service_subjects"]), flush=True)


def _run_isaac_worker(task_id: str, output_root: Path, *, device: str,
                      algorithm: str = "witness",
                      allocation_id: str | None = None) -> dict[str, Any]:
    """Open one Isaac session and run the formal episode in that process."""
    import torch
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must be loaded by the simulator application, not worker preflight")
    from demo.benchmark.goal_house_episode import house_selection
    from demo.benchmark.second_mission_deployment import (
        build_second_mission_deployment, compile_second_mission_formal_deployment,
    )
    from simulator import SimulatorLaunchConfig, open_simulator_session

    builders = second_mission_builders()
    if task_id not in builders:
        raise ValueError(f"unknown second mission: {task_id}")
    package = builders[task_id]()
    if package["public_task"]["mission"]["scene_key"] == "restaurant":
        os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = str(
            Path("tmp/restaurant_benchmark/products-candidate-v7").resolve())
    witness_plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
    plan_override = None
    if allocation_id is not None:
        from demo.benchmark.second_mission_allocation_matrix import expand_allocation_plan

        plan_override = expand_allocation_plan(task_id, allocation_id)
    formal = compile_second_mission_formal_deployment(package["public_task"])
    deployment = formal or build_second_mission_deployment(package["public_task"], witness_plan)
    selection = house_selection(deployment)
    output_root.mkdir(parents=True, exist_ok=True)

    def configure(env_cfg):
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        env_cfg.sim.enable_scene_query_support = True
        # Factory cuda requests are rewritten to CPU PhysX by
        # simulator._runtime_device_for_env. Fabric batch kernels need CUDA,
        # so this scene leaves Fabric off. Do not set EAI_FACTORY_GPU_PHYSICS.
        scene_key = package["public_task"]["mission"]["scene_key"]
        if str(device).startswith("cuda") and scene_key != "factory":
            env_cfg.sim.use_fabric = True
        env_cfg.episode_length_s = 3600.0
        if formal is not None:
            from demo.benchmark.goal_native_objects import configure_goal_objects
            configure_goal_objects(env_cfg, formal)
        if scene_key == "restaurant":
            from demo.restaurant_benchmark.service_layout_probe import attach_service_dishes
            attach_service_dishes(env_cfg)
        if scene_key in {"modern_house_nvd", "factory"}:
            from demo.benchmark.second_mission_payloads import attach_second_mission_payloads
            attach_second_mission_payloads(env_cfg, package["public_task"])

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=device, seed=7,
        headless=True, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        aerial_sensor_backend="disabled", env_cfg_hook=configure)
    with open_simulator_session(config) as session:
        agents = list(session.possible_agents)
        for _ in range(10):
            actions = {agent: torch.zeros((1, 3), device=session.device) for agent in agents}
            session.env.step(actions)
        scene_key = package["public_task"]["mission"]["scene_key"]
        if scene_key == "restaurant":
            bind_restaurant_service_bodies(deployment)
            _record_restaurant_customers(session, deployment)
        elif scene_key == "city":
            _record_city_exit_people(session, deployment)
        elif scene_key in {"modern_house_nvd", "factory"}:
            from demo.benchmark.second_mission_deployment import resolve_support_deploy
            from demo.benchmark.second_mission_payloads import bind_second_mission_payloads

            deployment["support_deploy"] = resolve_support_deploy(package["public_task"])
            bind_second_mission_payloads(deployment)
        summary = run_second_mission_episode(
            task_id, output_root, session=session, deployment=deployment,
            algorithm=algorithm, plan_override=plan_override)
        summary["possible_agents"] = agents
        summary["session_device"] = str(session.device)
        (output_root / "second_mission_episode_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("second_mission_isaac", json.dumps({
            "task_id": summary["task_id"],
            "session_device": summary["session_device"],
            "possible_agents": agents,
            "formal_geometry_admitted": summary["formal_geometry_admitted"],
            "skill_results": summary["skill_results"],
            "scoreable": summary["scoreable"],
        }, ensure_ascii=False), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda:0", "cpu"), default="cuda:0")
    parser.add_argument(
        "--algorithm", choices=("witness", "emos", "emos_diagnostic"), default="witness")
    parser.add_argument(
        "--allocation", default=None,
        help="run one matrix allocation id (fixed-plan witness path, GPU queue 3)")
    parser.add_argument("--isaac", action="store_true")
    args = parser.parse_args(argv)
    plan_override = None
    if args.allocation is not None:
        if args.algorithm != "witness":
            raise SystemExit("--allocation requires the default witness algorithm")
        from demo.benchmark.second_mission_allocation_matrix import expand_allocation_plan

        plan_override = expand_allocation_plan(args.task, args.allocation)
    if args.isaac:
        _run_isaac_worker(
            args.task, args.output_root, device=args.device, algorithm=args.algorithm,
            allocation_id=args.allocation)
        return 0
    summary = run_second_mission_episode(
        args.task, args.output_root, algorithm=args.algorithm,
        plan_override=plan_override)
    print(json.dumps({key: summary[key] for key in (
        "task_id", "instruction_source", "allocation_source", "discussion_source", "adapter_id",
        "skill_receipt_count", "scoreable", "clearance_status")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
