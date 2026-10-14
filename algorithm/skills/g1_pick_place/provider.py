"""Pure provider for fixed-base G1 right-hand pick and place."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from algorithm.skills.contracts import SkillCommand, SkillKind, SkillPlan, SkillRequest
from algorithm.skills.simulator_schema import (
    SIMULATOR_SKILL_SCHEMA_VERSION,
    SimulatorSchemaError,
    validate_simulator_skill_request,
)


SKILL_ID = "eai.manipulation.g1_pick_place"
PROVIDER_ID = "g1_pick_place.provider"
PROVIDER_VERSION = "1.0"
COMMAND_TYPE = "g1_pick_place"
COMMAND_ROUTE = "task.executors.g1_pick_place.G1PickPlaceSkill.start_plan"


def _request_digest(request: SkillRequest) -> str:
    payload = {
        "parameters": dict(request.parameters),
        "request_id": request.request_id,
        "robot_id": request.robot_id,
        "skill_id": request.skill_id,
        "skill_version": request.skill_version,
        "target_pose": request.target_pose,
        "target_ref": request.target_ref,
    }
    encoded = json.dumps(payload, allow_nan=False, ensure_ascii=True,
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_g1_pick_place_request(request: SkillRequest) -> None:
    """Reject inputs outside the fixed-base, world-XYZ executor boundary."""

    if request.skill_id != SKILL_ID:
        raise SimulatorSchemaError(
            f"request skill_id {request.skill_id!r} does not match {SKILL_ID!r}"
        )
    validate_simulator_skill_request(request)
    if request.target_ref is not None or request.target_pose is not None:
        raise SimulatorSchemaError(
            "G1 pick/place uses pick_position and place_position parameters"
        )
    if request.timing is not None:
        raise SimulatorSchemaError("G1 pick/place does not accept timing")
    if request.budget is not None:
        raise SimulatorSchemaError("G1 pick/place does not accept budget")


class G1PickPlaceProvider:
    """Compile a request without importing or invoking the simulator runtime."""

    skill_id = SKILL_ID
    kind = SkillKind.MANIPULATION
    provider_id = PROVIDER_ID
    provider_version = PROVIDER_VERSION

    def plan(
        self,
        request: SkillRequest,
        *,
        observation: Mapping[str, Any] | None = None,
    ) -> SkillPlan:
        del observation
        try:
            validate_g1_pick_place_request(request)
        except (SimulatorSchemaError, ValueError, TypeError) as exc:
            return SkillPlan(
                request_id=request.request_id,
                skill_id=request.skill_id,
                robot_id=request.robot_id,
                commands=(),
                status="invalid_request",
                metadata={
                    "command_route": COMMAND_ROUTE,
                    "physical_outcome": "external_evaluator_required",
                    "provider_id": PROVIDER_ID,
                    "provider_version": PROVIDER_VERSION,
                    "reason": str(exc),
                    "schema_version": SIMULATOR_SKILL_SCHEMA_VERSION,
                },
            )
        parameters = dict(request.parameters)
        payload = {
            "pick_position": list(parameters["pick_position"]),
            "place_position": list(parameters["place_position"]),
            "frame": parameters.get("frame", "world"),
        }
        if "object_ref" in parameters:
            payload["object_ref"] = parameters["object_ref"]
        return SkillPlan(
            request_id=request.request_id,
            skill_id=request.skill_id,
            robot_id=request.robot_id,
            commands=(SkillCommand(COMMAND_TYPE, payload),),
            status="planned",
            metadata={
                "command_route": COMMAND_ROUTE,
                "execution_scope": "fixed_base_calibrated_inspire_right_hand_usd_ideal_attachment",
                "physical_outcome": "external_evaluator_required",
                "provider_id": PROVIDER_ID,
                "provider_version": PROVIDER_VERSION,
                "request_sha256": _request_digest(request),
                "schema_version": SIMULATOR_SKILL_SCHEMA_VERSION,
            },
        )


__all__ = [
    "COMMAND_ROUTE",
    "COMMAND_TYPE",
    "G1PickPlaceProvider",
    "PROVIDER_ID",
    "PROVIDER_VERSION",
    "SKILL_ID",
    "validate_g1_pick_place_request",
]
