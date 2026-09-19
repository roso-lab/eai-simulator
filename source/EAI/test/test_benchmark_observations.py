"""Pure tests for benchmark observation provenance and freshness."""

import pytest

from demo.benchmark.observations import ObservationStore


def _store():
    store = ObservationStore()
    store.publish(
        observation_id="obs_001", producer="carter_1", sensor="camera",
        sensor_config={"frame": "camera_optical_frame", "resolution": [640, 480]},
        timestamp_s=2.0, frame_id="factory/storage", target_ref="crate_a",
        position_m=[1, 2, 0.4], quality={"score": 0.8, "status": "valid"}, valid_for_s=3.0,
    )
    return store


def test_observation_records_producer_and_explicit_consumer():
    store = _store()
    store.consume("obs_001", consumer="route_confirmation", timestamp_s=3.5)
    record = store.get("obs_001")
    assert record["producer"] == "carter_1"
    assert record["sensor"] == "camera"
    assert record["target_estimate"]["position_m"] == [1.0, 2.0, 0.4]
    assert record["consumed_by"] == [{"consumer": "route_confirmation", "timestamp_s": 3.5}]


def test_observation_rejects_expired_or_duplicate_use():
    store = _store()
    with pytest.raises(ValueError, match="expired"):
        store.consume("obs_001", consumer="late_node", timestamp_s=5.1)
    with pytest.raises(ValueError, match="duplicate"):
        store.publish(
            observation_id="obs_001", producer="carter_1", sensor="camera",
            sensor_config={}, timestamp_s=4.0, frame_id="factory/storage",
            target_ref="crate_a", position_m=[1, 2, 0], quality={}, valid_for_s=1,
        )


def test_observation_rejects_invalid_quality_and_coordinates():
    store = ObservationStore()
    with pytest.raises(ValueError, match="quality.score"):
        store.publish(
            observation_id="obs_bad", producer="carter_1", sensor="camera",
            sensor_config={}, timestamp_s=0, frame_id="factory/storage",
            target_ref="crate_a", position_m=[1, 2, 0], quality={"score": 2}, valid_for_s=1,
        )
    with pytest.raises(ValueError, match="three coordinates"):
        store.publish(
            observation_id="obs_bad", producer="carter_1", sensor="camera",
            sensor_config={}, timestamp_s=0, frame_id="factory/storage",
            target_ref="crate_a", position_m=[1, 2], quality={}, valid_for_s=1,
        )


def test_frame_observation_records_digest_and_marks_pose_only():
    numpy = pytest.importorskip("numpy")
    store = ObservationStore()
    frame = numpy.arange(12, dtype=numpy.uint8).reshape(2, 2, 3)
    record = store.publish_frame(
        observation_id="frame_001", producer="cf2x_1", sensor="camera",
        sensor_config={"resolution": [2, 2]}, timestamp_s=4.0,
        frame_id="cf2x_1/camera_optical_frame", target_ref="inspection_site",
        position_m=[1, 2, 3], frame=frame, valid_for_s=5.0,
    )
    assert record["target_estimate"]["kind"] == "sensor_pose_only"
    assert record["sensor_config"]["frame_shape"] == [2, 2, 3]
    assert record["sensor_config"]["frame_dtype"] == "uint8"
    assert len(record["sensor_config"]["frame_sha256"]) == 64
    assert record["quality"] == {"valid_pixel_count": 12, "frame_acquired": True}


def test_frame_observation_rejects_empty_frame():
    numpy = pytest.importorskip("numpy")
    store = ObservationStore()
    with pytest.raises(ValueError, match="at least one pixel"):
        store.publish_frame(
            observation_id="frame_empty", producer="cf2x_1", sensor="camera",
            sensor_config={}, timestamp_s=0.0, frame_id="camera",
            target_ref="inspection_site", position_m=[0, 0, 0],
            frame=numpy.empty((0, 2, 3), dtype=numpy.uint8), valid_for_s=1.0,
        )


def test_detector_output_is_linked_to_observation_without_ground_truth():
    store = _store()
    detection = store.publish_detection(
        detection_id="det_001", source_observation_id="obs_001",
        detector_source="factory_detector", detector_version="test-1",
        target_id="crate_a", estimated_position_m=[1.1, 2.0, 0.4],
        confidence=0.9, timestamp_s=2.5,
    )
    assert detection["source_observation_id"] == "obs_001"
    report = store.report()
    assert report["detections"][0]["target_id"] == "crate_a"
    assert "ground_truth" not in report["detections"][0]


def test_detector_confidence_can_remain_unknown():
    store = _store()
    result = store.publish_detection(detection_id="marker", source_observation_id="obs_001",
        detector_source="opencv", detector_version="test", target_id="inventory_marker:17",
        estimated_position_m=[1, 2, 3], confidence=None, timestamp_s=2.0)
    assert result["confidence"] is None


def test_detector_output_requires_valid_source_window():
    store = _store()
    with pytest.raises(ValueError, match="within source observation validity"):
        store.publish_detection(
            detection_id="det_late", source_observation_id="obs_001",
            detector_source="factory_detector", detector_version="test-1",
            target_id="crate_a", estimated_position_m=[1, 2, 0.4],
            confidence=0.9, timestamp_s=6.0,
        )
