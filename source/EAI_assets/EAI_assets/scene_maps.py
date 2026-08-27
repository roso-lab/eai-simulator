"""Compatibility helpers for scene-owned occupancy maps."""

from __future__ import annotations

from EAI_assets.scene_resources import (
    OCCUPANCY_MAP,
    SCENE_RESOURCE_PATHS,
    scene_resource_relative_paths,
)


SCENE_MAP_PATHS = {
    scene_key: resources[OCCUPANCY_MAP]
    for scene_key, resources in SCENE_RESOURCE_PATHS.items()
}


def scene_map_relative_paths(scene_key: str) -> tuple[str, str]:
    """Return the provider-relative YAML and PNG paths for a selectable scene."""
    yaml_path, png_path = scene_resource_relative_paths(scene_key, OCCUPANCY_MAP)
    return yaml_path, png_path
