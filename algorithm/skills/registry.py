"""Public EAI Skill registry.

Navigation has one canonical public Skill.  Controller-specific command
formats are registered below the Skill boundary by ``NavigationExecutor``.
"""
from __future__ import annotations

from typing import Any


SKILL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "eai.manipulation.pick_place",
        "provider_id": "eai.pick_place", "provider_version": "2.0.0",
        "kind": "manipulation", "role": "local_pick_or_place",
        "module": "algorithm.skills.pick_place", "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(operation, object_ref, timeout_s); navigation is caller-owned",
        "output_contract": "bool after physical completion; caller owns navigation",
        "executor_owner": "task.executors.pick_place.PickPlaceExecutor",
        "backends": ("ArmPickPlaceAdapter", "DronePickPlaceAdapter", "PepperPickPlaceAdapter", "G1PickPlaceAdapter"),
        "status": "implemented_registered_backends",
    },
    {
        "skill_id": "eai.navigation.node_route",
        "provider_id": "node_route.provider", "provider_version": "2.0",
        "kind": "navigation", "role": "authored_node_route",
        "module": "algorithm.skills.navigation.node_route",
        "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(target_ref, intent, world_state_seq, timing/budget)",
        "output_contract": "SkillPlan(navigation_target)",
        "executor_owner": "task.executors.navigation.NavigationExecutor",
        "adapter_owner": "task.executors.navigation_backends.NavigationControllerAdapter",
        "status": "implemented_unified_provider_adapter_boundary",
    },
    {
        "skill_id": "eai.observation.camera",
        "provider_id": "eai.observation.camera",
        "provider_version": "1.0.0",
        "kind": "inspection",
        "role": "on_demand_camera_observation",
        "module": "algorithm.skills.observation",
        "schema_version": "simulator.skill.v1",
        "input_contract": "SkillRequest(camera_ref, mode=capture, deadline, resolution, quality)",
        "output_contract": "metered observation receipt with frame digest, artifact identity and transport bytes",
        "executor_owner": "benchmark.observation_runtime.CameraObservationRuntime",
        "backends": ("BuilderCameraBackend", "FakeOnDemandCameraBackend", "OrsusOnDemandCameraBackend"),
        "status": "implemented_metered_builder_camera_gpu_frame_receipt",
        "subscription": False,
        "execution_validated": False,
        "scoreable": False,
    },
    {
        "skill_id": "nav2_navigation_io",
        "kind": "external_io", "role": "system_ros_navigation_goal_transport",
        "module": "algorithm.skills.external_io.nav2",
        "input_contract": "host-owned ROS/Nav2 transport request",
        "output_contract": "external transport message",
        "status": "provider_pending_dedicated_skill_developer",
    },
    {
        "skill_id": "keyboard_velocity_io",
        "kind": "external_io", "role": "manual_velocity_command_transport",
        "module": "algorithm.skills.external_io.keyboard",
        "input_contract": "operator velocity event",
        "output_contract": "external transport message",
        "status": "provider_pending_dedicated_skill_developer",
    },
)


def skill_records() -> list[dict[str, Any]]:
    return [dict(item) for item in SKILL_REGISTRY]


__all__ = ["SKILL_REGISTRY", "skill_records"]
