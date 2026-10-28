"""Registration metadata for the official HARL/HAPPO source boundary."""

from __future__ import annotations

import os

from algorithm.common_coordination import AlgorithmCondition


ALGORITHM_ID = "HARL-HAPPO-adapted-to-EAI"
SOURCE_REFERENCE = (
    "JMLR 2024 / PKU-MARL/HARL@"
    "b1af98b0dbab72a2eee9d160751cd09aedbb8ce2"
)


def condition_record() -> AlgorithmCondition:
    checkpoint = os.getenv("HETEROCOORD_HARL_HAPPO_CHECKPOINT", "").strip()
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="official_source_checkpoint_gated_adapter",
        execution_scope="observation_and_inference_contract_only",
        planner="HAPPO_policy_inference_requires_registered_environment_and_checkpoint",
        model_or_api="official_HARL_source",
        checkpoint=checkpoint or "N/A_no_verifiable_official_HAPPO_checkpoint",
        supported_primitives=("heterogeneous_observation_action", "fixed_actor_identity"),
        unsupported_dimensions=(
            "Fire_Rescue_inference_without_checkpoint",
            "environment_contract_not_registered",
            "D2_paired_communication_retention",
            "D4_member_exit_recovery",
            "D5_certified_cross_domain_transfer",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "The official HARL repository is a training framework. No verifiable HAPPO "
            "checkpoint and no matching Fire Rescue environment contract are present; "
            "the adapter therefore fails closed instead of substituting a heuristic policy."
        ),
    )


def condition_manifest() -> dict[str, object]:
    return condition_record().to_dict()


__all__ = ["ALGORITHM_ID", "SOURCE_REFERENCE", "condition_manifest", "condition_record"]
