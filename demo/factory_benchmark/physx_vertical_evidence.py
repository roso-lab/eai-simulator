"""Strict Factory F-VERT-01 PhysX evidence boundary.

This module is deliberately Isaac-free at import time.  The live adapter
accepts the small duck-typed interfaces exposed by Isaac/PhysX query and
contact views, while the validator accepts only normalized JSON records.

Raycasts identify collision geometry and provide a surface consistency check;
they are never support or contact evidence.  A physical support observation
requires an identified, calibrated contact stream.  Even a complete record
returned by this module is ``observed`` and remains pending independent route
acceptance; it is not a traversal success claim.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable, Mapping, Sequence

from demo.benchmark.vertical_traversal import decode_foot_contact_buffer


PHYSX_EVIDENCE_SCHEMA = "eai.factory_stair_physx_evidence"
PHYSX_EVIDENCE_VERSION = 1
_RAY_LENGTH_M = 2.5
_MAX_RAY_HITS = 256


def _finite_vector(value: Any, size: int, label: str) -> list[float]:
    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain finite coordinates") from exc
    if len(values) != size or not all(math.isfinite(item) for item in values):
        raise ValueError(f"{label} must contain {size} finite coordinates")
    return values


def _path_list(values: Any, label: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{label} must be a nonempty path list")
    paths = tuple(values)
    if (len(set(paths)) != len(paths)
            or any(not isinstance(path, str) or not path.startswith("/")
                   or any(char in path for char in "*?[]") for path in paths)):
        raise ValueError(f"{label} contains unresolved paths")
    return paths


def pending_physx_evidence(reason: str = "isaac_runtime_unavailable", **details: Any) -> dict:
    """Return the explicit no-runtime state used by the compiler and collector."""
    return {
        "schema": PHYSX_EVIDENCE_SCHEMA,
        "version": PHYSX_EVIDENCE_VERSION,
        "status": "pending",
        "validated": False,
        "execution_validated": False,
        "acceptance_status": "pending",
        "reason": reason,
        **deepcopy(details),
    }


def unknown_physx_evidence(reason: str, **details: Any) -> dict:
    """Return an attempted but unusable record without converting it to failure."""
    return {
        "schema": PHYSX_EVIDENCE_SCHEMA,
        "version": PHYSX_EVIDENCE_VERSION,
        "status": "unknown",
        "validated": False,
        "execution_validated": False,
        "acceptance_status": "pending",
        "reason": reason,
        **deepcopy(details),
    }


def _get_hit_field(hit: Any, name: str) -> Any:
    return hit.get(name) if isinstance(hit, Mapping) else getattr(hit, name, None)


def capture_raycast_patch(
    query: Any,
    root_xyz_m: Sequence[float],
    *,
    offsets_xy_m: Sequence[Sequence[float]],
    robot_roots: Sequence[str],
    static_collision_paths: Sequence[str],
    classify: Callable[[Mapping[str, Any]], str],
    ray_length_m: float = _RAY_LENGTH_M,
) -> dict:
    """Capture a normalized downward PhysX ray patch from a scene query.

    ``query`` only needs ``raycast_all(origin, direction, distance, callback)``.
    ``classify`` must resolve the hit against the live USD/PhysX stage and
    return ``static`` for an enabled static collider.  A hit that cannot be
    identity-resolved is unknown, including when a nearer dynamic or robot hit
    would otherwise be skipped.
    """
    try:
        root = _finite_vector(root_xyz_m, 3, "raycast root")
        offsets = [_finite_vector(offset, 2, "raycast offset") for offset in offsets_xy_m]
        robots = _path_list(robot_roots, "robot roots")
        static_paths = _path_list(static_collision_paths, "static collision paths")
        ray_length = float(ray_length_m)
        if not math.isfinite(ray_length) or ray_length <= 0:
            raise ValueError("ray length must be finite and positive")
        if not callable(classify) or not offsets:
            raise ValueError("raycast patch requires offsets and a collider classifier")
    except ValueError as exc:
        return {"status": "unknown", "reason": str(exc), "rays": []}

    rays = []
    for offset in offsets:
        origin = [root[0] + offset[0], root[1] + offset[1], root[2]]
        hits: list[dict] = []
        callback_errors: list[str] = []

        def report(hit: Any) -> bool:
            if len(hits) >= _MAX_RAY_HITS:
                callback_errors.append("raycast_hit_limit")
                return False
            try:
                collision = str(_get_hit_field(hit, "collision") or "")
                rigid_body = str(_get_hit_field(hit, "rigid_body") or "")
                position = _finite_vector(_get_hit_field(hit, "position"), 3, "ray hit position")
                normal = _finite_vector(_get_hit_field(hit, "normal"), 3, "ray hit normal")
                distance = float(_get_hit_field(hit, "distance"))
                if not math.isfinite(distance):
                    raise ValueError("ray hit distance is not finite")
                hits.append({"collision_path": collision, "rigid_body_path": rigid_body,
                             "position_world_m": position, "normal_world": normal,
                             "distance_m": distance})
            except (TypeError, ValueError, OverflowError) as exc:
                callback_errors.append(f"malformed_hit:{exc}")
            return True

        try:
            query.raycast_all(origin, (0.0, 0.0, -1.0), ray_length, report)
        except Exception as exc:  # SDK exceptions must become evidence state, not success.
            return {"status": "unknown", "reason": f"raycast_error:{type(exc).__name__}:{exc}",
                    "rays": rays}
        if callback_errors:
            return {"status": "unknown", "reason": callback_errors[0], "rays": rays}

        candidates = []
        for hit in hits:
            paths = [hit["collision_path"], hit["rigid_body_path"]]
            if not paths[0].startswith("/") and not paths[1].startswith("/"):
                return {"status": "unknown", "reason": "unidentified_hit", "rays": rays}
            if any(path and any(path == root_path or path.startswith(root_path.rstrip("/") + "/")
                                for root_path in robots) for path in paths):
                continue
            if not 0.0 <= hit["distance_m"] <= ray_length:
                return {"status": "unknown", "reason": "invalid_ray_distance", "rays": rays}
            expected_z = origin[2] - hit["distance_m"]
            if abs(hit["position_world_m"][2] - expected_z) > 0.01:
                return {"status": "unknown", "reason": "inconsistent_hit", "rays": rays}
            kind = classify(deepcopy(hit))
            if kind != "static":
                # Do not look through a dynamic/unknown nearer hit to claim a floor.
                return {"status": "unknown", "reason": "nonstatic_or_unverified_collider",
                        "rays": rays}
            if hit["collision_path"] not in static_paths:
                return {"status": "unknown", "reason": "unexpected_static_collider",
                        "rays": rays}
            normal = hit["normal_world"]
            norm = math.sqrt(sum(value * value for value in normal))
            if norm < 1e-9 or normal[2] / norm < math.cos(math.radians(65)):
                return {"status": "unknown", "reason": "unsupported_surface_normal", "rays": rays}
            candidates.append(hit)
        if not candidates:
            return {"status": "unknown", "reason": "no_static_support_hit", "rays": rays}
        hit = min(candidates, key=lambda row: row["distance_m"])
        rays.append({"origin_xyz_m": origin, "status": "measured", "hit": hit})

    heights = [row["hit"]["position_world_m"][2] for row in rays]
    return {"status": "observed", "rays": rays,
            "geometry_support_z_m": min(heights),
            "height_spread_m": max(heights) - min(heights),
            "evidence_kind": "physx_scene_query_raycast",
            "contact_evidence": False}


def _to_host(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def capture_contact_sample(
    views: Mapping[str, Any],
    *,
    body_paths: Mapping[str, str],
    static_collision_paths: Sequence[str],
    physics_dt_s: float,
    body_positions_world_m: Mapping[str, Sequence[float]] | None = None,
    capacity: int = 64,
) -> dict:
    """Capture exact per-foot contact views after one physics/control step."""
    try:
        paths = _path_list(static_collision_paths, "static collision paths")
        if (not isinstance(body_paths, Mapping) or not body_paths
                or set(body_paths) != set(views)):
            raise ValueError("contact view identity is incomplete")
        if not math.isfinite(float(physics_dt_s)) or float(physics_dt_s) <= 0:
            raise ValueError("physics dt must be finite and positive")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("contact capacity must be positive")
    except (TypeError, ValueError, OverflowError) as exc:
        return {"status": "unknown", "reason": str(exc), "feet": []}

    feet = []
    for name in body_paths:
        view = views.get(name)
        if view is None or not callable(getattr(view, "get_contact_data", None)):
            return {"status": "unknown", "reason": "missing_contact_view", "feet": feet}
        try:
            decoded = decode_foot_contact_buffer(
                _to_host(view.get_contact_data(dt=float(physics_dt_s))),
                collision_paths=list(paths), capacity=capacity)
        except Exception as exc:
            return {"status": "unknown", "reason": f"contact_view_error:{type(exc).__name__}:{exc}",
                    "feet": feet}
        if decoded.get("status") != "observed":
            return {"status": "unknown", "reason": decoded.get("reason", "invalid_contact_buffer"),
                    "feet": feet}
        foot = {"body_name": str(name), "body_path": body_paths[name], **decoded}
        if body_positions_world_m is not None:
            try:
                foot["body_position_world_m"] = _finite_vector(
                    body_positions_world_m[name], 3, "foot body position")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                return {"status": "unknown", "reason": str(exc), "feet": feet}
        feet.append(foot)
    return {"status": "observed", "feet": feet,
            "evidence_kind": "raw_foot_contact_buffers", "calibrated": False}


class PhysxReferenceCapture:
    """Collect post-step samples without owning Isaac application lifecycle."""

    def __init__(self, *, scenario_sha256: str, asset_sha256: str | None,
                 robot: Mapping[str, Any], runtime: Mapping[str, Any],
                 static_collision_paths: Sequence[str]):
        static_paths = _path_list(static_collision_paths, "static collision paths")
        self.record = {
            "schema": PHYSX_EVIDENCE_SCHEMA, "version": PHYSX_EVIDENCE_VERSION,
            "status": "capturing", "execution_validated": False,
            "acceptance_status": "pending", "scenario_sha256": scenario_sha256,
            "asset_sha256": asset_sha256, "robot": deepcopy(dict(robot)),
            "runtime": deepcopy(dict(runtime)), "static_collision_paths": list(static_paths),
            "samples": [],
            "source": "live_isaac_physx_post_step",
        }

    def append(self, *, step: int, simulation_time_s: float,
               root_state_w: Sequence[float], raycast: Mapping[str, Any],
               contact: Mapping[str, Any], support: Mapping[str, Any] | None = None) -> dict:
        """Append a normalized post-step sample; invalid samples stay unknown."""
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise ValueError("evidence step must be a positive integer")
        if not math.isfinite(float(simulation_time_s)):
            raise ValueError("evidence time must be finite")
        state = _finite_vector(root_state_w, 13, "root state")
        if not isinstance(raycast, Mapping) or not isinstance(contact, Mapping):
            raise ValueError("post-step evidence requires raycast and contact records")
        row = {
            "step": step, "simulation_time_s": float(simulation_time_s),
            "post_step_state_available": True, "root_state_w": state,
            "root_xyz_m": state[:3], "root_quaternion_wxyz": state[3:7],
            "raycast": deepcopy(dict(raycast)), "contact": deepcopy(dict(contact)),
        }
        if support is not None:
            row["support"] = deepcopy(dict(support))
        self.record["samples"].append(row)
        return deepcopy(row)

    def finish(self) -> dict:
        """Freeze the capture; only structural validator output may be observed."""
        self.record["status"] = "captured"
        result = validate_physx_evidence(self.record)
        self.record["validation"] = result
        return deepcopy(self.record)


def _validate_sample(sample: Mapping[str, Any], *, static_paths: tuple[str, ...],
                     body_paths: tuple[str, ...], calibration_id: str | None) -> list[str]:
    errors = []
    if sample.get("post_step_state_available") is not True:
        errors.append("missing_post_step_state")
    try:
        _finite_vector(sample.get("root_xyz_m"), 3, "root position")
        _finite_vector(sample.get("root_quaternion_wxyz"), 4, "root quaternion")
    except ValueError as exc:
        errors.append(str(exc))
    raycast = sample.get("raycast")
    if not isinstance(raycast, Mapping) or raycast.get("status") != "observed":
        errors.append("missing_physx_raycast_patch")
    else:
        rays = raycast.get("rays")
        if not isinstance(rays, list) or not rays:
            errors.append("empty_physx_raycast_patch")
        else:
            for ray in rays:
                hit = ray.get("hit") if isinstance(ray, Mapping) else None
                if not isinstance(hit, Mapping) or hit.get("collision_path") not in static_paths:
                    errors.append("raycast_static_identity_missing")
                    break
    contact = sample.get("contact")
    if not isinstance(contact, Mapping) or contact.get("status") != "observed":
        errors.append("missing_physx_contact_sample")
    else:
        feet = contact.get("feet")
        if not isinstance(feet, list) or tuple(foot.get("body_path") for foot in feet) != body_paths:
            errors.append("foot_contact_identity_changed")
        else:
            for foot in feet:
                if not foot.get("contacts"):
                    continue
                for hit in foot["contacts"]:
                    if hit.get("collision_path") not in static_paths:
                        errors.append("contact_static_identity_missing")
                        break
            if not any(foot.get("contacts") for foot in feet):
                errors.append("no_supporting_foot_contact")
    support = sample.get("support")
    if not isinstance(support, Mapping) or support.get("status") != "measured":
        errors.append("missing_calibrated_support")
    else:
        if support.get("calibrated") is not True or support.get("evidence_kind") != "calibrated_foot_contact":
            errors.append("support_is_not_calibrated_foot_contact")
        if not isinstance(support.get("calibration_id"), str) or not support["calibration_id"]:
            errors.append("missing_support_calibration_id")
        elif calibration_id is not None and support["calibration_id"] != calibration_id:
            errors.append("support_calibration_id_changed")
        try:
            _finite_vector([support["support_z_m"]], 1, "support height")
        except (KeyError, ValueError):
            errors.append("invalid_support_height")
    return errors


def validate_physx_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    expected_scenario_sha256: str | None = None,
    expected_asset_sha256: str | None = None,
    expected_robot_id: str | None = None,
) -> dict:
    """Validate a live evidence record without declaring traversal success."""
    if evidence is None:
        return pending_physx_evidence()
    if not isinstance(evidence, Mapping):
        return unknown_physx_evidence("evidence_record_is_not_mapping")
    if evidence.get("schema") != PHYSX_EVIDENCE_SCHEMA or evidence.get("version") != PHYSX_EVIDENCE_VERSION:
        return unknown_physx_evidence("unsupported_physx_evidence_schema")
    status = evidence.get("status")
    if status == "pending":
        return pending_physx_evidence(evidence.get("reason", "evidence_pending"))
    if status not in {"captured", "observed"}:
        return unknown_physx_evidence("evidence_capture_not_complete")
    if evidence.get("source") != "live_isaac_physx_post_step":
        return unknown_physx_evidence("evidence_source_is_not_live_isaac")
    runtime = evidence.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("isaac_runtime") is not True:
        return unknown_physx_evidence("isaac_runtime_provenance_missing")
    for key in ("physics_dt_s", "control_dt_s"):
        try:
            if not math.isfinite(float(runtime[key])) or float(runtime[key]) <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            return unknown_physx_evidence(f"invalid_runtime_{key}")
    if expected_scenario_sha256 is not None and evidence.get("scenario_sha256") != expected_scenario_sha256:
        return unknown_physx_evidence("scenario_hash_mismatch")
    if expected_asset_sha256 is not None and evidence.get("asset_sha256") != expected_asset_sha256:
        return unknown_physx_evidence("asset_hash_mismatch")
    robot = evidence.get("robot")
    if not isinstance(robot, Mapping) or not isinstance(robot.get("id"), str):
        return unknown_physx_evidence("robot_provenance_missing")
    if expected_robot_id is not None and robot.get("id") != expected_robot_id:
        return unknown_physx_evidence("robot_identity_mismatch")
    paths = robot.get("foot_body_paths")
    static_paths = evidence.get("static_collision_paths") or robot.get("static_collision_paths")
    try:
        body_paths = _path_list(paths, "foot body paths")
        static_paths = _path_list(static_paths, "static collision paths")
    except ValueError as exc:
        return unknown_physx_evidence(str(exc))
    samples = evidence.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        return pending_physx_evidence("insufficient_post_step_samples")
    previous_step = 0
    previous_time = -math.inf
    calibration_id = None
    errors = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            errors.append("sample_is_not_mapping")
            continue
        step = sample.get("step")
        timestamp = sample.get("simulation_time_s")
        if isinstance(step, bool) or not isinstance(step, int) or step <= previous_step:
            errors.append("sample_steps_not_strictly_increasing")
        else:
            previous_step = step
        try:
            timestamp = float(timestamp)
            if not math.isfinite(timestamp) or timestamp <= previous_time:
                errors.append("sample_times_not_strictly_increasing")
            previous_time = timestamp
        except (TypeError, ValueError, OverflowError):
            errors.append("invalid_sample_time")
        support = sample.get("support")
        if calibration_id is None and isinstance(support, Mapping):
            calibration_id = support.get("calibration_id")
        errors.extend(_validate_sample(sample, static_paths=static_paths,
                                       body_paths=body_paths, calibration_id=calibration_id))
    if errors:
        return unknown_physx_evidence(";".join(dict.fromkeys(errors)),
                                      sample_count=len(samples))
    return {
        "schema": PHYSX_EVIDENCE_SCHEMA, "version": PHYSX_EVIDENCE_VERSION,
        "status": "observed", "validated": True, "execution_validated": False,
        "acceptance_status": "pending", "sample_count": len(samples),
        "calibration_id": calibration_id,
        "limitations": [
            "raycast evidence identifies live colliders but is not contact evidence",
            "contact evidence is support input and does not prove connected traversal",
            "independent reference route acceptance and target dwell remain pending",
        ],
    }


__all__ = [
    "PHYSX_EVIDENCE_SCHEMA", "PHYSX_EVIDENCE_VERSION", "PhysxReferenceCapture",
    "capture_contact_sample", "capture_raycast_patch", "pending_physx_evidence",
    "unknown_physx_evidence", "validate_physx_evidence",
]
