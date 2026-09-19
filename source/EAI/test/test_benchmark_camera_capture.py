"""Pixel preservation and renderer matrix conventions for camera evidence."""

import hashlib

import numpy as np
import pytest

from demo.benchmark.camera_capture import archive_frame, project_world_points, verify_archived_frame


def _calibration():
    view = np.eye(4)
    view[3, :3] = [-2.0, -3.0, -4.0]
    projection = np.zeros((4, 4))
    projection[0, 0], projection[1, 1], projection[2, 3], projection[3, 2] = 2.0, 2.0, -1.0, 1.0
    return {"parameters": {"cameraModel": "pinhole", "cameraViewTransform": view.reshape(-1).tolist(),
                           "cameraProjection": projection.reshape(-1).tolist(), "metersPerSceneUnit": 1.0,
                           "renderProductResolution": [8, 6]}}


def test_camera_archive_roundtrip_preserves_rgba_and_rejects_tampering(tmp_path):
    frame = np.arange(6 * 8 * 4, dtype=np.uint8).reshape(6, 8, 4)
    config = archive_frame(tmp_path, "ground_view:frame:30", frame, _calibration())
    config.update(frame_shape=list(frame.shape), frame_dtype="uint8",
                  frame_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
    verify_archived_frame(config)
    assert np.array_equal(np.load(config["archive_path"]), frame)
    with pytest.raises(FileExistsError):
        archive_frame(tmp_path, "ground_view:frame:30", frame, _calibration())
    with open(config["archive_path"], "ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="pixels changed"):
        verify_archived_frame(config)


def test_camera_archive_requires_same_render_resolution(tmp_path):
    with pytest.raises(ValueError, match="dimensions differ"):
        archive_frame(tmp_path, "frame", np.zeros((12, 16, 3), dtype=np.uint8), _calibration())


def test_renderer_projection_uses_row_vectors_negative_z_forward_and_metric_units():
    result = project_world_points([[2, 3, 2], [2.5, 3.5, 2], [2, 3, 6], [5, 3, 2]], _calibration())
    assert result["pixels"][:2] == [[4.0, 3.0], [6.0, 1.5]]
    assert result["pixels"][2] is None
    assert result["depth_m"] == [2.0, 2.0, -2.0, 2.0]
    assert result["in_image"] == [True, True, False, False]
    calibration = _calibration()
    calibration["parameters"]["metersPerSceneUnit"] = 0.01
    result = project_world_points([[0.02, 0.03, 0.02]], calibration)
    assert result["pixels"] == [[4.0, 3.0]]
    assert result["depth_m"] == [0.02]


def test_renderer_projection_rejects_unsupported_or_nonfinite_data():
    calibration = _calibration()
    with pytest.raises(ValueError, match="finite"):
        project_world_points([[float("nan"), 0.0, 0.0]], calibration)
    calibration["parameters"]["cameraModel"] = "fisheyePolynomial"
    with pytest.raises(ValueError, match="pinhole"):
        project_world_points([[0.0, 0.0, 0.0]], calibration)
