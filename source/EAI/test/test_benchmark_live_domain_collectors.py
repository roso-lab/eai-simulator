"""Live scene collectors assemble claims but do not certify business truth."""

import numpy as np
import pytest

from demo.benchmark.domain_adapters import SceneDomainEvidenceAdapter
from demo.benchmark.live_domain_collectors import (
    LiveSceneDomainCollector, build_live_domain_collector,
)
from demo.benchmark.observations import ObservationStore


def _store(*, item_code="W-PRIORITY-01", consumer="priority_carton_stage"):
    store = ObservationStore()
    store.publish_frame(
        observation_id="frame-1", producer="iris_1", sensor="rgb",
        sensor_config={"camera_calibration": {"fx": 1.0}}, timestamp_s=2.0,
        frame_id="iris_1/camera", target_ref="rack_scan_zone",
        position_m=[0.0, 0.0, 2.0],
        frame=np.ones((2, 2, 3), dtype=np.uint8), valid_for_s=10.0)
    store.publish_detection(
        detection_id="det-1", source_observation_id="frame-1",
        detector_source="scene_detector", detector_version="1",
        target_id="opaque-object", item_code=item_code,
        estimated_position_m=[1.0, 2.0, 0.5], confidence=0.9,
        timestamp_s=2.1)
    store.consume("frame-1", consumer=consumer, timestamp_s=2.2)
    return store


def _public_item(identifier, item_code, consumer):
    return {"id": identifier, "item_code": item_code, "quantity": 1,
            "consumer_node": consumer}


def _public_task(scene_key, items):
    return {
        "schema": "eai.benchmark.public_task",
        "scene": {"scene_key": scene_key},
        "domain_requirements": {"items": items},
    }


def test_warehouse_collector_uses_consumed_detection_and_live_dock_service():
    store = _store()
    public = {"items": [_public_item(
        "priority", "W-PRIORITY-01", "priority_carton_stage")]}
    collector = build_live_domain_collector(
        "warehouse_nvd", public,
        service_provider=lambda _context: [
            {"id": "dock-state", "kind": "dock_state",
             "source": "benchmark_dock_controller", "time_s": 8.0,
             "target_ref": "outbound_dock_zone", "state": "ready",
             "valid_until_s": 10.0},
            {"id": "receipt", "kind": "dock_handoff",
             "source": "benchmark_dock_controller", "time_s": 9.0,
             "dock_state_id": "dock-state", "target_ref": "outbound_dock_zone",
             "accepted_object_ids": ["opaque-object"],
             "observation_id": "frame-1"},
        ])
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store,
        adapter_id="warehouse.live.v1", collector=collector)
    outcome = adapter.collect(phase="episode_finalized", time_s=9.0)
    report = adapter.report(contract_sha256="a" * 64)

    assert outcome["status"] == "claims_ingested"
    assert report["identity_checks"] == [{
        "requirement_id": "priority", "detection_ids": ["det-1"],
        "consumer_node": "priority_carton_stage"}]
    assert report["dock_receipt_id"] == "receipt"
    assert len(report["domain_service_events"]) == 2
    assert "business_success" not in str(report)


def test_house_collector_binds_access_paths_to_actual_map_hash():
    store = _store(item_code="personal_book_stack", consumer="keepsake_relocation")
    collector = LiveSceneDomainCollector(
        "modern_house_nvd",
        public_identity_requirements=[_public_item(
            "keepsake", "personal_book_stack", "keepsake_relocation")],
        access_provider=lambda _context: {
            "access_map_sha256": "b" * 64,
            "access_paths": {
                "before": {"observation_id": "frame-1", "time_s": 2.2,
                           "points": [[0.0, 0.0], [1.0, 1.0]],
                           "target_ref": "entry_scan_zone"},
                "after": {"observation_id": "frame-1", "time_s": 9.0,
                          "points": [[0.0, 0.0], [1.0, 1.0]],
                          "target_ref": "room_review_zone"},
            },
        })
    adapter = SceneDomainEvidenceAdapter(
        "modern_house_nvd", observation_store=store,
        adapter_id="house.live.v1", collector=collector)
    outcome = adapter.collect(phase="episode_finalized", time_s=9.0)
    report = adapter.report(contract_sha256="c" * 64)

    assert outcome["status"] == "claims_ingested"
    assert report["access_map_sha256"] == "b" * 64
    assert set(report["access_paths"]) == {"before", "after"}
    assert report["identity_checks"][0]["detection_ids"] == ["det-1"]


def test_collector_rejects_private_binding_and_ambiguous_detector_claims_atomically():
    with pytest.raises(ValueError, match="only public fields"):
        LiveSceneDomainCollector(
            "warehouse_nvd", public_identity_requirements=[{
                **_public_item("priority", "W-PRIORITY-01", "priority_carton_stage"),
                "object_ids": ["private-object"],
            }])

    store = _store()
    store.publish_detection(
        detection_id="det-2", source_observation_id="frame-1",
        detector_source="scene_detector", detector_version="1",
        target_id="other-opaque-object", item_code="W-PRIORITY-01",
        estimated_position_m=[1.1, 2.0, 0.5], confidence=0.8,
        timestamp_s=2.15)
    collector = LiveSceneDomainCollector(
        "warehouse_nvd", public_identity_requirements=[_public_item(
            "priority", "W-PRIORITY-01", "priority_carton_stage")])
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store, collector=collector)
    result = adapter.collect(phase="episode_finalized", time_s=3.0)
    report = adapter.report(contract_sha256="d" * 64)

    assert result["status"] == "error"
    assert "ambiguous" in result["reason"]
    assert report["adapter_status"] == "collector_error"
    assert report["identity_checks"] == []


