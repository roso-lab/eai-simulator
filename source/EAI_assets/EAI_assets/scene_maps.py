"""Scene-owned occupancy map paths shared by asset consumers."""

from __future__ import annotations


SCENE_MAP_PATHS = {
    scene_key: (
        f"scene/{scene_key}/{scene_key}_map.yaml",
        f"scene/{scene_key}/{scene_key}_map.png",
    )
    for scene_key in (
        "plane",
        "warehouse",
        "factory",
        "airs",
        "garden",
        "desert",
        "hospital",
    )
}


def scene_map_relative_paths(scene_key: str) -> tuple[str, str]:
    """Return the provider-relative YAML and PNG paths for a selectable scene."""
    try:
        return SCENE_MAP_PATHS[scene_key]
    except KeyError as exc:
        raise ValueError(f"Unknown scene map key: {scene_key!r}") from exc
