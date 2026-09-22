import hashlib
import json
from pathlib import Path

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
