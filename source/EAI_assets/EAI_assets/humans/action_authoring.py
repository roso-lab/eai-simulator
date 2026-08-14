# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Action draft validation, compilation, and atomic publishing."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .profiles import CANONICAL_PROFILES


_ACTION_SCHEMA_PATH = Path(__file__).parent / "action.schema.json"


class ActionValidationError(ValueError):
    """Raised when an action draft fails validation."""


class ActionPublishError(ValueError):
    """Raised when publishing fails (e.g. collision, write error)."""


@dataclass(frozen=True)
class ActionKeyframe:
    time: float
    joints: dict[str, tuple[float, float, float, float]]
    pelvis_translation: tuple[float, float, float] | None


@dataclass(frozen=True)
class ActionDraft:
    action_id: str
    source_profile: str
    keyframes: tuple[ActionKeyframe, ...]
    label: str | None = None
    fps: float = 30.0
    loop: bool = False
    version: int = 1


def _load_schema() -> Mapping[str, Any]:
    try:
        return json.loads(_ACTION_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionValidationError(f"could not load action schema: {exc}") from exc


def _validate_quaternion(value: Sequence[float], *, joint_name: str) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise ActionValidationError(f"joint '{joint_name}' quaternion must have 4 components")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise ActionValidationError(f"joint '{joint_name}' quaternion must be finite")
    norm = math.sqrt(sum(component * component for component in result))
    if abs(norm - 1.0) > 1e-6:
        raise ActionValidationError(
            f"joint '{joint_name}' quaternion is not unit length (norm={norm})"
        )
    return result


def _validate_vector3(value: Sequence[float], *, label: str) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ActionValidationError(f"{label} must have 3 components")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise ActionValidationError(f"{label} must be finite")
    return result


def validate_action(draft: dict[str, Any]) -> ActionDraft:
    """Validate a raw action draft dict against the schema and profile.

    Returns a structured ActionDraft on success or raises ActionValidationError.
    """
    source_profile = str(draft.get("source_profile", "smplx_70"))
    if source_profile not in CANONICAL_PROFILES:
        raise ActionValidationError(f"unknown source profile: {source_profile}")

    keyframes_candidate = draft.get("keyframes")
    if keyframes_candidate == []:
        raise ActionValidationError("at least one keyframe is required")
    if isinstance(keyframes_candidate, list):
        for index, raw in enumerate(keyframes_candidate):
            if not isinstance(raw, dict) or "time" not in raw:
                continue
            try:
                time = float(raw["time"])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(time) or time < 0.0:
                raise ActionValidationError(
                    f"keyframe {index} time must be finite and non-negative"
                )

    schema = _load_schema()
    try:
        import jsonschema
        jsonschema.validate(draft, schema)
    except ImportError:
        pass
    except jsonschema.ValidationError as exc:
        raise ActionValidationError(str(exc)) from exc

    action_id = str(draft["action_id"])
    canonical_joints = CANONICAL_PROFILES[source_profile]

    keyframes_raw = draft["keyframes"]
    if not keyframes_raw:
        raise ActionValidationError("at least one keyframe is required")

    keyframes: list[ActionKeyframe] = []
    for index, raw in enumerate(keyframes_raw):
        time = float(raw["time"])
        if not math.isfinite(time) or time < 0.0:
            raise ActionValidationError(f"keyframe {index} time must be finite and non-negative")

        joints: dict[str, tuple[float, float, float, float]] = {}
        for joint_name, quaternion in raw.get("joints", {}).items():
            if joint_name not in canonical_joints:
                raise ActionValidationError(f"unknown joint '{joint_name}' in keyframe {index}")
            joints[joint_name] = _validate_quaternion(quaternion, joint_name=joint_name)

        pelvis = None
        if "pelvis_translation" in raw:
            pelvis = _validate_vector3(raw["pelvis_translation"], label="pelvis_translation")

        keyframes.append(
            ActionKeyframe(time=time, joints=joints, pelvis_translation=pelvis)
        )

    times = [kf.time for kf in keyframes]
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            raise ActionValidationError(
                f"keyframe times must be strictly monotonic: {times[i]} <= {times[i - 1]}"
            )

    return ActionDraft(
        action_id=action_id,
        source_profile=source_profile,
        keyframes=tuple(keyframes),
        label=draft.get("label"),
        fps=float(draft.get("fps", 30.0)),
        loop=bool(draft.get("loop", False)),
        version=int(draft.get("version", 1)),
    )


@dataclass(frozen=True)
class PublishedActionResult:
    action_id: str
    animation_path: Path
    overlay_path: Path
    content_sha256: str


class HumanActionPublisher:
    """Atomic publish of custom action drafts into the human action catalog."""

    def __init__(
        self,
        output_root: Path,
        canonical_profile: str = "smplx_70",
    ) -> None:
        self.output_root = Path(output_root).resolve()
        if canonical_profile not in CANONICAL_PROFILES:
            raise ActionValidationError(f"unknown canonical profile: {canonical_profile}")
        self.canonical_profile = canonical_profile
        self._canonical_joints = CANONICAL_PROFILES[canonical_profile]

    def _compile_animation(self, draft: ActionDraft, output_path: Path) -> Path:
        """Write a UsdSkelAnimation for the draft and return its path."""
        output_path = Path(output_path)

        from pxr import Gf, Sdf, Usd, UsdSkel, Vt

        output_path.parent.mkdir(parents=True, exist_ok=True)
        stage = Usd.Stage.CreateNew(output_path.as_posix())
        stage.SetTimeCodesPerSecond(draft.fps)
        duration_seconds = draft.keyframes[-1].time
        stage.SetStartTimeCode(0.0)
        stage.SetEndTimeCode(duration_seconds * draft.fps)

        animation = UsdSkel.Animation.Define(stage, "/Action")
        stage.SetDefaultPrim(animation.GetPrim())
        animation.CreateJointsAttr().Set(Vt.TokenArray(self._canonical_joints))

        rotations_attr = animation.CreateRotationsAttr()
        translations_attr = animation.CreateTranslationsAttr()

        for keyframe in draft.keyframes:
            time_code = Usd.TimeCode(keyframe.time * draft.fps)
            rotations: list[Any] = []
            translations: list[Any] = []
            for joint in self._canonical_joints:
                if joint in keyframe.joints:
                    q = keyframe.joints[joint]
                    rotations.append(Gf.Quatf(q[3], Gf.Vec3f(q[0], q[1], q[2])))
                else:
                    rotations.append(Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)))
                if keyframe.pelvis_translation is not None and joint == "pelvis":
                    p = keyframe.pelvis_translation
                    translations.append(Gf.Vec3f(*p))
                else:
                    translations.append(Gf.Vec3f(0.0, 0.0, 0.0))

            rotations_attr.Set(Vt.QuatfArray(rotations), time_code)
            if keyframe.pelvis_translation is not None or joint == "pelvis":
                translations_attr.Set(Vt.Vec3fArray(translations), time_code)

        stage.GetRootLayer().Save()
        return output_path

    @staticmethod
    def _motion_record(
        draft: ActionDraft,
        *,
        relative_usd_path: str,
        content_sha256: str,
        source: Mapping[str, Any],
        sample_start: float | None = None,
        sample_end: float | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": draft.action_id,
            "label": draft.label or draft.action_id,
            "usd_path": relative_usd_path,
            "source_profile": draft.source_profile,
            "duration": draft.keyframes[-1].time,
            "loop": draft.loop,
            "enabled": True,
            "semantic": "action",
            "variant": "custom",
            "root_motion": "in_place",
            "path_policy": "pause",
            "resume_policy": "resume_phase",
            "content_sha256": content_sha256,
            "redistribution_status": "review_required",
            "source": dict(source),
        }
        if sample_start is not None and sample_end is not None:
            record["sample_start"] = sample_start
            record["sample_end"] = sample_end
        return record

    @staticmethod
    def _read_catalog(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"version": 2, "assets": [], "motions": []}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ActionPublishError(f"could not read custom action catalog: {exc}") from exc
        if (
            not isinstance(document, dict)
            or document.get("version") != 2
            or document.get("assets") != []
            or not isinstance(document.get("motions"), list)
        ):
            raise ActionPublishError("custom action catalog has an invalid structure")
        return document

    @staticmethod
    def _commit_catalog(temporary: Path, destination: Path) -> None:
        temporary.replace(destination)

    def _publish_prepared(
        self,
        draft: ActionDraft,
        *,
        animation_name: str,
        prepare_animation: Callable[[Path], Path],
        source: Mapping[str, Any],
        replace: bool,
        sample_start: float | None = None,
        sample_end: float | None = None,
    ) -> PublishedActionResult:
        if draft.action_id in {
            "bow",
            "jog",
            "motion_120_04",
            "motion_15_01",
            "stand_to_walk_back",
            "walk",
        }:
            raise ActionPublishError(
                f"cannot publish over canonical motion: {draft.action_id}"
            )

        custom_root = self.output_root / "custom-actions"
        action_dir = custom_root / draft.action_id
        anim_path = action_dir / animation_name
        overlay_path = custom_root / "manifest.json"
        staging = custom_root / f".{draft.action_id}"
        backup = custom_root / f".{draft.action_id}.backup"
        catalog_temporary = custom_root / f".manifest.{draft.action_id}.tmp"

        if action_dir.exists():
            if not replace:
                raise ActionPublishError(
                    f"action '{draft.action_id}' already exists; use replace=True"
                )
        if backup.exists():
            raise ActionPublishError(
                f"action '{draft.action_id}' has an unfinished backup: {backup}"
            )

        custom_root.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        catalog_temporary.unlink(missing_ok=True)
        previous_catalog = overlay_path.read_bytes() if overlay_path.is_file() else None
        installed_action = False
        backed_up_action = False

        try:
            staging_anim = staging / animation_name
            staging.mkdir(parents=True, exist_ok=True)
            compiled = prepare_animation(staging_anim)
            content_sha256 = hashlib.sha256(compiled.read_bytes()).hexdigest()

            catalog = self._read_catalog(overlay_path)
            motions = [
                motion
                for motion in catalog["motions"]
                if not isinstance(motion, dict) or motion.get("id") != draft.action_id
            ]
            motions.append(
                self._motion_record(
                    draft,
                    relative_usd_path=(
                        f"custom-actions/{draft.action_id}/{animation_name}"
                    ),
                    content_sha256=content_sha256,
                    source=source,
                    sample_start=sample_start,
                    sample_end=sample_end,
                )
            )
            catalog["motions"] = sorted(motions, key=lambda motion: motion["id"])
            catalog_temporary.write_text(
                json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8"
            )

            if action_dir.exists():
                action_dir.replace(backup)
                backed_up_action = True
            staging.replace(action_dir)
            installed_action = True
            self._commit_catalog(catalog_temporary, overlay_path)

            if backed_up_action:
                shutil.rmtree(backup)
        except Exception:
            catalog_temporary.unlink(missing_ok=True)
            if previous_catalog is None:
                overlay_path.unlink(missing_ok=True)
            else:
                rollback_catalog = custom_root / f".manifest.{draft.action_id}.rollback"
                rollback_catalog.write_bytes(previous_catalog)
                rollback_catalog.replace(overlay_path)
            if installed_action and action_dir.exists():
                shutil.rmtree(action_dir)
            if backed_up_action and backup.exists():
                backup.replace(action_dir)
            shutil.rmtree(staging, ignore_errors=True)
            raise

        return PublishedActionResult(
            action_id=draft.action_id,
            animation_path=anim_path,
            overlay_path=overlay_path,
            content_sha256=content_sha256,
        )

    def publish(
        self,
        draft: ActionDraft | dict[str, Any],
        *,
        replace: bool = False,
    ) -> PublishedActionResult:
        """Validate, compile, and atomically publish one authored action."""
        if isinstance(draft, dict):
            draft = validate_action(draft)
        return self._publish_prepared(
            draft,
            animation_name="animation.usda",
            prepare_animation=lambda output_path: self._compile_animation(
                draft, output_path
            ),
            source={},
            replace=replace,
        )

    def publish_usd_action(
        self,
        source_usd: Path,
        *,
        action_id: str,
        label: str | None = None,
        source_profile: str = "smplx_70",
        loop: bool = False,
        replace: bool = False,
        sample_start: float | None = None,
        sample_end: float | None = None,
    ) -> PublishedActionResult:
        """Validate and atomically publish one converted UsdSkelAnimation."""
        if source_profile not in CANONICAL_PROFILES:
            raise ActionValidationError(f"unknown source profile: {source_profile}")
        source_usd = Path(source_usd).resolve()
        if not source_usd.is_file():
            raise ActionPublishError(f"USD action source not found: {source_usd}")

        from pxr import Usd, UsdSkel

        stage = Usd.Stage.Open(source_usd.as_posix())
        if stage is None:
            raise ActionPublishError(f"could not open USD action source: {source_usd}")
        animations = [
            UsdSkel.Animation(prim)
            for prim in stage.Traverse()
            if prim.IsA(UsdSkel.Animation)
        ]
        if len(animations) != 1:
            raise ActionPublishError("USD action source must contain one UsdSkelAnimation")
        animation = animations[0]
        joints = tuple(str(joint) for joint in (animation.GetJointsAttr().Get() or ()))
        if joints != CANONICAL_PROFILES[source_profile]:
            raise ActionPublishError(
                f"USD action joints do not match source profile '{source_profile}'"
            )
        rotation_samples = animation.GetRotationsAttr().GetTimeSamples()
        if len(rotation_samples) < 2:
            raise ActionPublishError("USD action must contain at least two rotation samples")
        time_codes_per_second = float(stage.GetTimeCodesPerSecond())
        if not math.isfinite(time_codes_per_second) or time_codes_per_second <= 0.0:
            raise ActionPublishError("USD action has invalid time codes per second")
        source_sample_start = float(rotation_samples[0])
        source_sample_end = float(rotation_samples[-1])
        if (sample_start is None) != (sample_end is None):
            raise ActionPublishError("USD action sample range bounds must be provided together")
        explicit_sample_range = sample_start is not None
        if sample_start is None:
            selected_start = source_sample_start
            selected_end = source_sample_end
        else:
            selected_start = float(sample_start)
            selected_end = float(sample_end)
            if not math.isfinite(selected_start) or not math.isfinite(selected_end):
                raise ActionPublishError("USD action sample range bounds must be finite")
            if selected_start < source_sample_start or selected_end > source_sample_end:
                raise ActionPublishError(
                    "USD action sample range is outside source animation range "
                    f"[{source_sample_start}, {source_sample_end}]"
                )
            if selected_end <= selected_start:
                raise ActionPublishError("USD action sample range must have positive length")

        duration = (selected_end - selected_start) / time_codes_per_second
        if not math.isfinite(duration) or duration <= 0.0:
            raise ActionPublishError("USD action has no positive duration")

        draft = ActionDraft(
            action_id=str(action_id),
            source_profile=source_profile,
            keyframes=(
                ActionKeyframe(time=0.0, joints={}, pelvis_translation=None),
                ActionKeyframe(time=duration, joints={}, pelvis_translation=None),
            ),
            label=label,
            fps=time_codes_per_second,
            loop=bool(loop),
        )

        def copy_animation(output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_usd, output_path)
            return output_path

        return self._publish_prepared(
            draft,
            animation_name="animation.usd",
            prepare_animation=copy_animation,
            source={"kind": "gltf_import"},
            replace=replace,
            sample_start=selected_start if explicit_sample_range else None,
            sample_end=selected_end if explicit_sample_range else None,
        )
