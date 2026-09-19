"""Decode actual marker pixels and check metric pose recovery without scene truth."""

import cv2
import numpy as np
import pytest

from demo.benchmark.marker_detection import MARKER_SIDE_M, detect_markers, estimate_marker_pose, pinhole_intrinsics


def _calibration(units=1.0):
    projection = np.zeros((4, 4))
    projection[0, 0], projection[1, 1] = 800 / 320, 800 / 240
    projection[2, 3], projection[3, 2] = -1, -0.1
    view = np.eye(4)
    view[3, :3] = -np.array([2, 3, 4]) / units
    return {"parameters": {"cameraModel": "pinhole", "renderProductResolution": [640, 480],
                           "cameraProjection": projection.flatten().tolist(),
                           "cameraViewTransform": view.flatten().tolist(), "metersPerSceneUnit": units}}


def _frame(marker_id):
    image = np.full((480, 640), 255, dtype=np.uint8)
    image[168:312, 248:392] = cv2.aruco.generateImageMarker(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), marker_id, 144)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)


@pytest.mark.parametrize("marker_id", [0, 17, 23])
def test_decoder_identifies_pixels_without_receiving_expected_id(marker_id):
    result = detect_markers(_frame(marker_id), _calibration())
    detection, = result["detections"]
    assert detection["marker_id"] == marker_id
    assert detection["pose_valid"]
    assert detection["estimated_position_m"] == pytest.approx([2.0, 3.0, 4.0 - MARKER_SIDE_M / 0.18], abs=0.015)
    assert detection["confidence"] is None
    assert detection["reprojection_rmse_px"] < 1


def test_uniform_or_erased_marker_does_not_create_a_detection():
    uniform = np.full((480, 640, 4), 180, dtype=np.uint8)
    assert not detect_markers(uniform, _calibration())["detections"]
    erased = _frame(17)
    erased[168:312, 248:320] = 255
    assert not detect_markers(erased, _calibration())["detections"]


def test_camera_world_conversion_preserves_metres():
    result = detect_markers(_frame(17), _calibration(units=0.01))
    assert result["detections"][0]["estimated_position_m"] == pytest.approx(
        [2, 3, 4 - MARKER_SIDE_M / 0.18], abs=0.015)


def test_public_printed_size_controls_metric_scale():
    first = detect_markers(_frame(17), _calibration(), marker_side_m=0.09)
    second = detect_markers(_frame(17), _calibration(), marker_side_m=0.18)
    assert first["detections"][0]["camera_translation_m"][2] * 2 == pytest.approx(
        second["detections"][0]["camera_translation_m"][2])


def test_static_view_fusion_uses_all_estimates_without_truth_or_id_preselection():
    from demo.benchmark.marker_detection import fuse_marker_views
    from demo.factory_benchmark.inspect_inventory import replay_marker_views

    observations = [{"observation_id": f"ground_view:frame:{index}", "frame_id": "ground/camera",
                     "timestamp_s": float(index)} for index in range(3)]
    frames = [{"source_observation_id": observation["observation_id"], "detections": [{
        "pose_valid": True, "marker_id": 17, "estimated_position_m": position}]}
        for observation, position in zip(observations, [[1, 2, 3], [100, 100, 100], [1.1, 2.1, 3.1]])]
    estimate, = fuse_marker_views(frames, observations)
    assert estimate["sample_count"] == 3
    assert estimate["estimated_position_m"] == [1.1, 2.1, 3.1]
    assert estimate["source_observation_ids"] == [observation["observation_id"] for observation in observations]
    frames[1]["detections"][0]["marker_id"] = 23
    assert [estimate["marker_id"] for estimate in fuse_marker_views(frames, observations)] == [17, 23]
    report = {"inventory_detection": {"frames": frames, "fused_view_estimates": fuse_marker_views(frames, observations)}}
    by_id = {observation["observation_id"]: observation for observation in observations}
    assert len(replay_marker_views(report, by_id)) == 2
    report["inventory_detection"]["fused_view_estimates"][0]["estimated_position_m"][0] = 999
    with pytest.raises(ValueError, match="image-only fusion"):
        replay_marker_views(report, by_id)


def test_exact_frontoparallel_square_has_a_valid_low_residual_pose():
    estimate = estimate_marker_pose([[248., 168.], [392., 168.], [392., 312.], [248., 312.]], _calibration())
    assert estimate["camera_translation_m"] == pytest.approx([0, 0, 0.5], abs=1e-8)
    assert estimate["reprojection_rmse_px"] < 1e-6
    assert estimate["pose_solver"] in {"IPPE_SQUARE", "ITERATIVE"}


def test_pinhole_model_handles_shifted_principal_point_and_rejects_bad_data():
    calibration = _calibration()
    projection = np.array(calibration["parameters"]["cameraProjection"]).reshape(4, 4)
    projection[2, 0], projection[2, 1] = 0.1, -0.1
    calibration["parameters"]["cameraProjection"] = projection.flatten().tolist()
    assert pinhole_intrinsics(calibration)[:2, 2].tolist() == pytest.approx([288, 216])
    with pytest.raises(ValueError, match="four finite"):
        estimate_marker_pose([[float("nan"), 0]] * 4, calibration)
    with pytest.raises(ValueError, match="resolution"):
        detect_markers(np.zeros((1, 2, 4), dtype=np.uint8), calibration)
    calibration["parameters"]["cameraModel"] = "fisheye"
    with pytest.raises(ValueError, match="pinhole"):
        detect_markers(_frame(17), calibration)


def test_inventory_evaluator_recomputes_claims_from_pixels(tmp_path):
    import hashlib
    from demo.benchmark.camera_capture import archive_frame
    from demo.factory_benchmark.inspect_inventory import replay_marker_frames

    frame, calibration = _frame(17), _calibration()
    config = archive_frame(tmp_path, "frame", frame, calibration)
    config.update(frame_shape=list(frame.shape), frame_dtype="uint8",
                  frame_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
    result = detect_markers(frame, calibration)
    result["source_observation_id"] = "frame"
    report = {"observation_store": {"observations": [{"observation_id": "frame", "sensor_config": config}]},
              "inventory_detection": {"frames": [result]}}
    assert set(replay_marker_frames(report)) == {"frame"}
    result["detections"][0]["marker_id"] = 23
    with pytest.raises(ValueError, match="differs from pixel replay"):
        replay_marker_frames(report)
