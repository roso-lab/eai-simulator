#!/usr/bin/env python3
"""Migrate validated urban-sim human assets into the local usd/human tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from tools.human_assets.convert_gltf_assets import (
        build_conversion_plan,
        build_conversion_plan_with_rejections,
        ensure_safe_source_path,
        ensure_safe_target_path,
        ensure_target_directory,
        gltf_dependency_records,
        gltf_image_inventory,
        lexical_absolute,
        output_dependency_records,
        output_image_inventory,
        UnsafeTargetPathError,
        validate_texture_fidelity,
    )
except ModuleNotFoundError:  # direct execution from tools/human_assets
    from convert_gltf_assets import (
        build_conversion_plan,
        build_conversion_plan_with_rejections,
        ensure_safe_source_path,
        ensure_safe_target_path,
        ensure_target_directory,
        gltf_dependency_records,
        gltf_image_inventory,
        lexical_absolute,
        output_dependency_records,
        output_image_inventory,
        UnsafeTargetPathError,
        validate_texture_fidelity,
    )


_READY_MOTIONS = {
    "synbody_walking426.fbx": ("walk", "Walk", 28.0 / 24.0, True),
}

_MOTION_CANDIDATE_IDS = {
    "2023_09_04T16_30_39-greeting-09-certificate-kawaguchi-fps_30.gltf": "bow",
    "2023_09_04T16_40_16-15_01-fps_30.gltf": "walk_and_look",
    "2023_09_04T16_44_42-120_04-fps_30.gltf": "dance",
    "2023_09_04T16_48_11-B4_-_stand_to_walk_back-fps_30.gltf": "walk_backward",
    "synbody_jog426.fbx.gltf": "jog",
}

_ACTIVITY_CANDIDATES = {
    "BikeMan.fbx.gltf": ("bike-man", "cyclist"),
    "eScooterWoman.fbx.gltf": ("escooter-woman", "scooter_rider"),
    "skateboardMan1.fbx.gltf": ("skateboard-man-1", "skateboarder"),
    "free3DVersion.gltf": ("wheelchair-rider", "wheelchair"),
}

_SKELETON_SIGNATURES = {
    "rpm_87": "usdskel-joints-sha256:adc28fceaf2fb7a270e940ffff26ae9d99e3534a1fd469c992d26b37fd57fdec",
    "smplx_70": "usdskel-joints-sha256:fc5614ebc10af35bad38340ce3c225b5515988516e90506021a6f4ed42c72f14",
    "synbody_55": "usdskel-joints-sha256:78ef1131b9ce37a4f6b29f489f0591910220c19b0a6667db316e6ebce7a6179c",
}

_MOTION_SEMANTICS = {
    "bow": "gesture",
    "forward_dive": "action",
    "hit_reaction_retreat": "reaction",
    "jog": "locomotion",
    "dance": "dance",
    "long_stride_walk": "locomotion",
    "phone_call": "communication",
    "stagger_walk": "locomotion",
    "walk_and_look": "locomotion",
    "walk_and_text": "locomotion",
    "walk_backward": "locomotion",
    "walk": "locomotion",
}

_MOTION_VARIANTS = {
    "forward_dive": "forward",
    "hit_reaction_retreat": "backward",
    "long_stride_walk": "long_stride",
    "phone_call": "phone",
    "stagger_walk": "stagger",
    "walk_and_text": "texting",
    "walk_backward": "backward",
}

_MOTION_LABELS = {
    "bow": "Bow",
    "dance": "Dance",
    "forward_dive": "Forward Dive",
    "hit_reaction_retreat": "Hit Reaction Retreat",
    "long_stride_walk": "Long Stride Walk",
    "phone_call": "Phone Call",
    "stagger_walk": "Stagger Walk",
    "walk_and_look": "Walk And Look",
    "walk_and_text": "Walk And Text",
    "walk_backward": "Walk Backward",
}
_MOTION_PATH_POLICIES = {
    "bow": "pause",
    "forward_dive": "continue",
    "hit_reaction_retreat": "continue",
    "jog": "continue",
    "dance": "pause",
    "long_stride_walk": "continue",
    "phone_call": "pause",
    "stagger_walk": "continue",
    "walk_and_look": "continue",
    "walk_and_text": "continue",
    "walk_backward": "continue",
    "walk": "continue",
}
_MOTION_TAGS = {
    "bow": ("bow", "gesture", "gltf"),
    "forward_dive": ("action", "dive", "forward", "rpm"),
    "hit_reaction_retreat": ("backward", "reaction", "retreat", "rpm"),
    "jog": ("gltf", "locomotion"),
    "dance": ("dance", "gltf"),
    "long_stride_walk": ("locomotion", "long_stride", "rpm"),
    "phone_call": ("communication", "phone", "rpm"),
    "stagger_walk": ("locomotion", "rpm", "stagger"),
    "walk_and_look": ("gltf", "locomotion"),
    "walk_and_text": ("locomotion", "rpm", "texting"),
    "walk_backward": ("backward", "gltf", "locomotion"),
}
_MOTION_SAMPLE_RANGES = {
    "bow": (120, 160),
    "dance": (48, 1301),
    "walk_and_look": (0, 72),
    "walk_backward": (48, 182),
}
_MOTION_FACING_YAW_OFFSETS = {
    "bow": -math.pi / 2,
    "forward_dive": -math.pi / 2,
    "hit_reaction_retreat": math.pi / 2,
    "long_stride_walk": -math.pi / 2,
    "phone_call": -math.pi / 2,
    "stagger_walk": -math.pi / 2,
    "walk_and_look": math.pi,
    "walk_and_text": -math.pi / 2,
    "walk_backward": math.pi / 2,
}
_LOOPED_MOTIONS = {
    "jog",
    "long_stride_walk",
    "stagger_walk",
    "walk_and_text",
}
_SYNBODY_MOTION_ORDER = (
    "bow",
    "jog",
    "dance",
    "walk_and_look",
    "walk_backward",
    "walk",
)
_RPM_MOTION_ORDER = (
    "phone_call",
    "long_stride_walk",
    "walk_and_text",
    "stagger_walk",
    "hit_reaction_retreat",
    "forward_dive",
)
_UNIVERSAL_MOTION_ORDER = _SYNBODY_MOTION_ORDER + _RPM_MOTION_ORDER
_CONVERTED_ARTICULATED_ASSET_YAW = math.pi
_CONVERTED_RIGID_ASSET_YAW = math.pi / 2

_CREDENTIAL_NAMES = frozenset(
    {"client_secret.json", "client_secrets.json", "credentials.json", "token.json"}
)
_SOURCE_PROVENANCE_ROOT = "urban-sim"
_TARGET_PROVENANCE_ROOT = "usd/human"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REVIEWED_TEXTURE_COLLISION_ASSETS = {"wheelchair-rider": ("Image",)}
_DETAIL_FIELDS = frozenset(
    {
        "animation_count",
        "duration",
        "mesh_count",
        "sample_end",
        "sample_start",
        "skeleton_joint_counts",
        "source_fps",
        "texture_fidelity",
        "time_codes_per_second",
    }
)
_TEXTURE_FIDELITY_FIELDS = frozenset(
    {
        "collisions",
        "enabled",
        "extra",
        "mismatched",
        "missing",
        "output_count",
        "reason",
        "source_count",
    }
)


@dataclass(frozen=True)
class MigrationResult:
    ready_asset_count: int
    ready_motion_count: int
    candidate_count: int
    rejected_count: int
    converted_count: int
    manifest_path: Path
    audit_path: Path


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: Any, *, target_root: Path) -> None:
    path = ensure_safe_target_path(path, target_root)
    ensure_target_directory(path.parent, target_root)
    path = ensure_safe_target_path(path, target_root)
    content = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if path.is_file():
        path = ensure_safe_target_path(path, target_root)
        if path.read_text(encoding="utf-8") == content:
            return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary = ensure_safe_target_path(temporary, target_root)
        path = ensure_safe_target_path(path, target_root)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _copy_file(
    source: Path,
    target: Path,
    *,
    target_root: Path,
    dry_run: bool,
) -> None:
    target = ensure_safe_target_path(target, target_root)
    if dry_run:
        return
    ensure_target_directory(target.parent, target_root)
    target = ensure_safe_target_path(target, target_root)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        temporary = ensure_safe_target_path(temporary, target_root)
        target = ensure_safe_target_path(target, target_root)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_credential_path(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in _CREDENTIAL_NAMES
        or "secret" in name
        or "credential" in name
        or name.endswith("_token.json")
    )


def _is_humanfemale_name(path: Path) -> bool:
    return path.name.startswith("HumanFemale")


def _reject_humanfemale_entry(
    source: Path,
    target: Path,
    *,
    source_root: Path,
    rejected: list[dict[str, Any]],
) -> bool:
    if not (_is_humanfemale_name(source) or _is_humanfemale_name(target)):
        return False
    rejected.append(
        {
            "reason": "HumanFemale-named ready entries are never migrated",
            "source": source.relative_to(source_root).as_posix(),
            "status": "rejected",
        }
    )
    return True


def _humanfemale_ready_entries(source_dir: Path) -> tuple[Path, ...]:
    matches: list[Path] = []
    pending = [source_dir]
    while pending:
        directory = pending.pop()
        for entry in sorted(directory.iterdir(), key=lambda path: path.name):
            if entry.name == ".collect.mapping.json":
                continue
            if _is_humanfemale_name(entry):
                matches.append(entry)
            if not entry.is_symlink() and entry.is_dir():
                pending.append(entry)
    return tuple(sorted(matches, key=lambda path: path.as_posix()))


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return True
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _copy_ready_entry(
    source: Path,
    target: Path,
    *,
    source_root: Path,
    target_root: Path,
    dry_run: bool,
    rejected: list[dict[str, Any]],
) -> None:
    if _reject_humanfemale_entry(
        source,
        target,
        source_root=source_root,
        rejected=rejected,
    ):
        return
    target = ensure_safe_target_path(target, target_root)
    if _has_symlink_component(source, source_root):
        rejected.append(
            {
                "reason": "symbolic links are never migrated",
                "source": source.relative_to(source_root).as_posix(),
                "status": "rejected",
            }
        )
        return
    if _is_credential_path(source):
        rejected.append(
            {
                "reason": "credential files are never migrated",
                "source": source.relative_to(source_root).as_posix(),
                "status": "rejected",
            }
        )
        return
    if source.is_dir():
        if not dry_run:
            ensure_target_directory(target, target_root)
        for child in sorted(source.iterdir(), key=lambda path: path.name):
            _copy_ready_entry(
                child,
                target / child.name,
                source_root=source_root,
                target_root=target_root,
                dry_run=dry_run,
                rejected=rejected,
            )
    elif source.is_file():
        _copy_file(source, target, target_root=target_root, dry_run=dry_run)


def _copy_ready_synbody(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    assets: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for source_usd in sorted(source_root.glob("assets/peds/Collected_*/*.usd")):
        if _has_symlink_component(source_usd, source_root):
            rejected.append(
                {
                    "reason": "symbolic links are never migrated",
                    "source": source_usd.relative_to(source_root).as_posix(),
                    "status": "rejected",
                }
            )
            continue
        asset_id = source_usd.stem
        source_dir = source_usd.parent
        target_dir = target_root / "characters" / "synbody" / asset_id
        humanfemale_entries = _humanfemale_ready_entries(source_dir)
        if humanfemale_entries:
            for entry in humanfemale_entries:
                _reject_humanfemale_entry(
                    entry,
                    target_dir / entry.relative_to(source_dir),
                    source_root=source_root,
                    rejected=rejected,
                )
            continue
        ensure_safe_target_path(target_dir, target_root)
        if not dry_run:
            ensure_target_directory(target_dir, target_root)
        for item in sorted(source_dir.iterdir(), key=lambda path: path.name):
            if item.name == ".collect.mapping.json":
                continue
            target = target_dir / ("character.usd" if item == source_usd else item.name)
            _copy_ready_entry(
                item,
                target,
                source_root=source_root,
                target_root=target_root,
                dry_run=dry_run,
                rejected=rejected,
            )
        target_usd = target_dir / "character.usd"
        assets.append(
            {
                "activity_type": "pedestrian",
                "animation_profile": "synbody_55",
                "articulated": True,
                "can_play_actions": True,
                "default_speed": 1.2,
                "enabled": True,
                "ground_offset": 0.0,
                "content_up_axis": "Y",
                "id": f"synbody-{asset_id}",
                "label": f"SynBody {asset_id}",
                "motions": ["idle", "walk"],
                "duplicate_of": None,
                "path_following": True,
                "redistribution_status": "review_required",
                "scale": [0.01, 0.01, 0.01],
                "skeleton_signature": _SKELETON_SIGNATURES["synbody_55"],
                "source": {
                    "asset_id": asset_id,
                    "license": "review_required",
                    "managed_by": "urban_sim_migration",
                    "project": "SynBody",
                    "source_path": _relative(source_usd, source_root),
                },
                "turning_speed": math.tau,
                "usd_path": target_usd.relative_to(target_root).as_posix(),
                "validation": "source_usd_ready",
                "yaw_offset": math.pi,
            }
        )
        accepted.append(
            {
                "id": f"synbody-{asset_id}",
                "source": _relative(source_usd, source_root),
                "status": "accepted",
                "target": target_usd.relative_to(target_root).as_posix(),
            }
        )
    rejected.sort(key=lambda entry: entry["source"])
    return assets, accepted, rejected


def _copy_ready_motions(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    motions: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for source_usd in sorted(source_root.glob("assets/ped_actions/*/*.usd")):
        config = _READY_MOTIONS.get(source_usd.parent.name)
        expected_name = f"{source_usd.parent.name}.usd"
        if config is None or source_usd.name != expected_name:
            continue
        if _has_symlink_component(source_usd, source_root):
            rejected.append(
                {
                    "reason": "symbolic links are never migrated",
                    "source": source_usd.relative_to(source_root).as_posix(),
                    "status": "rejected",
                }
            )
            continue
        motion_id, label, duration, loop = config
        target_usd = target_root / "motions" / "sources" / f"{motion_id}.usd"
        _copy_file(
            source_usd,
            target_usd,
            target_root=target_root,
            dry_run=dry_run,
        )
        motions.append(
            {
                "content_sha256": _sha256(source_usd),
                "duration": duration,
                "enabled": True,
                "id": motion_id,
                "label": label,
                "loop": loop,
                "path_policy": "continue",
                "redistribution_status": "review_required",
                "resume_policy": "resume_phase",
                "root_motion": "in_place",
                "semantic": _MOTION_SEMANTICS[motion_id],
                "source": {
                    "license": "review_required",
                    "managed_by": "urban_sim_migration",
                    "project": "SynBody",
                    "source_path": _relative(source_usd, source_root),
                },
                "source_profile": "smplx_70",
                "usd_path": target_usd.relative_to(target_root).as_posix(),
                "variant": "default",
            }
        )
        accepted.append(
            {
                "id": motion_id,
                "source": _relative(source_usd, source_root),
                "status": "accepted",
                "target": target_usd.relative_to(target_root).as_posix(),
            }
        )
    rejected.sort(key=lambda entry: entry["source"])
    return motions, accepted, rejected


def _candidate_record(
    *,
    candidate_id: str,
    candidate_type: str,
    source: Path,
    source_root: Path,
) -> dict[str, Any]:
    return {
        "id": candidate_id,
        "source": _relative(source, source_root),
        "status": "conversion_required",
        "type": candidate_type,
    }


def _discover_candidates(
    source_root: Path, target_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    allowed_sources: set[Path] = set()
    plan, symlinked_candidates = build_conversion_plan_with_rejections(
        source_root, target_root
    )
    for item in plan:
        allowed_sources.add(item.source)
        entry = _candidate_record(
            candidate_id=item.id,
            candidate_type=item.kind,
            source=item.source,
            source_root=source_root,
        )
        entry.update(
            {
                "activity_type": item.activity_type,
                "output": item.output.relative_to(target_root).as_posix(),
                "profile": item.profile,
            }
        )
        candidates.append(entry)

    symlinked_sources = {item.source for item in symlinked_candidates}
    rejected: list[dict[str, Any]] = [
        {
            "reason": "symbolic links are never migrated",
            "source": item.source.relative_to(source_root).as_posix(),
            "status": "rejected",
        }
        for item in symlinked_candidates
    ]
    special_root = source_root / "assets/pedestrians/special_agents"
    for path in sorted(special_root.glob("*.gltf")) if special_root.is_dir() else ():
        if path in symlinked_sources:
            continue
        if path not in allowed_sources:
            rejected.append(
                {
                    "reason": (
                        "symbolic links are never migrated"
                        if _has_symlink_component(path, source_root)
                        else "not an allowlisted human activity asset"
                    ),
                    "source": path.relative_to(source_root).as_posix(),
                    "status": "rejected",
                }
            )

    for pattern in (
        "assets/pedestrians/**/client_secrets.json",
        "assets/pedestrians/**/token.json",
    ):
        for path in sorted(source_root.glob(pattern)):
            rejected.append(
                {
                    "reason": "credential files are never migrated",
                    "source": path.relative_to(source_root).as_posix(),
                    "status": "rejected",
                }
            )

    candidates.sort(key=lambda entry: (entry["type"], entry["id"], entry["source"]))
    rejected.sort(key=lambda entry: entry["source"])
    return candidates, rejected


def _nonnegative_report_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid conversion report details")
    return value


def _finite_report_number(value: Any, *, positive: bool) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid conversion report details")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError("invalid conversion report details")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("invalid conversion report details")
    return list(value)


def _texture_fidelity_reason(
    *,
    collisions: list[str],
    missing: list[str],
    extra: list[str],
    mismatched: list[str],
) -> str:
    if collisions:
        return f"texture collision: merged logical names {collisions}"
    if missing:
        return f"missing output texture mappings: {missing}"
    if extra:
        return f"extra output texture mappings: {extra}"
    if mismatched:
        return f"texture mapping mismatch: {mismatched}"
    return "texture mappings verified"


def _sanitized_texture_fidelity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TEXTURE_FIDELITY_FIELDS:
        raise ValueError("invalid conversion report details")
    enabled = value["enabled"]
    if not isinstance(enabled, bool) or not isinstance(value["reason"], str):
        raise ValueError("invalid conversion report details")
    collisions = _string_list(value["collisions"])
    extra = _string_list(value["extra"])
    mismatched = _string_list(value["mismatched"])
    missing = _string_list(value["missing"])
    issue_lists = (collisions, extra, mismatched, missing)
    if any(items != sorted(set(items)) for items in issue_lists):
        raise ValueError("invalid conversion report details")
    output_count = _nonnegative_report_int(value["output_count"])
    source_count = _nonnegative_report_int(value["source_count"])
    has_issues = any(issue_lists)
    expected_reason = _texture_fidelity_reason(
        collisions=collisions,
        missing=missing,
        extra=extra,
        mismatched=mismatched,
    )
    if enabled:
        expected_enabled = not has_issues and source_count == output_count
        if not expected_enabled:
            raise ValueError("invalid conversion report details")
        if value["reason"] != expected_reason:
            raise ValueError("invalid conversion report details")
    elif not has_issues and source_count == output_count and value["reason"] == expected_reason:
        raise ValueError("invalid conversion report details")
    return {
        "collisions": collisions,
        "enabled": enabled,
        "extra": extra,
        "mismatched": mismatched,
        "missing": missing,
        "output_count": output_count,
        "reason": value["reason"],
        "source_count": source_count,
    }


def _is_reviewed_texture_warning(candidate_id: str, fidelity: dict[str, Any]) -> bool:
    return (
        tuple(fidelity["collisions"])
        == _REVIEWED_TEXTURE_COLLISION_ASSETS.get(candidate_id)
        and not fidelity["extra"]
        and not fidelity["mismatched"]
        and not fidelity["missing"]
        and fidelity["source_count"] == fidelity["output_count"] + 1
    )


def _sanitized_report_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not set(value) <= _DETAIL_FIELDS:
        raise ValueError("invalid conversion report details")
    details: dict[str, Any] = {}
    for field in ("animation_count", "mesh_count"):
        if field in value:
            details[field] = _nonnegative_report_int(value[field])
    if "skeleton_joint_counts" in value:
        counts = value["skeleton_joint_counts"]
        if not isinstance(counts, list):
            raise ValueError("invalid conversion report details")
        details["skeleton_joint_counts"] = [
            _nonnegative_report_int(count) for count in counts
        ]
    for field in ("duration", "source_fps", "time_codes_per_second"):
        if field in value:
            details[field] = _finite_report_number(value[field], positive=True)
    for field in ("sample_start", "sample_end"):
        if field in value:
            details[field] = _finite_report_number(value[field], positive=False)
    if (
        "sample_start" in details
        and "sample_end" in details
        and details["sample_end"] < details["sample_start"]
    ):
        raise ValueError("invalid conversion report details")
    if "texture_fidelity" in value:
        details["texture_fidelity"] = _sanitized_texture_fidelity(
            value["texture_fidelity"]
        )
    return details


def _sanitized_dependency_records(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("invalid conversion report dependency attestations")
    records: dict[str, dict[str, str]] = {}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256"}:
            raise ValueError("invalid conversion report dependency attestations")
        path = raw["path"]
        sha256 = raw["sha256"]
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 1024
            or "\0" in path
            or "\\" in path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            raise ValueError("invalid conversion report dependency attestations")
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != path
            or path in records
            or not isinstance(sha256, str)
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise ValueError("invalid conversion report dependency attestations")
        records[path] = {"path": path, "sha256": sha256}
    return tuple(records[path] for path in sorted(records))


def _validated_conversion_results(
    source_root: Path,
    target_root: Path,
    candidates: Iterable[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[str],
]:
    report_path = target_root / "conversion-report.json"
    try:
        report_path = ensure_safe_target_path(report_path, target_root)
        if not report_path.is_file():
            return {}, {}, []
        report_path = ensure_safe_target_path(report_path, target_root)
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except UnsafeTargetPathError as exc:
        return {}, {}, [f"could not read conversion report: {type(exc).__name__}"]
    except OSError as exc:
        return {}, {}, [f"could not read conversion report: {type(exc).__name__}"]
    except json.JSONDecodeError as exc:
        return {}, {}, [f"could not read conversion report: {exc}"]
    if not isinstance(document, list):
        return {}, {}, ["conversion report must be a list"]

    expected = {entry["id"]: entry for entry in candidates}
    accepted: dict[str, dict[str, Any]] = {}
    reported: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for raw in document:
        if not isinstance(raw, dict) or "id" not in raw:
            errors.append("conversion report contains a malformed record")
            continue
        candidate_id = str(raw["id"])
        candidate = expected.get(candidate_id)
        if candidate is None:
            errors.append(f"conversion report contains unknown candidate '{candidate_id}'")
            continue
        expected_source = (source_root / candidate["source"]).resolve().as_posix()
        expected_output = lexical_absolute(target_root / candidate["output"]).as_posix()
        if (
            raw.get("kind") != candidate["type"]
            or Path(str(raw.get("source", ""))).resolve().as_posix() != expected_source
            or lexical_absolute(str(raw.get("output", ""))).as_posix()
            != expected_output
        ):
            errors.append(f"conversion report paths do not match candidate '{candidate_id}'")
            continue
        try:
            details = _sanitized_report_details(raw.get("details", {}))
        except ValueError:
            errors.append(
                f"conversion report details are invalid for candidate '{candidate_id}'"
            )
            continue
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            errors.append(
                f"conversion report enabled flag is invalid for candidate '{candidate_id}'"
            )
            continue
        trusted_record = {
            "details": details,
            "enabled": enabled,
            "id": candidate_id,
            "kind": candidate["type"],
            "output": expected_output,
            "source": expected_source,
        }
        fidelity = details.get("texture_fidelity")
        reviewed_texture_warning = bool(
            fidelity is not None
            and _is_reviewed_texture_warning(candidate_id, fidelity)
        )
        if reviewed_texture_warning:
            reported[candidate_id] = trusted_record
        if not enabled and not reviewed_texture_warning:
            reported[candidate_id] = trusted_record
            continue
        try:
            output = ensure_safe_target_path(expected_output, target_root)
            if not output.is_file():
                errors.append(
                    f"validated output is missing for candidate '{candidate_id}'"
                )
                continue
            output = ensure_safe_target_path(output, target_root)
            output_size = output.stat().st_size
        except UnsafeTargetPathError:
            errors.append(f"unsafe target path for candidate '{candidate_id}'")
            continue
        if output_size == 0:
            errors.append(f"validated output is missing for candidate '{candidate_id}'")
            continue
        if fidelity is None:
            errors.append(
                "conversion report texture fidelity is missing "
                f"for candidate '{candidate_id}'"
            )
            reported[candidate_id] = trusted_record
            continue
        if not fidelity["enabled"] and not reviewed_texture_warning:
            errors.append(
                f"conversion report details are invalid for candidate '{candidate_id}'"
            )
            reported[candidate_id] = trusted_record
            continue
        try:
            source_dependencies = _sanitized_dependency_records(
                raw.get("source_dependencies")
            )
            output_dependencies = _sanitized_dependency_records(
                raw.get("output_dependencies")
            )
        except ValueError:
            errors.append(
                "conversion report dependency attestations are missing or invalid "
                f"for candidate '{candidate_id}'"
            )
            continue
        source_sha256 = raw.get("source_sha256")
        output_sha256 = raw.get("output_sha256")
        if (
            not isinstance(source_sha256, str)
            or _SHA256_PATTERN.fullmatch(source_sha256) is None
            or not isinstance(output_sha256, str)
            or _SHA256_PATTERN.fullmatch(output_sha256) is None
        ):
            errors.append(
                "conversion report hashes are missing or invalid "
                f"for candidate '{candidate_id}'"
            )
            continue
        source = source_root / candidate["source"]
        try:
            actual_source_sha256 = _sha256(source)
        except OSError as exc:
            errors.append(
                "could not hash conversion source "
                f"for candidate '{candidate_id}': {type(exc).__name__}"
            )
            continue
        if actual_source_sha256 != source_sha256:
            errors.append(
                f"conversion source hash does not match candidate '{candidate_id}'"
            )
            continue
        try:
            actual_source_dependencies = gltf_dependency_records(
                source,
                source_root=source_root,
            )
        except Exception:
            actual_source_dependencies = None
        if actual_source_dependencies != source_dependencies:
            errors.append(
                f"conversion source dependencies do not match candidate '{candidate_id}'"
            )
            continue
        try:
            output = ensure_safe_target_path(output, target_root)
            actual_output_sha256 = _sha256(output)
        except UnsafeTargetPathError:
            errors.append(f"unsafe target path for candidate '{candidate_id}'")
            continue
        except OSError as exc:
            errors.append(
                "could not hash conversion output "
                f"for candidate '{candidate_id}': {type(exc).__name__}"
            )
            continue
        if actual_output_sha256 != output_sha256:
            errors.append(
                f"conversion output hash does not match candidate '{candidate_id}'"
            )
            continue
        dependency_paths = (
            tuple(record["path"] for record in output_dependencies)
            if candidate["type"] in {"synbody_motion", "rpm_motion"}
            else None
        )
        try:
            actual_output_dependencies = output_dependency_records(
                candidate["type"],
                output,
                target_root=target_root,
                dependency_paths=dependency_paths,
            )
        except Exception:
            actual_output_dependencies = None
        if actual_output_dependencies != output_dependencies:
            errors.append(
                f"conversion output dependencies do not match candidate '{candidate_id}'"
            )
            continue
        trusted_record.update(
            {
                "output_dependencies": output_dependencies,
                "output_sha256": output_sha256,
                "source_dependencies": source_dependencies,
                "source_sha256": source_sha256,
            }
        )
        accepted[candidate_id] = trusted_record
        reported[candidate_id] = trusted_record
    return accepted, reported, sorted(errors)


def _converted_motion_records(
    candidates: Iterable[dict[str, Any]],
    validated: dict[str, dict[str, Any]],
    *,
    source_root: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    looped = _LOOPED_MOTIONS
    for candidate in candidates:
        if candidate["type"] not in {"synbody_motion", "rpm_motion", "rpm_character_motion"}:
            continue
        result = validated.get(candidate["id"])
        if result is None:
            continue
        details = result.get("details", {})
        try:
            duration = float(details["duration"])
            sample_start = float(details["sample_start"])
            sample_end = float(details["sample_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not all(math.isfinite(value) for value in (duration, sample_start, sample_end))
            or duration <= 0.0
            or sample_start < 0.0
            or sample_end < sample_start
        ):
            continue
        motion_id = candidate["id"]
        selected_range = _MOTION_SAMPLE_RANGES.get(motion_id)
        if selected_range is not None:
            time_codes_per_second = details.get("time_codes_per_second")
            try:
                time_codes_per_second = float(time_codes_per_second)
            except (TypeError, ValueError):
                continue
            selected_start, selected_end = selected_range
            if (
                not math.isfinite(time_codes_per_second)
                or time_codes_per_second <= 0.0
                or selected_start < sample_start
                or selected_end > sample_end
            ):
                continue
            sample_start = selected_start
            sample_end = selected_end
            duration = (sample_end - sample_start) / time_codes_per_second
        semantic = _MOTION_SEMANTICS.get(motion_id, "unknown")
        tags = list(
            _MOTION_TAGS.get(
                motion_id,
                sorted(
                    {
                        semantic,
                        "gltf" if candidate["type"] == "synbody_motion" else "rpm",
                    }
                ),
            )
        )
        source = source_root / candidate["source"]
        record: dict[str, Any] = {
            "content_sha256": _sha256(source),
            "duration": duration,
            "enabled": True,
            "id": motion_id,
            "label": _MOTION_LABELS.get(
                motion_id, motion_id.replace("_", " ").replace("-", " ").title()
            ),
            "loop": motion_id in looped,
            "path_policy": _MOTION_PATH_POLICIES.get(
                motion_id,
                "continue"
                if semantic in {"idle", "locomotion"} or candidate["type"] == "rpm_motion"
                else "pause",
            ),
            "redistribution_status": "review_required",
            "resume_policy": "resume_phase",
            "root_motion": "in_place",
            "sample_end": sample_end,
            "sample_start": sample_start,
            "semantic": semantic,
            "source": {
                "license": "review_required",
                "managed_by": "urban_sim_migration",
                "project": "urban-sim",
                "source_path": candidate["source"],
            },
            "source_profile": candidate["profile"],
            "tags": tags,
            "usd_path": candidate["output"],
            "variant": _MOTION_VARIANTS.get(motion_id, "default"),
        }
        if motion_id in _MOTION_FACING_YAW_OFFSETS:
            record["facing_yaw_offset"] = _MOTION_FACING_YAW_OFFSETS[motion_id]
        if candidate["type"] == "synbody_motion":
            record["source_fps"] = 30.0
        elif "source_fps" in details:
            try:
                source_fps = float(details["source_fps"])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(source_fps) or source_fps <= 0.0:
                continue
            record["source_fps"] = source_fps
        records.append(
            record
        )
    return records


def _converted_asset_record(
    candidate: dict[str, Any],
    *,
    motion_ids: set[str],
) -> dict[str, Any]:
    kind = candidate["type"]
    activity_type = candidate.get("activity_type") or "pedestrian"
    articulated = kind != "rigid_activity"
    motions: list[str] = []
    if kind in {"synbody_character", "rpm_character", "rpm_character_motion"}:
        # Every articulated asset advertises every enabled standard motion:
        # cross-profile playback is handled by the retarget cache aliases.
        motions = [
            motion_id
            for motion_id in _UNIVERSAL_MOTION_ORDER
            if motion_id in motion_ids
        ]

    speed_by_activity = {
        "cyclist": 3.0,
        "scooter_rider": 3.0,
        "skateboarder": 2.0,
        "static_biker": 0.0,
        "wheelchair": 1.2,
    }
    scale = (
        [0.01, 0.01, 0.01]
        if activity_type == "static_biker"
        else [1.0, 1.0, 1.0]
    )
    return {
        "activity_type": activity_type,
        "animation_profile": candidate["profile"],
        "articulated": articulated,
        "can_play_actions": bool(motions),
        "default_speed": speed_by_activity.get(activity_type, 1.2),
        "duplicate_of": None,
        "enabled": True,
        "ground_offset": 0.0,
        "content_up_axis": "Y",
        "id": candidate["id"],
        "label": candidate["id"].replace("-", " ").title(),
        "motions": motions,
        "path_following": activity_type != "static_biker",
        "redistribution_status": "review_required",
        "scale": scale,
        "skeleton_signature": (
            _SKELETON_SIGNATURES[candidate["profile"]] if articulated else None
        ),
        "source": {
            "license": "review_required",
            "managed_by": "urban_sim_migration",
            "project": "urban-sim",
            "source_path": candidate["source"],
        },
        "turning_speed": math.tau,
        "usd_path": candidate["output"],
        "validation": "converted_usd_validated",
        "yaw_offset": (
            _CONVERTED_ARTICULATED_ASSET_YAW
            if articulated
            else (
                0.0
                if activity_type == "static_biker"
                else _CONVERTED_RIGID_ASSET_YAW
            )
        ),
    }


def _disabled_asset_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    duplicate_of: dict[str, str] | None = None,
    rejection_reasons: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    duplicate_of = duplicate_of or {}
    rejection_reasons = rejection_reasons or {}
    records = []
    for candidate in candidates:
        if candidate["type"] not in {
            "synbody_character",
            "rpm_character",
            "rpm_character_motion",
            "rigid_activity",
        }:
            continue
        activity_type = candidate.get("activity_type", "pedestrian")
        articulated = candidate["type"] != "rigid_activity"
        records.append(
            {
                "activity_type": activity_type,
                "animation_profile": candidate["profile"],
                "articulated": articulated,
                "can_play_actions": False,
                "default_speed": 1.0,
                "duplicate_of": duplicate_of.get(candidate["id"]),
                "enabled": False,
                "ground_offset": 0.0,
                "content_up_axis": "Y",
                "id": candidate["id"],
                "label": candidate["id"].replace("-", " ").title(),
                "motions": [],
                "path_following": True,
                "redistribution_status": "review_required",
                "scale": [1.0, 1.0, 1.0],
                "skeleton_signature": (
                    _SKELETON_SIGNATURES[candidate["profile"]] if articulated else None
                ),
                "source": {
                    "license": "review_required",
                    "managed_by": "urban_sim_migration",
                    "project": "urban-sim",
                    "source_path": candidate["source"],
                },
                "turning_speed": math.tau,
                "usd_path": candidate["output"],
                "validation": rejection_reasons.get(
                    candidate["id"], "conversion_required"
                ),
                "yaw_offset": 0.0,
            }
        )
    return records


def migrate_human_activity_assets(
    source_root: str | Path,
    target_root: str | Path,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    source_root = ensure_safe_source_path(source_root, source_root)
    target_root = lexical_absolute(target_root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"urban-sim source root does not exist: {source_root}")
    ensure_safe_target_path(target_root, target_root)
    if not dry_run:
        ensure_target_directory(target_root, target_root)

    manifest_path = ensure_safe_target_path(target_root / "manifest.json", target_root)
    audit_path = ensure_safe_target_path(target_root / "audit-summary.json", target_root)

    assets, accepted_assets, copy_rejected = _copy_ready_synbody(
        source_root, target_root, dry_run=dry_run
    )
    motions, accepted_motions, motion_rejected = _copy_ready_motions(
        source_root, target_root, dry_run=dry_run
    )
    candidates, rejected = _discover_candidates(source_root, target_root)
    rejected = sorted(
        [*rejected, *copy_rejected, *motion_rejected],
        key=lambda entry: entry["source"],
    )
    validated, reported, report_errors = _validated_conversion_results(
        source_root, target_root, candidates
    )
    technically_validated = dict(validated)
    rejection_reasons: dict[str, str] = {}
    texture_fidelity: list[dict[str, Any]] = []
    fidelity_ids: set[str] = set()
    for candidate in candidates:
        report_record = reported.get(candidate["id"])
        if report_record is None:
            continue
        fidelity = report_record["details"].get("texture_fidelity")
        if fidelity is None:
            continue
        fidelity_ids.add(candidate["id"])
        texture_fidelity.append({"id": candidate["id"], **fidelity})
        reviewed_warning = _is_reviewed_texture_warning(candidate["id"], fidelity)
        if reviewed_warning and candidate["id"] not in validated:
            rejection_reasons[candidate["id"]] = str(fidelity["reason"])
        elif not fidelity["enabled"] and not reviewed_warning:
            validated.pop(candidate["id"], None)
            technically_validated[candidate["id"]] = report_record
            rejection_reasons[candidate["id"]] = str(fidelity["reason"])

    wheelchair = next(
        (candidate for candidate in candidates if candidate["id"] == "wheelchair-rider"),
        None,
    )
    if (
        wheelchair is not None
        and wheelchair["id"] not in fidelity_ids
        and (wheelchair["id"] in validated or wheelchair["id"] in reported)
    ):
        try:
            fidelity = validate_texture_fidelity(
                gltf_image_inventory(
                    source_root / wheelchair["source"],
                    source_root=source_root,
                ),
                output_image_inventory(
                    (target_root / wheelchair["output"]).parent,
                    target_root=target_root,
                ),
            )
        except Exception as exc:
            fidelity = {
                "collisions": [],
                "enabled": False,
                "extra": [],
                "mismatched": [],
                "missing": [],
                "output_count": 0,
                "reason": f"texture fidelity validation failed: {type(exc).__name__}",
                "source_count": 0,
            }
        texture_fidelity.append({"id": wheelchair["id"], **fidelity})
        if not fidelity["enabled"] and not _is_reviewed_texture_warning(
            wheelchair["id"], fidelity
        ):
            validated.pop(wheelchair["id"], None)
            if wheelchair["id"] in validated:
                del validated[wheelchair["id"]]
            rejection_reasons[wheelchair["id"]] = str(fidelity["reason"])
        elif wheelchair["id"] in reported and wheelchair["id"] not in validated:
            validated[wheelchair["id"]] = reported[wheelchair["id"]]
            technically_validated[wheelchair["id"]] = reported[wheelchair["id"]]
    converted_motions = _converted_motion_records(
        candidates, validated, source_root=source_root
    )
    motions.extend(converted_motions)
    motion_ids = {record["id"] for record in motions}
    enabled_motion_ids = {
        record["id"]
        for record in motions
        if record["enabled"]
    }
    for asset in assets:
        asset["motions"] = [
            motion_id
            for motion_id in _UNIVERSAL_MOTION_ORDER
            if motion_id in enabled_motion_ids
        ]
        asset["can_play_actions"] = bool(asset["motions"])

    ready_ids = {asset["id"] for asset in assets}
    duplicate_of: dict[str, str] = {}
    for candidate in candidates:
        if candidate["type"] != "synbody_character":
            continue
        canonical_id = candidate["id"].replace("synbody-gltf-", "synbody-", 1)
        if canonical_id in ready_ids:
            duplicate_of[candidate["id"]] = canonical_id
            rejection_reasons[candidate["id"]] = f"duplicate of ready asset {canonical_id}"

    enabled_candidate_ids: set[str] = set()
    for candidate in candidates:
        if candidate["id"] not in validated or candidate["id"] in duplicate_of:
            continue
        if candidate["type"] in {
            "synbody_character",
            "rpm_character",
            "rpm_character_motion",
            "rigid_activity",
        }:
            record = _converted_asset_record(candidate, motion_ids=motion_ids)
            fidelity = validated[candidate["id"]]["details"].get(
                "texture_fidelity"
            )
            if fidelity is not None and _is_reviewed_texture_warning(
                candidate["id"], fidelity
            ):
                record["validation"] = str(fidelity["reason"])
            if candidate["type"] == "synbody_character":
                record["motions"] = [
                    motion_id
                    for motion_id in _UNIVERSAL_MOTION_ORDER
                    if motion_id in enabled_motion_ids
                ]
                record["can_play_actions"] = bool(record["motions"])
            assets.append(record)
            enabled_candidate_ids.add(candidate["id"])
        elif candidate["type"] in {"synbody_motion", "rpm_motion"}:
            if candidate["id"] in motion_ids:
                enabled_candidate_ids.add(candidate["id"])

    disabled = [
        candidate for candidate in candidates if candidate["id"] not in enabled_candidate_ids
    ]
    assets.extend(
        _disabled_asset_candidates(
            disabled,
            duplicate_of=duplicate_of,
            rejection_reasons=rejection_reasons,
        )
    )
    assets.sort(key=lambda record: record["id"])
    motions.sort(key=lambda record: record["id"])

    audited_candidates = []
    for candidate in candidates:
        entry = dict(candidate)
        if candidate["id"] in enabled_candidate_ids:
            entry["status"] = "accepted"
            entry["details"] = validated[candidate["id"]].get("details", {})
        else:
            entry["status"] = "disabled"
            if candidate["id"] in rejection_reasons:
                entry["reason"] = rejection_reasons[candidate["id"]]
            elif candidate["id"] in technically_validated:
                entry["reason"] = (
                    "required motion duration or sample range metadata is missing or invalid"
                )
            else:
                entry["reason"] = "not technically enabled by conversion report"
        entry["technically_validated"] = candidate["id"] in technically_validated
        audited_candidates.append(entry)

    if not dry_run:
        _write_json(
            manifest_path,
            {"assets": assets, "motions": motions, "version": 2},
            target_root=target_root,
        )
        accepted = sorted(
            accepted_assets
            + accepted_motions
            + [
                {
                    "id": candidate["id"],
                    "source": candidate["source"],
                    "status": "accepted",
                    "target": candidate["output"],
                }
                for candidate in candidates
                if candidate["id"] in enabled_candidate_ids
            ],
            key=lambda entry: (entry["id"], entry["source"]),
        )
        disabled_audit = sorted(
            [
                {
                    "id": entry["id"],
                    "reason": entry["reason"],
                    "source": entry["source"],
                    "status": "disabled",
                    "target": entry["output"],
                    "technically_validated": entry["technically_validated"],
                }
                for entry in audited_candidates
                if entry["status"] == "disabled"
            ],
            key=lambda entry: (entry["id"], entry["source"]),
        )
        duplicates = [
            {
                "canonical_id": canonical_id,
                "id": candidate_id,
                "reason": "ready USD asset is canonical",
            }
            for candidate_id, canonical_id in sorted(duplicate_of.items())
        ]
        enabled_assets = sum(bool(record["enabled"]) for record in assets)
        enabled_motions = sum(bool(record["enabled"]) for record in motions)
        _write_json(
            audit_path,
            {
                "accepted": accepted,
                "candidates": audited_candidates,
                "conversion_report_errors": report_errors,
                "counts": {
                    "accepted": len(accepted),
                    "asset_records": len(assets),
                    "candidate_records": len(candidates),
                    "canonical_unique_assets": len(assets) - len(duplicate_of),
                    "disabled": len(disabled_audit),
                    "disabled_assets": len(assets) - enabled_assets,
                    "enabled_assets": enabled_assets,
                    "enabled_motions": enabled_motions,
                    "motion_records": len(motions),
                    "ready_assets": len(accepted_assets),
                    "ready_motions": len(accepted_motions),
                    "rejected": len(rejected),
                    "technically_validated_candidates": len(technically_validated),
                },
                "disabled": disabled_audit,
                "duplicates": duplicates,
                "provenance": {
                    "conversion_report": (
                        f"{_TARGET_PROVENANCE_ROOT}/conversion-report.json"
                    ),
                    "generator": "tools/human_assets/migrate_assets.py",
                    "source_root": _SOURCE_PROVENANCE_ROOT,
                    "target_root": _TARGET_PROVENANCE_ROOT,
                },
                "rejected": rejected,
                "schema": "eai-human-asset-audit",
                "source_root": _SOURCE_PROVENANCE_ROOT,
                "target_root": _TARGET_PROVENANCE_ROOT,
                "texture_fidelity": texture_fidelity,
                "version": 2,
            },
            target_root=target_root,
        )

    return MigrationResult(
        ready_asset_count=len(accepted_assets),
        ready_motion_count=len(accepted_motions),
        candidate_count=len(candidates),
        rejected_count=len(rejected),
        converted_count=len(enabled_candidate_ids),
        manifest_path=manifest_path,
        audit_path=audit_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = migrate_human_activity_assets(
        args.source_root,
        args.target_root,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "audit": str(result.audit_path),
                "candidates": result.candidate_count,
                "converted": result.converted_count,
                "manifest": str(result.manifest_path),
                "ready_assets": result.ready_asset_count,
                "ready_motions": result.ready_motion_count,
                "rejected": result.rejected_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
