"""Focused pure UI/viewport checks for semantic pose authoring."""
from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

from tools.topology_authoring import viewport
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.ui import TopologyAuthoringWindow


LEVELS = [{"id": "1f", "label": "First floor", "support_z_m": 0.0}]


class _FloatModel:
    def __init__(self, value=0.0):
        self.as_float = float(value)

    def set_value(self, value):
        self.as_float = float(value)


def _document():
    document = TopologyDocument("office")
    document.set_levels(LEVELS)
    document._undo.clear()
    document._redo.clear()
    return document


def _window(document):
    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = document
    window._stage_ready = True
    window._heading_pick_active = False
    window.active_level_id = "1f"
    window._pending_node_kind = "custom"
    window._pose_x_model = _FloatModel()
    window._pose_y_model = _FloatModel()
    window._pose_z_model = _FloatModel()
    window._pose_heading_model = SimpleNamespace(as_string="")
    window._node_label_model = SimpleNamespace(as_string="")
    window._node_aliases_model = SimpleNamespace(as_string="")
    window.adapter = SimpleNamespace()
    window.source = object()
    window._overlay = None
    window._refresh = lambda: None
    window._last_status = None
    window._set_status = lambda value: setattr(window, "_last_status", value)
    return window


def test_typed_pose_preview_uses_real_document_and_normalizes_heading():
    document = _document()
    window = _window(document)
    window._pose_x_model = _FloatModel(1.0)
    window._pose_y_model = _FloatModel(2.0)
    window._pose_z_model = _FloatModel(3.0)
    window._pose_heading_model.as_string = "450"

    window.begin_typed_node_preview()

    assert document.pending_node.position_xyz == [1.0, 2.0, 3.0]
    assert document.pending_node.heading_rad == pytest.approx(math.pi / 2)
    assert window._heading_pick_active is False


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_typed_pose_preview_rejects_nonfinite_heading(value):
    document = _document()
    window = _window(document)
    window._pose_heading_model.as_string = value

    window.begin_typed_node_preview()

    assert document.pending_node is None
    assert window._last_status == "Pose preview failed: heading must be finite degrees"


def test_second_viewport_click_sets_pending_heading_without_support_projection():
    document = _document()
    document.begin_node_preview([1.0, 2.0, 0.0])
    projections = []
    window = _window(document)
    window.adapter = SimpleNamespace(
        project_to_support_surface=lambda point, source: projections.append(point)
    )
    window.source = object()
    window._heading_pick_active = True

    window.on_viewport_click([1.0, 3.0, 9.0])

    assert document.pending_node.heading_rad == pytest.approx(math.pi / 2)
    assert window._pose_heading_model.as_string == "90.000"
    assert window._heading_pick_active is False
    assert projections == []


def test_second_viewport_click_sets_selected_heading_through_model_api():
    document = _document()
    node_id = document.add_node([2.0, 4.0, 0.0], level_id="1f")
    document.begin_edge_preview(node_id)
    window = _window(document)

    window.begin_heading_pick()

    assert document.pending_edge is None
    assert document.selected_node_id == node_id
    window._set_heading_from_point([3.0, 4.0, 12.0])

    assert document.node(node_id)["heading_rad"] == pytest.approx(0.0)
    assert window._heading_pick_active is False
    assert document.undo() is True
    assert "heading_rad" not in document.node(node_id)


def test_zero_xy_heading_click_is_rejected_and_keeps_mode_for_retry():
    document = _document()
    document.begin_node_preview([1.0, 2.0, 0.0])
    window = _window(document)
    window.adapter = SimpleNamespace(
        project_to_support_surface=lambda *_args: pytest.fail(
            "heading direction clicks must not be projected"
        )
    )
    window.source = object()
    window._heading_pick_active = True

    window.on_viewport_click([1.0, 2.0, 9.0])

    assert document.pending_node.heading_rad is None
    assert window._heading_pick_active is True
    assert window._last_status == (
        "Heading failed: heading point must differ in XY from node"
    )


