"""Cross-scene branch lifecycle and append-only data ledgers.

The data tool deliberately does not import Isaac Sim.  Scene adapters own
simulation creation, actions, stepping, state interpretation and checkpoint
capture/restore.  This module owns durable branch identity, slot admission,
append-only records, resume state and integrity validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Protocol, Sequence
import uuid


SCHEMA_VERSION = "eai.branch_data.v1"
BRANCH_TYPES = frozenset({"fact", "reference", "sham", "intervention"})
EXECUTION_SCOPES = frozenset({"measured", "protocol_synthetic", "pending", "failed"})
LEDGER_FILES = {
    "branches": "branch_manifest.jsonl",
    "steps": "steps.jsonl",
    "episodes": "episodes.jsonl",
    "events": "events.jsonl",
    "progress": "progress.jsonl",
    "network": "network.jsonl",
    "costs": "costs.jsonl",
    "checkpoints": "checkpoints.jsonl",
}
REQUIRED_LEDGER_FIELDS = (
    "scene_key",
    "task_id",
    "base_instance_id",
    "condition_id",
    "pair_seed",
    "pair_group_id",
    "branch_id",
    "branch_type",
    "slot",
    "checkpoint_digest",
    "task_version",
    "source_revision",
    "execution_scope",
)
REQUIRED_STEP_FIELDS = (
    "simulator_step",
    "sim_time_s",
    "commands",
    "post_step_state",
    "phase_state",
    "task_state",
    "active",
    "completed",
    "scene_payload",
)
REQUIRED_CHECKPOINT_FIELDS = (
    "checkpoint_id",
    "simulator_step",
    "snapshot_digest",
    "snapshot_schema",
    "restore_error",
    "restore_passed",
    "restore_status",
    "restore_error_source",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def digest_state(state: Any) -> str:
    """Hash nested checkpoint state without losing array type or shape.

    This preserves the public-checkpoint digest used by the scene runners while
    keeping the common branch tool independent of Fire Rescue task classes.
    """
    from tools.physics_replay.checkpoints import _encode_tree

    def ordered(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: ordered(value[key]) for key in sorted(value)}
        if isinstance(value, (list, tuple)):
            return type(value)(ordered(item) for item in value)
        return value

    arrays: dict[str, Any] = {}
    tree = _encode_tree(ordered(state), arrays)
    digest = hashlib.sha256(
        json.dumps(tree, sort_keys=True, allow_nan=False).encode("utf-8")
    )
    for name, array in sorted(arrays.items()):
        digest.update(name.encode("utf-8"))
        digest.update(str(array.dtype).encode("utf-8"))
        digest.update(str(array.shape).encode("utf-8"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def source_revision(root: Path | None = None) -> str:
    repository = root or Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def _require_text(name: str, value: Any) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be nonempty")
    return result


@dataclass(frozen=True)
class BranchIdentity:
    scene_key: str
    task_id: str
    base_instance_id: str
    condition_id: str
    pair_seed: int
    pair_group_id: str
    branch_id: str
    branch_type: str
    slot: int
    checkpoint_digest: str
    task_version: str
    source_revision: str
    execution_scope: str

    def __post_init__(self) -> None:
        for name in (
            "scene_key", "task_id", "base_instance_id", "condition_id",
            "pair_group_id", "branch_id", "checkpoint_digest", "task_version",
            "source_revision",
        ):
            _require_text(name, getattr(self, name))
        if isinstance(self.pair_seed, bool) or not isinstance(self.pair_seed, int):
            raise ValueError("pair_seed must be an integer")
        if isinstance(self.slot, bool) or not isinstance(self.slot, int) or self.slot < 0:
            raise ValueError("slot must be a zero-based non-negative integer")
        if self.branch_type not in BRANCH_TYPES:
            raise ValueError(f"branch_type must be one of {sorted(BRANCH_TYPES)}")
        if self.execution_scope not in EXECUTION_SCOPES:
            raise ValueError(
                f"execution_scope must be one of {sorted(EXECUTION_SCOPES)}")

    def row(self) -> dict[str, Any]:
        return asdict(self)


class BranchSceneAdapter(Protocol):
    """Scene-owned Isaac boundary consumed by a branch executor."""

    scene_key: str
    task_id: str

    def create_scene(self, *, slots: int, headless: bool) -> Any: ...

    def read_branch_state(self, runtime: Any, slot: int) -> Mapping[str, Any]: ...

    def apply_participant_actions(
        self, runtime: Any, actions: Mapping[str, Any]
    ) -> None: ...

    def step_simulation(self, runtime: Any) -> int: ...

    def capture_checkpoint(self, runtime: Any) -> Mapping[str, Any]: ...

    def restore_checkpoint(
        self, runtime: Any, snapshot: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def task_status(self, runtime: Any, slot: int) -> Mapping[str, Any]: ...

    def scene_payload(self, runtime: Any, slot: int) -> Mapping[str, Any]: ...


class BranchDataBatch:
    """Open append-only branch run.

    A JSONL record is durable when its method returns.  ``run_state.json`` is
    checkpointed every ``autosave_every`` records and at every lifecycle or
    checkpoint boundary.  Resume reconstructs exact counters from JSONL, so a
    process interruption cannot silently discard already appended records.
    """

    def __init__(
        self,
        output: Path,
        manifest: dict[str, Any],
        run_state: dict[str, Any],
        *,
        autosave_every: int = 128,
    ) -> None:
        if autosave_every < 1:
            raise ValueError("autosave_every must be positive")
        self.output = output
        self.manifest = manifest
        self.run_state = run_state
        self.autosave_every = int(autosave_every)
        self._pending_state_records = 0
        self._branches: dict[str, BranchIdentity] = {}
        self._slot_to_branch: dict[int, str] = {}
        self._finished: set[str] = set()
        self._completed: set[str] = set()
        self._verified: set[str] = set()
        self._load_identities()

    @property
    def requested(self) -> int:
        return int(self.manifest["requested"])

    @property
    def formal(self) -> bool:
        return bool(self.manifest["formal"])

    @property
    def branches(self) -> Mapping[str, BranchIdentity]:
        return dict(self._branches)

    def _load_identities(self) -> None:
        for row in _read_jsonl(self.output / LEDGER_FILES["branches"]):
            identity = BranchIdentity(**{key: row[key] for key in REQUIRED_LEDGER_FIELDS})
            previous = self._branches.get(identity.branch_id)
            if previous is not None and previous != identity:
                raise ValueError(f"branch identity changed in ledger: {identity.branch_id}")
            other = self._slot_to_branch.get(identity.slot)
            if other is not None and other != identity.branch_id:
                raise ValueError(f"slot {identity.slot} has multiple branch identities")
            self._branches[identity.branch_id] = identity
            self._slot_to_branch[identity.slot] = identity.branch_id
        for row in _read_jsonl(self.output / LEDGER_FILES["episodes"]):
            if row.get("finished"):
                branch_id = str(row.get("branch_id"))
                self._finished.add(branch_id)
                if row.get("completed"):
                    self._completed.add(branch_id)
                if row.get("independently_verified"):
                    self._verified.add(branch_id)

    def _identity(self, branch_id: str) -> BranchIdentity:
        try:
            return self._branches[branch_id]
        except KeyError as error:
            raise KeyError(f"branch is not registered: {branch_id}") from error

    def _append(
        self,
        ledger: str,
        branch_id: str,
        payload: Mapping[str, Any],
        *,
        save_state: bool = False,
    ) -> dict[str, Any]:
        identity = self._identity(branch_id)
        row = identity.row()
        for key in REQUIRED_LEDGER_FIELDS:
            if key in payload and payload[key] != row[key]:
                raise ValueError(f"record attempts to change branch identity field {key}")
        row.update(dict(payload))
        row["recorded_at"] = _utc_now()
        encoded = _canonical_json(row)
        path = self.output / LEDGER_FILES[ledger]
        with path.open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        counts = self.run_state.setdefault("counts", {})
        counts[ledger] = int(counts.get(ledger, 0)) + 1
        self.run_state["updated_at"] = row["recorded_at"]
        self._pending_state_records += 1
        if save_state or self._pending_state_records >= self.autosave_every:
            self.save_state()
        return row

    def save_state(self) -> None:
        self.run_state["registered"] = len(self._branches)
        self.run_state["resident"] = max(
            int(self.run_state.get("resident", 0)), len(self._branches))
        self.run_state["completed"] = len(self._completed)
        self.run_state["independently_verified"] = len(self._verified)
        self.run_state["queued"] = max(0, self.requested - len(self._branches))
        self.run_state["updated_at"] = _utc_now()
        _write_json_atomic(self.output / "run_state.json", self.run_state)
        self._pending_state_records = 0

    def register_branch(self, identity: BranchIdentity | None = None, **fields: Any) -> BranchIdentity:
        if identity is None:
            identity = BranchIdentity(**fields)
        elif fields:
            raise ValueError("pass BranchIdentity or identity fields, not both")
        if identity.scene_key != self.manifest["scene_key"]:
            raise ValueError("branch scene_key does not match batch")
        if identity.task_id != self.manifest["task_id"]:
            raise ValueError("branch task_id does not match batch")
        if identity.execution_scope != self.manifest["execution_scope"]:
            raise ValueError("branch execution_scope does not match batch")
        if identity.slot >= self.requested:
            raise ValueError(
                f"slot {identity.slot} is outside requested range 0..{self.requested - 1}")
        previous = self._branches.get(identity.branch_id)
        if previous is not None:
            if previous != identity:
                raise ValueError(f"branch identity mismatch on resume: {identity.branch_id}")
            return previous
        other = self._slot_to_branch.get(identity.slot)
        if other is not None:
            raise ValueError(f"slot {identity.slot} is already assigned to {other}")
        if len(self._branches) >= self.requested:
            raise ValueError("batch has no unregistered slots")
        self._branches[identity.branch_id] = identity
        self._slot_to_branch[identity.slot] = identity.branch_id
        path = self.output / LEDGER_FILES["branches"]
        row = identity.row() | {"status": "registered", "recorded_at": _utc_now()}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(_canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        counts = self.run_state.setdefault("counts", {})
        counts["branches"] = int(counts.get("branches", 0)) + 1
        active = len(self._branches) - len(self._finished)
        self.run_state["peak_active"] = max(
            int(self.run_state.get("peak_active", 0)), active)
        self.save_state()
        return identity

    def record_step(
        self,
        branch_id: str,
        *,
        simulator_step: int,
        sim_time_s: float,
        commands: Mapping[str, Any],
        post_step_state: Mapping[str, Any],
        phase_state: Mapping[str, Any],
        task_state: Mapping[str, Any],
        active: bool,
        completed: bool,
        scene_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if simulator_step < 0:
            raise ValueError("simulator_step must be non-negative")
        if not math.isfinite(float(sim_time_s)) or sim_time_s < 0:
            raise ValueError("sim_time_s must be finite and non-negative")
        return self._append("steps", branch_id, {
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "commands": dict(commands),
            "post_step_state": dict(post_step_state),
            "phase_state": dict(phase_state),
            "task_state": dict(task_state),
            "active": bool(active),
            "completed": bool(completed),
            "scene_payload": dict(scene_payload),
        })

    def record_step_batch(
        self, records: Sequence[tuple[str, Mapping[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Append one simulator barrier's per-branch rows with one fsync."""
        if not records:
            return []
        recorded_at = _utc_now()
        rows: list[dict[str, Any]] = []
        for branch_id, payload in records:
            identity = self._identity(branch_id)
            missing = set(REQUIRED_STEP_FIELDS) - set(payload)
            if missing:
                raise ValueError(f"step record misses fields {sorted(missing)}")
            simulator_step = payload["simulator_step"]
            sim_time_s = payload["sim_time_s"]
            if isinstance(simulator_step, bool) or not isinstance(simulator_step, int) or simulator_step < 0:
                raise ValueError("simulator_step must be a non-negative integer")
            if not math.isfinite(float(sim_time_s)) or sim_time_s < 0:
                raise ValueError("sim_time_s must be finite and non-negative")
            row = identity.row()
            for key in REQUIRED_LEDGER_FIELDS:
                if key in payload and payload[key] != row[key]:
                    raise ValueError(
                        f"record attempts to change branch identity field {key}")
            row.update(dict(payload))
            row["recorded_at"] = recorded_at
            rows.append(row)
        path = self.output / LEDGER_FILES["steps"]
        with path.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(_canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        counts = self.run_state.setdefault("counts", {})
        counts["steps"] = int(counts.get("steps", 0)) + len(rows)
        self.run_state["updated_at"] = recorded_at
        self._pending_state_records += len(rows)
        if self._pending_state_records >= self.autosave_every:
            self.save_state()
        return rows

    def record_event(
        self, branch_id: str, *, event_type: str, simulator_step: int,
        sim_time_s: float, payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._append("events", branch_id, {
            "event_type": _require_text("event_type", event_type),
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "payload": dict(payload or {}),
        })

    def record_progress(
        self, branch_id: str, *, simulator_step: int, sim_time_s: float,
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._append("progress", branch_id, {
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "progress": dict(progress),
        })

    def record_network(
        self, branch_id: str, *, simulator_step: int, sim_time_s: float,
        network: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._append("network", branch_id, {
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "network": dict(network),
        })

    def record_cost(
        self, branch_id: str, *, simulator_step: int, sim_time_s: float,
        cost: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._append("costs", branch_id, {
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "cost": dict(cost),
        })

    def record_checkpoint(
        self,
        branch_id: str,
        *,
        checkpoint_id: str,
        simulator_step: int,
        snapshot_digest: str,
        snapshot_schema: str,
        restore_error: float | None,
        restore_passed: bool | None,
        restore_status: str,
        restore_error_source: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if restore_status not in {"measured", "pending", "unavailable", "failed"}:
            raise ValueError("invalid restore_status")
        if restore_error is None:
            if restore_status == "measured":
                raise ValueError("measured restore requires a numeric restore_error")
            if restore_passed is True:
                raise ValueError("restore cannot pass without a measured error")
        else:
            if not math.isfinite(float(restore_error)) or restore_error < 0:
                raise ValueError("restore_error must be finite and non-negative")
            if restore_status not in {"measured", "failed"}:
                raise ValueError("numeric restore_error requires measured or failed status")
            if restore_passed is None:
                raise ValueError("numeric restore_error requires restore_passed")
        return self._append("checkpoints", branch_id, {
            "checkpoint_id": _require_text("checkpoint_id", checkpoint_id),
            "simulator_step": int(simulator_step),
            # Keep the concise protocol spelling alongside the canonical
            # ledger field used by step/event/progress rows.
            "sim_step": int(simulator_step),
            "snapshot_digest": _require_text("snapshot_digest", snapshot_digest),
            "snapshot_schema": _require_text("snapshot_schema", snapshot_schema),
            "restore_error": None if restore_error is None else float(restore_error),
            "restore_passed": restore_passed,
            "restore_status": restore_status,
            "restore_error_source": _require_text(
                "restore_error_source", restore_error_source),
            "payload": dict(payload or {}),
        }, save_state=True)

    def finish_branch(
        self,
        branch_id: str,
        *,
        completed: bool,
        independently_verified: bool,
        simulator_step: int,
        sim_time_s: float,
        outcome: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if branch_id in self._finished:
            raise ValueError(f"branch already finished: {branch_id}")
        if independently_verified and not completed:
            raise ValueError("an incomplete branch cannot be independently verified")
        row = self._append("episodes", branch_id, {
            "finished": True,
            "completed": bool(completed),
            "independently_verified": bool(independently_verified),
            "simulator_step": int(simulator_step),
            "sim_time_s": float(sim_time_s),
            "outcome": dict(outcome or {}),
        }, save_state=False)
        self._finished.add(branch_id)
        if completed:
            self._completed.add(branch_id)
        if independently_verified:
            self._verified.add(branch_id)
        self.save_state()
        return row

    def close_batch(
        self,
        *,
        status: str = "completed",
        resident: int | None = None,
        peak_active: int | None = None,
        queued: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "interrupted", "failed"}:
            raise ValueError("status must be completed, interrupted or failed")
        self.run_state.update({
            "status": status,
            "resident": len(self._branches) if resident is None else int(resident),
            "peak_active": (
                int(self.run_state.get("peak_active", 0))
                if peak_active is None else int(peak_active)
            ),
            "queued": (
                max(0, self.requested - len(self._branches))
                if queued is None else int(queued)
            ),
            "closed_at": _utc_now(),
            "details": dict(details or {}),
        })
        self.save_state()
        return dict(self.run_state)


def open_batch(
    output: str | Path,
    *,
    scene_key: str,
    task_id: str,
    requested: int,
    execution_scope: str,
    task_version: str,
    source_revision_value: str | None = None,
    formal: bool = False,
    batch_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    autosave_every: int = 128,
) -> BranchDataBatch:
    output_path = Path(output).resolve()
    if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 32:
        raise ValueError("requested must be between 1 and 32")
    if formal and requested != 32:
        raise ValueError("a formal branch batch must request exactly 32 slots")
    if execution_scope not in EXECUTION_SCOPES:
        raise ValueError(f"invalid execution_scope: {execution_scope}")
    output_path.mkdir(parents=True, exist_ok=False)
    created_at = _utc_now()
    revision = source_revision_value or source_revision()
    manifest = {
        "schema": SCHEMA_VERSION,
        "batch_id": batch_id or str(uuid.uuid4()),
        "scene_key": _require_text("scene_key", scene_key),
        "task_id": _require_text("task_id", task_id),
        "requested": requested,
        "formal": bool(formal),
        "execution_scope": execution_scope,
        "task_version": _require_text("task_version", task_version),
        "source_revision": _require_text("source_revision", revision),
        "slot_indexing": "zero_based",
        "formal_requirements": {
            "requested": 32,
            "resident": 32,
            "peak_active": 32,
            "completed": 32,
            "independently_verified": 32,
            "queued": 0,
        },
        "ledger_files": dict(LEDGER_FILES),
        "created_at": created_at,
        "metadata": dict(metadata or {}),
    }
    run_state = {
        "schema": SCHEMA_VERSION,
        "batch_id": manifest["batch_id"],
        "status": "open",
        "requested": requested,
        "registered": 0,
        "resident": 0,
        "peak_active": 0,
        "completed": 0,
        "independently_verified": 0,
        "queued": requested,
        "resume_count": 0,
        "counts": {key: 0 for key in LEDGER_FILES},
        "created_at": created_at,
        "updated_at": created_at,
    }
    _write_json_atomic(output_path / "manifest.json", manifest)
    for filename in LEDGER_FILES.values():
        (output_path / filename).touch(exist_ok=False)
    _write_json_atomic(output_path / "run_state.json", run_state)
    return BranchDataBatch(
        output_path, manifest, run_state, autosave_every=autosave_every)


def resume_batch(output: str | Path, *, autosave_every: int = 128) -> BranchDataBatch:
    output_path = Path(output).resolve()
    manifest = _read_json(output_path / "manifest.json")
    run_state = _read_json(output_path / "run_state.json")
    if manifest.get("schema") != SCHEMA_VERSION or run_state.get("schema") != SCHEMA_VERSION:
        raise ValueError("unsupported branch data schema")
    if manifest.get("batch_id") != run_state.get("batch_id"):
        raise ValueError("manifest/run_state batch_id mismatch")
    for filename in LEDGER_FILES.values():
        if not (output_path / filename).is_file():
            raise FileNotFoundError(output_path / filename)
    batch = BranchDataBatch(
        output_path, manifest, run_state, autosave_every=autosave_every)
    counts = {
        ledger: len(_read_jsonl(output_path / filename))
        for ledger, filename in LEDGER_FILES.items()
    }
    episodes = _read_jsonl(output_path / LEDGER_FILES["episodes"])
    run_state.update({
        "status": "resumed",
        "counts": counts,
        "registered": len(batch._branches),
        "resident": max(int(run_state.get("resident", 0)), len(batch._branches)),
        "completed": sum(bool(row.get("completed")) for row in episodes),
        "independently_verified": sum(
            bool(row.get("independently_verified")) for row in episodes),
        "queued": max(0, batch.requested - len(batch._branches)),
        "resume_count": int(run_state.get("resume_count", 0)) + 1,
        "resumed_at": _utc_now(),
    })
    batch.save_state()
    return batch


def _validate_checkpoint(row: Mapping[str, Any], location: str, errors: list[str]) -> None:
    missing = set(REQUIRED_CHECKPOINT_FIELDS) - set(row)
    if missing:
        errors.append(f"{location}: missing checkpoint fields {sorted(missing)}")
        return
    status = row["restore_status"]
    error = row["restore_error"]
    passed = row["restore_passed"]
    if error is None and (status == "measured" or passed is True):
        errors.append(f"{location}: unmeasured restore cannot be measured/passed")
    if error is not None:
        if status not in {"measured", "failed"}:
            errors.append(f"{location}: numeric restore has invalid status {status!r}")
        if isinstance(error, bool) or not isinstance(error, (int, float)) or not math.isfinite(error) or error < 0:
            errors.append(f"{location}: invalid restore_error")
        if not isinstance(passed, bool):
            errors.append(f"{location}: numeric restore requires boolean restore_passed")
    sim_step = row.get("sim_step")
    simulator_step = row.get("simulator_step")
    if sim_step is not None:
        if (isinstance(sim_step, bool) or not isinstance(sim_step, int)
                or sim_step < 0):
            errors.append(f"{location}: invalid sim_step")
        elif sim_step != simulator_step:
            errors.append(f"{location}: sim_step differs from simulator_step")


def validate_run(output: str | Path, *, write: bool = True) -> dict[str, Any]:
    output_path = Path(output).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = _read_json(output_path / "manifest.json")
        run_state = _read_json(output_path / "run_state.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {
            "schema": SCHEMA_VERSION,
            "status": "failed",
            "validated_at": _utc_now(),
            "errors": [str(error)],
            "warnings": [],
            "counts": {},
        }
        if write and output_path.is_dir():
            _write_json_atomic(output_path / "validation.json", result)
        return result
    if manifest.get("schema") != SCHEMA_VERSION:
        errors.append("manifest schema mismatch")
    if manifest.get("batch_id") != run_state.get("batch_id"):
        errors.append("manifest/run_state batch_id mismatch")
    requested = manifest.get("requested")
    if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 32:
        errors.append("manifest requested must be between 1 and 32")
    rows_by_ledger: dict[str, list[dict[str, Any]]] = {}
    for ledger, filename in LEDGER_FILES.items():
        path = output_path / filename
        if not path.is_file():
            errors.append(f"missing ledger: {filename}")
            rows_by_ledger[ledger] = []
            continue
        try:
            rows_by_ledger[ledger] = _read_jsonl(path)
        except ValueError as error:
            errors.append(str(error))
            rows_by_ledger[ledger] = []
    identities: dict[str, dict[str, Any]] = {}
    slots: dict[int, str] = {}
    for index, row in enumerate(rows_by_ledger["branches"], 1):
        location = f"{LEDGER_FILES['branches']}:{index}"
        missing = set(REQUIRED_LEDGER_FIELDS) - set(row)
        if missing:
            errors.append(f"{location}: missing identity fields {sorted(missing)}")
            continue
        branch_id = str(row["branch_id"])
        identity = {key: row[key] for key in REQUIRED_LEDGER_FIELDS}
        if branch_id in identities and identities[branch_id] != identity:
            errors.append(f"{location}: branch identity changed")
        identities[branch_id] = identity
        slot = row["slot"]
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0 or (
            isinstance(requested, int) and slot >= requested
        ):
            errors.append(f"{location}: invalid slot {slot!r}")
        elif slot in slots and slots[slot] != branch_id:
            errors.append(f"{location}: slot is reused by {slots[slot]}")
        else:
            slots[slot] = branch_id
    for ledger, rows in rows_by_ledger.items():
        for index, row in enumerate(rows, 1):
            location = f"{LEDGER_FILES[ledger]}:{index}"
            missing = set(REQUIRED_LEDGER_FIELDS) - set(row)
            if missing:
                errors.append(f"{location}: missing identity fields {sorted(missing)}")
                continue
            branch_id = str(row["branch_id"])
            registered = identities.get(branch_id)
            if registered is None:
                errors.append(f"{location}: unregistered branch {branch_id}")
            elif any(row[key] != registered[key] for key in REQUIRED_LEDGER_FIELDS):
                errors.append(f"{location}: identity differs from branch manifest")
            if ledger == "steps":
                missing_step = set(REQUIRED_STEP_FIELDS) - set(row)
                if missing_step:
                    errors.append(f"{location}: missing step fields {sorted(missing_step)}")
            elif ledger == "checkpoints":
                _validate_checkpoint(row, location, errors)
    episodes = rows_by_ledger["episodes"]
    episode_branch_ids = [str(row.get("branch_id")) for row in episodes if row.get("finished")]
    if len(episode_branch_ids) != len(set(episode_branch_ids)):
        errors.append("episodes ledger contains duplicate finished branches")
    completed = sum(bool(row.get("completed")) for row in episodes)
    verified = sum(bool(row.get("independently_verified")) for row in episodes)
    active_by_step: dict[int, set[str]] = {}
    for row in rows_by_ledger["steps"]:
        simulator_step = row.get("simulator_step")
        if (row.get("active") and isinstance(simulator_step, int)
                and not isinstance(simulator_step, bool)):
            active_by_step.setdefault(simulator_step, set()).add(
                str(row.get("branch_id")))
    observed_peak_active = max(
        (len(branch_ids) for branch_ids in active_by_step.values()), default=0)
    metrics = {
        "requested": requested,
        "resident": int(run_state.get("resident", len(identities))),
        "peak_active": int(run_state.get("peak_active", 0)),
        "completed": completed,
        "independently_verified": verified,
        "queued": int(run_state.get("queued", max(0, (requested or 0) - len(identities)))),
        "checkpoint_restore_error": max(
            (
                float(row["restore_error"])
                for row in rows_by_ledger["checkpoints"]
                if row.get("restore_error") is not None
            ),
            default=None,
        ),
        "episode_count": len(episodes),
        "step_count": len(rows_by_ledger["steps"]),
        "event_count": len(rows_by_ledger["events"]),
        "network_count": len(rows_by_ledger["network"]),
        "cost_count": len(rows_by_ledger["costs"]),
        "checkpoint_count": len(rows_by_ledger["checkpoints"]),
    }
    if len(identities) != len(slots):
        errors.append("registered branch and occupied slot counts differ")
    if len(identities) > (requested if isinstance(requested, int) else 0):
        errors.append("registered branches exceed requested slots")
    if int(run_state.get("resident", 0)) > (requested or 0):
        errors.append("resident branches exceed requested slots")
    if int(run_state.get("peak_active", 0)) > int(run_state.get("resident", 0)):
        errors.append("peak_active exceeds resident branches")
    if manifest.get("formal"):
        required = manifest.get("formal_requirements", {})
        for key, expected in required.items():
            if metrics.get(key) != expected:
                errors.append(
                    f"formal requirement failed: {key}={metrics.get(key)!r}, expected {expected!r}")
        if manifest.get("execution_scope") != "measured":
            errors.append("formal batch execution_scope must be measured")
        if len(identities) != 32:
            errors.append("formal batch must contain 32 registered branch identities")
        if len(episode_branch_ids) != 32:
            errors.append("formal batch must contain 32 finished episodes")
        if observed_peak_active != 32:
            errors.append(
                "formal batch has no simulator step with 32 active branch rows: "
                f"observed {observed_peak_active}"
            )
        required_branch_ledgers = (
            "steps", "episodes", "events", "progress", "network", "costs",
            "checkpoints",
        )
        registered_ids = set(identities)
        for ledger in required_branch_ledgers:
            covered = {str(row.get("branch_id")) for row in rows_by_ledger[ledger]}
            missing = registered_ids - covered
            if missing:
                errors.append(
                    f"formal batch {ledger} ledger misses {len(missing)} branches")
        for branch_id in registered_ids:
            restore_rows = [
                row for row in rows_by_ledger["checkpoints"]
                if row.get("branch_id") == branch_id
                and row.get("restore_status") == "measured"
                and row.get("restore_passed") is True
            ]
            if not restore_rows:
                errors.append(
                    f"formal branch has no measured passing restore: {branch_id}")
    elif requested != 32:
        warnings.append("small-scale run is not evidence of formal 32-branch capacity")
    if run_state.get("status") != "completed":
        errors.append(f"run_state status is not completed: {run_state.get('status')!r}")
    result = {
        "schema": SCHEMA_VERSION,
        "batch_id": manifest.get("batch_id"),
        "status": "passed" if not errors else "failed",
        "execution_scope": manifest.get("execution_scope"),
        "formal": bool(manifest.get("formal")),
        "validated_at": _utc_now(),
        "errors": errors,
        "warnings": warnings,
        "counts": {key: len(value) for key, value in rows_by_ledger.items()},
        "metrics": metrics,
        "evidence": {"observed_peak_active": observed_peak_active},
    }
    result["metrics"]["validation_status"] = result["status"]
    if write:
        _write_json_atomic(output_path / "validation.json", result)
    return result


def register_branch(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> BranchIdentity:
    return batch.register_branch(*args, **kwargs)


def record_step(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_step(*args, **kwargs)


def record_step_batch(
    batch: BranchDataBatch, *args: Any, **kwargs: Any
) -> list[dict[str, Any]]:
    return batch.record_step_batch(*args, **kwargs)


def record_event(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_event(*args, **kwargs)


def record_progress(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_progress(*args, **kwargs)


def record_network(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_network(*args, **kwargs)


def record_cost(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_cost(*args, **kwargs)


def record_checkpoint(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.record_checkpoint(*args, **kwargs)


def finish_branch(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.finish_branch(*args, **kwargs)


def close_batch(batch: BranchDataBatch, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return batch.close_batch(*args, **kwargs)


__all__ = [
    "BRANCH_TYPES",
    "EXECUTION_SCOPES",
    "LEDGER_FILES",
    "REQUIRED_LEDGER_FIELDS",
    "BranchDataBatch",
    "BranchIdentity",
    "BranchSceneAdapter",
    "close_batch",
    "digest_state",
    "digest_value",
    "finish_branch",
    "open_batch",
    "record_checkpoint",
    "record_cost",
    "record_event",
    "record_network",
    "record_progress",
    "record_step",
    "record_step_batch",
    "register_branch",
    "resume_batch",
    "source_revision",
    "validate_run",
]
