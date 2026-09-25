"""Contract v4 schema, explicit migration, sealing, Q(t) wiring and CS gate.

Batch 2A acceptance for ``demo/benchmark/goal_contract_v4.py``:

* fail-closed closed-schema validation of the four v4 addon fields
  (exact field sets, nonnegative weights with positive total, protocol
  aggregation enum, one-to-one ``outcome_ref`` mapping onto
  ``required_outcomes``);
* the v3->v4 migrator only succeeds with a complete explicit author spec
  per outcome - no semantic defaults, no provisional artifacts;
* sealing detects tampering, and the pre-v4 reader keeps rejecting v4
  payloads (v3 reads v4 fail-closed);
* ``active_milestones_for_progress`` feeds the batch-1 curve builder end to
  end with equal and explicit weights;
* the CS gate has exactly three states and never claims scoreability.
"""

from copy import deepcopy
import functools

import pytest

from demo.benchmark.goal_contract_v4 import (
    AUTHOR_REQUIRED_SPEC_FIELDS,
    CONTRACT_V4_RELEASE,
    CONTRACT_V4_VERSION,
    CS_GATE_DEFERRED_STATUS,
    CS_GATE_NO_COMMITTED_PLAN_STATUS,
    CS_GATE_NO_CONTRACT_STATUS,
    active_milestones_for_progress,
    cs_vector_gate,
    freeze_contract_v4,
    migrate_v3_to_contract_v4,
    validate_contract_v4,
    validate_frozen_v4,
)
from demo.benchmark.goal_driven_contract import validate_goal_task_spec
from demo.benchmark.goal_progress_curve import build_progress_curve
from demo.benchmark.goal_protocol_deployment import (
    NOT_APPLICABLE_UNTIL_CONTRACT_V4,
)
from demo.benchmark.provenance import canonical_sha256


FACTORY_OUTCOMES = ("anomaly_confirmed", "response_area_served",
                    "maintenance_handoff_recorded")


@functools.lru_cache(maxsize=1)
def _factory_public_cached():
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    return build_goal_task_package("factory")["public_task"]


def _v3_task():
    return deepcopy(_factory_public_cached())


def _full_specs(**overrides):
    specs = {}
    for outcome_id in FACTORY_OUTCOMES:
        specs[outcome_id] = {
            "weight": 1.0,
            "activation_rule": "episode_start_active",
            "stability_duration_s": 5.0,
            "monotonicity": "monotonic",
            "anti_double_count_rule": "first_external_satisfaction_only",
        }
    specs.update(overrides)
    return specs


def _migrated(task=None, specs=None):
    return migrate_v3_to_contract_v4(
        _v3_task() if task is None else task,
        milestone_specs=_full_specs() if specs is None else specs)


def _capability_constraint(milestone_id, **overrides):
    row = {
        "constraint_id": "cap-anomaly-lift",
        "milestone_id": milestone_id,
        "capability_type": "lift_kg",
        "threshold": 10.0,
        "tolerance": "unit=kg; numeric tolerance 0.0; boundary inclusive",
        "aggregation_rule": "coalition_sum",
        "activation_rule": "episode_start_active",
        "evidence_fields": ["committed_plan.capability_value",
                            "dispatch_log.robot_ids"],
    }
    row.update(overrides)
    return row


def _role_constraint(milestone_id, **overrides):
    row = {
        "constraint_id": "role-response-uav",
        "milestone_id": milestone_id,
        "required_role": "uav",
        "cardinality": {"count": 1, "comparator": "at_least"},
        "activation_rule": "episode_start_active",
        "evidence_fields": ["committed_plan.role_id", "platform_registry.role"],
    }
    row.update(overrides)
    return row


def _obs(milestone_id, at, stability=0.0):
    return {"milestone_id": milestone_id, "satisfied_at_s": at,
            "stability_duration_s": stability}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_v4_round_trip_after_migration_validates():
    report = validate_contract_v4(_migrated()["task_v4"])
    assert report["validated"] is True
    assert report["contract_version"] == CONTRACT_V4_VERSION == 4
    assert report["milestone_count"] == report["outcome_count"] == 3
    # Stable identity mapping: milestone ids default to the outcome ids.
    assert report["outcome_milestone_map"] == {
        outcome_id: outcome_id for outcome_id in FACTORY_OUTCOMES}
    assert report["total_milestone_weight"] == 3.0
    assert report["episode_start_active_milestone_count"] == 3
    assert report["base_task_sha256"] == canonical_sha256(_v3_task())


def test_v4_rejects_missing_milestone_field():
    task = _migrated()["task_v4"]
    del task["contract_milestones"][1]["stability_duration_s"]
    with pytest.raises(ValueError, match="stability_duration_s"):
        validate_contract_v4(task)


