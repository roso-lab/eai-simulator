import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from benchmark.room06_runtime_preflight_v1 import (
    FORMAL_GATES,
    INPUT_SCHEMA,
    TASK_SETUP_SCHEMA,
    Room06RuntimePreflightError,
    audit_room06_runtime_preflight,
    require_room06_runtime_preflight,
)


SAVE_ID = "1234567890abcdef1234567890abcdef"
BINDING_DIGEST = "a" * 64
POINTS = (
    ("floor1-bookshelf", "node:start"),
    ("floor2-bookshelf-1", "node:target1"),
    ("floor2-bookshelf-2", "node:target2"),
    ("floor2-bookshelf-3", "node:target3"),
)
SKILLS = [
    {
        "skill_id": "eai.navigation.node_route",
        "provider_version": "2.0",
        "schema_version": "simulator.skill.v1",
        "backend": "NavigationControllerAdapter",
    },
    {
        "skill_id": "eai.manipulation.pick_place",
        "provider_version": "1.0.0",
        "schema_version": "simulator.skill.v1",
        "backend": "ArmPickPlaceAdapter",
    },
]


def _canonical_digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _map_bundle(tmp_path: Path, scene_digest: str) -> Path:
    current = tmp_path / "semantic_map" / "current"
    current.mkdir(parents=True)
    topology = {
        "schema": "eai.navigation.topology",
        "version": 2,
        "scene_key": "living_room_06",
        "coordinate_frame": "scene_world",
        "meters_per_unit": 1.0,
        "up_axis": "Z",
        "domain": "ground",
        "levels": [
            {"id": "1F", "support_z_m": 0.0},
            {"id": "2F", "support_z_m": 2.58},
        ],
        "source": {"usd_layer_0000": scene_digest},
        "nodes": [
            {"id": "node:start", "kind": "custom", "level_id": "1F", "position_xyz": [0, 0, 0]},
            {"id": "node:handoff", "kind": "transition", "level_id": "2F", "position_xyz": [1, 0, 2.58]},
            {"id": "node:target1", "kind": "custom", "level_id": "2F", "position_xyz": [2, 0, 2.58]},
            {"id": "node:target2", "kind": "custom", "level_id": "2F", "position_xyz": [3, 0, 2.58]},
            {"id": "node:target3", "kind": "custom", "level_id": "2F", "position_xyz": [4, 0, 2.58]},
        ],
        "edges": [
            {
                "id": "edge:cross",
                "from": "node:start",
                "to": "node:handoff",
                "directed": False,
                "traversal_mode": "stairs",
            },
            *[
                {
                    "id": f"edge:target{index}",
                    "from": "node:handoff",
                    "to": f"node:target{index}",
                    "directed": False,
                    "traversal_mode": "walk",
                }
                for index in range(1, 4)
            ],
        ],
    }
    points = {
        "schema": "eai.semantic_points",
        "version": 2,
        "scene_key": "living_room_06",
        "coordinate_frame": "scene_world",
        "points": [
            {
                "id": f"semantic:{index}",
                "label": label,
                "navigation_bindings": [{"domain": "ground", "node_id": node}],
            }
            for index, (label, node) in enumerate(POINTS)
        ],
    }
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
    }
    _write_json(current / "topology.json", topology)
    _write_json(current / "semantic-points.json", points)
    _write_json(current / "semantic-footprints.json", footprints)
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
    _write_json(current.parent / "project.json", project)
    return current


def _replace_topology(current: Path, topology, spec):
    _write_json(current / "topology.json", topology)
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["topology.json"] = _digest(current / "topology.json")
    _write_json(current / "manifest.json", manifest)
    spec["semantic_map_identity"]["topology_digest"] = _digest(
        current / "topology.json"
    )


