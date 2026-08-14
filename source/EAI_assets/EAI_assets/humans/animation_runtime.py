# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Actor-local human animation state independent of the global timeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .registry import (
    HumanAssetCapabilityError,
    HumanAssetRegistry,
    HumanMotionSpec,
)


class HumanMotionRetargetError(ValueError):
    """Raised when a motion cannot be mapped onto a target skeleton."""


class RetargetCacheError(ValueError):
    """Raised when a retarget cache is malformed or stale."""


_SIGNATURE_VERSION = 1
_LEGACY_RETARGET_CACHE_IDS = {
    "dance": "motion_120_04",
    "walk_and_look": "motion_15_01",
    "walk_backward": "stand_to_walk_back",
}


def resolve_retarget_cache_path(
    cache_root: str | Path,
    asset_id: str,
    motion_id: str,
) -> tuple[Path, str]:
    """Resolve a semantic motion cache, with bounded provider-ID fallback."""
    cache_dir = Path(cache_root) / str(asset_id)
    motion_id = str(motion_id)
    semantic_path = cache_dir / f"{motion_id}.json"
    if semantic_path.is_file():
        return semantic_path, motion_id

    legacy_motion_id = _LEGACY_RETARGET_CACHE_IDS.get(motion_id)
    if legacy_motion_id is not None:
        legacy_path = cache_dir / f"{legacy_motion_id}.json"
        if legacy_path.is_file():
            return legacy_path, legacy_motion_id
    return semantic_path, motion_id


