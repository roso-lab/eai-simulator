"""
Robot Resume Generator for Isaac Lab.

Generates capability descriptions for robots that can be used
by LLM agents for task planning and coordination.

对应 EMOS 中的:
- habitat_mas/agents/capabilities/parse_urdf.py
- habitat_mas/agents/capabilities/robot_resume.py
"""

import json
from typing import Any, Dict, List, Optional
import numpy as np


# 工厂场景专用：仅保留
# - carter_1：Carter + Orsus 传感器（无机械臂）
# - scout_1：Scout 差速移动底盘 + 背部 UR5 机械臂
# - m20_1：M20 四足轮腿 + 背部 UR5 机械臂（主要负责操作任务）
# - m20_2：M20 四足轮腿 + 背部 UR5 机械臂（主要负责协同搬运/抓取）
ROBOT_DESCRIPTIONS = {
    "carter_1": {
        "type": "CarterOrsusRobot",
        "description": (
            "Carter differential drive robot equipped with a Orsus high-precision sensor "
            "(Lidar and Odometry via ROS2 bridge). It is specialized for indoor navigation, "
            "mapping, and long-range perception, but it does not have a manipulation arm."
        ),
    },
    "scout_1": {
        "type": "ScoutUR5Robot",
        "description": (
            "Scout differential-drive mobile base with a UR5 6-DOF arm mounted on top. "
            "It is suitable for indoor flat-floor navigation and mobile manipulation tasks "
            "such as reaching to a console or interacting with objects around the base."
        ),
    },
    "m20_1": {
        "type": "M20FrankaRobot",
        "description": (
            "M20 wheeled quadruped robot with hybrid wheel-leg locomotion and a UR5 6-DOF arm "
            "mounted on the back. It is designed for rough terrain navigation and manipulation "
            "tasks such as pressing buttons or operating devices in constrained areas."
        ),
    },
    "m20_2": {
        "type": "M20Robot",
        "description": (
            "M20 wheeled quadruped robot with hybrid wheel-leg locomotion and a UR5 6-DOF arm "
            "mounted on the back. Compared to M20_1 it is typically used as an assistant "
            "manipulator or for pick-and-place style tasks in rough indoor factory terrain."
        ),
    },
}


def infer_robot_type(robot_name: str) -> str:
    """Infer robot type from robot name. Exact match first (e.g. carter_1, m20_1) for factory variants."""
    robot_name_lower = robot_name.lower()
    # Exact match first for factory-specific types
    if robot_name_lower in ROBOT_DESCRIPTIONS:
        return ROBOT_DESCRIPTIONS[robot_name_lower]["type"]
    for key in ROBOT_DESCRIPTIONS:
        if key in robot_name_lower:
            return ROBOT_DESCRIPTIONS[key]["type"]
    return "UnknownRobot"


def get_robot_description(robot_name: str) -> str:
    """Get default description for a robot type. Exact match first for factory variants."""
    robot_name_lower = robot_name.lower()
    if robot_name_lower in ROBOT_DESCRIPTIONS:
        return ROBOT_DESCRIPTIONS[robot_name_lower]["description"]
    for key in ROBOT_DESCRIPTIONS:
        if key in robot_name_lower:
            return ROBOT_DESCRIPTIONS[key]["description"]
    return f"Unknown robot type: {robot_name}"


def extract_mobility_capabilities(
    robot: Any,
    controller_cfg: Any = None,
    robot_name: str = ""
) -> Dict[str, Any]:
    """
    Extract mobility capabilities from robot and controller configuration.
    
    Args:
        robot: Isaac Lab robot articulation
        controller_cfg: Controller configuration
        robot_name: Name of the robot
        
    Returns:
        Dictionary describing mobility capabilities
    """
    robot_name_lower = robot_name.lower()
    
    # Default mobility description based on robot type
    mobility = {
        "summary": "Unknown mobility",
        "type": "unknown",
        "max_speed": 0.0,
        "can_climb_stairs": False,
        "can_fly": False,
        "terrain_types": ["flat"],
    }
    
    # 工厂场景：carter_1 / scout_1 差速，m20_1 / m20_2 四足轮腿
    if robot_name_lower in ("carter_1", "scout_1"):
        mobility.update({
            "summary": "Differential drive wheeled base, smooth indoor navigation",
            "type": "wheeled_differential",
            "max_speed": 1.0,
            "can_climb_stairs": False,
            "can_fly": False,
            "terrain_types": ["flat", "indoor"],
        })
    elif robot_name_lower in ("m20_1", "m20_2"):
        mobility.update({
            "summary": "Hybrid wheeled-legged locomotion for rough terrain",
            "type": "wheeled_quadruped",
            "max_speed": 1.2,
            "can_climb_stairs": True,
            "can_fly": False,
            "terrain_types": ["flat", "rough", "stairs", "slopes"],
        })

    # Try to extract from controller config if available
    if controller_cfg is not None:
        if hasattr(controller_cfg, 'max_speed'):
            mobility["max_speed"] = controller_cfg.max_speed
    
    return mobility


