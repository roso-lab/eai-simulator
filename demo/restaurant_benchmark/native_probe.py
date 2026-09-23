"""Run a bounded restaurant scene-load probe through the shared simulator session."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import uuid

from . import config as scene_config


_DIAGNOSTIC_ROBOTS = {
    "carter": "CARTER_DIFF_CFG",
}


def build_selection(robot_type: str | None = None) -> dict:
    robots = []
    if robot_type is not None:
        try:
            controller = _DIAGNOSTIC_ROBOTS[robot_type]
        except KeyError as exc:
            raise ValueError(f"unsupported diagnostic robot: {robot_type}") from exc
        robots.append(
            {
                "type": robot_type,
                "controller": {"mode": "default", "cfg": controller},
                "attachments": [],
            }
        )
    return {
        "version": 1,
        "task_name": "restaurant_scene",
        "scene_key": scene_config.SCENE_KEY,
        "robots": robots,
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_probe(
    output_root: str | Path,
    *,
    device: str,
    steps: int,
    gui: bool,
    robot_type: str | None = None,
    settle_steps: int = 120,
) -> dict:
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError("steps must be a positive integer")
    if (
        isinstance(settle_steps, bool)
        or not isinstance(settle_steps, int)
        or settle_steps < 0
    ):
        raise ValueError("settle_steps must be a nonnegative integer")
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "eai.restaurant.native_probe",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.SCENE_KEY,
        "status": "starting",
        "requested_device": device,
        "requested_steps": steps,
        "requested_settle_steps": settle_steps if robot_type is not None else 0,
        "requested_robot_type": robot_type,
        "gui": gui,
        "scope": "scene_load_step_and_optional_author_view_only",
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    report_path = output / "native_probe_report.json"
    _write(report_path, report)

    from simulator import SimulatorLaunchConfig, open_simulator_session

    launch = SimulatorLaunchConfig(
        env="restaurant_scene",
        resolved_env_name="EAI-Interactive-v0",
        selection_data=build_selection(robot_type),
        num_envs=1,
        device=device,
        seed=17,
        headless=not gui,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
    )
    try:
        with open_simulator_session(launch) as session:
            import omni.usd
            from pxr import Usd, UsdGeom, UsdPhysics

            stage = omni.usd.get_context().get_stage()
            scene = stage.GetPrimAtPath("/World/Restaurant")
            if not scene or not scene.IsValid():
                raise RuntimeError("restaurant scene prim is absent after environment reset")
            if robot_type is not None:
                import torch

                if len(session.possible_agents) != 1:
                    raise RuntimeError(
                        f"diagnostic robot selection produced {session.possible_agents}"
                    )
                agent = session.possible_agents[0]
                articulation = session.base_env.scene.articulations[agent]
                spawn_position = articulation.data.root_pos_w[0].detach().cpu().tolist()

                def step_zero_command(count: int) -> None:
                    for _ in range(count):
                        actions = {
                            agent: torch.zeros(
                                (session.num_envs, 3), device=session.device
                            )
                        }
                        session.env.step(actions)

                step_zero_command(settle_steps)
                robot_start = articulation.data.root_pos_w[0].detach().cpu().tolist()
                step_zero_command(steps)
                robot_end = articulation.data.root_pos_w[0].detach().cpu().tolist()
                displacement = sum(
                    (float(robot_end[index]) - float(robot_start[index])) ** 2
                    for index in range(3)
                ) ** 0.5
                report["robot_stationary_observation"] = {
                    "agent_id": agent,
                    "robot_type": robot_type,
                    "spawn_root_position_m": [float(value) for value in spawn_position],
                    "settle_steps": settle_steps,
                    "start_root_position_m": [float(value) for value in robot_start],
                    "end_root_position_m": [float(value) for value in robot_end],
                    "displacement_m": displacement,
                    "vertical_drift_m": float(robot_end[2]) - float(robot_start[2]),
                    "scope": "spawn_and_zero_command_window_only",
                    "navigation_validated": False,
                }
            else:
                for _ in range(steps):
                    session.base_env.sim.step(render=gui)

            scales = []
            for operation in UsdGeom.Xformable(scene).GetOrderedXformOps():
                if operation.GetOpType() == UsdGeom.XformOp.TypeScale:
                    scales.append([float(value) for value in operation.Get()])
            collisions = [
                str(prim.GetPath())
                for prim in Usd.PrimRange(scene)
                if prim.HasAPI(UsdPhysics.CollisionAPI)
            ]
            report.update(
                status="diagnostic_completed",
                actual_device=session.device,
                physics_steps=int(session.base_env.sim.current_time_step_index),
                possible_agents=list(session.possible_agents),
                scene_prim_path=str(scene.GetPath()),
                scene_scale_ops=scales,
                collision_prim_count=len(collisions),
                collision_prim_paths=collisions,
            )
            if gui:
                from omni.kit.viewport.utility import get_active_viewport
                from demo.city_benchmark.scene.presentation import capture_viewport

                hidden_for_review = [
                    "/World/Restaurant/assemblies_context/assembly_BaseBuildingL0/CeilingL0",
                ]
                for prim_path in hidden_for_review:
                    prim = stage.GetPrimAtPath(prim_path)
                    if not prim or not prim.IsValid():
                        raise RuntimeError(f"author-review occluder is absent: {prim_path}")
                    UsdGeom.Imageable(prim).MakeInvisible()
                viewport = get_active_viewport()
                if viewport is None:
                    raise RuntimeError("GUI probe has no active viewport")
                eye = (37.0, -7.0, 14.0)
                target = (18.0, 12.0, 1.2)
                session.base_env.sim.set_camera_view(eye=eye, target=target)
                for _ in range(32):
                    session.base_env.sim.render()
                capture = capture_viewport(
                    session.simulation_app,
                    viewport,
                    output / "restaurant_overview.png",
                    render=session.base_env.sim.render,
                )
                capture["sha256"] = _sha256(Path(capture["path"]))
                capture["camera_eye_world_m"] = list(eye)
                capture["camera_target_world_m"] = list(target)
                capture["scope"] = "author_review_camera_not_agent_observation"
                capture["temporarily_hidden_prim_paths"] = hidden_for_review
                report["capture"] = capture
            _write(report_path, report)
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _write(report_path, report)
        raise
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--settle-steps", type=int, default=120)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--robot", choices=tuple(_DIAGNOSTIC_ROBOTS))
    args = parser.parse_args(argv)
    report = run_probe(
        args.output_root,
        device=args.device,
        steps=args.steps,
        gui=args.gui,
        robot_type=args.robot,
        settle_steps=args.settle_steps,
    )
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
