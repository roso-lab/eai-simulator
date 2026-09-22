"""Read-only OpenUSD audit for the local restaurant authoring package."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_restaurant_scene(usd_root: str | Path) -> dict[str, Any]:
    """Inspect composition, scale and physics declarations without Isaac startup."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

    root = Path(usd_root).expanduser().resolve()
    source = root / config.USD_RELATIVE_PATH
    report: dict[str, Any] = {
        "schema": "eai.restaurant.scene_audit",
        "version": 1,
        "scene_key": config.SCENE_KEY,
        "source": str(source),
        "source_exists": source.is_file(),
        "release_status": config.RELEASE_STATUS,
        "execution_validated": config.EXECUTION_VALIDATED,
        "scoreable": config.SCOREABLE,
        "occupancy_map_available": False,
        "navigation_ready": False,
        "benchmark_ready": False,
    }
    if not source.is_file():
        return {**report, "composition_opened": False, "authoring_preflight_passed": False}

    stage = Usd.Stage.Open(str(source))
    if stage is None:
        return {**report, "composition_opened": False, "authoring_preflight_passed": False}

    default_prim = stage.GetDefaultPrim()
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    collision_paths = [
        str(prim.GetPath())
        for prim in stage.Traverse()
        if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(str(source)))
    bound = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    ).ComputeWorldBound(default_prim).ComputeAlignedRange()
    minimum = [float(value) * config.SOURCE_METERS_PER_UNIT for value in bound.GetMin()]
    maximum = [float(value) * config.SOURCE_METERS_PER_UNIT for value in bound.GetMax()]
    expected_scale = meters_per_unit == config.SOURCE_METERS_PER_UNIT
    authoring_preflight_passed = bool(default_prim) and expected_scale and bool(collision_paths)
    return {
        **report,
        "source_sha256": _sha256(source),
        "composition_opened": True,
        "default_prim": str(default_prim.GetPath()) if default_prim else None,
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "source_meters_per_unit": meters_per_unit,
        "runtime_scale": list(config.RUNTIME_SCALE),
        "runtime_bounds_m": {"min": minimum, "max": maximum},
        "layer_count": len(layers),
        "asset_dependency_count": len(assets),
        "unresolved_dependency_count": len(unresolved),
        "unresolved_dependencies": sorted(str(value) for value in unresolved),
        "collision_prim_count": len(collision_paths),
        "collision_prim_paths": collision_paths,
        "authoring_preflight_passed": authoring_preflight_passed,
        "asset_closure_validated": not unresolved,
    }