def extract_perception_capabilities(
    robot: Any,
    robot_name: str = ""
) -> Dict[str, Any]:
    """
    Extract perception capabilities from robot.
    
    Args:
        robot: Isaac Lab robot articulation
        robot_name: Name of the robot
        
    Returns:
        Dictionary describing perception capabilities
    """
    robot_name_lower = robot_name.lower()
    
    perception = {
        "summary": "Standard perception sensors",
        "note": "max_range is sensor detection range only, NOT a navigation distance limit. Robot can navigate to any reachable position using path planning.",
        "sensors": [],
        "camera_height": 0.5,
        "hfov": 90.0,  # Horizontal field of view
        "vfov": 60.0,  # Vertical field of view
        "max_range": 10.0,  # Sensor detection range, NOT navigation limit
    }
    
    # 工厂场景：carter_1 带 Orsus，scout_1 为 Scout，m20_1 / m20_2 前向相机
    if robot_name_lower == "carter_1":
        perception.update({
            "summary": "Orsus high-precision sensor: Lidar and Odometry (ROS2 bridge), suitable for indoor mapping and navigation",
            "sensors": ["lidar", "odometry", "orsus"],
            "camera_height": 0.418,  # Orsus 相对底盘安装高度
            "hfov": 360.0,  # Lidar 环扫
            "vfov": 30.0,
            "max_range": 15.0,
            "ros2_bridge": True,
        })
    elif robot_name_lower == "scout_1":
        perception.update({
            "summary": "Forward-facing RGBD camera and LIDAR",
            "sensors": ["rgb_camera", "depth_camera", "lidar"],
            "camera_height": 0.6,
            "hfov": 90.0,
            "max_range": 15.0,
        })
    elif robot_name_lower in ("m20_1", "m20_2"):
        perception.update({
            "summary": "Forward-facing cameras with wide field of view for terrain navigation",
            "sensors": ["rgb_camera", "depth_camera", "imu", "lidar"],
            "camera_height": 0.45,  # Body height
            "hfov": 100.0,
            "vfov": 70.0,
            "max_range": 15.0,
            "terrain_sensing": True,
        })

    return perception


def extract_manipulation_capabilities(
    robot: Any,
    robot_name: str = ""
) -> Dict[str, Any]:
    """
    Extract manipulation capabilities from robot.
    
    Args:
        robot: Isaac Lab robot articulation
        robot_name: Name of the robot
        
    Returns:
        Dictionary describing manipulation capabilities
    """
    robot_name_lower = robot_name.lower()
    
    manipulation = {
        "summary": "No manipulation capability",
        "has_arm": False,
        "arm_dof": 0,
        "has_gripper": False,
        "workspace": None,
        "max_payload": 0.0,
    }
    
    # 工厂场景：m20_1 / m20_2 / scout_1 均带背部 UR5 机械臂，carter_1 无操作臂
    if robot_name_lower == "m20_1":
        manipulation.update({
            "summary": "UR5 6-DOF arm mounted on M20 back for manipulation in rough terrain",
            "has_arm": True,
            "arm_dof": 6,
            "num_arms": 1,
            "has_gripper": True,
            "gripper_type": "parallel",
            "arm_mount": "back",
            "workspace": {
                "type": "sphere",
                "center": [0.0, 0.0, 0.64],  # Relative to M20 base (~0.52 + 0.12)
                "radius": 0.855,
            },
            "max_payload": 3.0,  # kg (UR5 typical payload)
            "max_reach_height": 1.2,
            "min_reach_height": 0.3,
        })
    elif robot_name_lower == "m20_2":
        manipulation.update({
            "summary": "UR5 6-DOF arm mounted on M20 back, mainly used for assisted pick-and-place in rough terrain",
            "has_arm": True,
            "arm_dof": 6,
            "num_arms": 1,
            "has_gripper": True,
            "gripper_type": "parallel",
            "arm_mount": "back",
            "workspace": {
                "type": "sphere",
                "center": [0.0, 0.0, 0.64],
                "radius": 0.85,
            },
            "max_payload": 3.0,
            "max_reach_height": 1.2,
            "min_reach_height": 0.3,
        })
    elif robot_name_lower == "scout_1":
        manipulation.update({
            "summary": "UR5 6-DOF arm mounted on Scout mobile base for indoor mobile manipulation",
            "has_arm": True,
            "arm_dof": 6,
            "num_arms": 1,
            "has_gripper": True,
            "gripper_type": "parallel",
            "arm_mount": "top",
            "workspace": {
                "type": "sphere",
                # 车体略低于 0.3m，机械臂基座高度约 0.6-0.7m
                "center": [0.0, 0.0, 0.7],
                "radius": 0.9,
            },
            "max_payload": 3.0,
            "max_reach_height": 1.4,
            "min_reach_height": 0.2,
        })

    return manipulation


