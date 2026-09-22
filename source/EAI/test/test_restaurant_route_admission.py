from copy import deepcopy
import json
import uuid

import pytest


def test_route_profiles_bind_public_candidates_to_service_regions():
    from demo.restaurant_benchmark import route_admission

    assert tuple(route_admission.ROUTE_PROFILES) == (
        "go2_1-dining",
        "lite3_1-dining",
        "iris_1-overview",
        "iris_2-overview",
    )
    assert route_admission.ROUTE_PROFILES["go2_1-dining"]["candidate_id"] == (
        "restaurant_go2_1"
    )
    assert route_admission.ROUTE_PROFILES["lite3_1-dining"]["candidate_id"] == (
        "restaurant_lite3_1"
    )
    assert route_admission.DINING_SERVICE_XY_M == (14.225, 21.925)
    assert route_admission.KITCHEN_SERVICE_XY_M == (24.225, 17.625)
    assert route_admission.OVERVIEW_ANCHOR_XYZ_M == (19.225, 19.775, 3.0)
    for route_id in ("go2_1-dining", "lite3_1-dining"):
        assert route_admission.ROUTE_PROFILES[route_id]["goal_xy_m"] == [
            14.225,
            21.925,
        ]
    for route_id in ("iris_1-overview", "iris_2-overview"):
        assert route_admission.ROUTE_PROFILES[route_id]["goal_xyz_m"] == [
            19.225,
            19.775,
            3.0,
        ]
        assert route_admission._aerial_route(route_id)
    for route_id in ("go2_1-dining", "lite3_1-dining"):
        assert not route_admission._aerial_route(route_id)
    with pytest.raises(ValueError, match="unknown Restaurant route"):
        route_admission._profile("m20_1-kitchen")


def test_route_selections_spawn_at_public_anchors():
    from demo.restaurant_benchmark import config, route_admission

    for route_id, spawn_key in (
        ("go2_1-dining", "go2_1"),
        ("lite3_1-dining", "lite3_1"),
        ("iris_1-overview", "iris_1"),
        ("iris_2-overview", "iris_2"),
    ):
        selection = route_admission._selection(route_id)
        assert selection["scene_key"] == config.BENCHMARK_SCENE_KEY
        robot = selection["robots"][0]
        assert robot["spawn_pose"]["position"] == list(
            config.BENCHMARK_ROBOT_SPAWN_XYZ_M[spawn_key]
        )
        assert robot["controller"]["cfg"] == route_admission._spec(route_id)[
            "controller_cfg"
        ]


def test_route_thresholds_are_mobility_specific():
    from demo.restaurant_benchmark import route_admission

    ground = route_admission._thresholds("go2_1-dining")
    assert ground["minimum_command_distance_scale"] == 0.55
    assert ground["speed_m_s"] == 0.45
    assert ground["minimum_stance_feet"] == 2
    assert ground["minimum_stance_feet_step_fraction"] == 0.95
    assert ground["max_steps"] == 12000
    assert ground["maximum_route_deviation_m"] == 0.40
    aerial = route_admission._thresholds("iris_1-overview")
    assert aerial["flight_speed_m_s"] == 0.20
    assert aerial["settle_steps"] == 240
    assert aerial["minimum_flight_altitude_m"] == 1.0
    assert aerial["maximum_tilt_rad"] == 0.30
    assert aerial["max_steps"] == 14000
    assert "median_support_ratio" not in aerial
    assert (
        route_admission._probe_scope("go2_1-dining")
        == "single_legged_candidate_commanded_route_and_foot_contact_only"
    )
    assert (
        route_admission._probe_scope("iris_2-overview")
        == "single_aerial_candidate_commanded_route_and_hover_health_only"
    )


