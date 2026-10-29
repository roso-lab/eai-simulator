from copy import deepcopy

import pytest

from algorithm.heterocoord.registry import (
    IMPLEMENTATION_REGISTER_REQUIRED_FIELDS,
    PRIMARY_SYSTEM_COLUMNS,
    REGISTRY,
    primary_condition_record,
    primary_registry_records,
    primary_system_columns,
    primary_capability_matrix,
    validate_implementation_register,
    validate_primary_job_identity,
)


def test_registry_has_exact_ordered_six_primary_and_eight_additional_conditions():
    assert sum(item["paper_scope"] == "table_3_4_primary" for item in REGISTRY) == 6
    assert sum(item["paper_scope"] == "additional_candidate" for item in REGISTRY) == 8
    assert sum(item["paper_scope"] == "existing_eai_extra" for item in REGISTRY) == 0
    assert primary_system_columns() == PRIMARY_SYSTEM_COLUMNS
    assert tuple(item["algorithm_id"] for item in primary_registry_records()) == PRIMARY_SYSTEM_COLUMNS
    assert validate_implementation_register() == PRIMARY_SYSTEM_COLUMNS
def test_registry_matches_supplied_pdf_result_columns():
    assert {item["algorithm_id"] for item in REGISTRY if item["active_paper_matrix"]} == {
        "FMC-ATA", "BT-capabilities", "SADCHER", "HARL-HAPPO",
        "TeamWeaver-adapted-to-EAI", "EMOS",
    }
    assert sum(item["paper_scope"] == "table_3_4_primary" for item in REGISTRY) == 6


def test_primary_capability_matrix_is_complete_and_does_not_promote_pending_conditions():
    rows = primary_capability_matrix()
    assert tuple(row["algorithm_id"] for row in rows) == PRIMARY_SYSTEM_COLUMNS
    assert all(row["relation_signature"] and row["p3_subtype"] for row in rows)
    assert all(row["status"] == "pending" for row in rows)
    assert all(row["information_rights_group"].startswith("N/A_pending") for row in rows)
    assert all(set(row["fixture_requirements"]) == {
        "native_decision_only", "input_rejection", "execution_evidence_boundary"
    } for row in rows)
    assert all(row["cross_algorithm_comparison_status"].startswith("pending_") for row in rows)


def test_registry_preserves_missing_artifact_boundaries():
    by_id = {item["algorithm_id"]: item for item in REGISTRY}
    assert by_id["Qrainbow"]["official_repo"] is None
    assert by_id["Qrainbow"]["paper_scope"] == "additional_candidate"
    assert by_id["HARL-HAPPO"]["checkpoint"] == "explicit_compatible_manifest_or_checkpoint_required"
    assert by_id["HARL-HAPPO"]["implementation"] == "original_source_io_adapter"
    assert "runtime_acceptance" in by_id["HARL-HAPPO"]["status"]
    assert by_id["HARL-HAPPO"]["historical_registration"]["status"].startswith("N/A_")
    assert by_id["LiP-LLM-adapted-to-EAI"]["implementation"] == "paper_derived_adapter_not_reproduction"
    assert by_id["LiP-LLM-adapted-to-EAI"]["status"].startswith("N/A_")
    assert by_id["MRTA-heuristic-adapted-to-EAI"]["source_commit"] == "c69509e8d2370f55824519dda0a13252e1817415"
    assert by_id["Hybrid-SUSD-MRTA-adapted-to-EAI"]["source_commit"] == "52473fe16f606dca6e6b3bcc99f3f164afdbe6ee"
    assert by_id["HeteroMRTA-adapted-to-EAI"]["source_commit"] == "db51e29535e34bcaa8cb75f70be3fd28b0027988"
    assert by_id["TeamWeaver-adapted-to-EAI"]["paper_scope"] == "table_3_4_primary"
    assert "persistent_semantic_feedback_not_accepted" in by_id["TeamWeaver-adapted-to-EAI"]["unsupported_dimensions"]
    assert "coalition_dispatch" in by_id["TeamWeaver-adapted-to-EAI"]["unsupported_dimensions"]


