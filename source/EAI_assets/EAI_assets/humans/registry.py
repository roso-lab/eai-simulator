# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Manifest-backed human and human-activity asset registry."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .profiles import COMPATIBLE_MOTION_PROFILES


_ACTIVITY_TYPES = frozenset(
    {
        "pedestrian",
        "cyclist",
        "scooter_rider",
        "skateboarder",
        "wheelchair",
        "static_biker",
    }
)
_ROOT_MOTION_VALUES = frozenset({"in_place", "authored", "none"})
_PATH_POLICY_VALUES = frozenset({"continue", "pause"})
_RESUME_POLICY_VALUES = frozenset({"resume_phase", "restart_phase", "hold_final"})
_REDISTRIBUTION_VALUES = frozenset({"review_required", "allowed", "prohibited"})
_FILE_POLICIES = frozenset({"metadata", "require"})
_CONTENT_UP_AXIS_VALUES = frozenset({"Y", "Z"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_TOP_LEVEL_FIELDS = frozenset({"version", "assets", "motions"})
_MOTION_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "label",
        "usd_path",
        "source_profile",
        "duration",
        "loop",
        "enabled",
        "semantic",
        "variant",
        "root_motion",
        "path_policy",
        "resume_policy",
        "content_sha256",
        "redistribution_status",
        "source",
    }
)
_MOTION_OPTIONAL_FIELDS = frozenset(
    {
        "source_fps",
        "sample_start",
        "sample_end",
        "tags",
        "blend_in",
        "blend_out",
        "retargeter_version",
        "direction",
        "intended_speed",
        "in_place",
        "facing_yaw_offset",
    }
)
_ASSET_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "label",
        "activity_type",
        "usd_path",
        "enabled",
        "validation",
        "scale",
        "yaw_offset",
        "ground_offset",
        "content_up_axis",
        "animation_profile",
        "articulated",
        "can_play_actions",
        "path_following",
        "motions",
        "default_speed",
        "turning_speed",
        "skeleton_signature",
        "duplicate_of",
        "redistribution_status",
        "source",
    }
)


class HumanAssetManifestError(ValueError):
    """Raised when the human asset manifest is invalid."""


class HumanAssetCapabilityError(ValueError):
    """Raised when an asset does not support a requested capability."""


@dataclass(frozen=True)
class HumanMotionSpec:
    id: str
    label: str
    usd_path: Path
    source_profile: str
    duration: float
    loop: bool
    source: Mapping[str, Any]
    semantic: str = "unspecified"
    variant: str = "default"
    root_motion: str = "in_place"
    path_policy: str = "continue"
    resume_policy: str = "resume_phase"
    content_sha256: str = "0" * 64
    redistribution_status: str = "review_required"
    origin: str = "canonical"
    source_fps: float | None = None
    sample_start: float | None = None
    sample_end: float | None = None
    tags: tuple[str, ...] = ()
    blend_in: float | None = None
    blend_out: float | None = None
    retargeter_version: str | None = None
    direction: str | None = None
    intended_speed: float | None = None
    in_place: bool | None = None
    facing_yaw_offset: float = 0.0


