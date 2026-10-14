"""Pure compiler from caller-supplied world coordinates to a base-frame intent."""
from __future__ import annotations

from ..contracts import SkillCommand, SkillKind, SkillPlan, SkillRequest
from ..simulator_schema import PICK_PLACE_SKILL_ID, validate_simulator_skill_request
from ..aerial_transport.geometry import conjugate, vector
from .contracts import PickPlaceCommand, multiply, pose_xyzw


def compile_request(value) -> PickPlaceCommand:
    request = value if isinstance(value, SkillRequest) else SkillRequest.from_mapping(value)
    if request.skill_id != PICK_PLACE_SKILL_ID:
        raise ValueError("request is not a local pick/place skill")
    validate_simulator_skill_request(request)
    p = request.parameters
    base = pose_xyzw(p["base_position_world_m"], p["base_orientation_xyzw"])
    orientation = p.get("orientation_xyzw")
    relative = None
    if orientation is not None:
        goal = pose_xyzw((0., 0., 0.), orientation)
        relative = multiply(conjugate(base.orientation_wxyz), goal.orientation_wxyz)
    grasp_offset = p.get("grasp_offset_object_m")
    if grasp_offset is not None:
        grasp_offset = vector(grasp_offset, 3)
    return PickPlaceCommand(request.request_id, request.robot_id, p["object_ref"],
        p["operation"], base, base.to_local(request.target_pose), relative,
        p["world_state_seq"], p["simulation_time_s"], p.get("timeout_s", 60.),
        grasp_offset)


class PickPlaceProvider:
    skill_id = PICK_PLACE_SKILL_ID
    kind = SkillKind.MANIPULATION
    provider_id = "eai.pick_place"
    provider_version = "1.0.0"

    def plan(self, request, *, observation=None):
        del observation  # All sampled coordinates are explicit request inputs.
        try:
            c = compile_request(request)
        except (ValueError, TypeError, KeyError) as exc:
            return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                status="invalid_request", metadata={"reason": str(exc)})
        orientation = c.target_orientation_base_wxyz
        return SkillPlan(c.request_id, self.skill_id, c.robot_id,
            (SkillCommand("pick_place", {
                "operation": c.operation, "object_ref": c.object_ref,
                "target_position_base_m": c.target_position_base_m,
                "target_orientation_base_xyzw": None if orientation is None else (*orientation[1:], orientation[0]),
                "grasp_offset_object_m": c.grasp_offset_object_m,
                "base_position_world_m": c.base_pose_world.position_m,
                "base_orientation_xyzw": (*c.base_pose_world.orientation_wxyz[1:], c.base_pose_world.orientation_wxyz[0]),
                "world_state_seq": c.world_state_seq, "simulation_time_s": c.simulation_time_s,
                "timeout_s": c.timeout_s,
            }),), metadata={"provider_id": self.provider_id,
                "provider_version": self.provider_version, "schema_version": request.skill_version,
                "physical_outcome": "unknown"})


__all__ = ["PickPlaceProvider", "compile_request"]
