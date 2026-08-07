from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from typing import Any

import isaaclab.sim as sim_utils
from isaaclab.assets import ArticulationCfg, AssetBaseCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.terrains import TerrainImporterCfg
from isaaclab.utils import configclass

from EAI.hmrs_env import MultiRobotDirectEnvCfg
from EAI_assets.humans import HUMAN_FEMALE_DEFAULT_Z, HUMAN_FEMALE_IDLE_CFG, HUMAN_FEMALE_RIG_CFG, HUMAN_FEMALE_WALK_CFG
from EAI_assets.robots.b2 import B2_CFG
from EAI_assets.robots.carter import CARTER_CFG
from EAI_assets.robots.coco import COCO_CFG
from EAI_assets.robots.deeprobotics import (
    DEEPROBOTICS_LITE3_CFG,
    DEEPROBOTICS_M20_CFG,
)
from EAI_assets.robots.go2 import GO2_CFG
from EAI_assets.robots.pepper import PEPPER_CFG
from EAI_assets.robots.quadcopter import CRAZYFLIE_CFG
from EAI_assets.robots.scout import SCOUT_CFG
from EAI_assets.robots.mushr_v2 import MUSHR_V2_CFG, MUSHR_V2_RWD_CFG
from EAI_assets.robots.unitree import G1_CFG
from EAI_assets.robots.ur5_mount import (
    UR5_MOUNT_PROFILES,
    Ur5MountProfile,
    build_mounted_ur5_asset_cfg,
    build_ur5_host_cfg,
)
from EAI_assets.robots.z1_mount import (
    Z1_MOUNT_PROFILES,
    Z1MountProfile,
    build_mounted_z1_asset_cfg,
)
from EAI_assets.sensor.high_sensor import GSHubCfg
from EAI_hmrs.controller_loader import load_controller_attr
from EAI.hmrs_env.env_diy.flow import (
    AttachmentSelection,
    ControllerChoice,
    InteractiveSelection,
    RobotSelection,
    attachment_supported,
    controller_cfg_names,
    default_controller_cfg,
    choose_terminal_interactive_selection,
    interactive_selection_from_dict as _flow_selection_from_dict,
    interactive_selection_to_dict,
)


@dataclass(frozen=True)
class SceneOption:
    key: str
    label: str
    spawn_cfg: Any | tuple[str, str] | None = None
    prim_path: str = "/World/InteractiveScene"
    spawn_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    robot_spawn_origin: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class RobotOption:
    key: str
    label: str
    cfg: ArticulationCfg | None
    controller: Any
    default_z: float
    gshub_mount_link: str | None = None
    ur5_mount_profile: Ur5MountProfile | None = None
    z1_mount_profile: Z1MountProfile | None = None
    gshub_offset: tuple[float, float, float] = (0.026, 0.0, 0.0)
    lidar_mount_link: str | None = None
    lidar_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    lidar_rot: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)


DESERT_SCENE_Z_OFFSET = -4322.563
HOSPITAL_SCENE_OFFSET = (-23.0896, 0.7555, -0.0010)
HOSPITAL_ROBOT_SPAWN_ORIGIN = (1.175, 1.8)
HUMAN_INITIAL_YAW = math.pi * 0.5
HUMAN_INITIAL_ROT = (0.707106781187, 0.0, 0.0, 0.707106781187)


SCENE_OPTIONS = [
    SceneOption("plane", "Plane flat ground", None),
    SceneOption("warehouse", "Warehouse", ("EAI_assets.scene.warehouse", "WAREHOUSE_CFG")),
    SceneOption(
        "factory",
        "Factory",
        ("EAI_assets.scene.factory", "FACTORY_CFG"),
        prim_path="/World/Indoor",
    ),
    SceneOption("airs", "AIRS scene", ("EAI_assets.scene.airs", "AIRS_CFG")),
    SceneOption("garden", "Garden", ("EAI_assets.scene.garden", "GARDEN_CFG")),
    SceneOption(
        "desert",
        "Desert",
        ("EAI_assets.scene.desert", "DESERT_CFG"),
        spawn_pos=(0.0, 0.0, DESERT_SCENE_Z_OFFSET),
    ),
    SceneOption(
        "hospital",
        "Hospital",
        ("EAI_assets.scene.hospital", "HOSPITAL_CFG"),
        spawn_pos=HOSPITAL_SCENE_OFFSET,
        robot_spawn_origin=HOSPITAL_ROBOT_SPAWN_ORIGIN,
    ),
]


