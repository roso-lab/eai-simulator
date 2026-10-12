"""Pure multilevel authoring regressions; no Isaac or GPU imports."""
from __future__ import annotations

import json
from types import MappingProxyType, SimpleNamespace

import pytest

from tools.topology_authoring import author
from tools.topology_authoring.adapter import SceneSource
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import validate_document
from tools.topology_authoring.ui import TopologyAuthoringWindow
from tools.topology_authoring.viewport import (
    draw_overlay,
    visible_edges,
    visible_node_ids,
)


LEVELS = [
    {"id": "1f", "label": "First floor", "support_z_m": 0.0},
    {"id": "2f", "label": "Second floor", "support_z_m": 1.0},
]


def multilevel_document() -> TopologyDocument:
    document = TopologyDocument("office")
    document.set_levels(LEVELS)
    lobby = document.add_node(
        [0.0, 0.0, 0.0], node_id="lobby", level_id="1f",
        label="Lobby", aliases=("entrance",), target_refs=("place:lobby@1f",),
    )
    transition = document.add_node(
        [1.0, 0.0, 0.5], node_id="stair_mid", kind="transition",
        level_id=None, label="Stair midpoint",
    )
    conference = document.add_node(
        [2.0, 0.0, 1.0], node_id="conference", level_id="2f",
        label="Conference", aliases=("meeting room",),
        target_refs=("place:conference@2f",),
    )
    document.add_edge(
        lobby, transition, [[0.0, 0.0, 0.0], [1.0, 0.0, 0.5]],
        edge_id="stairs_up_1", directed=True, traversal_mode="stairs",
    )
    document.add_edge(
        transition, conference, [[1.0, 0.0, 0.5], [2.0, 0.0, 1.0]],
        edge_id="stairs_up_2", directed=True, traversal_mode="stairs",
        required_controller_capabilities=("rough_terrain",),
    )
    return document


def test_v1_serialization_and_default_rows_remain_unchanged():
    document = TopologyDocument("warehouse")
    first = document.add_node([0, 0, 0], node_id="a")
    last = document.add_node([1, 0, 0], node_id="b")
    document.add_edge(first, last, [[0, 0, 0], [1, 0, 0]], edge_id="ab")

    payload = document.to_dict()

    assert payload["version"] == 1
    assert "levels" not in payload
    assert set(payload["nodes"][0]) == {
        "id", "kind", "position_xyz", "target_refs", "source_ref", "locked",
    }
    assert set(payload["edges"][0]) == {
        "id", "kind", "from", "to", "directed", "polyline_xyz", "profiles",
        "source_ref", "locked_geometry",
    }
    assert TopologyDocument.from_dict(payload).to_dict() == payload


def test_set_levels_is_atomic_undoable_and_redoable():
    document = TopologyDocument("office")
    document.add_node([0, 0, 0], node_id="legacy")
    assert document.undo()

    before = document.to_dict()
    with pytest.raises(ValueError, match="duplicate/invalid level"):
        document.set_levels([LEVELS[0], LEVELS[0]])
    assert document.to_dict() == before
    assert document.redo()
    assert document.node("legacy")["id"] == "legacy"

    document.set_levels(LEVELS)
    assert document.version == 2
    assert document.levels == LEVELS
    assert document.undo()
    assert document.version == 1
    assert document.levels == []
    assert document.redo()
    assert document.version == 2
    assert document.levels == LEVELS


def test_v2_metadata_traversal_round_trip_and_unique_target_binding():
    document = multilevel_document()
    document.set_node_metadata(
        "conference", level_id="2f", label="Main conference room",
        aliases=("meeting", "meeting"),
    )
    document.set_edge_traversal(
        "stairs_up_2", "ramp", ("rough_terrain", "payload_stable"),
    )
    with pytest.raises(ValueError, match="already bound"):
        document.bind_target("conference", "place:lobby@1f")

    assert validate_document(document) == []
    payload = document.to_dict()
    restored = TopologyDocument.from_dict(payload)

    assert restored.to_dict() == payload
    assert restored.node("conference")["aliases"] == ["meeting"]
    assert restored.edge("stairs_up_2")["traversal_mode"] == "ramp"
    assert restored.edge("stairs_up_2")["required_controller_capabilities"] == [
        "payload_stable", "rough_terrain",
    ]


def test_build_document_uses_source_levels_as_non_undoable_initial_state(tmp_path):
    source = SceneSource(
        "office", tmp_path, MappingProxyType({"levels": LEVELS}),
    )

    class Adapter:
        def load_source(self, _root):
            return source

        def source_digests(self, _source):
            return {}

    loaded, document = author.build_document(Adapter(), tmp_path)

    assert loaded is source
    assert document.version == 2
    assert document.levels == LEVELS
    assert document.undo() is False


