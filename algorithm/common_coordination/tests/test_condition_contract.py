"""Cross-algorithm checks for the HeteroCoord-facing adapter contract."""

from algorithm.system.coordination.coherent.fire_rescue_policy import (
    condition_manifest as coherent_manifest,
)
from algorithm.system.coordination.emos.benchmark_condition import (
    condition_manifest as emos_manifest,
)
from algorithm.system.coordination.TeamWeaver.eai_adapter.benchmark_condition import (
    condition_manifest as teamweaver_manifest,
)
from algorithm.common_coordination import EventLedger, MilestoneEvidence, RunManifest


def test_all_three_conditions_are_explicitly_adapted_and_weight_free():
    manifests = (coherent_manifest(), emos_manifest(), teamweaver_manifest())
    assert {item["algorithm_id"] for item in manifests} == {
        "COHERENT-adapted-to-EAI",
        "EMOS-adapted-to-EAI",
        "TeamWeaver-adapted-to-EAI",
    }
    for manifest in manifests:
        assert manifest["implementation"] == "adapted"
        assert manifest["checkpoint"] == "not_applicable_no_learned_weights"
        assert manifest["physical_executor_owner"] == "eai_fire_rescue_runtime"
        unsupported = set(manifest["unsupported_dimensions"])
        assert any("D2" in item for item in unsupported)
        assert any("D4" in item for item in unsupported)
        assert any("D5" in item for item in unsupported)


def test_assignment_and_physical_evidence_are_separate_records():
    ledger = EventLedger()
    ledger.append(
        "proposal",
        algorithm_id="EMOS-adapted-to-EAI",
        assignments=[{"robot_id": "r1", "task_id": "red"}],
    )
    manifest = RunManifest(
        condition=emos_manifest_record(),
        task_id="fire_rescue:1",
        scene_id="factory",
    )
    evidence = MilestoneEvidence(
        milestone_id="mission_success",
        satisfied=None,
        source="eai_evaluator_pending",
    )
    assert ledger.events[0]["kind"] == "proposal"
    assert not any(item["kind"] == "mission_success" for item in ledger.events)
    assert evidence.satisfied is None
    assert manifest.status == "pending"


def emos_manifest_record():
    from algorithm.system.coordination.emos.benchmark_condition import condition_record

    return condition_record(model_or_api="test-provider")
