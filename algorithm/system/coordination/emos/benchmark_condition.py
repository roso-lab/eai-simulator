"""HeteroCoord registration metadata for the existing EMOS integration.

The default Fire Rescue ``emos`` path remains the native EMOS discussion
manager.  This module does not replace it with a deterministic callback; it
only records exactly which part of the algorithm is being evaluated and which
HeteroCoord dimensions are outside EMOS's published scope.
"""

from __future__ import annotations

from typing import Any, Mapping

try:  # repository-root import
    from algorithm.common_coordination import AlgorithmCondition, Assignment, EventLedger
except ModuleNotFoundError:  # ``PYTHONPATH=algorithm`` import
    from common_coordination import AlgorithmCondition, Assignment, EventLedger


ALGORITHM_ID = "EMOS-adapted-to-EAI"
SOURCE_REFERENCE = "ICLR 2025 / arXiv:2410.22662 / SgtVincent/EMOS"


def condition_record(*, model_or_api: str = "provider-configured", prompt_revision: str = "registered-manager-prompt") -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="adapted",
        execution_scope="scenario_discussion_and_assignment",
        planner="robot_resume_plus_multi_llm_discussion_with_fallback",
        model_or_api=model_or_api,
        checkpoint="not_applicable_no_learned_weights",
        prompt_revision=prompt_revision,
        supported_primitives=("P1", "P2"),
        unsupported_dimensions=(
            "P3_coupled_joint_action",
            "D2_paired_communication_retention",
            "D4_member_exit_recovery",
            "D5_certified_cross_domain_transfer",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "EMOS owns robot-resume/discussion/allocation decisions. "
            "EAI owns navigation, manipulation, physical milestones, replay, and termination."
        ),
    )


def condition_manifest(**kwargs: Any) -> dict[str, object]:
    """Return credential-free JSON metadata for a benchmark run."""

    return condition_record(**kwargs).to_dict()


def record_assignment(
    ledger: EventLedger,
    assignments: Mapping[str, str],
    *,
    source: str,
    plan_version: str = "",
) -> tuple[Assignment, ...]:
    """Record an EMOS proposal without treating it as physical success."""

    records = tuple(
        Assignment(task_id=str(task_id), robot_id=str(robot_id), source=source, plan_version=plan_version)
        for robot_id, task_id in sorted(assignments.items())
    )
    ledger.append(
        "proposal",
        algorithm_id=ALGORITHM_ID,
        source=source,
        assignments=[{"robot_id": item.robot_id, "task_id": item.task_id} for item in records],
    )
    return records


__all__ = ["ALGORITHM_ID", "SOURCE_REFERENCE", "condition_manifest", "condition_record", "record_assignment"]
