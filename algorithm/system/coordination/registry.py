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
    "HARL-HAPPO": "algorithm.system.coordination.harl",
    "HARL-HAPPO-adapted-to-EAI": "algorithm.system.coordination.harl",
    "SADCHER-adapted-to-EAI": "algorithm.system.coordination.sadcher",
    "RobotFleet-adapted-to-EAI": "algorithm.system.coordination.robot_fleet",
    "LaMMA-P-adapted-to-EAI": "algorithm.system.coordination.lammap",
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
    "HARL-HAPPO-adapted-to-EAI": "checkpoint_gated_heterogeneous_policy_inference",
    "SADCHER-adapted-to-EAI": "native_scheduler_network_and_matching",
    "RobotFleet-adapted-to-EAI": "task_dag_allocator_and_feedback_replan",
    "LaMMA-P-adapted-to-EAI": "dependency_order_and_skill_allocation",
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


_BENCHMARK_ADAPTER_METADATA: dict[str, dict[str, Any]] = {
    "EMOS": {
        "decision_provider": "benchmark.emos.EmosDecisionAdapter",
        "replan_provider": "native_EMOS_or_runtime_specific",
        "active_matrix": True,
    },
    "COHERENT-adapted-to-EAI": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "FMC-ATA": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "BT-capabilities": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "HARL-HAPPO-adapted-to-EAI": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "SADCHER-adapted-to-EAI": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "RobotFleet-adapted-to-EAI": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "LaMMA-P-adapted-to-EAI": {
        "decision_provider": "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
        "replan_provider": "adapter.replan",
        "active_matrix": True,
    },
    "HeteroMRTA-adapted-to-EAI": {
        "decision_provider": "historical_registry_only",
        "replan_provider": "not_run_by_request",
        "active_matrix": False,
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
    records = [
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
            **_BENCHMARK_ADAPTER_METADATA.get(algorithm_id, {}),
            "input_contract": input_contract,
            "output_contract": output_contract,
            "executor_owner": "task.executors -> EAI controller/skill",
            "physical_success_owner": "task.evaluators -> EAI runtime",
        }
        for algorithm_id, module in SYSTEM_ALGORITHM_PATHS.items()
    ]
    primary = {"EMOS": "emos", "TeamWeaver": "teamweaver", "FMC-ATA": "fmc_ata",
               "BT-capabilities": "bt_capabilities", "HARL-HAPPO": "harl_happo",
               "SADCHER-adapted-to-EAI": "sadcher"}
    for record in records:
        key = primary.get(record["algorithm_id"])
        record["active_matrix"] = key is not None
        if key is not None:
            package = {"teamweaver": "TeamWeaver", "harl_happo": "harl"}.get(key, key)
            record.update(paper_algorithm_key=key,
                          decision_provider="benchmark.native_algorithms.build_native_adapter",
                          native_adapter_module=f"algorithm.system.coordination.{package}.native_adapter",
                          implementation_location="original_source_io_adapter",
                          replan_provider="only_when_native_algorithm_exposes_it",
                          acceptance="requires_native_and_runtime_evidence")
        elif record.get("decision_provider") == "benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter":
            record["acceptance"] = "legacy_diagnostic_only_not_paper_acceptance"
    return records
