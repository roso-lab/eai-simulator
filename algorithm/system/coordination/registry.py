"""Machine-readable classification of the system-level algorithm adapters."""

from __future__ import annotations

from typing import Any


SYSTEM_ALGORITHM_PATHS: dict[str, str] = {
    "EMOS": "algorithm.system.coordination.emos",
    "TeamWeaver": "algorithm.system.coordination.TeamWeaver",
    "COHERENT-adapted-to-EAI": "algorithm.system.coordination.coherent",
    "FMC-ATA": "algorithm.system.coordination.fmc_ata",
    "BT-capabilities": "algorithm.system.coordination.bt_capabilities",
    "MRTA-heuristic-adapted-to-EAI": "algorithm.system.coordination.mrta_heuristic",
    "Hybrid-SUSD-MRTA-adapted-to-EAI": "algorithm.system.coordination.hybrid_susd",
    "DCMRTA-adapted-to-EAI": "algorithm.system.coordination.dcmrta",
    "HeteroMRTA-adapted-to-EAI": "algorithm.system.coordination.heteromrta",
    "HARL-HAPPO": "algorithm.harl_isaac",
    "HeteroCoord-bridge": "algorithm.heterocoord",
    "Gosrich-et-al": "N/A (official source unavailable)",
    "Qrainbow": "N/A (official source and checkpoint unavailable)",
    "LiP-LLM-adapted-to-EAI": "N/A (official source unavailable)",
    "Robust-Online-Epistemic-Replanning-adapted-to-EAI": (
        "N/A (official reproduction unavailable)"
    ),
}


_ROLES: dict[str, str] = {
    "EMOS": "discussion_and_task_allocation",
    "TeamWeaver": "task_decomposition_allocation_replanning",
    "COHERENT-adapted-to-EAI": "centralized_capability_assignment_feedback",
    "FMC-ATA": "market_based_fractional_allocation",
    "BT-capabilities": "capability_auction_and_tree_dispatch",
    "MRTA-heuristic-adapted-to-EAI": "heuristic_assignment_and_delay_repair",
    "Hybrid-SUSD-MRTA-adapted-to-EAI": "market_initialization_and_susd_search",
    "DCMRTA-adapted-to-EAI": "checkpoint_coalition_decision",
    "HeteroMRTA-adapted-to-EAI": "checkpoint_heterogeneous_coalition_decision",
    "HARL-HAPPO": "learned_policy_decision_only",
    "HeteroCoord-bridge": "benchmark_decision_and_provenance",
    "Gosrich-et-al": "coalition_flow_precedence_replanning",
    "Qrainbow": "asynchronous_macro_action_policy",
    "LiP-LLM-adapted-to-EAI": "skill_dependency_dag_assignment",
    "Robust-Online-Epistemic-Replanning-adapted-to-EAI": "belief_and_failure_replanning",
}


_AVAILABILITY: dict[str, str] = {
    "Gosrich-et-al": "N/A",
    "Qrainbow": "N/A",
    "LiP-LLM-adapted-to-EAI": "N/A",
    "Robust-Online-Epistemic-Replanning-adapted-to-EAI": "N/A",
}


_IMPLEMENTATION_METADATA: dict[str, dict[str, Any]] = {
    "EMOS": {
        "implementation_location": "canonical_tested_system_algorithm",
        "compatibility_copy": False,
    },
    "TeamWeaver": {
        "implementation_location": "canonical_tested_system_algorithm",
        "compatibility_copy": False,
    },
}


def system_algorithm_records() -> list[dict[str, Any]]:
    """Return the canonical system layer records without importing Isaac."""

    input_contract = (
        "EaiTaskPackage + RobotState + symbolic_world_state + EventLedger"
    )
    output_contract = (
        "AlgorithmDecision(assignments, unassigned_tasks, provenance)"
    )
    return [
        {
            "algorithm_id": algorithm_id,
            "layer": "system",
            "role": _ROLES[algorithm_id],
            "module": module,
            "availability": _AVAILABILITY.get(algorithm_id, "registered"),
            **_IMPLEMENTATION_METADATA.get(
                algorithm_id,
                {
                    "implementation_location": "canonical_tested_system_algorithm",
                    "compatibility_copy": False,
                },
            ),
            "input_contract": input_contract,
            "output_contract": output_contract,
            "executor_owner": "task.executors -> EAI controller/skill",
            "physical_success_owner": "task.evaluators -> EAI runtime",
        }
        for algorithm_id, module in SYSTEM_ALGORITHM_PATHS.items()
    ]
