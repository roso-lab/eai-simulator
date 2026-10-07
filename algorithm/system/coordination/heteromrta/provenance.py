"""Provenance and domain limits for HeteroMRTA."""

from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="HeteroMRTA-adapted-to-EAI",
        source_reference=(
            "IEEE RA-L 2025 DOI 10.1109/LRA.2025.3534682; "
            "official https://github.com/marmotlab/HeteroMRTA@"
            "db51e29535e34bcaa8cb75f70be3fd28b0027988"
        ),
        implementation="official_checkpoint_native_descriptor_plus_fixed_dispatch_adapter",
        execution_scope="native_checkpoint_decision_only_and_EAI_execution_adapter",
        planner="official AttentionNet/REINFORCE policy (native domain) + fixed Fire Rescue dispatch",
        model_or_api="official_REINFORCE_checkpoint_when_available",
        checkpoint="model/save/checkpoint.pth; official checkpoint path recorded",
        supported_primitives=("heterogeneous_traits", "coalition_task_scheduling", "native_checkpoint_inference", "EAI_feedback_ledger"),
        unsupported_dimensions=(
            "zero_shot_Fire_Rescue_checkpoint_validity",
            "4_agent_4_task_input_without_padding",
            "Z1_manipulation_physics",
        ),
        physical_executor_owner="eai_runtime",
        notes=(
            "The official implementation uses 3-5 species with three agents per species, "
            "15-50 tasks and five-dimensional traits. Fire Rescue therefore uses its declared "
            "default mapping for physical evaluation; native checkpoint decision and cross-domain "
            "generalization remain separate and are N/A on this scene."
        ),
    )


__all__ = ["condition"]
