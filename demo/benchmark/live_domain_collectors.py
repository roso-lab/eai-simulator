"""Scene-owned assembly of live business-evidence claims.

Collectors in this module bind only the public task packet, receive live pixels
through a restricted frame hook, and consume the runtime observation-store
snapshot at finalization. They select detector claims that were causally
consumed by the relevant objective, and optionally copy records from an injected
dock service or access-path measurer. They never receive the private object
binding and never decide business success.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable, Mapping


_REQUIREMENT_KEYS = {"id", "item_code", "quantity", "consumer_node"}
_FORBIDDEN_KEYS = {"object_ids", "ground_truth", "business_success", "success"}


def _requirements(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("public identity requirements must be a sequence")
    result = []
    identifiers = set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != _REQUIREMENT_KEYS:
            raise ValueError("identity requirement must contain only public fields")
        normalized = deepcopy(dict(row))
        if any(not isinstance(normalized[key], str) or not normalized[key]
               for key in ("id", "item_code", "consumer_node")):
            raise ValueError("identity requirement strings must be nonempty")
        if (type(normalized["quantity"]) is not int
                or normalized["quantity"] <= 0):
            raise ValueError("identity requirement quantity must be positive")
        if normalized["id"] in identifiers:
            raise ValueError("identity requirement ids must be unique")
        identifiers.add(normalized["id"])
        result.append(normalized)
    return tuple(result)


def _index(rows: Any, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError(f"{label} must be a list of objects")
    result = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"{label} has a missing or duplicate {key}")
        result[identifier] = row
    return result


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


class LiveSceneDomainCollector:
    """Build adapter claims from live detector/service/path records.

    ``service_provider`` returns a list of dock-state/handoff event records.
    ``access_provider`` returns exactly ``access_paths`` and
    ``access_map_sha256``. Providers are scene integrations; their results are
    still checked by the hidden evaluator and are never exposed as truth to a
    participant.
    """

    def __init__(self, scene_key: str, *, public_identity_requirements,
                 detector: Callable[[Mapping[str, Any]], Any] | None = None,
                 service_provider: Callable[[Mapping[str, Any]], Any] | None = None,
                 access_provider: Callable[[Mapping[str, Any]], Any] | None = None):
        if scene_key not in {"warehouse_nvd", "modern_house_nvd"}:
            raise ValueError("live business collector supports Warehouse and Modern House")
        self.scene_key = scene_key
        self.identity_requirements = _requirements(public_identity_requirements)
        for provider, label in ((detector, "detector"),
                                (service_provider, "service_provider"),
                                (access_provider, "access_provider")):
            if (provider is not None and not callable(provider)
                    and not callable(getattr(provider, "collect", None))):
                raise ValueError(f"{label} must be callable or expose collect()")
        if scene_key == "warehouse_nvd" and access_provider is not None:
            raise ValueError("Warehouse collector does not accept a resident-access provider")
        if scene_key == "modern_house_nvd" and service_provider is not None:
            raise ValueError("Modern House collector does not accept a dock-service provider")
        self.detector = detector
        self.service_provider = service_provider
        self.access_provider = access_provider
        self._runtime_binding: Mapping[str, Any] | None = None

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        """Bind trusted runtime handles while checking the public task view."""
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_runtime_binding_v1"
                or context.get("scene_key") != self.scene_key):
            raise ValueError("invalid domain runtime binding")
        public_task = context.get("public_task")
        if not isinstance(public_task, Mapping):
            raise ValueError("runtime binding requires the public task")
        public_domain = public_task.get("domain_requirements")
        if (not isinstance(public_domain, Mapping)
                or public_domain.get("items") != list(self.identity_requirements)):
            raise ValueError("runtime identity requirements differ from the public task")
        self._runtime_binding = context
        for provider in (self.detector, self.service_provider, self.access_provider):
            hook = getattr(provider, "bind_runtime", None)
            if callable(hook):
                hook(context)

    def process_frame(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Run pixel-only detection and notify stateful scene providers."""
        if self._runtime_binding is None:
            raise ValueError("live domain collector is not bound to a runtime")
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_frame_v1"
                or context.get("scene_key") != self.scene_key):
            raise ValueError("invalid live domain frame context")
        for provider in (self.service_provider, self.access_provider):
            hook = getattr(provider, "process_frame", None)
            if callable(hook):
                hook(context)
        if self.detector is None:
            return {"detections": []}
        detector_context = {
            key: context[key] for key in (
                "schema", "scene_key", "observation_id", "observation",
                "frame", "node_id", "time_s")
        }
        result = self.detector(detector_context)
        if not isinstance(result, list):
            raise ValueError("live detector must return a list of detection claims")
        return {"detections": result}

    def process_event(self, context: Mapping[str, Any]) -> None:
        """Forward trusted scheduler events to stateful service/path providers."""
        if self._runtime_binding is None:
            raise ValueError("live domain collector is not bound to a runtime")
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_runtime_event_v1"
                or context.get("scene_key") != self.scene_key):
            raise ValueError("invalid live domain runtime event context")
        for provider in (self.service_provider, self.access_provider):
            hook = getattr(provider, "process_event", None)
            if callable(hook):
                hook(context)

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return self(context)

    def __call__(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(context, Mapping):
            raise ValueError("collector context must be an object")
        if context.get("schema") != "eai.benchmark.domain_collector_context_v1":
            raise ValueError("unsupported collector context schema")
        if context.get("scene_key") != self.scene_key:
            raise ValueError("collector scene does not match runtime scene")
        if _FORBIDDEN_KEYS & set(context):
            raise ValueError("collector context contains evaluator truth or success fields")
        now = _time(context.get("time_s"), "collector time_s")
        store = context.get("observation_store")
        if not isinstance(store, Mapping) or store.get("schema") != "eai.observation_store":
            raise ValueError("collector requires an observation-store report")
        observations = _index(store.get("observations", []), "observation_id", "observations")
        detections = _index(store.get("detections", []), "detection_id", "detections")
        consumptions = store.get("consumptions", [])
        if not isinstance(consumptions, list) or any(
                not isinstance(row, Mapping) for row in consumptions):
            raise ValueError("consumptions must be a list of objects")

        identity_checks = []
        for requirement in self.identity_requirements:
            candidates = []
            for detection in detections.values():
                if detection.get("item_code") != requirement["item_code"]:
                    continue
                observation_id = detection.get("source_observation_id")
                observation = observations.get(observation_id)
                if observation is None:
                    raise ValueError("detection references an unavailable observation")
                detection_time = _time(detection.get("timestamp_s"), "detection timestamp")
                if detection_time > now:
                    raise ValueError("detection occurs after collection")
                consumed = any(
                    row.get("observation_id") == observation_id
                    and row.get("consumer") == requirement["consumer_node"]
                    and detection_time <= _time(row.get("timestamp_s"), "consumption timestamp") <= now
                    for row in consumptions)
                if consumed:
                    candidates.append(detection)
            candidates.sort(key=lambda row: (row["timestamp_s"], row["detection_id"]))
            if not candidates:
                continue
            if len(candidates) != requirement["quantity"]:
                raise ValueError(
                    f"{requirement['id']}: live detector claims are ambiguous for the required quantity")
            identity_checks.append({
                "requirement_id": requirement["id"],
                "detection_ids": [row["detection_id"] for row in candidates],
                "consumer_node": requirement["consumer_node"],
            })

        result: dict[str, Any] = {
            "identity_checks": identity_checks,
            "domain_service_events": [],
            "access_paths": {},
        }
        if self.service_provider is not None:
            provider = getattr(self.service_provider, "collect", None)
            if not callable(provider):
                provider = self.service_provider
            service_rows = provider(deepcopy(dict(context)))
            if not isinstance(service_rows, list) or any(
                    not isinstance(row, Mapping) for row in service_rows):
                raise ValueError("dock service provider must return a list of event objects")
            result["domain_service_events"] = [deepcopy(dict(row)) for row in service_rows]
        if self.access_provider is not None:
            provider = getattr(self.access_provider, "collect", None)
            if not callable(provider):
                provider = self.access_provider
            access = provider(deepcopy(dict(context)))
            if (not isinstance(access, Mapping)
                    or set(access) != {"access_paths", "access_map_sha256"}
                    or not isinstance(access["access_paths"], Mapping)):
                raise ValueError(
                    "access provider must return access_paths and access_map_sha256")
            result["access_paths"] = deepcopy(dict(access["access_paths"]))
            result["access_map_sha256"] = access["access_map_sha256"]
        return result


def build_live_domain_collector(scene_key: str, public_domain_requirements: Mapping[str, Any],
                                **providers) -> LiveSceneDomainCollector:
    """Construct a collector from the participant-visible requirement packet."""
    if not isinstance(public_domain_requirements, Mapping):
        raise ValueError("public domain requirements must be an object")
    if _FORBIDDEN_KEYS & set(public_domain_requirements):
        raise ValueError("public domain requirements contain evaluator truth")
    return LiveSceneDomainCollector(
        scene_key,
        public_identity_requirements=public_domain_requirements.get("items", []),
        **providers,
    )


__all__ = ["LiveSceneDomainCollector", "build_live_domain_collector"]
