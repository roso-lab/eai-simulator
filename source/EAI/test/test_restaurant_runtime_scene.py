import hashlib
import json
import math
from pathlib import Path
import uuid

import pytest


PRODUCT_NAMES = (
    "restaurant_benchmark_bench.usda",
    "restaurant_benchmark_map.png",
    "restaurant_benchmark_map.yaml",
)


def _candidate(root: Path) -> Path:
    from EAI_assets.restaurant_benchmark_products import (
        EXPECTED_SOURCE_SHA256,
        EXPECTED_UNRESOLVED_MANIFEST_SHA256,
        EXPECTED_USED_LAYER_MANIFEST_SHA256,
        _canonical_sha256,
        _sha256,
    )

    root.mkdir()
    rows = {}
    for name in PRODUCT_NAMES:
        path = root / name
        path.write_bytes(name.encode("ascii"))
        rows[name] = {
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    generator_path = (
        Path(__file__).resolve().parents[3]
        / "tools/assets/build_restaurant_products.py"
    )
    metadata = {
        "schema": "eai.restaurant-products.v1",
        "publication_status": "local_candidate",
        "provider_revision": None,
        "release_gates": {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        },
        "source": {
            "entry_path": "scene/restaurant/Restaurant_Only.usda",
            "entry_sha256": EXPECTED_SOURCE_SHA256,
            "source_meters_per_unit": 0.01,
            "runtime_scale": [0.01, 0.01, 0.01],
            "unresolved_manifest_sha256": EXPECTED_UNRESOLVED_MANIFEST_SHA256,
            "used_layer_manifest_sha256": EXPECTED_USED_LAYER_MANIFEST_SHA256,
            "effective_asset_closure_validated": True,
        },
        "generator": {
            "module": "tools.assets.build_restaurant_products",
            "algorithm_version": "1",
            "source_sha256": _sha256(generator_path),
        },
        "products": rows,
        "bundle_manifest_sha256": _canonical_sha256(rows),
    }
    (root / "bench_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return root


def _accept_test_candidate(root: Path, monkeypatch) -> None:
    from EAI_assets import restaurant_benchmark_products as products

    metadata = json.loads((root / "bench_meta.json").read_text())
    monkeypatch.setattr(
        products,
        "EXPECTED_PRODUCT_SHA256",
        {name: row["sha256"] for name, row in metadata["products"].items()},
    )
    monkeypatch.setattr(
        products,
        "EXPECTED_BUNDLE_MANIFEST_SHA256",
        metadata["bundle_manifest_sha256"],
    )
    monkeypatch.setattr(
        products,
        "EXPECTED_METADATA_SHA256",
        products._sha256(root / "bench_meta.json"),
    )


def test_restaurant_benchmark_candidate_resolver_is_explicit_and_hash_bound(
    tmp_path, monkeypatch
):
    from EAI_assets.restaurant_benchmark_products import (
        PRODUCT_ROOT_ENV,
        resolve_restaurant_benchmark_products,
    )

    monkeypatch.delenv(PRODUCT_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match=PRODUCT_ROOT_ENV):
        resolve_restaurant_benchmark_products()

    root = _candidate(tmp_path / "candidate")
    _accept_test_candidate(root, monkeypatch)
    resolved = resolve_restaurant_benchmark_products(root)
    assert resolved.root == root.resolve()
    assert resolved.overlay_usd.name == PRODUCT_NAMES[0]

    resolved.map_png.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="missing or changed"):
        resolve_restaurant_benchmark_products(root)


def test_restaurant_benchmark_candidate_rejects_release_metadata_tampering(
    tmp_path, monkeypatch
):
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    root = _candidate(tmp_path / "candidate")
    _accept_test_candidate(root, monkeypatch)
    metadata_path = root / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["release_gates"]["scoreable"] = True
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(RuntimeError, match="metadata snapshot"):
        resolve_restaurant_benchmark_products(root)


def test_restaurant_benchmark_scene_is_registered_without_provider_paths():
    from EAI.hmrs_env.env_diy import catalog
    from EAI_assets.asset_requirements import resolve_selection

    labels = dict(catalog.scene_choices())
    assert labels["restaurant"] == "Restaurant authoring scene"
    assert labels["restaurant_benchmark"] == "Restaurant benchmark candidate overlay"
    graph = resolve_selection(
        {"version": 1, "scene_key": "restaurant_benchmark", "robots": []}
    )
    scene = next(item for item in graph.requirements if item.id == "scene:restaurant_benchmark")
    assert scene.relative_paths == ()


def test_restaurant_benchmark_saved_environment_uses_dedicated_scene_key():
    root = Path(__file__).resolve().parents[3]
    saved = json.loads(
        (
            root
            / "source/EAI_hmrs/EAI_hmrs/envs/restaurant_benchmark_scene.json"
        ).read_text()
    )
    assert saved == {
        "robots": [],
        "scene_key": "restaurant_benchmark",
        "task_name": "restaurant_benchmark_scene",
        "version": 1,
    }


def test_restaurant_native_probe_selection_and_thresholds_are_frozen():
    from demo.restaurant_benchmark import config
    from demo.restaurant_benchmark import runtime_probe

    selection = runtime_probe._selection()
    assert selection["scene_key"] == config.BENCHMARK_SCENE_KEY
    assert selection["robots"][0]["type"] == "carter"
    assert config.BENCHMARK_PRIMARY_SPAWN_XY_M == (21.875, 20.875)
    assert runtime_probe.STATIONARY_DISPLACEMENT_LIMIT_M == 0.05
    assert runtime_probe.STATIONARY_VERTICAL_DRIFT_LIMIT_M == 0.02


def test_restaurant_contact_probe_thresholds_and_wheels_are_frozen():
    from demo.restaurant_benchmark import contact_probe

    assert contact_probe.WHEEL_NAMES == (
        "wheel_left",
        "wheel_right",
        "caster_wheel_left",
        "caster_wheel_right",
    )
    assert contact_probe.GROUND_COLLISION_PATH.endswith("/BenchCollision/zz_ground")
    assert contact_probe.MINIMUM_CONTACT_SAMPLE_RATIO == 0.80
    assert contact_probe.MINIMUM_WEIGHT_SUPPORT_RATIO == 0.70
    assert contact_probe.MAXIMUM_WEIGHT_SUPPORT_RATIO == 1.30
    assert contact_probe.MAXIMUM_XY_EXCURSION_M == 0.05
    assert contact_probe.MAXIMUM_Z_EXCURSION_M == 0.02
    assert contact_probe.MAXIMUM_TILT_RAD == 0.10
    assert contact_probe.PROBE_SCOPE.endswith("zero_command_stability_only")


def _contact_request():
    from demo.restaurant_benchmark import config, contact_probe, runtime_probe

    return {
        "schema": "eai.restaurant.native_contact_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": config.BENCHMARK_SCENE_KEY,
        "selection": runtime_probe._selection(),
        "requested_device": "cpu",
        "source_sha256": config.SOURCE_SHA256,
        "product_validation": {"validated": True},
        "source_files": contact_probe._source_files(),
        "settle_steps": 120,
        "sample_steps": 240,
        "thresholds": contact_probe._expected_thresholds(),
        "release_gates": {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        },
        "scoreable": False,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "not-a-run-id"),
        ("settle_steps", 0),
        ("settle_steps", True),
        ("sample_steps", 1201),
        ("thresholds", {}),
    ],
)
def test_restaurant_contact_request_contract_fails_closed(field, value):
    from demo.restaurant_benchmark import contact_probe

    request = _contact_request()
    request[field] = value
    with pytest.raises(ValueError, match="invalid Restaurant native contact request"):
        contact_probe._validate_request_contract(request)


