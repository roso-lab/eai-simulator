"""Provenance registry for the latest HeteroCoord paper and extra candidates.

The registry is deliberately descriptive.  It does not turn an assignment
into a physical success, and it records missing source/checkpoint evidence as
``None``/``N/A`` so benchmark reports cannot silently upgrade an adapter.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


PRIMARY_SYSTEM_COLUMNS: tuple[str, ...] = (
    "FMC-ATA",
    "BT-capabilities",
    "Gosrich-et-al",
    "HARL-HAPPO",
    "TeamWeaver-adapted-to-EAI",
    "EMOS",
)

IMPLEMENTATION_REGISTER_REQUIRED_FIELDS: tuple[str, ...] = (
    "algorithm_id",
    "code_version",
    "source_version",
    "model_version",
    "adapter_version",
    "cognition_configuration",
    "training_access",
    "runtime_information",
    "supplied_decomposition",
    "supported_task_subset",
    "supported_disturbances",
    "comparison_group",
    "comparison_condition_id",
    "information_rights",
    "information_rights_group",
    "developer_relationship",
    "changed_components",
    "actor_identities",
    "action_encoding",
    "admissible_task_subset",
    "semantic_model",
    "world_graph",
    "solver",
    "replanning_policy",
    "budget_accounting",
    "registration_status",
    "paper_column_coverage_status",
    "comparison_condition_status",
)


_RAW_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "algorithm_id": "FMC-ATA",
        "paper_scope": "table_3_4_primary",
        "source_reference": "IJCAI 2023 / 10.24963/ijcai.2023/30",
        "official_repo": "https://github.com/benrachmut/FisherMarketSimulator",
        "source_commit": "fe61273463d06970d2eb019150900f33f77f64a5",
        "license": "unresolved_in_repo",
        "implementation": "original_source_isolated; fire_rescue_typed_bridge",
        "execution_scope": "asynchronous_fisher_market_allocation",
        "model_or_api": "classical_market_clearing",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["communication_delay_loss", "task_arrival", "heterogeneous_skills"],
        "unsupported_dimensions": ["Isaac_physical_execution_until_adapter"],
        "physical_executor_owner": "eai_runtime",
        "status": "source_pulled_legacy_scenario_bridge_only; indivisible_condition_N/A",
    },
    {
        "algorithm_id": "BT-capabilities",
        "paper_scope": "table_3_4_primary",
        "source_reference": "ICRA 2024 / 10.1109/ICRA57147.2024.10611016 / arXiv:2402.02833",
        "official_repo": "https://github.com/fzi-forschungszentrum-informatik/ros_bt_py",
        "source_commit": "bc000c3ec7c15ca9565318746a8e44aff7945ba1",
        "license": "repo_license_to_review",
        "implementation": "original_ros_bt_py_foundation; fire_rescue_typed_bridge",
        "execution_scope": "behavior_tree_capability_auction",
        "model_or_api": "classical_bt_and_auction",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["capability_auction", "mission_tree", "member_failure"],
        "unsupported_dimensions": ["ROS1_catkin_to_Isaac5_bridge", "physical_evidence_until_adapter"],
        "physical_executor_owner": "eai_runtime",
        "status": "source_pulled_legacy_scenario_bridge_only",
    },
    {
        "algorithm_id": "Gosrich-et-al",
        "paper_scope": "table_3_4_primary",
        "source_reference": "T-RO 2025 / arXiv:2509.15052",
        "official_repo": None,
        "source_commit": None,
        "license": "not_available",
        "implementation": "reference_only_no_official_source",
        "execution_scope": "coalition_flow_precedence_online_replanning",
        "model_or_api": "network_flow_or_mip",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["precedence", "coalition", "online_reallocation"],
        "unsupported_dimensions": ["original_code_unavailable", "Isaac_physical_execution"],
        "physical_executor_owner": "eai_runtime",
        "status": "N/A_official_reproduction_unavailable",
    },
    {
        "algorithm_id": "HARL-HAPPO",
        "paper_scope": "table_3_4_primary",
        "source_reference": "JMLR 2024 / HARL",
        "official_repo": "https://github.com/PKU-MARL/HARL",
        "source_commit": "b1af98b0dbab72a2eee9d160751cd09aedbb8ce2",
        "license": "repo_license_to_review",
        "implementation": "official_source_reference_only_no_in_tree_EAI_adapter",
        "execution_scope": "heterogeneous_ctde_policy_training_or_inference",
        "model_or_api": "HAPPO_sequential_actor_updates",
        "checkpoint": "N/A_no_verifiable_official_HAPPO_checkpoint",
        "training_access": "official_weights_N/A; any_self_training_is_a_separate_condition",
        "supported_primitives": ["heterogeneous_observation_action", "fixed_actor_identity"],
        "unsupported_dimensions": ["in_tree_EAI_adapter", "official_weight_evaluation", "physical_success_without_checkpoint"],
        "physical_executor_owner": "eai_runtime",
        "status": "N/A_no_in_tree_EAI_adapter_or_verifiable_official_HAPPO_weights",
    },
    {
        "algorithm_id": "Qrainbow",
        "paper_scope": "additional_candidate",
        "source_reference": "RA-L 2024 / 10.1109/LRA.2023.3328448",
        "official_repo": None,
        "source_commit": None,
        "license": "not_available",
        "implementation": "paper_reference_only",
        "execution_scope": "asynchronous_macro_action_q_value_mixing",
        "model_or_api": "Qatten_plus_Rainbow_components",
        "checkpoint": "N/A_no_public_official_checkpoint",
        "training_access": "requires_original_training_environment_and_weights",
        "supported_primitives": ["macro_action", "asynchronous_execution"],
        "unsupported_dimensions": ["faithful_EAI_inference", "physical_success"],
        "physical_executor_owner": "eai_runtime",
        "status": "N/A_until_official_code_and_weights",
    },
    {
        "algorithm_id": "EMOS",
        "paper_scope": "table_3_4_primary",
        "source_reference": "ICLR 2025 / EMOS",
        "official_repo": "https://github.com/SgtVincent/EMOS",
        "source_commit": "e9501db45d634b087bf5d1a14228266685e8feeb",
        "license": "repo_license_to_review",
        "implementation": "EMOS-adapted-to-EAI",
        "execution_scope": "embodiment_aware_llm_group_planning",
        "model_or_api": "declared_LLM_endpoint_or_local_model",
        "checkpoint": "not_applicable_no_RL_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["heterogeneous_navigation", "assignment", "group_planning"],
        "unsupported_dimensions": ["ideal_geometry", "disabled_physics", "D2_D4_D5_until_interventions"],
        "physical_executor_owner": "eai_runtime",
        "status": "existing_adapted_assignment_condition",
    },
    {
        "algorithm_id": "COHERENT-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "ICRA 2025 / 10.1109/ICRA55743.2025.11127808 / arXiv:2409.15146",
        "official_repo": "https://github.com/MrKeee/COHERENT",
        "source_commit": "17554a52792e302921bbb5e5fd3b4049e25b50d4",
        "license": "unresolved_in_repo",
        "implementation": "adapted",
        "execution_scope": "centralized_PEFA_assignment",
        "model_or_api": "LLM_prompted_PEFA_or_deterministic_fixture",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["heterogeneous_capability_assignment", "feedback_adjustment"],
        "unsupported_dimensions": ["serial_physics_loop", "D2_D4_D5_without_registered_evidence"],
        "physical_executor_owner": "eai_runtime",
        "status": "legacy_scenario_bridge_unit_tested; generic_runtime_readiness_N/A",
    },
    {
        "algorithm_id": "LiP-LLM-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "RA-L 2025 / 10.1109/LRA.2024.3518105 / arXiv:2410.21040",
        "official_repo": None,
        "source_commit": None,
        "license": "not_available",
        "implementation": "paper_derived_adapter_not_reproduction",
        "execution_scope": "skill_list_dependency_DAG_linear_assignment",
        "model_or_api": "optional_LLM_skill_and_dependency_generators",
        "checkpoint": "N/A_no_author_code_or_checkpoint",
        "training_access": "not_applicable",
        "supported_primitives": ["precedence", "heterogeneous_capability_assignment", "parallel_ready_tasks"],
        "unsupported_dimensions": ["original_AWS_RoboMaker_result", "physical_success_without_EAI_skill_adapter"],
        "physical_executor_owner": "eai_runtime",
        "status": "N/A_no_official_code_reproduction_not_started",
    },
    {
        "algorithm_id": "Robust-Online-Epistemic-Replanning-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "IROS 2024 / 10.1109/IROS58592.2024.10802214 / arXiv:2403.00641",
        "official_repo": "https://github.com/laurenbramblett/mrs_online_epistemic_replanning",
        "source_commit": "8112a81cc4edb76b916b3dbc31f712aaafd0102d",
        "license": "unresolved_in_repo",
        "implementation": "paper_derived_wrapper_not_reproduction",
        "execution_scope": "communication_loss_and_failure_replanning",
        "model_or_api": "deterministic_belief_ledger_optional_MCTS_hook",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["communication_loss", "rendezvous", "member_exit", "online_reallocation"],
        "unsupported_dimensions": ["MATLAB_aerial_dynamics", "D3_D5_without_EAI_executor"],
        "physical_executor_owner": "eai_runtime",
        "status": "N/A_no_official_code_reproduction_not_started",
    },
    {
        "algorithm_id": "MRTA-heuristic-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "ICRA 2024 / 10.1109/ICRA57147.2024.10611147; IEEE T-RO 2025 / 10.1109/TRO.2025.3626651",
        "official_repo": "https://github.com/multirobot-use/mrta_heuristic_planner",
        "source_commit": "c69509e8d2370f55824519dda0a13252e1817415",
        "license": "MIT",
        "implementation": "official_source_equivalent_python_adapter",
        "execution_scope": "heterogeneous_task_allocation_and_delay_repair",
        "model_or_api": "deterministic_heuristic_no_learned_weights",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["heterogeneous_capability", "battery_cost", "priority", "delay_repair"],
        "unsupported_dimensions": ["official_matlab_zero_modification_execution", "coalition_tasks", "navigation_manipulation_physics"],
        "physical_executor_owner": "eai_runtime",
        "status": "historical_scenario_smoke_only; current_published_runtime_readiness_N/A",
    },
    {
        "algorithm_id": "TeamWeaver-adapted-to-EAI",
        "paper_scope": "table_3_4_primary",
        "source_reference": "TeamWeaver paper and existing EAI benchmark integration",
        "official_repo": None,
        "source_commit": None,
        "license": "workspace_existing_code",
        "implementation": "existing_adapted_eai",
        "execution_scope": "LLM_task_decomposition_MIQP_allocation_replanning",
        "model_or_api": "LLM_optional_plus_Gurobi",
        "checkpoint": "not_applicable_classical_planner",
        "training_access": "not_applicable",
        "supported_primitives": ["heterogeneous_capability", "precedence", "replanning"],
        "unsupported_dimensions": ["nonempty_history_reconstruction", "task_graph_prerequisites", "physical_qualification"],
        "physical_executor_owner": "eai_runtime",
        "status": "existing_adapter_ready",
    },
    {
        "algorithm_id": "Hybrid-SUSD-MRTA-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "ICRA 2023 / 10.1109/ICRA48891.2023.10161349",
        "official_repo": "https://github.com/GeorgiaTechSystemResearch/Hybrid_SUSD_MRTA_public",
        "source_commit": "52473fe16f606dca6e6b3bcc99f3f164afdbe6ee",
        "license": "MIT",
        "implementation": "official_matlab_interface_equivalent_python_adapter",
        "execution_scope": "heterogeneous_market_initialization_and_susd_allocation",
        "model_or_api": "classical_market_and_derivative_free_search",
        "checkpoint": "not_applicable_no_learned_weights",
        "training_access": "not_applicable",
        "supported_primitives": ["specialization_Q", "task_types_Y", "market_initialization", "susd_local_search"],
        "unsupported_dimensions": ["native_matlab_zero_modification_execution", "native_failure_replanning", "navigation_manipulation_physics"],
        "physical_executor_owner": "eai_runtime",
        "status": "historical_scenario_smoke_only; current_published_runtime_readiness_N/A; native_failure_replanning_N/A",
    },
    {
        "algorithm_id": "DCMRTA-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "ICRA 2024 / 10.1109/ICRA57147.2024.10611244",
        "official_repo": "https://github.com/marmotlab/DCMRTA",
        "source_commit": "029aeb81c28a38ecd69667153223e4aa740e01dd",
        "license": "MIT",
        "implementation": "official_checkpoint_native_descriptor_plus_fixed_dispatch_adapter",
        "execution_scope": "native_checkpoint_decision_only_and_EAI_execution_adapter",
        "model_or_api": "official_REINFORCE_checkpoint_when_available",
        "checkpoint": "model/REINFORCE/checkpoint.pth; official checkpoint verified",
        "training_access": "no_EAI_retraining",
        "supported_primitives": ["coalition_task_decision", "native_checkpoint_inference", "EAI_feedback_ledger"],
        "unsupported_dimensions": ["zero_shot_Fire_Rescue_checkpoint_validity", "4_agent_4_task_native_input", "Z1_manipulation_physics"],
        "physical_executor_owner": "eai_runtime",
        "status": "native_decision_only; legacy fixed-default scenario bridge; current runtime and cross_domain_generalization_N/A",
    },
    {
        "algorithm_id": "HeteroMRTA-adapted-to-EAI",
        "paper_scope": "additional_candidate",
        "source_reference": "IEEE RA-L 2025 / 10.1109/LRA.2025.3534682",
        "official_repo": "https://github.com/marmotlab/HeteroMRTA",
        "source_commit": "db51e29535e34bcaa8cb75f70be3fd28b0027988",
        "license": "repo_license_to_review",
        "implementation": "official_checkpoint_native_descriptor_plus_fixed_dispatch_adapter",
        "execution_scope": "native_checkpoint_decision_only_and_EAI_execution_adapter",
        "model_or_api": "official_REINFORCE_checkpoint_when_available",
        "checkpoint": "model/save/checkpoint.pth; official checkpoint path recorded",
        "training_access": "no_EAI_retraining",
        "supported_primitives": ["heterogeneous_traits", "coalition_task_scheduling", "native_checkpoint_inference", "EAI_feedback_ledger"],
        "unsupported_dimensions": ["zero_shot_Fire_Rescue_checkpoint_validity", "4_agent_4_task_native_input", "Z1_manipulation_physics"],
        "physical_executor_owner": "eai_runtime",
        "status": "native_decision_only; legacy fixed-default scenario bridge; current runtime and cross_domain_generalization_N/A",
    },
)


_SYSTEM_ROLES: dict[str, str] = {
    "FMC-ATA": "market_based_fractional_allocation",
    "BT-capabilities": "capability_auction_and_tree_dispatch",
    "Gosrich-et-al": "coalition_flow_precedence_replanning",
    "HARL-HAPPO": "learned_policy_decision_only",
    "Qrainbow": "asynchronous_macro_action_policy",
    "EMOS": "discussion_and_task_allocation",
    "COHERENT-adapted-to-EAI": "centralized_capability_assignment_feedback",
    "LiP-LLM-adapted-to-EAI": "skill_dependency_dag_assignment",
    "Robust-Online-Epistemic-Replanning-adapted-to-EAI": "belief_and_failure_replanning",
    "MRTA-heuristic-adapted-to-EAI": "heuristic_assignment_and_delay_repair",
    "TeamWeaver-adapted-to-EAI": "task_decomposition_allocation_replanning",
    "Hybrid-SUSD-MRTA-adapted-to-EAI": "market_initialization_and_susd_search",
    "DCMRTA-adapted-to-EAI": "checkpoint_coalition_decision",
    "HeteroMRTA-adapted-to-EAI": "checkpoint_heterogeneous_coalition_decision",
}


_PRIMARY_REGISTER_OVERRIDES: dict[str, dict[str, Any]] = {
    "FMC-ATA": {
        "job_system_id": "FMC-ATA",
        "adapter_version": "N/A_no_current_generic_EAI_adapter",
        "supplied_decomposition": "not_applicable_system_owned_market_tasks",
        "comparison_group": "complete_coordination_system",
        "information_rights_group": "N/A_pending_frozen_information_rights",
        "information_rights": "N/A_pending_frozen_information_rights",
        "comparison_condition_id": "paper-condition.v1::complete-coordination",
        "registration_status": "source_only",
        "paper_column_coverage_status": "legacy_scenario_only",
        "comparison_condition_status": "pending",
    },
    "BT-capabilities": {
        "job_system_id": "BT-capabilities",
        "adapter_version": "N/A_no_current_generic_EAI_adapter",
        "supplied_decomposition": "required_registered_mission_tree",
        "comparison_group": "methods_given_task_decomposition",
        "information_rights_group": "N/A_pending_frozen_information_rights",
        "information_rights": "N/A_pending_frozen_information_rights",
        "comparison_condition_id": "paper-condition.v1::supplied-decomposition",
        "registration_status": "source_only",
        "paper_column_coverage_status": "legacy_scenario_only",
        "comparison_condition_status": "pending",
    },
    "Gosrich-et-al": {
        "job_system_id": "Gosrich-et-al",
        "adapter_version": "N/A_official_source_unavailable",
        "supplied_decomposition": "N/A_pending_reproduction",
        "comparison_group": "complete_coordination_system",
        "information_rights_group": "N/A_pending_frozen_information_rights",
        "information_rights": "N/A_pending_frozen_information_rights",
        "comparison_condition_id": "paper-condition.v1::complete-coordination",
        "registration_status": "unavailable",
        "paper_column_coverage_status": "none",
        "comparison_condition_status": "pending",
    },
    "HARL-HAPPO": {
        "job_system_id": "HARL-HAPPO",
        "adapter_version": "N/A_no_in_tree_EAI_adapter",
        "supplied_decomposition": "not_applicable_policy_owned",
        "comparison_group": "heterogeneous_learned_policy",
        "information_rights_group": "N/A_pending_frozen_information_rights",
        "information_rights": "N/A_pending_frozen_information_rights",
        "comparison_condition_id": "paper-condition.v1::heterogeneous-learned-policy",
        "registration_status": "unavailable",
        "paper_column_coverage_status": "none",
        "comparison_condition_status": "pending",
    },
    "TeamWeaver-adapted-to-EAI": {
        "job_system_id": "TeamWeaver-adapted-to-EAI",
        "source_version": "workspace_source_release_digest_required",
        "model_version": "not_applicable_classical_planner",
        "adapter_version": "benchmark.teamweaver.contract-adapter.v1",
        "cognition_configuration": "optional_LLM_semantic_decomposition_plus_DynamicFactoryAllocator",
        "runtime_information": "CPU_Gurobi_or_SciPy_allocator; physical_runtime_not_qualified",
        "supplied_decomposition": "system_generated_when_enabled; registered_task_package_otherwise",
        "supported_task_subset": ["bounded_allocation_without_prerequisites"],
        "supported_disturbances": ["N/A_nonempty_history_reconstruction_rejected"],
        "comparison_group": "complete_coordination_system",
        "information_rights_group": "N/A_pending_frozen_information_rights",
        "information_rights": "N/A_pending_frozen_information_rights",
        "comparison_condition_id": "paper-condition.v1::complete-coordination",
        "registration_status": "bounded_adapter_connected",
        "paper_column_coverage_status": "bounded_no_history_or_prerequisites",
        "comparison_condition_status": "pending",
        "changed_components": "uniform_contract_input_output_adapter_only; TeamWeaver_algorithm_unchanged",
        "actor_identities": "EAI_robot_id_preserved_as_TeamWeaver_RobotState.name",
        "action_encoding": "typed_task_id_to_robot_name_assignment",
        "admissible_task_subset": "no_prerequisite_edges; empty_H_r_initialization_only",
        "semantic_model": "FactoryTaskSpec_required_capabilities_duration_priority",
        "world_graph": "N/A_not_consumed_by_contract_adapter",
        "solver": "DynamicFactoryAllocator_Gurobi_with_SciPy_fallback",
        "replanning_policy": "N/A_not_exposed_by_contract_adapter",
        "budget_accounting": "required_not_yet_bound_to_formal_computation_and_message_budgets",
    },
    "EMOS": {
        "job_system_id": "EMOS-adapted-to-EAI",
        "adapter_version": "benchmark.emos.v1",
        "supplied_decomposition": "system_generated_embodiment_aware_plan",
        "comparison_group": "privileged_reference",
        "information_rights_group": "N/A_pending_ideal_geometry_rights_registration",
        "information_rights": "N/A_pending_ideal_geometry_rights_registration",
        "comparison_condition_id": "paper-condition.v1::privileged-reference",
        "registration_status": "bounded_adapter_connected",
        "paper_column_coverage_status": "bounded_zero_system",
        "comparison_condition_status": "pending",
    },
}


def _annotate_system_record(record: dict[str, Any]) -> dict[str, Any]:
    """Add the layer contract without changing source/checkpoint metadata."""

    result = dict(record)
    algorithm_id = str(result["algorithm_id"])
    result.update(
        {
            "layer": "system",
            "role": _SYSTEM_ROLES.get(algorithm_id, "system_coordination"),
            "input_contract": (
                "EaiTaskPackage + RobotState + symbolic_world_state + EventLedger"
            ),
            "output_contract": (
                "AlgorithmDecision(assignments, unassigned_tasks, provenance)"
            ),
            "executor_owner": "task.executors -> EAI controller/skill",
            "physical_success_owner": "task.evaluators -> EAI runtime",
            "published_runtime_readiness": "not_established_by_registry",
            "job_system_id": algorithm_id,
            "code_version": str(result.get("source_commit") or "workspace_release_digest_required"),
            "source_version": str(result.get("source_commit") or "N/A_no_registered_source_version"),
            "model_version": str(result.get("checkpoint") or "N/A_no_registered_model_version"),
            "adapter_version": "N/A_adapter_version_not_registered",
            "cognition_configuration": str(result.get("model_or_api") or "N/A_not_registered"),
            "runtime_information": "N/A_runtime_not_qualified",
            "supplied_decomposition": "N/A_not_registered",
            "supported_task_subset": list(result.get("supported_primitives") or ["N/A_not_registered"]),
            "supported_disturbances": ["N/A_not_registered"],
            "comparison_group": "additional_candidate_not_in_primary_panel",
            "comparison_condition_id": f"paper-condition.v1::{algorithm_id}",
            "information_rights": "N/A_not_registered",
            "information_rights_group": "N/A_not_registered",
            "developer_relationship": "N/A_not_registered",
            "changed_components": "N/A_not_registered",
            "actor_identities": "N/A_not_registered",
            "action_encoding": "N/A_not_registered",
            "admissible_task_subset": "N/A_not_registered",
            "semantic_model": "N/A_not_registered",
            "world_graph": "N/A_not_registered",
            "solver": "N/A_not_registered",
            "replanning_policy": "N/A_not_registered",
            "budget_accounting": "N/A_not_registered",
            "registration_status": "candidate_inventory_only",
            "paper_column_coverage_status": "none",
            "comparison_condition_status": "not_applicable",
        }
    )
    result.update(_PRIMARY_REGISTER_OVERRIDES.get(algorithm_id, {}))
    return result


# Keep the public tuple name used by existing tests and manifests, while making
# the system/skill boundary machine-readable for new tooling.
_ANNOTATED_REGISTRY = tuple(_annotate_system_record(item) for item in _RAW_REGISTRY)
_REGISTRY_BY_ID = {item["algorithm_id"]: item for item in _ANNOTATED_REGISTRY}
REGISTRY: tuple[dict[str, Any], ...] = tuple(
    _REGISTRY_BY_ID[algorithm_id] for algorithm_id in PRIMARY_SYSTEM_COLUMNS
) + tuple(
    item for item in _ANNOTATED_REGISTRY
    if item["algorithm_id"] not in PRIMARY_SYSTEM_COLUMNS
)


def primary_system_columns() -> tuple[str, ...]:
    """Return the paper's exact, ordered six result columns."""

    return PRIMARY_SYSTEM_COLUMNS


