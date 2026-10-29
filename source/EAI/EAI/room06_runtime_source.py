"""Room06 source backed only by host skill sessions and the logical ledger."""
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from EAI.room06_runtime_hook import CallbackRoom06RuntimeHookSource
from benchmark.records_v3 import canonical_digest
from benchmark.room06_task_instance_v2 import logical_object_definitions
from benchmark.room06_task_runtime_v1 import normalize_room06_task_setup
from task.runtime.logical_objects import LogicalObjectRuntime


class Room06RuntimeSourceError(RuntimeError):
    """Missing or inconsistent host runtime bindings."""


def create_room06_runtime_source(*, base_env: Any, package: Mapping[str, Any],
                                instance_agent_map: Mapping[str, str], **_: Any):
    if not isinstance(package, Mapping):
        raise Room06RuntimeSourceError("package must be an object")
    frozen = deepcopy(dict(package))
    body = dict(frozen)
    if body.pop("package_digest", None) != canonical_digest(body):
        raise Room06RuntimeSourceError("package_digest does not bind the task package")
    try:
        normalize_room06_task_setup(frozen["task_setup"], frozen["task_object_bindings"])
        objects = logical_object_definitions(frozen["task_setup"])
    except (ValueError, KeyError) as exc:
        raise Room06RuntimeSourceError(str(exc)) from exc
    pinned = canonical_digest(frozen)
    agents = dict(instance_agent_map)

    def require_binding(kwargs):
        candidate = kwargs.get("package")
        if not isinstance(candidate, Mapping) or canonical_digest(candidate) != pinned:
            raise Room06RuntimeSourceError("task package changed before runtime observation")
        if kwargs.get("instance_agent_map") != agents:
            raise Room06RuntimeSourceError("room06 roster binding changed")
        runtime = getattr(base_env, "logical_object_runtime", None)
        if not isinstance(runtime, LogicalObjectRuntime):
            raise Room06RuntimeSourceError("host logical_object_runtime is not registered")
        if runtime.scene_id != "room_06" or runtime.robot_ids != tuple(sorted(agents)):
            raise Room06RuntimeSourceError("logical ledger scene/roster differs from the package")
        if runtime.definitions != {row["entity_id"]: row for row in objects}:
            raise Room06RuntimeSourceError("logical ledger object definitions differ from the package")
        return runtime

    def object_bindings(**kwargs):
        require_binding(kwargs)
        return deepcopy(objects)

    def collect_room06(**kwargs):
        require_binding(kwargs)
        driver = getattr(base_env, "room06_task_driver", None)
        if not callable(getattr(driver, "collect_room06", None)):
            raise Room06RuntimeSourceError("room06_task_driver is not registered; refusing synthetic task execution")
        collector = kwargs.get("collector")
        if collector is None:
            raise Room06RuntimeSourceError("room06 collector is unavailable")
        result = driver.collect_room06(package=deepcopy(frozen), instance_agent_map=dict(agents), collector=collector)
        if not isinstance(result, Mapping):
            raise Room06RuntimeSourceError("room06 task driver returned no source result")
        return dict(result)

    return CallbackRoom06RuntimeHookSource(object_bindings=object_bindings, collect_room06=collect_room06)


__all__ = ["Room06RuntimeSourceError", "create_room06_runtime_source"]
