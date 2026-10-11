from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from tools.topology_authoring.adapter import SceneSource, SemanticTarget
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import (
    generate_product,
    load_product,
    validate_document,
)
from tools.topology_authoring.viewport import draw_overlay


SOURCE_DIGEST = "a" * 64


class TargetAdapter:
    scene_key = "warehouse"

    def semantic_targets(self, source):
        del source
        return [SemanticTarget("place:goal", "Goal")]

    def source_digests(self, source):
        del source
        return {"scene.json": SOURCE_DIGEST}

    def validate_polyline(self, polyline, profile, source):
        del polyline, profile, source
        return []


def _source(tmp_path: Path) -> SceneSource:
    return SceneSource("warehouse", tmp_path, {})


def _document() -> TopologyDocument:
    document = TopologyDocument("warehouse", {"scene.json": SOURCE_DIGEST})
    document.add_node([0.0, 0.0, 0.0], node_id="node:start")
    document.add_node(
        [2.0, 0.0, 0.0],
        node_id="node:goal",
        target_refs=["place:goal"],
    )
    document.add_edge(
        "node:start",
        "node:goal",
        [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        directed=True,
    )
    return document


def test_preview_direction_history_and_node_move_update_edge_endpoint():
    document = TopologyDocument("warehouse")
    document.begin_node_preview([0.0, 0.0, 0.0], support_ref="floor")
    assert document.pending_node.position_xyz == [0.0, 0.0, 0.0]
    document.cancel_node_preview()
    assert document.pending_node is None

    start = document.add_node([0.0, 0.0, 0.0], node_id="start")
    goal = document.add_node([2.0, 0.0, 0.0], node_id="goal")
    document.begin_edge_preview(start)
    document.update_edge_preview([2.0, 0.0, 0.0], snapped_node_id=goal)
    document.pending_edge.directed = True
    edge_id = document.confirm_edge_preview()

    assert document.edge(edge_id)["directed"] is True
    assert document.undo() is True
    assert document.edges == []
    assert document.redo() is True
    assert document.edge(edge_id)["to"] == goal

    document.move_node(goal, [3.0, 0.0, 0.0])
    assert document.edge(edge_id)["polyline_xyz"][-1] == [3.0, 0.0, 0.0]


def test_target_binding_serialization_and_product_round_trip(tmp_path):
    source = _source(tmp_path)
    adapter = TargetAdapter()
    document = _document()

    document.bind_target("node:start", "place:goal")
    document.set_edge_profile("edge:0001", "ground_small", 1.0)
    assert validate_document(document, adapter, source, require_targets=True) == []

    path = tmp_path / "authoring" / "navigation" / "ground" / "topology.json"
    assert generate_product(path, document, adapter, source, require_targets=True) == path
    loaded = load_product(path, adapter, source)
    assert loaded.to_dict() == document.to_dict()
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "eai.navigation.topology"


def test_validation_fails_closed_for_duplicate_geometry_and_profile():
    payload = _document().to_dict()
    payload["nodes"].append(deepcopy(payload["nodes"][0]))
    payload["nodes"][2]["position_xyz"][0] = float("nan")
    payload["edges"][0]["polyline_xyz"][0] = [99.0, 0.0, 0.0]
    payload["edges"][0]["profiles"]["ground_small"]["clearance_m"] = -1.0

    errors = validate_document(TopologyDocument.from_dict(payload))

    assert any("duplicate/invalid node id" in error for error in errors)
    assert any("invalid node position" in error for error in errors)
    assert any("edge endpoints do not match polyline" in error for error in errors)
    assert any("invalid profile ground_small" in error for error in errors)


def test_validation_rejects_stale_source_and_unknown_target(tmp_path):
    source = _source(tmp_path)
    adapter = TargetAdapter()
    payload = _document().to_dict()
    payload["source"]["scene.json"] = "b" * 64
    payload["nodes"][1]["target_refs"] = ["place:missing"]

    errors = validate_document(
        TopologyDocument.from_dict(payload),
        adapter,
        source,
        require_targets=True,
    )

    assert "source digest differs from current scene input" in errors
    assert "unknown target reference: place:missing" in errors
    assert "target has no entrance: place:goal" in errors


def test_headless_empty_adapter_does_not_import_isaac(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    asset_root = tmp_path / "assets"
    output_root = tmp_path / "output"
    asset_root.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "tools/topology_authoring/author.py",
            "--scene-key",
            "warehouse",
            "--adapter",
            "tools.topology_authoring.adapters.empty:EmptyTopologyAdapter",
            "--asset-root",
            str(asset_root),
            "--output-root",
            str(output_root),
            "--headless",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "scene_key": "warehouse",
        "nodes": 0,
        "edges": 0,
        "validation_errors": [],
    }


def test_viewport_overlay_is_serializable_without_isaac():
    document = _document()
    document.begin_node_preview([1.0, 1.0, 0.0])

    overlay = draw_overlay(None, document)

    assert [node["id"] for node in overlay["nodes"]] == ["node:start", "node:goal"]
    assert overlay["edges"][0]["directed"] is True
    assert overlay["pending_node"] == [1.0, 1.0, 0.0]
