"""Fail-closed adapter for Isaac PhysX cooked-shape property queries.

This module deliberately sits below ``goal_native_occupancy``.  It records the
property-query callbacks and their provenance; it does not infer a footprint,
replace missing cooked shapes with a profile, or certify an occupancy snapshot.
The Isaac imports are deferred so the callback contract can be tested with a
small injected interface in the normal ``env_isaaclab`` Python test runner.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any, Callable, Mapping


_VALID_RESULTS = {"VALID", "PhysxPropertyQueryResult.VALID"}
_FINISHED_REQUIRED = True


def _finite(value: Any, *, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _vec(value: Any, size: int, *, label: str) -> list[float]:
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError(f"{label} must have {size} components")
    return [_finite(item, label=label) for item in value]


def _result_name(value: Any) -> str:
    if value is None:
        return "MISSING"
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


def _is_valid(value: Any) -> bool:
    return _result_name(value) in _VALID_RESULTS or _result_name(value).endswith(".VALID")


def _path_from_response(response: Any, decoder: Callable[[Any], str] | None) -> str:
    if decoder is not None:
        path = decoder(getattr(response, "path_id", None))
    else:
        path = getattr(response, "path", None)
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise ValueError("property query callback must resolve an absolute collider prim path")
    return path


def _validate_context(context: Mapping[str, Any]) -> dict[str, Any]:
    required = ("scene_id", "snapshot_id", "source_id", "stage_identifier",
                "physics_step", "frame_id", "physx_view_frame", "stage_cache_id",
                "configuration_revision")
    if not isinstance(context, Mapping):
        raise ValueError("query provenance context is required")
    for key in required:
        value = context.get(key)
        if not isinstance(value, (str, int)) or value == "":
            raise ValueError(f"missing query provenance: {key}")
    if type(context["physics_step"]) is not int or context["physics_step"] < 0:
        raise ValueError("physics_step must be a nonnegative integer")
    if type(context["stage_cache_id"]) is not int or context["stage_cache_id"] < 0:
        raise ValueError("stage_cache_id must be a nonnegative integer")
    if context["frame_id"] != "USD_world" or context["physx_view_frame"] != "USD_world":
        raise ValueError("property query must be expressed in USD_world")
    if context.get("readback_frozen") is not True or context.get("kinematics_current") is not True:
        raise ValueError("property query requires frozen current kinematics")
    return deepcopy(dict(context))


def _collider_record(response: Any, decoder: Callable[[Any], str] | None) -> dict[str, Any]:
    result = _result_name(getattr(response, "result", None))
    path = _path_from_response(response, decoder)
    if not _is_valid(getattr(response, "result", None)):
        raise ValueError(f"collider query failed for {path}: {result}")
    minimum = _vec(getattr(response, "aabb_local_min", None), 3, label="aabb_local_min")
    maximum = _vec(getattr(response, "aabb_local_max", None), 3, label="aabb_local_max")
    if any(lo > hi for lo, hi in zip(minimum, maximum)):
        raise ValueError(f"inverted cooked local AABB for {path}")
    position = _vec(getattr(response, "local_pos", None), 3, label="local_pos")
    rotation = _vec(getattr(response, "local_rot", None), 4, label="local_rot")
    norm = math.sqrt(sum(item * item for item in rotation))
    if abs(norm - 1.0) > 1e-4:
        raise ValueError(f"non-unit cooked local rotation for {path}")
    volume = _finite(getattr(response, "volume", None), label="volume")
    if volume <= 0:
        raise ValueError(f"nonpositive cooked volume for {path}")
    # Preserve every callback, including repeated paths from distinct shapes;
    # the caller must decide whether the backend represents compound geometry.
    return {
        "path": path,
        "result": result,
        "aabb_local_min_m": minimum,
        "aabb_local_max_m": maximum,
        "local_pos_m": position,
        "local_rot_xyzw": rotation,
        "volume_m3": volume,
    }


@dataclass
class _Collector:
    context: dict[str, Any]
    expected_body_path: str
    decoder: Callable[[Any], str] | None
    expected_stage_id: int
    rigid_callbacks: list[dict[str, Any]] = field(default_factory=list)
    collider_callbacks: list[dict[str, Any]] = field(default_factory=list)
    callback_errors: list[str] = field(default_factory=list)
    finished_callbacks: int = 0

    def rigid_body(self, response: Any) -> None:
        result = _result_name(getattr(response, "result", None))
        if getattr(response, "stage_id", self.expected_stage_id) != self.expected_stage_id:
            self.callback_errors.append("foreign_stage_in_rigid_body_callback")
            return
        if not _is_valid(getattr(response, "result", None)):
            self.callback_errors.append(f"rigid_body_query_failed:{result}")
            return
        body_path = _path_from_response(response, self.decoder)
        if body_path != self.expected_body_path:
            self.callback_errors.append(f"foreign_body_in_rigid_body_callback:{body_path}")
            return
        self.rigid_callbacks.append({
            "result": result,
            "body_path": self.expected_body_path,
            "mass_kg": _finite(getattr(response, "mass", None), label="mass"),
        })

    def collider(self, response: Any) -> None:
        try:
            if getattr(response, "stage_id", self.expected_stage_id) != self.expected_stage_id:
                raise ValueError("foreign_stage_in_collider_callback")
            record = _collider_record(response, self.decoder)
            if not (record["path"] == self.expected_body_path
                    or record["path"].startswith(self.expected_body_path + "/")):
                raise ValueError(f"foreign_collider_path:{record['path']}")
            self.collider_callbacks.append(record)
        except (TypeError, ValueError) as exc:
            self.callback_errors.append(str(exc))

    def finished(self) -> None:
        self.finished_callbacks += 1


def collect_property_query(
    query_interface: Any,
    *,
    query_kwargs: Mapping[str, Any],
    context: Mapping[str, Any],
    expected_body_path: str,
    path_decoder: Callable[[Any], str] | None = None,
    timeout_ms: int = 2000,
) -> dict[str, Any]:
    """Run one injected PhysX property query and return auditable evidence.

    ``query_interface`` is the object returned by Isaac's
    ``get_physx_property_query_interface``. ``query_kwargs`` must contain the
    stage and prim identity selected by the caller; this function adds the
    callback functions, mode-independent timeout, and completion callback.
    Every invalid response, exception, missing completion callback, duplicate
    completion, or empty callback inventory returns ``blocked``.
    """

    try:
        provenance = _validate_context(context)
        if (not isinstance(expected_body_path, str) or not expected_body_path.startswith("/")
                or expected_body_path == "/"):
            raise ValueError("expected body prim path required")
        if type(timeout_ms) is not int or not 0 < timeout_ms <= 120_000:
            raise ValueError("timeout_ms outside bounded range")
        if not hasattr(query_interface, "query_prim"):
            raise ValueError("PhysX property query interface unavailable")
        expected_stage_id = kwargs_stage_id = dict(query_kwargs).get("stage_id")
        if type(expected_stage_id) is not int or expected_stage_id != provenance["stage_cache_id"]:
            raise ValueError("query stage_id must equal context stage_cache_id")
        collector = _Collector(provenance, expected_body_path, path_decoder, expected_stage_id)
        kwargs = dict(query_kwargs)
        kwargs.update({"rigid_body_fn": collector.rigid_body,
                       "collider_fn": collector.collider,
                       "finished_fn": collector.finished,
                       "timeout_ms": timeout_ms})
        query_interface.query_prim(**kwargs)
    except (TypeError, ValueError, RuntimeError, AttributeError) as exc:
        return {
            "status": "blocked",
            "provenance": deepcopy(dict(context)) if isinstance(context, Mapping) else None,
            "body_path": expected_body_path,
            "callbacks": {"rigid_body": [], "collider": [], "finished": 0},
            "blockers": [f"query_setup_or_execution_error:{type(exc).__name__}:{exc}"],
        }

    blockers = list(collector.callback_errors)
    if collector.finished_callbacks != 1:
        blockers.append(f"finished_callback_count:{collector.finished_callbacks}")
    if not collector.rigid_callbacks:
        blockers.append("missing_valid_rigid_body_callback")
    if not collector.collider_callbacks:
        blockers.append("missing_valid_collider_callback")
    if collector.finished_callbacks != 1:
        blockers.append("query_incomplete_or_timeout")
    return {
        "status": "completed" if not blockers else "blocked",
        "provenance": deepcopy(collector.context),
        "body_path": expected_body_path,
        "callbacks": {
            "rigid_body": deepcopy(collector.rigid_callbacks),
            "collider": deepcopy(collector.collider_callbacks),
            "finished": collector.finished_callbacks,
        },
        "callback_counts": {
            "rigid_body": len(collector.rigid_callbacks),
            "collider": len(collector.collider_callbacks),
        },
        "blockers": sorted(set(blockers)),
        "shape_coverage": "complete_for_this_query" if not blockers else "unknown",
        "measurement_authenticity_validated": False,
    }


def request_convex_collision_representation(
    request_interface: Any,
    *,
    stage_id: Any,
    prim_id: Any,
    finished_fn: Callable[[], None] | None = None,
    error_fn: Callable[[Any], None] | None = None,
    pump_update: Callable[[], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    deadline_s: float = 5.0,
) -> dict[str, Any]:
    """Keep convex cooking requests explicit and fail closed.

    Isaac versions expose this helper through different bindings.  The caller
    supplies the already-resolved request interface and callbacks; this small
    wrapper records whether the request was accepted and never treats an
    accepted asynchronous request as cooked geometry.
    """

    method = getattr(request_interface, "request_convex_collision_representation", None)
    if method is None:
        return {"status": "blocked", "blockers": ["convex_request_api_unavailable"]}
    state = {"finished": 0, "errors": [], "accepted": False, "result": None,
             "convex_count": 0}

    def done() -> None:
        state["finished"] += 1
        if finished_fn is not None:
            finished_fn()

    def error(value: Any) -> None:
        state["errors"].append(_result_name(value))
        if error_fn is not None:
            error_fn(value)

    def result_callback(result: Any, convexes: Any) -> None:
        state["result"] = _result_name(result)
        try:
            state["convex_count"] = len(convexes)
        except TypeError:
            state["errors"].append("convex_result_is_not_sized")
            return
        if state["result"] not in {"RESULT_VALID", "PhysxCollisionRepresentationResult.RESULT_VALID"}:
            error(result)
        elif state["convex_count"] <= 0:
            error("empty_convex_representation")
        done()

    try:
        state["task"] = method(stage_id=stage_id, collision_prim_id=prim_id,
                                run_asynchronously=True, on_result=result_callback)
        state["accepted"] = True
    except (TypeError, RuntimeError, AttributeError) as exc:
        state["errors"].append(f"{type(exc).__name__}:{exc}")
    blockers = list(state["errors"])
    if not state["accepted"]:
        blockers.append("convex_request_not_accepted")
    if state["finished"] != 1:
        if pump_update is not None and monotonic is not None:
            started = monotonic()
            deadline = started + deadline_s
            while state["finished"] == 0 and monotonic() < deadline:
                pump_update()
        if state["finished"] != 1:
            cancel = getattr(request_interface, "cancel_collision_representation_task", None)
            if cancel is not None and state.get("task") is not None:
                cancel(state["task"], invoke_callback=False)
            blockers.append("convex_request_completion_unverified")
    return {
        "status": "accepted_only" if not blockers else "blocked",
        "accepted": state["accepted"],
        "finished_callbacks": state["finished"],
        "result": state["result"],
        "convex_count": state["convex_count"],
        "blockers": sorted(set(blockers)),
        "cooked_geometry_validated": False,
    }
