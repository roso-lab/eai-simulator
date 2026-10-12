"""Machine-readable simulator contracts for registered EAI skills."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .contracts import SkillKind, SkillRequest


SIMULATOR_SKILL_SCHEMA_VERSION = "simulator.skill.v1"
GROUND_NAVIGATION_SKILL_ID = "eai.navigation.ground_navigation"
GROUND_NAVIGATION_SKILL_IDS = frozenset({GROUND_NAVIGATION_SKILL_ID, "ground_navigation"})


class SimulatorSchemaError(ValueError):
    """Raised when a fixed simulator skill input does not match its schema."""


@dataclass(frozen=True)
class SimulatorFieldSpec:
    name: str
    value_type: str
    required: bool = False
    unit: str | None = None
    frame: str | None = None
    description: str = ""
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class SimulatorCommandSpec:
    command_type: str
    route: str
    payload_fields: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class SimulatorSkillSchema:
    skill_id: str
    kind: SkillKind
    version: str = SIMULATOR_SKILL_SCHEMA_VERSION
    fields: tuple[SimulatorFieldSpec, ...] = ()
    commands: tuple[SimulatorCommandSpec, ...] = ()
    target_required: bool = False
    composite: bool = False

    @property
    def field_names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields)

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields if field.required)

    def validate(self, request: SkillRequest) -> None:
        ground = self.skill_id == GROUND_NAVIGATION_SKILL_ID
        if request.skill_id != self.skill_id and not (ground and request.skill_id in GROUND_NAVIGATION_SKILL_IDS):
            raise SimulatorSchemaError(
                f"request skill_id {request.skill_id!r} does not match {self.skill_id!r}"
            )
        parameters = dict(request.parameters)
        unknown = sorted(set(parameters) - self.field_names)
        if unknown:
            raise SimulatorSchemaError(
                f"{self.skill_id} has undeclared parameter fields: {unknown}"
            )
        missing = sorted(self.required_fields - set(parameters))
        if missing:
            raise SimulatorSchemaError(
                f"{self.skill_id} is missing required parameter fields: {missing}"
            )
        for field in self.fields:
            if field.name in parameters:
                _validate_field(field.name, parameters[field.name], field)
        if ground:
            if request.skill_version != self.version:
                raise SimulatorSchemaError("unsupported simulator Skill schema version")
            if (request.target_ref is None) == (request.target_pose is None):
                raise SimulatorSchemaError("ground navigation requires exactly one target")
            if request.target_ref is not None and (
                not isinstance(request.target_ref, str) or not request.target_ref.strip()
            ):
                raise SimulatorSchemaError("target_ref must be a non-empty string")
            if request.target_pose is not None and len(request.target_pose) not in (2, 3):
                raise SimulatorSchemaError("ground navigation target_pose must contain XY or XYZ")
            for name, default, allowed in (
                ("intent", "start", {"start", "continue", "hold", "replan", "stop"}),
                ("mode", "velocity", {"goal", "velocity"}),
                ("frame", "base_link", {"world", "base_link"}),
            ):
                value = parameters.get(name, default)
                if not isinstance(value, str) or value not in allowed:
                    raise SimulatorSchemaError(f"ground navigation {name} is invalid")
            if "horizon_ticks" in parameters and type(parameters["horizon_ticks"]) is not int:
                raise SimulatorSchemaError("horizon_ticks must be a positive integer")


def _validate_field(name: str, value: Any, spec: SimulatorFieldSpec) -> None:
    if value is None:
        if spec.required:
            raise SimulatorSchemaError(f"{name} cannot be null")
        return
    if spec.value_type == "positive_int":
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SimulatorSchemaError(f"{name} must be a positive integer") from exc
        if isinstance(value, bool) or integer != value or integer <= 0:
            raise SimulatorSchemaError(f"{name} must be a positive integer")
        return
    if spec.value_type == "string":
        if not isinstance(value, str) or not value.strip():
            raise SimulatorSchemaError(f"{name} must be a non-empty string")
        if spec.enum_values and value not in spec.enum_values:
            raise SimulatorSchemaError(f"{name} must be one of {spec.enum_values}")
        return
    if spec.value_type == "nonnegative_int":
        if type(value) is not int or value < 0:
            raise SimulatorSchemaError(f"{name} must be a nonnegative integer")
        return
    if spec.value_type == "finite_number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SimulatorSchemaError(f"{name} must be finite numeric")
        return
    raise SimulatorSchemaError(f"unsupported field type for {name}: {spec.value_type}")


SIMULATOR_SKILL_SCHEMAS: tuple[SimulatorSkillSchema, ...] = (
    SimulatorSkillSchema(
        GROUND_NAVIGATION_SKILL_ID,
        SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("intent", "string", enum_values=("start", "continue", "hold", "replan", "stop")),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("mode", "string", enum_values=("goal", "velocity")),
            SimulatorFieldSpec("frame", "string", enum_values=("world", "base_link")),
            SimulatorFieldSpec("target_yaw", "finite_number", unit="rad"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
            SimulatorFieldSpec("origin_ref", "string", description="Optional authored start landmark; start verifies proximity."),
            SimulatorFieldSpec("route_graph_digest", "string", description="Frozen executable topology identity from route resolution."),
        ),
        commands=(SimulatorCommandSpec(
            "base_velocity", "env.step -> EAI velocity controller",
            ("velocity", "frame", "horizon_ticks"),
            "Apply body-frame [vx, vy, wz]; physical success requires external evidence.",
        ),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        "eai.transport.composite",
        SkillKind.TRANSPORT,
        fields=(
            SimulatorFieldSpec("object_ref", "string", required=True),
            SimulatorFieldSpec("source_ref", "string", required=True),
            SimulatorFieldSpec("destination_ref", "string", required=True),
            SimulatorFieldSpec("carry_mode", "string", required=True),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(
            SimulatorCommandSpec(
                "pepper_transport_advance",
                "env.step -> PEPPER_BOX_TRANSPORT_CFG",
                (
                    "object_ref",
                    "source_ref",
                    "destination_ref",
                    "carry_mode",
                    "horizon_ticks",
                ),
                "Advance the existing Pepper composite transport controller.",
            ),
        ),
        composite=True,
    ),
)

_SCHEMA_BY_ID = {schema.skill_id: schema for schema in SIMULATOR_SKILL_SCHEMAS}
_SCHEMA_BY_ID["ground_navigation"] = _SCHEMA_BY_ID[GROUND_NAVIGATION_SKILL_ID]


def simulator_skill_schema(skill_id: str) -> SimulatorSkillSchema:
    try:
        return _SCHEMA_BY_ID[str(skill_id).strip()]
    except KeyError as exc:
        raise SimulatorSchemaError(f"unknown simulator skill schema: {skill_id!r}") from exc


def validate_simulator_skill_request(request: SkillRequest) -> SimulatorSkillSchema:
    schema = simulator_skill_schema(request.skill_id)
    schema.validate(request)
    return schema


__all__ = [
    "GROUND_NAVIGATION_SKILL_ID",
    "GROUND_NAVIGATION_SKILL_IDS",
    "SIMULATOR_SKILL_SCHEMAS",
    "SIMULATOR_SKILL_SCHEMA_VERSION",
    "SimulatorCommandSpec",
    "SimulatorFieldSpec",
    "SimulatorSchemaError",
    "SimulatorSkillSchema",
    "simulator_skill_schema",
    "validate_simulator_skill_request",
]
