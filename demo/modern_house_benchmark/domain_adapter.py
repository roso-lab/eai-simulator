"""Modern House live business-evidence entry point.

The shared benchmark runtime owns captured observations and scheduling.  This
module binds the participant-visible household item requirement to an injected
detector and binds the resident access measurement to the designated before
and after scan frames.  Missing integrations remain auditable pending state;
this adapter never creates a business-success claim from scheduler completion
or from a simulator object name.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from demo.benchmark.live_domain_collectors import LiveSceneDomainCollector
from demo.benchmark.reference_domain_providers import (
    ModernHousePathReferenceProvider,
    ReferenceFrameDetector,
)


MODERN_HOUSE_SCENE_KEY = "modern_house_nvd"
MODERN_HOUSE_ADAPTER_ID = "modern_house_nvd.reference_domain_adapter.v1"
MODERN_HOUSE_IDENTITY_SCAN_NODES = ("furniture_clearance_scan",)


def _valid_context(context: Any, schema: str) -> Mapping[str, Any]:
    if (not isinstance(context, Mapping)
            or context.get("schema") != schema
            or context.get("scene_key") != MODERN_HOUSE_SCENE_KEY):
        raise ValueError(f"invalid Modern House {schema} context")
    return context


class _PendingAccessProvider:
    """Protocol-compatible no-op while no path measurement backend is wired."""

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        _valid_context(context, "eai.benchmark.domain_runtime_binding_v1")

    def process_frame(self, context: Mapping[str, Any]) -> None:
        _valid_context(context, "eai.benchmark.domain_frame_v1")

    def process_event(self, context: Mapping[str, Any]) -> None:
        _valid_context(context, "eai.benchmark.domain_runtime_event_v1")

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        _valid_context(context, "eai.benchmark.domain_collector_context_v1")
        if context.get("phase") != "episode_finalized":
            raise ValueError("Modern House access evidence is collected after finalization")
        return {"access_paths": {}, "access_map_sha256": None}


def _backend_status(name: str, backend: Any) -> dict[str, Any]:
    configured = backend is not None
    return {
        "name": name,
        "required": True,
        "configured": configured,
        "status": "configured_unverified" if configured else "pending",
    }


class ModernHouseDomainEvidenceProvider:
    """Scene-owned collector with a diagnostic backend-readiness view.

    The provider accepts only the public task packet.  The household item's
    private object binding stays in ``domain_evaluator`` and is never passed
    to either backend.  A detector may publish an identity claim and a path
    backend may publish measured paths, but the shared evaluator remains the
    only component that can validate those claims.
    """

    scene_key = MODERN_HOUSE_SCENE_KEY
    adapter_id = MODERN_HOUSE_ADAPTER_ID

    def __init__(self, public_task: Mapping[str, Any], *,
                 detector_backend: Any = None,
                 path_backend: Any = None,
                 detector_scan_node_ids=MODERN_HOUSE_IDENTITY_SCAN_NODES,
                 **path_options: Any):
        if not isinstance(public_task, Mapping):
            raise ValueError("Modern House public_task must be an object")
        scene = public_task.get("scene")
        if (not isinstance(scene, Mapping)
                or scene.get("scene_key") != MODERN_HOUSE_SCENE_KEY):
            raise ValueError("Modern House public_task has the wrong scene")
        if {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(public_task):
            raise ValueError("Modern House provider cannot receive hidden evaluator data")
        domain = public_task.get("domain_requirements")
        if not isinstance(domain, Mapping):
            raise ValueError("Modern House public_task lacks domain_requirements")
        if (not isinstance(detector_scan_node_ids, (list, tuple, set))
                or not detector_scan_node_ids
                or any(not isinstance(item, str) or not item for item in detector_scan_node_ids)):
            raise ValueError("detector_scan_node_ids must contain objective ids")

        self.backend_status = {
            "schema": "eai.benchmark.modern_house_backend_status_v1",
            "scene_key": MODERN_HOUSE_SCENE_KEY,
            "business_evidence": "pending",
            "components": {
                "household_identity_detector": _backend_status(
                    "household_identity_detector", detector_backend),
                "resident_access_path_backend": _backend_status(
                    "resident_access_path_backend", path_backend),
            },
        }
        detector = (
            ReferenceFrameDetector(
                MODERN_HOUSE_SCENE_KEY,
                backend=detector_backend,
                scan_node_ids=detector_scan_node_ids,
            )
            if detector_backend is not None else None
        )
        access_provider = (
            ModernHousePathReferenceProvider(
                path_backend=path_backend, **path_options)
            if path_backend is not None else _PendingAccessProvider()
        )
        self.collector = LiveSceneDomainCollector(
            MODERN_HOUSE_SCENE_KEY,
            public_identity_requirements=domain.get("items", []),
            detector=detector,
            access_provider=access_provider,
        )

    def bind_runtime(self, context: Mapping[str, Any]) -> None:
        self.collector.bind_runtime(context)

    def process_frame(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return self.collector.process_frame(context)

    def process_event(self, context: Mapping[str, Any]) -> None:
        return self.collector.process_event(context)

    def collect(self, context: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(self.collector.collect(context))
        has_identity = bool(result.get("identity_checks"))
        has_before_after = set(result.get("access_paths", {})) == {"before", "after"}
        if self._all_backends_configured() and has_identity and has_before_after:
            self.backend_status["business_evidence"] = "claims_available"
        else:
            self.backend_status["business_evidence"] = "pending"
        return result

    def integration_status(self) -> dict[str, Any]:
        """Return backend readiness without exposing private evaluator truth."""
        return deepcopy(self.backend_status)

    def _all_backends_configured(self) -> bool:
        return all(
            component["configured"]
            for component in self.backend_status["components"].values())


def build_modern_house_domain_evidence_provider(
    public_task: Mapping[str, Any], *,
    detector_backend: Any = None,
    path_backend: Any = None,
    detector_scan_node_ids=MODERN_HOUSE_IDENTITY_SCAN_NODES,
    **path_options: Any,
) -> ModernHouseDomainEvidenceProvider:
    """Build the House collector from participant-visible task data.

    Backends follow the reference protocols: ``detect(context)`` and
    ``measure_path(context)``.  Omitting either backend is supported and keeps
    its evidence pending/unknown.
    """
    return ModernHouseDomainEvidenceProvider(
        public_task,
        detector_backend=detector_backend,
        path_backend=path_backend,
        detector_scan_node_ids=detector_scan_node_ids,
        **path_options,
    )


__all__ = [
    "MODERN_HOUSE_ADAPTER_ID",
    "MODERN_HOUSE_IDENTITY_SCAN_NODES",
    "ModernHouseDomainEvidenceProvider",
    "build_modern_house_domain_evidence_provider",
]
