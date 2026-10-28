"""Registration metadata for the RobotFleet EAI adapter."""

from algorithm.common_coordination import AlgorithmCondition


ALGORITHM_ID = "RobotFleet-adapted-to-EAI"
SOURCE_REFERENCE = (
    "arXiv:2510.10379 / therohangupta/robot-fleet@"
    "67c5f724727bf25d4955fedbfb1fd63f8ec29914"
)


def condition_record() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="in_memory_dag_allocator_adapter",
        execution_scope="allocation_only_fire_rescue_hook",
        planner="caller_supplied_task_dag_plus_capability_allocator",
        model_or_api="MILP_semantics_without_postgresql_or_LLM_service",
        checkpoint="not_applicable_classical_framework",
        supported_primitives=("task_DAG", "heterogeneous_capabilities", "allocation", "feedback_replan"),
        unsupported_dimensions=(
            "LLM_task_decomposition_without_registered_API",
            "PostgreSQL_world_registry",
            "Docker_robot_executor",
            "D2_paired_communication_retention",
            "D5_certified_cross_domain_transfer",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "The public RobotFleet service depends on PostgreSQL, Docker and optional LLM services. "
            "This adapter preserves its DAG, capability allocation and feedback/replan boundary "
            "with the Fire Rescue task contract supplied by EAI."
        ),
    )


def condition_manifest() -> dict[str, object]:
    return condition_record().to_dict()


__all__ = ["ALGORITHM_ID", "SOURCE_REFERENCE", "condition_manifest", "condition_record"]
