"""Typed, dependency-light skill contracts for EAI controllers."""

from .contracts import (
    SkillCommand,
    SkillContractError,
    SkillKind,
    SkillPlan,
    SkillProvider,
    SkillRequest,
)
from .pick_place import PickPlaceProvider
from .registry import SKILL_REGISTRY, skill_records
from .simulator_schema import (
    AERIAL_NAVIGATION_SKILL_ID,
    GROUND_NAVIGATION_SKILL_ID,
    GROUND_NAVIGATION_SKILL_IDS,
    PICK_PLACE_SKILL_ID,
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
    "AERIAL_NAVIGATION_SKILL_ID",
    "GROUND_NAVIGATION_SKILL_ID",
    "GROUND_NAVIGATION_SKILL_IDS",
    "PickPlaceProvider",
    "PICK_PLACE_SKILL_ID",
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
