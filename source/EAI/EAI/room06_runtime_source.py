"""Production registration seam for the room06 task driver.

This module deliberately contains no navigation, manipulation, or success
simulation.  The simulator owns the live scene and a case runner must attach a
``room06_task_driver`` to the environment.  The driver is the only component
allowed to issue skills and report completed sessions.  The source verifies
the generated USD bodies and forwards the driver's observer callbacks to the
fail-closed collector in :mod:`EAI.room06_runtime_hook`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .room06_runtime_hook import CallbackRoom06RuntimeHookSource, HOOK_SOURCE_SCHEMA


class Room06RuntimeSourceError(RuntimeError):
    """Raised when the live room06 driver or object bindings are incomplete."""


def _task_object_paths(package: Mapping[str, Any]) -> dict[str, str]:
    bindings = package.get("task_object_bindings")
    if not isinstance(bindings, Mapping):
        raise Room06RuntimeSourceError("task_object_bindings are unavailable")
    rows = bindings.get("objects")
    if not isinstance(rows, list) or not rows:
        raise Room06RuntimeSourceError("task_object_bindings.objects must be non-empty")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise Room06RuntimeSourceError("task object binding must be an object")
        entity_id = row.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise Room06RuntimeSourceError("task object binding entity_id is invalid")
        # The case-owned generated objects have stable names under env_0.  A
        # driver may return a more specific path later, but it may not replace
        # this identity with command metadata or a basename guess.
        if entity_id == "book_batch_01":
            path = "/World/envs/env_0/room06_task/book_batch_01"
        elif entity_id == "heavy_object":
            path = "/World/envs/env_0/room06_task/heavy_object"
        else:
            raise Room06RuntimeSourceError(f"unsupported room06 task object: {entity_id}")
        if entity_id in result:
            raise Room06RuntimeSourceError(f"duplicate room06 task object: {entity_id}")
        result[entity_id] = path
    return result


def _require_driver(base_env: Any) -> Any:
    driver = getattr(base_env, "room06_task_driver", None)
    if driver is None:
        raise Room06RuntimeSourceError(
            "room06_task_driver is not registered; refusing synthetic task execution"
        )
    if not callable(getattr(driver, "collect_room06", None)):
        raise Room06RuntimeSourceError(
            "room06_task_driver.collect_room06 is required"
        )
    return driver


def create_room06_runtime_source(
    *,
    base_env: Any,
    package: Mapping[str, Any],
    instance_agent_map: Mapping[str, str],
    **_: Any,
) -> CallbackRoom06RuntimeHookSource:
    """Create the source attached by ``SimulatorLaunchConfig``.

    ``collect_room06`` delegates to the driver, while all event/hold/schema
    validation remains in ``collect_room06_runtime_hook``.  The returned
    object intentionally does not expose a fallback driver.
    """

    if not isinstance(package, Mapping):
        raise Room06RuntimeSourceError("package must be an object")
    paths = _task_object_paths(package)

    def object_path_bindings(**kwargs: Any) -> dict[str, str]:
        if kwargs.get("package") != package:
            # The probe passes a detached copy.  Compare the stable object
            # binding rather than object identity, while still rejecting a
            # changed task package.
            candidate = kwargs.get("package")
            if not isinstance(candidate, Mapping) or _task_object_paths(candidate) != paths:
                raise Room06RuntimeSourceError("task package changed before object binding")
        stage = getattr(base_env, "stage", None)
        if stage is None:
            sim = getattr(base_env, "sim", None)
            stage = getattr(sim, "stage", None)
        if stage is None:
            # Isaac exposes the stage through omni.usd; importing it lazily
            # keeps CPU preflight and unit tests independent of Kit.
            try:
                import omni.usd
                stage = omni.usd.get_context().get_stage()
            except Exception as exc:  # pragma: no cover - native-only branch
                raise Room06RuntimeSourceError("live USD stage is unavailable") from exc
        result: dict[str, str] = {}
        for entity_id, path in paths.items():
            prim = stage.GetPrimAtPath(path)
            if prim is None or not prim.IsValid():
                raise Room06RuntimeSourceError(f"generated task rigid body is unavailable: {path}")
            result[entity_id] = path
        return result

    def collect_room06(**kwargs: Any) -> Mapping[str, Any]:
        driver = _require_driver(base_env)
        if kwargs.get("package") is None or kwargs.get("instance_agent_map") != dict(instance_agent_map):
            raise Room06RuntimeSourceError("room06 driver package or roster binding changed")
        collector = kwargs.get("collector")
        if collector is None:
            raise Room06RuntimeSourceError("room06 collector is unavailable")
        result = driver.collect_room06(
            package=kwargs["package"],
            instance_agent_map=dict(instance_agent_map),
            collector=collector,
        )
        if not isinstance(result, Mapping):
            raise Room06RuntimeSourceError("room06 task driver returned no source result")
        return dict(result)

    source = CallbackRoom06RuntimeHookSource(
        object_path_bindings=object_path_bindings,
        collect_room06=collect_room06,
    )
    if source.schema != HOOK_SOURCE_SCHEMA:  # defensive contract assertion
        raise Room06RuntimeSourceError("room06 source schema is unsupported")
    return source


__all__ = [
    "Room06RuntimeSourceError",
    "create_room06_runtime_source",
]
