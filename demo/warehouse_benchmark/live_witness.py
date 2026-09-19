"""Warehouse-owned live evidence and physical witness boundaries.

This module contains only claim validation.  It never decides business
success, and it never turns a scheduler ACK, a static route, or a configured
USD fixed point into a physical claim.  The optional ``witness_backend`` is
episode-owned and is expected to read the live Isaac session (or an equivalent
trusted runtime) after each simulation step.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


WAREHOUSE_SCENE_KEY = "warehouse_nvd"
LIVE_WITNESS_SCHEMA = "eai.benchmark.warehouse_live_witness_v1"


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return value


def _positive(value: Any, label: str) -> float:
    result = _time(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _point(value: Any, label: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(float(item)) for item in value)):
        raise ValueError(f"{label} must contain three finite coordinates")
    return [float(item) for item in value]


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _public_task(context: Mapping[str, Any]) -> Mapping[str, Any]:
    if (not isinstance(context, Mapping)
            or context.get("schema") != "eai.benchmark.domain_runtime_binding_v1"
            or context.get("scene_key") != WAREHOUSE_SCENE_KEY):
        raise ValueError("invalid Warehouse runtime binding")
    public_task = context.get("public_task")
    if not isinstance(public_task, Mapping):
        raise ValueError("Warehouse runtime binding requires public_task")
    scene = public_task.get("scene")
    if not isinstance(scene, Mapping) or scene.get("scene_key") != WAREHOUSE_SCENE_KEY:
        raise ValueError("Warehouse public task has the wrong scene")
    if {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(public_task):
        raise ValueError("Warehouse witness cannot receive hidden evaluator data")
    return public_task


def _backend_call(backend: Any, method: str, context: Mapping[str, Any]) -> Any:
    callback = getattr(backend, method, None)
    if not callable(callback):
        callback = backend if callable(backend) else None
    if callback is None:
        raise ValueError(f"Warehouse backend must expose {method}() or be callable")
    return callback(deepcopy(dict(context)))


class WarehouseFrameDetector:
    """Local Warehouse adapter for real detector claims."""

    def __init__(self, backend: Any, scan_node_ids):
        if not callable(backend) and not callable(getattr(backend, "detect", None)):
            raise ValueError("detector backend must expose detect() or be callable")
        if (not isinstance(scan_node_ids, (list, tuple, set)) or not scan_node_ids
                or any(not isinstance(item, str) or not item for item in scan_node_ids)):
            raise ValueError("scan_node_ids must contain nonempty objective ids")
        self.backend = backend
        self.scan_node_ids = frozenset(scan_node_ids)
        self._bound = False

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        public_task = _public_task(context)
        objectives = public_task.get("objectives")
        if not isinstance(objectives, list):
            raise ValueError("Warehouse public task objectives must be a list")
        missing = self.scan_node_ids - {
            row.get("id") for row in objectives if isinstance(row, Mapping)}
        if missing:
            raise ValueError(f"detector scan objectives are absent: {sorted(missing)}")
        hook = getattr(self.backend, "bind_runtime", None)
        if callable(hook):
            hook({
                "schema": "eai.benchmark.warehouse_detector_binding_v1",
                "scene_key": WAREHOUSE_SCENE_KEY,
                "public_task": deepcopy(dict(public_task)),
            })
        self._bound = True

    def __call__(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self._bound:
            raise ValueError("Warehouse detector is not bound to a runtime")
        if context.get("node_id") not in self.scan_node_ids:
            return []
        observation = context.get("observation")
        if (not isinstance(observation, Mapping)
                or observation.get("observation_id") != context.get("observation_id")
                or observation.get("quality", {}).get("frame_acquired") is not True):
            raise ValueError("detector frame lacks captured observation provenance")
        digest = observation.get("sensor_config", {}).get("frame_sha256")
        _sha256(digest, "detector frame_sha256")
        request = {
            "schema": "eai.benchmark.warehouse_detector_frame_v1",
            "scene_key": WAREHOUSE_SCENE_KEY,
            "observation_id": context["observation_id"],
            "observation": deepcopy(dict(observation)),
            "frame": context.get("frame"),
            "node_id": context["node_id"],
            "time_s": _time(context.get("time_s"), "detector processing time_s"),
        }
        callback = getattr(self.backend, "detect", None)
        if not callable(callback):
            callback = self.backend if callable(self.backend) else None
        if callback is None:
            raise ValueError("detector backend must expose detect() or be callable")
        # Preserve the live frame object for a detector that uses zero-copy
        # input; the claim rows are copied and validated below.
        rows = callback(request)
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise ValueError("detector backend must return a list of claim objects")
        required = {"detection_id", "detector_source", "detector_version", "target_id",
                    "estimated_position_m", "confidence", "timestamp_s"}
        allowed = required | {"item_code"}
        result = []
        for row in rows:
            if set(row) - allowed or required - set(row):
                raise ValueError("detector claim fields are incomplete or undeclared")
            if set(row) & {"success", "validated", "ground_truth", "object_ids"}:
                raise ValueError("detector claim contains forbidden decision/truth fields")
            claim = deepcopy(dict(row))
            for key in ("detection_id", "detector_source", "detector_version", "target_id"):
                _id(claim[key], key)
            if "item_code" in claim:
                _id(claim["item_code"], "item_code")
            claim["estimated_position_m"] = _point(claim["estimated_position_m"], "estimated_position_m")
            timestamp = _time(claim["timestamp_s"], "detection timestamp_s")
            if not observation["timestamp_s"] <= timestamp <= context["time_s"]:
                raise ValueError("detection timestamp is outside the observed frame window")
            confidence = claim["confidence"]
            if confidence is not None:
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                    raise ValueError("detection confidence must be numeric or null")
                confidence = float(confidence)
                if not 0 <= confidence <= 1 or not math.isfinite(confidence):
                    raise ValueError("detection confidence must be in [0, 1]")
                claim["confidence"] = confidence
            claim["timestamp_s"] = timestamp
            result.append(claim)
        return result


class WarehouseDockReceiptProvider:
    """Issue dock claims only from a current frame and a live object pose."""

    def __init__(self, *, service_backend: Any, object_pose_backend: Any,
                 service_source: str = "benchmark_dock_controller",
                 dock_node_id: str = "dock_confirmation_hold",
                 target_ref: str = "outbound_dock_zone", max_pose_age_s: float = 0.25):
        self.service_backend = service_backend
        self.object_pose_backend = object_pose_backend
        self.service_source = _id(service_source, "service_source")
        self.dock_node_id = _id(dock_node_id, "dock_node_id")
        self.target_ref = _id(target_ref, "target_ref")
        self.max_pose_age_s = _positive(max_pose_age_s, "max_pose_age_s")
        self._bound = False
        self._events: list[dict[str, Any]] = []

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        public_task = _public_task(context)
        objectives = {
            row.get("id"): row for row in public_task.get("objectives", [])
            if isinstance(row, Mapping)
        }
        objective = objectives.get(self.dock_node_id)
        if objective is None or objective.get("target_ref") != self.target_ref:
            raise ValueError("dock observation objective is absent or targets another region")
        backend_context = {
            "schema": "eai.benchmark.warehouse_dock_binding_v1",
            "scene_key": WAREHOUSE_SCENE_KEY,
            "base_env": context.get("base_env"),
            "public_task": deepcopy(dict(public_task)),
            "service_source": self.service_source,
            "target_ref": self.target_ref,
        }
        for backend in (self.service_backend, self.object_pose_backend):
            hook = getattr(backend, "bind_runtime", None)
            if callable(hook):
                hook(deepcopy(backend_context))
        self._bound = True

    def process_frame(self, context: Mapping[str, Any]) -> None:
        if not self._bound:
            raise ValueError("dock provider is not bound to a runtime")
        if context.get("node_id") != self.dock_node_id or self._events:
            return None
        observation = context.get("observation")
        if observation.get("target_ref") != self.target_ref:
            raise ValueError("dock frame targets a different operational region")
        response = _backend_call(self.service_backend, "issue_handoff", {
            "schema": "eai.benchmark.warehouse_dock_handoff_request_v1",
            "scene_key": WAREHOUSE_SCENE_KEY,
            "service_source": self.service_source,
            "target_ref": self.target_ref,
            "observation_id": context["observation_id"],
            "observation": deepcopy(dict(observation)),
            "frame": context.get("frame"),
            "node_id": context["node_id"],
            "time_s": _time(context.get("time_s"), "dock processing time_s"),
        })
        if response is None:
            return None
        if not isinstance(response, Mapping):
            raise ValueError("dock service backend must return an object or None")
        if set(response) != {"source", "target_ref", "observation_id", "accepted_object_ids", "state", "receipt"}:
            raise ValueError("dock service response has incomplete or undeclared fields")
        if (response["source"] != self.service_source
                or response["target_ref"] != self.target_ref
                or response["observation_id"] != context["observation_id"]):
            raise ValueError("dock service response is not bound to this source, target, and observation")
        accepted = response["accepted_object_ids"]
        if not isinstance(accepted, list) or len(accepted) != 1:
            raise ValueError("dock service must identify exactly one accepted runtime object")
        object_id = _id(accepted[0], "accepted object id")
        state, receipt = response["state"], response["receipt"]
        if (not isinstance(state, Mapping) or set(state) != {"id", "state", "time_s", "valid_until_s"}
                or not isinstance(receipt, Mapping) or set(receipt) != {"id", "time_s"}):
            raise ValueError("dock state or receipt schema is incomplete")
        state_id, receipt_id = _id(state["id"], "dock state id"), _id(receipt["id"], "dock receipt id")
        if state_id == receipt_id or state["state"] != "ready":
            raise ValueError("dock state is not a distinct ready state")
        state_time = _time(state["time_s"], "dock state time_s")
        expiry = _time(state["valid_until_s"], "dock state valid_until_s")
        receipt_time = _time(receipt["time_s"], "dock receipt time_s")
        if not (state_time <= observation["timestamp_s"] <= receipt_time <= context["time_s"] <= expiry):
            raise ValueError("dock state, frame, receipt, or expiry order is invalid")
        pose = _backend_call(self.object_pose_backend, "sample_pose", {
            "schema": "eai.benchmark.warehouse_object_pose_request_v1",
            "scene_key": WAREHOUSE_SCENE_KEY, "object_id": object_id,
            "target_ref": self.target_ref, "time_s": receipt_time,
        })
        if not isinstance(pose, Mapping) or set(pose) != {"object_id", "position_m", "sample_time_s", "source"}:
            raise ValueError("object pose backend returned an incomplete record")
        if pose["object_id"] != object_id:
            raise ValueError("object pose response is bound to another object")
        pose_time = _time(pose["sample_time_s"], "object pose sample_time_s")
        if receipt_time - pose_time > self.max_pose_age_s or pose_time > receipt_time:
            raise ValueError("dock object pose is future or stale at handoff")
        frame_sha = observation.get("sensor_config", {}).get("frame_sha256")
        _sha256(frame_sha, "dock observation frame_sha256")
        self._events = [{
            "id": state_id, "kind": "dock_state", "source": self.service_source,
            "target_ref": self.target_ref, "state": "ready", "time_s": state_time,
            "valid_until_s": expiry,
        }, {
            "id": receipt_id, "kind": "dock_handoff", "source": self.service_source,
            "dock_state_id": state_id, "target_ref": self.target_ref,
            "time_s": receipt_time, "accepted_object_ids": [object_id],
            "observation_id": context["observation_id"],
            "observation_frame_sha256": frame_sha,
            "object_pose_m": _point(pose["position_m"], "object pose"),
            "object_pose_time_s": pose_time, "object_pose_source": _id(pose["source"], "object pose source"),
        }]

    def process_event(self, _context: Mapping[str, Any]) -> None:
        if not self._bound:
            raise ValueError("dock provider is not bound to a runtime")

    def collect(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not self._bound:
            raise ValueError("dock provider is not bound to a runtime")
        if context.get("phase") != "episode_finalized":
            raise ValueError("dock evidence is available only after episode finalization")
        return deepcopy(self._events)


class WarehouseLiveWitnessProvider:
    """Validate episode-owned carry, recovery, service-car, and dock witnesses."""

    def __init__(self, public_task: Mapping[str, Any], backend: Any = None):
        if not isinstance(public_task, Mapping):
            raise ValueError("Warehouse public_task must be an object")
        scene = public_task.get("scene")
        if not isinstance(scene, Mapping) or scene.get("scene_key") != WAREHOUSE_SCENE_KEY:
            raise ValueError("Warehouse public_task has the wrong scene")
        if {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(public_task):
            raise ValueError("Warehouse witness cannot receive hidden evaluator data")
        self.public_task = deepcopy(dict(public_task))
        self.backend = backend
        self.episode_id: str | None = None
        self._bound = False
        self._witnesses: list[dict[str, Any]] = []
        self._ids: set[str] = set()
        self._object_phase: dict[str, str] = {}
        self._object_step: dict[str, int] = {}
        self._object_carry_progress: dict[str, bool] = {}
        self._object_identity: dict[str, str] = {}
        self._service_car_states: dict[str, str] = {}
        self._dock_states: dict[str, dict[str, Any]] = {}
        self._last_event: Mapping[str, Any] | None = None
        self._pending_event: Mapping[str, Any] | None = None
        self._last_frame: Mapping[str, Any] | None = None
        self._errors: list[str] = []

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        _public_task(context)
        if self.backend is None:
            return
        episode_id = _id(context.get("episode_id"), "episode_id")
        bind = getattr(self.backend, "bind_episode", None)
        if not callable(bind):
            raise ValueError("witness backend must expose bind_episode()")
        bind({
            "schema": "eai.benchmark.warehouse_witness_binding_v1",
            "scene_key": WAREHOUSE_SCENE_KEY,
            "episode_id": episode_id,
            "public_task": deepcopy(self.public_task),
            "base_env": context.get("base_env"),
            "occupancy": context.get("occupancy"),
        })
        self.episode_id = episode_id
        self._bound = True

    def process_event(self, context: Mapping[str, Any]) -> None:
        if self.backend is None:
            return None
        if not self._bound:
            raise ValueError("Warehouse witness is not bound to an episode")
        event = {
            "schema": "eai.benchmark.warehouse_witness_event_v1",
            "scene_key": WAREHOUSE_SCENE_KEY, "episode_id": self.episode_id,
            "event_type": _id(context.get("event_type"), "event_type"),
            "node_id": _id(context.get("node_id"), "node_id"),
            "time_s": _time(context.get("time_s"), "event time_s"),
        }
        rows = _backend_call(self.backend, "observe_event", event)
        self._pending_event = event
        try:
            accepted = self._ingest(rows, trigger="event")
        finally:
            self._pending_event = None
        if accepted:
            self._last_event = deepcopy(event)

    def process_frame(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if self.backend is None:
            return {"witnesses": []}
        if not self._bound:
            raise ValueError("Warehouse witness is not bound to an episode")
        observation = context.get("observation")
        if not isinstance(observation, Mapping):
            raise ValueError("witness frame requires observation metadata")
        frame_sha = observation.get("sensor_config", {}).get("frame_sha256")
        _sha256(frame_sha, "witness frame_sha256")
        self._last_frame = {
            "observation_id": _id(context.get("observation_id"), "observation_id"),
            "frame_sha256": frame_sha,
            "node_id": _id(context.get("node_id"), "node_id"),
            "time_s": _time(context.get("time_s"), "frame time_s"),
        }
        rows = _backend_call(self.backend, "observe_frame", {
            "schema": "eai.benchmark.warehouse_witness_frame_v1",
            "scene_key": WAREHOUSE_SCENE_KEY, "episode_id": self.episode_id,
            "observation_id": self._last_frame["observation_id"],
            "frame_sha256": frame_sha, "observation": deepcopy(dict(observation)),
            "frame": context.get("frame"), "node_id": self._last_frame["node_id"],
            "time_s": self._last_frame["time_s"],
        })
        self._ingest(rows, trigger="frame")
        return {"witnesses": [row["witness_id"] for row in self._witnesses[-len(_as_rows(rows)):] ] if rows else []}

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if context.get("phase") != "episode_finalized":
            raise ValueError("Warehouse physical witness is available only after episode finalization")
        report = self.witness_report()
        return deepcopy(report)

    def witness_report(self) -> dict[str, Any]:
        if self.backend is None:
            return {
                "schema": LIVE_WITNESS_SCHEMA, "status": "pending",
                "episode_id": None, "witnesses": [], "errors": ["backend_not_configured"],
            }
        status = "claims_available" if self._witnesses and not self._errors else "pending"
        if self._errors:
            status = "error"
        return {
            "schema": LIVE_WITNESS_SCHEMA, "status": status,
            "episode_id": self.episode_id, "witnesses": deepcopy(self._witnesses),
            "errors": list(self._errors),
        }

    def _ingest(self, rows: Any, *, trigger: str) -> list[dict[str, Any]]:
        accepted = []
        for row in _as_rows(rows):
            try:
                normalized = self._validate_witness(row, trigger=trigger)
                self._witnesses.append(normalized)
                self._ids.add(normalized["witness_id"])
                accepted.append(normalized)
            except (TypeError, ValueError, KeyError) as exc:
                self._errors.append(f"{type(exc).__name__}: {exc}")
                raise
        return accepted

    def _validate_witness(self, row: Mapping[str, Any], *, trigger: str) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise ValueError("witness backend must return objects")
        common = {"schema", "witness_id", "episode_id", "kind", "time_s", "source", "simulation_step"}
        if set(row) & {"success", "validated", "business_success", "ground_truth", "reference_backend"}:
            raise ValueError("witness contains forbidden decision or reference fields")
        if set(row) - (common | {"object_id", "object_identity", "object_pose_m", "member_ids",
                                 "phase", "fixed_points", "payload", "physics", "clearance",
                                 "service_car_id", "service_car", "dock"}):
            raise ValueError("witness contains undeclared fields")
        normalized = deepcopy(dict(row))
        if normalized.get("schema") != LIVE_WITNESS_SCHEMA:
            raise ValueError("witness has the wrong schema")
        for key in ("witness_id", "episode_id", "kind", "source"):
            _id(normalized.get(key), key)
        if normalized["episode_id"] != self.episode_id:
            raise ValueError("witness episode_id does not match the bound episode")
        if normalized["witness_id"] in self._ids:
            raise ValueError("duplicate witness_id")
        _time(normalized.get("time_s"), "witness time_s")
        if self._witnesses and normalized["time_s"] < self._witnesses[-1]["time_s"]:
            raise ValueError("witness times must be monotonic")
        if type(normalized.get("simulation_step")) is not int or normalized["simulation_step"] < 0:
            raise ValueError("simulation_step must be a nonnegative integer")
        if self._witnesses and normalized["simulation_step"] < self._witnesses[-1]["simulation_step"]:
            raise ValueError("simulation_step must not regress")
        kind = normalized["kind"]
        if kind == "carry":
            self._validate_carry(normalized)
        elif kind == "service_car":
            self._validate_service_car(normalized)
        elif kind in {"dock_state", "dock_handoff"}:
            self._validate_dock(normalized, trigger=trigger)
        else:
            raise ValueError("unsupported Warehouse witness kind")
        return normalized

    def _validate_identity_pose(self, row: Mapping[str, Any]) -> None:
        identity = row.get("object_identity")
        if not isinstance(identity, Mapping) or set(identity) != {"identity_id", "source", "observation_id", "claim_sha256"}:
            raise ValueError("object identity witness is incomplete")
        for key in ("identity_id", "source", "observation_id"):
            _id(identity[key], f"object_identity.{key}")
        _sha256(identity["claim_sha256"], "object_identity.claim_sha256")
        _id(row.get("object_id"), "object_id")
        row["object_pose_m"] = _point(row.get("object_pose_m"), "object_pose_m")

    def _validate_carry(self, row: dict[str, Any]) -> None:
        required = {"object_id", "object_identity", "object_pose_m", "member_ids", "phase",
                    "fixed_points", "payload", "physics", "clearance"}
        if not required <= set(row):
            raise ValueError("carry witness is incomplete")
        self._validate_identity_pose(row)
        members = row["member_ids"]
        if (not isinstance(members, list) or len(members) not in {1, 2}
                or len(set(members)) != len(members)
                or any(not isinstance(item, str) or not item for item in members)):
            raise ValueError("carry witness requires one or two distinct members")
        phase = _id(row["phase"], "carry phase")
        if phase not in {"pickup", "hold", "move", "safe_hold", "reacquire", "release"}:
            raise ValueError("unsupported carry phase")
        previous = self._object_phase.get(row["object_id"])
        previous_step = self._object_step.get(row["object_id"])
        if previous is None and phase != "pickup":
            raise ValueError("carry witness must start with pickup")
        if previous_step is not None and row["simulation_step"] <= previous_step:
            raise ValueError("carry simulation_step must advance for the same object")
        if previous == "safe_hold" and phase != "reacquire":
            raise ValueError("safe_hold must be followed by reacquire")
        if previous == "release":
            raise ValueError("released object cannot resume carrying")
        if phase == "safe_hold":
            if not self._pending_event or self._pending_event.get("event_type") != "member_exit":
                raise ValueError("safe_hold requires the current host-confirmed member_exit event")
        if phase == "reacquire":
            if (not self._pending_event
                    or self._pending_event.get("event_type") not in {"member_join", "reacquire"}):
                raise ValueError("reacquire requires the current host-confirmed member_join/reacquire event")
        if phase == "release":
            if any(point.get("engaged") is not False for point in row["fixed_points"]):
                raise ValueError("release witness must read back disengaged fixed points")
        elif any(point.get("engaged") is not True for point in row["fixed_points"]):
            raise ValueError("active carry phases require engaged fixed points")
        identity_id = row["object_identity"]["identity_id"]
        if row["object_id"] in self._object_identity and self._object_identity[row["object_id"]] != identity_id:
            raise ValueError("object identity changed during episode")
        fixed = row["fixed_points"]
        if (not isinstance(fixed, list) or len(fixed) != len(members)
                or any(not isinstance(item, Mapping) for item in fixed)):
            raise ValueError("fixed_points must cover every active member")
        seen_members = set()
        for point in fixed:
            if set(point) != {"id", "member_id", "interface", "connector_id", "engaged",
                               "world_position_m", "readback_source", "simulation_step"}:
                raise ValueError("fixed point readback is incomplete")
            _id(point["id"], "fixed point id")
            member = _id(point["member_id"], "fixed point member_id")
            if member not in members or member in seen_members:
                raise ValueError("fixed point members do not match the active team")
            seen_members.add(member)
            _id(point["interface"], "fixed point interface")
            _id(point["connector_id"], "fixed point connector_id")
            if type(point["engaged"]) is not bool:
                raise ValueError("fixed point engaged must be boolean readback")
            _point(point["world_position_m"], "fixed point world_position_m")
            _id(point["readback_source"], "fixed point readback_source")
            if point["simulation_step"] != row["simulation_step"]:
                raise ValueError("fixed point readback is from another simulation step")
        payload = row["payload"]
        if not isinstance(payload, Mapping) or set(payload) != {"mass_kg", "capacity_kg", "load_share_kg"}:
            raise ValueError("payload readback is incomplete")
        mass, capacity, shares = (_positive(payload[key], f"payload.{key}") for key in ("mass_kg", "capacity_kg", "load_share_kg"))
        if shares > capacity or shares > mass + 1e-6:
            raise ValueError("payload load share exceeds measured capacity or mass")
        physics = row["physics"]
        if not isinstance(physics, Mapping) or set(physics) != {"rigid_body_path", "readback_source", "post_step", "physics_step"}:
            raise ValueError("physics readback is incomplete")
        _id(physics["rigid_body_path"], "physics.rigid_body_path")
        _id(physics["readback_source"], "physics.readback_source")
        if physics["post_step"] is not True or physics["physics_step"] != row["simulation_step"]:
            raise ValueError("physics witness must be a post-step readback")
        clearance = row["clearance"]
        if not isinstance(clearance, Mapping) or set(clearance) != {"swept_clearance_m", "formation_error_m"}:
            raise ValueError("carry clearance readback is incomplete")
        _time(clearance["swept_clearance_m"], "swept_clearance_m")
        _time(clearance["formation_error_m"], "formation_error_m")
        if phase in {"hold", "move"}:
            self._object_carry_progress[row["object_id"]] = True
        if phase == "release":
            completed = self._object_carry_progress.get(row["object_id"], False)
            row["carry_completion"] = {
                "status": "complete" if completed else "not_completed",
                "reason": ("post-pickup_hold_or_move_observed"
                           if completed else "released_without_post-pickup_hold_or_move"),
            }
        self._object_identity[row["object_id"]] = identity_id
        self._object_phase[row["object_id"]] = phase
        self._object_step[row["object_id"]] = row["simulation_step"]

    def _validate_service_car(self, row: dict[str, Any]) -> None:
        if set(row) != {"schema", "witness_id", "episode_id", "kind", "time_s", "source",
                        "simulation_step", "service_car_id", "service_car"}:
            raise ValueError("service-car witness is incomplete")
        _id(row["service_car_id"], "service_car_id")
        data = row["service_car"]
        if not isinstance(data, Mapping) or set(data) != {"state", "pose_m", "roster"}:
            raise ValueError("service-car readback is incomplete")
        if data["state"] not in {"available", "at_dock", "safe_hold", "clear"}:
            raise ValueError("unsupported service-car state")
        _point(data["pose_m"], "service-car pose_m")
        if (not isinstance(data["roster"], list) or len(set(data["roster"])) != len(data["roster"])
                or any(not isinstance(item, str) or not item for item in data["roster"])):
            raise ValueError("service-car roster must contain distinct runtime ids")
        self._service_car_states[row["service_car_id"]] = data["state"]

    def _validate_dock(self, row: dict[str, Any], *, trigger: str) -> None:
        dock = row.get("dock")
        if not isinstance(dock, Mapping):
            raise ValueError("dock witness requires dock binding")
        if row["kind"] == "dock_state":
            required = {"state_id", "target_ref", "state", "valid_until_s", "observation_id", "frame_sha256"}
            if set(dock) != required or dock["state"] != "ready" or trigger != "frame":
                raise ValueError("dock state witness is incomplete or not ready")
            state_id = _id(dock["state_id"], "dock state_id")
            target_ref = _id(dock["target_ref"], "dock target_ref")
            if state_id in self._dock_states:
                raise ValueError("duplicate dock state_id")
            valid_until = _time(dock["valid_until_s"], "dock valid_until_s")
            if valid_until < row["time_s"]:
                raise ValueError("dock ready state is already expired")
            _id(dock["observation_id"], "dock observation_id")
            _sha256(dock["frame_sha256"], "dock frame_sha256")
            if (not self._last_frame or dock["observation_id"] != self._last_frame["observation_id"]
                    or dock["frame_sha256"] != self._last_frame["frame_sha256"]
                    or row["time_s"] > self._last_frame["time_s"]):
                raise ValueError("dock ready state is not bound to the current captured frame")
            self._dock_states[state_id] = {
                "target_ref": target_ref,
                "time_s": row["time_s"],
                "valid_until_s": valid_until,
                "source": row["source"],
                "simulation_step": row["simulation_step"],
            }
        else:
            required = {"receipt_id", "dock_state_id", "target_ref", "accepted_object_id", "observation_id",
                        "frame_sha256", "object_pose_m", "object_identity", "pose_sample_time_s"}
            allowed = required | {"service_car_id"}
            if not required <= set(dock) or set(dock) - allowed or trigger != "frame":
                raise ValueError("dock receipt requires a current frame and complete live bindings")
            for key in ("receipt_id", "dock_state_id", "target_ref", "accepted_object_id", "observation_id"):
                _id(dock[key], f"dock.{key}")
            if "service_car_id" in dock:
                _id(dock["service_car_id"], "dock.service_car_id")
            _sha256(dock["frame_sha256"], "dock frame_sha256")
            _point(dock["object_pose_m"], "dock object_pose_m")
            pose_time = _time(dock["pose_sample_time_s"], "dock pose_sample_time_s")
            if not self._last_frame or dock["observation_id"] != self._last_frame["observation_id"] \
                    or dock["frame_sha256"] != self._last_frame["frame_sha256"] \
                    or row["time_s"] > self._last_frame["time_s"]:
                raise ValueError("dock receipt is not bound to the current captured frame")
            state = self._dock_states.get(dock["dock_state_id"])
            if state is None:
                raise ValueError("dock receipt references an unknown ready state")
            if (dock["target_ref"] != state["target_ref"] or row["source"] != state["source"]
                    or row["simulation_step"] < state["simulation_step"]):
                raise ValueError("dock receipt does not match its ready state")
            if not state["time_s"] <= pose_time <= row["time_s"] <= state["valid_until_s"]:
                raise ValueError("dock receipt or object pose is outside the ready-state window")
            object_id = dock["accepted_object_id"]
            if self._object_phase.get(object_id) != "release":
                raise ValueError("dock receipt requires an accepted released carry object")
            if self._object_carry_progress.get(object_id) is not True:
                raise ValueError("dock receipt requires completed carry evidence")
            if row["simulation_step"] <= self._object_step[object_id]:
                raise ValueError("dock receipt must use a post-release simulation step")
            # A service-car readback may be included by a deployment that has
            # one, but the current Warehouse task does not require a service
            # car. Dock acceptance remains bound to the live frame, object
            # identity/pose, and current dock state.
            identity = dock["object_identity"]
            if not isinstance(identity, Mapping) or set(identity) != {"identity_id", "source", "observation_id", "claim_sha256"}:
                raise ValueError("dock object identity is incomplete")
            _id(identity["identity_id"], "dock object identity_id")
            _id(identity["source"], "dock object identity source")
            _id(identity["observation_id"], "dock object identity observation_id")
            _sha256(identity["claim_sha256"], "dock object identity claim_sha256")
            if identity["identity_id"] != self._object_identity.get(object_id):
                raise ValueError("dock object identity differs from the carried object")


def _as_rows(rows: Any) -> list[Mapping[str, Any]]:
    if rows is None:
        return []
    if isinstance(rows, Mapping):
        return [rows]
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("witness backend must return an object, list, or None")
    return rows


__all__ = [
    "LIVE_WITNESS_SCHEMA", "WAREHOUSE_SCENE_KEY", "WarehouseDockReceiptProvider",
    "WarehouseFrameDetector", "WarehouseLiveWitnessProvider",
]
