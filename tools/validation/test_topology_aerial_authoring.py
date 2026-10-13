from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from algorithm.skills.navigation.aerial_navigation import AerialNavigationProvider, AerialPose
from algorithm.skills.navigation.landmarks import landmark_inventory, resolve_landmark_route
from algorithm.skills.navigation.topology import validate_ground_topology
from algorithm.skills.contracts import SkillRequest
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, load_product, product_digest, validate_document
from tools.topology_authoring.adapters.empty import EmptyTopologyAdapter
from tools.topology_authoring.author import build_document


def air_document():
    doc = TopologyDocument("plane", domain="aerial")
    doc.add_node((0, 0, 1), node_id="start", label="loading", heading_rad=0.2)
    doc.add_node((0, 0, 3), node_id="upper", label="meeting_room", aliases=["meeting"])
    doc.add_edge("start", "upper", [(0, 0, 1), (0, 0, 3)], directed=True)
    return doc


def test_aerial_roundtrip_semantics_vertical_route_and_measured_origin(tmp_path):
    doc = air_document()
    adapter = EmptyTopologyAdapter("plane")
    source = adapter.load_source(tmp_path)
    assert validate_document(doc, adapter, source) == []
    assert validate_ground_topology(doc)
    path = generate_product(tmp_path / "air.json", doc, adapter, source)
    loaded = load_product(path, adapter, source, expected_digest=product_digest(doc))
    assert loaded.to_dict() == doc.to_dict()
    assert landmark_inventory(loaded)[0]["heading_rad"] == 0.2
    route = resolve_landmark_route(loaded, origin_ref="loading", destination_ref="meeting")
    assert route["route_points_m"] == [[0, 0, 1], [0, 0, 3]]
    assert not resolve_landmark_route(loaded, origin_ref="meeting", destination_ref="loading")["admitted"]
    request = SkillRequest("air", "eai.navigation.aerial_navigation", "iris", target_ref=route["target_ref"],
        parameters={**route["skill_parameters"], "world_state_seq": 1})
    p = AerialNavigationProvider()
    obs = {"valid": True, "fresh": True, "world_state_seq": 1, "pose": AerialPose((10, 0, 1)), "topology": loaded.to_dict()}
    p.dispatch(request, observation=obs)
    assert p.control(request, observation=obs).state["status"] == "failed"


def test_domain_source_build_and_ground_vertical_edge_still_rejected(tmp_path):
    source, doc = build_document(EmptyTopologyAdapter("plane"), tmp_path, domain="aerial")
    assert source.payload["navigation_domain"] == doc.domain == "aerial"
    ground = TopologyDocument("plane")
    ground.add_node((0, 0, 1), node_id="a")
    ground.add_node((0, 0, 3), node_id="b")
    ground.add_edge("a", "b", [(0, 0, 1), (0, 0, 3)])
    assert any("nonzero XY" in e for e in validate_document(ground))


def test_unknown_domain_and_zero_air_edge_fail_closed():
    data = air_document().to_dict()
    data["domain"] = "unknown"
    with pytest.raises(ValueError, match="domain"):
        TopologyDocument.from_dict(data)
    doc = air_document()
    doc.nodes[1]["position_xyz"] = [0, 0, 1]
    assert any("nonzero XYZ" in e for e in validate_document(doc))


def test_aerial_product_does_not_silently_upgrade_invalid_version():
    data = air_document().to_dict()
    data["version"] = 1
    with pytest.raises(ValueError, match="version 2"):
        TopologyDocument.from_dict(data)


def test_frozen_product_isolated_and_source_binding_required():
    from algorithm.skills.navigation.aerial_topology import FrozenAerialTopology
    data = air_document().to_dict()
    frozen = FrozenAerialTopology(data, scene_key="plane", source_digests={})
    data["nodes"][0]["position_xyz"][0] = 100
    copy = frozen.snapshot()
    copy["nodes"].clear()
    assert frozen.snapshot()["nodes"][0]["position_xyz"] == [0, 0, 1]
    with pytest.raises(ValueError, match="source changed"):
        frozen.validate_source("plane", {"new": "a" * 64})
    missing_schema = air_document().to_dict()
    missing_schema.pop("schema")
    with pytest.raises(ValueError, match="explicit v2 aerial"):
        FrozenAerialTopology(missing_schema, scene_key="plane", source_digests={})
    for field in ("coordinate_frame", "meters_per_unit", "up_axis"):
        missing = air_document().to_dict()
        missing.pop(field)
        with pytest.raises(ValueError, match="explicit world"):
            FrozenAerialTopology(missing, scene_key="plane", source_digests={})
    with pytest.raises(ValueError, match="connected nodes"):
        FrozenAerialTopology(TopologyDocument("plane", domain="aerial").to_dict(), scene_key="plane", source_digests={})


def test_aerial_ui_saves_loads_and_exports_only_in_its_domain(tmp_path):
    from tools.topology_authoring.ui import TopologyAuthoringWindow
    window = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    window.document = air_document()
    window.output_root = tmp_path
    window.adapter = EmptyTopologyAdapter("plane")
    window.source = window.adapter.load_source(tmp_path)
    window._stage_ready = True
    window._overlay = None
    window._route_preview = None
    window._heading_pick_active = False
    window._set_status = lambda text: None
    window._refresh = lambda: None
    window._apply_active_level_view = lambda: None
    window._route_origin_model = SimpleNamespace(as_string="loading")
    window._route_destination_model = SimpleNamespace(as_string="meeting")
    window._route_capabilities_model = SimpleNamespace(as_string="fly")
    window.save_draft()
    window.document.nodes[0]["label"] = "changed"
    window.load_draft()
    assert window.document.nodes[0]["label"] == "loading"
    path = window.export_route_preview()
    assert path == tmp_path / "authoring/navigation/aerial/route.preview.json"
    window.generate()
    assert (path.parent / "topology.json").is_file()
    assert not (tmp_path / "authoring/navigation/ground").exists()