CONTROLLER_CFG_IMPORTS = {
    "CARTER_DIFF_CFG": ("traditional/carter_diff/carter_diff.py", "CARTER_DIFF_CFG"),
    "PEPPER_HOLONOMIC_CFG": ("traditional/pepper_holonomic/pepper_holonomic.py", "PEPPER_HOLONOMIC_CFG"),
    "GO2_VELOCITY_RSL_CFG": ("rl/go2_rsl_rl/go2_rsl_rl.py", "GO2_VELOCITY_RSL_CFG"),
    "B2_VELOCITY_RSL_CFG": ("rl/b2_rsl_rl/b2_rsl_rl.py", "B2_VELOCITY_RSL_CFG"),
    "M20_ROUGH_RSL_CFG": ("rl/m20_rough_rsl/m20_rough_rsl.py", "M20_ROUGH_RSL_CFG"),
    "LITE3_VELOCITY_RSL_CFG": ("rl/lite3_rsl_rl/lite3_rsl_rl.py", "LITE3_VELOCITY_RSL_CFG"),
    "SCOUT_DIFF_CFG": ("traditional/scout_diff/scout_diff.py", "SCOUT_DIFF_CFG"),
    "MUSHR_ACKERMANN_CFG": ("traditional/mushr_ackermann/mushr_ackermann.py", "MUSHR_ACKERMANN_CFG"),
    "MUSHR_RWD_ACKERMANN_CFG": ("traditional/mushr_ackermann/mushr_ackermann.py", "MUSHR_RWD_ACKERMANN_CFG"),
    "COCO_ACKERMANN_CFG": ("traditional/coco_ackermann/coco_ackermann.py", "COCO_ACKERMANN_CFG"),
    "G1_SKRL_CFG": ("rl/g1_skrl/g1_skrl.py", "G1_SKRL_CFG"),
    "QUADCOPTER_GOAL_SKRL_CFG": ("rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py", "QUADCOPTER_GOAL_SKRL_CFG"),
    "HUMAN_ANIMATION_CFG": ("traditional/human_animation/human_animation.py", "HUMAN_ANIMATION_CFG"),
    "UR5_IK_CFG": ("traditional/ur5_ik/ur5_ik.py", "UR5_IK_CFG"),
    "Z1_IK_CFG": ("traditional/z1_ik/z1_ik.py", "Z1_IK_CFG"),
}


ROBOT_OPTIONS = [
    RobotOption(
        "carter",
        "Carter differential base",
        CARTER_CFG,
        "CARTER_DIFF_CFG",
        0.2,
        "Carter/GS_Hub_chassis_link",
        z1_mount_profile=Z1_MOUNT_PROFILES["carter"],
        gshub_offset=(0.026, 0.0, 0.418),
        lidar_mount_link="Carter/GS_Hub_chassis_link",
        lidar_offset=(0.026, 0.0, 0.418),
    ),
    RobotOption("pepper", "Pepper holonomic base", PEPPER_CFG, "PEPPER_HOLONOMIC_CFG", 0.0),
    RobotOption(
        "go2",
        "Unitree Go2",
        GO2_CFG,
        "GO2_VELOCITY_RSL_CFG",
        0.4,
        "base",
        ur5_mount_profile=UR5_MOUNT_PROFILES["go2"],
        z1_mount_profile=Z1_MOUNT_PROFILES["go2"],
        lidar_mount_link="base",
        lidar_offset=(0.22631, -0.003, 0.13055),
        lidar_rot=(1.0, 0.0, 0.0, 0.0),
    ),
    RobotOption(
        "b2",
        "Unitree B2",
        B2_CFG,
        "B2_VELOCITY_RSL_CFG",
        0.58,
        "base_link",
        ur5_mount_profile=UR5_MOUNT_PROFILES["b2"],
        z1_mount_profile=Z1_MOUNT_PROFILES["b2"],
        gshub_offset=(0.36723, 0.0, 0.223494),
        lidar_mount_link="base_link",
        lidar_offset=(0.0, 0.0, 0.5),
    ),
    RobotOption(
        "m20",
        "DeepRobotics M20",
        DEEPROBOTICS_M20_CFG,
        "M20_ROUGH_RSL_CFG",
        0.52,
        gshub_mount_link="base_link",
        ur5_mount_profile=UR5_MOUNT_PROFILES["m20"],
        z1_mount_profile=Z1_MOUNT_PROFILES["m20"],
        gshub_offset=(0.29718, 0.0, 0.07994),
        lidar_mount_link="base_link",
        lidar_offset=(0.0, 0.0, 0.5),
    ),
    RobotOption(
        "scout",
        "Scout mobile base",
        SCOUT_CFG,
        "SCOUT_DIFF_CFG",
        0.2,
        gshub_mount_link="base_link",
        ur5_mount_profile=UR5_MOUNT_PROFILES["scout"],
        z1_mount_profile=Z1_MOUNT_PROFILES["scout"],
        gshub_offset=(0.24749, 0.0, 0.11309),
    ),
    RobotOption(
        "mushr_v2",
        "MuSHR Nano v2 Ackermann base",
        MUSHR_V2_CFG,
        "MUSHR_ACKERMANN_CFG",
        0.0,
        gshub_mount_link="mushr_nano/base_link",
        lidar_mount_link="mushr_nano/laser_link",
    ),
    RobotOption("coco", "Coco AIRS Ackermann base", COCO_CFG, "COCO_ACKERMANN_CFG", 0.3),
    RobotOption("g1", "Unitree G1", G1_CFG, "G1_SKRL_CFG", 0.74),
    RobotOption("cf2x", "Crazyflie CF2X", CRAZYFLIE_CFG, "QUADCOPTER_GOAL_SKRL_CFG", 1.0),
    RobotOption("human", "Human animation", None, "HUMAN_ANIMATION_CFG", HUMAN_FEMALE_DEFAULT_Z),
    RobotOption(
        "lite3",
        "DeepRobotics Lite3",
        DEEPROBOTICS_LITE3_CFG,
        "LITE3_VELOCITY_RSL_CFG",
        0.35,
        gshub_mount_link="TORSO",
        ur5_mount_profile=UR5_MOUNT_PROFILES["lite3"],
        z1_mount_profile=Z1_MOUNT_PROFILES["lite3"],
        gshub_offset=(0.16669, 0.0, 0.06773),
        lidar_mount_link="TORSO",
        lidar_offset=(0.0, 0.0, 0.35),
    ),
]


