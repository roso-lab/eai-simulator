"""Public EAI skill registry.

Only capabilities with a concrete provider and EAI execution route are listed.
G1 box transport is intentionally absent because it is not supported.
"""

from __future__ import annotations

from typing import Any


SKILL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "eai.navigation.ground_navigation",
        "provider_id": "ground_navigation.provider",
        "provider_version": "1.0",
        "kind": "navigation",
        "role": "authored_node_heading_pid",
        "module": "algorithm.skills.navigation.ground_navigation",
        "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(target_ref | target_pose, intent, world_state_seq)",
        "output_contract": "SkillPlan(base_velocity=[v, 0, wz])",
        "executor_owner": "task.executors.ground_navigation.GroundNavigationExecutor",
        "evaluator_owner": "external evidence evaluator",
        "status": "implemented_generic_executor",
    },
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
    {
        "skill_id": "eai.transport.composite",
        "provider_id": "eai.pepper.box_transport",
        "provider_version": "1.0.0",
        "kind": "transport",
        "robot_type": "Pepper",
        "controller_cfg": "PEPPER_BOX_TRANSPORT_CFG",
        "module": "algorithm.skills.pepper_transport",
        "schema_version": "simulator.skill.v1",
        "executor_owner": "EAI runtime",
        "evaluator_owner": "external evidence evaluator",
        "status": "implemented_pepper_only",
    },
)


def skill_records() -> list[dict[str, Any]]:
    return [dict(item) for item in SKILL_REGISTRY]


__all__ = ["SKILL_REGISTRY", "skill_records"]
