from pathlib import Path

import pytest

from tools.assets.prepare_restaurant_benchmark_assets import (
    _source_reference, copy_candidate, inspect_candidate, materialize_source_closure,
)


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


def test_materialize_source_closure_preserves_overlay_and_rejects_byte_mismatch(tmp_path: Path):
    root = tmp_path / "candidate"
    root.mkdir()
    overlay = root / "restaurant_benchmark_bench.usda"
    overlay.write_text("prepend references = @../assets/Restaurant_Only.usda@\n", encoding="utf-8")
    source = tmp_path / "reviewed.usda"
    source.write_bytes(b"reviewed-source")
    original_overlay = overlay.read_bytes()
    report = materialize_source_closure(root, source)
    assert report["copied"] is True
    assert Path(report["destination"]).read_bytes() == source.read_bytes()
    assert overlay.read_bytes() == original_overlay
    assert materialize_source_closure(root, source)["copied"] is False
    Path(report["destination"]).write_bytes(b"different-source")
    with pytest.raises(RuntimeError, match="bytes differ"):
        materialize_source_closure(root, source)


def test_copy_candidate_can_materialize_source_after_target_layout_exists(tmp_path: Path, monkeypatch):
    import EAI_assets.restaurant_benchmark_products as products

    source_root = tmp_path / "source"
    target_root = tmp_path / "a" / "b" / "c" / "d" / "candidate"
    source_root.mkdir()
    overlay = source_root / "restaurant_benchmark_bench.usda"
    overlay.write_text(
        'prepend references = @../../../../../eai-simulator/usd/scene/restaurant/Restaurant_Only.usda@\n',
        encoding="utf-8",
    )
    for name in ("restaurant_benchmark_map.png", "restaurant_benchmark_map.yaml", "bench_meta.json"):
        (source_root / name).write_bytes(name.encode())
    source = tmp_path / "reviewed.usda"
    source.write_bytes(b"reviewed-source")
    monkeypatch.setattr(products, "resolve_restaurant_benchmark_products", lambda root: None)

    result = copy_candidate(source_root, target_root, source_usd=source)

    assert result["status"] == "ready"
    assert Path(result["overlay_source_reference"]).read_bytes() == source.read_bytes()
