"""Public EAI skill registry.

Only capabilities with a concrete provider and EAI execution route are listed.
Unified manipulation binds arms, calibrated fixed-base G1 and Pepper transport.
"""

from __future__ import annotations

from typing import Any


SKILL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "eai.manipulation.pick_place",
        "provider_id": "pick_place.provider", "provider_version": "1.0",
        "kind": "manipulation", "role": "world_xyz_pick_place",
        "module": "algorithm.skills.pick_place", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(object_ref, pick_position XYZ, place_position XYZ, frame=world)",
        "output_contract": "SkillPlan(pick_place)",
        "executor_owner": "task.executors.manipulation.ManipulationSkillRuntime",
        "evaluator_owner": "external evidence evaluator",
        "status": "implemented_shared_tick_binding",
        "scope": "UR5/Z1 arms, calibrated fixed-base G1, configured Pepper transport; per-environment robot/object leases",
    },
    {
        "skill_id": "eai.manipulation.g1_pick_place",
        "provider_id": "g1_pick_place.provider", "provider_version": "1.0",
        "kind": "manipulation", "role": "fixed_base_world_xyz_pick_place",
        "module": "algorithm.skills.g1_pick_place", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(pick_position XYZ, place_position XYZ, optional object_ref, frame=world)",
        "output_contract": "SkillPlan(g1_pick_place)",
        "executor_owner": "task.executors.manipulation.ManipulationSkillRuntime",
        "evaluator_owner": "external evidence evaluator",
        "runtime_owner": "task.executors.g1_live.G1LiveHandle",
        "status": "implemented_shared_tick_binding",
        "scope": "fixed-base calibrated Inspire right-hand USD ideal attachments",
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
        "skill_id": "eai.transport.aerial_pick_place",
        "provider_id": "aerial_pick_place.provider", "provider_version": "1.0",
        "kind": "transport", "role": "single_drone_fixed_joint_transport",
        "module": "algorithm.skills.aerial_transport", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(object/source/destination refs, world anchors, robot_ids, intent, world_state_seq)",
        "output_contract": "transport_state + SkillPlan(current-phase goal_position / transport_mutation)",
        "executor_owner": "task.executors.aerial_runtime.AerialSkillRuntime / AerialPickPlaceExecutor",
        "evaluator_owner": "external evidence evaluator", "status": "implemented_generic_executor",
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
    {
        "skill_id": "eai.transport.composite",
        "provider_id": "eai.pepper.box_transport",
        "provider_version": "1.0.0",
        "kind": "transport",
        "robot_type": "Pepper",
        "controller_cfg": "PEPPER_BOX_TRANSPORT_CFG",
        "cooperative_controller_cfg": "PEPPER_BOX_TRANSPORT_TASK_CFG",
        "module": "algorithm.skills.pepper_transport",
        "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(object_ref, source_ref, destination_ref, carry_mode, optional horizon_ticks)",
        "output_contract": "SkillPlan(pepper_transport_advance)",
        "executor_owner": "algorithm.skills.pepper_transport.executor.PepperTransportExecutorAdapter",
        "runtime_owner": "algorithm.skills.pepper_transport.runtime.PepperTransportHandle",
        "evaluator_owner": "algorithm.skills.pepper_transport.evaluator.PepperTransportEvidenceEvaluator",
        "scope": "Pepper box transport with bilateral ideal attachment and caller-owned physics stepping",
        "status": "implemented_pepper_only",
    },
)


def skill_records() -> list[dict[str, Any]]:
    return [dict(item) for item in SKILL_REGISTRY]


__all__ = ["SKILL_REGISTRY", "skill_records"]