def test_v4_rejects_zero_total_milestone_weight():
    specs = _full_specs()
    for outcome_id in FACTORY_OUTCOMES:
        specs[outcome_id]["weight"] = 0.0
    with pytest.raises(ValueError, match="total contract milestone weight"):
        validate_contract_v4(migrate_v3_to_contract_v4(
            _v3_task(), milestone_specs=specs)["task_v4"])


def test_v4_rejects_negative_milestone_weight():
    specs = _full_specs()
    specs["anomaly_confirmed"]["weight"] = -0.5
    with pytest.raises(ValueError, match="weight.*nonnegative"):
        validate_contract_v4(migrate_v3_to_contract_v4(
            _v3_task(), milestone_specs=specs)["task_v4"])


def test_v4_rejects_unknown_aggregation_rule():
    task = _migrated()["task_v4"]
    milestone_id = task["contract_milestones"][0]["milestone_id"]
    task["capability_constraints"] = [
        _capability_constraint(milestone_id, aggregation_rule="sum")]
    with pytest.raises(ValueError, match="aggregation_rule"):
        validate_contract_v4(task)


def test_v4_rejects_dangling_outcome_ref():
    task = _migrated()["task_v4"]
    task["contract_milestones"][0]["outcome_ref"] = "ghost_outcome"
    with pytest.raises(ValueError, match="ghost_outcome"):
        validate_contract_v4(task)


def test_v4_rejects_two_milestones_for_one_outcome():
    task = _migrated()["task_v4"]
    task["contract_milestones"][1]["outcome_ref"] = (
        task["contract_milestones"][0]["outcome_ref"])
    with pytest.raises(ValueError, match="one-to-one"):
        validate_contract_v4(task)


def test_v4_accepts_explicit_constraints_and_reports_counts():
    task = _migrated()["task_v4"]
    milestone_id = task["contract_milestones"][0]["milestone_id"]
    task["capability_constraints"] = [_capability_constraint(milestone_id)]
    task["role_constraints"] = [_role_constraint(milestone_id)]
    report = validate_contract_v4(task)
    assert report["capability_constraint_count"] == 1
    assert report["role_constraint_count"] == 1
    # Protocol aggregation vocabulary is accepted as-is.
    task["capability_constraints"][0]["aggregation_rule"] = "single_actor"
    task["capability_constraints"].append(
        _capability_constraint(milestone_id, constraint_id="cap-2",
                               aggregation_rule="exists", threshold="has_arm"))
    assert validate_contract_v4(task)["capability_constraint_count"] == 2


def test_v4_rejects_constraint_referencing_unknown_milestone():
    task = _migrated()["task_v4"]
    task["role_constraints"] = [_role_constraint("ghost_milestone")]
    with pytest.raises(ValueError, match="ghost_milestone"):
        validate_contract_v4(task)


def test_v4_rejects_duplicate_constraint_ids_across_obligation_classes():
    task = _migrated()["task_v4"]
    milestone_id = task["contract_milestones"][0]["milestone_id"]
    task["capability_constraints"] = [_capability_constraint(milestone_id)]
    task["role_constraints"] = [
        _role_constraint(milestone_id, constraint_id="cap-anomaly-lift")]
    with pytest.raises(ValueError, match="globally unique"):
        validate_contract_v4(task)


# ---------------------------------------------------------------------------
# Explicit migration
# ---------------------------------------------------------------------------


def test_migration_succeeds_with_provenance_and_preserved_base():
    source = _v3_task()
    result = _migrated(task=source)
    task_v4 = result["task_v4"]
    provenance = result["provenance"]
    # Provenance binds the exact source task and the migrator version.
    assert provenance["migrator_version"] == 1
    assert provenance["source_task_sha256"] == canonical_sha256(source)
    assert provenance["provisional_fields"] == []
    # Every semantic field is author_explicit; structural derivations are
    # the only non-author sources recorded.
    for milestone_sources in provenance["milestone_field_sources"].values():
        for field in AUTHOR_REQUIRED_SPEC_FIELDS:
            assert milestone_sources[field] == "author_explicit"
        assert milestone_sources["milestone_id"] == "migrator_structural"
        assert milestone_sources["outcome_ref"] == "migrator_structural"
        assert milestone_sources["semantic_description"] == "v3_outcome_copy"
        assert milestone_sources["visibility"] == "v3_public_outcome"
        assert (milestone_sources["required_for_success"]
                == "v3_structural_required_outcomes")
    # The pre-v4 base is carried over unchanged (addons aside) and the
    # outcome ids never move.
    addons = {"contract_version", "contract_milestones",
              "capability_constraints", "role_constraints"}
    assert {key: value for key, value in task_v4.items()
            if key not in addons} == source
    assert [m["outcome_ref"] for m in task_v4["contract_milestones"]] == (
        list(FACTORY_OUTCOMES))
    # The migration never mutates its input.
    assert "contract_version" not in source
    assert source["version"] in {2, 3}
    # Undervivable obligations start empty, never invented.
    assert task_v4["capability_constraints"] == []
    assert task_v4["role_constraints"] == []