def _contact_buffer(*, count=1, start=0, normal_force=12.0):
    capacity = 64
    unused_scalar = [[math.nan] for _ in range(capacity - 1)]
    unused_vector = [[math.nan] * 3 for _ in range(capacity - 1)]
    return (
        [[normal_force], *unused_scalar],
        [[1.0, 2.0, 0.0], *unused_vector],
        [[0.0, 0.0, 1.0], *unused_vector],
        [[-0.001], *unused_scalar],
        [[count]],
        [[start]],
    )


def test_restaurant_contact_decoder_preserves_filter_and_range():
    from demo.restaurant_benchmark import contact_probe

    decoded = contact_probe._decode_contact_data(_contact_buffer())
    assert decoded["contact_count"] == 1
    assert decoded["contact_start"] == 0
    assert decoded["contacts"] == [
        {
            "collision_path": contact_probe.GROUND_COLLISION_PATH,
            "position_world_m": [1.0, 2.0, 0.0],
            "normal_world": [0.0, 0.0, 1.0],
            "normal_force_N": 12.0,
            "separation_m": -0.001,
        }
    ]


@pytest.mark.parametrize(
    "raw",
    [
        _contact_buffer(count=1, start=64),
        _contact_buffer(count=-1),
        _contact_buffer(normal_force=-1.0),
    ],
)
def test_restaurant_contact_decoder_rejects_invalid_buffers(raw):
    from demo.restaurant_benchmark import contact_probe

    with pytest.raises(RuntimeError, match="invalid Carter wheel contact buffer"):
        contact_probe._decode_contact_data(raw)