def choose_interactive_env_cfg() -> tuple[str, MultiRobotDirectEnvCfg]:
    selection = choose_interactive_selection()
    return "EAI-Interactive-v0", build_interactive_env_cfg_from_selection(selection)


def choose_interactive_selection(
    *,
    initial_selection: InteractiveSelection | None = None,
    allow_back_from_first: bool = False,
    start_step: int = 0,
) -> InteractiveSelection | None:
    return choose_terminal_interactive_selection(
        initial_selection=initial_selection,
        allow_back_from_first=allow_back_from_first,
        start_step=start_step,
    )


def interactive_selection_from_dict(data: dict[str, Any]) -> InteractiveSelection:
    if "robots" in data:
        return _flow_selection_from_dict(data)

    robots: list[RobotSelection] = []
    for key, count in data["robot_counts"]:
        for _index in range(int(count)):
            attachments: list[AttachmentSelection] = []
            if bool(data.get("use_gshub")) and attachment_supported(str(key), "gshub"):
                attachments.append(AttachmentSelection("gshub", None))
            if bool(data.get("use_ur5")) and attachment_supported(str(key), "ur5"):
                attachments.append(AttachmentSelection("ur5", ControllerChoice("default", "UR5_IK_CFG")))
            if bool(data.get("use_lidar")) and attachment_supported(str(key), "lidar"):
                attachments.append(AttachmentSelection("lidar", None))
            robots.append(
                RobotSelection(
                    type=str(key),
                    controller=ControllerChoice("default", default_controller_cfg(str(key))),
                    visual={},
                    attachments=tuple(attachments),
                )
            )
    return InteractiveSelection(scene_key=str(data["scene_key"]), robots=tuple(robots))


def build_interactive_env_cfg_from_selection(selection: InteractiveSelection) -> MultiRobotDirectEnvCfg:
    scene_by_key = {scene.key: scene for scene in SCENE_OPTIONS}
    robot_by_key = {robot.key: robot for robot in ROBOT_OPTIONS}
    if selection.scene_key not in scene_by_key:
        raise ValueError(
            f"DIY scene '{selection.scene_key}' is visual-only or not yet connected to interactive simulation."
        )
    scene = scene_by_key[selection.scene_key]
    unsupported = sorted({item.type for item in selection.robots if item.type not in robot_by_key})
    if unsupported:
        raise ValueError(
            "These DIY robots are visual-only or not yet connected to interactive simulation: "
            + ", ".join(unsupported)
        )
    return build_interactive_env_cfg(
        scene,
        [(robot_by_key[item.type], item) for item in selection.robots],
    )


