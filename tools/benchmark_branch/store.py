"""Append-only JSONL persistence, checksums and resumable collector state."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping, TextIO

from .models import BranchJob, BranchPlan, IDENTITY_FIELDS, SCHEMA_VERSION


LEDGERS = (
    "branch_manifest", "checkpoints", "steps", "episodes", "events",
    "progress", "network", "costs", "resources", "metrics",
    "observations",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class BranchRunStore:
    """Durable run directory owned by the parent collector process."""

    def __init__(self, root: str | Path, plan: BranchPlan, manifest: dict[str, Any], *, resume: bool = False):
        self.root = Path(root).resolve()
        self.plan = plan
        self.manifest = manifest
        self.root.mkdir(parents=True, exist_ok=True)
        self._event_seq: dict[str, int] = defaultdict(int)
        self._branch_ids: set[str] = set()
        self._logical_ids: set[str] = set()
        self._committed = 0
        # The parent collector is the only writer for the run ledgers.  Keep
        # one append handle per ledger so a high-frequency 32-worker run does
        # not pay open/close overhead for every JSONL row.  ``sync=True``
        # still fsyncs lifecycle boundaries explicitly.
        self._streams: dict[str, TextIO] = {}
        self._branch_streams: dict[str, TextIO] = {}
        self._append_lock = threading.RLock()
        if not resume:
            for name in LEDGERS:
                (self.root / f"{name}.jsonl").touch(exist_ok=True)
            _atomic_json(self.root / "plan.json", plan.row())
            _atomic_json(self.root / "manifest.json", manifest)
            _atomic_json(self.root / "run_state.json", self.state())
            _atomic_json(self.root / "collector_state.json", self.collector_state())
        else:
            self._load_existing()

    @classmethod
    def create(cls, root: str | Path, plan: BranchPlan, *, requested_max_workers: int = 1,
               execution_mode: str = "isolated_parallel", execution_scope: str | None = None,
               resource_budget: Mapping[str, Any] | None = None, adapter: str = "",
               adapter_kwargs: Mapping[str, Any] | None = None) -> "BranchRunStore":
        run_id = plan.run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
        plan = BranchPlan.from_mapping({**plan.row(), "run_id": run_id})
        manifest = {
            "schema": SCHEMA_VERSION, "run_id": run_id,
            "created_at": _now(), "scene_key": plan.scene_key, "task_id": plan.task_id,
            "system_id": plan.system_id, "condition_id": plan.condition_id,
            "task_version": plan.task_version, "system_version": plan.system_version,
            "source_revision": plan.source_revision, "execution_scope": execution_scope or plan.execution_scope,
            "pair_slots": plan.pair_slots, "logical_branches": plan.logical_branch_count,
            "requested_max_workers": int(requested_max_workers), "admitted_max_workers": int(requested_max_workers),
            "observed_peak_branch_workers": 0, "requested": plan.logical_branch_count,
            "resident": 0, "active": 0, "completed": 0, "queued": plan.logical_branch_count, "failed": 0,
            "execution_mode": execution_mode, "concurrency_reduced": False, "reduction_reason": "",
            "resource_budget": dict(resource_budget or {}), "adapter": adapter,
            "adapter_kwargs": dict(adapter_kwargs or {}), "plan_digest": _sha256_json(plan.row()),
        }
        return cls(root, plan, manifest)

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.plan.run_id))

    def rows(self, ledger: str) -> list[dict[str, Any]]:
        return self.read_ledger(ledger)

    def checksum(self, ledger: str) -> str:
        if ledger not in LEDGERS:
            raise ValueError(ledger)
        return _sha256(self.root / f"{ledger}.jsonl")

    @classmethod
    def resume(cls, root: str | Path) -> "BranchRunStore":
        root = Path(root).resolve()
        plan_data = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        return cls(root, BranchPlan.from_mapping(plan_data), manifest, resume=True)

    def _load_existing(self) -> None:
        self._branch_ids.clear(); self._logical_ids.clear(); self._event_seq.clear()
        path = self.root / "branch_manifest.jsonl"
        if path.exists():
            for row in _read_jsonl(path):
                branch_id = str(row.get("branch_id", ""))
                if branch_id:
                    self._branch_ids.add(branch_id)
                    self._logical_ids.add(str(row.get("logical_branch_id") or branch_id.split("#attempt-", 1)[0]))
        events = self.root / "events.jsonl"
        if events.exists():
            for row in _read_jsonl(events):
                branch_id = str(row.get("branch_id", ""))
                if branch_id and "event_seq" in row:
                    self._event_seq[branch_id] = max(self._event_seq[branch_id], int(row["event_seq"]) + 1)
        self._committed = sum(len(_read_jsonl(self.root / f"{name}.jsonl")) for name in LEDGERS)

    def state(self) -> dict[str, Any]:
        return {key: self.manifest.get(key, 0) for key in (
            "run_id", "requested", "resident", "active", "completed", "queued", "failed",
            "requested_max_workers", "admitted_max_workers", "observed_peak_branch_workers",
            "concurrency_reduced", "reduction_reason",
        )}

    def collector_state(self) -> dict[str, Any]:
        checksums = {}
        for name in LEDGERS:
            path = self.root / f"{name}.jsonl"
            checksums[name] = _sha256(path) if path.exists() else ""
        return {"schema": f"{SCHEMA_VERSION}.collector", "committed_records": self._committed,
                "checksums": checksums, "updated_at": _now()}

    def persist_state(self) -> None:
        _atomic_json(self.root / "run_state.json", self.state())
        _atomic_json(self.root / "collector_state.json", self.collector_state())

    def update_manifest(self, **updates: Any) -> None:
        self.manifest.update(updates)
        _atomic_json(self.root / "manifest.json", self.manifest)

    def write_manifest_updates(self, **updates: Any) -> None:
        self.update_manifest(**updates)

    def append(self, ledger: str, row: Mapping[str, Any], *, job: BranchJob | None = None,
               event: bool = False, sync: bool = True) -> dict[str, Any]:
        # Session backends may run multiple scene sessions concurrently.  The
        # parent remains the sole writer, and this lock keeps JSONL rows,
        # event counters and branch mirrors coherent across those threads.
        with self._append_lock:
            return self._append_locked(ledger, row, job=job, event=event, sync=sync)

    def _append_locked(self, ledger: str, row: Mapping[str, Any], *, job: BranchJob | None = None,
                       event: bool = False, sync: bool = True) -> dict[str, Any]:
        if ledger not in LEDGERS:
            raise ValueError(f"unknown ledger: {ledger}")
        payload = dict(row)
        if job is not None:
            identity = job.identity()
            for key in IDENTITY_FIELDS:
                if key not in payload and key in identity:
                    payload[key] = identity[key]
            payload.setdefault("logical_branch_id", job.logical_branch_id)
        payload.setdefault("schema", f"{SCHEMA_VERSION}.{ledger}.v1")
        payload.setdefault("recorded_at", _now())
        if event:
            branch_id = str(payload.get("branch_id", ""))
            payload["event_seq"] = self._event_seq[branch_id]
            self._event_seq[branch_id] += 1
        branch_id = str(payload.get("branch_id", ""))
        # Reject duplicate manifest identities before mutating the append-only
        # file.  Previously the row was written and only then rejected,
        # leaving a corrupted run that required validator cleanup.
        if ledger == "branch_manifest" and branch_id in self._branch_ids:
            raise ValueError(f"duplicate branch_id: {branch_id}")
        stream = self._streams.get(ledger)
        if stream is None:
            stream = (self.root / f"{ledger}.jsonl").open("a", encoding="utf-8")
            self._streams[ledger] = stream
        stream.write(_json(payload) + "\n")
        stream.flush()
        # High-frequency state rows are flushed to the kernel on every
        # append, while lifecycle boundaries request an fsync.  The collector
        # calls ``finish`` after every normal run and fsyncs all ledgers there,
        # so a faster row path does not weaken completed-run durability.
        if sync:
            os.fsync(stream.fileno())
        self._committed += 1
        if ledger == "branch_manifest":
            self._branch_ids.add(branch_id)
            self._logical_ids.add(str(payload.get("logical_branch_id") or branch_id.split("#attempt-", 1)[0]))
        # Shared-session adapters run all slots inside one parent process.  A
        # branch-local append-only mirror makes each slot independently
        # inspectable while the top-level ledger remains the canonical source
        # for validation and aggregation.  Fresh-process workers already own
        # their branch/ledger.jsonl, so they do not use this mirror.
        if job is not None and (self.manifest.get("execution_mode") == "shared_session"
                                or self.manifest.get("execution_backend") == "session_backend"):
            self.append_branch_ledger(job, ledger, payload, sync=sync)
        return payload

    def append_branch_ledger(self, job: BranchJob, ledger: str,
                             row: Mapping[str, Any], *, sync: bool = True) -> Path:
        """Append one shared-session row to its branch-local audit ledger."""
        safe = job.branch_id.replace("/", "_").replace("#", "_")
        branch_dir = self.root / "branches" / safe
        branch_dir.mkdir(parents=True, exist_ok=True)
        path = branch_dir / "ledger.jsonl"
        stream = self._branch_streams.get(job.branch_id)
        if stream is None:
            stream = path.open("a", encoding="utf-8")
            self._branch_streams[job.branch_id] = stream
        value = {"ledger": ledger, **dict(row)}
        stream.write(_json(value) + "\n")
        stream.flush()
        if sync:
            os.fsync(stream.fileno())
        return path

    def append_many(self, items: Iterable[tuple[str, Mapping[str, Any], BranchJob | None, bool]]) -> None:
        for ledger, row, job, event in items:
            self.append(ledger, row, job=job, event=event)

    def write_checkpoint(self, row: Mapping[str, Any], job: BranchJob | None = None) -> dict[str, Any]:
        return self.append("checkpoints", row, job=job)

    def finish(self) -> None:
        self._flush_streams(sync=False)
        self._flush_branch_streams(sync=False)
        self.persist_state()
        # Make the last buffered high-frequency rows durable before hashing.
        self._flush_streams(sync=True)
        self._flush_branch_streams(sync=True)
        checksums = {name: _sha256(self.root / f"{name}.jsonl") for name in LEDGERS}
        branch_files = sorted((self.root / "branches").glob("*/ledger.jsonl")) if (self.root / "branches").exists() else []
        checksums.update({str(path.relative_to(self.root)): _sha256(path) for path in branch_files})
        self._close_streams()
        self._close_branch_streams()
        (self.root / "checksums.sha256").write_text(
            "".join(f"{digest}  {name if name.endswith('.jsonl') else name + '.jsonl'}\n"
                     for name, digest in checksums.items()), encoding="ascii"
        )
        self.persist_state()

    def _flush_streams(self, *, sync: bool) -> None:
        for stream in tuple(self._streams.values()):
            try:
                stream.flush()
                if sync:
                    os.fsync(stream.fileno())
            except (OSError, ValueError):
                # A closed stream cannot affect already committed rows.
                continue

    def _close_streams(self) -> None:
        for stream in tuple(self._streams.values()):
            try:
                stream.close()
            except OSError:
                pass
        self._streams.clear()

    def _flush_branch_streams(self, *, sync: bool) -> None:
        for stream in tuple(self._branch_streams.values()):
            try:
                stream.flush()
                if sync:
                    os.fsync(stream.fileno())
            except (OSError, ValueError):
                continue

    def _close_branch_streams(self) -> None:
        for stream in tuple(self._branch_streams.values()):
            try:
                stream.close()
            except OSError:
                pass
        self._branch_streams.clear()

    def close(self) -> None:
        """Close open append handles without changing checksums."""

        self._flush_streams(sync=False)
        self._close_streams()
        self._flush_branch_streams(sync=False)
        self._close_branch_streams()

    def read_ledger(self, name: str) -> list[dict[str, Any]]:
        if name not in LEDGERS:
            raise ValueError(name)
        stream = self._streams.get(name)
        if stream is not None:
            stream.flush()
        return _read_jsonl(self.root / f"{name}.jsonl")


class BranchDataCollector:
    """Compatibility facade for callers that want a collector object."""

    def __init__(self, store: BranchRunStore):
        self.store = store

    def submit(self, ledger: str, row: Mapping[str, Any], *, job: BranchJob | None = None, event: bool = False) -> dict[str, Any]:
        return self.store.append(ledger, row, job=job, event=event)

    def emit(self, ledger: str, payload: Mapping[str, Any], *, key: str | None = None) -> None:
        # ``key`` is retained for API compatibility; append-only rows remain
        # authoritative and are never silently overwritten.
        del key
        self.submit(ledger, payload, event=ledger == "events")

    def flush(self) -> None:
        self.store.persist_state()

    def on_run_started(self, **payload: Any) -> None: self.emit("metrics", {"kind": "run_started", **payload})
    def on_branch_registered(self, job: BranchJob | Mapping[str, Any]) -> None:
        self.emit("branch_manifest", job.identity() if isinstance(job, BranchJob) else dict(job))
    def on_checkpoint(self, **payload: Any) -> None: self.emit("checkpoints", payload)
    def on_step(self, **payload: Any) -> None: self.emit("steps", payload)
    def on_event(self, **payload: Any) -> None: self.emit("events", payload)
    def on_progress(self, **payload: Any) -> None: self.emit("progress", payload)
    def on_network(self, **payload: Any) -> None: self.emit("network", payload)
    def on_cost(self, **payload: Any) -> None: self.emit("costs", payload)
    def on_resource_sample(self, **payload: Any) -> None: self.emit("resources", payload)
    def on_episode_finished(self, **payload: Any) -> None: self.emit("episodes", payload)

    def close(self) -> None:
        self.store.finish()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        rows.append(value)
    return rows


__all__ = ["LEDGERS", "BranchRunStore", "BranchDataCollector"]
