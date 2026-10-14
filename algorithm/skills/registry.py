"""Public EAI skill registry.

Only capabilities with a concrete provider and EAI execution route are listed.
Local manipulation binds host-created adapters by robot_id. Navigation
remains caller-owned and complete-transfer skills are not registered.
"""

from __future__ import annotations

from typing import Any


SKILL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "eai.manipulation.pick_place",
        "provider_id": "eai.pick_place", "provider_version": "1.0.0",
        "kind": "manipulation", "role": "local_pick_or_place",
        "module": "algorithm.skills.pick_place", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(operation, object_ref, chassis world pose, object/placement world XYZ)",
        "output_contract": "bool after physical completion; caller owns navigation",
        "executor_owner": "task.executors.pick_place.PickPlaceExecutor",
        "backends": ("ArmPickPlaceAdapter", "DronePickPlaceAdapter", "PepperPickPlaceAdapter", "G1PickPlaceAdapter"),
        "status": "implemented_registered_backends",
    },
    {
        "skill_id": "eai.navigation.aerial_navigation",
        "provider_id": "aerial_navigation.provider", "provider_version": "1.0",
        "kind": "navigation", "role": "authored_xyz_nodes",
        "module": "algorithm.skills.navigation.aerial_navigation", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(target_ref | XYZ target_pose, intent, world_state_seq)",
        "output_contract": "SkillPlan(goal_position)",
        "executor_owner": "task.executors.aerial_runtime.AerialSkillRuntime",
        "evaluator_owner": "external evidence evaluator", "status": "implemented_shared_tick_binding",
    },
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
        "module": "algorithm.skills.navigation.provider",
        "input_contract": "SkillRequest(target_pose), WorldSnapshot, injected planner/map/profile",
        "output_contract": "SkillPlan(navigate_waypoints)",
        "executor_owner": "task.executors -> EAI navigation controller",
        "status": "implemented_requires_injected_planner_and_runtime_binding",
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
    return [dict(item) for item in SKILL_REGISTRY]


__all__ = ["SKILL_REGISTRY", "skill_records"]
