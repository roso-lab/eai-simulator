#!/usr/bin/env python3
"""Validate scene-map ownership, requirements, and preflight collection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
for source_root in (REPO_ROOT, REPO_ROOT / "source" / "EAI", REPO_ROOT / "source" / "EAI_assets"):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

import simulator  # noqa: E402
from EAI.hmrs_env.env_diy.catalog import scene_choices  # noqa: E402
from EAI_assets.asset_requirements import _SCENE_PATHS, resolve_selection  # noqa: E402
from EAI_assets.scene_maps import SCENE_MAP_PATHS  # noqa: E402


def _check_scene_requirements() -> None:
    scene_keys = tuple(key for key, _label in scene_choices())
    assert scene_keys == tuple(SCENE_MAP_PATHS)
    assert scene_keys == tuple(_SCENE_PATHS)

    for scene_key in scene_keys:
        expected_maps = (
            f"scene/{scene_key}/{scene_key}_map.yaml",
            f"scene/{scene_key}/{scene_key}_map.png",
        )
        assert SCENE_MAP_PATHS[scene_key] == expected_maps
        assert _SCENE_PATHS[scene_key][-2:] == expected_maps

        graph = resolve_selection({"scene_key": scene_key, "robots": []})
        requirement = next(item for item in graph.requirements if item.id == f"scene:{scene_key}")
        assert requirement.relative_paths == _SCENE_PATHS[scene_key]
        assert requirement.remote_paths[-2:] == tuple(f"usd/{path}" for path in expected_maps)


def _check_preflight_merge() -> None:
    payload = simulator._build_asset_payload(
        task_name="scene-map-check",
        selection_data={"scene_key": "plane", "robots": []},
        saved_task_data=None,
        should_run=True,
        env_cfg=object(),
        collect_usd_asset_paths=lambda _cfg: ["/tmp/root.usd", "/tmp/shared.png"],
        collect_controller_asset_paths=lambda _cfg: [],
        collect_selection_usd_asset_paths=lambda _selection: ["/tmp/shared.png", "/tmp/plane_map.yaml"],
    )
    assert payload["usd_paths"] == ["/tmp/root.usd", "/tmp/shared.png", "/tmp/plane_map.yaml"]

    collected = simulator._collect_selection_usd_asset_paths({"scene_key": "plane", "robots": []})
    assert any(path.endswith("/scene/plane/plane_map.yaml") for path in collected)
    assert any(path.endswith("/scene/plane/plane_map.png") for path in collected)


def _check_source_ownership() -> None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "algorithm/multi_robot_navigation/maps/**",
            "demo/fire_rescue/assets/factory_map.*",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tuple(line for line in result.stdout.splitlines() if line)
    remaining = tuple(path for path in tracked if (REPO_ROOT / path).exists())
    assert not remaining, f"Scene maps still exist under algorithms or demos: {remaining}"


def main() -> int:
    _check_scene_requirements()
    _check_preflight_merge()
    _check_source_ownership()
    print("PASS: scene maps are provider-owned and included in selection preflight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