def _robot(instance_id: str, controller: str = "B2_MIXED_STAIRS_CFG"):
    source_input = {
        "source_resume_id": f"{instance_id}.source",
        "motion_controller": controller,
        "manipulator_controller": "D1_IK_CFG",
        "external_manipulator": "d1",
        "skills": deepcopy(SKILLS),
        "skill_fingerprint_policy": "skill_change_invalidates_capability_and_runtime_receipt",
    }
    return {
        "instance_id": instance_id,
        "restrictions": [],
        "runtime_qualified": True,
        "runtime_qualification_receipt": {"status": "qualified"},
        "capability_binding": {
            "skill_bindings": deepcopy(SKILLS),
            "source_input": source_input,
            "capability_digest": _canonical_digest(source_input),
        },
    }


def _roster(path: Path, robots=None):
    value = {
        "schema_id": "heterocoord.robot_runtime_roster",
        "formal_gates": deepcopy(FORMAL_GATES),
        "robots": robots or [_robot("hc-rt-a"), _robot("hc-rt-b")],
    }
    _write_json(path, value)
    return value


def _task_setup():
    return {
        "schema": TASK_SETUP_SCHEMA,
        "task_id": "liv-t-04",
        "objects": [
            {
                "entity_id": "book_batch_01",
                "definition": {"kind": "book_batch", "count": 6},
            },
            {
                "entity_id": "heavy_object",
                "definition": {"kind": "logical_load", "mass_class": "heavy"},
            },
        ],
    }


def _binding(scene_digest: str, current: Path, roster_path: Path, setup):
    definitions = {row["entity_id"]: row["definition"] for row in setup["objects"]}
    return {
        "schema": INPUT_SCHEMA,
        "scene_key": "living_room_06",
        "scene_asset_digest": scene_digest,
        "semantic_map_identity": {
            "save_id": SAVE_ID,
            "semantic_binding_digest": BINDING_DIGEST,
            "topology_digest": _digest(current / "topology.json"),
        },
        "transition_handoff_node_id": "node:handoff",
        "selected_robot_ids": ["hc-rt-a", "hc-rt-b"],
        "cross_floor_robot_ids": ["hc-rt-a"],
        "robot_roster_digest": _digest(roster_path),
        "task_object_bindings": {
            "schema": "heterocoord.task-object-bindings.v1",
            "scene_digest": scene_digest,
            "objects": [
                {
                    "entity_id": entity_id,
                    "origin": "task_generated",
                    "definition_ref": f"task-setup:liv-t-04/{entity_id}",
                    "definition_digest": _canonical_digest(definitions[entity_id]),
                }
                for entity_id in ("book_batch_01", "heavy_object")
            ],
        },
        "formal_gates": deepcopy(FORMAL_GATES),
    }


@pytest.fixture
def valid_inputs(tmp_path):
    usd = tmp_path / "room_06.usda"
    usd.write_bytes(b"#usda 1.0\ndef Xform \"World\" {}\n")
    scene_digest = _digest(usd)
    current = _map_bundle(tmp_path, scene_digest)
    roster_path = tmp_path / "roster.json"
    _roster(roster_path)
    setup = _task_setup()
    spec = _binding(scene_digest, current, roster_path, setup)
    return current, usd, roster_path, spec, setup


def _audit(inputs):
    current, usd, roster, spec, setup = inputs
    return audit_room06_runtime_preflight(
        semantic_root=current,
        scene_usd=usd,
        robot_roster=roster,
        binding=spec,
        task_setup=setup,
    )


def test_minimal_cross_floor_boundary_passes_without_opening_any_gate(valid_inputs):
    result = _audit(valid_inputs)

    assert result["preflight_passed"] is True
    assert result["scene_smoke_may_start"] is True
    assert result["room_specific_feasibility"] is False
    assert result["formal_gates"] == FORMAL_GATES
    assert result["runtime_binding_digest"] == _canonical_digest(valid_inputs[3])
    assert result["transition_handoff_node_id"] == "node:handoff"
    assert result["semantic_map"]["route_precomputed"] is False
    assert result["semantic_map"]["navigation_authority"] == "system_runtime"
    assert all(row["room_specific_feasibility"] is False for row in result["robots"])