def _interactive_after_reset_idx_hook(env, env_ids) -> None:
    """Clear native manipulator commands and IK smoothing state on reset."""
    try:
        from EAI.hmrs_ros.manipulator_omnigraph import get_manipulator_graph_manager
        from EAI.hmrs_ros.ur5_omnigraph import get_ur5_graph_manager

        reset_ur5_ik_command_smoothers = load_controller_attr(
            "traditional/ur5_ik/ur5_ik.py",
            "reset_ur5_ik_command_smoothers",
        )

        manager = get_ur5_graph_manager(env)
        if manager is not None:
            manager.clear()
        manipulator_manager = get_manipulator_graph_manager(env)
        if manipulator_manager is not None:
            manipulator_manager.clear()
        reset_ur5_ik_command_smoothers(env)
    except Exception:
        pass


def build_interactive_env_cfg(
    scene: SceneOption,
    robot_selections: list[tuple[RobotOption, RobotSelection]],
) -> MultiRobotDirectEnvCfg:
    attrs: dict[str, Any] = {"__annotations__": {}}
    controllers: dict[str, Any] = {}
    scene_spawn_cfg = _resolve_import_ref(scene.spawn_cfg)

    if scene_spawn_cfg is None:
        attrs["__annotations__"]["terrain"] = TerrainImporterCfg
        attrs["terrain"] = TerrainImporterCfg(
            prim_path="/World/ground",
            terrain_type="plane",
            collision_group=-1,
            physics_material=sim_utils.RigidBodyMaterialCfg(
                friction_combine_mode="multiply",
                restitution_combine_mode="multiply",
                static_friction=1.0,
                dynamic_friction=1.0,
                restitution=0.0,
            ),
            debug_vis=False,
        )
    else:
        attrs["__annotations__"]["world"] = AssetBaseCfg
        attrs["world"] = AssetBaseCfg(
            prim_path=scene.prim_path,
            spawn=scene_spawn_cfg,
            init_state=AssetBaseCfg.InitialStateCfg(pos=scene.spawn_pos),
        )

    attrs["__annotations__"]["dome_light"] = AssetBaseCfg
    attrs["dome_light"] = AssetBaseCfg(
        prim_path="/World/DomeLight",
        spawn=sim_utils.DomeLightCfg(intensity=2000.0, color=(0.75, 0.75, 0.75)),
    )

    spawn_index = 0
    robot_type_counts: dict[str, int] = {}
    uses_manipulator = False
    for robot, selection in robot_selections:
        spawn_index += 1
        robot_type_counts[robot.key] = robot_type_counts.get(robot.key, 0) + 1
        name = f"{robot.key}_{robot_type_counts[robot.key]}"
        x, y = _spawn_xy(spawn_index, origin=scene.robot_spawn_origin)
        position, rotation = _selection_spawn_pose(
            selection,
            default_position=(x, y, robot.default_z),
            default_rotation=HUMAN_INITIAL_ROT if robot.key == "human" else (1.0, 0.0, 0.0, 0.0),
        )
        if robot.key == "human":
            _add_human(attrs, controllers, name, position, rotation, robot, selection)
            continue

        has_ur5 = any(attachment.type == "ur5" for attachment in selection.attachments)
        has_z1 = any(attachment.type == "z1" for attachment in selection.attachments)
        if has_ur5 and has_z1:
            raise ValueError(f"DIY robot '{name}' cannot attach both UR5 and Z1.")
        uses_manipulator = uses_manipulator or has_ur5 or has_z1
        if robot.cfg is None:
            raise ValueError(f"DIY robot '{robot.key}' does not have an articulation cfg.")
        if has_ur5 and robot.ur5_mount_profile is None:
            raise ValueError(f"DIY robot '{robot.key}' does not define a UR5 mount profile.")
        if has_z1 and robot.z1_mount_profile is None:
            raise ValueError(f"DIY robot '{robot.key}' does not define a Z1 mount profile.")
        selected_robot_cfg = robot.cfg
        if robot.key == "mushr_v2" and selection.controller.cfg == "MUSHR_RWD_ACKERMANN_CFG":
            selected_robot_cfg = MUSHR_V2_RWD_CFG
        base_cfg = (
            build_ur5_host_cfg(selected_robot_cfg, robot.ur5_mount_profile)
            if has_ur5 and robot.ur5_mount_profile is not None
            else selected_robot_cfg
        )
        articulation = base_cfg.replace(prim_path=f"{{ENV_REGEX_NS}}/{name}")
        articulation.init_state.pos = position
        articulation.init_state.rot = rotation
        attrs["__annotations__"][name] = ArticulationCfg
        attrs[name] = articulation

        base_controller = _resolve_controller(selection.controller, fallback=robot.controller)
        robot_controllers: list[Any] = [base_controller]

        if has_ur5 and robot.ur5_mount_profile is not None:
            attrs["__annotations__"][f"{name}_arm"] = ArticulationCfg
            attrs[f"{name}_arm"] = build_mounted_ur5_asset_cfg(
                f"{{ENV_REGEX_NS}}/{name}_arm",
                robot.ur5_mount_profile,
            )
            ur5_attachment = next(attachment for attachment in selection.attachments if attachment.type == "ur5")
            robot_controllers.append(_resolve_controller(ur5_attachment.controller, fallback="UR5_IK_CFG"))
        elif has_z1 and robot.z1_mount_profile is not None:
            attrs["__annotations__"][f"{name}_arm"] = ArticulationCfg
            attrs[f"{name}_arm"] = build_mounted_z1_asset_cfg(
                f"{{ENV_REGEX_NS}}/{name}_arm",
                robot.z1_mount_profile,
            )
            z1_attachment = next(attachment for attachment in selection.attachments if attachment.type == "z1")
            robot_controllers.append(_resolve_controller(z1_attachment.controller, fallback="Z1_IK_CFG"))

        controllers[name] = tuple(robot_controllers) if len(robot_controllers) > 1 else robot_controllers[0]

        # GSHub spawn：检查是否有 gshub 硬件 + 是否开启 ROS 通道
        has_gshub = any(attachment.type == "gshub" for attachment in selection.attachments)
        ros_enabled = any(attachment.type == "ros" for attachment in selection.attachments)
        cmd_vel_enabled = any(attachment.type in {"ros", "keyboard"} for attachment in selection.attachments)

        if has_gshub and robot.gshub_mount_link:
            gshub_prim_path = f"{{ENV_REGEX_NS}}/{name}/{robot.gshub_mount_link}/GSHub"

            # 通过 custom attribute 传递配置（spawn 后立即设置）
            # 这比全局字典更可靠，因为属性直接附加在 prim 上
            attrs["__annotations__"][f"gs_hub_{name}"] = AssetBaseCfg
            attrs[f"gs_hub_{name}"] = GSHubCfg(
                prim_path=gshub_prim_path,
                init_state=AssetBaseCfg.InitialStateCfg(pos=robot.gshub_offset),
                ros_namespace=f"/{name}",
                enable_ros_publish=ros_enabled,  # 传给 GSHubCfg（虽然 spawn 时用不上，但保留向后兼容）
            )

            # 同时写入全局字典作为备用（兼容旧代码）
            from EAI_assets.sensor.high_sensor.gs_hub import _gshub_ros_publish_config
            # 尝试多种可能的路径格式
            for path_variant in [
                gshub_prim_path,
                f"/World/envs/env_0/{name}/{robot.gshub_mount_link}/GSHub",
                f"/World/envs/env_./{name}/{robot.gshub_mount_link}/GSHub",
            ]:
                _gshub_ros_publish_config[path_variant] = ros_enabled

        if any(attachment.type == "lidar" for attachment in selection.attachments) and robot.lidar_mount_link:
            from EAI_assets.sensor.low_sensor import RosLidarCfg

            attrs["__annotations__"][f"lidar_{name}"] = AssetBaseCfg
            attrs[f"lidar_{name}"] = RosLidarCfg(
                prim_path=f"{{ENV_REGEX_NS}}/{name}/{robot.lidar_mount_link}/Lidar",
                init_state=AssetBaseCfg.InitialStateCfg(pos=robot.lidar_offset, rot=robot.lidar_rot),
                ros_namespace=f"/{name}",
            )

    scene_cls = configclass(type("InteractiveEaiSceneCfg", (InteractiveSceneCfg,), attrs))
    env_attrs = {
        "__annotations__": {"scene": scene_cls},
        "scene": scene_cls(num_envs=1, env_spacing=2.0),
        "controllers": controllers,
    }
    env_cls = configclass(type("InteractiveEaiEnvCfg", (MultiRobotDirectEnvCfg,), env_attrs))
    InteractiveEaiEnvCfg = env_cls
    env_cfg = InteractiveEaiEnvCfg()
    env_cfg.task_name = "EAI-Interactive-v0"
    if uses_manipulator:
        env_cfg.after_reset_idx_hook = _interactive_after_reset_idx_hook
    return env_cfg