def test_migration_missing_spec_lists_missing_outcomes():
    specs = _full_specs()
    del specs["response_area_served"]
    with pytest.raises(ValueError, match="response_area_served") as excinfo:
        migrate_v3_to_contract_v4(_v3_task(), milestone_specs=specs)
    assert "missing milestone_specs" in str(excinfo.value)


@pytest.mark.parametrize("field", list(AUTHOR_REQUIRED_SPEC_FIELDS))
def test_migration_never_defaults_required_semantics(field):
    # Removing any semantic field from any outcome's spec fails the
    # migration naming the outcome and the field; no provisional default
    # activation/stability/weight/monotonicity/anti-double-count is ever
    # generated (stricter than the viewer's provisional suggestion).
    specs = _full_specs()
    del specs["maintenance_handoff_recorded"][field]
    with pytest.raises(ValueError, match="maintenance_handoff_recorded") as excinfo:
        migrate_v3_to_contract_v4(_v3_task(), milestone_specs=specs)
    assert field in str(excinfo.value)
    assert "never defaulted" in str(excinfo.value)


def test_migration_rejects_unknown_spec_keys_and_outcomes():
    specs = _full_specs()
    specs["anomaly_confirmed"]["reference_dag"] = "forbidden"
    with pytest.raises(ValueError, match="unknown fields.*reference_dag"):
        migrate_v3_to_contract_v4(_v3_task(), milestone_specs=specs)
    with pytest.raises(ValueError, match="unknown outcomes.*ghost_outcome"):
        migrate_v3_to_contract_v4(
            _v3_task(), milestone_specs=_full_specs(ghost_outcome={}))


def test_migration_accepts_v2_and_v3_era_sources():
    from demo.benchmark.goal_deployment_assessment import (
        enable_deployment_assessment,
    )

    v2_result = _migrated()
    assert v2_result["provenance"]["source_contract_version"] == 2
    v3_source = enable_deployment_assessment(_v3_task())
    v3_result = migrate_v3_to_contract_v4(
        v3_source, milestone_specs=_full_specs())
    assert v3_result["provenance"]["source_contract_version"] == 3
    assert validate_contract_v4(v3_result["task_v4"])["validated"] is True


# ---------------------------------------------------------------------------
# Version separation and sealing
# ---------------------------------------------------------------------------


def test_pre_v4_reader_rejects_v4_and_v4_reader_rejects_pre_v4():
    task_v4 = _migrated()["task_v4"]
    # The frozen pre-v4 reader fail-closes on the v4 payload (extra fields).
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_goal_task_spec(task_v4)
    # And the v4 reader fail-closes on a pre-v4 payload (missing addons).
    with pytest.raises(ValueError, match="addon fields missing"):
        validate_contract_v4(_v3_task())
    # Setting the legacy version field to 4 does not smuggle a payload in.
    impostor = _v3_task()
    impostor["version"] = 4
    with pytest.raises(ValueError, match="unsupported goal task schema"):
        validate_goal_task_spec(impostor)


def test_freeze_seal_detects_tampering():
    task_v4 = _migrated()["task_v4"]
    frozen = freeze_contract_v4(task_v4)
    assert validate_frozen_v4(frozen)["validated"] is True
    tampered = deepcopy(frozen)
    tampered["task"]["contract_milestones"][0]["weight"] = 99.0
    with pytest.raises(ValueError, match="seal mismatch"):
        validate_frozen_v4(tampered)
    recomputed_seal = deepcopy(tampered)
    recomputed_seal["seal_sha256"] = frozen["seal_sha256"].upper()
    with pytest.raises(ValueError, match="seal mismatch"):
        validate_frozen_v4(recomputed_seal)


# ---------------------------------------------------------------------------
# Q(t) interface (batch-1 curve consumer)
# ---------------------------------------------------------------------------


def test_qt_end_to_end_equal_weights():
    task_v4 = _migrated()["task_v4"]
    active_ids, weights = active_milestones_for_progress(task_v4)
    assert active_ids == sorted(FACTORY_OUTCOMES)
    assert weights == {outcome_id: 1.0 for outcome_id in FACTORY_OUTCOMES}
    observations = [_obs("anomaly_confirmed", 10.0),
                    _obs("maintenance_handoff_recorded", 40.0)]
    curve = build_progress_curve(active_ids, observations, horizon_s=100.0,
                                 weights=weights)
    assert curve["status"] == "computed"
    assert curve["weight_policy"] == "explicit_frozen"
    assert curve["Q_H"] == pytest.approx(2 / 3)
    # The frozen equal weights match the builder's equal-weight policy.
    default_curve = build_progress_curve(active_ids, observations,
                                         horizon_s=100.0)
    assert default_curve["Q_H"] == curve["Q_H"]


