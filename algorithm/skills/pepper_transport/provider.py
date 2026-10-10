"""Pure ``simulator.skill.v1`` provider for Pepper box transport."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from algorithm.skills.contracts import (
    SkillCommand,
    SkillKind,
    SkillPlan,
    SkillRequest,
)
from algorithm.skills.simulator_schema import (
    SIMULATOR_SKILL_SCHEMA_VERSION,
    SimulatorSchemaError,
    validate_simulator_skill_request,
)


SKILL_ID = "eai.transport.composite"
PROVIDER_ID = "eai.pepper.box_transport"
PROVIDER_VERSION = "1.0.0"
CONTROLLER_CFG = "PEPPER_BOX_TRANSPORT_CFG"
COMMAND_TYPE = "pepper_transport_advance"
COMMAND_ROUTE = "env.step -> PEPPER_BOX_TRANSPORT_CFG"
CARRY_MODE = "bilateral_ideal_attachment"
DEFAULT_HORIZON_TICKS = 1200
MAX_HORIZON_TICKS = 5000


class PepperTransportRequestError(ValueError):
    """Raised when a request is outside the supported Pepper-only contract."""


def is_pepper_robot_id(robot_id: object) -> bool:
    """Return whether an identifier has a distinct ``pepper`` name token."""

    tokens = re.findall(r"[a-z0-9]+", str(robot_id).casefold())
    return "pepper" in tokens


def validate_pepper_transport_request(request: SkillRequest) -> None:
    """Validate both the shared schema and the concrete Pepper capability."""

    validate_simulator_skill_request(request)
    if not is_pepper_robot_id(request.robot_id):
        raise PepperTransportRequestError(
            "eai.transport.composite is implemented only for a Pepper robot"
        )
    if request.target_ref is not None or request.target_pose is not None:
        raise PepperTransportRequestError(
            "Pepper transport targets must use source_ref and destination_ref parameters"
        )
    parameters = dict(request.parameters)
    if parameters["carry_mode"] != CARRY_MODE:
        raise PepperTransportRequestError(
            f"unsupported Pepper carry_mode: {parameters['carry_mode']!r}"
        )
    horizon_ticks = parameters.get("horizon_ticks", DEFAULT_HORIZON_TICKS)
    if int(horizon_ticks) > MAX_HORIZON_TICKS:
        raise PepperTransportRequestError(
            f"horizon_ticks must not exceed {MAX_HORIZON_TICKS}"
        )
    refs = ("object_ref", "source_ref", "destination_ref")
    if len({parameters[name] for name in refs}) != len(refs):
        raise PepperTransportRequestError(
            "object_ref, source_ref, and destination_ref must be distinct"
        )


def _canonical_request(request: SkillRequest) -> dict[str, Any]:
    return {
        "parameters": dict(request.parameters),
        "request_id": request.request_id,
        "robot_id": request.robot_id,
        "skill_id": request.skill_id,
        "target_pose": list(request.target_pose) if request.target_pose is not None else None,
        "target_ref": request.target_ref,
    }


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PepperTransportProvider:
    """Compile a validated request without importing or invoking a simulator."""

    skill_id = SKILL_ID
    kind = SkillKind.TRANSPORT
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
            validate_pepper_transport_request(request)
        except SimulatorSchemaError as exc:
            return self._invalid_plan(request, "invalid_request", str(exc))
        except PepperTransportRequestError as exc:
            return self._invalid_plan(request, "unsupported_capability", str(exc))
        payload = dict(request.parameters)
        payload["horizon_ticks"] = int(
            payload.get("horizon_ticks", DEFAULT_HORIZON_TICKS)
        )
        return SkillPlan(
            request_id=request.request_id,
            skill_id=request.skill_id,
            robot_id=request.robot_id,
            commands=(SkillCommand(COMMAND_TYPE, payload),),
            status="planned",
            metadata={
                "command_route": COMMAND_ROUTE,
                "controller_cfg": CONTROLLER_CFG,
                "physical_outcome": "external_evaluator_required",
                "provider_id": PROVIDER_ID,
                "provider_version": PROVIDER_VERSION,
                "request_sha256": _digest(_canonical_request(request)),
                "schema_version": SIMULATOR_SKILL_SCHEMA_VERSION,
            },
        )

    @staticmethod
    def _invalid_plan(
        request: SkillRequest,
        status: str,
        reason: str,
    ) -> SkillPlan:
        return SkillPlan(
            request_id=request.request_id,
            skill_id=request.skill_id,
            robot_id=request.robot_id,
            commands=(),
            status=status,
            metadata={
                "physical_outcome": "external_evaluator_required",
                "provider_id": PROVIDER_ID,
                "provider_version": PROVIDER_VERSION,
                "reason": reason,
                "schema_version": SIMULATOR_SKILL_SCHEMA_VERSION,
            },
        )


__all__ = [
    "CARRY_MODE",
    "COMMAND_ROUTE",
    "COMMAND_TYPE",
    "CONTROLLER_CFG",
    "DEFAULT_HORIZON_TICKS",
    "MAX_HORIZON_TICKS",
    "PROVIDER_ID",
    "PROVIDER_VERSION",
    "PepperTransportProvider",
    "PepperTransportRequestError",
    "SKILL_ID",
    "is_pepper_robot_id",
    "validate_pepper_transport_request",
]
