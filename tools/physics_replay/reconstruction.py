"""Stream and gate fresh-scene reconstruction without writing physical state."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .store import _atomic_json_write, _sha256


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _arrays(frame: Mapping[str, Any]) -> dict[str, np.ndarray]:
    if not frame or any(not isinstance(name, str) or not name for name in frame):
        raise ValueError("reconstruction needs nonempty named measurement channels")
    result = {}
    for name, value in frame.items():
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        array = np.asarray(value)
        if array.dtype.kind not in "biuf" or not np.all(np.isfinite(array)):
            raise ValueError(f"non-numeric/non-finite reconstruction channel: {name}")
        result[name] = array.copy()
    return result


class ReconstructionRecorder:
    """Record an uninterrupted original, including variable-size contact patches."""

    def __init__(self, directory: str | Path, fingerprint: Mapping[str, Any]):
        self.directory = Path(directory)
        if self.directory.exists() and any(self.directory.iterdir()):
            raise FileExistsError(f"reconstruction output must be empty: {self.directory}")
        self.fingerprint = _json_copy(dict(fingerprint))
        if not self.fingerprint:
            raise ValueError("a task/runtime fingerprint is required")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._index = (self.directory / "frames.jsonl").open("x", encoding="utf-8")
        self.steps = 0
        self.checkpoints = {}
        self.closed = False
        self.manifest = {"version": 1, "status": "recording", "pid": os.getpid(),
                         "fingerprint": self.fingerprint, "method": "fresh_scene_reexecution"}
        _atomic_json_write(self.directory / "reconstruction.json", self.manifest)

    def append(self, frame: Mapping[str, Any], *, task_state: Mapping[str, Any], checkpoint: str | None = None) -> None:
        if self.closed:
            raise RuntimeError("reconstruction journal is closed")
        arrays = _arrays(frame)
        state = _json_copy(dict(task_state))
        if checkpoint is not None and (not isinstance(checkpoint, str) or not checkpoint.strip()
                                       or checkpoint in self.checkpoints):
            raise ValueError("checkpoint must have a unique nonempty task name")
        path = self.directory / f"frame_{self.steps:08d}.npz"
        with path.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
        record = {"step": self.steps, "sha256": _sha256(path), "task_state": state, "checkpoint": checkpoint}
        self._index.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        self._index.flush()
        self.steps += 1
        if checkpoint is not None:
            self.checkpoints[checkpoint] = self.steps

    def close(self, *, complete: bool = True) -> None:
        if self.closed:
            return
        self.closed = True
        self._index.close()
        self.manifest.update(status="complete" if complete and self.steps else "incomplete", steps=self.steps,
                             checkpoints=self.checkpoints, index_sha256=_sha256(self.directory / "frames.jsonl"))
        _atomic_json_write(self.directory / "reconstruction.json", self.manifest)


class ReconstructionMismatch(ValueError):
    """The live process must not be released after a reconstruction mismatch."""


class ReconstructionVerifier:
    """Check a freshly executed prefix and only then allow live continuation.

    The caller owns clean scene construction and re-executes its task and policy.
    This class never feeds stored poses, actions or contact forces to the scene.
    Fingerprints describe caller-declared coverage, not an exhaustive state proof.
    """

    def __init__(self, directory: str | Path, fingerprint: Mapping[str, Any], *, checkpoint: str):
        self.directory = Path(directory)
        self.manifest = json.loads((self.directory / "reconstruction.json").read_text(encoding="utf-8"))
        if self.manifest.get("version") != 1 or self.manifest.get("status") != "complete":
            raise ValueError("a complete supported original reconstruction journal is required")
        if self.manifest["pid"] == os.getpid():
            raise ValueError("reconstruction requires a fresh independent process, not the recording process")
        if self.manifest["fingerprint"] != _json_copy(dict(fingerprint)):
            raise ValueError("task/runtime fingerprint mismatch")
        if checkpoint not in self.manifest["checkpoints"]:
            raise ValueError(f"unknown task checkpoint: {checkpoint}")
        self.target_steps = self.manifest["checkpoints"][checkpoint]
        self.total_steps = self.manifest["steps"]
        if not isinstance(self.total_steps, int) or not 0 < self.target_steps <= self.total_steps:
            raise ValueError("invalid reconstruction step/checkpoint range")
        index_path = self.directory / "frames.jsonl"
        if _sha256(index_path) != self.manifest["index_sha256"]:
            raise ValueError("reconstruction index integrity mismatch")
        self._index = index_path.open(encoding="utf-8")
        self.steps = 0
        self.checkpoint = checkpoint
        self.failure = None
        self.released = False
        self.closed = False
        self.max_errors = {}
        self._index_digest = sha256()

    def _reject(self, channel: str, reason: str, **details: Any) -> None:
        self.failure = {"step": self.steps, "channel": channel, "reason": reason, **details}
        raise ReconstructionMismatch(f"reconstruction mismatch at step {self.steps}: {channel}: {reason}")

    def compare(self, frame: Mapping[str, Any], *, task_state: Mapping[str, Any], checkpoint: str | None = None) -> None:
        if self.failure or self.closed or self.released:
            raise ReconstructionMismatch("reconstruction verifier is failed, closed or already released")
        try:
            self._compare(frame, task_state=task_state, checkpoint=checkpoint)
        except ReconstructionMismatch:
            raise
        except Exception as exc:
            self._reject("journal", str(exc))

    def _compare(self, frame: Mapping[str, Any], *, task_state: Mapping[str, Any], checkpoint: str | None) -> None:
        if self.steps >= self.total_steps:
            self._reject("step", "unexpected extra environment step")
        line = self._index.readline()
        record = json.loads(line)
        self._index_digest.update(line.encode("utf-8"))
        if record["step"] != self.steps:
            self._reject("step", "noncontiguous or reordered journal")
        if record["task_state"] != _json_copy(dict(task_state)) or record["checkpoint"] != checkpoint:
            self._reject("task_state", "task events, logical state or checkpoint timing differs")
        if checkpoint is not None and self.manifest["checkpoints"].get(checkpoint) != self.steps + 1:
            self._reject("checkpoint", "checkpoint index does not match the recorded frame")
        if self.steps + 1 == self.target_steps and checkpoint != self.checkpoint:
            self._reject("checkpoint", "target boundary was not observed")
        path = self.directory / f"frame_{self.steps:08d}.npz"
        payload = path.read_bytes()
        if sha256(payload).hexdigest() != record["sha256"]:
            self._reject("journal", "frame integrity mismatch")
        candidate = _arrays(frame)
        with np.load(BytesIO(payload), allow_pickle=False) as expected:
            if set(expected.files) != set(candidate):
                self._reject("channels", "missing or unexpected measurement channels")
            for name in sorted(candidate):
                reference, actual = expected[name], candidate[name]
                if reference.shape != actual.shape or reference.dtype != actual.dtype:
                    self._reject(name, "shape/dtype mismatch", expected_shape=list(reference.shape),
                                 actual_shape=list(actual.shape), expected_dtype=str(reference.dtype),
                                 actual_dtype=str(actual.dtype))
                error = float(np.abs(reference.astype(np.longdouble) - actual.astype(np.longdouble)).max(initial=0))
                self.max_errors[name] = max(self.max_errors.get(name, 0.0), error)
                if not np.array_equal(reference, actual):
                    self._reject(name, "nonzero numerical deviation", max_abs=error)
        self.steps += 1
        if self.steps == self.total_steps:
            if self._index.readline() or self._index_digest.hexdigest() != self.manifest["index_sha256"]:
                self._reject("journal", "index changed or contains unexpected frames")

    @property
    def prefix_verified(self) -> bool:
        return self.failure is None and self.steps >= self.target_steps

    def release(self) -> None:
        if self.closed or self.released or not self.prefix_verified or self.steps != self.target_steps:
            raise ReconstructionMismatch("live release requires the exact verified task checkpoint boundary")
        if _sha256(self.directory / "frames.jsonl") != self.manifest["index_sha256"]:
            self._reject("journal", "index changed before live release")
        self.released = True

    def report(self) -> dict[str, Any]:
        return {"method": "fresh_scene_reexecution", "checkpoint": self.checkpoint, "threshold": 0.0,
                "compared_steps": self.steps, "target_steps": self.target_steps, "total_steps": self.total_steps,
                "prefix_verified": self.prefix_verified, "released_for_live_continuation": self.released,
                "original_continuation_verified": self.failure is None and self.steps == self.total_steps
                and self.total_steps > self.target_steps, "first_divergence": self.failure,
                "max_errors": dict(self.max_errors), "solver_cache_serialized": False,
                "complete_physical_state_proven": False,
                "scope": "exact equality of recorded channels/events over the measured horizon only"}

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._index.close()