def test_repositioning_preview_preserves_heading_and_updates_xyz_fields():
    document = _document()
    document.begin_node_preview([1., 2., 0.], heading_rad=math.pi/2)
    window = _window(document)
    window.adapter.project_to_support_surface = lambda point, source: point
    window.on_viewport_click([3., 4., 0.])
    assert document.pending_node.position_xyz == [3., 4., 0.]
    assert document.pending_node.heading_rad == math.pi/2
    assert window._pose_x_model.as_float == 3.
    assert window._pose_heading_model.as_string == "90.000"


def test_named_landmark_selection_switches_floor_and_routes_by_stable_id():
    document = _document()
    document.set_levels(LEVELS + [{"id":"2f", "label":"Second floor", "support_z_m":3.}])
    node_id = document.add_node((2,3,3), level_id="2f", label="meeting_room_2f", heading_rad=math.pi/2)
    window = _window(document)
    window.use_landmark(node_id)
    assert document.selected_node_id == node_id
    assert window.active_level_id == "2f"
    assert window._pose_z_model.as_float == 3.
    assert window._pose_heading_model.as_string == "90.000"
    window._route_origin_model = SimpleNamespace(as_string="")
    assert window._node_label_model.as_string == "meeting_room_2f"
    window._route_destination_model = SimpleNamespace(as_string="")
    window.use_landmark(node_id, "origin")
    window.use_landmark(node_id, "destination")
    assert window._route_origin_model.as_string == node_id
    assert window._route_destination_model.as_string == node_id


def test_confirm_node_serializes_pending_heading_label_and_aliases():
    document = _document()
    document.begin_node_preview([1.0, 2.0, 3.0], heading_rad=math.pi / 2)
    window = _window(document)
    window._node_label_model.as_string = "Meeting room"
    window._node_aliases_model.as_string = "conference, team room, conference"
    window._heading_pick_active = True

    window.confirm_node()

    node = document.to_dict()["nodes"][0]
    assert node["position_xyz"] == [1.0, 2.0, 3.0]
    assert node["heading_rad"] == pytest.approx(math.pi / 2)
    assert node["level_id"] == "1f"
    assert node["label"] == "Meeting room"
    assert node["aliases"] == ["conference", "team room"]
    assert window._heading_pick_active is False


def test_blank_selected_heading_clears_constraint_and_is_undoable():
    document = _document()
    node_id = document.add_node(
        [0.0, 0.0, 0.0], level_id="1f", heading_rad=math.pi / 4
    )
    document.select_node(node_id)
    window = _window(document)
    window._heading_pick_active = True

    window._apply_selected_heading(SimpleNamespace(as_string="  "))

    assert "heading_rad" not in document.node(node_id)
    assert window._heading_pick_active is False
    assert document.undo() is True
    assert document.node(node_id)["heading_rad"] == pytest.approx(math.pi / 4)


def test_cancel_and_clear_selection_clear_heading_pick_mode():
    document = _document()
    window = _window(document)
    window._heading_pick_active = True
    document.begin_node_preview([0.0, 0.0, 0.0])

    window.cancel_preview()

    assert window._heading_pick_active is False
    assert document.pending_node is None

    node_id = document.add_node([0.0, 0.0, 0.0], level_id="1f")
    document.select_node(node_id)
    window._heading_pick_active = True
    window.clear_selection()
    assert window._heading_pick_active is False
    assert document.selected_node_id is None


def test_level_and_history_changes_clear_heading_pick_mode():
    document = _document()
    node_id = document.add_node([0.0, 0.0, 0.0], level_id="1f")
    document.select_node(node_id)
    window = _window(document)

    window._heading_pick_active = True
    window.set_active_level("1f")
    assert window._heading_pick_active is False

    document.set_node_heading(node_id, math.pi / 2)
    window._heading_pick_active = True
    window.undo()
    assert window._heading_pick_active is False

    window._heading_pick_active = True
    window.redo()
    assert window._heading_pick_active is False


