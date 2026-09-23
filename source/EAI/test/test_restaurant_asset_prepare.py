from pathlib import Path

import pytest

from tools.assets.prepare_restaurant_benchmark_assets import _source_reference, inspect_candidate


def test_source_reference_is_resolved_relative_to_overlay(tmp_path: Path):
    overlay = tmp_path / "nested" / "products" / "restaurant_benchmark_bench.usda"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        'prepend references = @../../../../../eai-simulator/usd/scene/restaurant/Restaurant_Only.usda@\n',
        encoding="utf-8",
    )
    # The frozen overlay intentionally reaches the checkout-level
    # ``/tmp/eai-simulator`` layout after five parent traversals.
    assert _source_reference(overlay) == Path(
        "/tmp/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda"
    )


def test_inspection_fails_before_hash_or_episode_when_bundle_is_incomplete(tmp_path: Path):
    report = inspect_candidate(tmp_path / "missing")
    assert report["status"] == "failed"
    assert report["reason"] == "candidate_files_missing"
    assert "restaurant_benchmark_bench.usda" in report["missing"]


def test_inspection_rejects_overlay_without_source_reference(tmp_path: Path):
    root = tmp_path / "candidate"
    root.mkdir()
    for name in ("restaurant_benchmark_bench.usda", "restaurant_benchmark_map.png",
                 "restaurant_benchmark_map.yaml", "bench_meta.json"):
        (root / name).write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source reference"):
        inspect_candidate(root)
