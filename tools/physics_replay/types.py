"""Portable data contracts for the physics replay tool.

This module deliberately has no Isaac Sim or Torch imports.  It is usable by
the lightweight repository validation environment as well as the simulator
runtime.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, is_dataclass, asdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np


FORMAT_VERSION = 1
SUPPORTED_CHANNELS = frozenset(
    {"pose", "velocity", "joint_state", "contact_force", "joint_effort"}
)
WRITABLE_CHANNELS = frozenset({"pose", "velocity", "joint_state"})
REPORT_STATUSES = frozenset(
    {"restored", "physical_state_only", "replay_diverged", "unsupported", "corrupt"}
)


def stable_hash(value: Any) -> str:
    """Return a stable SHA-256 identity for JSON-like configuration data."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _jsonable(value: Any) -> Any:
    """Convert common NumPy/dataclass values into JSON-compatible values."""

    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "detach") and hasattr(value, "cpu"):
        try:
            return _jsonable(value.detach().cpu().numpy())
        except (AttributeError, TypeError, ValueError):
            pass
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True)
class TargetSpec:
    """A user-selected USD Prim and the physical channels to record."""

    prim_path: str
    channels: tuple[str, ...] = ("pose", "velocity")
    env_id: int = 0

    def __post_init__(self) -> None:
        path = str(self.prim_path).strip()
        if not path.startswith("/"):
            raise ValueError("prim_path must be an absolute USD path")
        if path == "/":
            raise ValueError("prim_path must identify a concrete Prim")
        object.__setattr__(self, "prim_path", path.rstrip("/") or "/")

        channels = (self.channels,) if isinstance(self.channels, str) else tuple(self.channels)
        normalized = tuple(str(channel).strip().lower() for channel in channels)
        unknown = sorted(set(normalized) - SUPPORTED_CHANNELS)
        if unknown:
            raise ValueError(f"unknown channel(s): {', '.join(unknown)}")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate channels are not allowed")
        if not normalized:
            raise ValueError("at least one channel is required")
        object.__setattr__(self, "channels", normalized)

        if isinstance(self.env_id, bool) or int(self.env_id) < 0:
            raise ValueError("env_id must be a non-negative integer")
        object.__setattr__(self, "env_id", int(self.env_id))


@dataclass(frozen=True)
class ResolvedTarget:
    """A target mapped to one object in an Isaac Lab scene."""

    target_id: str
    spec: TargetSpec
    kind: str
    collection_name: str
    root_path: str
    body_index: int | None = None
    joint_indices: tuple[int, ...] = ()
    supported_channels: tuple[str, ...] = ()
    writable_channels: tuple[str, ...] = ()
    depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.target_id).strip():
            raise ValueError("target_id must not be empty")
        if self.kind not in {"articulation", "link", "rigid_object"}:
            raise ValueError(f"unsupported target kind: {self.kind}")
        if not str(self.root_path).startswith("/"):
            raise ValueError("root_path must be an absolute USD path")
        object.__setattr__(self, "joint_indices", tuple(int(index) for index in self.joint_indices))
        object.__setattr__(self, "supported_channels", tuple(self.supported_channels))
        object.__setattr__(self, "writable_channels", tuple(self.writable_channels))
        object.__setattr__(self, "metadata", deepcopy(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "prim_path": self.spec.prim_path,
            "channels": list(self.spec.channels),
            "env_id": self.spec.env_id,
            "kind": self.kind,
            "collection_name": self.collection_name,
            "root_path": self.root_path,
            "body_index": self.body_index,
            "joint_indices": list(self.joint_indices),
            "supported_channels": list(self.supported_channels),
            "writable_channels": list(self.writable_channels),
            "depth": self.depth,
            "metadata": _jsonable(self.metadata),
        }


@dataclass(frozen=True)
class FieldSchema:
    """Shape and coordinate metadata for one persisted field."""

    shape: tuple[int, ...]
    dtype: str
    unit: str = ""
    frame: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "shape", tuple(int(value) for value in self.shape))
        if any(value < 0 for value in self.shape):
            raise ValueError("field shape cannot contain negative dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": list(self.shape),
            "dtype": self.dtype,
            "unit": self.unit,
            "frame": self.frame,
        }