def _add_human(
    attrs: dict[str, Any],
    controllers: dict[str, Any],
    name: str,
    position: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
    robot: RobotOption,
    selection: RobotSelection,
) -> None:
    unsupported = [attachment.type for attachment in selection.attachments if attachment.type not in {"keyboard", "ros"}]
    if unsupported:
        raise ValueError(
            "Human animation robots only support non-physical DIY tools: "
            + ", ".join(sorted(set(unsupported)))
        )

    rig_path = f"{{ENV_REGEX_NS}}/{name}_Rig"
    rig_attr = f"{name}_rig"
    idle_attr = f"{name}_idle"
    walk_attr = f"{name}_walk"

    attrs["__annotations__"][rig_attr] = AssetBaseCfg
    rig_cfg = HUMAN_FEMALE_RIG_CFG.replace(prim_path=rig_path)
    rig_cfg.init_state.pos = position
    rig_cfg.init_state.rot = rotation
    attrs[rig_attr] = rig_cfg

    attrs["__annotations__"][idle_attr] = AssetBaseCfg
    idle_cfg = HUMAN_FEMALE_IDLE_CFG.replace(prim_path=f"{rig_path}/Idle")
    idle_cfg.init_state.pos = (0.0, 0.0, 0.0)
    attrs[idle_attr] = idle_cfg

    attrs["__annotations__"][walk_attr] = AssetBaseCfg
    walk_cfg = HUMAN_FEMALE_WALK_CFG.replace(prim_path=f"{rig_path}/Walk")
    walk_cfg.init_state.pos = (0.0, 0.0, 0.0)
    attrs[walk_attr] = walk_cfg

    controllers[name] = _resolve_human_controller(selection.controller, name, rig_path, position)


