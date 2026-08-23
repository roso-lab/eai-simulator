from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from EAI.hmrs_env.env_diy import catalog as env_diy_catalog
from EAI.interface_catalog.loader import _load_device, load_catalog
from EAI.interface_catalog.query import resolve_scene_interfaces
from EAI.physics.aerial_sensors import (
    aerial_sensor_specs_from_selection,
    selection_requires_aerial_camera,
)


def _ids(attachments: list[str]) -> set[str]:
    selection = {
        "scene_key": "plane",
        "robots": [
            {
                "type": "mushr_v2",
                "attachments": [{"type": item} for item in attachments],
            }
        ],
    }
    return {
        interface.interface_id
        for interface in resolve_scene_interfaces(
            load_catalog(), selection, env_name="test"
        )
    }


def test_mushr_never_declares_removed_builtin_camera_interfaces():
    for attachments in ([], ["camera"], ["Camera"]):
        ids = _ids(attachments)
        assert "ros.mushr_camera_image" not in ids
        assert "ros.mushr_camera_info" not in ids


def test_builder_has_no_mushr_builtin_camera_spawn_path():
    builder_path = (
        Path(__file__).resolve().parents[2]
        / "EAI_hmrs"
        / "EAI_hmrs"
        / "env_builder.py"
    )
    tree = ast.parse(builder_path.read_text(encoding="utf-8"), filename=str(builder_path))
    mushr_option = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RobotOption"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "mushr_v2"
    )
    assert "camera_mount_link" not in {keyword.arg for keyword in mushr_option.keywords}

    builtin_camera_sets = [
        {
            element.value
            for element in node.value.comparators[0].elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "is_builtin_camera_robot"
            for target in node.targets
        )
        and isinstance(node.value, ast.Compare)
        and len(node.value.ops) == 1
        and isinstance(node.value.ops[0], ast.In)
        and len(node.value.comparators) == 1
        and isinstance(node.value.comparators[0], ast.Set)
    ]
    assert builtin_camera_sets
    assert all("mushr_v2" not in robot_types for robot_types in builtin_camera_sets)


def test_visual_env_diy_has_no_mushr_camera_exemption():
    html_path = (
        Path(__file__).resolve().parents[1]
        / "EAI"
        / "hmrs_env"
        / "env_diy"
        / "env_diy_app.html"
    )
    html = html_path.read_text(encoding="utf-8")
    assert '["cf2x", "iris", "pegasus", "mushr_v2"]' not in html
    assert 'const hasBuiltinCamera = ["cf2x", "iris", "pegasus"]' in html


def test_env_diy_requires_explicit_mushr_camera_provider():
    assert "mushr_v2" not in env_diy_catalog.BUILTIN_CAMERA_ROBOTS
    try:
        env_diy_catalog.validate_attachment_types("mushr_v2", ["camera"])
    except ValueError as exc:
        assert "requires the Orsus or RealSense D455 payload" in str(exc)
    else:
        raise AssertionError("MuSHR camera-only selection must be rejected")
    assert env_diy_catalog.validate_attachment_types(
        "mushr_v2", ["realsense_d455", "camera"]
    ) == ("realsense_d455", "camera")


def test_mushr_camera_tool_does_not_enable_builtin_camera_runtime():
    selection = {
        "robots": [{
            "type": "mushr_v2",
            "attachments": [{"type": "camera"}],
        }]
    }
    assert aerial_sensor_specs_from_selection(selection, ["mushr_v2_1"]) == ()
    assert not selection_requires_aerial_camera(selection)


def test_realsense_is_the_only_mushr_camera_provider():
    attachments = ["Camera", "REALSENSE_D455", "navigation_IO"]
    ids = _ids(attachments)
    selection = {
        "robots": [{
            "type": "mushr_v2",
            "attachments": [{"type": item} for item in attachments],
        }]
    }
    assert aerial_sensor_specs_from_selection(selection, ["mushr_v2_1"]) == ()
    assert selection_requires_aerial_camera(selection)
    assert "ros.mushr_camera_image" not in ids
    assert "ros.mushr_camera_info" not in ids
    assert "ros.realsense_d455.rgb_image" in ids
    assert "ros.realsense_d455.depth_image" in ids
    assert "ros.realsense_d455.camera_info" in ids
    assert "ros.realsense_d455.imu" in ids


def test_attachment_gate_normalizes_scalar_and_list_schema(tmp_path: Path):
    path = tmp_path / "gates.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "sensor.test",
                "name": "Test",
                "category": "robot",
                "models": ["mushr_v2"],
                "interfaces": [
                    {
                        "id": "test.gated",
                        "name": "gated",
                        "direction": "output",
                        "protocol": "python",
                        "kind": "method",
                        "endpoint": "gated",
                        "data_type": "test",
                        "requires_attachment": ["Camera", "Navigation_IO"],
                        "excludes_attachment": ["Realsense_D455", "other"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    device = _load_device(path)
    catalog = type(load_catalog())(devices=(device,))
    assert device.interfaces[0].requires_attachments == ("camera", "navigation_io")
    assert device.interfaces[0].excludes_attachments == ("realsense_d455", "other")
    required_selection = {
        "robots": [{
            "type": "mushr_v2",
            "attachments": [{"type": item} for item in ("CAMERA", "navigation_io")],
        }]
    }
    excluded_selection = {
        "robots": [{
            "type": "mushr_v2",
            "attachments": [
                {"type": item}
                for item in ("camera", "navigation_io", "REALSENSE_D455")
            ],
        }]
    }
    assert resolve_scene_interfaces(catalog, required_selection, env_name="test")
    assert not resolve_scene_interfaces(catalog, excluded_selection, env_name="test")


@pytest.mark.parametrize("field", ["requires_attachment", "excludes_attachment"])
@pytest.mark.parametrize("value", ["   ", ["camera", "   "]])
def test_attachment_gate_rejects_whitespace_only_values(
    tmp_path: Path, field: str, value: str | list[str]
):
    path = tmp_path / "invalid-gate.yaml"
    interface = {
        "id": "test.invalid_gate",
        "name": "invalid gate",
        "protocol": "python",
        "direction": "output",
        "kind": "method",
        "endpoint": "invalid_gate",
        "data_type": "test",
        field: value,
    }
    path.write_text(
        yaml.safe_dump(
            {
                "id": "sensor.test",
                "name": "Test",
                "category": "robot",
                "models": ["mushr_v2"],
                "interfaces": [interface],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        _load_device(path)