def skeleton_signature(
    joints: Sequence[str],
    rest_transforms: Sequence[Any],
    bind_transforms: Sequence[Any],
    *,
    units_per_meter: float = 0.01,
    up_axis: str = "Z",
) -> str:
    """Hash a canonical JSON fingerprint of skeleton identity.

    Changing joint order, rest/bind matrices, units, or up axis produces a
    different signature, invalidating any retarget cache built for the old
    skeleton.
    """
    if len(rest_transforms) != len(joints):
        raise HumanMotionRetargetError("rest transforms must match joint count")
    if len(bind_transforms) != len(joints):
        raise HumanMotionRetargetError("bind transforms must match joint count")

    def _matrix_components(matrix: Any) -> list[float]:
        rows = matrix.GetTranspose() if hasattr(matrix, "GetTranspose") else matrix
        return [float(rows[i][j]) for i in range(4) for j in range(4)]

    canonical = {
        "version": _SIGNATURE_VERSION,
        "joints": [str(j) for j in joints],
        "rest": [_matrix_components(m) for m in rest_transforms],
        "bind": [_matrix_components(m) for m in bind_transforms],
        "units_per_meter": float(units_per_meter),
        "up_axis": str(up_axis),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class RetargetPlan:
    source_joints: tuple[str, ...]
    target_joints: tuple[str, ...]
    joint_indices: tuple[int, ...]
    target_rest_translations: tuple[tuple[float, float, float], ...]
    target_root_index: int
    source_root_index: int
    source_root_origin: tuple[float, float, float]
    source_meters_per_unit: float
    target_meters_per_unit: float
    target_root_up: tuple[float, float, float]
    retarget_mode: str = "strict"
    unmapped_target_joints: tuple[str, ...] = ()
    source_rest_rotations: (
        tuple[tuple[float, float, float, float], ...] | None
    ) = None
    target_rest_rotations: (
        tuple[tuple[float, float, float, float], ...] | None
    ) = None


@dataclass(frozen=True)
class RetargetedPose:
    rotations: tuple[Any, ...]
    translations: tuple[tuple[float, float, float], ...]
    scales: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class RetargetCacheEntry:
    asset_id: str
    motion_id: str
    source_sha256: str
    target_sha256: str
    source_animation_path: str
    target_skeleton_relative_path: str
    source_time_codes_per_second: float
    source_sample_start: float
    source_sample_end: float
    plan: RetargetPlan
    dependency_sha256: str | None = None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "asset_id": self.asset_id,
            "motion_id": self.motion_id,
            "plan": {
                "joint_indices": list(self.plan.joint_indices),
                "source_joints": list(self.plan.source_joints),
                "source_root_index": self.plan.source_root_index,
                "source_root_origin": list(self.plan.source_root_origin),
                "source_meters_per_unit": self.plan.source_meters_per_unit,
                "target_joints": list(self.plan.target_joints),
                "target_meters_per_unit": self.plan.target_meters_per_unit,
                "target_rest_translations": [
                    list(value) for value in self.plan.target_rest_translations
                ],
                "target_root_index": self.plan.target_root_index,
                "target_root_up": list(self.plan.target_root_up),
            },
            "source_animation_path": self.source_animation_path,
            "source_sample_end": self.source_sample_end,
            "source_sample_start": self.source_sample_start,
            "source_sha256": self.source_sha256,
            "source_time_codes_per_second": self.source_time_codes_per_second,
            "target_sha256": self.target_sha256,
            "target_skeleton_relative_path": self.target_skeleton_relative_path,
            "version": 2,
        }
        if self.dependency_sha256 is not None:
            document["dependency_sha256"] = self.dependency_sha256
        # Strict plans stay lean so existing cache documents remain
        # byte-identical; only aliased-lenient plans carry the extra keys.
        if self.plan.retarget_mode != "strict":
            document["plan"]["retarget_mode"] = self.plan.retarget_mode
            if self.plan.source_rest_rotations is not None:
                document["plan"]["source_rest_rotations"] = [
                    list(value) for value in self.plan.source_rest_rotations
                ]
            if self.plan.target_rest_rotations is not None:
                document["plan"]["target_rest_rotations"] = [
                    list(value) for value in self.plan.target_rest_rotations
                ]
        if self.plan.unmapped_target_joints:
            document["plan"]["unmapped_target_joints"] = list(
                self.plan.unmapped_target_joints
            )
        return document

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any],
        *,
        expected_source_sha256: str | None = None,
        expected_target_sha256: str | None = None,
        expected_dependency_sha256: str | None = None,
    ) -> "RetargetCacheEntry":
        try:
            if document.get("version") != 2:
                raise RetargetCacheError("unsupported retarget cache version")
            source_hash = str(document["source_sha256"])
            target_hash = str(document["target_sha256"])
            dependency_hash = document.get("dependency_sha256")
            if dependency_hash is not None:
                dependency_hash = str(dependency_hash)
            if (
                expected_source_sha256 is not None
                and source_hash != expected_source_sha256
            ) or (
                expected_target_sha256 is not None
                and target_hash != expected_target_sha256
            ) or (
                expected_dependency_sha256 is not None
                and dependency_hash != expected_dependency_sha256
            ):
                raise RetargetCacheError("retarget cache is stale")
            raw_plan = document["plan"]
            if not isinstance(raw_plan, Mapping):
                raise RetargetCacheError("retarget cache plan must be an object")
            plan = RetargetPlan(
                source_joints=tuple(str(value) for value in raw_plan["source_joints"]),
                target_joints=tuple(str(value) for value in raw_plan["target_joints"]),
                joint_indices=tuple(int(value) for value in raw_plan["joint_indices"]),
                target_rest_translations=tuple(
                    _vector3(value, label="target rest translation")
                    for value in raw_plan["target_rest_translations"]
                ),
                target_root_index=int(raw_plan["target_root_index"]),
                source_root_index=int(raw_plan["source_root_index"]),
                source_root_origin=_vector3(
                    raw_plan["source_root_origin"], label="source root origin"
                ),
                source_meters_per_unit=_positive_float(
                    raw_plan["source_meters_per_unit"],
                    label="source meters per unit",
                ),
                target_meters_per_unit=_positive_float(
                    raw_plan["target_meters_per_unit"],
                    label="target meters per unit",
                ),
                target_root_up=_unit_vector3(
                    raw_plan["target_root_up"], label="target root up"
                ),
                retarget_mode=str(raw_plan.get("retarget_mode", "strict")),
                unmapped_target_joints=tuple(
                    str(value)
                    for value in raw_plan.get("unmapped_target_joints", ())
                ),
                source_rest_rotations=(
                    tuple(
                        _quaternion4(value, label="source rest rotation")
                        for value in raw_plan["source_rest_rotations"]
                    )
                    if raw_plan.get("source_rest_rotations") is not None
                    else None
                ),
                target_rest_rotations=(
                    tuple(
                        _quaternion4(value, label="target rest rotation")
                        for value in raw_plan["target_rest_rotations"]
                    )
                    if raw_plan.get("target_rest_rotations") is not None
                    else None
                ),
            )
            if (plan.source_rest_rotations is None) != (
                plan.target_rest_rotations is None
            ):
                raise RetargetCacheError(
                    "retarget cache rest rotations must be provided together"
                )
            if plan.source_rest_rotations is not None:
                if len(plan.source_rest_rotations) != len(plan.source_joints):
                    raise RetargetCacheError(
                        "retarget cache source rest rotation width must match "
                        "source joints"
                    )
                if len(plan.target_rest_rotations or ()) != len(plan.target_joints):
                    raise RetargetCacheError(
                        "retarget cache target rest rotation width must match "
                        "target joints"
                    )
            result = cls(
                asset_id=str(document["asset_id"]),
                motion_id=str(document["motion_id"]),
                source_sha256=source_hash,
                target_sha256=target_hash,
                source_animation_path=str(document["source_animation_path"]),
                target_skeleton_relative_path=str(
                    document["target_skeleton_relative_path"]
                ),
                source_time_codes_per_second=float(
                    document["source_time_codes_per_second"]
                ),
                source_sample_start=float(document["source_sample_start"]),
                source_sample_end=float(document["source_sample_end"]),
                plan=plan,
                dependency_sha256=dependency_hash,
            )
        except RetargetCacheError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RetargetCacheError(f"malformed retarget cache: {exc}") from exc
        if (
            not math.isfinite(result.source_time_codes_per_second)
            or result.source_time_codes_per_second <= 0.0
            or not math.isfinite(result.source_sample_start)
            or not math.isfinite(result.source_sample_end)
            or result.source_sample_end < result.source_sample_start
        ):
            raise RetargetCacheError("retarget cache has invalid source timing")
        return result


