#!/usr/bin/env python3
"""Build lightweight retarget maps for every advertised human motion pair."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
EAI_ASSETS_SOURCE = REPO_ROOT / "source/EAI_assets"
if str(EAI_ASSETS_SOURCE) not in sys.path:
    sys.path.insert(0, str(EAI_ASSETS_SOURCE))

from EAI_assets.humans import (
    HumanAssetRegistry,
    RetargetCacheEntry,
    build_retarget_plan,
)
from EAI_assets.humans.profiles import joint_aliases_for


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: Any) -> bool:
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return True


@dataclass(frozen=True)
class _TargetSkeleton:
    joints: tuple[str, ...]
    rest_translations: tuple[tuple[float, float, float], ...]
    rest_rotations: tuple[tuple[float, float, float, float], ...]
    relative_path: str
    meters_per_unit: float
    root_up: tuple[float, float, float]
    sha256: str


@dataclass(frozen=True)
class _SourceAnimation:
    joints: tuple[str, ...]
    path: str
    sample_start: float
    sample_end: float
    time_codes_per_second: float
    meters_per_unit: float
    root_translation_by_joint: dict[str, tuple[float, float, float]]
    skeleton_rest_rotation_by_joint: dict[
        str, tuple[float, float, float, float]
    ]
    sha256: str


def _rotation_components(transform: Any) -> tuple[float, float, float, float]:
    quaternion = transform.ExtractRotationQuat()
    imaginary = quaternion.GetImaginary()
    return (
        float(quaternion.GetReal()),
        float(imaginary[0]),
        float(imaginary[1]),
        float(imaginary[2]),
    )


def _target_skeleton(path: Path) -> _TargetSkeleton:
    from pxr import Gf, Usd, UsdGeom, UsdSkel

    stage = Usd.Stage.Open(path.as_posix())
    if stage is None or not stage.GetDefaultPrim():
        raise ValueError("target stage or default prim is missing")
    skeleton_prim = next(
        (prim for prim in stage.Traverse() if prim.IsA(UsdSkel.Skeleton)), None
    )
    if skeleton_prim is None:
        raise ValueError("target asset has no Skeleton")
    skeleton = UsdSkel.Skeleton(skeleton_prim)
    joints = tuple(str(value) for value in skeleton.GetJointsAttr().Get() or [])
    rest_transforms = tuple(skeleton.GetRestTransformsAttr().Get() or [])
    rest_translations = tuple(
        tuple(float(value) for value in transform.ExtractTranslation())
        for transform in rest_transforms
    )
    rest_rotations = tuple(
        _rotation_components(transform) for transform in rest_transforms
    )
    relative = skeleton_prim.GetPath().MakeRelativePath(
        stage.GetDefaultPrim().GetPath()
    ).pathString
    stage_up_axis = str(UsdGeom.GetStageUpAxis(stage))
    if stage_up_axis == "Y":
        stage_up = Gf.Vec3d(0.0, 1.0, 0.0)
    elif stage_up_axis == "Z":
        stage_up = Gf.Vec3d(0.0, 0.0, 1.0)
    else:
        raise ValueError(f"unsupported target stage up axis: {stage_up_axis}")
    skeleton_to_stage = UsdGeom.XformCache(
        Usd.TimeCode.Default()
    ).GetLocalToWorldTransform(skeleton_prim)
    local_up = skeleton_to_stage.GetInverse().TransformDir(stage_up)
    root_up = tuple(float(component) for component in local_up)
    return _TargetSkeleton(
        joints,
        rest_translations,
        rest_rotations,
        relative,
        float(UsdGeom.GetStageMetersPerUnit(stage)),
        root_up,
        _sha256(path),
    )


def _source_animation(path: Path) -> _SourceAnimation:
    from pxr import Usd, UsdGeom, UsdSkel

    stage = Usd.Stage.Open(path.as_posix())
    if stage is None:
        raise ValueError("motion stage could not be opened")
    animation_prim = next(
        (prim for prim in stage.Traverse() if prim.IsA(UsdSkel.Animation)), None
    )
    if animation_prim is None:
        raise ValueError("motion stage has no UsdSkelAnimation")
    animation = UsdSkel.Animation(animation_prim)
    joints = tuple(str(value) for value in animation.GetJointsAttr().Get() or [])
    skeleton_prims = tuple(
        prim for prim in stage.Traverse() if prim.IsA(UsdSkel.Skeleton)
    )
    if len(skeleton_prims) != 1:
        raise ValueError("motion stage must contain exactly one Skeleton")
    skeleton_prim = skeleton_prims[0]
    skeleton = UsdSkel.Skeleton(skeleton_prim)
    skeleton_joints = tuple(
        str(value) for value in skeleton.GetJointsAttr().Get() or []
    )
    skeleton_rest_transforms = tuple(
        skeleton.GetRestTransformsAttr().Get() or []
    )
    if len(skeleton_joints) != len(skeleton_rest_transforms):
        raise ValueError("motion skeleton joints and rest transforms differ in width")
    skeleton_rest_rotation_by_joint = {
        joint: _rotation_components(skeleton_rest_transforms[index])
        for index, joint in enumerate(skeleton_joints)
    }
    times = animation.GetRotationsAttr().GetTimeSamples()
    if not times:
        raise ValueError("motion has no rotation samples")
    translation_attr = animation.GetTranslationsAttr()
    translation_times = translation_attr.GetTimeSamples()
    root_translations: dict[str, tuple[float, float, float]] = {}
    if translation_times:
        translations = translation_attr.Get(Usd.TimeCode(translation_times[0])) or []
        root_translations = {
            joint: tuple(float(component) for component in translations[index])
            for index, joint in enumerate(joints)
            if index < len(translations)
        }
    return _SourceAnimation(
        joints=joints,
        path=animation_prim.GetPath().pathString,
        sample_start=float(min(times)),
        sample_end=float(max(times)),
        time_codes_per_second=float(stage.GetTimeCodesPerSecond()),
        meters_per_unit=float(UsdGeom.GetStageMetersPerUnit(stage)),
        root_translation_by_joint=root_translations,
        skeleton_rest_rotation_by_joint=skeleton_rest_rotation_by_joint,
        sha256=_sha256(path),
    )


def _selected_sample_range(motion: Any, source: _SourceAnimation) -> tuple[float, float]:
    requested_start = motion.sample_start
    requested_end = motion.sample_end
    if (requested_start is None) != (requested_end is None):
        raise ValueError("motion sample range bounds must be provided together")
    if requested_start is None:
        return source.sample_start, source.sample_end

    sample_start = float(requested_start)
    sample_end = float(requested_end)
    if not math.isfinite(sample_start) or not math.isfinite(sample_end):
        raise ValueError("motion sample range bounds must be finite")
    if sample_start < source.sample_start or sample_end > source.sample_end:
        raise ValueError(
            "motion sample range is outside source animation range "
            f"[{source.sample_start}, {source.sample_end}]"
        )
    if sample_end <= sample_start:
        raise ValueError("motion sample range must have positive length")
    return sample_start, sample_end


def build_caches(
    manifest_path: Path,
    cache_root: Path,
    *,
    overlays: Sequence[Path] = (),
    asset_ids: Sequence[str] | None = None,
    motion_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    registry = HumanAssetRegistry.load(manifest_path, overlays=overlays)

    if asset_ids is None:
        selected_asset_ids = tuple(
            asset.id for asset in registry.assets() if asset.can_play_actions
        )
    else:
        selected_asset_ids = tuple(sorted({str(asset_id) for asset_id in asset_ids}))
        for asset_id in selected_asset_ids:
            registry.asset(asset_id)

    selected_motion_ids: tuple[str, ...] | None = None
    if motion_ids is not None:
        selected_motion_ids = tuple(
            sorted({str(motion_id) for motion_id in motion_ids})
        )
        for motion_id in selected_motion_ids:
            registry.motion(motion_id)
        for motion_id in selected_motion_ids:
            if registry.motion(motion_id).origin == "custom":
                registry = registry.with_motion_for_assets(
                    motion_id, selected_asset_ids
                )

    targets: dict[Path, _TargetSkeleton] = {}
    sources: dict[Path, _SourceAnimation] = {}
    written = 0
    unchanged = 0
    accepted: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for asset_id in selected_asset_ids:
        asset = registry.asset(asset_id)
        if not asset.can_play_actions:
            continue
        try:
            if asset.usd_path not in targets:
                targets[asset.usd_path] = _target_skeleton(asset.usd_path)
            target = targets[asset.usd_path]
        except Exception as exc:
            failed.append({"asset_id": asset.id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        asset_motion_ids = (
            selected_motion_ids if selected_motion_ids is not None else asset.motions
        )
        for motion_id in asset_motion_ids:
            try:
                motion = registry.require_motion(asset.id, motion_id)
                if motion.usd_path not in sources:
                    sources[motion.usd_path] = _source_animation(motion.usd_path)
                source = sources[motion.usd_path]
                sample_start, sample_end = _selected_sample_range(motion, source)
                joint_aliases = joint_aliases_for(
                    motion.source_profile, asset.animation_profile
                )
                plan = build_retarget_plan(
                    source_joints=source.joints,
                    target_joints=target.joints,
                    target_rest_translations=target.rest_translations,
                    # Placeholder: the plan stores original source names, so
                    # the root translation is fixed up by source name below.
                    source_root_origin=(0.0, 0.0, 0.0),
                    source_meters_per_unit=source.meters_per_unit,
                    target_meters_per_unit=target.meters_per_unit,
                    target_root_up=target.root_up,
                    joint_aliases=joint_aliases,
                    allow_missing=joint_aliases is not None,
                )
                plan = replace(
                    plan,
                    source_root_origin=source.root_translation_by_joint.get(
                        plan.source_joints[plan.source_root_index], (0.0, 0.0, 0.0)
                    ),
                )
                if joint_aliases is not None:
                    target_index_by_name = {
                        joint: index for index, joint in enumerate(target.joints)
                    }
                    plan = replace(
                        plan,
                        source_rest_rotations=tuple(
                            source.skeleton_rest_rotation_by_joint[joint]
                            for joint in plan.source_joints
                        ),
                        target_rest_rotations=tuple(
                            target.rest_rotations[target_index_by_name[joint]]
                            for joint in plan.target_joints
                        ),
                    )
                entry = RetargetCacheEntry(
                    asset_id=asset.id,
                    motion_id=motion.id,
                    source_sha256=source.sha256,
                    target_sha256=target.sha256,
                    source_animation_path=source.path,
                    target_skeleton_relative_path=target.relative_path,
                    source_time_codes_per_second=source.time_codes_per_second,
                    source_sample_start=sample_start,
                    source_sample_end=sample_end,
                    plan=plan,
                )
                cache_path = cache_root / asset.id / f"{motion.id}.json"
                if _write_json(cache_path, entry.to_document()):
                    written += 1
                else:
                    unchanged += 1
                accepted.append(
                    {
                        "asset_id": asset.id,
                        "cache_path": cache_path.relative_to(cache_root.parent).as_posix(),
                        "motion_id": motion.id,
                    }
                )
            except Exception as exc:
                failed.append(
                    {
                        "asset_id": asset.id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "motion_id": motion_id,
                    }
                )

    report = {
        "accepted": accepted,
        "failed": failed,
        "manifest": manifest_path.resolve().as_posix(),
        "unchanged": unchanged,
        "version": 1,
        "written": written,
    }
    _write_json(cache_root.parent / "cache-report.json", report)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--overlay", action="append", type=Path, default=[])
    parser.add_argument("--asset-id", action="append", default=None)
    parser.add_argument("--motion-id", action="append", default=None)
    return parser.parse_args()


def _ensure_pxr_runtime() -> Any | None:
    try:
        importlib.import_module("pxr")
    except ModuleNotFoundError as exc:
        if exc.name != "pxr":
            raise
        from isaacsim import SimulationApp

        return SimulationApp({"headless": True})
    return None


def main() -> int:
    args = _parse_args()
    simulation_app = _ensure_pxr_runtime()
    try:
        cache_root = args.cache_root or args.manifest.parent / "motions/cache"
        report = build_caches(
            args.manifest,
            cache_root,
            overlays=args.overlay,
            asset_ids=args.asset_id,
            motion_ids=args.motion_id,
        )
        print(
            json.dumps(
                {
                    "accepted": len(report["accepted"]),
                    "failed": len(report["failed"]),
                    "unchanged": report["unchanged"],
                    "written": report["written"],
                },
                sort_keys=True,
            )
        )
        return 0 if not report["failed"] else 1
    finally:
        if simulation_app is not None:
            simulation_app.close()


if __name__ == "__main__":
    raise SystemExit(main())