def test_registry_records_system_layer_contract():
    for item in REGISTRY:
        assert item["layer"] == "system"
        assert item["input_contract"].startswith("EaiTaskPackage")
        assert "AlgorithmDecision" in item["output_contract"]
        assert item["executor_owner"].startswith("task.executors")
        assert item["published_runtime_readiness"] == "not_established_by_registry"


def test_implementation_register_fields_are_explicit_and_do_not_imply_qualification():
    for item in REGISTRY:
        assert all(field in item for field in IMPLEMENTATION_REGISTER_REQUIRED_FIELDS)
        assert item["published_runtime_readiness"] == "not_established_by_registry"
    teamweaver = next(
        item for item in primary_registry_records()
        if item["algorithm_id"] == "TeamWeaver-adapted-to-EAI"
    )
    assert "authorized_compatibility_recorded" in teamweaver["changed_components"]
    assert teamweaver["historical_condition_registration"]["changed_components"].endswith("TeamWeaver_algorithm_unchanged")
    assert teamweaver["budget_accounting"].startswith("required_not_yet_bound")
    assert teamweaver["world_graph"] == "public_task_package_and_explicit_method_observation"
    assert teamweaver["registration_status"] == "bounded_adapter_connected"
    assert teamweaver["comparison_condition_status"] == "pending"


def test_primary_condition_identity_fails_closed_on_mismatch():
    record = primary_registry_records()[4]
    assert primary_condition_record(
        record["job_system_id"],
        record["comparison_condition_id"],
        record["information_rights_group"],
    )["algorithm_id"] == "TeamWeaver-adapted-to-EAI"
    for values in (
        ("Qrainbow", record["comparison_condition_id"], record["information_rights_group"]),
        (record["job_system_id"], "unregistered-condition", record["information_rights_group"]),
        (record["job_system_id"], record["comparison_condition_id"], "other-rights"),
    ):
        with pytest.raises(ValueError, match="unregistered primary"):
            primary_condition_record(*values)


def test_paper_job_identity_validator_is_opt_in_and_current_conditions_stay_incomplete():
    record = primary_registry_records()[4]
    metadata = {
        name: record[name]
        for name in (
            "comparison_condition_id", "information_rights_group",
            "code_version", "source_version", "model_version", "adapter_version",
        )
    }
    job = {"system_id": record["job_system_id"], "metadata": metadata}
    assert validate_primary_job_identity(job, require_complete=False)["algorithm_id"] == record["algorithm_id"]
    with pytest.raises(ValueError, match="condition is incomplete"):
        validate_primary_job_identity(job)
    with pytest.raises(ValueError, match="not a primary registered"):
        validate_primary_job_identity({"system_id": "emos-zero", "metadata": metadata})
    with pytest.raises(ValueError, match="not a primary registered"):
        validate_primary_job_identity({"system_id": "Qrainbow", "metadata": metadata}, require_complete=False)


def test_implementation_register_rejects_missing_fields_and_roster_drift():
    rows = [deepcopy(item) for item in REGISTRY]
    del rows[0]["adapter_version"]
    with pytest.raises(ValueError, match="missing fields"):
        validate_implementation_register(tuple(rows))

    rows = [deepcopy(item) for item in REGISTRY]
    rows[4]["paper_scope"] = "additional_candidate"
    with pytest.raises(ValueError, match="manuscript column order"):
        validate_implementation_register(tuple(rows))
def test_active_records_do_not_inherit_wrong_historical_sources_or_scope():
    by_id = {item["algorithm_id"]: item for item in REGISTRY}
    fmc = by_id["FMC-ATA"]
    assert "Simulation_For_Research" in fmc["source_reference"]
    assert fmc["source_commit"] in fmc["source_reference"]
    teamweaver = by_id["TeamWeaver-adapted-to-EAI"]
    assert "paper_table_3_4_primary_comparison" not in teamweaver["unsupported_dimensions"]
    assert "persistent_semantic_feedback_not_accepted" in teamweaver["unsupported_dimensions"]
