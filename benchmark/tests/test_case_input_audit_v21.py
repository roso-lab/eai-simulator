import hashlib
import json
from pathlib import Path

import pytest

from benchmark.case_input_audit_v1 import CaseInputAuditError, audit_semantic_map


SAVE_ID = "1234567890abcdef1234567890abcdef"
BINDING_DIGEST = "a" * 64
REQUIRED_POINTS = (
    "floor1-bookshelf",
    "floor2-bookshelf-1",
    "floor2-bookshelf-2",
    "floor2-bookshelf-3",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_bundle(tmp_path: Path) -> Path:
    map_root = tmp_path / "semantic_map"
    current = map_root / "current"
    current.mkdir(parents=True)
    footprints = {
        "schema": "eai.semantic_footprints",
        "version": 1,
        "scene_id": "living_room_06",
        "project_revision": SAVE_ID,
        "coordinate_frame": "scene_world",
        "semantics": {
            "schema": "eai.scene.semantics",
            "version": 1,
            "regions": [],
            "entities": [],
            "interaction_poses": [],
            "routes": [],
        },
        "robot_validated": False,
    }
    topology = {
        "schema": "eai.navigation.topology",
        "version": 2,
        "scene_key": "living_room_06",
        "coordinate_frame": "scene_world",
        "meters_per_unit": 1.0,
        "up_axis": "Z",
        "domain": "ground",
        "nodes": [{"id": "node:0001"}, {"id": "node:0002"}],
        "edges": [{"id": "edge:0001", "source": "node:0001", "target": "node:0002"}],
    }
    points = {
        "schema": "eai.semantic_points",
        "version": 2,
        "scene_key": "living_room_06",
        "coordinate_frame": "scene_world",
        "points": [
            {
                "id": f"semantic:{index:04d}",
                "label": label,
                "isaac_binding": None,
                "navigation_bindings": [
                    {"domain": "ground", "node_id": "node:0001"}
                ],
            }
            for index, label in enumerate(REQUIRED_POINTS)
        ],
    }
    _write_json(current / "semantic-footprints.json", footprints)
    _write_json(current / "topology.json", topology)
    _write_json(current / "semantic-points.json", points)
    manifest = {
        "schema": "eai.semantic_map.revision",
        "version": 2,
        "revision": SAVE_ID,
        "scene_key": "living_room_06",
        "navigation_products": {"ground": "topology.json"},
        "semantic_point_product": "semantic-points.json",
        "semantic_binding_digest": BINDING_DIGEST,
        "files": {
            name: _digest(current / name)
            for name in (
                "semantic-footprints.json",
                "topology.json",
                "semantic-points.json",
            )
        },
        "robot_validated": False,
    }
    project = {
        "schema": "eai.semantic_map.project",
        "version": 2,
        "scene_key": "living_room_06",
        "coordinate_frame": "scene_world",
        "current_revision": "current",
        "save_id": SAVE_ID,
        "semantic_binding_digest": BINDING_DIGEST,
    }
    _write_json(current / "manifest.json", manifest)
    _write_json(map_root / "project.json", project)
    return current


def _replace_product(current: Path, name: str, value: object) -> None:
    _write_json(current / name, value)
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][name] = _digest(current / name)
    _write_json(current / "manifest.json", manifest)


def _requirements(**overrides: list[str]) -> dict[str, list[str]]:
    result = {
        "regions": [],
        "entities": [],
        "interaction_poses": [],
        "routes": [],
        "semantic_points": list(REQUIRED_POINTS),
    }
    result.update(overrides)
    return result


def test_v21_accepts_empty_footprint_layers_and_ignores_robot_and_isaac_flags(tmp_path):
    current = _revision_bundle(tmp_path)

    result = audit_semantic_map(current, semantic_requirements=_requirements())

    assert result["case_ready"] is True
    assert result["failures"] == []
    assert result["format_contract_version"] == "2.1"
    assert result["version_chain"] == {
        "project_current_revision": "current",
        "project_save_id": SAVE_ID,
        "manifest_revision": SAVE_ID,
        "footprints_project_revision": SAVE_ID,
    }
    assert result["semantic_summary"]["counts"] == {
        "regions": 0,
        "entities": 0,
        "interaction_poses": 0,
        "routes": 0,
    }
    assert result["semantic_summary"]["semantic_point_count"] == 4