def test_require_wrapper_raises_the_exact_fail_closed_reason(valid_inputs):
    current, usd, roster, spec, setup = valid_inputs
    spec["scene_asset_digest"] = "0" * 64

    with pytest.raises(Room06RuntimePreflightError, match="scene USD digest mismatch"):
        require_room06_runtime_preflight(
            semantic_root=current,
            scene_usd=usd,
            robot_roster=roster,
            binding=spec,
            task_setup=setup,
        )


def test_accepted_map_without_cross_floor_route_passes_static_input_check():
    from benchmark.case_input_audit_v1 import audit_semantic_map
    from benchmark.room06_runtime_preflight_v1 import _map_and_targets
    current = Path(__file__).resolve().parents[2] / "usd/scene/Living and public space/room_06/semantic_map/current"
    topology = json.loads((current / "topology.json").read_text())
    project = json.loads((current.parent / "project.json").read_text())
    assert audit_semantic_map(current)["case_ready"] is True
    result = _map_and_targets(
        current, scene_digest=topology["source"]["usd_layer_0000"],
        identity={
            "save_id": project["save_id"],
            "semantic_binding_digest": project["semantic_binding_digest"],
            "topology_digest": _digest(current / "topology.json"),
        },
        transition_handoff_node_id="node:0001",
        point_labels={label: label for label, _ in POINTS},
    )
    assert result["route_precomputed"] is False
    assert result["room_specific_feasibility"] is False
    assert result["target_bindings"]["floor1-bookshelf"] == ["node:0001"]


def test_case_can_bind_custom_node_as_handoff_without_rewriting_map_kind(valid_inputs):
    current, _, _, spec, _ = valid_inputs
    topology = json.loads((current / "topology.json").read_text())
    topology["nodes"][1]["kind"] = "custom"
    _replace_topology(current, topology, spec)

    result = _audit(valid_inputs)

    assert result["preflight_passed"] is True
    assert result["formal_gates"] == FORMAL_GATES


def test_map_without_cross_floor_edges_does_not_claim_or_require_a_route(valid_inputs):
    current, _, _, spec, _ = valid_inputs
    topology = json.loads((current / "topology.json").read_text())
    topology["edges"] = [edge for edge in topology["edges"] if edge["id"] != "edge:cross"]
    for node in topology["nodes"]:
        node["kind"] = "custom"
    _replace_topology(current, topology, spec)
    before = (current / "topology.json").read_bytes()

    result = _audit(valid_inputs)

    assert result["preflight_passed"] is True
    assert result["semantic_map"]["route_precomputed"] is False
    assert result["room_specific_feasibility"] is False
    assert (current / "topology.json").read_bytes() == before


def test_handoff_must_still_identify_an_existing_observation_node(valid_inputs):
    valid_inputs[3]["transition_handoff_node_id"] = "node:unknown"
    result = _audit(valid_inputs)
    assert result["preflight_passed"] is False
    assert "absent from ground topology" in result["failures"][0]


def test_exact_floor_qualified_target_labels_can_be_bound(valid_inputs):
    current, _, _, spec, _ = valid_inputs
    points = json.loads((current / "semantic-points.json").read_text())
    labels = dict(zip((label for label, _ in POINTS),
                      ["一楼.书架", "二楼.书架1", "二楼.书架2", "二楼.书架3"]))
    for point in points["points"]:
        point["label"] = labels[point["label"]]
    _write_json(current / "semantic-points.json", points)
    manifest = json.loads((current / "manifest.json").read_text())
    manifest["files"]["semantic-points.json"] = _digest(current / "semantic-points.json")
    _write_json(current / "manifest.json", manifest)
    spec["semantic_point_labels"] = labels
    result = _audit(valid_inputs)
    assert result["preflight_passed"] is True
    assert result["semantic_map"]["semantic_point_labels"] == labels
    assert result["semantic_map"]["target_bindings"]["floor2-bookshelf-1"] == ["node:target1"]
    spec["semantic_point_labels"]["floor2-bookshelf-1"] = "二楼.不存在"
    assert _audit(valid_inputs)["preflight_passed"] is False


