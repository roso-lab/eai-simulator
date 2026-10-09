"""Dependency-light admission and lifecycle scheduling for replay sessions.

The scheduler owns resources and session lifetimes only.  It deliberately does
not understand branch roles, task graphs, evaluator predicates, or benchmark
metrics.  A caller creates one isolated session/coordinator per admitted scene
or branch and uses the leases as the auditable ownership boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import os
import threading
import time
import uuid
from typing import Any, Iterator


def _positive_int(value: int, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _device_name(value: Any) -> str:
    value = "" if value is None else str(value).strip().lower()
    return "cuda:0" if value == "cuda" else value


@dataclass(frozen=True)
class ReplayCapacity:
    max_parallel_scenes: int
    max_parallel_branches: int
    max_branches_per_scene: int = 4
    gpu_memory_budget_mb: int | None = None
    host_memory_budget_mb: int | None = None

    def __post_init__(self) -> None:
        _positive_int(self.max_parallel_scenes, "max_parallel_scenes")
        _positive_int(self.max_parallel_branches, "max_parallel_branches")
        _positive_int(self.max_branches_per_scene, "max_branches_per_scene")
        for name in ("gpu_memory_budget_mb", "host_memory_budget_mb"):
            value = getattr(self, name)
            if value is not None:
                _positive_int(value, name, allow_zero=True)


@dataclass(frozen=True)
class ResourceReservation:
    gpu_memory_mb: int
    host_memory_mb: int = 0

    def __post_init__(self) -> None:
        _positive_int(self.gpu_memory_mb, "gpu_memory_mb", allow_zero=True)
        _positive_int(self.host_memory_mb, "host_memory_mb", allow_zero=True)


@dataclass(frozen=True)
class DeviceEvidence:
    requested_device: str
    actual_physics_device: str
    controller_device: str
    device_validation: str
    gpu_uuid: str = ""
    gpu_memory_budget_mb: int | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.device_validation not in {"passed", "failed", "unavailable"}:
            raise ValueError("device_validation must be passed, failed, or unavailable")
        object.__setattr__(self, "requested_device", _device_name(self.requested_device))
        object.__setattr__(self, "actual_physics_device", _device_name(self.actual_physics_device))
        object.__setattr__(self, "controller_device", _device_name(self.controller_device))
        object.__setattr__(self, "diagnostics", tuple(str(item) for item in self.diagnostics))

    @property
    def valid(self) -> bool:
        return self.device_validation == "passed"


@dataclass(frozen=True)
class ReplayRequest:
    scene_id: str
    branch_key: str
    reservation: ResourceReservation
    requested_device: str
    warmup_ticks: int = 0

    def __post_init__(self) -> None:
        if not str(self.scene_id).strip() or not str(self.branch_key).strip():
            raise ValueError("scene_id and branch_key are required")
        _positive_int(self.warmup_ticks, "warmup_ticks", allow_zero=True)
        object.__setattr__(self, "requested_device", _device_name(self.requested_device))


@dataclass(frozen=True)
class SceneLease:
    lease_id: str
    scene_id: str
    slot: int
    capacity: ReplayCapacity
    branch_key: str = ""
    owner_pid: int = 0
    acquired_at_ns: int = 0


@dataclass(frozen=True)
class BranchLease:
    lease_id: str
    scene_id: str
    branch_key: str
    slot: int
    device: DeviceEvidence
    owner_pid: int = 0
    acquired_at_ns: int = 0
    warmup_ticks: int = 0


@dataclass(frozen=True)
class CapacitySnapshot:
    active_scenes: int
    active_branches: int
    active_branches_by_scene: Mapping[str, int]
    queued_requests: int
    gpu_uuid: str
    gpu_free_memory_mb: int | None
    events_digest: str


@dataclass(frozen=True)
class ReplayAudit:
    event: str
    lease_id: str
    scene_id: str
    branch_key: str = ""
    requested_device: str = ""
    actual_physics_device: str = ""
    controller_device: str = ""
    device_validation: str = ""
    gpu_uuid: str = ""
    gpu_memory_budget_mb: int | None = None
    max_parallel_scenes: int | None = None
    max_parallel_branches: int | None = None
    max_branches_per_scene: int | None = None
    warmup_ticks: int = 0
    episode_time_s: float | None = None
    episode_start_time_ns: int | None = None
    episode_end_time_ns: int | None = None
    release_status: str = ""
    crash_or_abort_reason: str = ""
    owner_pid: int | None = None
    timestamp_ns: int = field(default_factory=time.time_ns)
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["diagnostics"] = list(self.diagnostics)
        return row


class CapacityDenied(RuntimeError):
    """A request was explicitly rejected by a scheduler capacity limit."""


class DevicePreflightError(RuntimeError):
    """A caller attempted to use device evidence that did not pass."""


class ReplayScheduler:
    """Thread-safe scene/branch admission with auditable lease cleanup."""

    def __init__(
        self,
        capacity: ReplayCapacity,
        *,
        resource_probe: Any = None,
        device_probe: Any = None,
        audit_sink: Callable[[Mapping[str, Any]], None] | Any = None,
    ) -> None:
        self.capacity = capacity
        self.resource_probe = resource_probe
        self.device_probe = device_probe
        self.audit_sink = audit_sink
        self._condition = threading.Condition(threading.RLock())
        self._scenes: dict[str, SceneLease] = {}
        self._branches: dict[str, BranchLease] = {}
        self._released_scene_ids: set[str] = set()
        self._released_branch_ids: set[str] = set()
        self._queued = 0
        self._events: list[dict[str, Any]] = []
        self._preflights: dict[str, DeviceEvidence] = {}
        self._scene_devices: dict[str, DeviceEvidence] = {}
        self._branch_scene_leases: dict[str, str] = {}

    def _resource_sample(self) -> Mapping[str, Any]:
        probe = self.resource_probe
        if probe is None:
            return {}
        try:
            sample = probe.sample() if callable(getattr(probe, "sample", None)) else probe()
        except Exception as exc:
            return {"diagnostic": f"resource probe unavailable: {type(exc).__name__}: {exc}"}
        return sample if isinstance(sample, Mapping) else {}

    @staticmethod
    def _memory_mb(sample: Mapping[str, Any], *names: str) -> int | None:
        for name in names:
            value = sample.get(name)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if name.endswith("_bytes"):
                value /= 1024 * 1024
            return max(0, int(value))
        return None

    def _admission_reason(self, *, scene: bool, reservation: ResourceReservation) -> str | None:
        if scene and len(self._scenes) >= self.capacity.max_parallel_scenes:
            return "max_parallel_scenes reached"
        if not scene and len(self._branches) >= self.capacity.max_parallel_branches:
            return "max_parallel_branches reached"
        if not scene:
            return None
        # Scene admission reserves the supplied scene resources too; branch
        # reservations are checked separately when a branch is acquired.
        scene_reservations = [getattr(lease, "_reservation", ResourceReservation(0, 0)) for lease in self._scenes.values()]
        gpu_used = sum(item.gpu_memory_mb for item in scene_reservations) + reservation.gpu_memory_mb
        host_used = sum(item.host_memory_mb for item in scene_reservations) + reservation.host_memory_mb
        if self.capacity.gpu_memory_budget_mb is not None and gpu_used > self.capacity.gpu_memory_budget_mb:
            return "GPU memory budget exceeded"
        if self.capacity.host_memory_budget_mb is not None and host_used > self.capacity.host_memory_budget_mb:
            return "host memory budget exceeded"
        sample = self._resource_sample()
        free_gpu = self._memory_mb(sample, "gpu_free_memory_mb", "gpu_free_bytes", "gpu_free", "vram_free_bytes")
        free_host = self._memory_mb(sample, "host_available_memory_mb", "host_available_bytes", "host_available", "ram_available_bytes")
        if free_gpu is not None and reservation.gpu_memory_mb > free_gpu:
            return "GPU free memory below reservation"
        if free_host is not None and reservation.host_memory_mb > free_host:
            return "host memory below reservation"
        return None

    def _branch_admission_reason(self, scene: SceneLease,
                                 reservation: ResourceReservation) -> str | None:
        scene_count = sum(item.scene_id == scene.scene_id for item in self._branches.values())
        if scene_count >= self.capacity.max_branches_per_scene:
            return "max_branches_per_scene reached"
        if len(self._branches) >= self.capacity.max_parallel_branches:
            return "max_parallel_branches reached"
        active_res = [getattr(item, "_reservation", ResourceReservation(0, 0))
                      for item in self._branches.values()]
        if (self.capacity.gpu_memory_budget_mb is not None
                and sum(item.gpu_memory_mb for item in active_res) + reservation.gpu_memory_mb
                > self.capacity.gpu_memory_budget_mb):
            return "GPU memory budget exceeded"
        if (self.capacity.host_memory_budget_mb is not None
                and sum(item.host_memory_mb for item in active_res) + reservation.host_memory_mb
                > self.capacity.host_memory_budget_mb):
            return "host memory budget exceeded"
        sample = self._resource_sample()
        free_gpu = self._memory_mb(sample, "gpu_free_memory_mb", "gpu_free_bytes", "gpu_free", "vram_free_bytes")
        free_host = self._memory_mb(sample, "host_available_memory_mb", "host_available_bytes", "host_available", "ram_available_bytes")
        if free_gpu is not None and reservation.gpu_memory_mb > free_gpu:
            return "GPU free memory below reservation"
        if free_host is not None and reservation.host_memory_mb > free_host:
            return "host memory below reservation"
        return None

    def _audit(self, event: ReplayAudit) -> None:
        row = event.to_dict()
        self._events.append(row)
        sink = self.audit_sink
        if sink is None:
            return
        if callable(sink):
            sink(row)
        elif hasattr(sink, "append"):
            sink.append(row)
        else:
            raise TypeError("audit_sink must be callable or appendable")

    def _base_audit(self, *, event: str, lease_id: str, scene_id: str, branch_key: str = "",
                    device: DeviceEvidence | None = None, **kwargs: Any) -> ReplayAudit:
        return ReplayAudit(
            event=event, lease_id=lease_id, scene_id=scene_id, branch_key=branch_key,
            requested_device=device.requested_device if device else "",
            actual_physics_device=device.actual_physics_device if device else "",
            controller_device=device.controller_device if device else "",
            device_validation=device.device_validation if device else "",
            gpu_uuid=device.gpu_uuid if device else "",
            gpu_memory_budget_mb=device.gpu_memory_budget_mb if device else None,
            max_parallel_scenes=self.capacity.max_parallel_scenes,
            max_parallel_branches=self.capacity.max_parallel_branches,
            max_branches_per_scene=self.capacity.max_branches_per_scene,
            **kwargs,
        )

    def _wait_for(self, predicate: Callable[[], bool], *, wait: bool, timeout_s: float | None,
                  queue_event: Callable[[], None]) -> None:
        if predicate():
            return
        queue_event()
        if not wait:
            raise CapacityDenied("scheduler capacity is unavailable")
        deadline = None if timeout_s is None else time.monotonic() + max(0.0, float(timeout_s))
        while not predicate():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise CapacityDenied("scheduler capacity wait timed out")
            self._condition.wait(remaining)

    def acquire_scene(
        self, scene_id: str, *, reservation: ResourceReservation,
        requested_device: str, wait: bool = True, timeout_s: float | None = None,
        owner_pid: int | None = None,
    ) -> SceneLease:
        scene_id = str(scene_id)
        requested_device = _device_name(requested_device)
        scene_device = DeviceEvidence(requested_device, "", "", "unavailable")
        with self._condition:
            queued = False

            def ready() -> bool:
                return self._admission_reason(scene=True, reservation=reservation) is None

            def queue_event() -> None:
                nonlocal queued
                if not queued:
                    queued = True
                    self._queued += 1
                    reason = self._admission_reason(scene=True, reservation=reservation) or ""
                    event = "gpu_admission_rejected" if "GPU" in reason or "gpu" in reason else "scene_queued"
                    self._audit(self._base_audit(event=event, lease_id="", scene_id=scene_id,
                                                 device=scene_device, diagnostics=(reason,)))

            try:
                self._wait_for(ready, wait=wait, timeout_s=timeout_s, queue_event=queue_event)
            finally:
                if queued:
                    self._queued -= 1
            slot = len(self._scenes)
            now = time.time_ns()
            lease = SceneLease(
                str(uuid.uuid4()), scene_id, slot, self.capacity, "",
                os.getpid() if owner_pid is None else int(owner_pid), now,
            )
            object.__setattr__(lease, "_reservation", reservation)
            self._scenes[lease.lease_id] = lease
            self._scene_devices[lease.lease_id] = scene_device
            self._audit(self._base_audit(event="scene_acquired", lease_id=lease.lease_id,
                                         scene_id=scene_id, device=scene_device,
                                         owner_pid=lease.owner_pid))
            return lease

    def acquire_branch(
        self, scene: SceneLease, *, branch_key: str,
        reservation: ResourceReservation, requested_device: str,
        wait: bool = True, timeout_s: float | None = None,
        owner_pid: int | None = None, warmup_ticks: int = 0,
    ) -> BranchLease:
        requested_device = _device_name(requested_device)
        branch_key = str(branch_key)
        with self._condition:
            if scene.lease_id not in self._scenes:
                raise RuntimeError("scene lease is not active")
            if any(item.branch_key == branch_key for item in self._branches.values()):
                raise ValueError(f"branch_key is already active: {branch_key}")
            queued = False

            def ready() -> bool:
                return self._branch_admission_reason(scene, reservation) is None

            def queue_event() -> None:
                nonlocal queued
                if not queued:
                    queued = True
                    self._queued += 1
                    reason = self._branch_admission_reason(scene, reservation) or "capacity unavailable"
                    event = "gpu_admission_rejected" if "GPU" in reason or "gpu" in reason else "branch_queued"
                    self._audit(self._base_audit(event=event, lease_id="", scene_id=scene.scene_id,
                                                 branch_key=branch_key, diagnostics=(reason,)))

            try:
                self._wait_for(ready, wait=wait, timeout_s=timeout_s, queue_event=queue_event)
            finally:
                if queued:
                    self._queued -= 1
            slot = sum(item.scene_id == scene.scene_id for item in self._branches.values())
            now = time.time_ns()
            evidence = self._preflights.get(branch_key) or self._preflights.get(scene.scene_id)
            if evidence is None:
                raise DevicePreflightError(
                    f"scene {scene.scene_id!r} must pass device preflight before branch admission"
                )
            if not evidence.valid:
                raise DevicePreflightError(
                    f"scene {scene.scene_id!r} device preflight did not pass: "
                    + "; ".join(evidence.diagnostics)
                )
            lease = BranchLease(str(uuid.uuid4()), scene.scene_id, branch_key, slot, evidence,
                                os.getpid() if owner_pid is None else int(owner_pid), now,
                                int(warmup_ticks))
            object.__setattr__(lease, "_reservation", reservation)
            self._branches[lease.lease_id] = lease
            self._branch_scene_leases[lease.lease_id] = scene.lease_id
            self._audit(self._base_audit(event="branch_acquired", lease_id=lease.lease_id,
                                         scene_id=scene.scene_id, branch_key=branch_key,
                                         device=evidence, warmup_ticks=int(warmup_ticks),
                                         episode_start_time_ns=now,
                                         owner_pid=lease.owner_pid))
            self._audit(self._base_audit(event="branch_started", lease_id=lease.lease_id,
                                         scene_id=scene.scene_id, branch_key=branch_key,
                                         device=evidence, warmup_ticks=int(warmup_ticks),
                                         episode_start_time_ns=now,
                                         owner_pid=lease.owner_pid))
            return lease

    def preflight(self, request: ReplayRequest, session: Any) -> DeviceEvidence:
        """Probe actual physics/controller devices without importing Isaac types."""
        requested = _device_name(request.requested_device)
        values: Mapping[str, Any] = {}
        probe = self.device_probe
        try:
            if probe is not None:
                if callable(getattr(probe, "probe", None)):
                    values = probe.probe(session, request)  # type: ignore[misc]
                elif callable(probe):
                    try:
                        values = probe(session, request)
                    except TypeError:
                        values = probe(session)
            if not isinstance(values, Mapping):
                values = {}
        except Exception as exc:
            values = {"diagnostics": (f"device probe failed: {type(exc).__name__}: {exc}",)}

        def read(*names: str) -> Any:
            for name in names:
                if name in values:
                    return values[name]
                current = session
                for part in name.split("."):
                    current = getattr(current, part, None)
                    if current is None:
                        break
                if current is not None:
                    return current
            return None

        actual = _device_name(read("actual_physics_device", "physics_device", "physics_backend_device", "sim.physics_device"))
        controller = _device_name(read("controller_device", "action_device", "controller.device"))
        gpu_uuid = str(read("gpu_uuid", "device_uuid") or "")
        diagnostics = tuple(str(item) for item in (values.get("diagnostics") or ()))
        if not actual or not controller:
            validation = "unavailable"
            diagnostics += ("actual physics and controller devices must both be observable",)
        elif actual != requested or controller != requested:
            validation = "failed"
            diagnostics += (f"requested {requested}, physics={actual}, controller={controller}",)
        else:
            validation = "passed"
        evidence = DeviceEvidence(requested, actual, controller, validation, gpu_uuid,
                                  request.reservation.gpu_memory_mb, diagnostics)
        with self._condition:
            self._preflights[request.scene_id] = evidence
            self._preflights[request.branch_key] = evidence
            # Make the preflight visible to scene lifecycle records too.
            for lease_id, lease in self._scenes.items():
                if lease.scene_id == request.scene_id:
                    self._scene_devices[lease_id] = evidence
            self._audit(self._base_audit(
                event="scene_preflight_passed" if evidence.valid else "scene_preflight_failed",
                lease_id="", scene_id=request.scene_id, branch_key=request.branch_key,
                device=evidence, warmup_ticks=request.warmup_ticks,
                diagnostics=evidence.diagnostics,
            ))
        return evidence

    def release_branch(self, lease: BranchLease, *, status: str = "completed", reason: str = "") -> None:
        with self._condition:
            current = self._branches.pop(lease.lease_id, None)
            if current is None:
                return
            episode_time = max(0.0, (time.time_ns() - current.acquired_at_ns) / 1e9)
            abort = status not in {"completed", "success", "released"}
            if abort:
                self._audit(self._base_audit(event="branch_abort", lease_id=current.lease_id,
                                             scene_id=current.scene_id, branch_key=current.branch_key,
                                             device=current.device, warmup_ticks=current.warmup_ticks,
                                             episode_time_s=episode_time, release_status=status,
                                             crash_or_abort_reason=reason or status,
                                             episode_start_time_ns=current.acquired_at_ns,
                                             episode_end_time_ns=time.time_ns(),
                                             owner_pid=current.owner_pid))
            self._audit(self._base_audit(event="branch_released", lease_id=current.lease_id,
                                         scene_id=current.scene_id, branch_key=current.branch_key,
                                         device=current.device, warmup_ticks=current.warmup_ticks,
                                         episode_time_s=episode_time, release_status=status,
                                         crash_or_abort_reason=reason,
                                         episode_start_time_ns=current.acquired_at_ns,
                                         episode_end_time_ns=time.time_ns(),
                                         owner_pid=current.owner_pid))
            self._released_branch_ids.add(current.lease_id)
            self._branch_scene_leases.pop(current.lease_id, None)
            self._condition.notify_all()

    def release_scene(self, lease: SceneLease, *, status: str = "completed", reason: str = "") -> None:
        with self._condition:
            if any(self._branch_scene_leases.get(item.lease_id) == lease.lease_id
                   for item in self._branches.values()):
                raise RuntimeError("cannot release scene while branch leases are active")
            current = self._scenes.pop(lease.lease_id, None)
            if current is None:
                return
            self._audit(self._base_audit(event="scene_released", lease_id=current.lease_id,
                                         scene_id=current.scene_id, device=self._scene_devices.get(current.lease_id), release_status=status,
                                         crash_or_abort_reason=reason, owner_pid=current.owner_pid))
            self._released_scene_ids.add(current.lease_id)
            self._scene_devices.pop(current.lease_id, None)
            self._condition.notify_all()

    @contextmanager
    def scene_slot(self, scene_id: str, *, reservation: ResourceReservation,
                   requested_device: str, wait: bool = True,
                   timeout_s: float | None = None) -> Iterator[SceneLease]:
        lease = self.acquire_scene(scene_id, reservation=reservation,
                                   requested_device=requested_device,
                                   wait=wait, timeout_s=timeout_s)
        try:
            yield lease
        except BaseException as exc:
            self.release_scene(lease, status="aborted", reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            self.release_scene(lease)

    @contextmanager
    def branch_slot(self, scene: SceneLease, *, branch_key: str,
                    reservation: ResourceReservation, requested_device: str,
                    wait: bool = True, timeout_s: float | None = None,
                    warmup_ticks: int = 0) -> Iterator[BranchLease]:
        lease = self.acquire_branch(scene, branch_key=branch_key,
                                    reservation=reservation,
                                    requested_device=requested_device,
                                    wait=wait, timeout_s=timeout_s,
                                    warmup_ticks=warmup_ticks)
        try:
            yield lease
        except BaseException as exc:
            self.release_branch(lease, status="aborted", reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            self.release_branch(lease)

    def snapshot(self) -> CapacitySnapshot:
        with self._condition:
            sample = self._resource_sample()
            free_gpu = self._memory_mb(sample, "gpu_free_memory_mb", "gpu_free_bytes", "gpu_free", "vram_free_bytes")
            gpu_uuid = str(sample.get("gpu_uuid", ""))
            import hashlib
            digest = hashlib.sha256(
                repr([(row.get("event"), row.get("lease_id"), row.get("timestamp_ns")) for row in self._events]).encode()
            ).hexdigest()
            counts: dict[str, int] = {}
            for lease in self._branches.values():
                counts[lease.scene_id] = counts.get(lease.scene_id, 0) + 1
            return CapacitySnapshot(len(self._scenes), len(self._branches), counts,
                                    self._queued, gpu_uuid, free_gpu, digest)

    def recover_stale(self, *, live_pid_check: Callable[[int], bool]) -> tuple[str, ...]:
        recovered: list[str] = []
        with self._condition:
            branch_leases = list(self._branches.values())
            scene_leases = list(self._scenes.values())
        for lease in branch_leases:
            if lease.owner_pid and not live_pid_check(lease.owner_pid):
                self.release_branch(lease, status="stale", reason="owner PID is no longer alive")
                recovered.append(lease.lease_id)
                with self._condition:
                    self._audit(self._base_audit(event="stale_lease_recovered", lease_id=lease.lease_id,
                                                 scene_id=lease.scene_id, branch_key=lease.branch_key,
                                                 device=lease.device, release_status="stale",
                                                 owner_pid=lease.owner_pid))
        for lease in scene_leases:
            with self._condition:
                if lease.lease_id not in self._scenes:
                    continue
                has_branch = any(item.scene_id == lease.scene_id for item in self._branches.values())
            if not has_branch and lease.owner_pid and not live_pid_check(lease.owner_pid):
                self.release_scene(lease, status="stale", reason="owner PID is no longer alive")
                recovered.append(lease.lease_id)
                with self._condition:
                    self._audit(self._base_audit(event="stale_lease_recovered", lease_id=lease.lease_id,
                                                 scene_id=lease.scene_id, release_status="stale",
                                                 owner_pid=lease.owner_pid))
        return tuple(recovered)

    @property
    def audits(self) -> tuple[Mapping[str, Any], ...]:
        with self._condition:
            return tuple(dict(row) for row in self._events)


__all__ = [
    "ReplayCapacity", "ResourceReservation", "DeviceEvidence", "ReplayRequest",
    "SceneLease", "BranchLease", "CapacitySnapshot", "ReplayAudit",
    "CapacityDenied", "DevicePreflightError", "ReplayScheduler",
]
