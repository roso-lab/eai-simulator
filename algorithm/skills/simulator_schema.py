"""Machine-readable simulator contract for the Pepper transport skill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import SkillKind, SkillRequest


SIMULATOR_SKILL_SCHEMA_VERSION = "simulator.skill.v1"


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
        if request.skill_id != self.skill_id:
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
        return
    raise SimulatorSchemaError(f"unsupported field type for {name}: {spec.value_type}")


SIMULATOR_SKILL_SCHEMAS: tuple[SimulatorSkillSchema, ...] = (
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
    "SIMULATOR_SKILL_SCHEMAS",
    "SIMULATOR_SKILL_SCHEMA_VERSION",
    "SimulatorCommandSpec",
    "SimulatorFieldSpec",
    "SimulatorSchemaError",
    "SimulatorSkillSchema",
    "simulator_skill_schema",
    "validate_simulator_skill_request",
]
