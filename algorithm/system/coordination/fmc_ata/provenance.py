"""Provenance for FMC-ATA's isolated Java source and EAI bridge."""

from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="FMC-ATA-adapted-to-EAI",
        source_reference="IJCAI 2023 / 10.24963/ijcai.2023/30 / FisherMarketSimulator",
        implementation="adapted_market_boundary",
        execution_scope="asynchronous_divisible_workload_allocation",
        planner="Fisher market clearing with delayed/lost message events",
        model_or_api="classical_market_clearing",
        checkpoint="not_applicable_no_learned_weights",
        supported_primitives=("divisible_workload", "heterogeneous_skills", "message_delay", "message_loss", "task_arrival"),
        unsupported_dimensions=("indivisible_physical_skill_without_fraction_adapter", "Isaac_execution_until_eai_executor"),
        physical_executor_owner="eai_runtime",
        serial_action_loop=False,
        notes=(
            "The public source is a Java FisherMarketSimulator. This Python bridge "
            "keeps fractional allocations and message-triggered re-clearing explicit; "
            "it is not a claim of byte-identical Java execution. For the current "
            "indivisible Fire Rescue graph, the benchmark supplies the scenario's "
            "default assignment so the downstream EAI execution path remains testable; "
            "FMC-ATA allocation itself remains N/A."
        ),
    )