def _selection_spawn_pose(
    selection: RobotSelection,
    *,
    default_position: tuple[float, float, float],
    default_rotation: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    spawn_pose = selection.spawn_pose or {}
    position = tuple(spawn_pose.get("position", default_position))
    rotation = tuple(spawn_pose.get("rotation", default_rotation))
    return position, rotation


def _resolve_human_controller(
    choice: ControllerChoice | None,
    name: str,
    rig_path: str,
    initial_position: tuple[float, float, float],
) -> Any:
    cfg_name = choice.cfg if choice is not None else None
    if cfg_name not in (None, "manual", "HUMAN_ANIMATION_CFG"):
        raise ValueError(
            f"Human animation robot '{name}' requires HUMAN_ANIMATION_CFG, got '{cfg_name}'."
        )
    controller_cls = _controller_attr_by_name("HUMAN_ANIMATION_CFG", "HumanAnimationControllerCfg")
    return controller_cls(
        rig_prim_path=rig_path,
        idle_prim_path=f"{rig_path}/Idle",
        walk_prim_path=f"{rig_path}/Walk",
        walk_animation_prim_path=f"{rig_path}/Walk/SkelAnim",
        initial_position=initial_position,
    )


def _resolve_controller(choice: ControllerChoice | None, *, fallback: Any) -> Any:
    if choice is None or not choice.cfg or choice.cfg == "manual":
        return _controller_cfg_by_name(fallback) if isinstance(fallback, str) else fallback
    return _controller_cfg_by_name(choice.cfg)


def _controller_cfg_by_name(name: str) -> Any:
    return _controller_attr_by_name(name)


def _controller_attr_by_name(name: str, attr_override: str | None = None) -> Any:
    if name not in CONTROLLER_CFG_IMPORTS:
        raise ValueError(f"Unknown controller cfg '{name}'. Available: {', '.join(CONTROLLER_CFG_IMPORTS)}")
    relative_path, attr_name = CONTROLLER_CFG_IMPORTS[name]
    return load_controller_attr(relative_path, attr_override or attr_name)


def _resolve_import_ref(ref: Any | tuple[str, str] | None) -> Any | None:
    if ref is None:
        return None
    if isinstance(ref, tuple) and len(ref) == 2 and all(isinstance(item, str) for item in ref):
        module_name, attr_name = ref
        return getattr(importlib.import_module(module_name), attr_name)
    return ref


def _spawn_xy(index: int, *, origin: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
    row = (index - 1) // 4
    col = (index - 1) % 4
    return (origin[0] + (col - 1.5) * 2.0, origin[1] + row * -2.0)