def primary_registry_records() -> tuple[dict[str, Any], ...]:
    """Return defensive copies in manuscript column order."""

    return tuple(dict(_REGISTRY_BY_ID[item]) for item in PRIMARY_SYSTEM_COLUMNS)


def _registered_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    return value is not None


def validate_implementation_register(
    records: Iterable[Mapping[str, Any]] = REGISTRY,
) -> tuple[str, ...]:
    """Validate exact primary membership and the paper-required register fields."""

    rows = tuple(dict(item) for item in records)
    ids = tuple(str(item.get("algorithm_id", "")) for item in rows)
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("implementation register requires unique algorithm_id values")
    for item in rows:
        missing = [
            name for name in IMPLEMENTATION_REGISTER_REQUIRED_FIELDS
            if name not in item or not _registered_value(item[name])
        ]
        if missing:
            raise ValueError(
                f"implementation register {item['algorithm_id']} has missing fields: {missing}"
            )
        if item.get("published_runtime_readiness") != "not_established_by_registry":
            raise ValueError("implementation registry cannot establish runtime readiness")
    primary = tuple(
        item["algorithm_id"] for item in rows
        if item.get("paper_scope") == "table_3_4_primary"
    )
    if primary != PRIMARY_SYSTEM_COLUMNS:
        raise ValueError("primary implementation register must match manuscript column order")
    return primary


