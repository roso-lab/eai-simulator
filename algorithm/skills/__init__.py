"""Typed, dependency-light skill contracts for EAI controllers."""

from .contracts import (
    SkillCommand,
    SkillContractError,
    SkillKind,
    SkillPlan,
    SkillProvider,
    SkillRequest,
)
from .registry import SKILL_REGISTRY, skill_records
from .simulator_schema import (
    SIMULATOR_SKILL_SCHEMAS,
    SIMULATOR_SKILL_SCHEMA_VERSION,
    SimulatorCommandSpec,
    SimulatorFieldSpec,
    SimulatorSchemaError,
    SimulatorSkillSchema,
    simulator_skill_schema,
    validate_simulator_skill_request,
)

__all__ = [
    "SKILL_REGISTRY",
    "SIMULATOR_SKILL_SCHEMAS",
    "SIMULATOR_SKILL_SCHEMA_VERSION",
    "SimulatorCommandSpec",
    "SimulatorFieldSpec",
    "SimulatorSchemaError",
    "SimulatorSkillSchema",
    "SkillCommand",
    "SkillContractError",
    "SkillKind",
    "SkillPlan",
    "SkillProvider",
    "SkillRequest",
    "simulator_skill_schema",
    "skill_records",
    "validate_simulator_skill_request",
]
