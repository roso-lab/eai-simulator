"""Classification of existing motion/control modules as benchmark skills."""

from __future__ import annotations

from typing import Any


SKILL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "global_2d_navigation",
        "kind": "navigation",
        "role": "single_robot_path_and_velocity",
        "module": "algorithm.skills.navigation.global_planner",
        "input_contract": "SkillRequest(target_pose, map, robot_pose, kinematics)",
        "output_contract": "SkillPlan(waypoints, velocity_command)",
        "executor_owner": "task.executors -> EAI navigation controller",
        "status": "provider_pending_dedicated_skill_developer",
    },
    {
        "skill_id": "db_cbs_multi_robot_navigation",
        "kind": "navigation",
        "role": "multi_robot_collision_free_trajectory",
        "module": "algorithm.skills.navigation.multi_robot_navigation",
        "input_contract": "SkillRequest(batch goals, occupancy map, robot radii)",
        "output_contract": "SkillPlan(synchronized trajectories)",
        "executor_owner": "task.executors -> EAI controller loop",
        "status": "provider_pending_dedicated_skill_developer",
    },
    {
        "skill_id": "nav2_navigation_io",
        "kind": "external_io",
        "role": "system_ros_navigation_goal_transport",
        "module": "algorithm.skills.external_io.nav2",
        "input_contract": "SkillRequest(target_pose, ROS/Nav2 profile)",
        "output_contract": "SkillCommand(nav2_goal / cmd_vel transport)",
        "executor_owner": "system ROS2 process + EAI cmd_vel bridge",
        "status": "provider_pending_dedicated_skill_developer",
    },
    {
        "skill_id": "keyboard_velocity_io",
        "kind": "external_io",
        "role": "manual_velocity_command_transport",
        "module": "algorithm.skills.external_io.keyboard",
        "input_contract": "operator velocity event",
        "output_contract": "SkillCommand(ROS Twist)",
        "executor_owner": "system ROS2 process + EAI cmd_vel bridge",
        "status": "provider_pending_dedicated_skill_developer",
    },
    {
        "skill_id": "emos_internal_navigation_skill",
        "kind": "navigation",
        "role": "legacy_robot_specific_skill_runtime",
        "module": "algorithm.system.coordination.emos.brain.skills",
        "input_contract": "legacy EMOS skill target + EAI env",
        "output_contract": "legacy torch command",
        "executor_owner": "migration target: task.executors -> EAI controller",
        "status": "internal_emos_runtime_not_standalone_system_algorithm",
    },
)


def skill_records() -> list[dict[str, Any]]:
    """Return a copy suitable for manifests and handoff tooling."""

    return [dict(item) for item in SKILL_REGISTRY]