@dataclass
class FrameRecord:
    """One step interval: state before and after ``action`` is applied."""

    step_index: int
    sim_time: float
    state_before: Mapping[str, Any]
    state_after: Mapping[str, Any]
    actions: Mapping[str, Any]
    phase: str | None = None
    events: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if int(self.step_index) < 0:
            raise ValueError("step_index must be non-negative")
        if not math.isfinite(float(self.sim_time)):
            raise ValueError("sim_time must be finite")
        self.step_index = int(self.step_index)
        self.sim_time = float(self.sim_time)
        self.state_before = {
            str(key): np.asarray(value).copy() for key, value in self.state_before.items()
        }
        self.state_after = {
            str(key): np.asarray(value).copy() for key, value in self.state_after.items()
        }
        self.actions = {
            str(key): np.asarray(value).copy() for key, value in self.actions.items()
        }
        self.phase = None if self.phase is None else str(self.phase)
        self.events = tuple(_jsonable(dict(event)) for event in self.events)


@dataclass
class Manifest:
    """Versioned episode metadata persisted beside trajectory segments."""

    environment: dict[str, Any]
    targets: list[dict[str, Any]]
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    format_version: int = FORMAT_VERSION
    segment_frames: int = 256
    sample_every: int = 1
    replay_fidelity: str = "physical_state_only"
    status: str = "recording"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.environment = deepcopy(dict(self.environment))
        self.targets = deepcopy(list(self.targets))
        self.fields = deepcopy(dict(self.fields))
        self.actions = deepcopy(dict(self.actions))
        self.metadata = deepcopy(dict(self.metadata))
        self.format_version = int(self.format_version)
        if self.format_version != FORMAT_VERSION:
            raise ValueError(f"unsupported replay format version: {self.format_version}")
        self.segment_frames = int(self.segment_frames)
        self.sample_every = int(self.sample_every)
        if self.segment_frames <= 0:
            raise ValueError("segment_frames must be positive")
        if self.sample_every <= 0:
            raise ValueError("sample_every must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(
            {
                "format_version": self.format_version,
                "environment": self.environment,
                "targets": self.targets,
                "fields": self.fields,
                "actions": self.actions,
                "segment_frames": self.segment_frames,
                "sample_every": self.sample_every,
                "replay_fidelity": self.replay_fidelity,
                "status": self.status,
                "metadata": self.metadata,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Manifest":
        if not isinstance(payload, Mapping):
            raise ValueError("manifest must be a JSON object")
        if "environment" not in payload or "targets" not in payload:
            raise ValueError("manifest requires environment and targets")
        return cls(
            environment=dict(payload["environment"]),
            targets=list(payload["targets"]),
            fields=dict(payload.get("fields", {})),
            actions=dict(payload.get("actions", {})),
            format_version=int(payload.get("format_version", 0)),
            segment_frames=int(payload.get("segment_frames", 256)),
            sample_every=int(payload.get("sample_every", 1)),
            replay_fidelity=str(payload.get("replay_fidelity", "physical_state_only")),
            status=str(payload.get("status", "closed")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass
class RestoreReport:
    """Structured result of restoring one checkpoint."""

    status: str
    step_index: int
    channel_errors: dict[str, dict[str, float | bool]] = field(default_factory=dict)
    unrestored_channels: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in REPORT_STATUSES:
            raise ValueError(f"unknown restore status: {self.status}")
        self.step_index = int(self.step_index)
        self.unrestored_channels = tuple(str(value) for value in self.unrestored_channels)
        self.diagnostics = tuple(str(value) for value in self.diagnostics)

    @property
    def ok(self) -> bool:
        return self.status in {"restored", "physical_state_only"}

    @property
    def summary(self) -> str:
        details = "; ".join(self.diagnostics)
        suffix = f": {details}" if details else ""
        return f"{self.status} at step {self.step_index}{suffix}"
