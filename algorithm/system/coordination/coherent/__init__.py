"""Dependency-free COHERENT PEFA adapter for EAI task contracts.

This package deliberately stops at the typed task/skill boundary.  Isaac Sim
controllers can be supplied by an EAI skill executor, while offline tests use
the deterministic Fire Rescue fixture shipped here.
"""

from .contract import (
    ActionCommand,
    CapabilityProfile,
    Feedback,
    PlanAssignment,
    Proposal,
    SkillSpec,
    TaskSpec,
)
from .adapter import COHERENTAdapter, PEFAReport, UnsupportedCapability
from .fire_rescue import fire_rescue_fixture
from .provenance import DongliConfig, ProvenanceError, chat_completion
from .fire_rescue_policy import (
    assign as fire_rescue_assign,
    condition_manifest,
    condition_record,
)

ALGORITHM_ID = "COHERENT-adapted-to-EAI"

__all__ = [
    "ActionCommand",
    "CapabilityProfile",
    "COHERENTAdapter",
    "Feedback",
    "PEFAReport",
    "PlanAssignment",
    "Proposal",
    "SkillSpec",
    "TaskSpec",
    "UnsupportedCapability",
    "fire_rescue_fixture",
    "DongliConfig",
    "ProvenanceError",
    "chat_completion",
    "fire_rescue_assign",
    "condition_manifest",
    "condition_record",
    "ALGORITHM_ID",
]