def test_draw_overlay_exposes_committed_pending_and_route_headings():
    document = _document()
    document.add_node(
        [0.0, 0.0, 0.0], node_id="tea", level_id="1f",
        label="Tea room", aliases=("tea",), heading_rad=math.pi / 4,
    )
    document.begin_node_preview([1.0, 0.0, 0.0], heading_rad=math.pi / 2)

    overlay = viewport.draw_overlay(
        None, document, active_level_id="1f",
        route_points_m=([0, 0, 0], [1, 0, 0]),
        route_node_ids=("tea", "meeting"),
        route_headings_rad=(None, math.pi / 2),
    )

    assert overlay["nodes"][0]["label"] == "Tea room"
    assert overlay["nodes"][0]["aliases"] == ["tea"]
    assert overlay["node_headings_rad"]["tea"] == pytest.approx(math.pi / 4)
    assert overlay["pending_heading_rad"] == pytest.approx(math.pi / 2)
    assert overlay["route_headings_rad"] == [None, pytest.approx(math.pi / 2)]


def test_committed_heading_arrow_is_not_child_of_translated_node(monkeypatch):
    document = _document()
    document.add_node(
        [10.0, 20.0, 0.0], level_id="1f", heading_rad=math.pi / 2
    )
    paths = []

    class _Attr:
        def Set(self, _value):
            return self

    class _Sphere:
        def CreateRadiusAttr(self):
            return _Attr()

        def AddTranslateOp(self):
            return _Attr()

        def GetPrim(self):
            return object()

    class _Stage:
        def GetPrimAtPath(self, _path):
            return SimpleNamespace(IsValid=lambda: False)

    fake_usd_geom = SimpleNamespace(
        Xform=SimpleNamespace(Define=lambda *_args: None),
        Sphere=SimpleNamespace(Define=lambda *_args: _Sphere()),
    )
    monkeypatch.setitem(
        sys.modules, "pxr",
        SimpleNamespace(
            Gf=SimpleNamespace(Vec3d=lambda *value: value),
            UsdGeom=fake_usd_geom,
        ),
    )
    monkeypatch.setattr(viewport, "_set_color", lambda *_args: None)
    overlay = viewport.TopologyOverlay.__new__(viewport.TopologyOverlay)
    overlay.document = document
    overlay.stage = _Stage()
    overlay.active_level_id = "1f"
    overlay.route_points_m = ()
    overlay.route_node_ids = ()
    overlay.route_headings_rad = ()
    overlay._draw_heading_arrow = lambda path, *_args: paths.append(path)

    overlay.rebuild()

    assert paths == [f"{viewport.OVERLAY_ROOT}/Headings/H00000"]


def test_route_heading_metadata_reaches_overlay_and_viewport_wrapper():
    document = _document()
    window = _window(document)
    calls = []
    route = {
        "route_points_m": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        "topology_node_ids": ["a", "b"],
        "route_headings_rad": [None, math.pi / 2],
    }
    window._resolve_route_preview = lambda: route
    window._overlay = SimpleNamespace(
        set_route_preview=lambda *args: calls.append(args)
    )

    assert window.preview_route() is route
    assert calls == [(
        route["route_points_m"], route["topology_node_ids"],
        route["route_headings_rad"],
    )]

    forwarded = []
    wrapper = viewport.ViewportOverlay.__new__(viewport.ViewportOverlay)
    wrapper.overlay = SimpleNamespace(
        set_route_preview=lambda *args: forwarded.append(args)
    )
    wrapper.refresh = lambda: forwarded.append("refresh")
    wrapper.set_route_preview(
        route["route_points_m"], route["topology_node_ids"],
        route["route_headings_rad"],
    )
    assert forwarded == [calls[0], "refresh"]


def test_v1_viewport_node_creation_remains_heading_free():
    document = TopologyDocument("warehouse")
    window = _window(document)
    window.active_level_id = None
    window.adapter = SimpleNamespace(
        project_to_support_surface=lambda point, _source: point
    )
    window.source = object()

    window.on_viewport_click([1.0, 2.0, 3.0])
    window.confirm_node()

    assert document.version == 1
    assert "heading_rad" not in document.to_dict()["nodes"][0]