@pytest.mark.parametrize("labels", [None, {}, {
    "floor1-bookshelf": "书架", "floor2-bookshelf-1": "书架",
    "floor2-bookshelf-2": "书架", "floor2-bookshelf-3": "书架",
}])
def test_missing_or_ambiguous_target_label_mapping_rejected(valid_inputs, labels):
    valid_inputs[3]["semantic_point_labels"] = labels
    assert _audit(valid_inputs)["preflight_passed"] is False


def test_unknown_robot_is_rejected(valid_inputs):
    _, _, _, spec, _ = valid_inputs
    spec["selected_robot_ids"][1] = "hc-rt-missing"

    result = _audit(valid_inputs)

    assert "selected robots are absent from roster" in result["failures"][0]


def test_missing_required_robot_skill_is_rejected(valid_inputs):
    _, _, roster_path, spec, _ = valid_inputs
    roster = json.loads(roster_path.read_text())
    robot = roster["robots"][1]
    robot["capability_binding"]["skill_bindings"] = [deepcopy(SKILLS[0])]
    robot["capability_binding"]["source_input"]["skills"] = [deepcopy(SKILLS[0])]
    robot["capability_binding"]["capability_digest"] = _canonical_digest(
        robot["capability_binding"]["source_input"]
    )
    _write_json(roster_path, roster)
    spec["robot_roster_digest"] = _digest(roster_path)

    result = _audit(valid_inputs)

    assert "lacks liv-t-04 skills" in result["failures"][0]


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("restriction", "restrictions conflict with liv-t-04"),
        ("controller", "has no frozen stair controller"),
        ("digest", "capability digest mismatch"),
    ],
)
def test_robot_restriction_controller_and_capability_digest_fail_closed(
    valid_inputs, mutation, expected
):
    _, _, roster_path, spec, _ = valid_inputs
    roster = json.loads(roster_path.read_text())
    robot = roster["robots"][0]
    if mutation == "restriction":
        robot["restrictions"] = ["LITE3_STAIRS_UNSUPPORTED"]
    elif mutation == "controller":
        robot["capability_binding"]["source_input"]["motion_controller"] = "FLAT_ONLY"
        robot["capability_binding"]["capability_digest"] = _canonical_digest(
            robot["capability_binding"]["source_input"]
        )
    else:
        robot["capability_binding"]["capability_digest"] = "f" * 64
    _write_json(roster_path, roster)
    spec["robot_roster_digest"] = _digest(roster_path)

    result = _audit(valid_inputs)

    assert expected in result["failures"][0]


def test_missing_or_tampered_task_definition_digest_is_rejected(valid_inputs):
    _, _, _, spec, _ = valid_inputs
    spec["task_object_bindings"]["objects"][0]["definition_digest"] = "f" * 64

    result = _audit(valid_inputs)

    assert result["failures"] == [
        "task object definition digest mismatch: book_batch_01"
    ]


def test_map_identity_and_formal_gate_tampering_are_rejected(valid_inputs):
    _, _, _, spec, _ = valid_inputs
    spec["semantic_map_identity"]["topology_digest"] = "f" * 64
    result = _audit(valid_inputs)
    assert result["failures"] == ["ground topology digest mismatch"]

    spec["semantic_map_identity"]["topology_digest"] = valid_inputs[3][
        "semantic_map_identity"
    ]["topology_digest"]
    spec["formal_gates"]["physical_qualified"] = True
    result = _audit(valid_inputs)
    assert result["failures"] == ["runtime preflight formal gates must remain closed"]
    assert result["formal_gates"] == FORMAL_GATES