def generate_robot_resume(
    env: Any,
    robot_name: str,
    include_numerical: bool = True
) -> Dict[str, Any]:
    """
    Generate a complete robot resume for LLM task planning.
    
    Args:
        env: Isaac Lab environment instance
        robot_name: Name of the robot
        include_numerical: Whether to include numerical values (for ablation)
        
    Returns:
        Dictionary containing robot capabilities suitable for LLM consumption
    """
    # Get robot and controller
    robot = None
    controller_cfg = None
    
    if hasattr(env, 'scene') and hasattr(env.scene, 'articulations'):
        robot = env.scene.articulations.get(robot_name)
    
    if hasattr(env, 'cfg') and hasattr(env.cfg, 'controllers'):
        controller_cfg = env.cfg.controllers.get(robot_name)
    
    # Build resume
    resume = {
        "robot_type": infer_robot_type(robot_name),
        "robot_name": robot_name,
        "description": get_robot_description(robot_name),
    }
    
    # Extract capabilities
    mobility = extract_mobility_capabilities(robot, controller_cfg, robot_name)
    perception = extract_perception_capabilities(robot, robot_name)
    manipulation = extract_manipulation_capabilities(robot, robot_name)
    
    if include_numerical:
        resume["mobility"] = mobility
        resume["perception"] = perception
        resume["manipulation"] = manipulation
    else:
        # Only include summary (for ablation study)
        resume["mobility"] = {"summary": mobility["summary"]}
        resume["perception"] = {"summary": perception["summary"]}
        resume["manipulation"] = {"summary": manipulation["summary"]}
    
    return resume


def generate_all_robot_resumes(
    env: Any,
    include_numerical: bool = True
) -> Dict[str, Dict[str, Any]]:
    """
    Generate resumes for all robots in the environment.
    
    Args:
        env: Isaac Lab environment instance
        include_numerical: Whether to include numerical values
        
    Returns:
        Dictionary mapping robot names to their resumes
    """
    resumes = {}
    
    if hasattr(env, 'cfg') and hasattr(env.cfg, 'possible_agents'):
        for robot_name in env.cfg.possible_agents:
            resumes[robot_name] = generate_robot_resume(
                env, robot_name, include_numerical
            )
    elif hasattr(env, 'scene') and hasattr(env.scene, 'articulations'):
        for robot_name in env.scene.articulations:
            resumes[robot_name] = generate_robot_resume(
                env, robot_name, include_numerical
            )
    
    return resumes


def format_resume_for_llm(resume: Dict[str, Any]) -> str:
    """
    Format a robot resume as a string for LLM consumption.
    
    Args:
        resume: Robot resume dictionary
        
    Returns:
        Formatted string description
    """
    return json.dumps(resume, indent=2)


def get_capabilities_text(resume: Dict[str, Any]) -> str:
    """
    Get a text description of robot capabilities for prompts.
    
    Args:
        resume: Robot resume dictionary
        
    Returns:
        Human-readable capability description
    """
    capabilities = f"Robot: {resume.get('robot_type', 'Unknown')}\n"
    
    if "mobility" in resume:
        mob = resume["mobility"]
        if isinstance(mob, dict):
            capabilities += f"Mobility: {mob.get('summary', 'Unknown')}\n"
            if "max_speed" in mob:
                capabilities += f"  - Max speed: {mob['max_speed']} m/s\n"
            if "can_fly" in mob:
                capabilities += f"  - Can fly: {mob['can_fly']}\n"
            if "can_climb_stairs" in mob:
                capabilities += f"  - Can climb stairs: {mob['can_climb_stairs']}\n"
    
    if "perception" in resume:
        per = resume["perception"]
        if isinstance(per, dict):
            capabilities += f"Perception: {per.get('summary', 'Unknown')}\n"
            if "sensors" in per:
                capabilities += f"  - Sensors: {', '.join(per['sensors'])}\n"
            if "camera_height" in per:
                capabilities += f"  - Camera height: {per['camera_height']} m\n"
    
    if "manipulation" in resume:
        man = resume["manipulation"]
        if isinstance(man, dict):
            capabilities += f"Manipulation: {man.get('summary', 'Unknown')}\n"
            if man.get("has_arm"):
                capabilities += f"  - Arm DOF: {man.get('arm_dof', 0)}\n"
                if "max_reach_height" in man:
                    capabilities += f"  - Max reach height: {man['max_reach_height']} m\n"
    
    return capabilities
