"""Registration metadata for the LaMMA-P EAI adapter."""

from algorithm.common_coordination import AlgorithmCondition


ALGORITHM_ID = "LaMMA-P-adapted-to-EAI"
SOURCE_REFERENCE = (
    "ICRA 2025 / arXiv:2409.20560 / tasl-lab/LaMMA-P@"
    "014ec981e0a7013e7bb7149c1c2dd12864800075"
)


def condition_record() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="dependency_order_and_skill_allocation_adapter",
        execution_scope="allocation_only_fire_rescue_hook",
        planner="caller_supplied_task_contract_plus_PDDL_compatible_skill_order",
        model_or_api="PDDL_semantics_without_AI2Thor_or_LLM_service",
        checkpoint="not_applicable_classical_planner",
        supported_primitives=("task_dependency_order", "heterogeneous_skills", "parallel_ready_tasks"),
        unsupported_dimensions=(
            "AI2Thor_execution",
            "LLM_decomposition_without_registered_API",
            "Fast_Downward_household_domain_transfer",
            "D2_paired_communication_retention",
            "D4_member_exit_recovery_without_feedback_round",
            "D5_certified_cross_domain_transfer",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "The Fire Rescue runner supplies the frozen task contract, so this boundary "
            "does not invent household PDDL or claim AI2-THOR execution. It preserves "
            "dependency ordering, skill feasibility and assignment provenance."
        ),
    )


def condition_manifest() -> dict[str, object]:
    return condition_record().to_dict()


__all__ = ["ALGORITHM_ID", "SOURCE_REFERENCE", "condition_manifest", "condition_record"]
