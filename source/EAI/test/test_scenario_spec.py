"""Focused offline checks for the Factory V2 scenario contract.

Covers JSON/schema gates, semantic gates, synthetic-map geometry gates, the
V1/V2 selection round-trip, storage versioning, and requirement expansion.
No Isaac, ROS, network, or provider access.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

from EAI.hmrs_env.env_diy.flow import (
    interactive_selection_from_dict,
    interactive_selection_to_dict,
)
from EAI.hmrs_env.env_diy.storage import _normalize_task_payload
from EAI.hmrs_env.env_diy import catalog
from EAI.scenario_spec import ScenarioError, validate_scenario
from EAI.scenario_spec.geometry import FactoryMap, validate_geometry
from EAI.scenario_spec.validation import parse_scenario

ROOT = Path(__file__).resolve().parents[3]


def _base_scenario() -> dict:
    return {
        "version": 2,
        "task_name": "factory_transfer_test",
        "scene_key": "factory",
        "seed": 17,
        "robots": [
            {
                "id": "carter_1",
                "type": "carter",
                "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
                "attachments": [
                    {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}}
                ],
                "spawn_pose": {
                    "position": [-7.95, -8.6, 0.2],
                    "rotation": [1.0, 0.0, 0.0, 0.0],
                },
            },
            {
                "id": "m20_1",
                "type": "m20",
                "controller": {"mode": "default", "cfg": "M20_ROUGH_RSL_CFG"},
                "attachments": [],
                "spawn_pose": {
                    "position": [-1.95, -8.6, 0.52],
                    "rotation": [1.0, 0.0, 0.0, 0.0],
                },
            },
        ],
        "composition": {
            "entities": [
                {
                    "id": "cargo_1",
                    "asset_key": "primitive_box",
                    "size_m": [0.5, 0.4, 0.4],
                    "spawn_pose": {
                        "position": [-7.95, -7.1, 0.2],
                        "rotation": [1.0, 0.0, 0.0, 0.0],
                    },
                    "physics": {
                        "mode": "dynamic",
                        "mass_kg": 12.0,
                        "static_friction": 0.8,
                        "dynamic_friction": 0.6,
                        "restitution": 0.05,
                    },
                },
            ],
            "targets": [
                {"id": "pickup_zone", "position": [-7.95, -5.6, 0.0], "radius_m": 0.5},
                {"id": "delivery_zone", "position": [-1.95, -5.6, 0.0], "radius_m": 0.5},
            ],
        },
        "phase_graph": {
            "nodes": [
                {
                    "id": "move_to_cargo",
                    "skill_key": "navigate",
                    "preferred_agent": "carter_1",
                    "target_ref": "pickup_zone",
                    "object_ref": None,
                    "prerequisites": [],
                    "timeout_s": 60.0,
                },
                {
                    "id": "transport_cargo",
                    "skill_key": "transport_object",
                    "preferred_agent": "carter_1",
                    "target_ref": "delivery_zone",
                    "object_ref": "cargo_1",
                    "prerequisites": ["move_to_cargo"],
                    "timeout_s": 120.0,
                },
            ]
        },
    }


def _validated(mutate=None) -> dict:
    data = _base_scenario()
    if mutate is not None:
        mutate(data)
    return validate_scenario(data)


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("duplicate_target_id", lambda d: d["composition"]["targets"].append(
            copy.deepcopy(d["composition"]["targets"][0]))),
        ("free_robot_id", lambda d: d["robots"][0].__setitem__("id", "herbie")),
        ("non_yaw_rotation", lambda d: d["robots"][0]["spawn_pose"].__setitem__(
            "rotation", [0.7, 0.7, 0.0, 0.0])),
        ("wrong_spawn_z", lambda d: d["robots"][0]["spawn_pose"]["position"].__setitem__(2, 0.4)),
        ("unsupported_mode", lambda d: d["composition"]["entities"][0]["physics"].__setitem__(
            "mode", "kinematic")),
        ("mass_forbidden_for_static", lambda d: d["composition"]["entities"][0]["physics"].update(
            {"mode": "static", "mass_kg": None})),
        ("missing_mass_when_dynamic", lambda d: d["composition"]["entities"][0]["physics"].__setitem__(
            "mass_kg", None)),
        ("friction_order", lambda d: d["composition"]["entities"][0]["physics"].update(
            {"static_friction": 0.6, "dynamic_friction": 0.8})),
        ("floating_entity", lambda d: d["composition"]["entities"][0]["spawn_pose"]["position"].__setitem__(2, 1.5)),
        ("unknown_target_ref", lambda d: d["phase_graph"]["nodes"][0].__setitem__("target_ref", "atlantis")),
        ("unknown_prerequisite", lambda d: d["phase_graph"]["nodes"][0].__setitem__(
            "prerequisites", ["never_defined"])),
        ("cycle", lambda d: d["phase_graph"]["nodes"][0].__setitem__(
            "prerequisites", ["transport_cargo"])),
        ("no_manipulation_robot", lambda d: d["robots"][0].__setitem__("attachments", [])),
        ("preferred_agent_lacks_equipment", lambda d: d["phase_graph"]["nodes"][1].__setitem__(
            "preferred_agent", "m20_1")),
        ("object_required", lambda d: d["phase_graph"]["nodes"][1].__setitem__("object_ref", None)),
        ("object_must_be_dynamic", lambda d: d["composition"]["entities"][0]["physics"].update(
            {"mode": "static", "mass_kg": None})),
        ("duplicate_attachment", lambda d: d["robots"][0].__setitem__("attachments", [
            {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}},
            {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}},
        ])),
        ("invalid_attachment_controller", lambda d: d["robots"][0]["attachments"][0].__setitem__(
            "controller", None)),
        ("target_off_ground", lambda d: d["composition"]["targets"][0]["position"].__setitem__(2, 0.5)),
        ("unknown_scene", lambda d: d.__setitem__("scene_key", "mars")),
        ("unknown_robot_type", lambda d: d["robots"][0].__setitem__("type", "atlas")),
    ],
)
def test_scenario_is_rejected(name, mutate) -> None:
    data = _base_scenario()
    mutate(data)
    with pytest.raises(ScenarioError):
        validate_scenario(data)


def test_valid_scenario_normalizes_spawn_poses() -> None:
    data = _validated()
    pose = data["robots"][0]["spawn_pose"]
    assert pose["position"] == [-7.95, -8.6, 0.2]
    assert pose["rotation"] == [1.0, 0.0, 0.0, 0.0]


def test_parse_scenario_rejects_duplicate_keys_and_non_finite() -> None:
    with pytest.raises(ScenarioError):
        parse_scenario('{"version": 2, "version": 2}')
    with pytest.raises(ScenarioError):
        parse_scenario('{"version": NaN}')


def _synthetic_map(tmp_path: Path) -> FactoryMap:
    from PIL import Image

    pixels = bytearray(b"\xff" * (20 * 20))
    for row in range(20):
        for col in (9, 10):
            pixels[row * 20 + col] = 0
    image_path = tmp_path / "map.png"
    Image.frombytes("L", (20, 20), bytes(pixels)).save(image_path)
    yaml_path = tmp_path / "map.yaml"
    yaml_path.write_text(
        "image: map.png\nresolution: 0.5\norigin: [0.0, 0.0, 0.0]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n",
        encoding="utf-8",
    )
    return FactoryMap(yaml_path)


def test_synthetic_map_marks_wall_strip_blocked(tmp_path: Path) -> None:
    occupancy = _synthetic_map(tmp_path)
    assert occupancy.rectangle_free(2.0, 2.0, 0.2, 0.2)
    assert not occupancy.rectangle_free(5.0, 2.0, 0.2, 0.2)
    assert not occupancy.rectangle_free(50.0, 2.0, 0.2, 0.2)


def test_geometry_accepts_free_placement(tmp_path: Path) -> None:
    def move_to_free_map(data: dict) -> dict:
        data["robots"][0]["spawn_pose"]["position"] = [1.0, 1.0, 0.2]
        data["robots"][1]["spawn_pose"]["position"] = [8.0, 8.0, 0.52]
        data["composition"]["entities"][0]["spawn_pose"]["position"] = [2.0, 3.0, 0.2]
        data["composition"]["targets"][0]["position"] = [8.0, 3.0, 0.0]
        data["composition"]["targets"][1]["position"] = [1.0, 6.0, 0.0]
        return data

    data = validate_scenario(move_to_free_map(_base_scenario()))
    report = validate_geometry(data, _synthetic_map(tmp_path))
    assert report["status"] == "placement_validated"
    assert report["oracle_validated"] is False
    assert report["map_sha256"]


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("robot_on_wall", lambda d: d["robots"][0]["spawn_pose"]["position"].__setitem__(0, 5.0)),
        ("entity_on_wall", lambda d: d["composition"]["entities"][0]["spawn_pose"]["position"].__setitem__(0, 5.0)),
        ("target_on_wall", lambda d: d["composition"]["targets"][0]["position"].__setitem__(0, 5.0)),
        ("footprints_overlap", lambda d: d["composition"]["entities"][0]["spawn_pose"]["position"].__setitem__(
            0, 1.0) or d["composition"]["entities"][0]["spawn_pose"]["position"].__setitem__(1, 1.0)),
    ],
)
def test_geometry_rejects_bad_placement(tmp_path, name, mutate) -> None:
    def base_free(data: dict) -> dict:
        data["robots"][0]["spawn_pose"]["position"] = [1.0, 1.0, 0.2]
        data["robots"][1]["spawn_pose"]["position"] = [8.0, 8.0, 0.52]
        data["composition"]["entities"][0]["spawn_pose"]["position"] = [2.0, 3.0, 0.2]
        data["composition"]["targets"][0]["position"] = [8.0, 3.0, 0.0]
        data["composition"]["targets"][1]["position"] = [1.0, 6.0, 0.0]
        return data

    data = validate_scenario(base_free(_base_scenario()))
    mutate(data)
    with pytest.raises(ScenarioError):
        validate_geometry(data, _synthetic_map(tmp_path))


def test_v1_selection_round_trip_is_unchanged() -> None:
    payload = {
        "scene_key": "plane",
        "robots": [
            {
                "type": "carter",
                "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
                "attachments": [{"type": "keyboard"}],
            }
        ],
    }
    selection = interactive_selection_from_dict(payload)
    assert selection.scenario_json is None
    data = interactive_selection_to_dict(selection)
    assert "version" not in data
    assert data["robots"][0]["attachments"][0]["type"] == "keyboard"


def test_v2_selection_keeps_scenario_payload() -> None:
    selection = interactive_selection_from_dict(_base_scenario())
    assert selection.scenario_json is not None
    data = interactive_selection_to_dict(selection)
    assert data["version"] == 2
    assert data["robots"][0]["spawn_pose"]["position"] == [-7.95, -8.6, 0.2]
    assert data["phase_graph"]["nodes"][1]["object_ref"] == "cargo_1"
    assert selection.robots[0].spawn_pose is not None


def test_storage_routes_versions() -> None:
    normalized = _normalize_task_payload("factory_transfer_test", _base_scenario())
    assert normalized["version"] == 2

    mixed = {"scene_key": "plane", "robots": [], "composition": {"entities": [], "targets": []}}
    with pytest.raises(ValueError, match="version 2"):
        _normalize_task_payload("mixed", mixed)

    with pytest.raises(ValueError, match="Unsupported environment version"):
        _normalize_task_payload("future", {"version": 3, "scene_key": "plane", "robots": []})


def test_requirements_expand_scenario_objects() -> None:
    from EAI_assets.asset_requirements import resolve_selection

    graph = resolve_selection(_base_scenario())
    ids = {item.id for item in graph.requirements}
    assert "scene:factory" in ids
    assert "scenario_object:primitive_box" in ids
    assert "robot:carter" in ids
    assert "controller:M20_ROUGH_RSL_CFG" in ids


def test_generator_manifest_has_no_asset_paths() -> None:
    sys.path.insert(0, str(ROOT))
    try:
        from tools.scenario_generation.common import manifest
    finally:
        sys.path.remove(str(ROOT))

    view = manifest()
    rendered = json.dumps(view)
    assert ".usd" not in rendered
    assert ".py" not in rendered
    assert {robot["type"] for robot in view["robots"]} == set(catalog.robot_keys())
    assert view["layout_mode"] == "existing_factory_ground_floor_overlay"
    assert any("payload" in constraint.lower() for constraint in view["constraints"])