@pytest.mark.parametrize("broken_link", ["project", "footprints"])
def test_save_id_chain_mismatch_fails_closed(tmp_path, broken_link):
    current = _revision_bundle(tmp_path)
    if broken_link == "project":
        project_path = current.parent / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["save_id"] = "fedcba0987654321fedcba0987654321"
        _write_json(project_path, project)
    else:
        footprints = json.loads(
            (current / "semantic-footprints.json").read_text(encoding="utf-8")
        )
        footprints["project_revision"] = "fedcba0987654321fedcba0987654321"
        _replace_product(current, "semantic-footprints.json", footprints)

    result = audit_semantic_map(current, semantic_requirements=_requirements())

    assert result["case_ready"] is False
    assert any("save_id chain mismatch" in failure for failure in result["failures"])


def test_project_current_revision_must_select_the_audited_bundle(tmp_path):
    current = _revision_bundle(tmp_path)
    project_path = current.parent / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["current_revision"] = "revisions/not-current"
    _write_json(project_path, project)

    result = audit_semantic_map(current, semantic_requirements=_requirements())

    assert result["case_ready"] is False
    assert any("does not select audited revision" in failure for failure in result["failures"])


@pytest.mark.parametrize("bad_label", ["", "floor1-bookshelf"])
def test_semantic_point_labels_must_be_nonempty_and_unique(tmp_path, bad_label):
    current = _revision_bundle(tmp_path)
    points = json.loads((current / "semantic-points.json").read_text(encoding="utf-8"))
    points["points"][1]["label"] = bad_label
    _replace_product(current, "semantic-points.json", points)

    result = audit_semantic_map(current, semantic_requirements=_requirements())

    assert result["case_ready"] is False
    expected = "label is empty" if not bad_label else "label is duplicated"
    assert any(expected in failure for failure in result["failures"])


def test_empty_semantic_point_directory_fails_closed(tmp_path):
    current = _revision_bundle(tmp_path)
    points = json.loads((current / "semantic-points.json").read_text(encoding="utf-8"))
    points["points"] = []
    _replace_product(current, "semantic-points.json", points)

    result = audit_semantic_map(current)

    assert result["case_ready"] is False
    assert "semantic-points.points is empty" in result["failures"]


@pytest.mark.parametrize("binding", [[], [{"domain": "ground", "node_id": "node:missing"}]])
def test_semantic_point_navigation_binding_must_be_nonempty_and_resolve(tmp_path, binding):
    current = _revision_bundle(tmp_path)
    points = json.loads((current / "semantic-points.json").read_text(encoding="utf-8"))
    points["points"][0]["navigation_bindings"] = binding
    _replace_product(current, "semantic-points.json", points)

    result = audit_semantic_map(current, semantic_requirements=_requirements())

    assert result["case_ready"] is False
    assert any("navigation" in failure for failure in result["failures"])


def test_anchor_requirements_not_generic_layer_emptiness_control_readiness(tmp_path):
    current = _revision_bundle(tmp_path)

    allowed = audit_semantic_map(current, semantic_requirements=_requirements())
    required_region = audit_semantic_map(
        current,
        semantic_requirements=_requirements(regions=["handoff-zone"]),
    )
    required_point = audit_semantic_map(
        current,
        semantic_requirements=_requirements(
            semantic_points=[*REQUIRED_POINTS, "heavy-object-destination"]
        ),
    )

    assert allowed["case_ready"] is True
    assert required_region["case_ready"] is False
    assert any("required semantic regions" in failure for failure in required_region["failures"])
    assert required_point["case_ready"] is False
    assert any("required semantic points" in failure for failure in required_point["failures"])


def test_malformed_anchor_requirements_are_rejected(tmp_path):
    current = _revision_bundle(tmp_path)

    with pytest.raises(CaseInputAuditError, match="unsupported collections"):
        audit_semantic_map(current, semantic_requirements={"isaac_binding": ["/World/Book"]})
