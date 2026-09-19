"""Domain adapter boundary tests; no test here certifies Isaac execution."""

import numpy as np
import pytest

from demo.benchmark.domain_adapters import SceneDomainEvidenceAdapter
from demo.benchmark.observations import ObservationStore
from demo.benchmark.provenance import canonical_sha256


def adapter_fixture():
    store = ObservationStore()
    store.publish_frame(
        observation_id="rack-frame", producer="cf2x_1", sensor="rgb",
        sensor_config={"camera_calibration": {"fx": 1}}, timestamp_s=2.0,
        frame_id="rack-frame", target_ref="rack_scan_zone",
        position_m=[-1.0, -2.0, 2.5], frame=np.zeros((2, 2, 3), dtype=np.uint8),
        valid_for_s=10.0)
    store.publish_detection(
        detection_id="det-1", source_observation_id="rack-frame",
        detector_source="warehouse_detector", detector_version="1",
        target_id="opaque-scene-object", estimated_position_m=[-1.0, -2.0, 0.5],
        confidence=0.9, timestamp_s=2.0)
    store.consume("rack-frame", consumer="rack_overview_scan", timestamp_s=3.0)
    return store, SceneDomainEvidenceAdapter("warehouse_nvd", observation_store=store)


def test_adapter_records_live_claims_without_inserting_identity_truth():
    store, adapter = adapter_fixture()
    adapter.record_identity_check(
        requirement_id="priority", detection_ids=["det-1"],
        consumer_node="priority_carton_stage")
    adapter.record_access_path(
        phase="before", observation_id="rack-frame", time_s=3.0,
        points=[[-2.0, -2.0], [1.0, -2.0]], target_ref="rack_scan_zone")
    adapter.record_service_event({
        "id": "dock-state", "kind": "dock_state", "source": "dock_controller",
        "target_ref": "outbound_dock_zone", "state": "ready", "time_s": 4.0,
        "valid_until_s": 8.0})
    report = adapter.report(contract_sha256="a" * 64)
    assert report["adapter_status"] == "live_claims_recorded"
    assert report["identity_checks"][0]["detection_ids"] == ["det-1"]
    assert "target_id" not in str(report["identity_checks"])
    assert report["access_paths"]["before"]["points"][0] == [-2.0, -2.0]
    assert report["record_sha256"] == canonical_sha256({k: v for k, v in report.items() if k != "record_sha256"})
    assert store.report()["detections"][0]["target_id"] == "opaque-scene-object"


def test_pending_adapter_report_is_explicit_and_does_not_claim_business_success():
    adapter = SceneDomainEvidenceAdapter("modern_house_nvd", observation_store=ObservationStore())
    report = adapter.report(contract_sha256="b" * 64)
    assert report["adapter_status"] == "pending_live_scene_adapter"
    assert report["identity_checks"] == []
    assert "access_paths" not in report


def test_scene_collector_is_invoked_with_runtime_metadata_and_ingests_claims():
    store = ObservationStore()
    seen = {}

    def collector(context):
        seen.update(context)
        return {
            "identity_checks": [{
                "requirement_id": "priority",
                "detection_ids": ["det-1"],
                "consumer_node": "priority_carton_stage",
            }],
            "domain_service_events": [{
                "id": "dock-state", "kind": "dock_state", "source": "dock_controller",
                "target_ref": "outbound_dock_zone", "state": "ready",
                "time_s": 4.0, "valid_until_s": 8.0,
            }],
            "access_paths": {
                "before": {
                    "observation_id": "rack-frame", "time_s": 3.0,
                    "points": [[-2.0, -2.0], [1.0, -2.0]],
                    "target_ref": "rack_scan_zone",
                },
            },
        }

    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store,
        adapter_id="warehouse.live_detector.v1", collector=collector)
    result = adapter.collect(
        phase="episode_finalized", time_s=9.0,
        context={"collection_scope": "final_episode_observations_only"})
    report = adapter.report(contract_sha256="c" * 64)

    assert result["status"] == "claims_ingested"
    assert result["claim_counts"] == {
        "identity_checks": 1, "domain_service_events": 1, "access_paths": 1}
    assert seen["schema"] == "eai.benchmark.domain_collector_context_v1"
    assert seen["scene_key"] == "warehouse_nvd"
    assert seen["metadata"]["collection_scope"] == "final_episode_observations_only"
    assert report["adapter_status"] == "live_claims_recorded"
    assert report["collector"]["configured"] is True
    assert len(report["collections"]) == 1
    assert "target_id" not in str(report["identity_checks"])


def test_malformed_collector_result_is_atomic_and_explicitly_fails_closed():
    adapter = SceneDomainEvidenceAdapter(
        "modern_house_nvd", observation_store=ObservationStore(),
        collector=lambda _context: {"business_success": True})
    result = adapter.collect(phase="episode_finalized", time_s=1.0)
    report = adapter.report(contract_sha256="d" * 64)

    assert result["status"] == "error"
    assert "undeclared fields" in result["reason"]
    assert report["adapter_status"] == "collector_error"
    assert report["identity_checks"] == []
    assert "domain_service_events" not in report
    assert "access_paths" not in report


@pytest.mark.parametrize("operation", ["duplicate_identity", "out_of_order_service", "bad_phase"])
def test_adapter_rejects_ambiguous_or_unreplayable_claims(operation):
    _, adapter = adapter_fixture()
    adapter.record_identity_check(
        requirement_id="priority", detection_ids=["det-1"],
        consumer_node="priority_carton_stage")
    if operation == "duplicate_identity":
        with pytest.raises(ValueError, match="duplicate"):
            adapter.record_identity_check(
                requirement_id="priority", detection_ids=["det-2"],
                consumer_node="priority_carton_stage")
    elif operation == "out_of_order_service":
        adapter.record_service_event({"id": "late", "kind": "dock_state", "source": "s", "time_s": 3.0})
        with pytest.raises(ValueError, match="monotonic"):
            adapter.record_service_event({"id": "early", "kind": "dock_state", "source": "s", "time_s": 2.0})
    else:
        with pytest.raises(ValueError, match="before or after"):
            adapter.record_access_path(
                phase="middle", observation_id="rack-frame", time_s=3.0,
                points=[[-1.0, -1.0], [0.0, 0.0]], target_ref="rack_scan_zone")
