#!/usr/bin/env python3
"""Load a converted Z1 USD and expose JointState control through Isaac ROS2 bridge."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


REQUIRED_PYTHON_MODULES = ("torch", "psutil", "typing_extensions")
REQUIRED_EXTENSIONS = ("isaacsim.core.nodes", "isaacsim.ros2.bridge")
GRAPH_TRIGGER_NODE = ("OnPhysicsStep", "isaacsim.core.nodes.OnPhysicsStep", "outputs:step")
GRAPH_PIPELINE_STAGE = "GRAPH_PIPELINE_STAGE_ONDEMAND"
ARTICULATION_CONTROLLER_TRIGGER = "SubscribeJointState.outputs:execOut"


def default_usd_path() -> str:
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / "usd" / "payloads" / "manipulators" / "z1" / "z1_description.usda")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Unitree Z1 USD with ROS2 JointState bridge.")
    parser.add_argument("--usd", default=default_usd_path(), help="Path to the converted Z1 USD.")
    parser.add_argument("--prim-path", default="/World/Z1", help="Stage prim path for the Z1 reference.")
    parser.add_argument(
        "--articulation-path",
        default=None,
        help="Articulation root path. Defaults to the first PhysicsArticulationRootAPI under --prim-path.",
    )
    parser.add_argument("--joint-states-topic", default="/z1/joint_states")
    parser.add_argument("--joint-commands-topic", default="/z1/joint_commands")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--test-frames", type=int, default=0, help="Exit after N frames; 0 runs until closed.")
    args, _ = parser.parse_known_args()
    sys.argv = [sys.argv[0]]
    return args


def missing_python_modules() -> list[str]:
    return [name for name in REQUIRED_PYTHON_MODULES if importlib.util.find_spec(name) is None]


def require_bridge_python_dependencies() -> None:
    missing = missing_python_modules()
    if not missing:
        return

    missing_text = ", ".join(missing)
    raise RuntimeError(
        "Isaac ROS2 bridge needs Python modules missing from this interpreter: "
        f"{missing_text}. Run algorithm/ros/z1/run_z1_ros2_bridge.sh with "
        "ISAACSIM_CONDA_ENV=<your-python3.11-isaac-env>, or activate that conda env, "
        "source Isaac Sim setup_conda_env.sh, and run this script with python."
    )


def enable_required_extensions(simulation_app) -> None:
    import omni.kit.app

    ext_manager = omni.kit.app.get_app().get_extension_manager()
    for extension_name in REQUIRED_EXTENSIONS:
        ext_manager.set_extension_enabled_immediate(extension_name, True)
        simulation_app.update()


def find_articulation_root_path(stage, root_path: str) -> str | None:
    from pxr import Usd, UsdPhysics

    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim.IsValid():
        return None

    for prim in Usd.PrimRange(root_prim):
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            return str(prim.GetPath())
    return None


def ensure_physics_scene(stage) -> None:
    from pxr import UsdPhysics

    if not stage.GetPrimAtPath("/World/physicsScene").IsValid():
        UsdPhysics.Scene.Define(stage, "/World/physicsScene")


def main() -> None:
    args = parse_args()
    usd_path = Path(args.usd).expanduser().resolve()
    if not usd_path.is_file():
        raise FileNotFoundError(f"Z1 USD not found: {usd_path}")

    require_bridge_python_dependencies()

    from isaacsim import SimulationApp

    simulation_app = SimulationApp({"renderer": "RaytracedLighting", "headless": bool(args.headless)})

    import omni.graph.core as og
    import omni.timeline
    import omni.usd
    import usdrt.Sdf
    from pxr import UsdGeom

    enable_required_extensions(simulation_app)

    usd_context = omni.usd.get_context()
    stage = usd_context.get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    ensure_physics_scene(stage)

    robot_prim = UsdGeom.Xform.Define(stage, args.prim_path).GetPrim()
    robot_prim.GetReferences().AddReference(str(usd_path))
    if not robot_prim.IsValid():
        simulation_app.close()
        raise RuntimeError(f"Failed to load Z1 at {args.prim_path}")
    simulation_app.update()

    articulation_path = args.articulation_path or find_articulation_root_path(stage, args.prim_path)
    if not articulation_path:
        simulation_app.close()
        raise RuntimeError(f"Could not find an articulation root under {args.prim_path}")

    og.Controller.edit(
        {
            "graph_path": "/Z1_ROS2_ActionGraph",
            "evaluator_name": "execution",
            "pipeline_stage": getattr(og.GraphPipelineStage, GRAPH_PIPELINE_STAGE),
        },
        {
            og.Controller.Keys.CREATE_NODES: [
                (GRAPH_TRIGGER_NODE[0], GRAPH_TRIGGER_NODE[1]),
                ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                ("Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
                ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
                ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ],
            og.Controller.Keys.CONNECT: [
                (f"{GRAPH_TRIGGER_NODE[0]}.{GRAPH_TRIGGER_NODE[2]}", "PublishJointState.inputs:execIn"),
                (f"{GRAPH_TRIGGER_NODE[0]}.{GRAPH_TRIGGER_NODE[2]}", "SubscribeJointState.inputs:execIn"),
                (f"{GRAPH_TRIGGER_NODE[0]}.{GRAPH_TRIGGER_NODE[2]}", "PublishClock.inputs:execIn"),
                (ARTICULATION_CONTROLLER_TRIGGER, "ArticulationController.inputs:execIn"),
                ("Context.outputs:context", "PublishJointState.inputs:context"),
                ("Context.outputs:context", "SubscribeJointState.inputs:context"),
                ("Context.outputs:context", "PublishClock.inputs:context"),
                ("ReadSimTime.outputs:simulationTime", "PublishJointState.inputs:timeStamp"),
                ("ReadSimTime.outputs:simulationTime", "PublishClock.inputs:timeStamp"),
                ("SubscribeJointState.outputs:jointNames", "ArticulationController.inputs:jointNames"),
                ("SubscribeJointState.outputs:positionCommand", "ArticulationController.inputs:positionCommand"),
                ("SubscribeJointState.outputs:velocityCommand", "ArticulationController.inputs:velocityCommand"),
                ("SubscribeJointState.outputs:effortCommand", "ArticulationController.inputs:effortCommand"),
            ],
            og.Controller.Keys.SET_VALUES: [
                ("ArticulationController.inputs:robotPath", articulation_path),
                ("PublishJointState.inputs:topicName", args.joint_states_topic),
                ("SubscribeJointState.inputs:topicName", args.joint_commands_topic),
                ("PublishJointState.inputs:targetPrim", [usdrt.Sdf.Path(articulation_path)]),
                ("PublishClock.inputs:topicName", "/clock"),
            ],
        },
    )

    simulation_app.update()
    timeline = omni.timeline.get_timeline_interface()
    timeline.play()

    frame = 0
    while simulation_app.is_running():
        simulation_app.update()
        frame += 1
        if args.test_frames > 0 and frame >= args.test_frames:
            break

    timeline.stop()
    simulation_app.close()


if __name__ == "__main__":
    main()