def test_restaurant_navigation_routes_and_scoped_gates_are_frozen():
    from demo.restaurant_benchmark import config, navigation_probe

    assert navigation_probe.ROUTES == {
        "A-E": {
            "start_xy_m": (21.875, 20.875),
            "goal_xy_m": (24.225, 17.625),
        },
        "A-B": {
            "start_xy_m": (21.875, 20.875),
            "goal_xy_m": (14.225, 21.925),
        },
        "C-B": {
            "start_xy_m": (10.675, 18.375),
            "goal_xy_m": (14.225, 21.925),
        },
    }
    assert config.BENCHMARK_DEPLOYMENT_PROFILE == "bound_native_v2"
    assert config.BENCHMARK_ADMISSION_STATUS == "authoring_pending_native_candidate_evidence"
    assert config.BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS == (
        "carter_1", "carter_2", "go2_1", "lite3_1", "iris_1", "iris_2",
    )
    assert set(config.BENCHMARK_ROBOT_SPAWN_XYZ_M) == {
        "carter_1", "carter_2", "m20_1", "m20_2", "go2_1", "lite3_1", "iris_1", "iris_2",
    }
    anchors = list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M.values())
    assert min(
        math.dist(a[:2], b[:2])
        for index, a in enumerate(anchors)
        for b in anchors[index + 1:]
    ) >= config.BENCHMARK_MINIMUM_SPAWN_SPACING_M
    assert navigation_probe.PLANNING_RADIUS_M == 0.40
    assert navigation_probe.FINAL_TOLERANCE_M == 0.25
    assert navigation_probe.MAXIMUM_ROUTE_DEVIATION_M == 0.40
    assert navigation_probe.PROBE_SCOPE.endswith("wheel_contact_only")


def test_restaurant_deployment_route_uses_its_explicit_native_spawn():
    from demo.restaurant_benchmark import config, navigation_probe, runtime_probe

    assert navigation_probe._selection_for_route("A-E") == runtime_probe._selection()
    selection = navigation_probe._selection_for_route("C-B")
    assert selection["robots"][0]["spawn_pose"] == {
        "position": list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_2"]),
        "rotation": [1.0, 0.0, 0.0, 0.0],
    }


def test_restaurant_navigation_route_deviation_uses_all_segments():
    from demo.restaurant_benchmark import navigation_probe

    polyline = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]
    assert navigation_probe._route_deviation((1.0, 0.3), polyline) == pytest.approx(0.3)
    assert navigation_probe._route_deviation((2.2, 1.0), polyline) == pytest.approx(0.2)


def _navigation_request(monkeypatch):
    from demo.restaurant_benchmark import config, navigation_probe, runtime_probe

    monkeypatch.setattr(navigation_probe, "_source_files", lambda: {})
    return {
        "schema": "eai.restaurant.native_navigation_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": config.BENCHMARK_SCENE_KEY,
        "selection": runtime_probe._selection(),
        "requested_device": "cpu",
        "source_sha256": config.SOURCE_SHA256,
        "product_validation": {"validated": True},
        "source_files": {},
        "thresholds": navigation_probe._thresholds(),
        "route": {
            "route_id": "A-E",
            "start_xy_m": [21.875, 20.875],
            "goal_xy_m": [24.225, 17.625],
            "map_sha256": "m" * 64,
            "route_cells": [[1, 2], [2, 3]],
            "waypoints_xy_m": [[21.875, 20.875], [24.225, 17.625]],
            "planned_length_m": 4.0,
        },
        "release_gates": {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        },
        "business_episode_execution_validated": False,
        "scoreable": False,
    }


@pytest.mark.parametrize("tamper", ["run_id", "thresholds", "route", "scoreable"])
def test_restaurant_navigation_request_contract_fails_closed(monkeypatch, tamper):
    from demo.restaurant_benchmark import navigation_probe

    request = _navigation_request(monkeypatch)
    if tamper == "run_id":
        request["run_id"] = "not-a-run-id"
    elif tamper == "thresholds":
        request["thresholds"]["max_steps"] += 1
    elif tamper == "route":
        request["route"]["goal_xy_m"] = [0.0, 0.0]
    else:
        request["scoreable"] = True
    with pytest.raises(ValueError, match="invalid Restaurant native navigation request"):
        navigation_probe._validate_request_contract(request)
