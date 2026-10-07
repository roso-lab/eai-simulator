"""Provenance and domain limits for DCMRTA."""

from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="DCMRTA-adapted-to-EAI",
        source_reference=(
            "ICRA 2024 DOI 10.1109/ICRA57147.2024.10611244; "
            "official https://github.com/marmotlab/DCMRTA@"
            "029aeb81c28a38ecd69667153223e4aa740e01dd"
        ),
        implementation="official_checkpoint_native_descriptor_plus_fixed_dispatch_adapter",
        execution_scope="native_checkpoint_decision_only_and_EAI_execution_adapter",
        planner="official REINFORCE attention policy (native domain) + fixed Fire Rescue dispatch",
        model_or_api="official_REINFORCE_checkpoint_when_available",
        checkpoint="model/REINFORCE/checkpoint.pth; official checkpoint verified in source repository",
        supported_primitives=("coalition_task_decision", "native_checkpoint_inference", "EAI_feedback_ledger"),
        unsupported_dimensions=(
            "zero_shot_Fire_Rescue_checkpoint_validity",
            "4_agent_4_task_input_without_padding",
            "Z1_manipulation_physics",
        ),
        physical_executor_owner="eai_runtime",
        notes=(
            "Official training uses 10-20 agents, 20-50 tasks, five-dimensional requirements, "
            "and coalition tasks. Fire Rescue therefore runs the declared default mapping for "
            "physical evaluation; padded native inference is decision-only and cross-domain "
            "generalization is N/A."
        ),
    )


__all__ = ["condition"]