def test_polyline_point_walks_arc_length():
    from demo.restaurant_benchmark.route_admission import _polyline_point

    polyline = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    assert _polyline_point(polyline, 0.0) == (0.0, 0.0)
    assert _polyline_point(polyline, 0.5) == (0.5, 0.0)
    assert _polyline_point(polyline, 1.0) == (1.0, 0.0)
    assert _polyline_point(polyline, 1.5) == (1.0, 0.5)
    assert _polyline_point(polyline, 99.0) == (1.0, 1.0)


def _request(monkeypatch, route_id="go2_1-dining"):
    from demo.restaurant_benchmark import config, route_admission

    source_files = {"/tmp/route_admission.py": "a" * 64}
    monkeypatch.setattr(
        route_admission, "_source_files", lambda _route_id: source_files
    )
    route = {
        "route_id": route_id,
        "start_xy_m": [0.875, 14.525],
        "goal_xy_m": [14.225, 21.925],
        "goal_xyz_m": [14.225, 21.925, 3.0],
        "map_sha256": "0" * 64,
        "route_cells": [[1, 2]],
        "waypoints_xy_m": [[1.0, 15.0]],
        "planned_length_m": 16.77,
    }
    return {
        "schema": "eai.restaurant.route_admission_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": config.BENCHMARK_SCENE_KEY,
        "route_id": route_id,
        "candidate_id": route_admission._profile(route_id)["candidate_id"],
        "candidate_spec": route_admission._spec(route_id),
        "products_root": "/tmp/products",
        "source_usd": "/tmp/source.usda",
        "source_sha256": config.SOURCE_SHA256,
        "metadata_sha256": "1" * 64,
        "bundle_manifest_sha256": "2" * 64,
        "product_validation": {"validated": True},
        "source_files": source_files,
        "selection": route_admission._selection(route_id),
        "route": route,
        "requested_device": "cpu",
        "thresholds": route_admission._thresholds(route_id),
        "release_gates": {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        },
        "execution_validated": False,
        "scoreable": False,
    }


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("route_id", "m20_1-kitchen"),
        ("candidate_id", "restaurant_m20_1"),
        ("requested_device", "cuda"),
        ("thresholds", {}),
        ("execution_validated", True),
        ("scoreable", True),
        ("selection", {}),
    ),
)
def test_route_admission_request_fails_closed(monkeypatch, mutation, value):
    from demo.restaurant_benchmark import route_admission

    request = _request(monkeypatch)
    request[mutation] = value
    with pytest.raises(
        (ValueError, KeyError), match="unknown Restaurant route|invalid Restaurant"
    ):
        route_admission._validate_request_contract(request)


def test_route_admission_request_rejects_goal_or_source_drift(monkeypatch):
    from demo.restaurant_benchmark import route_admission

    request = _request(monkeypatch)
    route_admission._validate_request_contract(request)
    round_trip = json.loads(json.dumps(request))
    route_admission._validate_request_contract(round_trip)
    assert round_trip["route"]["route_id"] == "go2_1-dining"

    changed_goal = deepcopy(request)
    changed_goal["route"]["goal_xy_m"] = [24.225, 17.625]
    with pytest.raises(ValueError, match="invalid Restaurant route"):
        route_admission._validate_request_contract(changed_goal)

    changed_sources = deepcopy(request)
    changed_sources["source_files"] = {"/tmp/other.py": "b" * 64}
    with pytest.raises(ValueError, match="invalid Restaurant route"):
        route_admission._validate_request_contract(changed_sources)

    empty_cells = deepcopy(request)
    empty_cells["route"]["route_cells"] = []
    with pytest.raises(ValueError, match="invalid Restaurant route"):
        route_admission._validate_request_contract(empty_cells)


def test_route_evidence_promotes_only_route_validated_anchors():
    from demo.restaurant_benchmark import config

    # The M20 anchors keep failing admission, so they must not appear in the
    # route-validated roster even though both now have route tooling.
    assert "m20_1" not in config.BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS
    assert "m20_2" not in config.BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS
    assert config.EXECUTION_VALIDATED is False
    assert config.SCOREABLE is False