@dataclass(frozen=True)
class HumanAssetSpec:
    id: str
    label: str
    activity_type: str
    usd_path: Path
    validation: str
    scale: tuple[float, float, float]
    yaw_offset: float
    ground_offset: float
    content_up_axis: str
    animation_profile: str
    articulated: bool
    can_play_actions: bool
    path_following: bool
    motions: tuple[str, ...]
    default_speed: float
    turning_speed: float
    source: Mapping[str, Any]
    skeleton_signature: str | None = None
    duplicate_of: str | None = None
    redistribution_status: str = "review_required"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _source_mapping(value: Any, *, record_id: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HumanAssetManifestError(f"source for '{record_id}' must be an object")
    return _freeze_json(value)


def _read_document(path: Path, *, kind: str) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HumanAssetManifestError(f"could not read human {kind} manifest: {exc}") from exc
    if not isinstance(document, Mapping):
        raise HumanAssetManifestError(f"human {kind} manifest must be an object")
    return document


def _validate_fields(
    record: Any,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    kind: str,
) -> Mapping[str, Any]:
    if not isinstance(record, Mapping):
        raise HumanAssetManifestError(f"{kind} record must be an object")
    missing = sorted(required.difference(record))
    if missing:
        raise HumanAssetManifestError(
            f"{kind} record is missing required field '{missing[0]}'"
        )
    unknown = sorted(set(record).difference(allowed))
    if unknown:
        raise HumanAssetManifestError(f"{kind} record has unknown field '{unknown[0]}'")
    return record


def _nonempty_string(value: Any, *, field: str, record_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise HumanAssetManifestError(
            f"field '{field}' for '{record_id}' must be a nonempty string"
        )
    return value


def _nullable_string(value: Any, *, field: str, record_id: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field=field, record_id=record_id)


def _boolean(value: Any, *, field: str, record_id: str) -> bool:
    if not isinstance(value, bool):
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' must be boolean")
    return value


def _finite_float(
    value: Any,
    *,
    field: str,
    record_id: str,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' has an invalid value")
    if minimum is not None and result < minimum:
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' has an invalid value")
    if exclusive_minimum is not None and result <= exclusive_minimum:
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' has an invalid value")
    return result


def _optional_float(
    record: Mapping[str, Any],
    field: str,
    *,
    record_id: str,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float | None:
    if field not in record:
        return None
    return _finite_float(
        record[field],
        field=field,
        record_id=record_id,
        minimum=minimum,
        exclusive_minimum=exclusive_minimum,
    )


def _enum_string(
    value: Any,
    *,
    field: str,
    record_id: str,
    allowed: frozenset[str],
) -> str:
    result = _nonempty_string(value, field=field, record_id=record_id)
    if result not in allowed:
        raise HumanAssetManifestError(
            f"field '{field}' for '{record_id}' must be one of {sorted(allowed)}"
        )
    return result


def _string_array(value: Any, *, field: str, record_id: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HumanAssetManifestError(f"field '{field}' for '{record_id}' must be an array")
    result = tuple(
        _nonempty_string(item, field=field, record_id=record_id) for item in value
    )
    if len(set(result)) != len(result):
        raise HumanAssetManifestError(
            f"field '{field}' for '{record_id}' contains duplicate values"
        )
    return result


def _safe_asset_path(root: Path, value: Any, *, record_id: str) -> Path:
    raw_path = _nonempty_string(value, field="usd_path", record_id=record_id)
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise HumanAssetManifestError(
            f"asset path for '{record_id}' must stay inside the human root"
        )
    root = root.resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HumanAssetManifestError(
            f"asset path for '{record_id}' must stay inside the human root"
        ) from exc
    return candidate


def _require_file(path: Path, *, kind: str, record_id: str, file_policy: str) -> None:
    if file_policy == "require" and not path.is_file():
        raise HumanAssetManifestError(
            f"enabled {kind} '{record_id}' file does not exist: {path}"
        )


def _validate_v2_document(document: Mapping[str, Any], *, kind: str) -> None:
    missing = sorted(_TOP_LEVEL_FIELDS.difference(document))
    if missing:
        raise HumanAssetManifestError(
            f"human {kind} manifest is missing required field '{missing[0]}'"
        )
    if type(document["version"]) is not int or document["version"] != 2:
        raise HumanAssetManifestError(f"human {kind} manifest version must be 2")
    unknown = sorted(set(document).difference(_TOP_LEVEL_FIELDS))
    if unknown:
        raise HumanAssetManifestError(
            f"human {kind} manifest has unknown field '{unknown[0]}'"
        )
    if not isinstance(document["assets"], list):
        raise HumanAssetManifestError(f"human {kind} manifest assets must be an array")
    if not isinstance(document["motions"], list):
        raise HumanAssetManifestError(f"human {kind} manifest motions must be an array")


def _motion_spec(
    raw: Any,
    *,
    root: Path,
    origin: str,
) -> tuple[str, bool, HumanMotionSpec | None]:
    record = _validate_fields(
        raw,
        required=_MOTION_REQUIRED_FIELDS,
        allowed=_MOTION_REQUIRED_FIELDS | _MOTION_OPTIONAL_FIELDS,
        kind="motion",
    )
    motion_id = _nonempty_string(record["id"], field="id", record_id="motion")
    label = _nonempty_string(record["label"], field="label", record_id=motion_id)
    usd_path = _safe_asset_path(root, record["usd_path"], record_id=motion_id)
    source_profile = _nonempty_string(
        record["source_profile"], field="source_profile", record_id=motion_id
    )
    duration = _finite_float(
        record["duration"], field="duration", record_id=motion_id, minimum=0.0
    )
    loop = _boolean(record["loop"], field="loop", record_id=motion_id)
    enabled = _boolean(record["enabled"], field="enabled", record_id=motion_id)
    semantic = _nonempty_string(record["semantic"], field="semantic", record_id=motion_id)
    variant = _nonempty_string(record["variant"], field="variant", record_id=motion_id)
    root_motion = _enum_string(
        record["root_motion"],
        field="root_motion",
        record_id=motion_id,
        allowed=_ROOT_MOTION_VALUES,
    )
    path_policy = _enum_string(
        record["path_policy"],
        field="path_policy",
        record_id=motion_id,
        allowed=_PATH_POLICY_VALUES,
    )
    resume_policy = _enum_string(
        record["resume_policy"],
        field="resume_policy",
        record_id=motion_id,
        allowed=_RESUME_POLICY_VALUES,
    )
    content_sha256 = _nonempty_string(
        record["content_sha256"], field="content_sha256", record_id=motion_id
    )
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise HumanAssetManifestError(
            f"field 'content_sha256' for '{motion_id}' must match ^[0-9a-f]{{64}}$"
        )
    redistribution_status = _enum_string(
        record["redistribution_status"],
        field="redistribution_status",
        record_id=motion_id,
        allowed=_REDISTRIBUTION_VALUES,
    )
    source = _source_mapping(record["source"], record_id=motion_id)
    source_fps = _optional_float(
        record,
        "source_fps",
        record_id=motion_id,
        exclusive_minimum=0.0,
    )
    sample_start = _optional_float(
        record, "sample_start", record_id=motion_id, minimum=0.0
    )
    sample_end = _optional_float(
        record, "sample_end", record_id=motion_id, minimum=0.0
    )
    if sample_start is not None and sample_end is not None and sample_end < sample_start:
        raise HumanAssetManifestError(
            f"field 'sample_end' for '{motion_id}' must be greater than or equal to sample_start"
        )
    tags = _string_array(record.get("tags", []), field="tags", record_id=motion_id)
    blend_in = _optional_float(record, "blend_in", record_id=motion_id, minimum=0.0)
    blend_out = _optional_float(record, "blend_out", record_id=motion_id, minimum=0.0)
    retargeter_version = (
        _nonempty_string(
            record["retargeter_version"],
            field="retargeter_version",
            record_id=motion_id,
        )
        if "retargeter_version" in record
        else None
    )
    direction = (
        _nonempty_string(record["direction"], field="direction", record_id=motion_id)
        if "direction" in record
        else None
    )
    intended_speed = _optional_float(
        record, "intended_speed", record_id=motion_id, minimum=0.0
    )
    in_place = (
        _boolean(record["in_place"], field="in_place", record_id=motion_id)
        if "in_place" in record
        else None
    )
    facing_yaw_offset = (
        _finite_float(
            record["facing_yaw_offset"],
            field="facing_yaw_offset",
            record_id=motion_id,
        )
        if "facing_yaw_offset" in record
        else 0.0
    )
    if not enabled:
        return motion_id, False, None
    return motion_id, True, HumanMotionSpec(
        id=motion_id,
        label=label,
        usd_path=usd_path,
        source_profile=source_profile,
        duration=duration,
        loop=loop,
        source=source,
        semantic=semantic,
        variant=variant,
        root_motion=root_motion,
        path_policy=path_policy,
        resume_policy=resume_policy,
        content_sha256=content_sha256,
        redistribution_status=redistribution_status,
        origin=origin,
        source_fps=source_fps,
        sample_start=sample_start,
        sample_end=sample_end,
        tags=tags,
        blend_in=blend_in,
        blend_out=blend_out,
        retargeter_version=retargeter_version,
        direction=direction,
        intended_speed=intended_speed,
        in_place=in_place,
        facing_yaw_offset=facing_yaw_offset,
    )


def _asset_spec(
    raw: Any,
    *,
    root: Path,
    canonical_motion_ids: set[str],
    motions: Mapping[str, HumanMotionSpec],
) -> tuple[str, bool, HumanAssetSpec | None]:
    record = _validate_fields(
        raw,
        required=_ASSET_REQUIRED_FIELDS,
        allowed=_ASSET_REQUIRED_FIELDS,
        kind="asset",
    )
    asset_id = _nonempty_string(record["id"], field="id", record_id="asset")
    label = _nonempty_string(record["label"], field="label", record_id=asset_id)
    activity_type = _enum_string(
        record["activity_type"],
        field="activity_type",
        record_id=asset_id,
        allowed=_ACTIVITY_TYPES,
    )
    usd_path = _safe_asset_path(root, record["usd_path"], record_id=asset_id)
    enabled = _boolean(record["enabled"], field="enabled", record_id=asset_id)
    validation = _nonempty_string(
        record["validation"], field="validation", record_id=asset_id
    )
    raw_scale = record["scale"]
    if not isinstance(raw_scale, list) or len(raw_scale) != 3:
        raise HumanAssetManifestError(f"scale for '{asset_id}' must contain three values")
    scale = tuple(
        _finite_float(
            value,
            field="scale",
            record_id=asset_id,
            exclusive_minimum=0.0,
        )
        for value in raw_scale
    )
    yaw_offset = _finite_float(record["yaw_offset"], field="yaw_offset", record_id=asset_id)
    ground_offset = _finite_float(
        record["ground_offset"], field="ground_offset", record_id=asset_id
    )
    content_up_axis = _enum_string(
        record["content_up_axis"],
        field="content_up_axis",
        record_id=asset_id,
        allowed=_CONTENT_UP_AXIS_VALUES,
    )
    animation_profile = _nonempty_string(
        record["animation_profile"], field="animation_profile", record_id=asset_id
    )
    articulated = _boolean(record["articulated"], field="articulated", record_id=asset_id)
    can_play_actions = _boolean(
        record["can_play_actions"], field="can_play_actions", record_id=asset_id
    )
    path_following = _boolean(
        record["path_following"], field="path_following", record_id=asset_id
    )
    advertised_motions = _string_array(
        record["motions"], field="motions", record_id=asset_id
    )
    default_speed = _finite_float(
        record["default_speed"], field="default_speed", record_id=asset_id, minimum=0.0
    )
    turning_speed = _finite_float(
        record["turning_speed"], field="turning_speed", record_id=asset_id, minimum=0.0
    )
    skeleton_signature = _nullable_string(
        record["skeleton_signature"], field="skeleton_signature", record_id=asset_id
    )
    duplicate_of = _nullable_string(
        record["duplicate_of"], field="duplicate_of", record_id=asset_id
    )
    redistribution_status = _enum_string(
        record["redistribution_status"],
        field="redistribution_status",
        record_id=asset_id,
        allowed=_REDISTRIBUTION_VALUES,
    )
    source = _source_mapping(record["source"], record_id=asset_id)

    if articulated and skeleton_signature is None:
        raise HumanAssetManifestError(
            f"articulated asset '{asset_id}' requires skeleton_signature"
        )
    if not articulated and skeleton_signature is not None:
        raise HumanAssetManifestError(
            f"non-articulated asset '{asset_id}' must use null skeleton_signature"
        )
    if can_play_actions and not articulated:
        raise HumanAssetManifestError(
            f"rigid asset '{asset_id}' cannot advertise action playback"
        )
    if advertised_motions and not can_play_actions:
        raise HumanAssetManifestError(
            f"asset '{asset_id}' cannot advertise motions without action playback"
        )
    missing_canonical_motions = [
        motion_id for motion_id in advertised_motions if motion_id not in canonical_motion_ids
    ]
    if missing_canonical_motions:
        raise HumanAssetManifestError(
            f"asset '{asset_id}' references unavailable motions in canonical catalog: "
            f"{missing_canonical_motions}"
        )
    missing_motions = [motion_id for motion_id in advertised_motions if motion_id not in motions]
    if enabled and missing_motions:
        raise HumanAssetManifestError(
            f"asset '{asset_id}' references unavailable motions: {missing_motions}"
    )
    if not enabled:
        return asset_id, False, None
    return asset_id, True, HumanAssetSpec(
        id=asset_id,
        label=label,
        activity_type=activity_type,
        usd_path=usd_path,
        validation=validation,
        scale=scale,
        yaw_offset=yaw_offset,
        ground_offset=ground_offset,
        content_up_axis=content_up_axis,
        animation_profile=animation_profile,
        articulated=articulated,
        can_play_actions=can_play_actions,
        path_following=path_following,
        motions=advertised_motions,
        default_speed=default_speed,
        turning_speed=turning_speed,
        source=source,
        skeleton_signature=skeleton_signature,
        duplicate_of=duplicate_of,
        redistribution_status=redistribution_status,
    )


class HumanAssetRegistry:
    """Validated, immutable view of enabled human assets and motions."""

    def __init__(
        self,
        *,
        human_root: Path,
        assets: Mapping[str, HumanAssetSpec],
        motions: Mapping[str, HumanMotionSpec],
        file_policy: str,
        catalog_version: int = 2,
    ) -> None:
        self._human_root = human_root.resolve()
        self._assets = MappingProxyType(dict(assets))
        self._motions = MappingProxyType(dict(motions))
        self._file_policy = file_policy
        self._catalog_version = catalog_version

    @property
    def human_root(self) -> Path:
        return self._human_root

    @property
    def file_policy(self) -> str:
        return self._file_policy

    @property
    def catalog_version(self) -> int:
        return self._catalog_version

    @classmethod
    def load(
        cls,
        manifest_path: str | Path,
        *,
        asset_root: str | Path | None = None,
        overlays: Iterable[str | Path] = (),
        file_policy: str = "require",
    ) -> "HumanAssetRegistry":
        if not isinstance(file_policy, str) or file_policy not in _FILE_POLICIES:
            raise HumanAssetManifestError(
                f"file_policy must be one of {sorted(_FILE_POLICIES)}"
            )
        manifest_path = Path(manifest_path).resolve()
        human_root = (
            manifest_path.parent if asset_root is None else Path(asset_root)
        ).resolve()
        document = _read_document(manifest_path, kind="asset")
        overlay_paths = tuple(Path(path).resolve() for path in overlays)
        source_version = document.get("version")
        _validate_v2_document(document, kind="asset")

        motions: dict[str, HumanMotionSpec] = {}
        canonical_motion_ids: set[str] = set()
        for raw in document["motions"]:
            motion_id, enabled, spec = _motion_spec(
                raw,
                root=human_root,
                origin="canonical",
            )
            if motion_id in canonical_motion_ids:
                raise HumanAssetManifestError(f"duplicate motion id: {motion_id}")
            canonical_motion_ids.add(motion_id)
            if enabled and spec is not None:
                motions[motion_id] = spec

        assets: dict[str, HumanAssetSpec] = {}
        asset_ids: set[str] = set()
        for raw in document["assets"]:
            asset_id, enabled, spec = _asset_spec(
                raw,
                root=human_root,
                canonical_motion_ids=canonical_motion_ids,
                motions=motions,
            )
            if asset_id in asset_ids:
                raise HumanAssetManifestError(f"duplicate asset id: {asset_id}")
            asset_ids.add(asset_id)
            if enabled and spec is not None:
                assets[asset_id] = spec

        overlay_motion_ids: set[str] = set()
        for overlay_path in overlay_paths:
            overlay = _read_document(overlay_path, kind="overlay")
            _validate_v2_document(overlay, kind="overlay")
            if overlay["assets"]:
                raise HumanAssetManifestError("overlay manifest cannot define assets")
            for raw in overlay["motions"]:
                motion_id, enabled, spec = _motion_spec(
                    raw,
                    root=human_root,
                    origin="custom",
                )
                if motion_id in canonical_motion_ids:
                    raise HumanAssetManifestError(
                        f"overlay motion '{motion_id}' cannot replace canonical motion id"
                    )
                if motion_id in overlay_motion_ids:
                    raise HumanAssetManifestError(f"duplicate motion id: {motion_id}")
                overlay_motion_ids.add(motion_id)
                if enabled and spec is not None:
                    motions[motion_id] = spec

        for motion in motions.values():
            _require_file(
                motion.usd_path,
                kind="motion",
                record_id=motion.id,
                file_policy=file_policy,
            )
        for asset in assets.values():
            _require_file(
                asset.usd_path,
                kind="asset",
                record_id=asset.id,
                file_policy=file_policy,
            )

        return cls(
            human_root=human_root,
            assets=assets,
            motions=motions,
            file_policy=file_policy,
            catalog_version=int(source_version),
        )

    @classmethod
    def load_default(cls) -> "HumanAssetRegistry":
        """Load the canonical metadata catalog and any installed custom actions."""

        from EAI_assets import asset_resolver

        manifest_path = Path(asset_resolver.asset_path("human/manifest.json"))
        human_root = Path(asset_resolver.asset_path("human"))
        custom_manifest = human_root / "custom-actions" / "manifest.json"
        overlays = (custom_manifest,) if custom_manifest.is_file() else ()
        return cls.load(
            manifest_path,
            asset_root=human_root,
            overlays=overlays,
            file_policy="metadata",
        )

    def assets(
        self,
        *,
        activity_type: str | None = None,
        articulated: bool | None = None,
        path_following: bool | None = None,
    ) -> tuple[HumanAssetSpec, ...]:
        values = sorted(self._assets.values(), key=lambda asset: asset.id)
        return tuple(
            asset
            for asset in values
            if (activity_type is None or asset.activity_type == activity_type)
            and (articulated is None or asset.articulated is articulated)
            and (path_following is None or asset.path_following is path_following)
        )

    def asset(self, asset_id: str) -> HumanAssetSpec:
        try:
            return self._assets[str(asset_id)]
        except KeyError as exc:
            raise KeyError(f"unknown or disabled human asset: {asset_id}") from exc

    def motion(self, motion_id: str) -> HumanMotionSpec:
        try:
            return self._motions[str(motion_id)]
        except KeyError as exc:
            raise KeyError(f"unknown or disabled human motion: {motion_id}") from exc

    def require_motion(self, asset_id: str, motion_id: str) -> HumanMotionSpec:
        asset = self.asset(asset_id)
        if not asset.can_play_actions:
            raise HumanAssetCapabilityError(f"human asset '{asset_id}' cannot play actions")
        if motion_id not in asset.motions:
            raise HumanAssetCapabilityError(
                f"human asset '{asset_id}' does not support motion '{motion_id}'"
            )
        return self.motion(motion_id)

    def with_motion_for_assets(
        self,
        motion_id: str,
        asset_ids: Iterable[str],
    ) -> "HumanAssetRegistry":
        """Return a new view advertising one validated custom motion on compatible assets."""

        motion = self.motion(motion_id)
        if motion.origin != "custom":
            raise HumanAssetCapabilityError(
                f"motion '{motion_id}' is canonical-origin; only overlay motions can be added"
            )
        requested = tuple(str(asset_id) for asset_id in asset_ids)
        if len(set(requested)) != len(requested):
            raise HumanAssetCapabilityError("duplicate asset id in custom motion request")

        selected: list[HumanAssetSpec] = []
        for asset_id in requested:
            asset = self.asset(asset_id)
            if asset.duplicate_of is not None:
                raise HumanAssetCapabilityError(
                    f"duplicate human asset '{asset_id}' must use canonical asset "
                    f"'{asset.duplicate_of}' for custom motions"
                )
            if not asset.articulated or not asset.can_play_actions:
                raise HumanAssetCapabilityError(
                    f"human asset '{asset_id}' cannot play actions"
                )
            allowed_profiles = COMPATIBLE_MOTION_PROFILES.get(
                asset.animation_profile, frozenset()
            )
            if motion.source_profile not in allowed_profiles:
                raise HumanAssetCapabilityError(
                    f"asset profile '{asset.animation_profile}' for '{asset_id}' is incompatible "
                    f"with motion profile '{motion.source_profile}' for '{motion_id}'"
                )
            selected.append(asset)

        assets = dict(self._assets)
        for asset in selected:
            if motion_id not in asset.motions:
                assets[asset.id] = replace(asset, motions=(*asset.motions, motion_id))
        return type(self)(
            human_root=self.human_root,
            assets=assets,
            motions=self._motions,
            file_policy=self.file_policy,
            catalog_version=self.catalog_version,
        )

    def ensure_asset(
        self,
        asset_id: str,
        motion_ids: Iterable[str] = (),
    ) -> "HumanAssetRegistry":
        """Materialize and require only one asset and its explicitly requested motions."""

        asset = self.asset(asset_id)
        requested = tuple(str(motion_id) for motion_id in motion_ids)
        if len(set(requested)) != len(requested):
            raise HumanAssetCapabilityError("duplicate motion id in asset materialization request")
        motions = tuple(self.require_motion(asset_id, motion_id) for motion_id in requested)
        paths = [str(asset.usd_path), *(str(motion.usd_path) for motion in motions)]

        missing_paths = [path for path in paths if not Path(path).is_file()]
        if missing_paths:
            from EAI_assets import asset_resolver

            resolver_root = Path(asset_resolver.asset_path("human")).resolve()
            if self.human_root != resolver_root:
                raise HumanAssetManifestError(
                    f"external asset root '{self.human_root}' cannot auto-materialize "
                    "missing files; provision them first or configure EAI_USD_ROOT"
                )
            asset_resolver.ensure_usd_assets_for_paths(missing_paths)
        for path, kind, record_id in [
            (asset.usd_path, "asset", asset.id),
            *((motion.usd_path, "motion", motion.id) for motion in motions),
        ]:
            _require_file(path, kind=kind, record_id=record_id, file_policy="require")

        materialized_asset = replace(asset, motions=requested)
        return type(self)(
            human_root=self.human_root,
            assets={asset.id: materialized_asset},
            motions={motion.id: motion for motion in motions},
            file_policy="require",
            catalog_version=self.catalog_version,
        )
