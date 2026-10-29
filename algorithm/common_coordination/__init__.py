"""Shared benchmark-facing coordination records.

The HeteroCoord protocol separates a submitted algorithm's decisions from
the fleet service, skill adapters, and evaluator-owned physical predicates.
This package contains only serialisable Python records so algorithm adapters
can expose the same provenance and event vocabulary without importing Isaac
Sim, ROS, Torch, or an LLM client.
"""

from .contract import (
    AlgorithmCondition,
    Assignment,
    CoalitionAssignment,
    CoalitionSkillRequest,
    CoalitionExecutionReceipt,
    ContractError,
    CoordinationTask,
    EventLedger,
    ExecutionFeedback,
    FailureCode,
    MilestoneEvidence,
    RobotCapability,
    RunManifest,
    TaskGraph,
)

__all__ = [
    "AlgorithmCondition",
    "Assignment",
    "CoalitionAssignment",
    "CoalitionSkillRequest",
    "CoalitionExecutionReceipt",
    "ContractError",
    "CoordinationTask",
    "EventLedger",
    "ExecutionFeedback",
    "FailureCode",
    "MilestoneEvidence",
    "RobotCapability",
    "RunManifest",
    "TaskGraph",
]
