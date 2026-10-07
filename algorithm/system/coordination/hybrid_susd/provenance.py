"""Source and applicability record for Hybrid SUSD MRTA."""

from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="Hybrid-SUSD-MRTA-adapted-to-EAI",
        source_reference=(
            "ICRA 2023 DOI 10.1109/ICRA48891.2023.10161349; "
            "official https://github.com/GeorgiaTechSystemResearch/Hybrid_SUSD_MRTA_public@"
            "52473fe16f606dca6e6b3bcc99f3f164afdbe6ee"
        ),
        implementation="official_matlab_interface_equivalent_python_adapter",
        execution_scope="heterogeneous_market_initialization_and_susd_allocation",
        planner="market_onlineTA + susd_onlineTA discrete neighborhood adapter",
        model_or_api="classical_market_and_derivative_free_search",
        checkpoint="not_applicable_no_learned_weights",
        supported_primitives=("specialization_Q", "task_types_Y", "market_initialization", "susd_local_search"),
        unsupported_dimensions=(
            "native_matlab_zero_modification_execution",
            "native_failure_replanning",
            "navigation_manipulation_and_physical_milestones",
        ),
        physical_executor_owner="eai_runtime",
        notes=(
            "The adapter preserves the paper's Q/Y and market-to-SUSD stages. "
            "EAI uses deterministic finite neighborhoods for indivisible task slots; "
            "failure recovery is an adapter-level rerun and native replanning is N/A."
        ),
    )


__all__ = ["condition"]
