"""Audited scenario asset registry: procedural primitives plus NVIDIA SimReady props.

The procedural entries are always available. SimReady props come from the
tracked ``scenario_props.json`` registry and resolve against the local NVIDIA
asset download via ``EAI_NVD_ASSET_ROOT``; when the root is unset or a file is
missing the props simply drop out, so a clean checkout keeps the three
procedural assets only. All SimReady props are visual meshes without physics
on disk -- the scenario builder injects rigid-body properties and a
programmatically estimated mass at spawn time.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScenarioAsset:
    key: str
    description: str
    modes: tuple[str, ...]
    color: tuple[float, float, float]
    relative_paths: tuple[str, ...] = ()
    suggested_mass_kg: float | None = None
    category: str = "primitive"
    size_m: tuple[float, float, float] | None = None


_ASSETS_FILE = Path(__file__).with_name("scenario_props.json")
PROP_COLOR = (0.45, 0.55, 0.7)


def _scenario_asset_from_registry(item: dict) -> ScenarioAsset:
    return ScenarioAsset(
        key=item["key"],
        description=f"NVIDIA SimReady {item['category']} "
                    f"({item['size_m'][0]:g}x{item['size_m'][1]:g}x{item['size_m'][2]:g} m)",
        modes=tuple(item["modes"]),
        color=PROP_COLOR,
        relative_paths=(item["usd_rel"],),
        suggested_mass_kg=item.get("suggested_mass_kg"),
        category=item["category"],
        size_m=tuple(item["size_m"]) if item.get("size_m") else None,
    )


def _load_declared_nvd_props() -> dict[str, ScenarioAsset]:
    if not _ASSETS_FILE.exists():
        return {}
    registry = json.loads(_ASSETS_FILE.read_text(encoding="utf-8"))
    return {item["key"]: _scenario_asset_from_registry(item)
            for item in registry.get("props", [])}


def _load_nvd_props() -> dict[str, ScenarioAsset]:
    """Load only NVD declarations whose files are available under the root."""
    nvd_root = os.environ.get("EAI_NVD_ASSET_ROOT")
    if not nvd_root or not Path(nvd_root).is_dir():
        return {}
    declared = _load_declared_nvd_props()
    return {key: asset for key, asset in declared.items()
            if (Path(nvd_root) / asset.relative_paths[0]).is_file()}


PROGRAMMATIC_ASSETS = {
    "primitive_box": ScenarioAsset("primitive_box", "Solid cuboid cargo; no grasp mechanism implied", ("dynamic",), (0.65, 0.3, 0.1)),
    "safety_beam": ScenarioAsset("safety_beam", "Cuboid barrier", ("static", "kinematic", "dynamic"), (0.9, 0.45, 0.05)),
    "goal_marker": ScenarioAsset("goal_marker", "Non-colliding cuboid visual marker", ("visual",), (0.15, 0.8, 0.25)),
}

DECLARED_ASSETS = {**PROGRAMMATIC_ASSETS, **_load_declared_nvd_props()}
ASSETS = {**PROGRAMMATIC_ASSETS, **_load_nvd_props()}


def get_declared_asset(key: str) -> ScenarioAsset:
    """Return a registered asset declaration without checking local availability.

    This pure lookup is intended for scenario generation and validation before
    a provider root is configured.  It performs no downloads and does not
    inspect or mutate environment variables.  ``ASSETS`` remains the runtime
    availability view for callers that need a locally resolvable asset.
    """
    try:
        return DECLARED_ASSETS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown declared scenario asset: {key!r}") from exc


def resolve_usd_path(key: str) -> Path | None:
    """Absolute USD path for an NVD-backed asset key, or None."""
    asset = ASSETS.get(key)
    if asset is None or not asset.relative_paths:
        return None
    root = os.environ.get("EAI_NVD_ASSET_ROOT")
    if not root:
        return None
    candidate = Path(root) / asset.relative_paths[0]
    return candidate if candidate.exists() else None


def nvd_root_status() -> dict:
    """Registry visibility report for manifests and diagnostics."""
    root = os.environ.get("EAI_NVD_ASSET_ROOT")
    registered = sum(1 for asset in ASSETS.values() if asset.category != "primitive")
    return {"env_var": "EAI_NVD_ASSET_ROOT", "root_set": bool(root),
            "props_registered": registered,
            "props_in_registry_file": len(json.loads(_ASSETS_FILE.read_text()).get("props", []))
            if _ASSETS_FILE.exists() else 0}


def asset_manifest() -> list[dict]:
    return [{key: value for key, value in asdict(asset).items() if key != "relative_paths"}
            for asset in ASSETS.values()]
