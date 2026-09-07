"""Resolve user-facing USD paths to EAI scene collections.

The resolver uses the scene objects already created by Isaac Lab.  It does not
import USD or Isaac modules, which keeps path and capability checks testable in
the lightweight repository environment.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .types import ResolvedTarget, TargetSpec


_ENV_PATTERN = re.compile(r"env_\.\*|env_\*")


def _normalize_path(path: object) -> str:
    value = str(path or "").strip()
    value = value.replace("/World/{ENV_REGEX_NS}", "/World/envs/env_0")
    value = value.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
    value = _ENV_PATTERN.sub("env_0", value)
    while "//" in value:
        value = value.replace("//", "/")
    if not value.startswith("/"):
        return ""
    return value.rstrip("/") or "/"


def _is_descendant(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _configured_path(obj: Any) -> str:
    return _normalize_path(getattr(getattr(obj, "cfg", None), "prim_path", ""))


def _physx_paths(obj: Any) -> tuple[str, ...]:
    view = getattr(obj, "root_physx_view", None)
    values = getattr(view, "prim_paths", ()) or ()
    return tuple(path for path in (_normalize_path(item) for item in values) if path)


def _link_paths(obj: Any) -> tuple[str, ...]:
    view = getattr(obj, "root_physx_view", None)
    values = getattr(view, "link_paths", ()) or ()
    if values and isinstance(values[0], (list, tuple)):
        values = values[0]
    return tuple(path for path in (_normalize_path(item) for item in values) if path)


def _candidate_roots(obj: Any) -> tuple[str, ...]:
    configured = _configured_path(obj)
    roots: list[str] = []
    if configured:
        roots.append(configured)
    for path in _physx_paths(obj):
        if configured and _is_descendant(path, configured):
            continue
        parent = path.rsplit("/", 1)[0] or "/"
        if parent not in roots:
            roots.append(parent)
    return tuple(roots)


def _has_attrs(data: Any, *names: str) -> bool:
    return all(hasattr(data, name) for name in names)


def _effort_attr(data: Any) -> str | None:
    for name in ("applied_torque", "joint_effort", "computed_torque"):
        if hasattr(data, name):
            return name
    return None


def _body_index(obj: Any, path: str) -> int | None:
    leaf = path.rsplit("/", 1)[-1]
    finder = getattr(obj, "find_bodies", None)
    if not callable(finder):
        return None
    try:
        body_ids, _ = finder(leaf, preserve_order=True)
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None
    if len(body_ids) != 1:
        return None
    return int(body_ids[0])


class TargetResolver:
    """Resolve :class:`TargetSpec` values against one EAI environment."""

    def __init__(self, base_env: Any):
        scene = getattr(base_env, "scene", None)
        if scene is None:
            raise ValueError("base_env has no scene")
        self.base_env = base_env
        self.num_envs = int(
            getattr(base_env, "num_envs", getattr(scene, "num_envs", 1))
        )
        if self.num_envs != 1:
            raise ValueError("physics replay currently supports num_envs=1 only")
        self.stage = getattr(base_env, "stage", None)
        if self.stage is None:
            try:
                import omni.usd

                self.stage = omni.usd.get_context().get_stage()
            except (ImportError, AttributeError, RuntimeError):
                self.stage = None
        self._articulations = dict(getattr(scene, "articulations", {}) or {})
        self._rigid_objects = dict(getattr(scene, "rigid_objects", {}) or {})

    def resolve_all(self, specs: Iterable[TargetSpec]) -> tuple[ResolvedTarget, ...]:
        return tuple(
            self._resolve_one(spec, target_id=f"target_{index}")
            for index, spec in enumerate(specs)
        )

    def resolve(self, spec: TargetSpec) -> ResolvedTarget:
        return self._resolve_one(spec, target_id="target_0")

    def _resolve_one(self, spec: TargetSpec, *, target_id: str) -> ResolvedTarget:
        if spec.env_id >= self.num_envs:
            raise ValueError(
                f"env_id={spec.env_id} is outside the available environment range"
            )
        if self.stage is not None and hasattr(self.stage, "GetPrimAtPath"):
            try:
                prim = self.stage.GetPrimAtPath(spec.prim_path)
                valid = bool(prim is not None and prim.IsValid())
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"could not inspect USD Prim path {spec.prim_path!r}: {exc}"
                ) from exc
            if not valid:
                raise ValueError(f"Prim path {spec.prim_path!r} does not exist in the USD stage")
        matches: list[ResolvedTarget] = []
        for name, obj in self._articulations.items():
            match = self._resolve_articulation(spec, target_id, str(name), obj)
            if match is not None:
                matches.append(match)
        if not matches:
            for name, obj in self._rigid_objects.items():
                match = self._resolve_rigid_object(spec, target_id, str(name), obj)
                if match is not None:
                    matches.append(match)
        if not matches:
            raise ValueError(f"Prim path {spec.prim_path!r} does not match a scene object")
        if len(matches) > 1:
            names = ", ".join(item.collection_name for item in matches)
            raise ValueError(f"Prim path {spec.prim_path!r} matches multiple scene objects: {names}")
        resolved = matches[0]
        unavailable = [
            channel for channel in spec.channels if channel not in resolved.supported_channels
        ]
        if unavailable:
            raise ValueError(
                f"Prim path {spec.prim_path!r} does not support channel(s): "
                + ", ".join(unavailable)
            )
        return resolved

    def _resolve_articulation(
        self, spec: TargetSpec, target_id: str, name: str, obj: Any
    ) -> ResolvedTarget | None:
        path = spec.prim_path
        roots = _candidate_roots(obj)
        configured = _configured_path(obj)
        physx_paths = _physx_paths(obj)
        link_paths = _link_paths(obj)
        is_root = bool(configured and path == configured) or (
            not configured and path in roots
        )
        body_index: int | None = None
        kind = "articulation"
        root_path = configured or (roots[0] if roots else path)
        if not is_root:
            descendant_of_root = any(_is_descendant(path, root) for root in roots)
            if not descendant_of_root and path not in physx_paths:
                return None
            if link_paths and path not in link_paths:
                return None
            body_index = _body_index(obj, path)
            if body_index is None:
                return None
            kind = "link"

        data = getattr(obj, "data", None)
        supported = self._supported_channels(data, kind)
        writable = tuple(
            channel
            for channel in spec.channels
            if channel in self._writable_channels(obj, data, kind)
        )
        return ResolvedTarget(
            target_id=target_id,
            spec=spec,
            kind=kind,
            collection_name=name,
            root_path=root_path,
            body_index=body_index,
            supported_channels=supported,
            writable_channels=writable,
            depth=path.count("/"),
            metadata={
                "configured_path": configured,
                "physx_paths": list(physx_paths),
                "link_paths": list(link_paths),
            },
        )

    def _resolve_rigid_object(
        self, spec: TargetSpec, target_id: str, name: str, obj: Any
    ) -> ResolvedTarget | None:
        configured = _configured_path(obj)
        if not configured or not _is_descendant(spec.prim_path, configured):
            return None
        if spec.prim_path != configured:
            raise ValueError(
                f"Rigid object target must use its root Prim path {configured!r}, "
                f"got {spec.prim_path!r}"
            )
        data = getattr(obj, "data", None)
        supported = self._supported_channels(data, "rigid_object")
        writable = tuple(
            channel
            for channel in spec.channels
            if channel in self._writable_channels(obj, data, "rigid_object")
        )
        return ResolvedTarget(
            target_id=target_id,
            spec=spec,
            kind="rigid_object",
            collection_name=name,
            root_path=configured,
            supported_channels=supported,
            writable_channels=writable,
            depth=spec.prim_path.count("/"),
            metadata={"configured_path": configured},
        )

    @staticmethod
    def _supported_channels(data: Any, kind: str) -> tuple[str, ...]:
        if data is None:
            return ()
        channels: list[str] = []
        if kind == "link":
            if _has_attrs(data, "body_pos_w", "body_quat_w"):
                channels.append("pose")
            if _has_attrs(data, "body_lin_vel_w", "body_ang_vel_w"):
                channels.append("velocity")
            if hasattr(data, "net_contact_forces_w"):
                channels.append("contact_force")
            return tuple(channels)
        if _has_attrs(data, "root_pos_w", "root_quat_w"):
            channels.append("pose")
        if _has_attrs(data, "root_lin_vel_w", "root_ang_vel_w"):
            channels.append("velocity")
        if kind == "articulation" and _has_attrs(data, "joint_pos", "joint_vel"):
            channels.append("joint_state")
        if kind == "articulation" and _has_attrs(data, "joint_pos_target", "joint_vel_target"):
            channels.append("joint_target")
        if kind == "articulation" and hasattr(data, "joint_effort_target"):
            channels.append("joint_effort_target")
        if hasattr(data, "net_contact_forces_w"):
            channels.append("contact_force")
        if kind == "articulation" and _effort_attr(data):
            channels.append("joint_effort")
        return tuple(channels)

    @staticmethod
    def _writable_channels(obj: Any, data: Any, kind: str) -> tuple[str, ...]:
        if data is None:
            return ()
        channels: list[str] = []
        if (
            kind == "articulation"
            and hasattr(data, "joint_effort_target")
            and callable(getattr(obj, "set_joint_effort_target", None))
        ):
            channels.append("joint_effort_target")
        if kind in {"articulation", "rigid_object"}:
            if _has_attrs(data, "root_pos_w", "root_quat_w") and callable(
                getattr(obj, "write_root_pose_to_sim", None)
            ):
                channels.append("pose")
            if _has_attrs(data, "root_lin_vel_w", "root_ang_vel_w") and callable(
                getattr(obj, "write_root_velocity_to_sim", None)
            ):
                channels.append("velocity")
        if (
            kind == "articulation"
            and _has_attrs(data, "joint_pos", "joint_vel")
            and callable(getattr(obj, "write_joint_state_to_sim", None))
        ):
            channels.append("joint_state")
        if (
            kind == "articulation"
            and _has_attrs(data, "joint_pos_target", "joint_vel_target")
            and callable(getattr(obj, "set_joint_position_target", None))
            and callable(getattr(obj, "set_joint_velocity_target", None))
        ):
            channels.append("joint_target")
        return tuple(channels)
