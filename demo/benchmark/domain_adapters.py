"""Runtime boundary for scene-specific business evidence.

The shared executor knows how to sample poses and acquire sensor frames, but it
must not infer SKU, household identity, dock state, or resident access from a
successful scheduler node.  Scene adapters use this small boundary to attach
their own detector/service output to the already trusted observation store.

This module deliberately records claims rather than validating them.  The
private domain evaluator remains the only component that can compare claims
with object identity, order, and service truth.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable, Mapping

from .provenance import canonical_sha256


_SCENES = {"factory", "warehouse_nvd", "modern_house_nvd"}
_COLLECTOR_RESULT_KEYS = {
    "identity_checks", "domain_service_events", "access_paths", "access_map_sha256",
}
_SERVICE_KINDS = {"dock_state", "dock_handoff"}
_DETECTION_RESULT_KEYS = {
    "detection_id", "detector_source", "detector_version", "target_id",
    "estimated_position_m", "confidence", "timestamp_s", "item_code",
}


def _finite(value: Any, label: str, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or (nonnegative and value < 0):
        qualifier = "nonnegative " if nonnegative else ""
        raise ValueError(f"{label} must be a finite {qualifier}number")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


class SceneDomainEvidenceAdapter:
    """Collect evaluator-bound records from a scene-owned live adapter.

    ``observation_store`` is intentionally supplied by the runtime.  Adapter
    code can publish a real frame or detector output through that object, but
    cannot insert evaluator truth.  The adapter accepts only records produced
    by an explicitly named scene adapter and keeps a monotonically ordered
    service ledger for replay.
    """

    def __init__(self, scene_key: str, *, observation_store,
                 adapter_id: str | None = None,
                 collector: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
        if scene_key not in _SCENES:
            raise ValueError(f"unsupported scene domain adapter: {scene_key}")
        self.scene_key = scene_key
        self.observation_store = observation_store
        self.adapter_id = adapter_id or f"{scene_key}.domain_adapter.pending"
        _string(self.adapter_id, "adapter_id")
        if (collector is not None and not callable(collector)
                and not callable(getattr(collector, "collect", None))):
            raise ValueError("domain evidence collector must be callable or expose collect()")
        self.collector = collector
        self._identity_checks: list[dict[str, Any]] = []
        self._service_events: list[dict[str, Any]] = []
        self._access_paths: dict[str, dict[str, Any]] = {}
        self._access_map_sha256: str | None = None
        self._dock_receipt_id: str | None = None
        self._status = "pending_live_scene_adapter"
        self._collections: list[dict[str, Any]] = []
        self._runtime_binding = {
            "bind_hook_configured": callable(getattr(collector, "bind_runtime", None)),
            "frame_hook_configured": callable(getattr(collector, "process_frame", None)),
            "bound": False,
            "status": "not_bound",
        }
        self._frame_processing: list[dict[str, Any]] = []
        self._event_processing: list[dict[str, Any]] = []
        self._runtime_errors: list[str] = []

    @property
    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        status = _string(status, "adapter status")
        if status not in {"pending_live_scene_adapter", "live_claims_recorded", "collector_error"}:
            raise ValueError(f"unsupported domain adapter status: {status}")
        self._status = status

    def bind_runtime(self, *, base_env, occupancy,
                     public_task: Mapping[str, Any]) -> dict[str, Any]:
        """Bind live scene handles without serializing them into evidence.

        Only the participant-visible task packet is supplied as scenario
        metadata.  The private domain contract and its object bindings stay in
        the evaluator.  A collector may use the trusted ``base_env`` and
        occupancy handles to connect sensors or services, but those handles
        never enter the report.
        """
        if not isinstance(public_task, Mapping):
            raise ValueError("public_task must be an object")
        scene = public_task.get("scene")
        if (not isinstance(scene, Mapping)
                or scene.get("scene_key") != self.scene_key):
            raise ValueError("public task scene does not match domain adapter")
        hook = getattr(self.collector, "bind_runtime", None)
        event = {
            "bind_hook_configured": callable(hook),
            "frame_hook_configured": callable(
                getattr(self.collector, "process_frame", None)),
            "bound": False,
            "status": "pending_no_runtime_bind_hook" if not callable(hook) else "pending",
        }
        if not callable(hook):
            self._runtime_binding = event
            return deepcopy(event)
        try:
            hook({
                "schema": "eai.benchmark.domain_runtime_binding_v1",
                "scene_key": self.scene_key,
                "base_env": base_env,
                "occupancy": occupancy,
                "observation_store": self.observation_store,
                "public_task": deepcopy(dict(public_task)),
            })
            event.update(bound=True, status="bound")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            event.update(status="error", reason=reason)
            self._runtime_errors.append(reason)
            self._status = "collector_error"
        self._runtime_binding = event
        return deepcopy(event)

    def process_frame(self, *, observation_id: str, frame: Any,
                      node_id: str, time_s: float) -> dict[str, Any]:
        """Run a live detector hook and atomically publish its claims.

        The hook receives pixels and the already-published observation record.
        It returns ``{"detections": [...]}``; the shared adapter binds every
        row to ``observation_id`` and writes it through ``ObservationStore``.
        Success booleans and private object bindings are outside this schema.
        """
        observation_id = _string(observation_id, "observation_id")
        node_id = _string(node_id, "node_id")
        time_s = _finite(time_s, "frame processing time_s")
        event = {"observation_id": observation_id, "node_id": node_id,
                 "time_s": time_s, "status": "pending", "detection_ids": []}
        hook = getattr(self.collector, "process_frame", None)
        if not callable(hook):
            event["status"] = "pending_no_frame_hook"
            self._frame_processing.append(event)
            return deepcopy(event)
        if self._runtime_errors:
            event.update(status="suppressed_after_runtime_error",
                         reason=self._runtime_errors[-1])
            self._frame_processing.append(event)
            return deepcopy(event)
        try:
            observation = self.observation_store.get(observation_id)
            result = hook({
                "schema": "eai.benchmark.domain_frame_v1",
                "scene_key": self.scene_key,
                "observation_id": observation_id,
                "observation": observation,
                "frame": frame,
                "node_id": node_id,
                "time_s": time_s,
            })
            if result is None:
                result = {"detections": []}
            if not isinstance(result, Mapping) or set(result) != {"detections"}:
                raise ValueError("frame hook must return only a detections list")
            rows = result["detections"]
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise ValueError("frame detections must be a list of objects")
            normalized = []
            for row in rows:
                row = dict(row)
                unknown = set(row) - _DETECTION_RESULT_KEYS
                missing = (_DETECTION_RESULT_KEYS - {"item_code"}) - set(row)
                if unknown or missing:
                    raise ValueError(
                        "frame detection fields are invalid; missing="
                        f"{sorted(missing)}, unknown={sorted(unknown)}")
                normalized.append(row)
            # Validate the complete batch on a detached store first.  A bad
            # later row therefore cannot leave an earlier claim visible.
            staged_store = deepcopy(self.observation_store)
            for row in normalized:
                staged_store.publish_detection(
                    source_observation_id=observation_id, **row)
            for row in normalized:
                self.observation_store.publish_detection(
                    source_observation_id=observation_id, **row)
            event.update(status="processed",
                         detection_ids=[row["detection_id"] for row in normalized])
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            event.update(status="error", reason=reason)
            self._runtime_errors.append(reason)
            self._status = "collector_error"
        self._frame_processing.append(event)
        return deepcopy(event)

    def process_event(self, *, event_type: str, node_id: str,
                      time_s: float) -> dict[str, Any]:
        """Forward a trusted runtime event to a stateful scene provider."""
        event_type = _string(event_type, "domain runtime event_type")
        node_id = _string(node_id, "domain runtime node_id")
        time_s = _finite(time_s, "domain runtime event time_s")
        event = {"event_type": event_type, "node_id": node_id,
                 "time_s": time_s, "status": "pending"}
        hook = getattr(self.collector, "process_event", None)
        if not callable(hook):
            event["status"] = "pending_no_event_hook"
            self._event_processing.append(event)
            return deepcopy(event)
        if self._runtime_errors:
            event.update(status="suppressed_after_runtime_error",
                         reason=self._runtime_errors[-1])
            self._event_processing.append(event)
            return deepcopy(event)
        try:
            result = hook({
                "schema": "eai.benchmark.domain_runtime_event_v1",
                "scene_key": self.scene_key,
                "event_type": event_type,
                "node_id": node_id,
                "time_s": time_s,
            })
            if result is not None:
                if not isinstance(result, Mapping):
                    raise ValueError("domain event hook result must be an object or None")
                unknown = set(result) - {"status", "detail"}
                if unknown:
                    raise ValueError(
                        "domain event hook returned undeclared fields: "
                        + ", ".join(sorted(unknown)))
                event["provider_result"] = deepcopy(dict(result))
            event["status"] = "processed"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            event.update(status="error", reason=reason)
            self._runtime_errors.append(reason)
            self._status = "collector_error"
        self._event_processing.append(event)
        return deepcopy(event)

    def collect(self, *, phase: str, time_s: float,
                context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a scene-owned collector and ingest only its declared claims.

        The collector is an internal harness callback. It may close over a live
        Isaac sensor/stage handle, but it receives only serializable episode
        metadata here and its return value is never treated as evaluator truth.
        A missing collector is an explicit pending result. Malformed collector
        output becomes an auditable error and cannot partially certify a task.
        """
        phase = _string(phase, "collector phase")
        time_s = _finite(time_s, "collector time_s")
        if context is not None and not isinstance(context, Mapping):
            raise ValueError("collector context must be an object")
        collection = {"phase": phase, "time_s": time_s,
                      "collector_configured": self.collector is not None,
                      "status": "pending", "claim_counts": {
                          "identity_checks": 0, "domain_service_events": 0,
                          "access_paths": 0}}
        if self.collector is None:
            self._collections.append(collection)
            return deepcopy(collection)

        if self._runtime_errors:
            collection.update({"status": "error",
                               "reason": self._runtime_errors[-1]})
            self._status = "collector_error"
            self._collections.append(collection)
            return deepcopy(collection)

        try:
            collector = getattr(self.collector, "collect", None)
            if not callable(collector):
                collector = self.collector
            result = collector({
                "schema": "eai.benchmark.domain_collector_context_v1",
                "scene_key": self.scene_key,
                "phase": phase,
                "time_s": time_s,
                "observation_store": self.observation_store.report(),
                "metadata": deepcopy(dict(context or {})),
            })
            if not isinstance(result, Mapping):
                raise ValueError("collector result must be an object")
            unknown = sorted(set(result) - _COLLECTOR_RESULT_KEYS)
            if unknown:
                raise ValueError("collector result contains undeclared fields: " + ", ".join(unknown))
            identity_rows = result.get("identity_checks", [])
            service_rows = result.get("domain_service_events", [])
            access_rows = result.get("access_paths", {})
            if (not isinstance(identity_rows, list)
                    or not isinstance(service_rows, list)
                    or not isinstance(access_rows, Mapping)):
                raise ValueError("collector claims have invalid container types")
            staged_identity = list(self._identity_checks)
            staged_service = list(self._service_events)
            staged_access = dict(self._access_paths)
            staged_access_map_sha256 = self._access_map_sha256
            staged_receipt = self._dock_receipt_id
            # Ingest against a temporary adapter so a bad later row cannot
            # leave an earlier row visible in the episode ledger.
            temporary = object.__new__(type(self))
            temporary.__dict__ = dict(self.__dict__)
            temporary._identity_checks = staged_identity
            temporary._service_events = staged_service
            temporary._access_paths = staged_access
            temporary._access_map_sha256 = staged_access_map_sha256
            temporary._dock_receipt_id = staged_receipt
            for row in identity_rows:
                if not isinstance(row, Mapping):
                    raise ValueError("identity claim must be an object")
                temporary.record_identity_check(**dict(row))
            for row in service_rows:
                temporary.record_service_event(row)
            for access_phase, row in access_rows.items():
                if not isinstance(row, Mapping):
                    raise ValueError("access claim must be an object")
                temporary.record_access_path(phase=access_phase, **dict(row))
            access_map_sha256 = result.get("access_map_sha256")
            if access_map_sha256 is not None:
                if (not isinstance(access_map_sha256, str)
                        or len(access_map_sha256) != 64
                        or any(character not in "0123456789abcdef"
                               for character in access_map_sha256)):
                    raise ValueError("access_map_sha256 must be a lowercase SHA-256 digest")
                if not access_rows:
                    raise ValueError("access_map_sha256 requires measured access paths")
                temporary._access_map_sha256 = access_map_sha256
            self._identity_checks = temporary._identity_checks
            self._service_events = temporary._service_events
            self._access_paths = temporary._access_paths
            self._access_map_sha256 = temporary._access_map_sha256
            self._dock_receipt_id = temporary._dock_receipt_id
            claim_counts = {
                "identity_checks": len(identity_rows),
                "domain_service_events": len(service_rows),
                "access_paths": len(access_rows),
            }
            collection.update({"status": "claims_ingested", "claim_counts": claim_counts})
            self._status = ("live_claims_recorded" if any(claim_counts.values())
                            else "pending_live_scene_adapter")
        except (TypeError, ValueError, KeyError) as exc:
            collection.update({"status": "error", "reason": f"{type(exc).__name__}: {exc}"})
            self._status = "collector_error"
        self._collections.append(collection)
        return deepcopy(collection)

    def record_identity_check(self, *, requirement_id: str, detection_ids: list[str],
                              consumer_node: str) -> dict[str, Any]:
        """Record a detector claim and its downstream consumer.

        ``requirement_id`` and ``consumer_node`` are public task references;
        the object identity is never accepted here.  The evaluator resolves the
        detection IDs against its private object binding.
        """
        requirement_id = _string(requirement_id, "requirement_id")
        consumer_node = _string(consumer_node, "consumer_node")
        if (not isinstance(detection_ids, list) or not detection_ids
                or any(not isinstance(item, str) or not item for item in detection_ids)
                or len(set(detection_ids)) != len(detection_ids)):
            raise ValueError("detection_ids must be a nonempty list of unique ids")
        if any(row["requirement_id"] == requirement_id for row in self._identity_checks):
            raise ValueError(f"duplicate identity requirement {requirement_id!r}")
        row = {"requirement_id": requirement_id,
               "detection_ids": list(detection_ids), "consumer_node": consumer_node}
        self._identity_checks.append(row)
        self._status = "live_claims_recorded"
        return deepcopy(row)

    def record_service_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Append one evaluator/service observation without trusting booleans."""
        if not isinstance(event, Mapping):
            raise ValueError("domain service event must be an object")
        row = deepcopy(dict(event))
        identifier = _string(row.get("id"), "domain service event id")
        source = _string(row.get("source"), "domain service event source")
        kind = _string(row.get("kind"), "domain service event kind")
        if kind not in _SERVICE_KINDS:
            raise ValueError(f"unsupported domain service event kind: {kind}")
        time_s = _finite(row.get("time_s"), "domain service event time_s")
        if any(existing["id"] == identifier for existing in self._service_events):
            raise ValueError(f"duplicate domain service event id {identifier!r}")
        if self._service_events and time_s < self._service_events[-1]["time_s"]:
            raise ValueError("domain service events must be monotonic")
        # The source is provenance, not a success assertion.  In particular,
        # participant/self-reported service events remain visible and will fail
        # the private dock predicate.
        row.update({"id": identifier, "source": source, "kind": kind, "time_s": time_s})
        self._service_events.append(row)
        if kind == "dock_handoff":
            self._dock_receipt_id = identifier
        self._status = "live_claims_recorded"
        return deepcopy(row)

    def record_access_path(self, *, phase: str, observation_id: str, time_s: float,
                           points: list[list[float]], target_ref: str) -> dict[str, Any]:
        """Record a measured path claim; geometry is recomputed privately."""
        phase = _string(phase, "access phase")
        if phase not in {"before", "after"}:
            raise ValueError("access phase must be before or after")
        observation_id = _string(observation_id, "access observation_id")
        target_ref = _string(target_ref, "access target_ref")
        time_s = _finite(time_s, "access time_s")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("access path needs at least two points")
        normalized = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                raise ValueError("access path points need at least xy")
            normalized.append([_finite(point[0], "access x", nonnegative=False),
                               _finite(point[1], "access y", nonnegative=False)])
        if phase in self._access_paths:
            raise ValueError(f"duplicate access phase {phase!r}")
        row = {"time_s": time_s, "observation_id": observation_id,
               "points": normalized, "target_ref": target_ref}
        self._access_paths[phase] = row
        self._status = "live_claims_recorded"
        return deepcopy(row)

    def report(self, *, contract_sha256: str) -> dict[str, Any]:
        """Return the only domain data the shared runtime may attach."""
        contract_sha256 = _string(contract_sha256, "domain contract hash")
        result: dict[str, Any] = {
            "contract_sha256": contract_sha256,
            "adapter_id": self.adapter_id,
            "adapter_status": self._status,
            "identity_checks": deepcopy(self._identity_checks),
            "collector": {
                "configured": self.collector is not None,
                "context_schema": "eai.benchmark.domain_collector_context_v1",
                "result_schema": "eai.benchmark.domain_collector_result_v1",
            },
            "runtime_binding": deepcopy(self._runtime_binding),
            "frame_processing": deepcopy(self._frame_processing),
            "event_processing": deepcopy(self._event_processing),
            "collections": deepcopy(self._collections),
        }
        if self._service_events:
            result["domain_service_events"] = deepcopy(self._service_events)
        if self._dock_receipt_id is not None:
            result["dock_receipt_id"] = self._dock_receipt_id
        if self._access_paths:
            result["access_paths"] = deepcopy(self._access_paths)
        if self._access_map_sha256 is not None:
            result["access_map_sha256"] = self._access_map_sha256
        result["record_sha256"] = canonical_sha256(result)
        return result


__all__ = ["SceneDomainEvidenceAdapter"]