def primary_condition_record(
    system_id: str,
    comparison_condition_id: str,
    information_rights_group: str,
) -> dict[str, Any]:
    """Resolve one paper-rankable job/baseline identity or fail closed."""

    matches = [
        item for item in primary_registry_records()
        if item["job_system_id"] == system_id
        and item["comparison_condition_id"] == comparison_condition_id
        and item["information_rights_group"] == information_rights_group
    ]
    if len(matches) != 1:
        raise ValueError("unregistered primary comparison identity")
    return matches[0]


def implementation_condition_complete(record: Mapping[str, Any]) -> bool:
    """Return whether a register row is complete enough for formal evidence."""

    unresolved_tokens = (
        "N/A", "pending", "not_registered", "release_digest_required",
        "not_yet", "not_qualified",
    )

    def resolved(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip()) and not any(token in value for token in unresolved_tokens)
        if isinstance(value, (list, tuple)):
            return bool(value) and all(resolved(item) for item in value)
        return value is not None

    return bool(
        record.get("registration_status") == "complete"
        and record.get("paper_column_coverage_status") == "full"
        and record.get("comparison_condition_status") == "frozen"
        and all(resolved(record.get(name)) for name in IMPLEMENTATION_REGISTER_REQUIRED_FIELDS)
    )