def test_level_visibility_and_xyz_picking_disambiguate_stacked_nodes():
    document = multilevel_document()
    document.add_node(
        [0.0, 0.0, 1.0], node_id="stacked_2f", level_id="2f",
        label="Upper landing",
    )
    first_floor = visible_node_ids(document, "1f")
    second_floor = visible_node_ids(document, "2f")

    assert first_floor == {"lobby", "stair_mid"}
    assert second_floor == {"conference", "stair_mid", "stacked_2f"}
    assert {edge["id"] for edge in visible_edges(document, "1f")} == {
        "stairs_up_1", "stairs_up_2",
    }

    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window.active_level_id = "1f"
    assert window._nearest_node((0.0, 0.0, 1.0)) is None
    assert window._nearest_node((0.0, 0.0, 0.0))[1]["id"] == "lobby"
    window.active_level_id = None
    assert window._nearest_node((0.0, 0.0, 1.0))[1]["id"] == "stacked_2f"
    assert window._point_segment_distance(
        (0.0, 0.0, 2.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
    ) == pytest.approx(2.0)

    overlay = draw_overlay(None, document, active_level_id="1f")
    assert {row["id"] for row in overlay["nodes"]} == first_floor
    assert overlay["active_level_id"] == "1f"


def test_level_switch_calls_optional_adapter_hooks_and_cancels_preview(tmp_path):
    document = multilevel_document()
    document.begin_node_preview([0.0, 0.0, 0.0])
    calls = []

    class Adapter:
        def set_active_level(self, level_id, source):
            calls.append(("active", level_id, source.scene_key))

        def set_level_view(self, level_id, source):
            calls.append(("view", level_id, source.scene_key))

    class Overlay:
        def set_active_level(self, level_id):
            calls.append(("overlay", level_id))

    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window.adapter = Adapter()
    window.source = SceneSource("office", tmp_path, {})
    window.active_level_id = "1f"
    window._overlay = Overlay()
    window._set_status = lambda _text: None
    window._refresh = lambda: None

    window.set_active_level("2f")
    window.set_active_level(None)

    assert document.pending_node is None
    assert calls == [
        ("active", "2f", "office"), ("view", "2f", "office"),
        ("overlay", "2f"), ("active", None, "office"),
        ("view", None, "office"), ("overlay", None),
    ]


def test_route_preview_and_export_use_resolver_output(tmp_path):
    document = multilevel_document()
    shown = []

    class Overlay:
        def set_route_preview(self, points, node_ids):
            shown.append((points, node_ids))

    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window.output_root = tmp_path
    window._route_origin_model = SimpleNamespace(as_string="place:lobby@1f")
    window._route_destination_model = SimpleNamespace(as_string="place:conference@2f")
    window._route_capabilities_model = SimpleNamespace(
        as_string="walk, stairs, rough_terrain")
    window._route_preview = None
    window._overlay = Overlay()
    window._last_status = None
    window._set_status = lambda value: setattr(window, "_last_status", value)
    window._refresh = lambda: None

    route = window.preview_route()
    path = window.export_route_preview()

    assert route["topology_node_ids"] == ["lobby", "stair_mid", "conference"]
    assert route["route_points_m"] == [
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.5], [2.0, 0.0, 1.0],
    ]
    assert shown[-1] == (route["route_points_m"], route["topology_node_ids"])
    assert path == tmp_path / "authoring/navigation/ground/route.preview.json"
    assert json.loads(path.read_text(encoding="utf-8"))["route_graph_digest"] == route[
        "route_graph_digest"
    ]
    assert window._last_status.startswith("Route preview exported:")


def test_failed_or_stale_route_preview_is_removed(tmp_path):
    document = multilevel_document()
    shown = []

    class Overlay:
        def set_route_preview(self, points, node_ids):
            shown.append((points, node_ids))

        def refresh(self):
            pass

    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window.output_root = tmp_path
    window._route_origin_model = SimpleNamespace(as_string="place:lobby@1f")
    window._route_destination_model = SimpleNamespace(
        as_string="place:conference@2f")
    window._route_capabilities_model = SimpleNamespace(
        as_string="walk, stairs, rough_terrain")
    window._route_preview = None
    window._overlay = Overlay()
    window._frames = []
    window.object_summary = None
    window.status = None
    window._last_status = None
    window._set_status = lambda value: setattr(window, "_last_status", value)

    assert window.preview_route() is not None
    window._route_destination_model.as_string = "unknown landmark"
    assert window.preview_route() is None
    assert window._route_preview is None
    assert shown[-1] == ((), ())
    assert window._last_status.startswith("Route preview failed:")

    window._route_destination_model.as_string = "place:conference@2f"
    assert window.preview_route() is not None
    document.move_node("conference", [2.5, 0.0, 1.0])
    window._refresh()
    assert window._route_preview is None
    assert shown[-1] == ((), ())


def test_route_preview_contains_optional_overlay_failure(tmp_path):
    document = multilevel_document()

    class BrokenOverlay:
        def set_route_preview(self, _points, _node_ids):
            raise RuntimeError("stage is unavailable")

        def refresh(self):
            pass

    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window.output_root = tmp_path
    window._route_origin_model = SimpleNamespace(as_string="place:lobby@1f")
    window._route_destination_model = SimpleNamespace(
        as_string="place:conference@2f")
    window._route_capabilities_model = SimpleNamespace(
        as_string="walk, stairs, rough_terrain")
    window._route_preview = None
    window._overlay = BrokenOverlay()
    window._frames = []
    window.object_summary = None
    window.status = None
    window._last_status = None
    window._set_status = lambda value: setattr(window, "_last_status", value)

    assert window.preview_route() is None
    assert window._route_preview is None
    assert window._last_status == "Route preview failed: stage is unavailable"
