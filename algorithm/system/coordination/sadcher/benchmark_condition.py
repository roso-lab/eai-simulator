"""Registration metadata for the SADCHER EAI adapter."""

from __future__ import annotations

import os
from pathlib import Path

from algorithm.common_coordination import AlgorithmCondition


ALGORITHM_ID = "SADCHER-adapted-to-EAI"
SOURCE_REFERENCE = (
    "IEEE MRS 2025 / jakbichler/Sadcher@"
    "a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae"
)
DEFAULT_SOURCE_ROOT = "/home/airs/.cache/heterocoord/upstream/sadcher"
DEFAULT_CHECKPOINT = (
    "imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt"
)


def source_root() -> str:
    return os.getenv("HETEROCOORD_SADCHER_ROOT", DEFAULT_SOURCE_ROOT)


def checkpoint_path() -> str:
    configured = os.getenv("HETEROCOORD_SADCHER_CHECKPOINT", "").strip()
    if configured:
        return configured
    return str(Path(source_root()) / DEFAULT_CHECKPOINT)


def condition_record() -> AlgorithmCondition:
    checkpoint = Path(checkpoint_path())
    checkpoint_record = str(checkpoint) if checkpoint.is_file() else f"N/A_missing:{checkpoint}"
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="legacy_entrypoint_disabled_use_NativeSadcherAdapter",
        execution_scope="not_executable_legacy_contract_incomplete",
        planner="unchanged_SadcherScheduler_requires_complete_checkpoint",
        model_or_api="official_SADCHER_IL_checkpoint",
        checkpoint=checkpoint_record,
        supported_primitives=(),
        unsupported_dimensions=(
            "P3_continuous_service",
            "D2_paired_communication_retention",
            "D4_registered_member_exit_panel",
            "D5_certified_cross_domain_transfer",
            "physical_success_without_eai_evidence",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "Legacy AssignmentRequest adaptation is disabled. NativeSadcherAdapter requires "
            "explicit EaiTaskPackage durations, positions, statuses and sentinels, and runs "
            "the unchanged scheduler with both native filters. The default checkpoint matches "
            "the official benchmark; explicitly requested incompatible weights are rejected. Native coalitions "
            "cannot be dispatched by the current single-owner TaskRunner."
        ),
    )


def condition_manifest() -> dict[str, object]:
    return condition_record().to_dict()


__all__ = [
    "ALGORITHM_ID",
    "SOURCE_REFERENCE",
    "condition_manifest",
    "condition_record",
    "checkpoint_path",
    "source_root",
]
