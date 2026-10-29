"""Compiler for the fixed-action PickPlace public contract."""
from __future__ import annotations

from ..contracts import SkillCommand, SkillKind, SkillPlan, SkillRequest
from ..simulator_schema import PICK_PLACE_SKILL_ID, validate_simulator_skill_request
from .contracts import PickPlaceCommand


def compile_request(value) -> PickPlaceCommand:
    request = value if isinstance(value, SkillRequest) else SkillRequest.from_mapping(value)
    if request.skill_id != PICK_PLACE_SKILL_ID:
        raise ValueError("request is not a local pick/place skill")
    validate_simulator_skill_request(request)
    p = request.parameters
    return PickPlaceCommand(
        request_id=request.request_id,
        robot_id=request.robot_id,
        object_ref=p["object_ref"],
        operation=p["operation"],
        timeout_s=float(p.get("timeout_s", 60.0)),
    )


class PickPlaceProvider:
    skill_id = PICK_PLACE_SKILL_ID
    kind = SkillKind.MANIPULATION
    provider_id = "eai.pick_place"
    provider_version = "2.0.0"

    def plan(self, request, *, observation=None):
        del observation
        try:
            c = compile_request(request)
        except (ValueError, TypeError, KeyError) as exc:
            return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                             status="invalid_request", metadata={"reason": str(exc)})
        payload = {
            "operation": c.operation,
            "object_ref": c.object_ref,
            "timeout_s": c.timeout_s,
        }
        return SkillPlan(c.request_id, self.skill_id, c.robot_id,
                         (SkillCommand("pick_place", payload),),
                         metadata={"provider_id": self.provider_id,
                                   "provider_version": self.provider_version,
                                   "schema_version": request.skill_version,
                                   "physical_outcome": "unknown"})


__all__ = ["PickPlaceProvider", "compile_request"]
