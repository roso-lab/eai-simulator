"""Reference runtime providers for Warehouse and Modern House evidence.

The providers in this module adapt injected live detector, dock-service, pose,
and path backends to :class:`LiveSceneDomainCollector`.  They only emit
replayable claims.  Business acceptance remains in ``domain_evaluator.py``;
these providers neither read its private contract nor return success or
validation decisions.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable, Mapping


_FORBIDDEN_OUTPUT_KEYS = {
    "business_success", "domain_contract", "ground_truth", "hidden_domain_contract",
    "hidden_evaluator", "object_ids", "success", "validated",
}
_DETECTION_KEYS = {
    "detection_id", "detector_source", "detector_version", "target_id",
    "estimated_position_m", "confidence", "timestamp_s", "item_code",
}


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _finite(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _position(value: Any, label: str, *, dimensions: int = 3) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) < dimensions
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in value)):
        raise ValueError(f"{label} must contain at least {dimensions} finite coordinates")
    return [float(item) for item in value[:dimensions]]


def _reject_forbidden_output(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_OUTPUT_KEYS & set(value)
        if forbidden:
            raise ValueError(f"{label} contains forbidden decision/truth fields: {sorted(forbidden)}")
        for key, nested in value.items():
            _reject_forbidden_output(nested, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_forbidden_output(nested, f"{label}[{index}]")


def _backend(backend: Any, method: str, context: Mapping[str, Any]) -> Any:
    callback = getattr(backend, method, None)
    if not callable(callback):
        callback = backend if callable(backend) else None
    if callback is None:
        raise ValueError(f"backend must be callable or expose {method}()")
    return callback(context)


def _bind_backend(backend: Any, context: Mapping[str, Any]) -> None:
    callback = getattr(backend, "bind_runtime", None)
    if callable(callback):
        callback(dict(context))


def _binding(context: Any, scene_key: str) -> Mapping[str, Any]:
    if (not isinstance(context, Mapping)
            or context.get("schema") != "eai.benchmark.domain_runtime_binding_v1"
            or context.get("scene_key") != scene_key):
        raise ValueError("invalid reference-provider runtime binding")
    if {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(context):
        raise ValueError("reference provider must not receive a hidden evaluator contract")
    public_task = context.get("public_task")
    if not isinstance(public_task, Mapping):
        raise ValueError("reference provider requires the public task")
    scene = public_task.get("scene")
    if not isinstance(scene, Mapping) or scene.get("scene_key") != scene_key:
        raise ValueError("public task scene does not match reference provider")
    return context


def _frame_context(context: Any, scene_key: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if (not isinstance(context, Mapping)
            or context.get("schema") != "eai.benchmark.domain_frame_v1"
            or context.get("scene_key") != scene_key):
        raise ValueError("invalid reference-provider frame context")
    observation = context.get("observation")
    if not isinstance(observation, Mapping):
        raise ValueError("frame context requires its published observation")
    observation_id = _identifier(context.get("observation_id"), "frame observation_id")
    if observation.get("observation_id") != observation_id:
        raise ValueError("frame context and observation identifiers differ")
    observed_at = _finite(observation.get("timestamp_s"), "observation timestamp_s")
    valid_until = _finite(observation.get("valid_until_s"), "observation valid_until_s")
    processed_at = _finite(context.get("time_s"), "frame processing time_s")
    if not observed_at <= processed_at <= valid_until:
        raise ValueError("frame processing is outside observation validity")
    quality = observation.get("quality")
    config = observation.get("sensor_config")
    digest = config.get("frame_sha256") if isinstance(config, Mapping) else None
    if (not isinstance(quality, Mapping) or quality.get("frame_acquired") is not True
            or not isinstance(digest, str)):
        raise ValueError("frame lacks captured observation provenance")
    _sha256(digest, "observation frame_sha256")
    if context.get("frame") is None:
        raise ValueError("frame pixels are unavailable")
    _identifier(context.get("node_id"), "frame node_id")
    return context, observation


def _collection_context(context: Any, scene_key: str) -> Mapping[str, Any]:
    if (not isinstance(context, Mapping)
            or context.get("schema") != "eai.benchmark.domain_collector_context_v1"
            or context.get("scene_key") != scene_key):
        raise ValueError("invalid reference-provider collection context")
    if context.get("phase") != "episode_finalized":
        raise ValueError("reference evidence is available only after episode finalization")
    _finite(context.get("time_s"), "collection time_s")
    return context


def _public_objectives(public_task: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = public_task.get("objectives")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("public task objectives must be a list of objects")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identifier = _identifier(row.get("id"), "public objective id")
        if identifier in result:
            raise ValueError("public objective ids must be unique")
        result[identifier] = row
    return result


class ReferenceFrameDetector:
    """Strict adapter for a real RGB/RGB-D detector backend.

    The backend is invoked only for configured scan nodes and receives pixels,
    observation provenance, and public runtime references.  It must return
    ordinary detector claims accepted by ``ObservationStore``.
    """

    def __init__(self, scene_key: str, *, backend: Callable[[Mapping[str, Any]], Any],
                 scan_node_ids: set[str] | tuple[str, ...] | list[str]):
        if scene_key not in {"warehouse_nvd", "modern_house_nvd"}:
            raise ValueError("reference detector supports Warehouse and Modern House")
        if not callable(backend) and not callable(getattr(backend, "detect", None)):
            raise ValueError("detector backend must be callable or expose detect()")
        if (not isinstance(scan_node_ids, (set, tuple, list)) or not scan_node_ids
                or any(not isinstance(item, str) or not item for item in scan_node_ids)):
            raise ValueError("scan_node_ids must contain nonempty objective ids")
        self.scene_key = scene_key
        self.backend = backend
        self.scan_node_ids = frozenset(scan_node_ids)
        self._bound = False

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        if self._bound:
            raise ValueError("reference detector is already bound")
        context = _binding(context, self.scene_key)
        objectives = _public_objectives(context["public_task"])
        missing = self.scan_node_ids - set(objectives)
        if missing:
            raise ValueError(f"detector scan objectives are absent: {sorted(missing)}")
        _bind_backend(self.backend, {
            "schema": "eai.benchmark.reference_detector_binding_v1",
            "scene_key": self.scene_key,
            "public_task": deepcopy(dict(context["public_task"])),
        })
        self._bound = True

    def __call__(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self._bound:
            raise ValueError("reference detector is not bound to a runtime")
        context, observation = _frame_context(context, self.scene_key)
        if context["node_id"] not in self.scan_node_ids:
            return []
        request = {
            "schema": "eai.benchmark.reference_detector_frame_v1",
            "scene_key": self.scene_key,
            "observation_id": context["observation_id"],
            "observation": deepcopy(dict(observation)),
            "frame": context["frame"],
            "node_id": context["node_id"],
            "time_s": float(context["time_s"]),
        }
        rows = _backend(self.backend, "detect", request)
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise ValueError("detector backend must return a list of claim objects")
        normalized = []
        for row in rows:
            _reject_forbidden_output(row, "detector claim")
            unknown = set(row) - _DETECTION_KEYS
            missing = (_DETECTION_KEYS - {"item_code"}) - set(row)
            if unknown or missing:
                raise ValueError(
                    f"detector claim fields are invalid; missing={sorted(missing)}, "
                    f"unknown={sorted(unknown)}")
            claim = deepcopy(dict(row))
            for key in ("detection_id", "detector_source", "detector_version", "target_id"):
                _identifier(claim[key], key)
            if "item_code" in claim:
                _identifier(claim["item_code"], "item_code")
            claim["estimated_position_m"] = _position(
                claim["estimated_position_m"], "estimated_position_m")
            timestamp = _finite(claim["timestamp_s"], "detection timestamp_s")
            if not observation["timestamp_s"] <= timestamp <= context["time_s"]:
                raise ValueError("detection timestamp is outside the observed frame window")
            claim["timestamp_s"] = timestamp
            confidence = claim["confidence"]
            if confidence is not None:
                confidence = _finite(confidence, "detection confidence")
                if confidence > 1:
                    raise ValueError("detection confidence must be in [0, 1]")
                claim["confidence"] = confidence
            normalized.append(claim)
        return normalized


class WarehouseDockReferenceProvider:
    """Create dock state and handoff claims from live service and pose backends."""

    scene_key = "warehouse_nvd"

    def __init__(self, *, service_backend: Any, object_pose_backend: Any,
                 service_source: str = "benchmark_dock_controller",
                 dock_node_id: str = "dock_confirmation_hold",
                 target_ref: str = "outbound_dock_zone", max_pose_age_s: float = 0.25):
        self.service_backend = service_backend
        self.object_pose_backend = object_pose_backend
        self.service_source = _identifier(service_source, "service_source")
        self.dock_node_id = _identifier(dock_node_id, "dock_node_id")
        self.target_ref = _identifier(target_ref, "target_ref")
        self.max_pose_age_s = _finite(max_pose_age_s, "max_pose_age_s")
        if self.max_pose_age_s == 0:
            raise ValueError("max_pose_age_s must be positive")
        self._events: list[dict[str, Any]] = []
        self._bound = False

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        if self._bound:
            raise ValueError("dock provider is already bound")
        context = _binding(context, self.scene_key)
        objectives = _public_objectives(context["public_task"])
        objective = objectives.get(self.dock_node_id)
        if objective is None or objective.get("target_ref") != self.target_ref:
            raise ValueError("dock observation objective is absent or targets another region")
        backend_context = {
            "schema": "eai.benchmark.warehouse_dock_binding_v1",
            "scene_key": self.scene_key,
            "base_env": context.get("base_env"),
            "public_task": deepcopy(dict(context["public_task"])),
            "service_source": self.service_source,
            "target_ref": self.target_ref,
        }
        _bind_backend(self.service_backend, backend_context)
        _bind_backend(self.object_pose_backend, backend_context)
        self._bound = True

    def process_frame(self, context: Mapping[str, Any]) -> None:
        if not self._bound:
            raise ValueError("dock provider is not bound to a runtime")
        context, observation = _frame_context(context, self.scene_key)
        if context["node_id"] != self.dock_node_id:
            return
        if observation.get("target_ref") != self.target_ref:
            raise ValueError("dock frame targets a different operational region")
        if self._events:
            # Wait objectives may publish several coverage frames.  The first
            # accepted receipt is immutable evidence; later frames cannot
            # replace it or turn normal sampling into a sticky adapter error.
            return
        request = {
            "schema": "eai.benchmark.warehouse_dock_handoff_request_v1",
            "scene_key": self.scene_key,
            "service_source": self.service_source,
            "target_ref": self.target_ref,
            "observation_id": context["observation_id"],
            "observation": deepcopy(dict(observation)),
            "frame": context["frame"],
            "node_id": context["node_id"],
            "time_s": float(context["time_s"]),
        }
        response = _backend(self.service_backend, "issue_handoff", request)
        if response is None:
            return
        if not isinstance(response, Mapping):
            raise ValueError("dock service backend must return an object or None")
        _reject_forbidden_output(response, "dock service response")
        if set(response) != {
                "source", "target_ref", "observation_id", "accepted_object_ids",
                "state", "receipt"}:
            raise ValueError("dock service response has incomplete or undeclared fields")
        if (response["source"] != self.service_source
                or response["target_ref"] != self.target_ref
                or response["observation_id"] != context["observation_id"]):
            raise ValueError("dock service response is not bound to this source, target, and observation")
        accepted = response["accepted_object_ids"]
        if (not isinstance(accepted, list) or len(accepted) != 1
                or not isinstance(accepted[0], str) or not accepted[0]):
            raise ValueError("dock service must identify exactly one accepted runtime object")
        state = response["state"]
        receipt = response["receipt"]
        if (not isinstance(state, Mapping) or set(state) != {
                "id", "state", "time_s", "valid_until_s"}
                or not isinstance(receipt, Mapping) or set(receipt) != {"id", "time_s"}):
            raise ValueError("dock state or receipt schema is incomplete")
        state_id = _identifier(state["id"], "dock state id")
        receipt_id = _identifier(receipt["id"], "dock receipt id")
        if state_id == receipt_id:
            raise ValueError("dock state and receipt ids must differ")
        if state["state"] != "ready":
            raise ValueError("dock service did not observe a ready state")
        state_time = _finite(state["time_s"], "dock state time_s")
        expiry = _finite(state["valid_until_s"], "dock state valid_until_s")
        receipt_time = _finite(receipt["time_s"], "dock receipt time_s")
        if not (state_time <= observation["timestamp_s"] <= receipt_time
                <= context["time_s"] <= observation["valid_until_s"]
                and receipt_time <= expiry):
            raise ValueError("dock state, frame, receipt, or expiry order is invalid")

        pose_request = {
            "schema": "eai.benchmark.warehouse_object_pose_request_v1",
            "scene_key": self.scene_key,
            "object_id": accepted[0],
            "target_ref": self.target_ref,
            "time_s": receipt_time,
        }
        pose = _backend(self.object_pose_backend, "sample_pose", pose_request)
        if not isinstance(pose, Mapping):
            raise ValueError("object pose backend must return an object")
        _reject_forbidden_output(pose, "object pose response")
        if set(pose) != {"object_id", "position_m", "sample_time_s", "source"}:
            raise ValueError("object pose response has incomplete or undeclared fields")
        if pose["object_id"] != accepted[0]:
            raise ValueError("object pose response is bound to another object")
        pose_source = _identifier(pose["source"], "object pose source")
        position = _position(pose["position_m"], "object live position")
        pose_time = _finite(pose["sample_time_s"], "object pose sample_time_s")
        if not pose_time <= receipt_time or receipt_time - pose_time > self.max_pose_age_s:
            raise ValueError("dock object pose is future or stale at handoff")

        frame_sha = observation["sensor_config"]["frame_sha256"]
        events = [{
            "id": state_id,
            "kind": "dock_state",
            "source": self.service_source,
            "target_ref": self.target_ref,
            "state": "ready",
            "time_s": state_time,
            "valid_until_s": expiry,
        }, {
            "id": receipt_id,
            "kind": "dock_handoff",
            "source": self.service_source,
            "dock_state_id": state_id,
            "target_ref": self.target_ref,
            "time_s": receipt_time,
            "accepted_object_ids": list(accepted),
            "observation_id": context["observation_id"],
            "observation_frame_sha256": frame_sha,
            "object_pose_m": position,
            "object_pose_time_s": pose_time,
            "object_pose_source": pose_source,
        }]
        self._events = events

    def collect(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self._bound:
            raise ValueError("dock provider is not bound to a runtime")
        context = _collection_context(context, self.scene_key)
        if self._events and self._events[-1]["time_s"] > context["time_s"]:
            raise ValueError("dock handoff occurs after evidence collection")
        return deepcopy(self._events)


class ModernHousePathReferenceProvider:
    """Sample before/after circulation paths from designated live scan frames."""

    scene_key = "modern_house_nvd"

    def __init__(self, *, path_backend: Any,
                 before_scan_node_id: str = "room_entry_scan",
                 after_scan_node_id: str = "room_condition_scan",
                 closeout_node_id: str = "hall_release",
                 before_target_ref: str = "entry_scan_zone",
                 after_target_ref: str = "room_review_zone"):
        self.path_backend = path_backend
        self.before_scan_node_id = _identifier(before_scan_node_id, "before_scan_node_id")
        self.after_scan_node_id = _identifier(after_scan_node_id, "after_scan_node_id")
        self.closeout_node_id = _identifier(closeout_node_id, "closeout_node_id")
        self.before_target_ref = _identifier(before_target_ref, "before_target_ref")
        self.after_target_ref = _identifier(after_target_ref, "after_target_ref")
        if len({self.before_scan_node_id, self.after_scan_node_id, self.closeout_node_id}) != 3:
            raise ValueError("before, after, and closeout objective ids must differ")
        self._occupancy = None
        self._occupancy_sha256: str | None = None
        self._paths: dict[str, dict[str, Any]] = {}
        self._measurement_status: dict[str, str] = {}
        self._measurement_times: dict[str, float] = {}
        self._closeout_time_s: float | None = None
        self._bound = False

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        if self._bound:
            raise ValueError("house path provider is already bound")
        context = _binding(context, self.scene_key)
        objectives = _public_objectives(context["public_task"])
        expected = {
            self.before_scan_node_id: self.before_target_ref,
            self.after_scan_node_id: self.after_target_ref,
        }
        for node_id, target_ref in expected.items():
            objective = objectives.get(node_id)
            if objective is None or objective.get("target_ref") != target_ref:
                raise ValueError(f"{node_id}: public scan target differs from path provider")
        if self.closeout_node_id not in objectives:
            raise ValueError("public closeout objective is absent")
        ancestors: set[str] = set()
        pending = list(objectives[self.after_scan_node_id].get("prerequisites") or [])
        while pending:
            node_id = pending.pop()
            if node_id in ancestors:
                continue
            if node_id not in objectives:
                raise ValueError("public objective graph contains an unknown prerequisite")
            ancestors.add(node_id)
            pending.extend(objectives[node_id].get("prerequisites") or [])
        if self.closeout_node_id not in ancestors:
            raise ValueError("after scan is not causally downstream of closeout")
        occupancy = context.get("occupancy")
        digest = _sha256(getattr(occupancy, "sha256", None), "occupancy.sha256")
        self._occupancy = occupancy
        self._occupancy_sha256 = digest
        _bind_backend(self.path_backend, {
            "schema": "eai.benchmark.modern_house_path_binding_v1",
            "scene_key": self.scene_key,
            "base_env": context.get("base_env"),
            "occupancy": occupancy,
            "occupancy_sha256": digest,
            "public_task": deepcopy(dict(context["public_task"])),
        })
        self._bound = True

    def record_closeout(self, *, node_id: str, time_s: float) -> dict[str, Any]:
        """Record trusted scheduler completion before accepting an after frame."""
        if not self._bound:
            raise ValueError("house path provider is not bound to a runtime")
        if node_id != self.closeout_node_id:
            raise ValueError("closeout completion names another objective")
        time_s = _finite(time_s, "closeout time_s")
        if self._closeout_time_s is not None:
            raise ValueError("closeout completion is already recorded")
        before = self._paths.get("before")
        if before is not None and time_s < before["time_s"]:
            raise ValueError("closeout completion predates the before scan")
        self._closeout_time_s = time_s
        return {"node_id": node_id, "time_s": time_s}

    def process_event(self, context: Mapping[str, Any]) -> None:
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_runtime_event_v1"
                or context.get("scene_key") != self.scene_key):
            raise ValueError("invalid Modern House runtime event context")
        if context.get("event_type") == "node_completed" \
                and context.get("node_id") == self.closeout_node_id:
            self.record_closeout(
                node_id=context["node_id"], time_s=context.get("time_s"))

    def process_frame(self, context: Mapping[str, Any]) -> None:
        if not self._bound:
            raise ValueError("house path provider is not bound to a runtime")
        context, observation = _frame_context(context, self.scene_key)
        node_id = context["node_id"]
        if node_id == self.before_scan_node_id:
            phase, target_ref = "before", self.before_target_ref
        elif node_id == self.after_scan_node_id:
            phase, target_ref = "after", self.after_target_ref
        else:
            return
        if phase in self._measurement_status:
            raise ValueError(f"{phase} access path is already sampled")
        if observation.get("target_ref") != target_ref:
            raise ValueError(f"{phase} path frame targets a different operational region")
        if phase == "after":
            if self._closeout_time_s is None:
                raise ValueError("after path requires observed closeout completion")
            if observation["timestamp_s"] <= self._closeout_time_s:
                raise ValueError("after path frame must be acquired after closeout")
        elif self._closeout_time_s is not None:
            raise ValueError("before path frame cannot arrive after closeout")
        current_hash = _sha256(
            getattr(self._occupancy, "sha256", None), "current occupancy.sha256")
        if current_hash != self._occupancy_sha256:
            raise ValueError("occupancy hash changed after runtime binding")
        request = {
            "schema": "eai.benchmark.modern_house_path_request_v1",
            "scene_key": self.scene_key,
            "phase": phase,
            "observation_id": context["observation_id"],
            "observation": deepcopy(dict(observation)),
            "frame": context["frame"],
            "node_id": node_id,
            "time_s": float(context["time_s"]),
            "target_ref": target_ref,
            "occupancy_sha256": self._occupancy_sha256,
        }
        response = _backend(self.path_backend, "measure_path", request)
        if not isinstance(response, Mapping):
            raise ValueError("path backend must return a measurement object")
        _reject_forbidden_output(response, "path measurement")
        required = {"observation_id", "occupancy_sha256", "points", "sample_time_s"}
        if not required <= set(response) or set(response) - required - {"measurement_status"}:
            raise ValueError("path measurement has incomplete or undeclared fields")
        measurement_status = response.get("measurement_status", "clear")
        if measurement_status not in {"clear", "blocked"}:
            raise ValueError("path measurement is unknown or has invalid status")
        if (response["observation_id"] != context["observation_id"]
                or response["occupancy_sha256"] != self._occupancy_sha256):
            raise ValueError("path measurement is not bound to this frame and occupancy map")
        sample_time = _finite(response["sample_time_s"], "path sample_time_s")
        if not observation["timestamp_s"] <= sample_time <= context["time_s"]:
            raise ValueError("path measurement time is outside the frame processing window")
        if phase == "after" and sample_time <= self._closeout_time_s:
            raise ValueError("after path measurement does not follow closeout")
        points = response["points"]
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("path measurement needs at least two points")
        normalized = [_position(point, "path point", dimensions=2) for point in points]
        self._measurement_status[phase] = measurement_status
        self._measurement_times[phase] = sample_time
        if phase == "after" and measurement_status == "blocked":
            # A completed native obstruction measurement is not a backend error.
            # The legacy evaluator cannot ingest its 3-D status, so withhold the
            # positive after-path claim. The backend retains the full receipt.
            return
        self._paths[phase] = {
            "observation_id": context["observation_id"],
            "time_s": sample_time,
            "points": normalized,
            "target_ref": target_ref,
        }

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if not self._bound:
            raise ValueError("house path provider is not bound to a runtime")
        context = _collection_context(context, self.scene_key)
        current_hash = _sha256(
            getattr(self._occupancy, "sha256", None), "current occupancy.sha256")
        if current_hash != self._occupancy_sha256:
            raise ValueError("occupancy hash changed before path collection")
        if set(self._measurement_status) != {"before", "after"}:
            raise ValueError("both designated before and after scan frames are required")
        if any(time > context["time_s"] for time in self._measurement_times.values()):
            raise ValueError("after path occurs after evidence collection")
        return {
            "access_paths": deepcopy(self._paths),
            "access_map_sha256": self._occupancy_sha256,
        }


def warehouse_reference_providers(*, detector_backend: Any, dock_service_backend: Any,
                                  object_pose_backend: Any,
                                  detector_scan_node_ids=("rack_overview_scan",),
                                  **dock_options: Any) -> dict[str, Any]:
    """Build keyword arguments accepted by ``LiveSceneDomainCollector``."""
    return {
        "detector": ReferenceFrameDetector(
            "warehouse_nvd", backend=detector_backend,
            scan_node_ids=detector_scan_node_ids),
        "service_provider": WarehouseDockReferenceProvider(
            service_backend=dock_service_backend,
            object_pose_backend=object_pose_backend, **dock_options),
    }


def modern_house_reference_providers(*, detector_backend: Any, path_backend: Any,
                                     detector_scan_node_ids=("furniture_clearance_scan",),
                                     **path_options: Any) -> dict[str, Any]:
    """Build keyword arguments accepted by ``LiveSceneDomainCollector``."""
    return {
        "detector": ReferenceFrameDetector(
            "modern_house_nvd", backend=detector_backend,
            scan_node_ids=detector_scan_node_ids),
        "access_provider": ModernHousePathReferenceProvider(
            path_backend=path_backend, **path_options),
    }


def build_warehouse_reference_collector(public_task: Mapping[str, Any], *,
                                          detector_backend: Any,
                                          dock_service_backend: Any,
                                          object_pose_backend: Any,
                                          **options: Any):
    """Build the complete Warehouse runtime collector from a public task."""
    _binding({
        "schema": "eai.benchmark.domain_runtime_binding_v1",
        "scene_key": "warehouse_nvd", "public_task": public_task,
    }, "warehouse_nvd")
    from .live_domain_collectors import build_live_domain_collector
    domain = public_task.get("domain_requirements")
    if not isinstance(domain, Mapping):
        raise ValueError("Warehouse public task lacks domain requirements")
    return build_live_domain_collector(
        "warehouse_nvd", domain,
        **warehouse_reference_providers(
            detector_backend=detector_backend,
            dock_service_backend=dock_service_backend,
            object_pose_backend=object_pose_backend, **options))


def build_modern_house_reference_collector(public_task: Mapping[str, Any], *,
                                            detector_backend: Any,
                                            path_backend: Any,
                                            **options: Any):
    """Build the complete Modern House runtime collector from a public task."""
    _binding({
        "schema": "eai.benchmark.domain_runtime_binding_v1",
        "scene_key": "modern_house_nvd", "public_task": public_task,
    }, "modern_house_nvd")
    from .live_domain_collectors import build_live_domain_collector
    domain = public_task.get("domain_requirements")
    if not isinstance(domain, Mapping):
        raise ValueError("Modern House public task lacks domain requirements")
    return build_live_domain_collector(
        "modern_house_nvd", domain,
        **modern_house_reference_providers(
            detector_backend=detector_backend,
            path_backend=path_backend, **options))


__all__ = [
    "ModernHousePathReferenceProvider",
    "ReferenceFrameDetector",
    "WarehouseDockReferenceProvider",
    "build_modern_house_reference_collector",
    "build_warehouse_reference_collector",
    "modern_house_reference_providers",
    "warehouse_reference_providers",
]