def test_detection_item_code_is_a_detector_claim_not_a_truth_result():
    report = _store().report()
    detection = report["detections"][0]
    assert detection["item_code"] == "W-PRIORITY-01"
    assert detection["detector_source"] == "scene_detector"
    assert "validated" not in detection
    assert "success" not in detection


def test_runtime_frame_hook_writes_detector_claim_into_the_bound_store():
    store = ObservationStore()
    store.publish_frame(
        observation_id="live-frame", producer="iris_1", sensor="camera",
        sensor_config={"source": "live"}, timestamp_s=4.0,
        frame_id="iris_1/camera", target_ref="rack_scan_zone",
        position_m=[0.0, 0.0, 2.0],
        frame=np.ones((3, 3, 3), dtype=np.uint8), valid_for_s=5.0)
    seen = {}

    def detector(context):
        seen.update(context)
        return [{
            "detection_id": "live-det", "detector_source": "rgb_sku_detector",
            "detector_version": "weights-sha256:abc", "target_id": "track-7",
            "item_code": "W-PRIORITY-01", "estimated_position_m": [1.0, 2.0, 0.5],
            "confidence": 0.88, "timestamp_s": 4.1,
        }]

    items = [_public_item("priority", "W-PRIORITY-01", "priority_carton_stage")]
    collector = LiveSceneDomainCollector(
        "warehouse_nvd", public_identity_requirements=items, detector=detector)
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store, collector=collector)

    binding = adapter.bind_runtime(
        base_env=object(), occupancy=object(),
        public_task=_public_task("warehouse_nvd", items))
    outcome = adapter.process_frame(
        observation_id="live-frame", frame=np.ones((3, 3, 3), dtype=np.uint8),
        node_id="rack_overview_scan", time_s=4.0)

    assert binding["status"] == "bound"
    assert outcome == {
        "observation_id": "live-frame", "node_id": "rack_overview_scan",
        "time_s": 4.0, "status": "processed", "detection_ids": ["live-det"],
    }
    assert store.report()["detections"][0]["item_code"] == "W-PRIORITY-01"
    assert "base_env" not in seen
    assert "public_task" not in seen
    assert set(seen) == {
        "schema", "scene_key", "observation_id", "observation", "frame",
        "node_id", "time_s",
    }


def test_invalid_frame_detection_batch_is_atomic_and_blocks_final_collection():
    store = _store()
    items = [_public_item("priority", "W-PRIORITY-01", "priority_carton_stage")]

    def detector(_context):
        valid = {
            "detection_id": "new-valid", "detector_source": "detector",
            "detector_version": "1", "target_id": "track-1",
            "item_code": "W-PRIORITY-01", "estimated_position_m": [0.0, 0.0, 0.0],
            "confidence": 0.8, "timestamp_s": 2.2,
        }
        invalid = {**valid, "detection_id": "new-invalid"}
        invalid.pop("target_id")
        return [valid, invalid]

    collector = LiveSceneDomainCollector(
        "warehouse_nvd", public_identity_requirements=items, detector=detector)
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store, collector=collector)
    adapter.bind_runtime(
        base_env=object(), occupancy=object(),
        public_task=_public_task("warehouse_nvd", items))

    outcome = adapter.process_frame(
        observation_id="frame-1", frame=np.ones((2, 2, 3), dtype=np.uint8),
        node_id="rack_overview_scan", time_s=2.2)
    collection = adapter.collect(phase="episode_finalized", time_s=3.0)
    report = adapter.report(contract_sha256="e" * 64)

    assert outcome["status"] == "error"
    assert collection["status"] == "error"
    assert [row["detection_id"] for row in store.report()["detections"]] == ["det-1"]
    assert report["adapter_status"] == "collector_error"
    assert report["identity_checks"] == []


def test_runtime_binding_rejects_public_requirement_drift_before_detection():
    items = [_public_item("priority", "W-PRIORITY-01", "priority_carton_stage")]
    collector = LiveSceneDomainCollector(
        "warehouse_nvd", public_identity_requirements=items, detector=lambda _context: [])
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=ObservationStore(), collector=collector)

    result = adapter.bind_runtime(
        base_env=object(), occupancy=object(),
        public_task=_public_task("warehouse_nvd", []))

    assert result["status"] == "error"
    assert adapter.report(contract_sha256="f" * 64)["adapter_status"] == "collector_error"


def test_runtime_event_hook_reaches_stateful_provider_and_is_reported():
    seen = []

    class AccessProvider:
        def bind_runtime(self, _context):
            pass

        def process_event(self, context):
            seen.append(dict(context))

        def process_frame(self, _context):
            pass

        def collect(self, _context):
            return {"access_paths": {}, "access_map_sha256": "a" * 64}

    collector = LiveSceneDomainCollector(
        "modern_house_nvd", public_identity_requirements=[],
        access_provider=AccessProvider())
    adapter = SceneDomainEvidenceAdapter(
        "modern_house_nvd", observation_store=ObservationStore(), collector=collector)
    adapter.bind_runtime(
        base_env=object(), occupancy=object(),
        public_task=_public_task("modern_house_nvd", []))

    outcome = adapter.process_event(
        event_type="node_completed", node_id="hall_release", time_s=8.0)
    report = adapter.report(contract_sha256="1" * 64)

    assert outcome["status"] == "processed"
    assert seen == [{
        "schema": "eai.benchmark.domain_runtime_event_v1",
        "scene_key": "modern_house_nvd", "event_type": "node_completed",
        "node_id": "hall_release", "time_s": 8.0,
    }]
    assert report["event_processing"][0]["status"] == "processed"
