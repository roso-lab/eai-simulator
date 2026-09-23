"""Fail-closed resolver for local Restaurant benchmark candidate products."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


PRODUCT_ROOT_ENV = "EAI_RESTAURANT_BENCHMARK_ROOT"
PRODUCT_SCHEMA = "eai.restaurant-products.v1"
PRODUCT_NAMES = (
    "restaurant_benchmark_bench.usda",
    "restaurant_benchmark_map.png",
    "restaurant_benchmark_map.yaml",
)
METADATA_NAME = "bench_meta.json"
EXPECTED_PRODUCT_SHA256 = {
    "restaurant_benchmark_bench.usda": (
        "13d58d212b13f744cbb7f822970ee37fbc9cfff5df894780d9946e9ee1c84521"
    ),
    "restaurant_benchmark_map.png": (
        "946b44f46176393c3526953397dc9a3dfb57d7cc5085d8f4198b73743ec63adf"
    ),
    "restaurant_benchmark_map.yaml": (
        "a111666d14597a0b2ede2f67b741c2bd75685d956c55d93769fb6ca4dbcf8e73"
    ),
}
EXPECTED_BUNDLE_MANIFEST_SHA256 = (
    "b937c6e9c1a8fb6f9841e01b1462298f6b79b408d195aba3437621b1bd85ffe0"
)
EXPECTED_METADATA_SHA256 = (
    "dc3fe893959a5fd83fa9dc5a4b0861f1ffaf4bf2d776320d66a855a3f53b921a"
)
EXPECTED_SOURCE_SHA256 = "618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b"
EXPECTED_UNRESOLVED_MANIFEST_SHA256 = (
    "9c05e33a274844ba5f8fc7dd6a24ab00eabcaf9e5d9ece99d9c6d641a6b1f626"
)
EXPECTED_USED_LAYER_MANIFEST_SHA256 = (
    "9a499b56079e4a7658fac047cb0d80bc2b98f54f17ef1aac7c4caa42bf887eaf"
)
LOCAL_CANDIDATE_GATES = {
    "navigation_ready": False,
    "execution_validated": False,
    "scoreable": False,
}


@dataclass(frozen=True)
class RestaurantBenchmarkProducts:
    root: Path
    overlay_usd: Path
    map_png: Path
    map_yaml: Path
    metadata: Path
    bundle_manifest_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def resolve_restaurant_benchmark_products(
    root: str | os.PathLike[str] | None = None,
) -> RestaurantBenchmarkProducts:
    """Resolve one immutable local candidate directory and verify its hashes."""

    configured = root if root is not None else os.environ.get(PRODUCT_ROOT_ENV)
    if not configured:
        raise RuntimeError(
            f"{PRODUCT_ROOT_ENV} must name a validated Restaurant candidate directory"
        )
    candidate = Path(configured).expanduser().resolve()
    if not candidate.is_dir():
        raise FileNotFoundError(f"Restaurant candidate directory is missing: {candidate}")
    metadata_path = candidate / METADATA_NAME
    if not metadata_path.is_file() or _sha256(metadata_path) != EXPECTED_METADATA_SHA256:
        raise RuntimeError("Restaurant candidate metadata snapshot is missing or changed")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read Restaurant candidate metadata: {metadata_path}") from exc
    if (
        metadata.get("schema") != PRODUCT_SCHEMA
        or metadata.get("publication_status") != "local_candidate"
        or metadata.get("provider_revision") is not None
        or metadata.get("release_gates") != LOCAL_CANDIDATE_GATES
    ):
        raise RuntimeError("Restaurant candidate metadata or release gates are invalid")
    rows = metadata.get("products")
    if not isinstance(rows, Mapping) or set(rows) != set(PRODUCT_NAMES):
        raise RuntimeError("Restaurant candidate product inventory is invalid")
    paths: dict[str, Path] = {}
    for name in PRODUCT_NAMES:
        row = rows[name]
        path = candidate / name
        if (
            not isinstance(row, Mapping)
            or row.get("path") != name
            or not path.is_file()
            or row.get("sha256") != EXPECTED_PRODUCT_SHA256[name]
            or _sha256(path) != EXPECTED_PRODUCT_SHA256[name]
        ):
            raise RuntimeError(f"Restaurant candidate product is missing or changed: {name}")
        paths[name] = path
    manifest_sha = metadata.get("bundle_manifest_sha256")
    if (
        manifest_sha != EXPECTED_BUNDLE_MANIFEST_SHA256
        or manifest_sha != _canonical_sha256(rows)
    ):
        raise RuntimeError("Restaurant candidate bundle manifest is invalid")
    source = metadata.get("source")
    if not isinstance(source, Mapping) or (
        source.get("entry_path") != "scene/restaurant/Restaurant_Only.usda"
        or source.get("entry_sha256") != EXPECTED_SOURCE_SHA256
        or source.get("source_meters_per_unit") != 0.01
        or source.get("runtime_scale") != [0.01, 0.01, 0.01]
        or source.get("unresolved_manifest_sha256")
        != EXPECTED_UNRESOLVED_MANIFEST_SHA256
        or source.get("used_layer_manifest_sha256")
        != EXPECTED_USED_LAYER_MANIFEST_SHA256
        or source.get("effective_asset_closure_validated") is not True
    ):
        raise RuntimeError("Restaurant candidate source provenance is invalid")
    generator = metadata.get("generator")
    generator_path = (
        Path(__file__).resolve().parents[3]
        / "tools/assets/build_restaurant_products.py"
    )
    if not isinstance(generator, Mapping) or (
        generator.get("module") != "tools.assets.build_restaurant_products"
        or generator.get("algorithm_version") != "1"
        or not generator_path.is_file()
        or generator.get("source_sha256") != _sha256(generator_path)
    ):
        raise RuntimeError("Restaurant candidate generator provenance is invalid")
    return RestaurantBenchmarkProducts(
        root=candidate,
        overlay_usd=paths[PRODUCT_NAMES[0]],
        map_png=paths[PRODUCT_NAMES[1]],
        map_yaml=paths[PRODUCT_NAMES[2]],
        metadata=metadata_path,
        bundle_manifest_sha256=manifest_sha,
    )


__all__ = [
    "LOCAL_CANDIDATE_GATES",
    "PRODUCT_ROOT_ENV",
    "RestaurantBenchmarkProducts",
    "resolve_restaurant_benchmark_products",
]
