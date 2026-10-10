"""Dependency-light models for heterogeneous benchmark branch collection.

The models in this module deliberately know nothing about Isaac Sim, robot
names, or task semantics.  A :class:`BranchPlan` describes the experimental
identity space; execution and evidence are supplied by a plugin.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
from typing import Any, Mapping, Sequence


BRANCH_ROLES = ("fact", "reference", "sham", "intervention")
EXECUTION_SCOPES = ("measured", "protocol_synthetic", "pending", "failed")
SCHEMA_VERSION = "heterocoord.branch_tool.v2"

IDENTITY_FIELDS = (
    "run_id", "system_id", "scene_key", "task_id", "base_instance_id",
    "condition_id", "pair_group_id", "branch_id", "branch_type", "slot",
    "attempt", "repeat_id", "pair_seed", "checkpoint_digest",
    "restore_checkpoint_digest", "task_version", "system_version",
    "source_revision", "worker_instance_id", "worker_session_id",
    "rng_namespace", "communication_rng_namespace", "intervention_point",
    "execution_scope", "task_contract_digest", "scene_build_digest",
    "asset_resolution_digest", "scene_build_order_digest",
    "skill_adapter_version",
)


def canonical_digest(value: Any) -> str:
    """Return a stable SHA-256 digest for JSON-compatible values."""

    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"value is not JSON serialisable: {type(value).__name__}")


@dataclass(frozen=True)
class CheckpointArtifact:
    """Immutable public checkpoint metadata and state digest."""

    checkpoint_id: str
    digest: str
    state: Any
    schema: str = "heterocoord.branch_checkpoint.v1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(
        cls,
        state: Any,
        *,
        checkpoint_id: str = "base",
        schema: str = "heterocoord.branch_checkpoint.v1",
        metadata: Mapping[str, Any] | None = None,
    ) -> "CheckpointArtifact":
        return cls(
            checkpoint_id=str(checkpoint_id), digest=canonical_digest(state),
            state=state, schema=str(schema), metadata=dict(metadata or {}),
        )

    @property
    def task_contract_digest(self) -> str:
        return str(self.metadata.get("task_contract_digest", ""))

    def row(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_digest": self.digest,
            "snapshot_schema": self.schema,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BranchJob:
    """One immutable branch attempt.

    A retry gets a new ``branch_id`` and ``attempt`` while its
    ``logical_branch_id`` remains stable for aggregation.
    """

    run_id: str
    system_id: str
    scene_key: str
    task_id: str
    condition_id: str
    slot: int
    branch_type: str
    branch_id: str
    pair_group_id: str
    # Keep this as text: the paper protocol uses stable ids such as ``r1``.
    # Numeric repeat counters lose identity when a benchmark manifest is
    # projected into the collector.
    repeat_id: str = "r1"
    pair_seed: int = 0
    attempt: int = 0
    base_instance_id: str = ""
    checkpoint_digest: str = ""
    restore_checkpoint_digest: str = ""
    task_version: str = "task.v1"
    system_version: str = "system.v1"
    source_revision: str = "unknown"
    rng_namespace: str = ""
    communication_rng_namespace: str = ""
    intervention_point: str = ""
    execution_scope: str = "protocol_synthetic"
    task_contract_digest: str = ""
    scene_build_digest: str = ""
    asset_resolution_digest: str = ""
    scene_build_order_digest: str = ""
    skill_adapter_version: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.branch_type not in BRANCH_ROLES:
            raise ValueError(f"unknown branch type: {self.branch_type!r}")
        if self.execution_scope not in EXECUTION_SCOPES:
            raise ValueError(f"unknown execution_scope: {self.execution_scope!r}")
        for name in ("slot", "attempt"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.repeat_id, str) or not self.repeat_id.strip():
            raise ValueError("repeat_id must be non-empty text")
        for name in ("run_id", "system_id", "scene_key", "task_id", "condition_id",
                     "branch_id", "pair_group_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.base_instance_id:
            object.__setattr__(self, "base_instance_id", f"{self.pair_group_id}/base")
        if not self.rng_namespace:
            object.__setattr__(self, "rng_namespace", f"rng/{self.run_id}/{self.pair_group_id}")
        if not self.communication_rng_namespace:
            object.__setattr__(self, "communication_rng_namespace", f"comm/{self.run_id}/{self.pair_group_id}")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def logical_branch_id(self) -> str:
        return self.branch_id.split("#attempt-", 1)[0]

    def with_attempt(self, attempt: int) -> "BranchJob":
        base = self.logical_branch_id
        return replace(
            self,
            branch_id=f"{base}#attempt-{int(attempt)}",
            attempt=int(attempt),
            # Retry is an execution attempt, not a new statistical base
            # instance.  Reducers cluster repeats by this stable id.
            base_instance_id=self.base_instance_id,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BranchJob":
        """Rehydrate an explicit benchmark-owned job without worker fields."""

        names = {
            "run_id", "system_id", "scene_key", "task_id", "condition_id",
            "slot", "branch_type", "branch_id", "pair_group_id", "repeat_id",
            "pair_seed", "attempt", "base_instance_id", "checkpoint_digest",
            "restore_checkpoint_digest", "task_version", "system_version",
            "source_revision", "rng_namespace", "communication_rng_namespace",
            "intervention_point", "execution_scope", "task_contract_digest",
            "scene_build_digest", "asset_resolution_digest",
            "scene_build_order_digest", "skill_adapter_version", "metadata",
        }
        payload = {key: value[key] for key in names if key in value}
        # v2 collectors used an integer repeat counter.  Reading an old
        # manifest is safe when it is normalised to the paper's text id.
        if "repeat_id" in payload:
            payload["repeat_id"] = str(payload["repeat_id"])
        return cls(**payload)

    def identity(self) -> dict[str, Any]:
        row = asdict(self)
        # Worker identity is assigned only after a fresh process starts.  Keep
        # the stable fields present in the pre-launch manifest and let runtime
        # rows fill these two provenance values.
        row.setdefault("worker_instance_id", "")
        row.setdefault("worker_session_id", "")
        row["logical_branch_id"] = self.logical_branch_id
        return row

    def row(self) -> dict[str, Any]:
        return self.identity()


@dataclass(frozen=True)
class BranchPlan:
    """Cartesian experiment plan with a fixed four-arm paired protocol."""

    scene_key: str = "scene"
    task_id: str = "task"
    system_id: str = "system"
    condition_id: str = "default"
    task_version: str = "task.v1"
    system_version: str = "system.v1"
    source_revision: str = "unknown"
    slots: int = 32
    arms: tuple[str, ...] = BRANCH_ROLES
    repeats: int = 1
    pair_seed: int = 0
    run_id: str = ""
    execution_scope: str = "protocol_synthetic"
    scenes: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    systems: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # A benchmark bridge may provide an exact fact-only or strict-pair job
    # list.  When present it is the identity authority; ``jobs()`` must not
    # regenerate branch ids from the collector's convenience dimensions.
    explicit_jobs: tuple[BranchJob, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "arms", tuple(self.arms))
        object.__setattr__(self, "explicit_jobs", tuple(self.explicit_jobs))
        if self.explicit_jobs:
            if len({job.branch_id for job in self.explicit_jobs}) != len(self.explicit_jobs):
                raise ValueError("explicit_jobs must have unique branch_id values")
            roles = tuple(dict.fromkeys(job.branch_type for job in self.explicit_jobs))
            object.__setattr__(self, "arms", roles)
        if self.slots < 1:
            raise ValueError("slots must be positive")
        if self.repeats < 1:
            raise ValueError("repeats must be positive")
        if tuple(self.arms) != tuple(dict.fromkeys(self.arms)):
            raise ValueError("arms must not contain duplicates")
        if not self.explicit_jobs and set(self.arms) != set(BRANCH_ROLES):
            raise ValueError("formal plans must contain fact/reference/sham/intervention")
        if self.execution_scope not in EXECUTION_SCOPES:
            raise ValueError(f"unknown execution_scope: {self.execution_scope!r}")
        for name in ("scenes", "tasks", "systems", "conditions"):
            value = tuple(str(item) for item in getattr(self, name) if str(item).strip())
            object.__setattr__(self, name, value)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def dimensions(self) -> tuple[tuple[str, ...], ...]:
        return (
            self.scenes or (self.scene_key,), self.tasks or (self.task_id,),
            self.systems or (self.system_id,), self.conditions or (self.condition_id,),
        )

    @property
    def combination_count(self) -> int:
        return 1 if not self.dimensions else _product_len(self.dimensions)

    @property
    def pair_slots(self) -> int:
        if self.explicit_jobs:
            return len({job.pair_group_id for job in self.explicit_jobs})
        return self.slots * self.repeats * self.combination_count

    @property
    def logical_branch_count(self) -> int:
        if self.explicit_jobs:
            return len(self.explicit_jobs)
        return self.pair_slots * len(self.arms)

    @property
    def logical_branches(self) -> int:
        return self.logical_branch_count

    @property
    def pair_slot_count(self) -> int:
        return self.pair_slots

    def jobs(self) -> tuple[BranchJob, ...]:
        """Generate deterministic logical jobs in repeat/combination/slot order."""

        if self.explicit_jobs:
            return self.explicit_jobs

        import itertools

        run = self.run_id or "planned"
        jobs: list[BranchJob] = []
        for repeat_index in range(self.repeats):
            repeat_id = f"r{repeat_index + 1}"
            for scene, task, system, condition in itertools.product(*self.dimensions):
                group_seed = self.pair_seed + repeat_index
                for slot in range(self.slots):
                    group = f"{run}/{scene}/{task}/{system}/{condition}/repeat-{repeat_id}/slot-{slot}/seed-{group_seed}"
                    base = f"{group}/base"
                    for role in self.arms:
                        logical = f"{group}/{role}"
                        jobs.append(BranchJob(
                            run_id=run, system_id=system, scene_key=scene,
                            task_id=task, condition_id=condition, slot=slot,
                            branch_type=role, branch_id=f"{logical}#attempt-0",
                            pair_group_id=group, repeat_id=repeat_id,
                            pair_seed=group_seed, base_instance_id=base,
                            checkpoint_digest=str(self.metadata.get("checkpoint_digest", "")),
                            restore_checkpoint_digest=str(self.metadata.get("checkpoint_digest", "")),
                            task_version=self.task_version, system_version=self.system_version,
                            source_revision=self.source_revision,
                            intervention_point=str(self.metadata.get("intervention_point", "")),
                            execution_scope=self.execution_scope,
                            task_contract_digest=str(self.metadata.get("task_contract_digest", "")),
                            scene_build_digest=str(self.metadata.get("scene_build_digest", "")),
                            asset_resolution_digest=str(self.metadata.get("asset_resolution_digest", "")),
                            scene_build_order_digest=str(self.metadata.get("scene_build_order_digest", "")),
                            skill_adapter_version=str(self.metadata.get("skill_adapter_version", "")),
                            rng_namespace=f"rng/{run}/{group}",
                            communication_rng_namespace=f"comm/{run}/{group}",
                            metadata=dict(self.metadata),
                        ))
        return tuple(jobs)

    generate_jobs = jobs

    def row(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "scene_key": self.scene_key, "task_id": self.task_id,
            "system_id": self.system_id, "condition_id": self.condition_id,
            "task_version": self.task_version, "system_version": self.system_version,
            "source_revision": self.source_revision, "slots": self.slots,
            "arms": list(self.arms), "repeats": self.repeats,
            "pair_seed": self.pair_seed, "run_id": self.run_id,
            "execution_scope": self.execution_scope,
            "scenes": list(self.scenes), "tasks": list(self.tasks),
            "systems": list(self.systems), "conditions": list(self.conditions),
            "pair_slots": self.pair_slots,
            "logical_branches": self.logical_branch_count,
            "metadata": dict(self.metadata),
            "explicit_jobs": [job.identity() for job in self.explicit_jobs],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BranchPlan":
        def _seq(name: str) -> tuple[str, ...]:
            raw = value.get(name, ())
            if isinstance(raw, str):
                return (raw,)
            return tuple(str(item) for item in raw or ())

        raw_jobs = value.get("explicit_jobs", ())
        explicit_jobs = tuple(
            item if isinstance(item, BranchJob) else BranchJob.from_mapping(item)
            for item in (raw_jobs or ())
        )
        return cls(
            scene_key=str(value.get("scene_key", "scene")),
            task_id=str(value.get("task_id", "task")),
            system_id=str(value.get("system_id", value.get("system", "system"))),
            condition_id=str(value.get("condition_id", "default")),
            task_version=str(value.get("task_version", "task.v1")),
            system_version=str(value.get("system_version", "system.v1")),
            source_revision=str(value.get("source_revision", "unknown")),
            slots=int(value.get("slots", 32)),
            arms=tuple(value.get("arms", BRANCH_ROLES)),
            repeats=int(value.get("repeats", value.get("repeat", 1))),
            pair_seed=int(value.get("pair_seed", 0)),
            run_id=str(value.get("run_id", "")),
            execution_scope=str(value.get("execution_scope", "protocol_synthetic")),
            scenes=_seq("scenes"), tasks=_seq("tasks"), systems=_seq("systems"),
            conditions=_seq("conditions"), metadata=dict(value.get("metadata", {})),
            explicit_jobs=explicit_jobs,
        )


def _product_len(values: Sequence[Sequence[Any]]) -> int:
    result = 1
    for value in values:
        result *= max(1, len(value))
    return result


@dataclass(frozen=True)
class ResourceSnapshot:
    """Portable resource sample stored in ``resources.jsonl``."""

    timestamp_ns: int
    host_pid: int
    cpu_percent: float | None = None
    ram_available_bytes: int | None = None
    ram_total_bytes: int | None = None
    vram_free_bytes: int | None = None
    vram_total_bytes: int | None = None
    worker_pids: tuple[int, ...] = ()
    observed_active: int = 0

    def row(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "BRANCH_ROLES", "EXECUTION_SCOPES", "SCHEMA_VERSION", "IDENTITY_FIELDS",
    "canonical_digest", "CheckpointArtifact", "BranchJob", "BranchPlan",
    "ResourceSnapshot",
]
