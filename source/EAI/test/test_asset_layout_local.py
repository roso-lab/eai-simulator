"""Regression checks for portable category scene packages."""

import json
from pathlib import Path


def test_factory_provider_path_resolves_to_checked_in_payload():
    from EAI_assets.asset_resolver import asset_path

    path = Path(asset_path("scene/factory/factory.usd"))
    assert path.is_file()
    assert path == Path(
        "usd/scene/Production and logistics/factory/payload/scene/factory/factory.usd"
    ).resolve()


def test_usd_dependency_collection_uses_the_same_factory_alias():
    from EAI_assets.asset_resolver import collect_usd_asset_paths

    paths = collect_usd_asset_paths({"usd_path": "usd/scene/factory/factory.usd"})
    assert paths == [str(
        Path("usd/scene/Production and logistics/factory/payload/scene/factory/factory.usd").resolve()
    )]


def test_scene_alias_resolution_does_not_leak_between_configured_roots(monkeypatch, tmp_path):
    from EAI_assets import asset_resolver

    package = tmp_path / "scene" / "Imported" / "factory"
    payload = package / "payload" / "scene" / "factory"
    payload.mkdir(parents=True)
    (payload / "factory.usd").write_bytes(b"usd")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "scene_name": "factory",
                "source_scene": "usd/scene/factory",
                "runtime": {"payload_scene": "./payload/scene/factory/factory.usd"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EAI_USD_ROOT", str(tmp_path))
    first = Path(asset_resolver.asset_path("scene/factory/factory.usd"))
    assert first == (payload / "factory.usd").resolve()

    other_root = tmp_path / "other"
    other_root.mkdir()
    monkeypatch.setenv("EAI_USD_ROOT", str(other_root))
    second = Path(asset_resolver.asset_path("scene/factory/factory.usd"))
    assert second == (other_root / "scene/factory/factory.usd").resolve()