def validate_primary_job_identity(
    job: Mapping[str, Any] | Any,
    *,
    records: Iterable[Mapping[str, Any]] = REGISTRY,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Validate paper-job identity without constraining diagnostic BranchJob use."""

    rows = tuple(dict(item) for item in records)
    validate_implementation_register(rows)
    system_id = job.get("system_id") if isinstance(job, Mapping) else getattr(job, "system_id", None)
    metadata = job.get("metadata") if isinstance(job, Mapping) else getattr(job, "metadata", None)
    if not isinstance(metadata, Mapping):
        raise ValueError("paper job requires implementation identity metadata")
    matches = [
        item for item in rows
        if item.get("paper_scope") == "table_3_4_primary"
        and item.get("job_system_id") == system_id
    ]
    if len(matches) != 1:
        raise ValueError("paper job system_id is not a primary registered implementation")
    record = matches[0]
    bindings = {
        "comparison_condition_id": "comparison_condition_id",
        "information_rights_group": "information_rights_group",
        "code_version": "code_version",
        "source_version": "source_version",
        "model_version": "model_version",
        "adapter_version": "adapter_version",
    }
    for metadata_name, record_name in bindings.items():
        if metadata.get(metadata_name) != record.get(record_name):
            raise ValueError(f"paper job {metadata_name} does not match implementation register")
    if require_complete and not implementation_condition_complete(record):
        raise ValueError("paper job implementation condition is incomplete")
    return dict(record)


def registry_records() -> list[dict[str, Any]]:
    return [dict(item) for item in REGISTRY]


def write_registry(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(registry_records(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
