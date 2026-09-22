"""Read-only OpenUSD audit for the local restaurant authoring package."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_UNCOMPOSED_STRUCTURAL_SUFFIXES = (
    "Assemblies/assembly_City.usd",
    "Assemblies/assembly_Site.usd",
    "Source/bldg_Restaurant/overs/assets_Exterior.usd",
)


def _unresolved_category(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith(_UNCOMPOSED_STRUCTURAL_SUFFIXES):
        return "uncomposed_structural"
    if normalized.endswith("Materials/CitySky1.exr"):
        return "uncomposed_environment"
    if normalized in {"OmniGlass.mdl", "OmniPBR.mdl", "OmniSurface.mdl"}:
        return "kit_core_mdl_module"
    if normalized.lower().endswith(".png"):
        if (
            "/Assets/Vegetation/" in normalized
            or normalized.startswith("Assets/Vegetation/")
            or "/Materials/SteelWire/" in normalized
            or normalized.startswith("Materials/SteelWire/")
        ):
            return "uncomposed_custom_data_texture"
        return "stale_custom_data_with_valid_default"
    return "effective_unresolved"


def _normalize_unresolved_path(raw_path: str, scene_root: Path) -> str:
    normalized = raw_path.replace("\\", "/")
    path = Path(normalized)
    if not path.is_absolute():
        return normalized
    try:
        return path.resolve().relative_to(scene_root.resolve()).as_posix()
    except ValueError:
        return f"external:{path.resolve().as_posix()}"


def _used_layer_manifest(stage, scene_root: Path) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    failures: list[str] = []
    resolved_root = scene_root.resolve()
    for layer in stage.GetUsedLayers():
        if not layer.realPath:
            continue
        path = Path(layer.realPath).resolve()
        if not path.is_file():
            failures.append(f"missing_used_layer:{path.as_posix()}")
            continue
        try:
            name = path.relative_to(resolved_root).as_posix()
        except ValueError:
            name = f"external:{path.as_posix()}"
        rows.append({"path": name, "sha256": _sha256(path)})
    rows.sort(key=lambda row: row["path"])
    return rows, failures


def _evaluate_closure_policy(
    *,
    source_sha256: str,
    normalized_unresolved: list[str],
    used_layer_manifest_sha256: str,
    used_layer_failures: list[str],
    composition_errors: list[str],
    missing_core_modules: list[str],
) -> list[str]:
    failures: list[str] = []
    if source_sha256 != config.SOURCE_SHA256:
        failures.append("policy:source_sha256_mismatch")
    if _manifest_sha256(normalized_unresolved) != config.UNRESOLVED_MANIFEST_SHA256:
        failures.append("policy:unresolved_manifest_mismatch")
    if used_layer_manifest_sha256 != config.USED_LAYER_MANIFEST_SHA256:
        failures.append("policy:used_layer_manifest_mismatch")
    failures.extend(used_layer_failures)
    failures.extend(f"composition_error:{error}" for error in composition_errors)
    failures.extend(f"missing_kit_core_module:{name}" for name in missing_core_modules)
    return failures


def _effective_closure_report(
    *,
    stage,
    scene_root: Path,
    source_sha256: str,
    unresolved: list[str],
) -> dict[str, Any]:
    categories: dict[str, int] = {}
    composition_errors = [str(error) for error in stage.GetCompositionErrors()]
    normalized_unresolved = sorted(
        _normalize_unresolved_path(raw_path, scene_root) for raw_path in unresolved
    )
    unresolved_manifest_sha256 = _manifest_sha256(normalized_unresolved)
    used_layer_rows, used_layer_failures = _used_layer_manifest(stage, scene_root)
    used_layer_manifest_sha256 = _manifest_sha256(used_layer_rows)
    source_matches_policy = source_sha256 == config.SOURCE_SHA256
    unresolved_manifest_matches_policy = (
        unresolved_manifest_sha256 == config.UNRESOLVED_MANIFEST_SHA256
    )
    used_layer_manifest_matches_policy = (
        used_layer_manifest_sha256 == config.USED_LAYER_MANIFEST_SHA256
    )
    core_mdl_root = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
        / "isaacsim"
        / "kit"
        / "mdl"
        / "core"
        / "Base"
    )

    for raw_path in normalized_unresolved:
        category = _unresolved_category(raw_path)
        categories[category] = categories.get(category, 0) + 1
    core_modules = sorted(
        path
        for path in normalized_unresolved
        if _unresolved_category(path) == "kit_core_mdl_module"
    )
    missing_core_modules = [
        module for module in core_modules if not (core_mdl_root / module).is_file()
    ]
    effective = _evaluate_closure_policy(
        source_sha256=source_sha256,
        normalized_unresolved=normalized_unresolved,
        used_layer_manifest_sha256=used_layer_manifest_sha256,
        used_layer_failures=used_layer_failures,
        composition_errors=composition_errors,
        missing_core_modules=missing_core_modules,
    )
    return {
        "policy_version": 2,
        "policy_source_sha256": config.SOURCE_SHA256,
        "source_matches_policy": source_matches_policy,
        "unresolved_manifest_sha256": unresolved_manifest_sha256,
        "policy_unresolved_manifest_sha256": config.UNRESOLVED_MANIFEST_SHA256,
        "unresolved_manifest_matches_policy": unresolved_manifest_matches_policy,
        "used_layer_count": len(used_layer_rows),
        "used_layer_manifest_sha256": used_layer_manifest_sha256,
        "policy_used_layer_manifest_sha256": config.USED_LAYER_MANIFEST_SHA256,
        "used_layer_manifest_matches_policy": used_layer_manifest_matches_policy,
        "used_layer_failures": used_layer_failures,
        "category_counts": dict(sorted(categories.items())),
        "composition_errors": composition_errors,
        "effective_unresolved_dependency_count": len(effective),
        "effective_unresolved_dependencies": effective,
        "effective_asset_closure_validated": not effective,
    }


def audit_restaurant_scene(usd_root: str | Path) -> dict[str, Any]:
    """Inspect composition, scale and physics declarations without Isaac startup."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

    root = Path(usd_root).expanduser().resolve()
    source = root / config.USD_RELATIVE_PATH
    report: dict[str, Any] = {
        "schema": "eai.restaurant.scene_audit",
        "version": 2,
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
    layers, assets, unresolved_assets = UsdUtils.ComputeAllDependencies(
        Sdf.AssetPath(str(source))
    )
    unresolved = sorted(str(value) for value in unresolved_assets)
    source_sha256 = _sha256(source)
    effective_closure = _effective_closure_report(
        stage=stage,
        scene_root=source.parent,
        source_sha256=source_sha256,
        unresolved=unresolved,
    )
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
        "source_sha256": source_sha256,
        "composition_opened": True,
        "default_prim": str(default_prim.GetPath()) if default_prim else None,
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "source_meters_per_unit": meters_per_unit,
        "runtime_scale": list(config.RUNTIME_SCALE),
        "runtime_bounds_m": {"min": minimum, "max": maximum},
        "layer_count": len(layers),
        "asset_dependency_count": len(assets),
        "unresolved_dependency_count": len(unresolved),
        "unresolved_dependencies": unresolved,
        "collision_prim_count": len(collision_paths),
        "collision_prim_paths": collision_paths,
        "authoring_preflight_passed": authoring_preflight_passed,
        "raw_asset_closure_validated": not unresolved,
        "asset_closure_validated": not unresolved,
        "effective_asset_closure_validated": effective_closure[
            "effective_asset_closure_validated"
        ],
        "effective_closure": effective_closure,
    }
