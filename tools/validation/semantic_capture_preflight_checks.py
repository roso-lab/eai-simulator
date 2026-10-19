"""Directed capture-contract regressions and real-stage n-gon coverage."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from tools.topology_authoring.examples.capture_living_semantic_scenes import (
    _clipping_range_metres,
    _fan_triangles,
    _image_to_world,
    _copy_stage_metrics,
    _metres_to_stage_units,
    _preflight_descriptor,
    _publish_scene_directory,
    _published_path,
    _validate_capture_set,
    _world_points_metres,
    _write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[2]
STADIUM = ROOT / "usd/scene/Service and operations/stadium/stadium.usda"
ROOM_05 = ROOT / "usd/scene/Living and public space/room_05/room_05.usda"


def _descriptor():
    return {
        "scene_root": "/unused",
        "scenes": [
            {
                "id": "scene-a",
                "usd": "a.usda",
                "capture_bounds_xy": [-4.0, -3.0, 5.0, 6.0],
                "levels": [
                    {"id": "1F", "support_z_m": 0.0},
                    {"id": "2F", "support_z_m": 3.0,
                     "capture_bounds_xy": [-2.0, -1.0, 2.0, 1.0]},
                ],
            },
            {"id": "scene-b", "usd": "b.usda",
             "levels": [{"id": "1F", "support_z_m": -1.0}]},
        ],
    }


def test_fan_triangulates_mixed_faces_deterministically():
    indices = [0, 1, 2, 3, 10, 11, 12, 13, 14]
    counts = [4, 5]
    expected = np.asarray([
        [0, 1, 2], [0, 2, 3],
        [10, 11, 12], [10, 12, 13], [10, 13, 14],
    ])
    np.testing.assert_array_equal(_fan_triangles(indices, counts), expected)
    np.testing.assert_array_equal(_fan_triangles(indices, counts), expected)
    with pytest.raises(ValueError, match="malformed"):
        _fan_triangles(indices, [4, 4])


def test_actual_stadium_all_ngons_are_accepted_without_starting_isaac():
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(STADIUM))
    rows = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        rows.append((counts, _fan_triangles(indices, counts)))
    assert len(rows) == 39
    assert all(any(int(count) != 3 for count in counts) for counts, _ in rows)
    assert all(len(triangles) > 0 for _, triangles in rows)
    assert sum(len(triangles) for _, triangles in rows) == 1_054_552


def test_preflight_rejects_duplicate_unknown_and_invalid_hard_exit():
    descriptor = _descriptor()
    assert [row["id"] for row in _preflight_descriptor(
        descriptor, {"scene-b"}, hard_exit=True)] == ["scene-b"]
    with pytest.raises(ValueError, match="unknown scene IDs"):
        _preflight_descriptor(descriptor, {"missing"}, hard_exit=True)
    with pytest.raises(ValueError, match="exactly one known"):
        _preflight_descriptor(descriptor, set(), hard_exit=True)
    duplicate = _descriptor()
    duplicate["scenes"].append(dict(duplicate["scenes"][0]))
    with pytest.raises(ValueError, match="duplicate scene id"):
        _preflight_descriptor(duplicate, set())
    reversed_levels = _descriptor()
    reversed_levels["scenes"][0]["levels"][1]["support_z_m"] = -1.0
    with pytest.raises(ValueError, match="strictly increasing"):
        _preflight_descriptor(reversed_levels, set())


def test_world_metres_roi_transform_and_clipping_contract():
    matrix = np.eye(4)
    matrix[3, :3] = [10.0, -2.0, 4.0]
    world = _world_points_metres([[1.0, 2.0, 3.0]], matrix, 0.5)
    np.testing.assert_allclose(world, [[5.5, 0.0, 3.5]])

    contract = _image_to_world([-4.0, 6.0, -3.0, 7.0])
    transform = np.asarray(contract["matrix_3x3"])
    np.testing.assert_allclose(transform @ [0.0, 0.0, 1.0], [-4.0, 7.0, 1.0])
    np.testing.assert_allclose(transform @ [1.0, 1.0, 1.0], [6.0, -3.0, 1.0])

    near, far = _clipping_range_metres(
        eye=(1.0, 2.0, 20.0), bounds_xy=(-4.0, -3.0, 6.0, 7.0), z_bounds=(0.0, 8.0))
    assert 0 < near < far
    corners = np.asarray([[x, y, z] for x in (-4.0, 6.0)
                          for y in (-3.0, 7.0) for z in (0.0, 8.0)])
    assert far > np.linalg.norm(corners - [1.0, 2.0, 20.0], axis=1).max()


def test_wrapper_preserves_actual_source_metrics_and_camera_scale(tmp_path):
    from pxr import Usd, UsdGeom

    source = Usd.Stage.Open(str(ROOM_05))
    assert UsdGeom.GetStageMetersPerUnit(source) == 1.0
    wrapper_path = tmp_path / "capture-wrapper.usda"
    wrapper = Usd.Stage.CreateNew(str(wrapper_path))
    wrapper.GetRootLayer().subLayerPaths = [str(ROOM_05)]
    metrics = _copy_stage_metrics(source, wrapper)
    wrapper.GetRootLayer().Save()
    reopened = Usd.Stage.Open(str(wrapper_path))

    assert UsdGeom.GetStageMetersPerUnit(reopened) == metrics["meters_per_unit"] == 1.0
    assert str(UsdGeom.GetStageUpAxis(reopened)) == metrics["up_axis"]
    assert _metres_to_stage_units((16.8, 3.2, 2.0), metrics["meters_per_unit"]) == (
        16.8, 3.2, 2.0)


def test_capture_set_requires_five_decodable_exact_size_images(tmp_path):
    expected = []
    for index in range(5):
        path = tmp_path / f"view-{index}.png"
        Image.new("RGB", (32, 24), (index, 20, 40)).save(path)
        expected.append((path, (32, 24)))
    metadata = _validate_capture_set({"1F": expected})
    assert len(metadata["1F"]) == 5
    assert all(len(row["sha256"]) == 64 and row["bytes"] > 0 for row in metadata["1F"])

    with pytest.raises(ValueError, match="exactly five"):
        _validate_capture_set({"1F": expected[:4]})
    with pytest.raises(RuntimeError, match="size mismatch"):
        _validate_capture_set({"1F": expected[:-1] + [(expected[-1][0], (31, 24))]})


def test_atomic_receipt_is_valid_json_and_fsyncs_parent(tmp_path, monkeypatch):
    import tools.topology_authoring.examples.capture_living_semantic_scenes as capture

    calls = []
    real_fsync = capture.os.fsync
    monkeypatch.setattr(capture.os, "fsync", lambda fd: calls.append(fd))
    path = tmp_path / "capture-complete.json"
    _write_json_atomic(path, {"complete": True, "images": 5})
    assert json.loads(path.read_text()) == {"complete": True, "images": 5}
    assert not path.with_name(path.name + ".tmp").exists()
    assert len(calls) == 2
    monkeypatch.setattr(capture.os, "fsync", real_fsync)


def test_scene_directory_is_rebased_then_published_by_single_rename(tmp_path):
    staging = tmp_path / ".scene-a.capture-tmp-1"
    final = tmp_path / "scene-a"
    staging.mkdir()
    (staging / "view.png").write_bytes(b"image")
    _write_json_atomic(staging / "capture.json", {"scene_id": "scene-a"})
    _write_json_atomic(staging / "capture-complete.json", {"complete": True})

    assert _published_path(staging / "view.png", staging, final) == str(final / "view.png")
    _publish_scene_directory(staging, final)

    assert not staging.exists()
    assert (final / "view.png").read_bytes() == b"image"
    assert (final / "capture-complete.json").is_file()


def test_incomplete_staging_directory_never_creates_final_scene(tmp_path):
    staging = tmp_path / ".scene-a.capture-tmp-1"
    final = tmp_path / "scene-a"
    staging.mkdir()
    _write_json_atomic(staging / "capture.json", {"scene_id": "scene-a"})

    with pytest.raises(RuntimeError, match="missing capture-complete.json"):
        _publish_scene_directory(staging, final)
    assert staging.is_dir()
    assert not final.exists()