def _vector3(value: Sequence[float], *, label: str) -> tuple[float, float, float]:
    if len(value) != 3:
        raise HumanMotionRetargetError(f"{label} must contain three values")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise HumanMotionRetargetError(f"{label} must contain finite values")
    return result  # type: ignore[return-value]


def _quaternion4(
    value: Sequence[float], *, label: str
) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise HumanMotionRetargetError(f"{label} must contain four values")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise HumanMotionRetargetError(f"{label} must contain finite values")
    if sum(component * component for component in result) <= 0.0:
        raise HumanMotionRetargetError(f"{label} must have positive norm")
    return result  # type: ignore[return-value]


def _positive_float(value: Any, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise HumanMotionRetargetError(f"{label} must be positive and finite")
    return result


def _unit_vector3(
    value: Sequence[float], *, label: str
) -> tuple[float, float, float]:
    result = _vector3(value, label=label)
    length = math.sqrt(sum(component * component for component in result))
    if not math.isfinite(length) or length <= 0.0:
        raise HumanMotionRetargetError(f"{label} must have positive length")
    return tuple(component / length for component in result)  # type: ignore[return-value]


def facing_yaw_quaternion(
    up_axis: Sequence[float], yaw: float
) -> tuple[float, float, float, float]:
    """Return an x-y-z-w quaternion rotating around a target up axis."""
    yaw = float(yaw)
    if not math.isfinite(yaw):
        raise HumanMotionRetargetError("facing yaw must be finite")
    axis = _unit_vector3(up_axis, label="target root up")
    half_yaw = 0.5 * yaw
    scale = math.sin(half_yaw)
    return (
        axis[0] * scale,
        axis[1] * scale,
        axis[2] * scale,
        math.cos(half_yaw),
    )


def build_retarget_plan(
    *,
    source_joints: Sequence[str],
    target_joints: Sequence[str],
    target_rest_translations: Sequence[Sequence[float]],
    source_root_origin: Sequence[float],
    source_meters_per_unit: float = 1.0,
    target_meters_per_unit: float = 1.0,
    target_root_up: Sequence[float] = (0.0, 1.0, 0.0),
    joint_aliases: Mapping[str, str] | None = None,
    allow_missing: bool = False,
) -> RetargetPlan:
    """Build a named-joint mapping from one clip to one target rest pose.

    Source joint names are translated through ``joint_aliases`` (source name
    to target name space) before matching, so skeletons with disjoint name
    sets can map onto each other.  When ``allow_missing`` is true, target
    joints the clip cannot drive are dropped from the plan and recorded in
    ``unmapped_target_joints``; the runtime leaves those joints at their
    skeleton rest pose.  The default strict path is unchanged.
    """
    source = tuple(str(joint) for joint in source_joints)
    target = tuple(str(joint) for joint in target_joints)
    if len(target) != len(target_rest_translations):
        raise HumanMotionRetargetError(
            "target joints and rest translations must have equal length"
        )
    if joint_aliases:
        source_index = {
            joint_aliases.get(joint, joint): index
            for index, joint in enumerate(source)
        }
    else:
        source_index = {joint: index for index, joint in enumerate(source)}
    missing = [joint for joint in target if joint not in source_index]
    unmapped: tuple[str, ...] = ()
    if missing:
        if not allow_missing:
            raise HumanMotionRetargetError(
                f"motion is missing target joints: {missing}"
            )
        unmapped = tuple(missing)
        keep = [
            index for index, joint in enumerate(target) if joint in source_index
        ]
        target = tuple(target[index] for index in keep)
        target_rest_translations = tuple(
            target_rest_translations[index] for index in keep
        )
    if not target:
        raise HumanMotionRetargetError("target skeleton has no joints")
    target_root_index = min(
        range(len(target)), key=lambda index: (target[index].count("/"), index)
    )
    indices = tuple(source_index[joint] for joint in target)
    return RetargetPlan(
        source_joints=source,
        target_joints=target,
        joint_indices=indices,
        target_rest_translations=tuple(
            _vector3(value, label="target rest translation")
            for value in target_rest_translations
        ),
        target_root_index=target_root_index,
        source_root_index=indices[target_root_index],
        source_root_origin=_vector3(source_root_origin, label="source root origin"),
        source_meters_per_unit=_positive_float(
            source_meters_per_unit, label="source meters per unit"
        ),
        target_meters_per_unit=_positive_float(
            target_meters_per_unit, label="target meters per unit"
        ),
        target_root_up=_unit_vector3(target_root_up, label="target root up"),
        retarget_mode="aliased-lenient" if joint_aliases else "strict",
        unmapped_target_joints=unmapped,
    )


def retarget_pose(
    plan: RetargetPlan,
    *,
    source_rotations: Sequence[Any],
    source_translations: Sequence[Sequence[float]],
    source_scales: Sequence[Sequence[float]],
    root_motion: Literal["in_place", "authored", "none"] = "authored",
) -> RetargetedPose:
    """Map absolute local joint rotations while preserving target bone translations."""
    source_width = len(plan.source_joints)
    if len(source_rotations) != source_width:
        raise HumanMotionRetargetError(
            "motion rotation sample width must match source joints"
        )
    if source_translations and len(source_translations) != source_width:
        raise HumanMotionRetargetError(
            "motion translation sample width must match source joints"
        )
    if source_scales and len(source_scales) != source_width:
        raise HumanMotionRetargetError(
            "motion scale sample width must match source joints"
        )

    translations = list(plan.target_rest_translations)
    if source_translations:
        source_root = _vector3(
            source_translations[plan.source_root_index], label="source root translation"
        )
        target_root = plan.target_rest_translations[plan.target_root_index]
        unit_scale = plan.source_meters_per_unit / plan.target_meters_per_unit
        root_delta = tuple(
            (source_root[axis] - plan.source_root_origin[axis]) * unit_scale
            for axis in range(3)
        )
        if root_motion == "in_place":
            vertical = sum(
                root_delta[axis] * plan.target_root_up[axis] for axis in range(3)
            )
            root_delta = tuple(
                vertical * plan.target_root_up[axis] for axis in range(3)
            )
        elif root_motion == "none":
            root_delta = (0.0, 0.0, 0.0)
        elif root_motion != "authored":
            raise HumanMotionRetargetError(
                f"unsupported root motion policy: {root_motion}"
            )
        translations[plan.target_root_index] = tuple(
            target_root[axis] + root_delta[axis] for axis in range(3)
        )

    if source_scales:
        scales = tuple(
            _vector3(source_scales[index], label="source scale")
            for index in plan.joint_indices
        )
    else:
        scales = tuple((1.0, 1.0, 1.0) for _ in plan.target_joints)

    if (
        plan.source_rest_rotations is not None
        and plan.target_rest_rotations is not None
        and all(
            hasattr(sample, "GetReal") and hasattr(sample, "GetImaginary")
            for sample in source_rotations
        )
    ):
        from pxr import Gf

        def parent_indices(joints: Sequence[str]) -> tuple[int, ...]:
            index_by_name = {name: index for index, name in enumerate(joints)}
            result = []
            for index, name in enumerate(joints):
                parent_name = name.rsplit("/", 1)[0] if "/" in name else None
                parent = -1 if parent_name is None else index_by_name.get(parent_name, -1)
                if parent_name is not None and (parent < 0 or parent >= index):
                    raise HumanMotionRetargetError(
                        f"joint hierarchy is not parent-first at {name!r}"
                    )
                result.append(parent)
            return tuple(result)

        source_parents = parent_indices(plan.source_joints)
        target_parents = parent_indices(plan.target_joints)
        source_rest_global = []
        source_pose_global = []
        for index, (rest, sample) in enumerate(
            zip(plan.source_rest_rotations, source_rotations)
        ):
            rest_local = Gf.Quatd(rest[0], Gf.Vec3d(*rest[1:])).GetNormalized()
            pose_local = Gf.Quatd(sample).GetNormalized()
            parent = source_parents[index]
            source_rest_global.append(
                rest_local
                if parent < 0
                else source_rest_global[parent] * rest_local
            )
            source_pose_global.append(
                pose_local
                if parent < 0
                else source_pose_global[parent] * pose_local
            )

        target_rest_global = []
        target_pose_global = []
        rotations = []
        for index, (source_index, target_rest) in enumerate(
            zip(plan.joint_indices, plan.target_rest_rotations)
        ):
            target_rest_local = Gf.Quatd(
                target_rest[0], Gf.Vec3d(*target_rest[1:])
            ).GetNormalized()
            parent = target_parents[index]
            rest_global = (
                target_rest_local
                if parent < 0
                else target_rest_global[parent] * target_rest_local
            )
            # UsdSkelAnimation rotations are absolute local transforms. Keep
            # the source skeleton-space rest-relative pose, then decompose it
            # back into the target hierarchy's local rotations.
            pose_global = (
                source_pose_global[source_index]
                * source_rest_global[source_index].GetInverse()
                * rest_global
            ).GetNormalized()
            pose_local = (
                pose_global
                if parent < 0
                else target_pose_global[parent].GetInverse() * pose_global
            )
            target_rest_global.append(rest_global)
            target_pose_global.append(pose_global)
            rotations.append(Gf.Quatf(pose_local.GetNormalized()))

        if plan.retarget_mode == "aliased-lenient" and source_translations:
            def joint_positions(parents, local_translations, global_rotations):
                positions = []
                for index, translation in enumerate(local_translations):
                    local = Gf.Vec3d(
                        *_vector3(translation, label="joint translation")
                    )
                    parent = parents[index]
                    positions.append(
                        local
                        if parent < 0
                        else positions[parent]
                        + Gf.Rotation(global_rotations[parent]).TransformDir(local)
                    )
                return positions

            def semantic_indices(joints, root_index):
                by_leaf = {
                    name.rsplit("/", 1)[-1]: index
                    for index, name in enumerate(joints)
                }
                alternatives = {
                    "root": (),
                    "head": ("head",),
                    "left_shoulder": ("left_shoulder", "upperarm_l"),
                    "left_elbow": ("left_elbow", "lowerarm_l"),
                    "left_wrist": ("left_wrist", "hand_l"),
                    "right_shoulder": ("right_shoulder", "upperarm_r"),
                    "right_elbow": ("right_elbow", "lowerarm_r"),
                    "right_wrist": ("right_wrist", "hand_r"),
                }
                result = {"root": root_index}
                for role, leaves in alternatives.items():
                    if role == "root":
                        continue
                    match = next(
                        (by_leaf[leaf] for leaf in leaves if leaf in by_leaf),
                        None,
                    )
                    if match is None:
                        return None
                    result[role] = match
                return result

            def body_frame(positions, indices):
                origin = positions[indices["root"]]
                up = positions[indices["head"]] - origin
                if up.GetLength() <= 1e-8:
                    return None
                scale = up.GetLength()
                up = up.GetNormalized()
                lateral = (
                    positions[indices["left_shoulder"]]
                    - positions[indices["right_shoulder"]]
                )
                lateral -= up * Gf.Dot(lateral, up)
                if lateral.GetLength() <= 1e-8:
                    return None
                lateral = lateral.GetNormalized()
                forward = Gf.Cross(lateral, up)
                if forward.GetLength() <= 1e-8:
                    return None
                return origin, scale, (lateral, up, forward.GetNormalized())

            def map_body_point(point, source_frame, target_frame):
                source_origin, source_scale, source_axes = source_frame
                target_origin, target_scale, target_axes = target_frame
                relative = (point - source_origin) / source_scale
                result = Gf.Vec3d(target_origin)
                for source_axis, target_axis in zip(source_axes, target_axes):
                    result += target_axis * (
                        Gf.Dot(relative, source_axis) * target_scale
                    )
                return result

            def solve_elbow(
                shoulder,
                wrist,
                elbow_hint,
                upper_length,
                lower_length,
                fallback_axis,
            ):
                if upper_length <= 1e-8 or lower_length <= 1e-8:
                    return None
                axis = wrist - shoulder
                if axis.GetLength() <= 1e-8:
                    return None
                minimum = abs(upper_length - lower_length) + 1e-6
                maximum = upper_length + lower_length - 1e-6
                if maximum <= minimum:
                    return None
                distance = min(max(axis.GetLength(), minimum), maximum)
                direction = axis.GetNormalized()
                wrist = shoulder + direction * distance
                along = (
                    upper_length * upper_length
                    - lower_length * lower_length
                    + distance * distance
                ) / (2.0 * distance)
                height = math.sqrt(
                    max(upper_length * upper_length - along * along, 0.0)
                )
                perpendicular = elbow_hint - (shoulder + direction * along)
                perpendicular -= direction * Gf.Dot(perpendicular, direction)
                if perpendicular.GetLength() <= 1e-8:
                    perpendicular = fallback_axis - direction * Gf.Dot(
                        fallback_axis, direction
                    )
                if perpendicular.GetLength() <= 1e-8:
                    return None
                elbow = (
                    shoulder
                    + direction * along
                    + perpendicular.GetNormalized() * height
                )
                return elbow, wrist

            source_positions = joint_positions(
                source_parents, source_translations, source_pose_global
            )
            target_positions = joint_positions(
                target_parents, translations, target_pose_global
            )
            source_indices = semantic_indices(
                plan.source_joints, plan.source_root_index
            )
            target_indices = semantic_indices(
                plan.target_joints, plan.target_root_index
            )
            if source_indices is not None and target_indices is not None:
                source_frame = body_frame(source_positions, source_indices)
                target_frame = body_frame(target_positions, target_indices)
                if source_frame is not None and target_frame is not None:
                    for side in ("left", "right"):
                        shoulder_index = target_indices[f"{side}_shoulder"]
                        elbow_index = target_indices[f"{side}_elbow"]
                        wrist_index = target_indices[f"{side}_wrist"]
                        source_elbow = source_positions[
                            source_indices[f"{side}_elbow"]
                        ]
                        source_wrist = source_positions[
                            source_indices[f"{side}_wrist"]
                        ]
                        shoulder = target_positions[shoulder_index]
                        elbow = target_positions[elbow_index]
                        wrist = target_positions[wrist_index]
                        solution = solve_elbow(
                            shoulder,
                            map_body_point(
                                source_wrist, source_frame, target_frame
                            ),
                            map_body_point(
                                source_elbow, source_frame, target_frame
                            ),
                            (elbow - shoulder).GetLength(),
                            (wrist - elbow).GetLength(),
                            target_frame[2][2],
                        )
                        if solution is None:
                            continue
                        desired_elbow, desired_wrist = solution
                        shoulder_global = (
                            Gf.Rotation(
                                elbow - shoulder,
                                desired_elbow - shoulder,
                            ).GetQuat()
                            * target_pose_global[shoulder_index]
                        ).GetNormalized()
                        elbow_global = (
                            Gf.Rotation(
                                wrist - elbow,
                                desired_wrist - desired_elbow,
                            ).GetQuat()
                            * target_pose_global[elbow_index]
                        ).GetNormalized()
                        shoulder_parent = target_parents[shoulder_index]
                        shoulder_local = (
                            shoulder_global
                            if shoulder_parent < 0
                            else target_pose_global[
                                shoulder_parent
                            ].GetInverse()
                            * shoulder_global
                        )
                        elbow_local = shoulder_global.GetInverse() * elbow_global
                        wrist_local = (
                            elbow_global.GetInverse()
                            * target_pose_global[wrist_index]
                        )
                        rotations[shoulder_index] = Gf.Quatf(
                            shoulder_local.GetNormalized()
                        )
                        rotations[elbow_index] = Gf.Quatf(
                            elbow_local.GetNormalized()
                        )
                        rotations[wrist_index] = Gf.Quatf(
                            wrist_local.GetNormalized()
                        )
    else:
        rotations = [source_rotations[index] for index in plan.joint_indices]
    return RetargetedPose(
        rotations=tuple(rotations),
        translations=tuple(translations),
        scales=scales,
    )


@dataclass
class ActorMotionState:
    actor_id: str
    asset_id: str
    phase: float
    locomotion_motion_id: str | None = None
    locomotion_time: float = 0.0
    action_motion_id: str | None = None
    action_time: float = 0.0
    action_loop: bool = False
    action_playback_speed: float = 1.0


@dataclass(frozen=True)
class MotionSampleRequest:
    actor_id: str
    asset_id: str
    motion_id: str
    local_time: float
    duration: float
    loop: bool


@dataclass(frozen=True)
class MotionEvent:
    """An observable motion lifecycle event for one actor."""

    actor_id: str
    motion_id: str
    kind: Literal["started", "completed", "cancelled", "replaced"]


@dataclass(frozen=True)
class MotionUpdate:
    """The result of one HumanMotionController.update() tick."""

    requests: tuple[MotionSampleRequest, ...]
    events: tuple[MotionEvent, ...]


class HumanMotionController:
    """Own independent motion clocks and action transitions for many actors."""

    def __init__(self, registry: HumanAssetRegistry) -> None:
        self.registry = registry
        self._actors: dict[str, ActorMotionState] = {}
        self._events: list[MotionEvent] = []

    def register_actor(self, actor_id: str, asset_id: str, *, phase: float = 0.0) -> None:
        actor_id = str(actor_id)
        phase = float(phase)
        if not math.isfinite(phase) or not 0.0 <= phase < 1.0:
            raise ValueError("phase must be finite and in the range [0, 1)")
        if actor_id in self._actors:
            raise ValueError(f"actor is already registered: {actor_id}")
        self.registry.asset(asset_id)
        self._actors[actor_id] = ActorMotionState(
            actor_id=actor_id,
            asset_id=str(asset_id),
            phase=phase,
        )

    def unregister_actor(self, actor_id: str) -> None:
        self._actors.pop(str(actor_id), None)

    def _actor(self, actor_id: str) -> ActorMotionState:
        try:
            return self._actors[str(actor_id)]
        except KeyError as exc:
            raise KeyError(f"unknown human actor: {actor_id}") from exc

    def set_locomotion(self, actor_id: str, motion_id: str) -> None:
        state = self._actor(actor_id)
        motion = self.registry.require_motion(state.asset_id, motion_id)
        if state.locomotion_motion_id != motion.id:
            state.locomotion_motion_id = motion.id
            state.locomotion_time = state.phase * motion.duration

    def play_action(
        self,
        actor_id: str,
        motion_id: str,
        *,
        loop: bool | None = None,
        playback_speed: float = 1.0,
    ) -> MotionSampleRequest:
        playback_speed = float(playback_speed)
        if not math.isfinite(playback_speed) or playback_speed <= 0.0:
            raise ValueError("playback_speed must be positive and finite")
        state = self._actor(actor_id)
        motion = self.registry.require_motion(state.asset_id, motion_id)
        resolved_loop = motion.loop if loop is None else bool(loop)
        previous_action = state.action_motion_id
        state.action_motion_id = motion.id
        state.action_time = 0.0
        state.action_loop = resolved_loop
        state.action_playback_speed = playback_speed
        if previous_action is not None and previous_action != motion.id:
            self._events.append(
                MotionEvent(
                    actor_id=state.actor_id,
                    motion_id=previous_action,
                    kind="replaced",
                )
            )
        self._events.append(
            MotionEvent(
                actor_id=state.actor_id,
                motion_id=motion.id,
                kind="started",
            )
        )
        return MotionSampleRequest(
            actor_id=state.actor_id,
            asset_id=state.asset_id,
            motion_id=motion.id,
            local_time=0.0,
            duration=motion.duration,
            loop=resolved_loop,
        )

    def stop_action(self, actor_id: str) -> None:
        state = self._actor(actor_id)
        if state.action_motion_id is not None:
            self._events.append(
                MotionEvent(
                    actor_id=state.actor_id,
                    motion_id=state.action_motion_id,
                    kind="cancelled",
                )
            )
        self._clear_action_state(state)

    @staticmethod
    def _clear_action_state(state: ActorMotionState) -> None:
        state.action_motion_id = None
        state.action_time = 0.0
        state.action_loop = False
        state.action_playback_speed = 1.0

    @staticmethod
    def _advance_loop(local_time: float, dt: float, motion: HumanMotionSpec) -> float:
        if motion.duration <= 0.0:
            raise ValueError(f"motion '{motion.id}' must have a positive duration")
        return (local_time + dt) % motion.duration

    def update(self, dt: float) -> MotionUpdate:
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError("dt must be non-negative and finite")

        requests: list[MotionSampleRequest] = []
        for actor_id in sorted(self._actors):
            state = self._actors[actor_id]
            locomotion: HumanMotionSpec | None = None
            if state.locomotion_motion_id is not None and state.action_motion_id is None:
                locomotion = self.registry.motion(state.locomotion_motion_id)
                state.locomotion_time = self._advance_loop(
                    state.locomotion_time, dt, locomotion
                )

            selected = locomotion
            selected_time = state.locomotion_time
            selected_loop = locomotion.loop if locomotion is not None else False
            if state.action_motion_id is not None:
                action = self.registry.motion(state.action_motion_id)
                elapsed = state.action_time + dt * state.action_playback_speed
                if state.action_loop:
                    state.action_time = self._advance_loop(0.0, elapsed, action)
                    selected = action
                    selected_time = state.action_time
                    selected_loop = True
                elif elapsed < action.duration:
                    state.action_time = elapsed
                    selected = action
                    selected_time = elapsed
                    selected_loop = False
                else:
                    self._events.append(
                        MotionEvent(
                            actor_id=state.actor_id,
                            motion_id=action.id,
                            kind="completed",
                        )
                    )
                    self._clear_action_state(state)
                    selected = action
                    selected_time = action.duration
                    selected_loop = False

            if selected is not None:
                requests.append(
                    MotionSampleRequest(
                        actor_id=actor_id,
                        asset_id=state.asset_id,
                        motion_id=selected.id,
                        local_time=selected_time,
                        duration=selected.duration,
                        loop=selected_loop,
                    )
                )

        events = tuple(self._events)
        self._events.clear()
        return MotionUpdate(requests=tuple(requests), events=events)


@dataclass
class _UsdActorBinding:
    asset_id: str
    actor_path: Any
    animation_path: Any
    skeleton_prim: Any
    animation: Any


class UsdHumanAnimationAdapter:
    """Sample cached clips into actor-local UsdSkelAnimation prims."""

    def __init__(
        self,
        stage: Any,
        registry: HumanAssetRegistry,
        *,
        cache_root: str | Path,
        verify_hashes: bool = True,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdSkel, Vt

        self.stage = stage
        self.registry = registry
        self.cache_root = Path(cache_root).resolve()
        self.verify_hashes = bool(verify_hashes)
        self._Gf = Gf
        self._Sdf = Sdf
        self._Usd = Usd
        self._UsdSkel = UsdSkel
        self._Vt = Vt
        self._actors: dict[str, _UsdActorBinding] = {}
        self._entries: dict[tuple[str, str], RetargetCacheEntry] = {}
        self._hashes: dict[Path, str] = {}
        self._source_animations: dict[tuple[Path, str], tuple[Any, Any]] = {}

    def _sha256(self, path: Path) -> str:
        if path not in self._hashes:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._hashes[path] = digest.hexdigest()
        return self._hashes[path]

    def _entry(self, asset_id: str, motion_id: str) -> RetargetCacheEntry:
        key = (asset_id, motion_id)
        if key in self._entries:
            return self._entries[key]
        asset = self.registry.asset(asset_id)
        motion = self.registry.require_motion(asset_id, motion_id)
        cache_path, cached_motion_id = resolve_retarget_cache_path(
            self.cache_root, asset_id, motion_id
        )
        try:
            document = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RetargetCacheError(f"could not read retarget cache {cache_path}: {exc}") from exc
        entry = RetargetCacheEntry.from_document(
            document,
            expected_source_sha256=self._sha256(motion.usd_path)
            if self.verify_hashes
            else None,
            expected_target_sha256=self._sha256(asset.usd_path)
            if self.verify_hashes
            else None,
        )
        if entry.asset_id != asset_id or entry.motion_id != cached_motion_id:
            raise RetargetCacheError("retarget cache identity does not match request")
        self._entries[key] = entry
        return entry

    def _bind_animation(self, skeleton_prim: Any, animation_path: Any) -> None:
        prims = [skeleton_prim]
        parent = skeleton_prim.GetParent()
        while parent.IsValid():
            if parent.IsA(self._UsdSkel.Root):
                prims.append(parent)
                break
            parent = parent.GetParent()
        for prim in prims:
            binding_api = self._UsdSkel.BindingAPI.Apply(prim)
            relationship = binding_api.CreateAnimationSourceRel()
            relationship.SetTargets([animation_path])

    def register_actor(self, actor_id: str, asset_id: str, actor_path: Any) -> None:
        actor_id = str(actor_id)
        if actor_id in self._actors:
            raise ValueError(f"actor is already registered with USD adapter: {actor_id}")
        asset = self.registry.asset(asset_id)
        if not asset.can_play_actions or not asset.motions:
            raise HumanAssetCapabilityError(
                f"human asset '{asset_id}' cannot play actions"
            )
        actor_path = self._Sdf.Path(str(actor_path))
        actor_prim = self.stage.GetPrimAtPath(actor_path)
        if not actor_prim.IsValid():
            raise ValueError(f"actor prim does not exist: {actor_path}")
        actor_prim.SetInstanceable(False)

        entry = self._entry(asset_id, asset.motions[0])
        skeleton_path = actor_path.AppendPath(
            self._Sdf.Path(entry.target_skeleton_relative_path)
        )
        skeleton_prim = self.stage.GetPrimAtPath(skeleton_path)
        if not skeleton_prim.IsValid() or not skeleton_prim.IsA(self._UsdSkel.Skeleton):
            raise RetargetCacheError(
                f"target Skeleton is missing under actor: {skeleton_path}"
            )
        animation_path = actor_path.AppendChild("HumanRuntimeAnimation")
        animation = self._UsdSkel.Animation.Define(self.stage, animation_path)
        self._bind_animation(skeleton_prim, animation_path)
        self._actors[actor_id] = _UsdActorBinding(
            asset_id=asset_id,
            actor_path=actor_path,
            animation_path=animation_path,
            skeleton_prim=skeleton_prim,
            animation=animation,
        )

    def unregister_actor(self, actor_id: str) -> None:
        binding = self._actors.pop(str(actor_id), None)
        if binding is not None:
            self.stage.RemovePrim(binding.animation_path)

    def animation_path(self, actor_id: str) -> Any:
        try:
            return self._actors[str(actor_id)].animation_path
        except KeyError as exc:
            raise KeyError(f"unknown USD human actor: {actor_id}") from exc

    def _source_animation(self, motion_path: Path, entry: RetargetCacheEntry) -> Any:
        key = (motion_path, entry.source_animation_path)
        if key not in self._source_animations:
            stage = self._Usd.Stage.Open(motion_path.as_posix())
            if stage is None:
                raise RetargetCacheError(f"could not open source motion: {motion_path}")
            prim = stage.GetPrimAtPath(self._Sdf.Path(entry.source_animation_path))
            if not prim.IsValid() or not prim.IsA(self._UsdSkel.Animation):
                raise RetargetCacheError(
                    f"source animation is missing: {entry.source_animation_path}"
                )
            self._source_animations[key] = (stage, self._UsdSkel.Animation(prim))
        return self._source_animations[key][1]

    def apply(self, request: MotionSampleRequest) -> None:
        try:
            binding = self._actors[request.actor_id]
        except KeyError as exc:
            raise KeyError(f"unknown USD human actor: {request.actor_id}") from exc
        if binding.asset_id != request.asset_id:
            raise ValueError("motion request asset does not match registered actor")
        motion = self.registry.require_motion(request.asset_id, request.motion_id)
        entry = self._entry(request.asset_id, request.motion_id)
        source = self._source_animation(motion.usd_path, entry)

        source_time = (
            entry.source_sample_start
            + float(request.local_time) * entry.source_time_codes_per_second
        )
        source_time = min(max(source_time, entry.source_sample_start), entry.source_sample_end)
        time_code = self._Usd.TimeCode(source_time)
        rotations = source.GetRotationsAttr().Get(time_code) or []
        translations = source.GetTranslationsAttr().Get(time_code) or []
        scales = source.GetScalesAttr().Get(time_code) or []
        pose = retarget_pose(
            entry.plan,
            source_rotations=rotations,
            source_translations=translations,
            source_scales=scales,
            root_motion=motion.root_motion,
        )
        animation = binding.animation
        animation.CreateJointsAttr().Set(self._Vt.TokenArray(entry.plan.target_joints))
        animation.CreateRotationsAttr().Set(self._Vt.QuatfArray(pose.rotations))
        animation.CreateTranslationsAttr().Set(
            self._Vt.Vec3fArray(
                [self._Gf.Vec3f(*translation) for translation in pose.translations]
            )
        )
        animation.CreateScalesAttr().Set(
            self._Vt.Vec3hArray([self._Gf.Vec3h(*scale) for scale in pose.scales])
        )

    def apply_all(self, requests: Sequence[MotionSampleRequest]) -> None:
        for request in requests:
            self.apply(request)
