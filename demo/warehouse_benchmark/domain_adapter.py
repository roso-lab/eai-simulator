"""Warehouse-owned live business evidence adapter.

This module is the scene entry point for real Warehouse integrations.  The
shared benchmark runtime owns observations and scheduling, while this module
binds Warehouse rack identity, outbound dock service, and live object pose
backends to the Warehouse-owned live witness protocol.

The adapter is intentionally conservative: a detector may publish SKU claims,
but a dock handoff is only attempted when both the dock service and object
pose backends are present.  Missing integrations produce an auditable pending
state and no business claims.  Neither this module nor its backends can return
business truth to the participant.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from demo.benchmark.live_domain_collectors import LiveSceneDomainCollector
from .live_witness import (
    WarehouseDockReceiptProvider,
    WarehouseFrameDetector,
    WarehouseLiveWitnessProvider,
)


WAREHOUSE_SCENE_KEY = "warehouse_nvd"
WAREHOUSE_ADAPTER_ID = "warehouse_nvd.live_witness_domain_adapter.v2"
WAREHOUSE_IDENTITY_SCAN_NODES = ("rack_overview_scan",)


class _PendingDockProvider:
    """Protocol-compatible no-op used when a dock backend is incomplete."""

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_runtime_binding_v1"
                or context.get("scene_key") != WAREHOUSE_SCENE_KEY):
            raise ValueError("invalid Warehouse pending dock binding")

    def process_frame(self, context: Mapping[str, Any]) -> None:
        # A partial service/pose pair cannot create a handoff claim.  Keeping
        # this hook quiet lets the normal collector report remain pending.
        return None

    def process_event(self, context: Mapping[str, Any]) -> None:
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_runtime_event_v1"
                or context.get("scene_key") != WAREHOUSE_SCENE_KEY):
            raise ValueError("invalid Warehouse pending dock event")

    def collect(self, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        if (not isinstance(context, Mapping)
                or context.get("schema") != "eai.benchmark.domain_collector_context_v1"
                or context.get("scene_key") != WAREHOUSE_SCENE_KEY):
            raise ValueError("invalid Warehouse pending dock collection")
        return []


def _backend_status(name: str, backend: Any, *, required: bool = True) -> dict[str, Any]:
    configured = backend is not None
    return {
        "name": name,
        "required": required,
        "configured": configured,
        "status": "ready" if configured else "pending",
    }


class WarehouseDomainEvidenceProvider:
    """Scene-owned collector with an auditable backend readiness view.

    ``public_task`` is copied and validated by the shared collector.  The
    provider never accepts a hidden domain contract or object-to-SKU binding.
    The readiness report is diagnostic metadata for the harness; actual
    identity and dock claims still come only from live backend output.
    """

    scene_key = WAREHOUSE_SCENE_KEY
    adapter_id = WAREHOUSE_ADAPTER_ID

    def __init__(self, public_task: Mapping[str, Any], *,
                 detector_backend: Any = None,
                 dock_service_backend: Any = None,
                 object_pose_backend: Any = None,
                 witness_backend: Any = None,
                 detector_scan_node_ids=WAREHOUSE_IDENTITY_SCAN_NODES,
                 **dock_options: Any):
        if not isinstance(public_task, Mapping):
            raise ValueError("Warehouse public_task must be an object")
        scene = public_task.get("scene")
        if not isinstance(scene, Mapping) or scene.get("scene_key") != WAREHOUSE_SCENE_KEY:
            raise ValueError("Warehouse public_task has the wrong scene")
        if {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(public_task):
            raise ValueError("Warehouse provider cannot receive hidden evaluator data")
        domain = public_task.get("domain_requirements")
        if not isinstance(domain, Mapping):
            raise ValueError("Warehouse public_task lacks domain_requirements")
        if (not isinstance(detector_scan_node_ids, (list, tuple, set))
                or not detector_scan_node_ids
                or any(not isinstance(item, str) or not item for item in detector_scan_node_ids)):
            raise ValueError("detector_scan_node_ids must contain objective ids")

        self.backend_status = {
            "schema": "eai.benchmark.warehouse_backend_status_v1",
            "scene_key": WAREHOUSE_SCENE_KEY,
            "business_evidence": "pending",
            "components": {
                "rack_identity_detector": _backend_status("rack_identity_detector", detector_backend),
                "dock_service": _backend_status("dock_service", dock_service_backend),
                "object_pose": _backend_status("object_pose", object_pose_backend),
                "physical_witness": _backend_status("physical_witness", witness_backend),
            },
        }
        if detector_backend is not None:
            detector = WarehouseFrameDetector(detector_backend, detector_scan_node_ids)
        else:
            detector = None

        dock_complete = dock_service_backend is not None and object_pose_backend is not None
        if dock_complete:
            service_provider = WarehouseDockReceiptProvider(
                service_backend=dock_service_backend,
                object_pose_backend=object_pose_backend,
                **dock_options,
            )
        else:
            service_provider = _PendingDockProvider()
        self.collector = LiveSceneDomainCollector(
            WAREHOUSE_SCENE_KEY,
            public_identity_requirements=domain.get("items", []),
            detector=detector,
            service_provider=service_provider,
        )
        self.witness = WarehouseLiveWitnessProvider(public_task, witness_backend)
        self._bound = False

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        self.collector.bind_runtime(context)
        self.witness.bind_runtime(context)
        self._bound = True

    def process_frame(self, context: Mapping[str, Any]) -> dict[str, Any]:
        result = self.collector.process_frame(context)
        self.witness.process_frame(context)
        return result

    def process_event(self, context: Mapping[str, Any]) -> None:
        result = self.collector.process_event(context)
        self.witness.process_event(context)
        return result

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        result = self.collector.collect(context)
        result = deepcopy(result)
        if self.witness_backend_configured:
            self._witness_report = self.witness.collect(context)
            # Keep the physical ledger in the standard collector result so a
            # runtime consumer cannot silently miss it by skipping the
            # adapter-specific witness_report() side channel. This remains a
            # host evidence record; it does not expose evaluator truth.
            result["physical_witness"] = deepcopy(self._witness_report)
        if self._all_backends_configured() and (
                result.get("identity_checks") or result.get("domain_service_events")):
            self.backend_status["business_evidence"] = "claims_available"
        return result

    def integration_status(self) -> dict[str, Any]:
        """Return readiness metadata without exposing evaluator truth."""
        result = deepcopy(self.backend_status)
        result["physical_witness"] = self.witness.witness_report()
        return result

    def witness_report(self) -> dict[str, Any]:
        """Return the physical witness ledger for trusted host integration."""
        return self.witness.witness_report()

    def _all_backends_configured(self) -> bool:
        return all(
            self.backend_status["components"][name]["configured"]
            for name in ("rack_identity_detector", "dock_service", "object_pose"))

    @property
    def witness_backend_configured(self) -> bool:
        return self.backend_status["components"]["physical_witness"]["configured"]


def build_warehouse_domain_evidence_provider(
    public_task: Mapping[str, Any], *,
    detector_backend: Any = None,
    dock_service_backend: Any = None,
    object_pose_backend: Any = None,
    witness_backend: Any = None,
    detector_scan_node_ids=WAREHOUSE_IDENTITY_SCAN_NODES,
    **dock_options: Any,
) -> WarehouseDomainEvidenceProvider:
    """Build the Warehouse collector from participant-visible task data.

    Detector and dock backends follow the live scene protocols:
    ``detect(context)``, ``issue_handoff(context)``, and
    ``sample_pose(context)``.  ``witness_backend`` additionally exposes
    ``bind_episode``, ``observe_event`` and ``observe_frame`` and is the only
    path for physical carry/cooperative-load claims.  Omitting any backend is
    supported and leaves the corresponding evidence pending/unknown.
    """
    return WarehouseDomainEvidenceProvider(
        public_task,
        detector_backend=detector_backend,
        dock_service_backend=dock_service_backend,
        object_pose_backend=object_pose_backend,
        witness_backend=witness_backend,
        detector_scan_node_ids=detector_scan_node_ids,
        **dock_options,
    )


__all__ = [
    "WAREHOUSE_ADAPTER_ID",
    "WAREHOUSE_IDENTITY_SCAN_NODES",
    "WarehouseDomainEvidenceProvider",
    "build_warehouse_domain_evidence_provider",
]
