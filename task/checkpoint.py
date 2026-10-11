"""In-session skill-state rewind at caller-owned physics step boundaries.

Uses the existing CheckpointableComponent protocol. This component cannot
recreate a running task in a new process or roll back scheduler history/budgets.
Leases must remain authentic and unchanged throughout a rewind. Physics is
restored first by the replay backend; a readback callback then reconciles held
objects and robot state before the caller may resume stepping.
"""
from __future__ import annotations

from copy import deepcopy
from threading import get_ident
from typing import Any, Callable, Mapping

from .resources import ResourceManager


class SkillRuntimeComponent:
    name = "task.skill_runtime"
    version = 1
    priority = 80

    def __init__(self, *, scene_id: str, dependency_digest: str,
                 resources: ResourceManager, skills: Mapping[str, Any],
                 boundary_check: Callable[[], bool],
                 reconcile: Callable[[Mapping[str, Any]], bool]) -> None:
        if not scene_id or not dependency_digest:
            raise ValueError("scene and dependency fingerprint are required")
        if not skills or any(not isinstance(k, str) or not k for k in skills):
            raise ValueError("explicit named skill checkpoint adapters are required")
        for adapter in skills.values():
            if any(not callable(getattr(adapter, method, None)) for method in
                   ("capture_state", "validate_state", "restore_state")):
                raise TypeError("skill adapter must implement checkpoint state methods")
        self.scene_id, self.dependency_digest = scene_id, dependency_digest
        self.resources, self.skills = resources, dict(skills)
        self.boundary_check, self.reconcile = boundary_check, reconcile
        self._owner = get_ident()
        self._identity = object()
        self.quarantined_leases = ()
        self.failed = False

    def _boundary(self):
        if get_ident() != self._owner or self.boundary_check() is not True:
            raise RuntimeError("checkpoint requires the paused owner-thread step boundary")
        if self.failed:
            raise RuntimeError("runtime restore failed; stop this run and explicitly reconcile recovery")

    def capture_state(self) -> Mapping[str, Any]:
        self._boundary()
        state = {"scene_id": self.scene_id, "dependency_digest": self.dependency_digest,
                 # Process-local identity is deliberate: no cross-session lease resurrection.
                 "binding": id(self._identity), "resources": self.resources.snapshot(),
                 "skills": {name: adapter.capture_state() for name, adapter in self.skills.items()}}
        self.validate_state(state)
        return deepcopy(state)

    def validate_state(self, state: Mapping[str, Any]) -> None:
        self._boundary()
        if (state.get("scene_id"), state.get("dependency_digest"), state.get("binding")) != (
                self.scene_id, self.dependency_digest, id(self._identity)):
            raise ValueError("checkpoint scene/dependency/session binding changed")
        occupancy = state.get("resources")
        if occupancy != self.resources.snapshot():
            raise ValueError("resource ownership changed; active leases cannot be resurrected")
        if any(item["state"] != "active" for item in occupancy["leases"]):
            raise ValueError("quarantined ownership cannot be checkpointed/restored")
        if set(self.skills) != {item['owner'] for item in occupancy['leases']}:
            raise ValueError("checkpoint adapters must cover every active lease owner exactly")
        if set(state.get("skills", {})) != set(self.skills):
            raise ValueError("live skill adapter set changed")
        for name, adapter in self.skills.items():
            adapter.validate_state(deepcopy(state["skills"][name]))

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        try:
            for name, adapter in self.skills.items():
                adapter.restore_state(deepcopy(state["skills"][name]))
            if self.reconcile(deepcopy(state)) is not True:
                raise RuntimeError("restored skill/held-object state disagrees with physical readback")
        except BaseException:
            self.on_restore_failed()
            raise

    def on_restore_failed(self) -> None:
        """Called by the outer replay backend if any later restore/readback fails."""
        self.failed = True
        self.quarantined_leases = self.resources.quarantine_active("partial or unverified skill restore")