def test_qt_end_to_end_explicit_weights():
    specs = _full_specs()
    specs["anomaly_confirmed"]["weight"] = 1.0
    specs["response_area_served"]["weight"] = 2.0
    specs["maintenance_handoff_recorded"]["weight"] = 1.0
    task_v4 = migrate_v3_to_contract_v4(
        _v3_task(), milestone_specs=specs)["task_v4"]
    active_ids, weights = active_milestones_for_progress(task_v4)
    # Satisfy the two heavier/lighter milestones (response stable at 25s,
    # maintenance at 30s): Q(H) = (2 + 1) / 4 = 0.75.
    curve = build_progress_curve(
        active_ids,
        [_obs("response_area_served", 20.0, stability=5.0),
         _obs("maintenance_handoff_recorded", 30.0)],
        horizon_s=100.0, weights=weights)
    assert curve["status"] == "computed"
    assert curve["Q_H"] == pytest.approx(0.75)
    assert curve["completed_milestones"] == [
        "response_area_served", "maintenance_handoff_recorded"]


def test_qt_interface_excludes_undeclared_activation_and_refuses_non_monotonic():
    task_v4 = _migrated()["task_v4"]
    task_v4["contract_milestones"][0]["activation_rule"] = (
        "exogenous_event:south_relay_confirmed")
    active_ids, weights = active_milestones_for_progress(task_v4)
    assert "anomaly_confirmed" not in active_ids
    assert len(active_ids) == 2 and set(weights) == set(active_ids)
    # A declared non-monotonic episode-start milestone cannot be fed to the
    # monotone-step builder: fail closed instead of mis-scoring.
    non_monotonic = _migrated()["task_v4"]
    non_monotonic["contract_milestones"][1]["monotonicity"] = "non_monotonic"
    with pytest.raises(ValueError, match="non_monotonic"):
        active_milestones_for_progress(non_monotonic)


# ---------------------------------------------------------------------------
# CS gate and release gating
# ---------------------------------------------------------------------------


def test_cs_gate_three_states():
    task_v4 = _migrated()["task_v4"]
    # 1. No contract v4: the exact batch-0 placeholder word.
    gate = cs_vector_gate(None, None)
    assert gate["status"] == CS_GATE_NO_CONTRACT_STATUS
    assert gate["status"] == NOT_APPLICABLE_UNTIL_CONTRACT_V4
    assert all(component["status"] == "not_evaluable_no_contract_v4"
               and component["rate"] is None
               for component in gate["components"].values())
    # 2. Contract v4 without committed-plan evidence.
    for evidence in (None, {}):
        gate = cs_vector_gate(task_v4, evidence)
        assert gate["status"] == CS_GATE_NO_COMMITTED_PLAN_STATUS == "no_committed_plan"
        assert all(component["rate"] is None
                   for component in gate["components"].values())
    # 3. Both gate inputs present: the verdicts belong to batch 2B.
    evidence = {"plan_version": 2, "plan_sha256": "deadbeef",
                "milestone_to_subtasks": {"anomaly_confirmed": ["t1"]}}
    gate = cs_vector_gate(task_v4, evidence)
    assert gate["status"] == CS_GATE_DEFERRED_STATUS
    assert set(gate["components"]) == {"cap", "role", "mile"}
    assert all(component["status"] == "pending_constraint_verifier_batch_2b"
               and component["rate"] is None
               for component in gate["components"].values())
    # A malformed evidence object is a caller error, not a silent verdict.
    with pytest.raises(ValueError, match="committed_plan_evidence"):
        cs_vector_gate(task_v4, ["not", "a", "mapping"])


def test_release_flags_are_always_false():
    assert CONTRACT_V4_RELEASE == {"release_eligible": False,
                                   "scoreable": False,
                                   "execution_validated": False}
    task_v4 = _migrated()["task_v4"]
    assert validate_contract_v4(task_v4)["scoreable"] is False
    result = _migrated()
    assert result["release_eligible"] is False and result["scoreable"] is False
    assert validate_frozen_v4(freeze_contract_v4(task_v4))["scoreable"] is False
    for gate in (cs_vector_gate(None, None),
                 cs_vector_gate(task_v4, None),
                 cs_vector_gate(task_v4, {"plan_version": 1})):
        assert gate["release_eligible"] is False
        assert gate["scoreable"] is False
        assert gate["execution_validated"] is False
        assert gate["not_protocol_score"] is True
