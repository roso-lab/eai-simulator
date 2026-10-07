from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="MRTA-heuristic-adapted-to-EAI",
        source_reference=(
            "ICRA 2024 DOI 10.1109/ICRA57147.2024.10611147; "
            "IEEE T-RO 2025 DOI 10.1109/TRO.2025.3626651; "
            "official https://github.com/multirobot-use/mrta_heuristic_planner@c69509e8"
        ),
        implementation="official_source_equivalent_python_adapter",
        execution_scope="heterogeneous_task_allocation_and_delay_repair",
        planner="heuristicTaskAllocator + planRepair (versions 9/13)",
        model_or_api="deterministic heuristic; no learned weights",
        supported_primitives=("heterogeneous_capability", "battery_cost", "priority", "delay_repair"),
        unsupported_dimensions=(
            "official_matlab_zero_modification_execution",
            "coalition_tasks",
            "navigation_and_manipulation_physics",
        ),
        notes=(
            "EAI uses a dependency-free Python interface-equivalent port so the Isaac Lab environment "
            "does not acquire MATLAB/Gurobi. Physical execution and milestones remain EAI-owned."
        ),
    )


__all__ = ["condition"]
